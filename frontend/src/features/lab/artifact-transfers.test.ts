import { afterEach, expect, it, vi } from "vitest";
import type { ApiClient } from "../../api/client";
import { ArtifactTransferClient, hashFile } from "./artifact-transfers";

const abc = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
const empty =
  "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
const partBytes = 5 * 1024 ** 2;
const selection = { role: "source", visibility: "evaluation" as const };
const policy = {
  backend: "r2",
  resumable: true,
  policy: {
    per_file: { max_bytes: null, max_files: null },
    aggregate: { max_bytes: null, max_files: null },
    geometry: { part_bytes: partBytes, concurrency: 2 },
  },
};
const file = () =>
  new File(["abc"], "authored.bin", { type: "application/octet-stream" });
const intent = (value = file(), sha256 = abc) => ({
  name: value.name,
  size: value.size,
  sha256,
  media_type: value.type || "application/octet-stream",
  ...selection,
});
const row = (state = "uploading", value = intent()) => ({
  id: "session-one",
  state,
  intent: value,
  plan: {
    size: value.size,
    part_bytes: partBytes,
    part_count: Math.ceil(value.size / partBytes),
    zero: value.size === 0,
    mode: value.size === 0 ? "empty_put" : "multipart",
  },
  operation_id: null,
  operation_state: null,
  artifact: null,
  error_class: null,
});
const verified = (value = intent()) => ({
  ...row("verified", value),
  artifact: { id: "artifact-one", ...value },
});
function fixture(
  overrides: {
    get?: (path: string) => unknown;
    post?: (path: string, body: any) => unknown;
  } = {},
) {
  const storage = new Map<string, string>();
  const memory = {
    get length() {
      return storage.size;
    },
    key: (n: number) => [...storage.keys()][n] ?? null,
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  };
  const get = vi.fn(
    async (path: string) =>
      overrides.get?.(path) ??
      (path.endsWith("transfer-policy")
        ? policy
        : path.endsWith("/parts")
          ? { transfer: row(), parts: [] }
          : verified()),
  );
  const post = vi.fn(
    async (path: string, body?: any) =>
      overrides.post?.(path, body) ??
      (path.endsWith("/transfers")
        ? row()
        : /\/parts\/\d+$/.test(path)
          ? {
              url: "https://objects.invalid/object?X-Amz-Signature=never-persist-this",
              headers: { "Content-Length": "3" },
              expires_in: 30,
              part_number: 1,
              offset: 0,
              size: 3,
            }
          : verified()),
  );
  const upload = vi.fn(async () => verified().artifact);
  const fetcher = vi.fn(async () => new Response(null, { status: 200 }));
  const make = (project = "project", origin = "https://studio.invalid") =>
    new ArtifactTransferClient({ get, post, upload } as unknown as ApiClient, {
      origin,
      project,
      storage: memory,
      fetcher,
      pollIntervalMs: 0,
    });
  return { storage, get, post, upload, fetcher, make };
}

afterEach(() => vi.restoreAllMocks());

it("hashes standard vectors through slices without reading the whole File", async () => {
  const value = file();
  Object.defineProperty(value, "arrayBuffer", {
    value: () => {
      throw Error("whole-file read");
    },
  });
  const slice = vi.spyOn(value, "slice");
  const progress: number[] = [];
  expect(
    await hashFile(value, {
      chunkBytes: 1,
      onProgress: (loaded) => progress.push(loaded),
    }),
  ).toBe(abc);
  expect(slice.mock.calls.map(([start, end]) => [start, end])).toEqual([
    [0, 1],
    [1, 2],
    [2, 3],
  ]);
  expect(progress.at(-1)).toBe(3);
  expect(await hashFile(new File([], "empty"))).toBe(empty);
});

