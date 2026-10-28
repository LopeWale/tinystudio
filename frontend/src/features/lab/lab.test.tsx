import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
  act,
} from "@testing-library/react";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { LabPage } from "./index";
import { makeDefinition, parseDefinition } from "./definition";
import { toGraph } from "./graph";
import { timelineSeries, numericFields, ReplayViews } from "./replay";
import userEvent from "@testing-library/user-event";
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
afterEach(cleanup);
function fixture() {
  const definition = makeDefinition();
  const row = {
    id: "definition-1",
    name: definition.name,
    revision: 1,
    definition,
  };
  const api = {
    get: vi.fn(async (path: string, _signal?: AbortSignal): Promise<any> => {
      if (path === "/api/lab/catalog")
        return { adapters: [], domains: [], methods: [] };
      if (path === "/api/lab/definitions" || path === "/api/lab/inputs")
        return [];
      if (path === "/api/lab/runs" || path === "/api/lab/operations") return [];
      if (path === "/api/lab/targets")
        return [{ id: "local", name: "Local computer" }];
      if (path.includes("/events?")) return { events: [], next_after: 0 };
      if (path.includes("/records?")) return { records: [] };
      return { id: "run-1", definition_id: row.id, state: "running" };
    }),
    post: vi.fn(
      async (
        path: string,
        _body?: unknown,
        _signal?: AbortSignal,
      ): Promise<any> =>
        path === "/api/lab/definitions"
          ? row
          : path.endsWith("/exports")
            ? {
                id: "export-1",
                kind: "export",
                state: "succeeded",
                result: { artifact: { id: "bundle-1" } },
              }
            : { id: "run-1", definition_id: row.id, state: "running" },
    ),
    put: vi.fn(async () => ({ ...row, revision: 2 })),
    download: vi.fn(
      async (_path: string, _signal?: AbortSignal) => new Blob(["bundle"]),
    ),
    upload: vi.fn(async (_path: string, _body: unknown, _options: unknown) => ({
      id: "input-1",
      name: "recording.jsonl",
      sha256: "checksum-from-server",
      size: 18,
      media_type: "application/x-ndjson",
    })),
  };
  const studio = {
    api,
    scopeKey: "user:alpha",
    project: "alpha",
    user: { id: "user" },
    route: "/lab",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as any;
  return { api, studio, row };
}
function runFixture(state: string) {
  const data = fixture();
  const run = {
    id: "recovery-run",
    definition_id: data.row.id,
    target_id: "local",
    state,
  };
  const baseGet = data.api.get.getMockImplementation()!;
  data.api.get.mockImplementation(async (path, signal) => {
    if (path === "/api/lab/runs") return [run];
    if (path === "/api/lab/runs/recovery-run") return run;
    return baseGet(path, signal);
  });
  return { ...data, run };
}
function mount(studio: any) {
  return render(
    <StudioContext.Provider value={studio}>
      <LabPage />
    </StudioContext.Provider>,
  );
}
async function findRunByName(name: string) {
  fireEvent.click(screen.getByRole("tab", { name: "Runs" }));
  const selector = await screen.findByRole("button", { name: /Retained run/ });
  await waitFor(() => expect(selector).toBeEnabled());
  fireEvent.click(selector);
  return screen.findByRole("option", { name });
}
async function findRetainedRun(state: string) {
  return findRunByName(`recovery-run · ${state}`);
}
describe("laboratory authoring and execution", () => {
  it("keeps experiment controls local and gives transfers and capabilities the full workspace", async () => {
    const { studio, api } = runFixture("succeeded");
    const { container } = mount(studio);
    expect(
      screen.getByRole("button", { name: "New experiment" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /Saved experiment/ }),
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText("Experiment name"), {
      target: { value: "Retained draft" },
    });
    fireEvent.click(screen.getByRole("tab", { name: "Import / export" }));
    expect(
      screen.getByRole("heading", { name: "Operation history" }),
    ).toBeVisible();
    expect(container.querySelector(".studio-template__inspector")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "New experiment" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Choose run to export" }),
    );
    fireEvent.click(await findRetainedRun("succeeded"));
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(
        "/api/lab/runs/recovery-run",
        expect.any(AbortSignal),
      ),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Capabilities" }));
    expect(
      screen.getByRole("table", { name: "Engine capabilities" }),
    ).toBeVisible();
    expect(container.querySelector(".studio-template__inspector")).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Retained run/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Experiment" }));
    expect(screen.getByLabelText("Experiment name")).toHaveValue(
      "Retained draft",
    );
  });
  it("prevents replacing the experiment while its save is pending", async () => {
    const { studio, api, row } = fixture();
    let finish!: (value: typeof row) => void;
    api.post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    mount(studio);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Saved experiment/ }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
    expect(
      screen.getByRole("button", { name: "New experiment" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /Saved experiment/ }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /Experiment engine/ }),
    ).toBeDisabled();
    await act(async () => finish(row));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "New experiment" }),
      ).toBeEnabled(),
    );
    expect(screen.getByRole("button", { name: "Save revision" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: /Experiment engine/ }),
    ).toBeEnabled();
  });
  it("switches exclusive run evidence panes and retains the dataset draft", async () => {
    const { studio, api, run } = runFixture("succeeded");
    const original = api.get.getMockImplementation()!;
    api.get.mockImplementation(async (path, signal) =>
      path === "/api/lab/runs/recovery-run"
        ? {
            ...run,
            result: {
              artifacts: [
                {
                  id: "chat-artifact",
                  name: "messages.jsonl",
                  role: "supervised_dataset",
                  visibility: "model",
                },
              ],
            },
          }
        : original(path, signal),
    );
    mount(studio);
    fireEvent.click(await findRetainedRun("succeeded"));
    expect(screen.getByRole("tab", { name: "Replay" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tabpanel", { name: "Replay" })).toBeVisible();
    expect(
      screen.queryByRole("table", { name: "Run event timeline" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("table", { name: "Retained records" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Dataset" }));
    const name = await screen.findByRole("textbox", { name: "Dataset name" });
    fireEvent.change(name, { target: { value: "Retained dataset draft" } });
    fireEvent.click(screen.getByRole("tab", { name: "Records" }));
    expect(
      screen.getByRole("table", { name: "Retained records" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("textbox", { name: "Dataset name" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Events" }));
    expect(
      screen.getByRole("table", { name: "Run event timeline" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("table", { name: "Retained records" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Dataset" }));
    expect(screen.getByRole("textbox", { name: "Dataset name" })).toBe(name);
    expect(name).toHaveValue("Retained dataset draft");
    fireEvent.click(screen.getByRole("tab", { name: "Experiment" }));
    fireEvent.click(screen.getByRole("tab", { name: "Runs" }));
    expect(screen.getByRole("tab", { name: "Dataset" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(name).toHaveValue("Retained dataset draft");
    expect(api.post).not.toHaveBeenCalled();
  });
  it("shows one work area and authoring pane, with topology disclosed on demand", async () => {
    const { studio } = fixture();
    const rendered = mount(studio);
    expect(
      screen
        .getAllByRole("tabpanel")
        .map((panel) => panel.getAttribute("aria-label")),
    ).toEqual(["Experiment", "Parameters"]);
    expect(rendered.container.querySelector(".react-flow")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Save experiment" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Run saved revision" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("tab", { name: "Inputs" }));
    expect(screen.getByRole("tabpanel", { name: "Inputs" })).toBeVisible();
    expect(
      screen.queryByRole("textbox", { name: "Experiment name" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Capabilities" }));
    expect(
      screen
        .getAllByRole("tabpanel")
        .map((panel) => panel.getAttribute("aria-label")),
    ).toEqual(["Capabilities"]);
    expect(
      screen.getByRole("table", { name: "Engine capabilities" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Import portable bundle" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Import / export" }));
    expect(
      screen.getByRole("button", { name: "Import portable bundle" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("table", { name: "Engine capabilities" }),
    ).not.toBeInTheDocument();
  });
  it("retains draft edits and selected input files while switching work areas", () => {
    const { studio } = fixture();
    mount(studio);
    fireEvent.change(screen.getByLabelText("Experiment name"), {
      target: { value: "Preserved experiment" },
    });
    fireEvent.click(screen.getByRole("tab", { name: "Inputs" }));
    const input = screen.getByLabelText("Import laboratory input", {
      selector: "input",
    }) as HTMLInputElement;
    const file = new File(["data"], "recording.jsonl");
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("tab", { name: "Runs" }));
    fireEvent.click(screen.getByRole("tab", { name: "Experiment" }));
    expect(screen.getByRole("tab", { name: "Inputs" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      (
        screen.getByLabelText("Import laboratory input", {
          selector: "input",
        }) as HTMLInputElement
      ).files?.[0],
    ).toBe(file);
    fireEvent.click(screen.getByRole("tab", { name: "Parameters" }));
    expect(screen.getByLabelText("Experiment name")).toHaveValue(
      "Preserved experiment",
    );
    expect(
      screen.getByRole("button", { name: "Run saved revision" }),
    ).toBeDisabled();
  });
  it("opens an existing retained run and keeps its selection when returning", async () => {
    const { studio, api } = runFixture("succeeded");
    mount(studio);
    fireEvent.click(await findRetainedRun("succeeded"));
    expect(screen.getByRole("tab", { name: "Runs" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith(
        "/api/lab/runs/recovery-run",
        expect.any(AbortSignal),
      ),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Experiment" }));
    fireEvent.click(screen.getByRole("tab", { name: "Runs" }));
    expect(
      screen.getByRole("button", {
        name: /recovery-run · succeeded Retained run/,
      }),
    ).toBeVisible();
    expect(screen.getByRole("tabpanel", { name: "Runs" })).toBeVisible();
  });

  it("uploads a portable bundle then observes the queued import and loads its retained definition", async () => {
    const { api, studio, row } = fixture();
    const imported = {
      ...row,
      name: "Imported experiment",
      definition: { ...row.definition, name: "Imported experiment" },
    };
    const baseGet = api.get.getMockImplementation()!;
    api.post.mockResolvedValueOnce({
      id: "import-1",
      kind: "bundle_import",
      state: "queued",
    });
    api.get.mockImplementation(async (path, signal) =>
      path === "/api/lab/operations/import-1"
        ? {
            id: "import-1",
            kind: "bundle_import",
            state: "succeeded",
            result: {
              definition: imported,
              historical_manifest: { id: "manifest-1" },
              artifact_mapping: { original: "retained" },
            },
          }
        : baseGet(path, signal),
    );
    mount(studio);
    fireEvent.click(screen.getByRole("tab", { name: "Import / export" }));
    const file = new File(["portable-bundle"], "experiment.zip", {
      type: "application/zip",
    });
    fireEvent.change(
      screen.getByLabelText("Portable laboratory bundle", {
        selector: "input",
      }),
      { target: { files: [file] } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Import portable bundle" }),
    );
    await screen.findByText("Import: queued");
    expect(api.upload).toHaveBeenCalledWith(
      "/api/lab/inputs",
      expect.any(FormData),
      { signal: expect.any(AbortSignal) },
    );
    expect((api.upload.mock.calls[0][1] as FormData).get("file")).toBe(file);
    expect(api.post).toHaveBeenCalledExactlyOnceWith(
      "/api/lab/bundle-imports",
      { artifact_id: "input-1", idempotency_key: expect.any(String) },
      expect.any(AbortSignal),
    );
    expect(
      screen.getByRole("button", { name: "Import portable bundle" }),
    ).toBeDisabled();
    await waitFor(
      () =>
        expect(screen.getByLabelText("Experiment name")).toHaveValue(
          "Imported experiment",
        ),
      { timeout: 2500 },
    );
    expect(screen.getByText("Import: succeeded")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save revision" })).toBeEnabled();
    expect(studio.evidence.snapshot()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Laboratory bundle import",
          project: "alpha",
          data: expect.objectContaining({
            result: expect.objectContaining({
              historical_manifest: { id: "manifest-1" },
            }),
          }),
        }),
      ]),
    );
  });
  it.each(["failed", "partial"])(
    "does not submit a bundle import after a %s upload",
    async (failure) => {
      const { api, studio } = fixture();
      if (failure === "failed")
        api.upload.mockRejectedValueOnce(new Error("Upload interrupted"));
      else api.upload.mockResolvedValueOnce({} as any);
      mount(studio);
      fireEvent.click(screen.getByRole("tab", { name: "Import / export" }));
      fireEvent.change(
        screen.getByLabelText("Portable laboratory bundle", {
          selector: "input",
        }),
        { target: { files: [new File(["bundle"], "experiment.zip")] } },
      );
      fireEvent.click(
        screen.getByRole("button", { name: "Import portable bundle" }),
      );
      await screen.findByRole("alert");
      expect(api.post).not.toHaveBeenCalled();
      expect(
        screen.getByRole("button", { name: "Import portable bundle" }),
      ).toBeEnabled();
    },
  );
  it("aborts bundle import polling and clears its evidence after account switch", async () => {
    const { api, studio } = fixture();
    api.post.mockResolvedValueOnce({
      id: "import-1",
      kind: "bundle_import",
      state: "queued",
    });
    const view = mount(studio);
    fireEvent.click(screen.getByRole("tab", { name: "Import / export" }));
    fireEvent.change(
      screen.getByLabelText("Portable laboratory bundle", {
        selector: "input",
      }),
      { target: { files: [new File(["bundle"], "experiment.zip")] } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Import portable bundle" }),
    );
    await screen.findByText("Import: queued");
    const signal = api.post.mock.calls[0][2] as AbortSignal;
    view.rerender(
      <StudioContext.Provider
        value={{ ...studio, scopeKey: "bob:alpha", user: { id: "bob" } }}
      >
        <LabPage />
      </StudioContext.Provider>,
    );
    expect(signal.aborted).toBe(true);
    await act(async () => {});
    expect(screen.queryByText("Import: queued")).not.toBeInTheDocument();
    expect(
      api.get.mock.calls.some(
        ([path]) => path === "/api/lab/operations/import-1",
      ),
    ).toBe(false);
    expect(
      studio.evidence
        .snapshot()
        .some((record: any) => record.label === "Laboratory bundle import"),
    ).toBe(false);
  });
  it("recovers queued and completed operations after remount without admitting duplicate work", async () => {
    const { api, studio, row } = fixture();
    const imported = {
      ...row,
      name: "Recovered import",
      definition: { ...row.definition, name: "Recovered import" },
    };
    const inventory = [
      { id: "queued-export", kind: "export", state: "queued" },
      { id: "done-export", kind: "export", state: "succeeded" },
      { id: "done-import", kind: "import", state: "succeeded" },
    ];
    let queuedReads = 0;
    const baseGet = api.get.getMockImplementation()!;
    api.get.mockImplementation(async (path, signal) => {
      if (path === "/api/lab/operations") return inventory;
      if (path === "/api/lab/operations/queued-export")
        return {
          ...inventory[0],
          state: ++queuedReads === 1 ? "queued" : "succeeded",
          result: queuedReads === 1 ? null : { artifact: { id: "bundle" } },
        };
      if (path === "/api/lab/operations/done-import")
        return {
          ...inventory[2],
          result: {
            definition: imported,
            historical_manifest: { id: "historical" },
          },
        };
      return baseGet(path, signal);
    });
    const first = mount(studio);
    fireEvent.click(screen.getByRole("tab", { name: "Import / export" }));
    await screen.findByRole("button", { name: "Inspect queued-export" });
    expect(api.post).not.toHaveBeenCalled();
    first.unmount();
    const second = mount(studio);
    fireEvent.click(screen.getByRole("tab", { name: "Import / export" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Inspect queued-export" }),
    );
    await screen.findByText("Operation queued-export: queued");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Cancel queued-export" }),
      ).toBeEnabled(),
    );
    await screen.findByText(
      "Operation queued-export: succeeded",
      {},
      { timeout: 2500 },
    );
    expect(api.post).not.toHaveBeenCalled();
    expect(api.upload).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Load imported definition done-import",
      }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Experiment name")).toHaveValue(
        "Recovered import",
      ),
    );
    expect(api.get).toHaveBeenCalledWith(
      "/api/lab/operations/done-import",
      expect.any(AbortSignal),
    );
    expect(api.post).not.toHaveBeenCalled();
    const originalURL = URL;
    const create = vi.fn(() => "blob:recovered-bundle");
    const revoke = vi.fn();
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = create;
        static revokeObjectURL = revoke;
      },
    );
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    try {
      fireEvent.click(screen.getByRole("tab", { name: "Import / export" }));
      fireEvent.click(
        screen.getByRole("button", { name: "Download done-export" }),
      );
      await waitFor(() =>
        expect(api.download).toHaveBeenCalledWith(
          "/api/lab/operations/done-export/download",
          expect.any(AbortSignal),
        ),
      );
      await waitFor(() => expect(click).toHaveBeenCalledOnce());
      expect(revoke).toHaveBeenCalledWith("blob:recovered-bundle");
    } finally {
      click.mockRestore();
      vi.stubGlobal("URL", originalURL);
      second.unmount();
    }
  });
  it("revokes reconnected operation polling when the account changes", async () => {
    const { api, studio } = fixture();
    const baseGet = api.get.getMockImplementation()!;
    api.get.mockImplementation(async (path, signal) => {
      if (path === "/api/lab/operations")
        return [{ id: "old-op", kind: "export", state: "queued" }];
      if (path === "/api/lab/operations/old-op")
        return { id: "old-op", kind: "export", state: "queued" };
      return baseGet(path, signal);
    });
    const view = mount(studio);
    fireEvent.click(screen.getByRole("tab", { name: "Import / export" }));
    const inspect = await screen.findByRole("button", {
      name: "Inspect old-op",
    });
    vi.useFakeTimers();
    try {
      fireEvent.click(inspect);
      await act(async () => {});
      expect(screen.getByText("Operation old-op: queued")).toBeInTheDocument();
      view.rerender(
        <StudioContext.Provider
          value={{ ...studio, scopeKey: "bob:alpha", user: { id: "bob" } }}
        >
          <LabPage />
        </StudioContext.Provider>,
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });
      expect(
        api.get.mock.calls.filter(
          ([path]) => path === "/api/lab/operations/old-op",
        ),
      ).toHaveLength(1);
      expect(
        screen.queryByText("Operation old-op: queued"),
      ).not.toBeInTheDocument();
      expect(api.post).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
  it.each(["cancel", "retry"])(
    "requires an explicit %s action for retained operation recovery",
    async (command) => {
      const { api, studio } = fixture();
      const before = command === "cancel" ? "queued" : "lost";
      const after = command === "cancel" ? "cancelled" : "succeeded";
      const baseGet = api.get.getMockImplementation()!;
      api.get.mockImplementation(async (path, signal) =>
        path === "/api/lab/operations"
          ? [{ id: "retained-op", kind: "export", state: before }]
          : baseGet(path, signal),
      );
      api.post.mockResolvedValueOnce({
        id: "retained-op",
        kind: "export",
        state: after,
      });
      mount(studio);
      fireEvent.click(screen.getByRole("tab", { name: "Import / export" }));
      const control = await screen.findByRole("button", {
        name: `${command === "cancel" ? "Cancel" : "Retry"} retained-op`,
      });
      expect(api.post).not.toHaveBeenCalled();
      fireEvent.click(control);
      await waitFor(() =>
        expect(api.post).toHaveBeenCalledExactlyOnceWith(
          `/api/lab/operations/retained-op/${command}`,
          {},
          expect.any(AbortSignal),
        ),
      );
      await screen.findByText(`Operation retained-op: ${after}`);
    },
  );
  it.each(["lost", "recovery_required"])(
    "treats %s as terminal and only permits an explicit safe retry",
    async (state) => {
      const { api, studio } = runFixture(state);
      mount(studio);
      fireEvent.click(await findRetainedRun(state));
      const retry = await screen.findByRole("button", { name: "Retry run" });
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Export manifest bundle" }),
        ).toBeEnabled(),
      );
      expect(screen.getByRole("button", { name: "Cancel run" })).toBeDisabled();
      if (state === "lost") {
        expect(retry).toBeEnabled();
        fireEvent.click(retry);
        await waitFor(() =>
          expect(api.post).toHaveBeenCalledExactlyOnceWith(
            "/api/lab/runs/recovery-run/retry",
            {},
            expect.any(AbortSignal),
          ),
        );
      } else {
        expect(retry).toBeDisabled();
        expect(api.post).not.toHaveBeenCalled();
      }
    },
  );
  it("recovers an expired runner through the selected target and retains the actual response without retrying", async () => {
    const { api, studio } = runFixture("running");
    const response = { recovered: 1, lost_attempts: ["attempt-1"] };
    api.post.mockResolvedValueOnce(response);
    mount(studio);
    fireEvent.click(await findRetainedRun("running"));
    const recover = await screen.findByRole("button", {
      name: "Recover expired runner",
    });
    await waitFor(() => expect(recover).toBeEnabled());
    fireEvent.click(recover);
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledExactlyOnceWith(
        "/api/lab/targets/local/recover",
        {},
        expect.any(AbortSignal),
      ),
    );
    await screen.findByText(/lost_attempts/);
    expect(studio.evidence.snapshot()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Laboratory runner recovery",
          project: "alpha",
          data: response,
        }),
      ]),
    );
    expect(api.post.mock.calls.some(([path]) => path.endsWith("/retry"))).toBe(
      false,
    );
  });
  it("reads terminal status before its event snapshot so completion evidence cannot be missed", async () => {
    const { api, studio, run } = runFixture("running");
    const baseGet = api.get.getMockImplementation()!;
    let resolveStatus!: (row: any) => void;
    let completionCommitted = false;
    const finalEvent = {
      seq: 42,
      kind: "run_completed",
      payload: { manifest: "retained-final-manifest" },
    };
    api.get.mockImplementation(async (path, signal) => {
      if (path === "/api/lab/runs/recovery-run")
        return new Promise((resolve) => {
          resolveStatus = resolve;
        });
      if (path.includes("/events?"))
        return completionCommitted
          ? {
              events: [finalEvent],
              next_after: 42,
              latest_seq: 42,
              has_more: false,
            }
          : { events: [], next_after: 0, latest_seq: 0, has_more: false };
      return baseGet(path, signal);
    });
    const view = mount(studio);
    fireEvent.click(await findRetainedRun("running"));
    await waitFor(() => expect(resolveStatus).toBeTypeOf("function"));
    const prematureEventRead = api.get.mock.calls.some(([path]) =>
      path.includes("/events?"),
    );
    vi.useFakeTimers();
    try {
      await act(async () => {
        completionCommitted = true;
        resolveStatus({ ...run, state: "succeeded" });
      });
      expect(prematureEventRead).toBe(false);
      expect(screen.getByText("run_completed")).toBeInTheDocument();
      expect(studio.evidence.snapshot()).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            label: "Laboratory run and event lineage",
            data: expect.objectContaining({
              run: expect.objectContaining({ state: "succeeded" }),
              events: [finalEvent],
            }),
          }),
        ]),
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60000);
      });
      expect(
        api.get.mock.calls.filter(
          ([path]) => path === "/api/lab/runs/recovery-run",
        ),
      ).toHaveLength(1);
      expect(
        api.get.mock.calls.filter(([path]) => path.includes("/events?")),
      ).toHaveLength(1);
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });
  it("keeps retained evidence chronology and server record window boundaries", async () => {
    const { api, studio } = runFixture("succeeded");
    const baseGet = api.get.getMockImplementation()!;
    api.get.mockImplementation(async (path, signal) => {
      if (path.includes("/events?"))
        return {
          events: [
            {
              seq: 2,
              time: "first",
              kind: "started",
              payload: { reading: -2 },
            },
            {
              seq: 10,
              time: "second",
              kind: "finished",
              payload: { reading: 0 },
            },
          ],
          next_after: 10,
        };
      if (path.includes("/records?") && path.includes("limit=100&"))
        return {
          records: [
            {
              original: { sensor: "original-value" },
              derived: { sensor: "derived-value" },
              lineage: "retained-lineage",
            },
          ],
          next_offset: path.includes("offset=0&") ? 100 : null,
        };
      return baseGet(path, signal);
    });
    mount(studio);
    fireEvent.click(await findRetainedRun("succeeded"));
    fireEvent.click(screen.getByRole("tab", { name: "Events" }));
    const timeline = await screen.findByRole("table", {
      name: "Run event timeline",
    });
    await within(timeline).findByText("finished");
    expect(
      within(timeline)
        .getAllByRole("row")
        .slice(1)
        .map((row) => within(row).getAllByRole("cell")[0].textContent),
    ).toEqual(["2", "10"]);
    expect(within(timeline).queryByRole("button")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Records" }));
    const records = screen.getByRole("table", { name: "Retained records" });
    await waitFor(() => {
      const cells = within(within(records).getAllByRole("row")[1]).getAllByRole(
        "cell",
      );
      expect(cells[1]).toHaveTextContent("original-value");
      expect(cells[2]).toHaveTextContent("derived-value");
    });
    expect(
      within(records).getByText("Inspect record").closest("details"),
    ).toHaveTextContent("retained-lineage");
    expect(within(records).getAllByRole("row")[1]).toHaveTextContent(/^1/);
    fireEvent.click(screen.getByRole("button", { name: "Next records" }));
    await waitFor(() =>
      expect(within(records).getAllByRole("row")[1]).toHaveTextContent(/^101/),
    );
    expect(api.get).toHaveBeenCalledWith(
      "/api/lab/runs/recovery-run/records?offset=100&limit=100&artifact=processed.jsonl",
      expect.any(AbortSignal),
    );
    expect(
      screen.getByRole("combobox", { name: "Retained records rows per page" }),
    ).toHaveValue("25");
    expect(screen.getByRole("button", { name: "Next records" })).toBeDisabled();
  });
  it("keeps record objects collapsed and pages the retained window without losing payloads", async () => {
    const { api, studio } = runFixture("succeeded");
    const original = api.get.getMockImplementation()!;
    const payloads = Array.from({ length: 26 }, (_, index) => ({
      original: {
        sensor: `raw-${index}`,
        nested: { exact: [index, "source"] },
      },
      derived: {
        sensor: `derived-${index}`,
        nested: { exact: [index * 2, "result"] },
      },
      lineage: { source: `lineage-${index}` },
    }));
    api.get.mockImplementation(async (path, signal) =>
      path.includes("/records?")
        ? { records: payloads }
        : original(path, signal),
    );
    mount(studio);
    fireEvent.click(await findRetainedRun("succeeded"));
    fireEvent.click(screen.getByRole("tab", { name: "Records" }));
    const table = await screen.findByRole("table", {
      name: "Retained records",
    });
    await waitFor(() =>
      expect(within(table).getAllByRole("row")).toHaveLength(26),
    );
    expect(
      screen.getByRole("combobox", { name: "Retained records rows per page" }),
    ).toHaveValue("25");
    const cells = within(within(table).getAllByRole("row")[1]).getAllByRole(
      "cell",
    );
    for (const [index, expected] of [
      [1, payloads[0].original],
      [2, payloads[0].derived],
    ] as const) {
      const disclosure = cells[index].querySelector("details")!;
      expect(disclosure).not.toHaveAttribute("open");
      expect(JSON.parse(disclosure.querySelector("pre")!.textContent!)).toEqual(
        expected,
      );
      expect(cells[index].querySelectorAll(":scope > pre")).toHaveLength(0);
    }
    const fullRecord = within(cells[3])
      .getByText("Inspect record")
      .closest("details")!;
    expect(fullRecord).not.toHaveAttribute("open");
    expect(JSON.parse(fullRecord.querySelector("pre")!.textContent!)).toEqual(
      payloads[0],
    );
    fireEvent.click(fullRecord.querySelector("summary")!);
    expect(fullRecord).toHaveAttribute("open");
    expect(JSON.parse(fullRecord.querySelector("pre")!.textContent!)).toEqual(
      payloads[0],
    );
    const readsBeforePage = api.get.mock.calls.filter(([path]) =>
      path.includes("/records?"),
    ).length;
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent(/^26/);
    expect(
      JSON.parse(
        within(table)
          .getByText("Inspect record")
          .closest("details")!
          .querySelector("pre")!.textContent!,
      ),
    ).toEqual(payloads[25]);
    expect(
      api.get.mock.calls.filter(([path]) => path.includes("/records?")).length,
    ).toBe(readsBeforePage);
  });
  it("shows loading evidence after choosing a retained run and then its actual state", async () => {
    const { api, studio, run } = runFixture("succeeded");
    const original = api.get.getMockImplementation()!;
    let finish!: (value: typeof run) => void;
    api.get.mockImplementation(async (path, signal) =>
      path === "/api/lab/runs/recovery-run"
        ? new Promise((resolve) => {
            finish = resolve;
          })
        : original(path, signal),
    );
    mount(studio);
    fireEvent.click(await findRetainedRun("succeeded"));
    expect(screen.getByText("Loading", { exact: true })).toBeVisible();
    await act(async () => finish(run));
    expect(await screen.findByText("succeeded", { exact: true })).toBeVisible();
  });
  it("drains retained event pages after the run becomes terminal", async () => {
    const { api, studio } = runFixture("succeeded");
    const baseGet = api.get.getMockImplementation()!;
    api.get.mockImplementation(async (path, signal) => {
      if (path.endsWith("/events?after=0"))
        return {
          events: [{ seq: 1, kind: "first-event", payload: {} }],
          next_after: 1,
          latest_seq: 2,
          has_more: true,
        };
      if (path.endsWith("/events?after=1"))
        return {
          events: [{ seq: 2, kind: "tail-event", payload: {} }],
          next_after: 2,
          latest_seq: 2,
          has_more: false,
        };
      return baseGet(path, signal);
    });
    const view = mount(studio);
    const row = await findRetainedRun("succeeded");
    vi.useFakeTimers();
    try {
      fireEvent.click(row);
      await act(async () => {});
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.getByText("tail-event")).toBeInTheDocument();
      expect(
        api.get.mock.calls
          .filter(([path]) => path.includes("/events?"))
          .map(([path]) => path),
      ).toEqual([
        "/api/lab/runs/recovery-run/events?after=0",
        "/api/lab/runs/recovery-run/events?after=1",
      ]);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30000);
      });
      expect(
        api.get.mock.calls.filter(([path]) => path.includes("/events?")),
      ).toHaveLength(2);
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });
  it.each([undefined, 503, 429])(
    "reconnects after transient polling status %s without dropping the event cursor or command error",
    async (status) => {
      const { api, studio } = runFixture("running");
      let reads = 0;
      const baseGet = api.get.getMockImplementation()!;
      api.get.mockImplementation(async (path, signal) => {
        if (path === "/api/lab/runs/recovery-run" && ++reads === 2)
          throw status === undefined
            ? new TypeError("offline")
            : Object.assign(new Error("offline"), { status });
        if (path.includes("/events?")) {
          const after = Number(path.split("after=")[1]);
          return {
            events:
              after < 2
                ? [{ seq: after + 1, kind: `event-${after + 1}`, payload: {} }]
                : [],
            next_after: Math.min(after + 1, 2),
            has_more: false,
          };
        }
        return baseGet(path, signal);
      });
      api.post.mockRejectedValueOnce(new Error("Command refused"));
      const view = mount(studio);
      const row = await findRetainedRun("running");
      vi.useFakeTimers();
      try {
        fireEvent.click(row);
        await act(async () => {});
        fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
        await act(async () => {});
        expect(screen.getByText("Command refused")).toBeInTheDocument();
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1200);
        });
        expect(
          screen.getByText(/Evidence connection interrupted/),
        ).toBeInTheDocument();
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1200);
        });
        expect(
          screen.queryByText(/Evidence connection interrupted/),
        ).not.toBeInTheDocument();
        expect(screen.getByText("Command refused")).toBeInTheDocument();
        expect(screen.getByText("event-2")).toBeInTheDocument();
        expect(
          api.get.mock.calls
            .filter(([path]) => path.includes("/events?"))
            .map(([path]) => path),
        ).toEqual([
          "/api/lab/runs/recovery-run/events?after=0",
          "/api/lab/runs/recovery-run/events?after=1",
        ]);
        // A failed status read is retried before another event snapshot is taken.
        expect(reads).toBe(3);
      } finally {
        view.unmount();
        vi.useRealTimers();
      }
    },
  );
  it.each([401, 403, 404])(
    "does not reconnect polling denied with status %s",
    async (status) => {
      const { api, studio } = runFixture("running");
      const baseGet = api.get.getMockImplementation()!;
      api.get.mockImplementation(async (path, signal) => {
        if (path === "/api/lab/runs/recovery-run")
          throw Object.assign(new Error("Denied evidence read"), { status });
        return baseGet(path, signal);
      });
      const view = mount(studio);
      const row = await findRetainedRun("running");
      vi.useFakeTimers();
      try {
        fireEvent.click(row);
        await act(async () => {});
        expect(screen.getByText("Denied evidence read")).toBeInTheDocument();
        await act(async () => {
          await vi.advanceTimersByTimeAsync(60000);
        });
        expect(
          api.get.mock.calls.filter(
            ([path]) => path === "/api/lab/runs/recovery-run",
          ),
        ).toHaveLength(1);
      } finally {
        view.unmount();
        vi.useRealTimers();
      }
    },
  );
  it("bounds reconnect backoff and leaves manual refresh available after repeated outages", async () => {
    const { api, studio } = runFixture("running");
    const baseGet = api.get.getMockImplementation()!;
    api.get.mockImplementation(async (path, signal) => {
      if (path === "/api/lab/runs/recovery-run") throw new TypeError("offline");
      return baseGet(path, signal);
    });
    const view = mount(studio);
    const row = await findRetainedRun("running");
    vi.useFakeTimers();
    try {
      fireEvent.click(row);
      await act(async () => {});
      await act(async () => {
        await vi.advanceTimersByTimeAsync(33000);
      });
      expect(
        screen.getByText(/Automatic reconnect stopped/),
      ).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60000);
      });
      expect(
        api.get.mock.calls.filter(
          ([path]) => path === "/api/lab/runs/recovery-run",
        ),
      ).toHaveLength(6);
      expect(
        screen.getByRole("button", { name: "Refresh evidence" }),
      ).toBeEnabled();
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });
  it("requires a valid portable schema instead of accepting arbitrary JSON", () => {
    expect(() => parseDefinition('{"name":"broken"}')).toThrow(
      "schema_version",
    );
    expect(parseDefinition(JSON.stringify(makeDefinition())).seed).toBe(42);
  });
  it("checks the draft without saving or submitting and registers the result as evidence", async () => {
    const { api, studio } = fixture();
    api.post.mockResolvedValueOnce({
      compatible: false,
      reason: "Target unavailable",
    });
    mount(studio);
    fireEvent.click(
      screen.getByRole("button", { name: "Check compatibility" }),
    );
    await screen.findByText(/Target unavailable/);
    expect(api.post).toHaveBeenCalledExactlyOnceWith(
      "/api/lab/preflight",
      {
        definition: makeDefinition(),
        target_id: "local",
      },
      expect.any(AbortSignal),
    );
    expect(
      screen.getByRole("button", { name: "Run saved revision" }),
    ).toBeDisabled();
    expect(studio.evidence.snapshot()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Laboratory compatibility check",
          project: "alpha",
          data: { compatible: false, reason: "Target unavailable" },
        }),
      ]),
    );
  });
  it("discards a compatibility result for a draft edited while the check is pending", async () => {
    const { api, studio } = fixture();
    let resolve!: (value: any) => void;
    api.post.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    mount(studio);
    fireEvent.click(
      screen.getByRole("button", { name: "Check compatibility" }),
    );
    fireEvent.change(screen.getByLabelText("Experiment name"), {
      target: { value: "Changed draft" },
    });
    await act(async () => {
      resolve({ reason: "Stale compatibility result" });
    });
    expect(
      screen.queryByText(/Stale compatibility result/),
    ).not.toBeInTheDocument();
    expect(
      studio.evidence
        .snapshot()
        .some((row: any) => row.label === "Laboratory compatibility check"),
    ).toBe(false);
  });
  it("saves an authored definition before submitting its retained revision, then cancels the run", async () => {
    const { api, studio } = fixture();
    mount(studio);
    fireEvent.change(screen.getByLabelText("Experiment name"), {
      target: { value: "Control response" },
    });
    expect(
      screen.getByRole("button", { name: "Run saved revision" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/api/lab/definitions",
        expect.objectContaining({
          name: "Control response",
          definition: expect.objectContaining({ seed: 42 }),
        }),
        expect.any(AbortSignal),
      ),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Run saved revision" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Run saved revision" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/api/lab/runs",
        expect.objectContaining({
          definition_id: "definition-1",
          revision: 1,
          target_id: "local",
          idempotency_key: expect.any(String),
        }),
        expect.any(AbortSignal),
      ),
    );
    expect(
      api.post.mock.calls.find(([path]) => path === "/api/lab/runs")?.[1],
    ).not.toHaveProperty("not_before");
    await screen.findByText("run-1");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Cancel run" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/api/lab/runs/run-1/cancel",
        {},
        expect.any(AbortSignal),
      ),
    );
  });
  it("submits an optional local start time as aware ISO and inspects the frozen schedule", async () => {
    const previousTimezone = process.env.TZ;
    process.env.TZ = "UTC";
    try {
      const { api, studio } = fixture();
      const baseGet = api.get.getMockImplementation()!;
      api.get.mockImplementation(async (path, signal) =>
        path === "/api/lab/runs/run-1"
          ? {
              id: "run-1",
              state: "queued",
              spec: { schedule: { not_before: "2031-01-02T03:04:00.000Z" } },
            }
          : baseGet(path, signal),
      );
      mount(studio);
      expect(screen.getByLabelText("Start after")).toHaveValue("");
      fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Run saved revision" }),
        ).toBeEnabled(),
      );
      fireEvent.change(screen.getByLabelText("Start after"), {
        target: { value: "2031-01-02T03:04" },
      });
      fireEvent.click(
        screen.getByRole("button", { name: "Run saved revision" }),
      );
      await waitFor(() =>
        expect(api.post).toHaveBeenCalledWith(
          "/api/lab/runs",
          expect.objectContaining({ not_before: "2031-01-02T03:04:00.000Z" }),
          expect.any(AbortSignal),
        ),
      );
      expect(
        await screen.findByText("2031-01-02T03:04:00.000Z"),
      ).toBeInTheDocument();
      const saved = api.post.mock.calls.find(
        ([path]) => path === "/api/lab/definitions",
      )?.[1] as any;
      expect(saved.definition).not.toHaveProperty("schedule");
      expect(saved.definition).not.toHaveProperty("not_before");
    } finally {
      if (previousTimezone === undefined) delete process.env.TZ;
      else process.env.TZ = previousTimezone;
    }
  });
  it("preserves a draft on revision conflict and fetches the latest revision explicitly", async () => {
    const { api, studio, row } = fixture();
    api.get.mockImplementation(async (path: string) =>
      path === "/api/lab/definitions"
        ? [row]
        : path === "/api/lab/targets"
          ? [{ id: "local" }]
          : path === "/api/lab/catalog"
            ? { adapters: [] }
            : [],
    );
    api.put.mockRejectedValueOnce(
      Object.assign(new Error("Revision conflict"), { status: 409 }),
    );
    mount(studio);
    fireEvent.click(
      await screen.findByRole("button", { name: /Saved experiment/ }),
    );
    fireEvent.click(
      await screen.findByRole("option", { name: "Synthetic signal · r1" }),
    );
    fireEvent.change(screen.getByLabelText("Experiment name"), {
      target: { value: "My preserved draft" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save revision" }));
    await screen.findByText("Revision conflict");
    expect(screen.getByLabelText("Experiment name")).toHaveValue(
      "My preserved draft",
    );
    expect(api.put).toHaveBeenCalledWith(
      "/api/lab/definitions/definition-1",
      expect.objectContaining({ expected_revision: 1 }),
      expect.any(AbortSignal),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Load latest saved revision" }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Experiment name")).toHaveValue(
        "Synthetic signal",
      ),
    );
  });
  it("aborts a pending save and ignores its response after switching projects", async () => {
    const { api, studio, row } = fixture();
    let resolve!: (v: any) => void;
    api.post.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const view = mount(studio);
    fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
    await waitFor(() => expect(api.post).toHaveBeenCalled());
    const signal = api.post.mock.calls[0][2] as AbortSignal;
    view.rerender(
      <StudioContext.Provider
        value={{ ...studio, scopeKey: "user:beta", project: "beta" }}
      >
        <LabPage />
      </StudioContext.Provider>,
    );
    expect(signal.aborted).toBe(true);
    await act(async () => {
      resolve(row);
    });
    expect(
      screen.getByRole("button", { name: "Save experiment" }),
    ).toBeEnabled();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
    expect(studio.toast).not.toHaveBeenCalled();
  });
  it("renders retained event payloads and aborts run reads on unmount", async () => {
    const { api, studio } = fixture();
    api.get.mockImplementation(async (path: string): Promise<any> => {
      if (path === "/api/lab/catalog") return { adapters: [] };
      if (path === "/api/lab/definitions" || path === "/api/lab/inputs")
        return [];
      if (path === "/api/lab/targets") return [{ id: "local", name: "Local" }];
      if (path === "/api/lab/runs")
        return [{ id: "observed-run", state: "running" }];
      if (path.includes("/events?"))
        return {
          events: [
            {
              seq: 7,
              time: "retained-time",
              kind: "stage_finished",
              payload: { stage: "normalize", count: 12 },
            },
          ],
          next_after: 7,
        };
      return {
        id: "observed-run",
        state: "running",
        spec_digest: "sha256:retained-spec",
      };
    });
    const view = mount(studio);
    fireEvent.click(await findRunByName("observed-run · running"));
    await screen.findByText("stage_finished");
    expect(screen.getByText("sha256:retained-spec")).toBeInTheDocument();
    expect(screen.getByText(/"normalize"/)).toBeInTheDocument();
    const reads = api.get.mock.calls.filter(([path]) =>
      path.startsWith("/api/lab/runs/observed-run"),
    );
    view.unmount();
    expect(reads.length).toBe(2);
    expect(reads.every(([, signal]) => signal?.aborted)).toBe(true);
  });
  it("exports a completed run through the scoped client and registers run lineage", async () => {
    const { api, studio } = fixture();
    api.post.mockResolvedValueOnce({
      id: "export-1",
      kind: "export",
      state: "queued",
      result: null,
    });
    const baseGet = api.get.getMockImplementation()!;
    api.get.mockImplementation(async (path, signal) => {
      if (path === "/api/lab/operations/export-1")
        return {
          id: "export-1",
          kind: "export",
          state: "succeeded",
          result: { artifact: { id: "bundle-1" } },
        };
      if (path === "/api/lab/runs")
        return [{ id: "finished-run", state: "succeeded" }];
      if (path === "/api/lab/runs/finished-run")
        return {
          id: "finished-run",
          state: "succeeded",
          spec_digest: "retained-digest",
        };
      return baseGet(path, signal);
    });
    const create = vi.fn(() => "blob:bundle");
    const revoke = vi.fn();
    const originalURL = URL;
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = create;
        static revokeObjectURL = revoke;
      },
    );
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    try {
      mount(studio);
      fireEvent.click(await findRunByName("finished-run · succeeded"));
      const button = await screen.findByRole("button", {
        name: "Export manifest bundle",
      });
      await waitFor(() => expect(button).toBeEnabled());
      fireEvent.click(button);
      expect(await screen.findByText("Export: queued")).toBeInTheDocument();
      expect(api.download).not.toHaveBeenCalled();
      expect(button).toBeDisabled();
      await waitFor(
        () =>
          expect(api.download).toHaveBeenCalledWith(
            "/api/lab/operations/export-1/download",
            expect.any(AbortSignal),
          ),
        { timeout: 2500 },
      );
      expect(screen.getByText("Export: succeeded")).toBeInTheDocument();
      expect(api.post).toHaveBeenCalledWith(
        "/api/lab/runs/finished-run/exports",
        { idempotency_key: expect.any(String) },
        expect.any(AbortSignal),
      );
      await waitFor(() => expect(click).toHaveBeenCalledOnce());
      expect(revoke).toHaveBeenCalledWith("blob:bundle");
      expect(studio.evidence.snapshot()).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            label: "Laboratory run and event lineage",
            project: "alpha",
          }),
        ]),
      );
    } finally {
      click.mockRestore();
      vi.stubGlobal("URL", originalURL);
    }
  });
  it("aborts export polling when the account changes and exposes no old operation in the new workspace", async () => {
    const { api, studio } = fixture();
    const baseGet = api.get.getMockImplementation()!;
    api.get.mockImplementation(async (path, signal) => {
      if (path === "/api/lab/runs")
        return [{ id: "finished-run", state: "succeeded" }];
      if (path === "/api/lab/runs/finished-run")
        return { id: "finished-run", state: "succeeded" };
      return baseGet(path, signal);
    });
    api.post.mockResolvedValueOnce({
      id: "export-1",
      kind: "export",
      state: "queued",
    });
    const view = mount(studio);
    fireEvent.click(await findRunByName("finished-run · succeeded"));
    const button = await screen.findByRole("button", {
      name: "Export manifest bundle",
    });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await screen.findByText("Export: queued");
    const signal = api.post.mock.calls[0][2] as AbortSignal;
    view.rerender(
      <StudioContext.Provider
        value={{ ...studio, scopeKey: "other:alpha", user: { id: "other" } }}
      >
        <LabPage />
      </StudioContext.Provider>,
    );
    expect(signal.aborted).toBe(true);
    expect(screen.queryByText("Export: queued")).not.toBeInTheDocument();
    expect(api.download).not.toHaveBeenCalled();
    await act(async () => {});
    expect(
      api.get.mock.calls.some(([path]) =>
        path.startsWith("/api/lab/operations/"),
      ),
    ).toBe(false);
  });
  it("imports an input and binds the checksum returned by the server", async () => {
    const { api, studio } = fixture();
    mount(studio);
    fireEvent.click(screen.getByRole("tab", { name: "Inputs" }));
    const file = new File(['{"time":0,"value":0}'], "recording.jsonl", {
      type: "application/x-ndjson",
    });
    fireEvent.change(
      screen.getByLabelText("Import laboratory input", { selector: "input" }),
      {
        target: { files: [file] },
      },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Import and attach input" }),
    );
    await waitFor(() =>
      expect(api.upload).toHaveBeenCalledWith(
        "/api/lab/inputs",
        expect.any(FormData),
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      ),
    );
    await screen.findByText("Bound input checksums (1)");
    fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/api/lab/definitions",
        expect.objectContaining({
          definition: expect.objectContaining({
            inputs: [
              {
                artifact_id: "input-1",
                sha256: "checksum-from-server",
                role: "source",
                visibility: "model",
              },
            ],
          }),
        }),
        expect.any(AbortSignal),
      ),
    );
  });
  it("preserves feedback topology and missing signal values instead of inventing DAG order or zero", () => {
    const definition = makeDefinition("simulation.plant");
    definition.components = [
      { id: "controller", label: "Controller" },
      { id: "plant", label: "Plant" },
    ];
    definition.connections = [
      { id: "forward", source: "controller", target: "plant", kind: "control" },
      { id: "feedback", source: "plant", target: "controller", kind: "signal" },
    ];
    const graph = toGraph(definition);
    expect(graph.edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["controller", "plant"],
      ["plant", "controller"],
    ]);
    expect(
      timelineSeries(
        [
          { time: 0, value: 0 },
          { time: 1, value: null },
          { time: 2, value: 2 },
        ],
        "value",
      ),
    ).toEqual([
      [0, 0],
      [1, null],
      [2, 2],
    ]);
    expect(
      numericFields([{ time: 0, available_at: 0, value: 0, text: "document" }]),
    ).toEqual(["value"]);
  });
  it("writes visual component edits into the same portable definition JSON", async () => {
    const { studio } = fixture();
    mount(studio);
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("Experiment engine"));
    await user.click(
      await screen.findByRole("option", {
        name: "Composed signal / controller / plant",
      }),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Visual topology" }));
    fireEvent.change(screen.getByLabelText("Component label"), {
      target: { value: "Cooling loop" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add component" }));
    fireEvent.click(screen.getByRole("tab", { name: "Definition JSON" }));
    const source = screen.getByLabelText(
      "Experiment definition JSON",
    ) as HTMLTextAreaElement;
    const definition = JSON.parse(source.value);
    expect(definition.components).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "Cooling loop",
          id: expect.any(String),
          kind: "signal",
          version: "1",
        }),
      ]),
    );
    definition.components.find(
      (item: any) => item.label === "Cooling loop",
    ).label = "Edited in JSON";
    fireEvent.change(source, { target: { value: JSON.stringify(definition) } });
    fireEvent.click(screen.getByRole("tab", { name: "Visual topology" }));
    expect(
      (await screen.findAllByText("Edited in JSON")).length,
    ).toBeGreaterThan(0);
  });
  it("opens evidence lenses with keyboard navigation and states missing prerequisites", async () => {
    const { studio } = fixture();
    render(
      <StudioContext.Provider value={studio}>
        <ReplayViews
          records={[]}
          rawRecords={[]}
          events={[]}
          run={{
            id: "empty-run",
            definition_id: "definition-1",
            state: "succeeded",
          }}
        />
      </StudioContext.Provider>,
    );
    const user = userEvent.setup();
    await user.tab();
    expect(
      screen.getByRole("button", { name: "Episode replay" }),
    ).toHaveFocus();
    await user.tab();
    expect(
      screen.getByRole("button", { name: "State / topology" }),
    ).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(
      screen.getByText(/A frozen run definition is required/),
    ).toBeInTheDocument();
    await user.tab();
    expect(
      screen.getByRole("button", { name: "Dataset coverage" }),
    ).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(
      screen.getByText(/Dataset coverage requires retained records/),
    ).toBeInTheDocument();
    await user.tab();
    await user.keyboard("{Enter}");
    expect(
      screen.getByText(
        "A retained record or representation artifact is required.",
      ),
    ).toBeInTheDocument();
    await user.tab();
    await user.keyboard("{Enter}");
    expect(
      screen.getByText(
        /Baseline comparison requires two compatible retained runs/,
      ),
    ).toBeInTheDocument();
    await user.tab();
    await user.keyboard("{Enter}");
    expect(
      screen.getByText(/Deployment tradeoffs require measured target latency/),
    ).toBeInTheDocument();
  });
});

