import type { ApiClient } from "../../api/client";
import { scientificJson } from "../../api/scientific-json";
import type { LabRun } from "./definition";
import { retainedArtifactPath } from "./retained-json";

type JsonObject = Record<string, unknown>;
export const RECOVERED_CLASSIFICATION = "operator_recovered_evidence";
export interface RecoveredArtifact extends JsonObject {
  id: string;
  name: string;
  sha256: string;
  size: number;
  role?: string;
  visibility?: string;
}
export interface RecoveryStates extends JsonObject {
  run: string;
  attempt: string;
}
export interface RecoveredReference extends JsonObject {
  classification: typeof RECOVERED_CLASSIFICATION;
  receipt: RecoveredArtifact;
  author_id: string;
  source_attempt_id: string;
  source_states: RecoveryStates;
  manifest_artifact_id: string;
  manifest_sha256: string;
  request_digest: string;
  idempotency_key: string;
}
export interface JsonlByteFacts extends JsonObject {
  newline_count: number;
  tail_bytes: number;
  ends_with_newline: boolean;
  semantic_validation: "not_performed";
}
export interface RecoveredFile extends JsonObject {
  name: string;
  artifact: RecoveredArtifact;
  attempt_id: string | null;
  verified_bytes: { sha256: string; size: number; jsonl?: JsonlByteFacts };
}
export interface RecoveredReceipt extends JsonObject {
  format: "tinystudio.operator-recovered-evidence.v1";
  source: {
    project_id: string;
    run_id: string;
    attempt_id: string;
    target_id: string;
    spec_digest: string;
  };
  source_states: RecoveryStates;
  source_states_observed_at: string;
  author_id: string;
  manifest: RecoveredArtifact;
  files: RecoveredFile[];
  producer_manifest: JsonObject;
  qualification: JsonObject;
}
const object = (value: unknown): value is JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string =>
  typeof value === "string" && !!value.trim();
const sha = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f\d]{64}$/i.test(value);
const bytes = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const states = (value: unknown): value is RecoveryStates =>
  object(value) && text(value.run) && text(value.attempt);
const artifact = (value: unknown): value is RecoveredArtifact =>
  object(value) &&
  text(value.id) &&
  text(value.name) &&
  sha(value.sha256) &&
  bytes(value.size);

function validateReference(
  value: unknown,
): asserts value is RecoveredReference {
  if (
    !object(value) ||
    value.classification !== RECOVERED_CLASSIFICATION ||
    !artifact(value.receipt) ||
    value.receipt.role !== RECOVERED_CLASSIFICATION ||
    value.receipt.visibility !== "evaluation" ||
    !text(value.source_attempt_id) ||
    !text(value.author_id) ||
    !states(value.source_states) ||
    !text(value.manifest_artifact_id) ||
    !sha(value.manifest_sha256) ||
    !sha(value.request_digest) ||
    !text(value.idempotency_key)
  )
    throw new Error(
      "Recovered evidence reference has an invalid receipt or artifact identity, size or classification.",
    );
}

function validateFile(
  value: unknown,
  attempt?: string,
): asserts value is RecoveredFile {
  if (
    !object(value) ||
    !text(value.name) ||
    !artifact(value.artifact) ||
    !(value.attempt_id === null || text(value.attempt_id)) ||
    (attempt !== undefined &&
      value.attempt_id !== null &&
      value.attempt_id !== attempt) ||
    !object(value.verified_bytes) ||
    value.verified_bytes.sha256 !== value.artifact.sha256 ||
    value.verified_bytes.size !== value.artifact.size
  )
    throw new Error(
      "Recovered file has inconsistent artifact or source-attempt attribution.",
    );
  const facts = value.verified_bytes.jsonl;
  if (
    facts !== undefined &&
    (!object(facts) ||
      !bytes(facts.newline_count) ||
      !bytes(facts.tail_bytes) ||
      typeof facts.ends_with_newline !== "boolean" ||
      facts.semantic_validation !== "not_performed" ||
      facts.newline_count > value.artifact.size ||
      facts.tail_bytes > value.artifact.size)
  )
    throw new Error("Recovered file has invalid newline or tail byte facts.");
}