it("uses authenticated controls and an isolated signed PUT without forbidden Content-Length", async () => {
  const f = fixture();
  const progress: string[] = [];
  const artifact = await f.make().upload(file(), selection, {
    onProgress: (value) => progress.push(value.phase),
  });
  expect(artifact).toEqual(verified().artifact);
  expect(f.fetcher).toHaveBeenCalledOnce();
  const [url, request] = f.fetcher.mock.calls[0] as unknown as [
    string,
    RequestInit,
  ];
  expect(url).toContain("objects.invalid");
  expect(request.credentials).toBe("omit");
  expect(request.redirect).toBe("error");
  expect(request.headers).toEqual({});
  expect((request.body as Blob).size).toBe(3);
  expect(progress).toContain("hashing");
  expect(progress).toContain("uploading");
  expect(progress.at(-1)).toBe("verified");
  expect([...f.storage.values()].join()).not.toMatch(
    /Signature|never-persist|https:\/\/objects/,
  );
  expect(f.post.mock.calls.some(([path]) => path.endsWith("/abort"))).toBe(
    false,
  );
});

it("resumes the same journal after an uploaded part's response is lost", async () => {
  let accepted = false;
  const f = fixture({
    get: (path) =>
      path.endsWith("session-one")
        ? row()
        : path.endsWith("/parts")
          ? {
              transfer: row(),
              parts: accepted
                ? [{ number: 1, size: 3, etag: '"provider-etag"' }]
                : [],
            }
          : undefined,
  });
  f.fetcher.mockImplementationOnce(async () => {
    accepted = true;
    throw Error("https://objects.invalid/?signed-secret");
  });
  await expect(f.make().upload(file(), selection)).rejects.toThrow(/resume/i);
  const stored = [...f.storage.values()].join();
  expect(stored).not.toContain("signed-secret");
  const session = JSON.parse([...f.storage.values()][0]);
  expect(session.session_id).toBe("session-one");
  expect(await f.make().upload(file(), selection)).toEqual(verified().artifact);
  expect(f.fetcher).toHaveBeenCalledOnce();
  expect(
    f.post.mock.calls.filter(([path]) => path === "/api/lab/transfers"),
  ).toHaveLength(1);
});

it("persists the immutable request key before admission and never overwrites a journal with mismatched rows", async () => {
  let bad = false;
  const f = fixture({
    get: (path) =>
      path === "/api/lab/transfers/session-one"
        ? bad
          ? { ...verified(), id: "foreign-session" }
          : verified()
        : undefined,
  });
  await f.make().upload(file(), selection);
  const before = [...f.storage.entries()];
  bad = true;
  await expect(f.make().upload(file(), selection)).rejects.toThrow();
  expect([...f.storage.entries()]).toEqual(before);
  expect(f.make("other-project").pending()).toEqual([]);
  expect(f.make("project", "https://other.invalid").pending()).toEqual([]);
});

it("keeps a pre-admission key when the create response is lost and reuses the exact request", async () => {
  let fail = true;
  const f = fixture({
    post: (path) => {
      if (path.endsWith("/transfers") && fail) throw Error("network");
    },
  });
  await expect(f.make().upload(file(), selection)).rejects.toThrow();
  const first = f.post.mock.calls[0][1];
  expect(JSON.parse([...f.storage.values()][0]).idempotency_key).toBe(
    first.idempotency_key,
  );
  fail = false;
  await f.make().upload(file(), selection);
  expect(
    f.post.mock.calls
      .filter(([path]) => path.endsWith("/transfers"))
      .map(([, body]) => body),
  ).toEqual([first, first]);
});

it.each(["failed", "lost", "cancelled"])(
  "never restarts a %s verifier",
  async (operation_state) => {
    const f = fixture({
      post: (path) =>
        path.endsWith("/transfers")
          ? { ...row("verifying"), operation_state }
          : undefined,
    });
    await expect(f.make().upload(file(), selection)).rejects.toThrow(
      /verification/i,
    );
    expect(f.fetcher).not.toHaveBeenCalled();
    expect(f.post).toHaveBeenCalledOnce();
  },
);

it.each([
  { headers: { "Content-Length": "4" } },
  { headers: { "Content-Length": 3 } },
  { headers: { "Content-Length": "3", Authorization: "private-app-token" } },
  { offset: 1 },
  { size: 3.5 },
  { part_number: true },
  { url: "https://user:password@objects.invalid/" },
])(
  "rejects unsafe or mismatched signed descriptors before sending bytes: %j",
  async (patch) => {
    const f = fixture({
      post: (path) =>
        /\/parts\/\d+$/.test(path)
          ? {
              url: "https://objects.invalid/?signed",
              headers: { "Content-Length": "3" },
              expires_in: 30,
              part_number: 1,
              offset: 0,
              size: 3,
              ...patch,
            }
          : undefined,
    });
    await expect(f.make().upload(file(), selection)).rejects.toThrow();
    expect(f.fetcher).not.toHaveBeenCalled();
  },
);