async function choose(label: string, name: string) {
  fireEvent.click(screen.getByRole("tab", { name: "Experiment" }));
  const section = label.startsWith("Execution")
    ? "Execution"
    : ["Connection source", "Connection target", "Target input port"].includes(
          label,
        )
      ? "Visual topology"
      : label.startsWith("Input") || label.startsWith("Role for")
        ? "Inputs"
        : "Parameters";
  fireEvent.click(screen.getByRole("tab", { name: section }));
  const user = userEvent.setup();
  await user.click(screen.getByLabelText(label));
  await user.click(await screen.findByRole("option", { name }));
}
function readJSON() {
  fireEvent.click(screen.getByRole("tab", { name: "Definition JSON" }));
  return JSON.parse(
    (screen.getByLabelText("Experiment definition JSON") as HTMLTextAreaElement)
      .value,
  );
}
describe("laboratory capability and evidence contracts", () => {
  it("retains unknown and unavailable engine IDs rather than substituting a signal", async () => {
    expect(makeDefinition("vendor.unavailable").engine).toBe(
      "vendor.unavailable",
    );
    const { api, studio } = fixture();
    const original = api.get.getMockImplementation()!;
    api.get.mockImplementation(async (path: string, signal?: AbortSignal) =>
      path === "/api/lab/catalog"
        ? {
            adapters: [
              {
                id: "vendor.unavailable",
                name: "Unavailable vendor engine",
                implemented: true,
                available: false,
                reason: "Requires remote Linux runtime",
              },
            ],
            methods: [
              {
                id: "method.extra",
                name: "Extra method",
                implemented: false,
                available: false,
              },
            ],
          }
        : original(path, signal),
    );
    mount(studio);
    await choose("Experiment engine", "Unavailable vendor engine");
    expect(
      screen.getAllByText("Requires remote Linux runtime").length,
    ).toBeGreaterThan(0);
    expect(readJSON().engine).toBe("vendor.unavailable");
    await choose("Experiment engine", "Extra method");
    expect(readJSON().engine).toBe("method.extra");
  });
  it("authors the supported co-simulation schema with clocks and controller ports", () => {
    const definition = makeDefinition("system.cosim");
    expect(definition.clocks).toEqual({
      step: 0.1,
      steps: 100,
      hold: "zero_order",
      tolerance: 1e-9,
    });
    expect(definition.components.map((c) => c.kind)).toEqual([
      "signal",
      "plant",
      "sensor",
      "transport",
      "controller",
    ]);
    expect(definition.components.every((c) => c.version === "1")).toBe(true);
    expect(definition.connections).toEqual(
      expect.arrayContaining([
        {
          source: "reference",
          target: "controller",
          port: "reference",
          version: "1",
        },
        {
          source: "transport",
          target: "controller",
          port: "measurement",
          version: "1",
        },
      ]),
    );
    expect(parseDefinition(JSON.stringify(definition)).engine).toBe(
      "system.cosim",
    );
  });
  it("reuses an uploaded artifact and defaults final-test visibility outside the model", async () => {
    const { api, studio } = fixture();
    const original = api.get.getMockImplementation()!;
    api.get.mockImplementation(async (path: string, signal?: AbortSignal) =>
      path === "/api/lab/inputs"
        ? [
            {
              id: "existing",
              name: "heldout.jsonl",
              sha256: "pinned-checksum",
              size: 100,
              media_type: "application/x-ndjson",
            },
          ]
        : original(path, signal),
    );
    mount(studio);
    await choose("Input role", "final_test");
    await screen.findByRole("button", { name: "Attach heldout.jsonl" });
    fireEvent.click(
      screen.getByRole("button", { name: "Attach heldout.jsonl" }),
    );
    expect(readJSON().inputs).toEqual([
      {
        artifact_id: "existing",
        sha256: "pinned-checksum",
        role: "final_test",
        visibility: "truth",
      },
    ]);
    await choose("Role for heldout.jsonl", "source");
    expect(readJSON().inputs[0].visibility).toBe("truth");
    expect(() =>
      parseDefinition(
        JSON.stringify({
          ...makeDefinition(),
          inputs: [
            {
              artifact_id: "a",
              sha256: "checksum",
              role: "final_test",
              visibility: "model",
            },
          ],
        }),
      ),
    ).toThrow("Truth");
  });
  it("persists explicit execution profile/backend/budgets across engine selection", async () => {
    const { api, studio } = fixture();
    mount(studio);
    await choose("Execution profile", "Isolated");
    await choose("Execution backend", "container");
    fireEvent.change(screen.getByLabelText("Maximum execution seconds"), {
      target: { value: "120" },
    });
    await choose("Experiment engine", "Recorded replay");
    expect(readJSON().execution).toEqual(
      expect.objectContaining({
        profile: "isolated",
        backend: "container",
        max_seconds: 120,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/api/lab/definitions",
        expect.objectContaining({
          definition: expect.objectContaining({
            execution: expect.objectContaining({
              profile: "isolated",
              backend: "container",
              max_seconds: 120,
            }),
          }),
        }),
        expect.any(AbortSignal),
      ),
    );
  });
  it("keeps optional pretrained update strategies selectable and pins their source roles", async () => {
    const { studio } = fixture();
    mount(studio);
    await choose("Experiment engine", "Pretrained full / LoRA / QLoRA / DPO");
    await choose("Training method", "qlora");
    let definition = readJSON();
    expect(definition.engine).toBe("learning.pretrained");
    expect(definition.learning.update_strategy).toBe("qlora");
    await choose("Training method", "dpo");
    definition = readJSON();
    expect(definition.learning.objective).toBe("dpo");
    fireEvent.click(screen.getByRole("tab", { name: "Inputs" }));
    expect(
      screen.getByText(/Local model\/tokenizer archive/),
    ).toBeInTheDocument();
  });
  it("supports keyboard graph wiring and prevents a generic graph from impersonating another adapter", async () => {
    const { studio } = fixture();
    mount(studio);
    fireEvent.click(screen.getByRole("tab", { name: "Visual topology" }));
    expect(
      screen.queryByRole("button", { name: "Add component" }),
    ).not.toBeInTheDocument();
    await choose("Experiment engine", "Composed signal / controller / plant");
    fireEvent.click(screen.getByRole("tab", { name: "Visual topology" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Remove connection 1" }),
    );
    await choose("Connection source", "reference");
    await choose("Connection target", "controller");
    await choose("Target input port", "reference");
    const wire = screen.getByRole("button", { name: "Wire selected ports" });
    expect(wire).toBeEnabled();
    wire.focus();
    await userEvent.setup().keyboard("{Enter}");
    const definition = readJSON();
    expect(definition.connections).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: "reference",
          target: "controller",
          port: "reference",
          version: "1",
        }),
      ]),
    );
    expect(
      definition.connections.filter(
        (c: any) => c.target === "controller" && c.port === "reference",
      ),
    ).toHaveLength(1);
  });
});
