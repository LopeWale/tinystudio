import { describe, it, expect, vi } from "vitest";
import { WorkspaceScope } from "../src/controllers/workspaceScope";
import { createApiClient, scopedPath } from "../src/api/client";
describe("workspace ownership", () => {
  it("scopes only project resources and task inventory", () => {
    expect(scopedPath("/api/datasets?limit=2", "a/b")).toBe(
      "/api/p/a%2Fb/datasets?limit=2",
    );
    expect(scopedPath("/api/auth/status", "a")).toBe("/api/auth/status");
    expect(scopedPath("/api/tasks?include_result=false", "a")).toBe(
      "/api/tasks?include_result=false&project=a",
    );
  });
  it.each(["GET", "POST"])(
    "rejects late %s from previous project despite noncooperative transport",
    async (method) => {
      let finish!: (r: Response) => void;
      const scope = new WorkspaceScope();
      scope.setIdentity("u:a");
      const api = createApiClient({
        scope,
        getProject: () => "a",
        onUnauthorized: vi.fn(),
        fetcher: vi.fn(() => new Promise<Response>((r) => (finish = r))),
      });
      const result =
        method === "GET"
          ? api.get("/api/datasets")
          : api.post("/api/datasets", {});
      scope.setIdentity("u:b");
      finish(
        new Response("{}", { headers: { "content-type": "application/json" } }),
      );
      await expect(result).rejects.toMatchObject({ name: "AbortError" });
    },
  );
  it("invalidates all account requests and signals listeners", () => {
    const scope = new WorkspaceScope();
    scope.setIdentity("alice:a");
    const lease = scope.capture();
    scope.setIdentity("bob:a");
    expect(lease.signal.aborted).toBe(true);
    expect(() => lease.assert()).toThrow();
  });
  it("recovers unauthorized session exactly once per response", async () => {
    const unauthorized = vi.fn();
    const api = createApiClient({
      scope: new WorkspaceScope(),
      getProject: () => "",
      onUnauthorized: unauthorized,
      fetcher: vi.fn(
        async () =>
          new Response('{"detail":"expired"}', {
            status: 401,
            headers: { "content-type": "application/json" },
          }),
      ),
    });
    await expect(api.get("/api/overview")).rejects.toThrow("expired");
    expect(unauthorized).toHaveBeenCalledOnce();
  });
});
