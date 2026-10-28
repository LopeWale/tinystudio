import { StrictMode } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  canCaptureApiEvidence,
  EvidenceRegistry,
  sanitizeEvidence,
} from "../src/app/evidence";
import { StudioContext, useResource } from "../src/app/StudioContext";
import { Evidence } from "../src/components/studio/primitives";
afterEach(cleanup);
describe("retained evidence security and omission", () => {
  it("redacts prefixed/nested credentials with explicit markers while preserving nonsensitive numeric records", () => {
    expect(
      sanitizeEvidence({
        cf_access_client_secret: "private-1",
        openai_api_key: "private-2",
        nested: {
          refreshToken: "private-3",
          passwordHash: "private-4",
          authorization_header: "private-5",
        },
        accuracy: 0.75,
        correlation: null,
      }),
    ).toEqual({
      cf_access_client_secret: "[redacted]",
      openai_api_key: "[redacted]",
      nested: {
        refreshToken: "[redacted]",
        passwordHash: "[redacted]",
        authorization_header: "[redacted]",
      },
      accuracy: 0.75,
      correlation: null,
    });
  });
  it.each([
    "/api/auth/status",
    "/api/settings",
    "/api/config?full=true",
    "/api/keys",
    "/api/users",
    "/api/assistant/models",
    "/api/tasks",
    "/api/hub/token",
    "/api/p/project/settings",
    "/api/p/project/assistant/conversation",
  ])("excludes sensitive endpoint %s", (path) =>
    expect(canCaptureApiEvidence(path)).toBe(false),
  );
  it("permits ordinary project scientific data and leaves explicit bounded-omission counts stable across snapshots", () => {
    expect(canCaptureApiEvidence("/api/interp/captures/demo/layers/0")).toBe(
      true,
    );
    expect(canCaptureApiEvidence("/api/p/project/datasets")).toBe(true);
    const registry = new EvidenceRegistry();
    registry.register({
      id: "bounded",
      label: "Data",
      route: "/datasets",
      project: "p",
      user: "u",
      data: {
        rows: Array.from({ length: 102 }, (_, i) => i),
        text: "x".repeat(12005),
        fields: Object.fromEntries(
          Array.from({ length: 82 }, (_, i) => ["field" + i, i]),
        ),
      },
    });
    const data = registry.snapshot()[0].data as any;
    expect(data.rows).toHaveLength(101);
    expect(data.rows[100]).toBe("[omitted: 2 additional array items]");
    expect(data.text).toBe(
      "x".repeat(12000) + "[omitted: 5 additional characters]",
    );
    expect(data.fields.__omitted_fields__).toBe(
      "[omitted: 2 additional fields]",
    );
    expect(registry.snapshot()[0].data).toEqual(data);
  });
  it("does not leak credentials through picking a sensitive field/region or the credentials route", () => {
    const context = {
      api: {},
      scopeKey: "u:p",
      route: "/datasets",
      project: "p",
      user: { id: "u" },
      evidence: new EvidenceRegistry(),
    } as any;
    render(
      <StudioContext.Provider value={context}>
        <Evidence label="Row" data={{ accuracy: 0.5 }}>
          <label htmlFor="secret">Provider credential</label>
          <input id="secret" />
          <input type="password" aria-label="Password" />
          <span data-assistant-sensitive>Private display</span>
          <span>Public display</span>
        </Evidence>
      </StudioContext.Provider>,
    );
    expect(
      context.evidence.pick(screen.getByLabelText("Provider credential")),
    ).toBeUndefined();
    expect(
      context.evidence.pick(screen.getByLabelText("Password")),
    ).toBeUndefined();
    expect(
      context.evidence.pick(screen.getByText("Private display")),
    ).toBeUndefined();
    expect(
      context.evidence.pick(screen.getByText("Public display"))?.data,
    ).toEqual({ accuracy: 0.5 });
    context.evidence.register({
      id: "key",
      label: "Generated key",
      data: { value: "do not capture" },
      route: "/api-keys",
      project: "p",
      user: "u",
    });
    expect(context.evidence.snapshot()).toHaveLength(1);
  });
  it("does not allow a snapshot consumer to mutate retained ownership or data", () => {
    const registry = new EvidenceRegistry();
    registry.register({
      id: "one",
      label: "One",
      route: "/datasets",
      project: "p",
      user: "u",
      data: { nested: { score: 1 } },
    });
    const snapshot = registry.snapshot();
    snapshot[0].project = "foreign";
    (snapshot[0].data as any).nested.score = 0;
    expect(registry.snapshot()[0]).toMatchObject({
      project: "p",
      data: { nested: { score: 1 } },
    });
  });
});
describe("resource identity and committed evidence", () => {
  it("retains same-path data across neuron query selection without refetching and registers it on the current route", async () => {
    const api = {
        get: vi.fn(async () => ({ name: "Capture", layers: [0, 1] })),
      },
      evidence = new EvidenceRegistry(),
      context = {
        api,
        evidence,
        project: "p",
        user: { id: "u" },
        scopeKey: "u:p",
        route: "/interp/c/demo?neuron=1",
      } as any;
    function View() {
      const resource = useResource<any>("/api/interp/captures/demo");
      return <p>{resource.data?.name ?? "Loading"}</p>;
    }
    const view = render(
      <StrictMode>
        <StudioContext.Provider value={context}>
          <View />
        </StudioContext.Provider>
      </StrictMode>,
    );
    await screen.findByText("Capture");
    const requests = api.get.mock.calls.length;
    expect(requests).toBe(2);
    expect(evidence.snapshot()[0]).toMatchObject({
      label: "GET /api/interp/captures/demo",
      route: "/interp/c/demo?neuron=1",
    });
    evidence.clear();
    view.rerender(
      <StrictMode>
        <StudioContext.Provider
          value={{ ...context, route: "/interp/c/demo?neuron=2" }}
        >
          <View />
        </StudioContext.Provider>
      </StrictMode>,
    );
    expect(screen.getByText("Capture")).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledTimes(requests);
    expect(evidence.snapshot()).toHaveLength(1);
    expect(evidence.snapshot()[0].route).toBe("/interp/c/demo?neuron=2");
    view.unmount();
    expect(evidence.snapshot()).toEqual([]);
  });
  it("never registers a stale delayed response after path/account/project replacement", async () => {
    let finish!: (data: any) => void;
    const api = {
        get: vi.fn((path: string) =>
          path.endsWith("first")
            ? new Promise((resolve) => (finish = resolve))
            : Promise.resolve({ name: "Current" }),
        ),
      },
      evidence = new EvidenceRegistry(),
      context = {
        api,
        evidence,
        project: "p",
        user: { id: "u" },
        scopeKey: "u:p",
        route: "/datasets",
      } as any;
    function View({ path }: { path: string }) {
      const resource = useResource<any>(path);
      return <p>{resource.data?.name ?? "Loading"}</p>;
    }
    const view = render(
      <StudioContext.Provider value={context}>
        <View path="/api/datasets/first" />
      </StudioContext.Provider>,
    );
    view.rerender(
      <StudioContext.Provider
        value={{ ...context, project: "new", scopeKey: "u:new" }}
      >
        <View path="/api/datasets/second" />
      </StudioContext.Provider>,
    );
    await screen.findByText("Current");
    await act(async () => finish({ name: "STALE" }));
    expect(screen.queryByText("STALE")).not.toBeInTheDocument();
    expect(evidence.snapshot()).toEqual([
      expect.objectContaining({ project: "new", data: { name: "Current" } }),
    ]);
  });
  it("does not register rendered sensitive endpoint data", async () => {
    const context = {
      api: { get: vi.fn(async () => ({ openai_api_key: "credential" })) },
      evidence: new EvidenceRegistry(),
      project: "p",
      user: { id: "u" },
      scopeKey: "u:p",
      route: "/settings",
    } as any;
    function View() {
      const resource = useResource("/api/settings");
      return <p>{resource.loading ? "Loading" : "Settings loaded"}</p>;
    }
    render(
      <StudioContext.Provider value={context}>
        <View />
      </StudioContext.Provider>,
    );
    await screen.findByText("Settings loaded");
    expect(context.evidence.snapshot()).toEqual([]);
  });
});
