import type { ApiClient } from "../../api/client";
import { scientificJson } from "../../api/scientific-json";
import type { RetainedArtifact } from "./evidence";

export const retainedArtifactPath = (id: string) =>
  `/api/lab/artifacts/${encodeURIComponent(id)}`;
export interface RetainedJson<T> {
  value: T;
  /** Original verified bytes, retained for downloading without reserialization. */
  blob: Blob;
}
export interface RetainedJsonOptions<T> {
  label: string;
  validate(value: unknown): asserts value is T;
}

/** Artifact integrity, scientific JSON fidelity and component ownership are checked separately. */
export async function loadRetainedJson<T>(
  api: ApiClient,
  artifact: RetainedArtifact,
  signal: AbortSignal,
  options: RetainedJsonOptions<T>,
): Promise<RetainedJson<T>> {
  const assertCurrent = () => {
    if (signal.aborted)
      throw new DOMException(
        `${options.label} loading cancelled`,
        "AbortError",
      );
  };
  assertCurrent();
  if (
    !artifact.sha256 ||
    !/^[a-f\d]{64}$/i.test(artifact.sha256) ||
    !globalThis.crypto?.subtle
  )
    throw new Error(`${options.label} requires a verifiable SHA-256 checksum.`);
  const blob = await api.download(retainedArtifactPath(artifact.id), signal);
  const bytes = await blob.arrayBuffer();
  assertCurrent();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  assertCurrent();
  const actual = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  if (actual !== artifact.sha256.toLowerCase())
    throw new Error(`${options.label} checksum mismatch.`);
  const value = scientificJson.parse(
    new TextDecoder("utf-8", { fatal: true }).decode(bytes),
  );
  options.validate(value);
  assertCurrent();
  return { value, blob };
}
