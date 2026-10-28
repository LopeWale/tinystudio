import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { LabPage } from "./index";
import { makeDefinition } from "./definition";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function fixture() {
  const api = {
    get: vi.fn(async (path: string, _signal?: AbortSignal): Promise<any> => {
      if (path === "/api/lab/catalog")
        return { adapters: [], domains: [], methods: [] };
      if (path === "/api/lab/targets")
        return [{ id: "local", name: "Local computer" }];
      if (path === "/api/repositories") return [{ name: "engineering" }];
      return [];
    }),
    post: vi.fn(
      async (
        _path: string,
        _body?: unknown,
        _signal?: AbortSignal,
      ): Promise<any> => undefined,
    ),
    put: vi.fn(),
    upload: vi.fn(),
    download: vi.fn(),
  };
  const studio = {
    api,
    scopeKey: "user:alpha",
    project: "alpha",
    user: { id: "user" },
    route: "/lab",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as unknown as StudioRuntime;
  return { api, studio };
}
function mount(studio: StudioRuntime) {
  return render(
    <StudioContext.Provider value={studio}>
      <LabPage />
    </StudioContext.Provider>,
  );
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("abandons an old repository observation when New experiment resets an unsaved draft of the same engine", async () => {
  const { api, studio } = fixture();
  const completion = deferred<any>();
  const baseGet = api.get.getMockImplementation()!;
  api.get.mockImplementation((path, signal) =>
    path === "/api/lab/operations/old-capture"
      ? completion.promise
      : baseGet(path, signal),
  );
  api.post.mockResolvedValue({
    id: "old-capture",
    kind: "data_bridge",
    state: "queued",
  });
  mount(studio);
  const user = userEvent.setup();
  fireEvent.click(screen.getByRole("tab", { name: "Inputs" }));
  await user.click(
    screen.getByText("Capture an existing repository", {
      selector: "summary span",
    }),
  );
  await user.click(
    screen.getByRole("button", { name: /Repository to capture$/ }),
  );
  await user.click(await screen.findByRole("option", { name: "engineering" }));
  const original = JSON.parse(
    (screen.getByLabelText("Experiment definition JSON") as HTMLTextAreaElement)
      .value,
  );
  expect(original.engine).toBe("synthetic.signal");
  expect(original.inputs).toEqual([]);
  // Control the standard helper's polling interval; keep its GET pending until
  // after the explicit same-engine reset, regardless of machine scheduling.
  vi.useFakeTimers();
  fireEvent.click(
    screen.getByRole("button", { name: "Capture and attach repository" }),
  );
  await act(async () => {});
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1200);
  });
  const observation = api.get.mock.calls.find(
    ([path]) => path === "/api/lab/operations/old-capture",
  );
  expect(observation).toBeDefined();
  const observationSignal = observation![1] as AbortSignal;
  expect(observationSignal.aborted).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "New experiment" }));
  expect(observationSignal.aborted).toBe(true);
  await act(async () =>
    completion.resolve({
      id: "old-capture",
      kind: "data_bridge",
      state: "succeeded",
      result: {
        inputs: [
          {
            artifact_id: "old-corpus",
            sha256: "a".repeat(64),
            role: "corpus",
            visibility: "model",
          },
        ],
      },
    }),
  );
  const reset = JSON.parse(
    (screen.getByLabelText("Experiment definition JSON") as HTMLTextAreaElement)
      .value,
  );
  expect(reset.engine).toBe(original.engine);
  expect(reset.inputs).toEqual([]);
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post.mock.calls[0][0]).toBe("/api/lab/inputs/from-repository");
  expect(api.upload).not.toHaveBeenCalled();
});

it("retains the imported-definition recovery action while operation inventory refresh is unresolved", async () => {
  const { api, studio } = fixture();
  const refreshedInventory = deferred<any[]>();
  const inventory = [
    { id: "completed-export", kind: "export", state: "succeeded" },
    { id: "completed-import", kind: "import", state: "succeeded" },
  ];
  const imported = {
    id: "recovered-definition",
    name: "Recovered retained import",
    revision: 1,
    definition: { ...makeDefinition(), name: "Recovered retained import" },
  };
  let inventoryReads = 0;
  const baseGet = api.get.getMockImplementation()!;
  api.get.mockImplementation(async (path, signal) => {
    if (path === "/api/lab/operations")
      return ++inventoryReads === 1 ? inventory : refreshedInventory.promise;
    if (path === "/api/lab/operations/completed-export")
      return { ...inventory[0], result: { artifact: { id: "bundle" } } };
    if (path === "/api/lab/operations/completed-import")
      return { ...inventory[1], result: { definition: imported } };
    return baseGet(path, signal);
  });
  mount(studio);
  const user = userEvent.setup();
  fireEvent.click(screen.getByRole("button", { name: "Import / export" }));
  fireEvent.click(screen.getByRole("tab", { name: "History" }));
  await user.click(
    await screen.findByRole("button", { name: "Inspect completed-export" }),
  );
  // The completed observation triggers inventory reload immediately. Its
  // response remains deliberately unresolved while the user recovers import.
  await act(async () => {});
  expect(inventoryReads).toBe(2);
  expect(
    screen.getByText("Operation completed-export: succeeded"),
  ).toBeInTheDocument();
  const recovery = screen.getByRole("button", {
    name: "Load imported definition completed-import",
  });
  expect(recovery).toBeEnabled();
  await user.click(recovery);
  expect(screen.getByLabelText("Experiment name")).toHaveValue(
    "Recovered retained import",
  );
  expect(api.post).not.toHaveBeenCalled();
  expect(api.upload).not.toHaveBeenCalled();
  await act(async () => refreshedInventory.resolve(inventory));
});
