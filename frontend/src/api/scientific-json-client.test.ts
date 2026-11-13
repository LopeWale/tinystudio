import { expect, it, vi } from "vitest";
import { createApiClient, withScientificJson } from "./client";
import { WorkspaceScope } from "../controllers/workspaceScope";

it("opts in scientific DTO serialization while ordinary requests remain byte-identical", async () => {
  const payloads: string[] = [];
  const fetcher = vi.fn(
    async (_path: RequestInfo | URL, init?: RequestInit) => {
      payloads.push(String(init?.body));
      return new Response("{}", {
        headers: { "content-type": "application/json" },
      });
    },
  );
  const api = createApiClient({
    scope: new WorkspaceScope(),
    getProject: () => "alpha",
    onUnauthorized: vi.fn(),
    fetcher,
  });
  const marked = withScientificJson({
    study: { metadata: { large: 1e20, signed_zero: -0 } },
  });
  await api.post("/api/lab/studies", marked);
  await api.put("/api/lab/studies/study-1", marked);
  const ordinary = {
    metadata: { large: 1e20, signed_zero: -0, missing: undefined },
  };
  await api.post("/api/lab/definitions", ordinary);
  for (const encoded of payloads.slice(0, 2)) {
    expect(encoded).toContain('"large":1e+20');
    expect(encoded).toContain('"signed_zero":-0.0');
    expect(Object.is(JSON.parse(encoded).study.metadata.signed_zero, -0)).toBe(
      true,
    );
  }
  expect(payloads[2]).toBe(JSON.stringify(ordinary));
  expect(Object.keys(marked)).toEqual(["study"]);
});