it("does not attach malformed verified receipts", async () => {
  const f = fixture({
    post: (path) =>
      path.endsWith("/complete")
        ? {
            ...verified(),
            artifact: { ...verified().artifact, id: "", size: "3" },
          }
        : undefined,
  });
  await expect(f.make().upload(file(), selection)).rejects.toThrow();
  expect(JSON.parse([...f.storage.values()][0]).state).toBe("uploading");
});

it("seals an empty object without a multipart body and waits for verified metadata", async () => {
  const value = new File([], "empty.bin"),
    selected = intent(value, empty);
  let complete = false;
  const f = fixture({
    get: (path) =>
      path.endsWith("/parts")
        ? { transfer: row("uploading", selected), parts: [] }
        : path.endsWith("session-one")
          ? verified(selected)
          : undefined,
    post: (path) =>
      path.endsWith("/transfers")
        ? row("uploading", selected)
        : path.endsWith("/complete")
          ? ((complete = true),
            { ...row("verifying", selected), operation_state: "queued" })
          : undefined,
  });
  expect(await f.make().upload(value, selection)).toEqual(
    verified(selected).artifact,
  );
  expect(complete).toBe(true);
  expect(f.fetcher).not.toHaveBeenCalled();
});

it("preserves null and zero local proxy budgets", async () => {
  const f = fixture({
    get: (path) =>
      path.endsWith("transfer-policy")
        ? {
            ...policy,
            backend: "local",
            resumable: false,
            policy: {
              ...policy.policy,
              per_file: { max_bytes: 0, max_files: null },
            },
          }
        : undefined,
  });
  await expect(f.make().upload(file(), selection)).rejects.toThrow(/budget/i);
  expect(f.upload).not.toHaveBeenCalled();
  const unlimited = fixture({
    get: (path) =>
      path.endsWith("transfer-policy")
        ? { ...policy, backend: "local", resumable: false }
        : undefined,
  });
  expect(await unlimited.make().upload(file(), selection)).toEqual(
    verified().artifact,
  );
  expect(unlimited.upload).toHaveBeenCalledOnce();
  expect(unlimited.fetcher).not.toHaveBeenCalled();
});

it("does not impose a browser cap on larger selected operational allowances", async () => {
  const f = fixture({
    get: (path) =>
      path.endsWith("transfer-policy")
        ? {
            ...policy,
            policy: {
              ...policy.policy,
              per_file: { max_bytes: 10 ** 30, max_files: null },
              geometry: { ...policy.policy.geometry, concurrency: 10 ** 30 },
            },
          }
        : undefined,
  });
  expect(await f.make().upload(file(), selection)).toEqual(verified().artifact);
  expect(f.fetcher).toHaveBeenCalledOnce();
});

it("aborts only on an explicit action, retaining uncertain aborts", async () => {
  const f = fixture({
    get: (path) => (path.endsWith("session-one") ? row() : undefined),
    post: (path) => (path.endsWith("/abort") ? row("aborted") : undefined),
  });
  f.fetcher.mockRejectedValueOnce(Error("network"));
  await expect(f.make().upload(file(), selection)).rejects.toThrow();
  expect(f.post.mock.calls.some(([path]) => path.endsWith("/abort"))).toBe(
    false,
  );
  await f.make().abort(f.make().pending()[0]);
  expect(f.post.mock.calls.at(-1)?.[0]).toBe(
    "/api/lab/transfers/session-one/abort",
  );
  expect(f.make().pending()).toEqual([]);
});