function validateReceipt(
  value: unknown,
  ref: RecoveredReference,
  run: Pick<LabRun, "id" | "target_id" | "spec_digest">,
): asserts value is RecoveredReceipt {
  if (
    !object(value) ||
    value.format !== "tinystudio.operator-recovered-evidence.v1" ||
    value.classification !== RECOVERED_CLASSIFICATION ||
    !object(value.source) ||
    !text(value.source.project_id) ||
    value.source.run_id !== run.id ||
    value.source.attempt_id !== ref.source_attempt_id ||
    !text(value.source.target_id) ||
    !sha(value.source.spec_digest) ||
    (run.target_id !== undefined && value.source.target_id !== run.target_id) ||
    (run.spec_digest !== undefined &&
      value.source.spec_digest !== run.spec_digest) ||
    value.author_id !== ref.author_id ||
    value.idempotency_key !== ref.idempotency_key ||
    value.request_digest !== ref.request_digest ||
    !states(value.source_states) ||
    !text(value.source_states_observed_at) ||
    !artifact(value.manifest) ||
    value.manifest.id !== ref.manifest_artifact_id ||
    value.manifest.sha256 !== ref.manifest_sha256 ||
    !object(value.producer_manifest) ||
    !Array.isArray(value.files)
  )
    throw new Error(
      "Recovered evidence receipt has inconsistent source identity or attribution.",
    );
  const qualification = value.qualification;
  if (
    !object(qualification) ||
    qualification.scope !== "bytes_and_existing_server_identity_only" ||
    qualification.operator_supplied !== true ||
    qualification.scientific_truth !== false ||
    qualification.physical_completion !== false ||
    qualification.runtime_attestation_verified !== false ||
    qualification.execution_authority_granted !== false ||
    qualification.cleanup_and_outcome !== "producer_claims_only"
  )
    throw new Error(
      "Recovered evidence receipt has an inconsistent qualification.",
    );
  value.files.forEach((file) => validateFile(file, ref.source_attempt_id));
}

function current(signal: AbortSignal) {
  if (signal.aborted)
    throw new DOMException(
      "Recovered evidence request cancelled",
      "AbortError",
    );
}

export async function loadRecoveredReferences(
  api: ApiClient,
  runId: string,
  signal: AbortSignal,
): Promise<RecoveredReference[]> {
  current(signal);
  const result: unknown = await api.get(
    `/api/lab/runs/${encodeURIComponent(runId)}/recovered-evidence`,
    signal,
  );
  current(signal);
  if (
    !object(result) ||
    result.classification !== RECOVERED_CLASSIFICATION ||
    !Array.isArray(result.references)
  )
    throw new Error(
      "Recovered evidence references have an invalid classification or envelope.",
    );
  result.references.forEach(validateReference);
  return result.references as RecoveredReference[];
}

/** Verify byte identity without decoding, rewriting or interpreting the file. */
export async function verifyRecoveredArtifact(
  api: ApiClient,
  identity: unknown,
  signal: AbortSignal,
): Promise<Blob> {
  current(signal);
  if (!artifact(identity))
    throw new Error(
      "Recovered artifact requires an exact ID, SHA-256 and safe byte size.",
    );
  if (!globalThis.crypto?.subtle)
    throw new Error(
      "Recovered artifact verification requires browser SHA-256 support.",
    );
  const blob = await api.download(retainedArtifactPath(identity.id), signal);
  current(signal);
  if (blob.size !== identity.size)
    throw new Error("Recovered artifact size mismatch.");
  const buffer = await blob.arrayBuffer();
  current(signal);
  if (buffer.byteLength !== identity.size)
    throw new Error("Recovered artifact size mismatch.");
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  current(signal);
  const actual = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  if (actual !== identity.sha256.toLowerCase())
    throw new Error("Recovered artifact checksum mismatch.");
  return blob;
}

export async function loadRecoveredReceipt(
  api: ApiClient,
  reference: unknown,
  run: Pick<LabRun, "id" | "target_id" | "spec_digest">,
  signal: AbortSignal,
) {
  validateReference(reference);
  const blob = await verifyRecoveredArtifact(api, reference.receipt, signal);
  const buffer = await blob.arrayBuffer();
  current(signal);
  const value = scientificJson.parse(
    new TextDecoder("utf-8", { fatal: true }).decode(buffer),
  );
  validateReceipt(value, reference, run);
  return { value, blob };
}

export async function downloadRecoveredFile(
  api: ApiClient,
  file: unknown,
  signal: AbortSignal,
): Promise<Blob> {
  validateFile(file);
  const blob = await verifyRecoveredArtifact(api, file.artifact, signal);
  const facts = file.verified_bytes.jsonl;
  if (facts !== undefined) {
    const data = new Uint8Array(await blob.arrayBuffer());
    current(signal);
    let newlines = 0,
      tail = 0;
    for (const byte of data) {
      if (byte === 10) {
        newlines++;
        tail = 0;
      } else tail++;
    }
    if (
      newlines !== facts.newline_count ||
      tail !== facts.tail_bytes ||
      (data.length > 0 && data[data.length - 1] === 10) !==
        facts.ends_with_newline
    )
      throw new Error("Recovered file newline or tail byte facts mismatch.");
  }
  return blob;
}
