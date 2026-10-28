import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { DatasetPage } from "./datasets";
import { track, useAction } from "./common";
afterEach(cleanup);
const minimumError =
  "A snapshot needs at least 10 training datapoints (currently 1)";
// Independently specified API views: services/snapshots.snapshot_dict and
// services/tasks.task_dict differ in meaning despite both having a persistent id.
const inlineSnapshot = {
  id: "snapshot-uuid",
  name: "fixture-v1",
  dataset: "fixture",
  training_count: 10,
  validation_count: 1,
  created: "2026-10-27T00:00:00Z",
};
const queuedTask = {
  id: "task-uuid",
  kind: "dataset.synthetic",
  state: "queued",
  args: { name: "new-data" },
  created: "2026-10-27T00:00:00Z",
  started: null,
  finished: null,
  error: null,
  result: null,
  project_id: "project-fixture",
  log_tail: [],
  worker: null,
};
function runtime(api: any) {
  return {
    api,
    project: "fixture",
    scopeKey: "u:fixture",
    user: { id: "u" },
    route: "/datasets/fixture",
    evidence: new EvidenceRegistry(),
    overview: { tree: { datasets: [] } },
    toast: vi.fn(),
    confirm: vi.fn(async () => true),
    navigate: vi.fn(),
    trackTask: vi.fn(),
    showTasks: vi.fn(),
    refreshOverview: vi.fn(async () => {}),
  } as any;
}
function mountDataset(post: any, training = 1) {
  const api = {
    get: vi.fn(async (path: string) =>
      path === "/api/snapshots"
        ? []
        : {
            meta: { buckets: { uncategorized: 0, training, validation: 1 } },
            total: training + 1,
            rows: [],
          },
    ),
    post,
  };
  const s = runtime(api);
  render(
    <StudioContext.Provider value={s}>
      <MemoryRouter initialEntries={["/datasets/fixture"]}>
        <Routes>
          <Route path="/datasets/:name" element={<DatasetPage />} />
        </Routes>
      </MemoryRouter>
    </StudioContext.Provider>,
  );
  return { api, s };
}
async function failSnapshot(post: any) {
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Snapshots",
      exact: true,
    } as any),
  );
  fireEvent.click(screen.getByRole("button", { name: "Create snapshot" }));
  await waitFor(() => expect(post).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(
      within(screen.getByRole("dialog")).getByRole("alert").textContent,
    ).toBe(minimumError),
  );
}
describe("operation-owned errors", () => {
  it("preserves the snapshot validation failure but does not inherit it into Add Datapoint", async () => {
    const post = vi.fn(async () => {
      throw Error(minimumError);
    });
    const { s } = mountDataset(post);
    await failSnapshot(post);
    fireEvent.change(
      screen.getByRole("textbox", { name: "New snapshot name" }),
      { target: { value: "still-too-small" } },
    );
    expect(
      within(screen.getByRole("dialog")).getByRole("alert").textContent,
    ).toBe(minimumError);
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    fireEvent.click(screen.getByRole("button", { name: "Add datapoint" }));
    expect(screen.getByRole("dialog").textContent).not.toContain(minimumError);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(post).toHaveBeenCalledOnce();
    expect(s.toast).toHaveBeenCalledWith(minimumError, true);
  });
  it("clears the prior error for retry while preserving busy and any new failure", async () => {
    let rejectRetry!: (error: Error) => void;
    const retry = new Promise((_, reject) => {
      rejectRetry = reject;
    });
    const post = vi
      .fn()
      .mockRejectedValueOnce(Error(minimumError))
      .mockImplementationOnce(() => retry);
    mountDataset(post);
    await failSnapshot(post);
    fireEvent.click(screen.getByRole("button", { name: "Create snapshot" }));
    expect(within(screen.getByRole("dialog")).queryByRole("alert")).toBeNull();
    expect(
      (screen.getByRole("button", { name: "Working…" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    await act(async () =>
      rejectRetry(Error("Storage temporarily unavailable")),
    );
    await waitFor(() =>
      expect(
        within(screen.getByRole("dialog")).getByRole("alert").textContent,
      ).toBe("Storage temporarily unavailable"),
    );
    expect(
      (
        screen.getByRole("button", {
          name: "Create snapshot",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
  });
  it("changing error ownership does not reset busy or publish an old request error into the new operation", async () => {
    let reject!: (error: Error) => void;
    const pending = new Promise((_, r) => {
      reject = r;
    });
    const s = runtime({});
    function Harness() {
      const a = useAction();
      return (
        <>
          <button onClick={() => a.run(() => pending)}>
            Start old operation
          </button>
          <button onClick={() => a.clearError()}>Open new operation</button>
          <p role="status">{a.busy ? "busy" : "idle"}</p>
          {a.error && <p role="alert">{a.error}</p>}
        </>
      );
    }
    render(
      <StudioContext.Provider value={s}>
        <Harness />
      </StudioContext.Provider>,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Start old operation" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Open new operation" }));
    expect(screen.getByRole("status").textContent).toBe("busy");
    await act(async () => reject(Error("Old operation failed")));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("idle");
    expect(s.toast).toHaveBeenCalledWith("Old operation failed", true);
  });
  it("does not report a failed request after its owning scope unmounts", async () => {
    let reject!: (error: Error) => void;
    const pending = new Promise((_, r) => {
      reject = r;
    });
    const s = runtime({});
    function Harness() {
      const a = useAction();
      return <button onClick={() => a.run(() => pending)}>Start</button>;
    }
    const view = render(
      <StudioContext.Provider value={s}>
        <Harness />
      </StudioContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    view.unmount();
    await act(async () => reject(Error("Old scope failed")));
    expect(s.toast).not.toHaveBeenCalled();
  });
});
describe("backend task view discrimination", () => {
  it.each([
    inlineSnapshot,
    { queued: true, task: inlineSnapshot },
    { ...queuedTask, state: "unknown" },
    { ...queuedTask, id: "" },
    { id: "dataset-id", name: "fixture", datapoints: 11 },
    { id: "job-id", name: "job", snapshot: "fixture-v1" },
    {
      id: "evaluation-id",
      state: "succeeded",
      models: ["job/1"],
      metrics: ["json"],
    },
    { id: "metric-id", kind: "rubric", name: "quality" },
  ])("does not open a task drawer for an inline result %#", (result) => {
    const s = runtime({});
    expect(track(s, result)).toBe(false);
    expect(s.trackTask).not.toHaveBeenCalled();
    expect(s.showTasks).not.toHaveBeenCalled();
  });
  it.each([queuedTask, { queued: true, task: queuedTask }])(
    "tracks actual plain or explicitly queued TaskView %#",
    (result) => {
      const s = runtime({});
      expect(track(s, result)).toBe(true);
      expect(s.trackTask).toHaveBeenCalledWith(queuedTask);
      expect(s.showTasks).toHaveBeenCalledOnce();
    },
  );
  it("does not show a phantom task drawer after actual inline snapshot creation", async () => {
    const { s } = mountDataset(
      vi.fn(async () => inlineSnapshot),
      10,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Snapshots",
        exact: true,
      } as any),
    );
    fireEvent.click(screen.getByRole("button", { name: "Create snapshot" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(s.refreshOverview).toHaveBeenCalled();
    expect(s.trackTask).not.toHaveBeenCalled();
    expect(s.showTasks).not.toHaveBeenCalled();
  });
  it("shows actual queued snapshot work from the explicit background response", async () => {
    const task = { ...queuedTask, kind: "snapshot.create" };
    const { s } = mountDataset(
      vi.fn(async () => ({ queued: true, task })),
      10,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Snapshots",
        exact: true,
      } as any),
    );
    fireEvent.click(screen.getByRole("button", { name: "Create snapshot" }));
    await waitFor(() => expect(s.trackTask).toHaveBeenCalledWith(task));
    expect(s.showTasks).toHaveBeenCalledOnce();
  });
});
