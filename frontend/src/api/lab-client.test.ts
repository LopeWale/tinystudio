import { describe, expect, it, vi } from "vitest";
import { createApiClient, scopedPath } from "./client";
import { WorkspaceScope } from "../controllers/workspaceScope";

describe("laboratory scoped downloads", () => {
  it("scopes Lab endpoints without matching similarly named global routes", () => {
    expect(scopedPath("/api/lab/runs/r/export", "a/b")).toBe(
      "/api/p/a%2Fb/lab/runs/r/export",
    );
    expect(scopedPath("/api/laboratory", "alpha")).toBe("/api/laboratory");
    expect(scopedPath("/api/p/alpha/lab/runs", "alpha")).toBe(
      "/api/p/alpha/lab/runs",
    );
  });
  it("downloads through the current authenticated transport", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response("bundle", {
          headers: { "content-type": "application/zip" },
        }),
    );
    const api = createApiClient({
      scope: new WorkspaceScope(),
      getProject: () => "alpha",
      getUser: () => "alice",
      onUnauthorized: vi.fn(),
      fetcher,
    });
    const bundle = await api.download("/api/lab/runs/r/export");
    expect(bundle.size).toBe(6);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/p/alpha/lab/runs/r/export",
      expect.objectContaining({
        credentials: "same-origin",
        headers: expect.objectContaining({ "X-Conversation-Owner": "alice" }),
      }),
    );
  });
  it.each(["alice:beta", "bob:alpha"])(
    "discards a late download after scope changes to %s",
    async (identity) => {
      const scope = new WorkspaceScope();
      scope.setIdentity("alice:alpha");
      let resolve!: (response: Response) => void;
      const fetcher = vi.fn(
        (_url: RequestInfo | URL, _init?: RequestInit) =>
          new Promise<Response>((r) => {
            resolve = r;
          }),
      );
      const api = createApiClient({
        scope,
        getProject: () => "alpha",
        onUnauthorized: vi.fn(),
        fetcher,
      });
      const pending = api.download("/api/lab/runs/r/export");
      scope.setIdentity(identity);
      resolve(new Response("bundle"));
      await expect(pending).rejects.toMatchObject({ name: "AbortError" });
      expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
    },
  );
});
