import { Blob } from "node:buffer";
import { createHash, webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ApiClient } from "../../api/client";
import {
  downloadRecoveredFile,
  loadRecoveredReceipt,
  loadRecoveredReferences,
  verifyRecoveredArtifact,
} from "./recovered-evidence-api";

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => vi.unstubAllGlobals());
const hash = (bytes: string) =>
  createHash("sha256").update(bytes).digest("hex");
const artifact = (
  bytes: string,
  id = "receipt-1",
  role = "operator_recovered_evidence",
) => ({
  id,
  name: "receipt.json",
  sha256: hash(bytes),
  size: Buffer.byteLength(bytes),
  role,
  visibility: "evaluation",
});
function payload(run = "run-1") {
  return {
    format: "tinystudio.operator-recovered-evidence.v1",
    classification: "operator_recovered_evidence",
    source: {
      project_id: "project-id",
      run_id: run,
      attempt_id: "attempt-1",
      target_id: "target-1",
      spec_digest: "a".repeat(64),
    },
    author_id: "operator-1",
    request_digest: "b".repeat(64),
    idempotency_key: "capture-1",
    source_states: { run: "failed", attempt: "failed" },
    source_states_observed_at: "2026-11-13T12:00:00Z",
    manifest: artifact("producer bytes", "manifest-1", "input"),
    qualification: {
      scope: "bytes_and_existing_server_identity_only",
      operator_supplied: true,
      scientific_truth: false,
      physical_completion: false,
      runtime_attestation_verified: false,
      cleanup_and_outcome: "producer_claims_only",
      execution_authority_granted: false,
    },
    files: [],
    producer_manifest: {
      outcome: { verification: "unverified", physical_stop: "unverified" },
      cleanup: { status: "confirmed" },
    },
  };
}
function reference(document = payload()) {
  return {
    classification: "operator_recovered_evidence",
    receipt: artifact(JSON.stringify(document)),
    author_id: document.author_id,
    source_attempt_id: document.source.attempt_id,
    source_states: { run: "lost", attempt: "lost" },
    manifest_artifact_id: document.manifest.id,
    manifest_sha256: document.manifest.sha256,
    idempotency_key: document.idempotency_key,
    request_digest: document.request_digest,
  };
}

it("loads only scoped recovery references through the encoded run path", async () => {
  const ref = reference();
  const get = vi.fn(async () => ({
    classification: "operator_recovered_evidence",
    references: [ref],
  }));
  const signal = new AbortController().signal;
  const result = await loadRecoveredReferences(
    { get } as unknown as ApiClient,
    "run/one",
    signal,
  );
  expect(result).toEqual([ref]);
  expect(get).toHaveBeenCalledWith(
    "/api/lab/runs/run%2Fone/recovered-evidence",
    signal,
  );
});

it.each([
  { classification: "runtime_verified", references: [] },
  {
    classification: "operator_recovered_evidence",
    references: [
      {
        ...reference(),
        receipt: { ...reference().receipt, visibility: "model" },
      },
    ],
  },
  {
    classification: "operator_recovered_evidence",
    references: [
      {
        ...reference(),
        receipt: { ...reference().receipt, size: Number.MAX_SAFE_INTEGER + 1 },
      },
    ],
  },
])(
  "rejects inconsistent reference envelopes and unsafe byte sizes",
  async (body) => {
    await expect(
      loadRecoveredReferences(
        { get: async () => body } as unknown as ApiClient,
        "run-1",
        new AbortController().signal,
      ),
    ).rejects.toThrow(/recovered|receipt|artifact|size/i);
  },
);

it("verifies size and SHA-256 while preserving the exact downloaded Blob", async () => {
  const wire = "one\r\npartial";
  const blob = new Blob([wire]);
  const download = vi.fn(async (_path: string) => blob);
  const verified = await verifyRecoveredArtifact(
    { download } as unknown as ApiClient,
    artifact(wire, "file/id"),
    new AbortController().signal,
  );
  expect(verified).toBe(blob);
  expect(download.mock.calls[0][0]).toBe("/api/lab/artifacts/file%2Fid");
});

