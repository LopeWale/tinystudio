import { Blob } from "node:buffer";
import { createHash, webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createApiClient } from "../../api/client";
import { WorkspaceScope } from "../../controllers/workspaceScope";
import { loadRetainedJson } from "./retained-json";

beforeEach(() => {
  vi.stubGlobal("Blob", Blob);
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => vi.unstubAllGlobals());
const checksum = (bytes: string) =>
  createHash("sha256").update(bytes).digest("hex");
function fixture(bytes: string, expected = checksum(bytes)) {
  const fetcher = vi.fn(
    async () =>
      new Response(bytes, { headers: { "content-type": "application/json" } }),
  );
  const scope = new WorkspaceScope();
  scope.setIdentity("researcher:alpha");
  return {
    fetcher,
    artifact: {
      id: "retained/report",
      name: "measurement.json",
      sha256: expected,
    },
    api: createApiClient({
      scope,
      getProject: () => "alpha",
      onUnauthorized: vi.fn(),
      fetcher,
    }),
  };
}
function validate(
  value: unknown,
): asserts value is { value: number; approximation?: number } {
  if (
    !value ||
    typeof value !== "object" ||
    typeof (value as { value?: unknown }).value !== "number"
  )
    throw new Error("Invalid measurement report.");
}
it("verifies immutable bytes and preserves scientific numbers and the exact download blob", async () => {
  const bytes = '{ "value": -0.0, "approximation": 1e30 }\n',
    f = fixture(bytes);
  const retained = await loadRetainedJson(
    f.api,
    f.artifact,
    new AbortController().signal,
    { label: "Measurement", validate },
  );
  expect(Object.is(retained.value.value, -0)).toBe(true);
  expect(retained.value.approximation).toBe(1e30);
  expect(await retained.blob.text()).toBe(bytes);
  expect(f.fetcher).toHaveBeenCalledWith(
    "/api/p/alpha/lab/artifacts/retained%2Freport",
    expect.objectContaining({ method: "GET" }),
  );
});
it("refuses corrupt bytes before invoking the report validator", async () => {
  const f = fixture('{"value":2}', "0".repeat(64)),
    inspect = vi.fn(validate);
  await expect(
    loadRetainedJson(f.api, f.artifact, new AbortController().signal, {
      label: "Measurement",
      validate: inspect,
    }),
  ).rejects.toThrow("Measurement checksum mismatch.");
  expect(inspect).not.toHaveBeenCalled();
});
it("requires a supplied artifact checksum and the browser digest capability", async () => {
  const f = fixture('{"value":2}');
  await expect(
    loadRetainedJson(
      f.api,
      { ...f.artifact, sha256: undefined },
      new AbortController().signal,
      { label: "Measurement", validate },
    ),
  ).rejects.toThrow(/verifiable SHA-256/);
  vi.stubGlobal("crypto", {});
  await expect(
    loadRetainedJson(f.api, f.artifact, new AbortController().signal, {
      label: "Measurement",
      validate,
    }),
  ).rejects.toThrow(/verifiable SHA-256/);
  expect(f.fetcher).not.toHaveBeenCalled();
});
it("rejects malformed JSON and unsafe exact integer tokens after verifying their bytes", async () => {
  for (const bytes of [
    '{"value":',
    '{"value":9007199254740993}',
    '{"value":1e400}',
  ]) {
    const f = fixture(bytes);
    await expect(
      loadRetainedJson(f.api, f.artifact, new AbortController().signal, {
        label: "Measurement",
        validate,
      }),
    ).rejects.toThrow();
  }
});
it("does not release a verified result after its owner aborts during checksum verification", async () => {
  const f = fixture('{"value":2}'),
    controller = new AbortController(),
    inspect = vi.fn(validate);
  vi.stubGlobal("crypto", {
    subtle: {
      digest: async (algorithm: AlgorithmIdentifier, data: BufferSource) => {
        controller.abort();
        return webcrypto.subtle.digest(algorithm as any, data as any);
      },
    },
  });
  await expect(
    loadRetainedJson(f.api, f.artifact, controller.signal, {
      label: "Measurement",
      validate: inspect,
    }),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(inspect).not.toHaveBeenCalled();
});
