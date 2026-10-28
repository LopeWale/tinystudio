import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { JobDialog } from "./training";
import { createApiClient } from "../../api/client";
import { WorkspaceScope } from "../../controllers/workspaceScope";

afterEach(cleanup);
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const snapshots = [
  { name: "older", training_count: 10, validation_count: 2 },
  { name: "promoted-c", training_count: 15, validation_count: 5 },
];
function runtime(api: any, scopeKey = "u:fixture") {
  return {
    api,
    scopeKey,
    project: scopeKey.split(":")[1],
    user: { id: "u" },
    overview: { tree: { datasets: [] } },
    route: "/finetuning",
    evidence: new EvidenceRegistry(),
    navigate: vi.fn(),
    toast: vi.fn(),
    refreshOverview: vi.fn(),
  } as any;
}
function view(runtime: any, props: any = {}) {
  return (
    <StudioContext.Provider value={runtime}>
      <MemoryRouter>
        <JobDialog onClose={() => {}} {...props} />
      </MemoryRouter>
    </StudioContext.Provider>
  );
}

describe("fresh snapshot to explicit training handoff", () => {
  it("fetches promoted snapshots despite an empty stale overview and only creates the chosen job", async () => {
    const api = {
      get: vi.fn(async () => snapshots),
      post: vi.fn(async () => ({ name: "explicit-job" })),
    };
    const s = runtime(api);
    render(view(s));
    expect(
      await screen.findByRole("option", { name: "promoted-c — 15/5" }),
    ).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(
      "/api/snapshots",
      expect.any(AbortSignal),
    );
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "explicit-job" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Snapshot" }), {
      target: { value: "promoted-c" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create job" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledExactlyOnceWith("/api/jobs", {
        name: "explicit-job",
        snapshot: "promoted-c",
        reasoning: false,
      }),
    );
    expect(s.navigate).toHaveBeenCalledWith("#/finetuning/explicit-job");
  });
  it("keeps an explicit cleared snapshot invalid until another available snapshot is chosen", async () => {
    const api = { get: vi.fn(async () => snapshots), post: vi.fn() };
    render(view(runtime(api), { snapshot: "promoted-c" }));
    await screen.findByRole("option", { name: "promoted-c — 15/5" });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "named-job" },
    });
    expect(screen.getByRole("button", { name: "Create job" })).toBeEnabled();
    fireEvent.change(screen.getByRole("combobox", { name: "Snapshot" }), {
      target: { value: "" },
    });
    expect(screen.getByRole("combobox", { name: "Snapshot" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "Create job" })).toBeDisabled();
    expect(api.post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("combobox", { name: "Snapshot" }), {
      target: { value: "older" },
    });
    expect(screen.getByRole("button", { name: "Create job" })).toBeEnabled();
  });
  it("blocks creation while snapshots load or fail, with a visible failure and retry", async () => {
    const pending = deferred<any[]>();
    const api = { get: vi.fn(() => pending.promise), post: vi.fn() };
    render(view(runtime(api)));
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "job" },
    });
    expect(screen.getByRole("button", { name: "Create job" })).toBeDisabled();
    expect(screen.getByText("Loading snapshots…")).toBeInTheDocument();
    await act(async () =>
      pending.reject(Error("Snapshot service unavailable")),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Snapshot service unavailable",
    );
    expect(screen.getByRole("button", { name: "Create job" })).toBeDisabled();
    api.get.mockImplementation(async () => snapshots);
    fireEvent.click(screen.getByRole("button", { name: "Retry snapshots" }));
    await screen.findByRole("option", { name: "promoted-c — 15/5" });
    expect(api.post).not.toHaveBeenCalled();
  });
  it("shows a successful empty snapshot list without permitting job creation", async () => {
    const api = { get: vi.fn(async () => []), post: vi.fn() };
    render(view(runtime(api)));
    expect(
      await screen.findByText(
        "Create a snapshot before creating a training job.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Loading snapshots…")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "job" },
    });
    expect(screen.getByRole("combobox", { name: "Snapshot" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Create job" })).toBeDisabled();
    expect(api.post).not.toHaveBeenCalled();
  });
  it("preserves a supplied valid snapshot and later user selection, but rejects a nonexistent supplied choice", async () => {
    const api = { get: vi.fn(async () => snapshots), post: vi.fn() };
    const s = runtime(api);
    const rendered = render(view(s, { snapshot: "promoted-c" }));
    await screen.findByRole("option", { name: "promoted-c — 15/5" });
    expect(screen.getByRole("combobox", { name: "Snapshot" })).toHaveValue(
      "promoted-c",
    );
    fireEvent.change(screen.getByRole("combobox", { name: "Snapshot" }), {
      target: { value: "older" },
    });
    rendered.rerender(
      view(
        { ...s, overview: { tree: { datasets: [] } } },
        { snapshot: "promoted-c" },
      ),
    );
    expect(screen.getByRole("combobox", { name: "Snapshot" })).toHaveValue(
      "older",
    );
    rendered.unmount();
    render(view(s, { snapshot: "deleted-snapshot" }));
    await screen.findByRole("option", { name: "promoted-c — 15/5" });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "job" },
    });
    expect(screen.getByRole("combobox", { name: "Snapshot" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "Create job" })).toBeDisabled();
  });
  it("discards old project results even when their transport ignores abort", async () => {
    const old = deferred<any[]>(),
      fresh = deferred<any[]>();
    const api = {
      get: vi
        .fn()
        .mockReturnValueOnce(old.promise)
        .mockReturnValueOnce(fresh.promise),
      post: vi.fn(),
    };
    const rendered = render(
      view(runtime(api, "u:old"), { snapshot: "old-private" }),
    );
    const signal = api.get.mock.calls[0][1] as AbortSignal;
    rendered.rerender(view(runtime(api, "u:new"), { snapshot: "old-private" }));
    expect(signal.aborted).toBe(true);
    expect(screen.getByRole("button", { name: "Create job" })).toBeDisabled();
    await act(async () =>
      old.resolve([
        { name: "old-private", training_count: 99, validation_count: 1 },
      ]),
    );
    expect(
      screen.queryByRole("option", { name: "old-private — 99/1" }),
    ).not.toBeInTheDocument();
    await act(async () =>
      fresh.resolve([
        { name: "new-project", training_count: 15, validation_count: 5 },
      ]),
    );
    expect(
      await screen.findByRole("option", { name: "new-project — 15/5" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("");
    expect(api.post).not.toHaveBeenCalled();
  });
  it("uses the canonical project-scoped API and discards a pending job response after workspace change", async () => {
    const scope = new WorkspaceScope();
    let project = "first";
    scope.setIdentity("u:first");
    const pending = deferred<Response>();
    const fetcher = vi.fn(async (path: any, options: any) => {
      if (options.method === "POST") return pending.promise;
      return new Response(JSON.stringify(snapshots), {
        headers: { "content-type": "application/json" },
      });
    });
    const api = createApiClient({
      scope,
      getProject: () => project,
      onUnauthorized: vi.fn(),
      fetcher,
    });
    const old = runtime(api, "u:first");
    const closed = vi.fn();
    const rendered = render(
      view(old, { onClose: closed, snapshot: "promoted-c" }),
    );
    await screen.findByRole("option", { name: "promoted-c — 15/5" });
    expect(fetcher.mock.calls[0][0]).toBe("/api/p/first/snapshots");
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "job" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create job" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(fetcher.mock.calls[1][0]).toBe("/api/p/first/jobs");
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
      name: "job",
      snapshot: "promoted-c",
      reasoning: false,
    });
    project = "second";
    scope.setIdentity("u:second");
    const fresh = runtime(api, "u:second");
    rendered.rerender(view(fresh, { onClose: closed }));
    await screen.findByRole("option", { name: "promoted-c — 15/5" });
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("");
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "new-workspace-job" },
    });
    expect(screen.getByRole("button", { name: "Create job" })).toBeEnabled();
    await act(async () =>
      pending.resolve(
        new Response(JSON.stringify({ name: "job" }), {
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    expect(closed).not.toHaveBeenCalled();
    expect(old.navigate).not.toHaveBeenCalled();
    expect(fresh.navigate).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "new-workspace-job",
    );
    expect(screen.getByRole("button", { name: "Create job" })).toBeEnabled();
    expect(
      fetcher.mock.calls.filter(([, options]) => options.method === "POST"),
    ).toHaveLength(1);
    expect(fetcher.mock.calls.at(-1)![0]).toBe("/api/p/second/snapshots");
  });
});