it.each(["hash", "size"])(
  "rejects a recovered artifact %s mismatch",
  async (kind) => {
    const identity = artifact("abc");
    if (kind === "hash") identity.sha256 = "f".repeat(64);
    else identity.size += 1;
    await expect(
      verifyRecoveredArtifact(
        { download: async () => new Blob(["abc"]) } as unknown as ApiClient,
        identity,
        new AbortController().signal,
      ),
    ).rejects.toThrow(kind === "hash" ? /checksum/i : /size/i);
  },
);

it("loads the verified receipt and checks source identity against its run and reference", async () => {
  const document = payload();
  const blob = new Blob([JSON.stringify(document)]);
  const result = await loadRecoveredReceipt(
    { download: async () => blob } as unknown as ApiClient,
    reference(document),
    { id: "run-1", target_id: "target-1", spec_digest: "a".repeat(64) },
    new AbortController().signal,
  );
  expect(result.blob).toBe(blob);
  expect(result.value.producer_manifest).toEqual(document.producer_manifest);
});

it.each(["run", "attempt", "author", "manifest", "qualification"])(
  "rejects a byte-valid receipt with inconsistent %s attribution",
  async (kind) => {
    const document = payload();
    const ref = reference(document);
    if (kind === "run") document.source.run_id = "other-run";
    if (kind === "attempt") document.source.attempt_id = "other-attempt";
    if (kind === "author") document.author_id = "other-author";
    if (kind === "manifest") document.manifest.id = "other-manifest";
    if (kind === "qualification")
      document.qualification.physical_completion = true;
    ref.receipt = artifact(JSON.stringify(document));
    await expect(
      loadRecoveredReceipt(
        {
          download: async () => new Blob([JSON.stringify(document)]),
        } as unknown as ApiClient,
        ref,
        { id: "run-1" },
        new AbortController().signal,
      ),
    ).rejects.toThrow(/source|identity|attribution|qualification|receipt/i);
  },
);

it("rejects unsafe integer lexemes instead of silently rounding producer claims", async () => {
  const text = JSON.stringify(payload()).replace(
    '"cleanup":{"status":"confirmed"}',
    '"cleanup":{"status":"confirmed","counter":9007199254740993}',
  );
  const ref = reference();
  ref.receipt = artifact(text);
  await expect(
    loadRecoveredReceipt(
      { download: async () => new Blob([text]) } as unknown as ApiClient,
      ref,
      { id: "run-1" },
      new AbortController().signal,
    ),
  ).rejects.toThrow(/exact JSON|Integer exceeds/i);
});

it("checks newline and tail byte facts without interpreting observations", async () => {
  const wire = "not JSON\npartial";
  const identity = artifact(wire, "file-1", "input");
  const file = {
    name: "evidence/raw.jsonl",
    artifact: identity,
    attempt_id: null,
    verified_bytes: {
      sha256: identity.sha256,
      size: identity.size,
      jsonl: {
        newline_count: 1,
        tail_bytes: 7,
        ends_with_newline: false,
        semantic_validation: "not_performed",
      },
    },
  };
  const blob = new Blob([wire]);
  expect(
    await downloadRecoveredFile(
      { download: async () => blob } as unknown as ApiClient,
      file,
      new AbortController().signal,
    ),
  ).toBe(blob);
  file.verified_bytes.jsonl.tail_bytes = 6;
  await expect(
    downloadRecoveredFile(
      { download: async () => blob } as unknown as ApiClient,
      file,
      new AbortController().signal,
    ),
  ).rejects.toThrow(/byte facts/i);
});

it("abandons a late artifact response after its run or project was left", async () => {
  let resolve!: (blob: Blob) => void;
  const controller = new AbortController();
  const pending = verifyRecoveredArtifact(
    {
      download: () =>
        new Promise<Blob>((done) => {
          resolve = done;
        }),
    } as unknown as ApiClient,
    artifact("abc"),
    controller.signal,
  );
  controller.abort();
  resolve(new Blob(["abc"]));
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
});