it("resumes an admitted frozen request when the current new-upload budget has changed", async () => {
  let constrained = false;
  const f = fixture({
    get: (path) =>
      path.endsWith("session-one")
        ? row()
        : path.endsWith("transfer-policy") && constrained
          ? {
              ...policy,
              policy: {
                ...policy.policy,
                per_file: { max_bytes: 0, max_files: null },
              },
            }
          : undefined,
  });
  f.fetcher.mockRejectedValueOnce(Error("lost response"));
  await expect(f.make().upload(file(), selection)).rejects.toThrow();
  constrained = true;
  expect(await f.make().upload(file(), selection)).toEqual(verified().artifact);
  expect(
    f.post.mock.calls.filter(([path]) => path === "/api/lab/transfers"),
  ).toHaveLength(1);
});

it("uploads missing parts within selected concurrency and preserves signed headers only", async () => {
  const value = new File(
    [new Uint8Array(partBytes * 2), "abc"],
    "multipart.bin",
    { type: "application/octet-stream" },
  );
  const selected = intent(value, await hashFile(value));
  let active = 0,
    maximum = 0;
  const f = fixture({
    get: (path) =>
      path.endsWith("/parts")
        ? {
            transfer: row("uploading", selected),
            parts: [{ number: 2, size: partBytes, etag: "provider-confirmed" }],
          }
        : undefined,
    post: (path) =>
      path.endsWith("/transfers")
        ? row("uploading", selected)
        : path.endsWith("/complete")
          ? verified(selected)
          : /\/parts\/\d+$/.test(path)
            ? (() => {
                const number = Number(path.split("/").at(-1));
                const offset = (number - 1) * partBytes,
                  size = Math.min(partBytes, value.size - offset);
                return {
                  url: `https://objects.invalid/?part=${number}`,
                  part_number: number,
                  offset,
                  size,
                  expires_in: 30,
                  headers: {
                    "Content-Length": String(size),
                    "Content-Type": "application/octet-stream",
                    "x-amz-checksum-sha256": "signed-part-checksum",
                  },
                };
              })()
            : undefined,
  });
  f.fetcher.mockImplementation(async () => {
    active++;
    maximum = Math.max(maximum, active);
    await new Promise((resolve) => setTimeout(resolve, 0));
    active--;
    return new Response(null, { status: 200 });
  });
  expect(await f.make().upload(value, selection)).toEqual(
    verified(selected).artifact,
  );
  expect(maximum).toBe(2);
  expect(
    f.post.mock.calls
      .filter(([path]) => /\/parts\/\d+$/.test(path))
      .map(([path]) => path.at(-1)),
  ).toEqual(["1", "3"]);
  for (const [, request] of f.fetcher.mock.calls as unknown as [
    string,
    RequestInit,
  ][]) {
    expect(request.headers).toEqual({
      "Content-Type": "application/octet-stream",
      "x-amz-checksum-sha256": "signed-part-checksum",
    });
    expect(request.credentials).toBe("omit");
    expect(request.redirect).toBe("error");
  }
});

it("pauses hashing without admission, a provider abort, or a whole-file read", async () => {
  const f = fixture(),
    controller = new AbortController();
  await expect(
    f.make().upload(file(), selection, {
      signal: controller.signal,
      onProgress: (value) => {
        if (value.phase === "hashing") controller.abort();
      },
    }),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(f.post).not.toHaveBeenCalled();
  expect(f.fetcher).not.toHaveBeenCalled();
});

it.each(["..", "bad/session", "bad\\session", "bad?query"])(
  "rejects invalid session route identities without storing them: %s",
  async (id) => {
    const f = fixture({
      post: (path) =>
        path.endsWith("/transfers") ? { ...row(), id } : undefined,
    });
    await expect(f.make().upload(file(), selection)).rejects.toThrow();
    expect(JSON.parse([...f.storage.values()][0]).session_id).toBeUndefined();
    expect(f.fetcher).not.toHaveBeenCalled();
  },
);

it("rejects cross-project aborts before sending controls", async () => {
  const f = fixture();
  f.fetcher.mockRejectedValueOnce(Error("lost response"));
  await expect(f.make().upload(file(), selection)).rejects.toThrow();
  const record = f.make().pending()[0],
    previous = f.post.mock.calls.length;
  await expect(f.make("other").abort(record)).rejects.toThrow(
    /another project/,
  );
  expect(f.post.mock.calls).toHaveLength(previous);
  expect(f.make().pending()[0]).toEqual(record);
});
