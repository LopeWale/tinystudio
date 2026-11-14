import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { LabPage } from "./index";
import { makeDefinition, type LabRun } from "./definition";

vi.mock("./trend-workbench", () => ({
  TrendWorkbench: ({ controller }: any) => (
    <output data-testid="retained-trend">
      {controller.samples.map((sample: any) => sample.record.value).join(",")}
    </output>
  ),
}));
vi.mock("./graph", () => ({
  DefinitionGraph: ({ readOnly }: any) =>
    readOnly ? <p>Frozen retained topology</p> : null,
}));
vi.mock("./coverage-inspector", () => ({
  CoverageInspector: ({ runId }: any) => (
    <p>Independent coverage for {runId}</p>
  ),
}));
vi.mock("./baseline-comparison", () => ({
  RetainedBaselineComparison: ({ comparison }: any) => (
    <p>{comparison?.retained_label}</p>
  ),
}));
afterEach(cleanup);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}
const observation = {
  time: 1,
  available_at: 1,
  value: 42,
  episode_id: "retained",
};
function mount(
  read: (path: string, signal?: AbortSignal) => Promise<any>,
  state = "succeeded",
  artifacts?: { id: string; name: string; role?: string }[],
  engine = "synthetic.signal",
) {
  const runs: LabRun[] = ["first-run", "second-run"].map(
    (id) =>
      ({
        id,
        definition_id: "definition",
        state,
        spec: { definition: makeDefinition(engine) },
        result: {
          artifacts,
          baseline_comparison: { retained_label: "Retained measured baseline" },
          deployment_tradeoffs: { latency_ms: 17 },
        },
      }) as LabRun,
  );
  const api = {
    get: vi.fn(async (path: string, signal?: AbortSignal): Promise<any> => {
      if (path.includes("/records?")) return read(path, signal);
      if (path === "/api/lab/runs") return runs;
      if (path.endsWith("/recovered-evidence"))
        return {
          classification: "operator_recovered_evidence",
          references: [],
        };
      if (path.includes("/events?")) return { events: [], next_after: 0 };
      const run = runs.find((run) => path === `/api/lab/runs/${run.id}`);
      if (run) return run;
      if (path === "/api/lab/catalog") return { adapters: [], methods: [] };
      return [];
    }),
    post: vi.fn(),
    put: vi.fn(),
  };
  const studio = {
    api,
    scopeKey: "retrieval",
    project: "retrieval",
    route: "/lab",
    user: { id: "user" },
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as unknown as StudioRuntime;
  const view = render(
    <StudioContext.Provider value={studio}>
      <LabPage />
    </StudioContext.Provider>,
  );
  return { api, view };
}
async function selectRun(
  id = "first-run",
  state = "succeeded",
  name = "Synthetic signal",
) {
  const selector = await screen.findByRole("button", { name: /Retained run/ });
  await waitFor(() => expect(selector).toBeEnabled());
  fireEvent.click(selector);
  fireEvent.click(
    await screen.findByRole("option", {
      name: `${name} · ${id} · ${state}`,
    }),
  );
}
async function evidenceView(name: string) {
  fireEvent.click(screen.getByRole("button", { name: /Evidence view/ }));
  fireEvent.click(await screen.findByRole("option", { name }));
}
function noReplayData() {
  expect(screen.queryByTestId("retained-trend")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Play replay" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(/Loaded window:/)).not.toBeInTheDocument();
}

it.each([
  [
    "runtime.qualify",
    "Runtime probe",
    "Open runtime probe report",
    "runtime_probe",
  ],
  [
    "statistics.analyze",
    "Analysis",
    "Open analysis report",
    "statistical_analysis",
  ],
])(
  "explains report and artifact evidence in Records for %s without waiting for replay rows",
  async (engine, reportTab, action, role) => {
    const read = vi.fn(async () => {
      throw new Error("Report engines must not request replay records");
    });
    const { api } = mount(
      read,
      "succeeded",
      [
        { id: "report", name: "retained-report.json", role },
        {
          id: "support",
          name: "supporting-evidence.jsonl",
          role: "supporting_evidence",
        },
      ],
      engine,
    );
    await selectRun("first-run", "succeeded", makeDefinition(engine).name);
    fireEvent.click(screen.getByRole("tab", { name: "Records" }));
    expect(
      await screen.findByRole("heading", {
        name: "Results use reports and supporting artifacts",
      }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Records are not available yet" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Record artifact$/ }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Next records" })).toBeNull();
    expect(
      within(
        screen.getByRole("table", {
          name: "Report and supporting artifact identities",
        }),
      ).getByText("supporting-evidence.jsonl"),
    ).toBeVisible();
    expect(read).not.toHaveBeenCalled();
    expect(
      api.get.mock.calls.some(([path]) => path.includes("/records?")),
    ).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: action }));
    expect(screen.getByRole("tab", { name: reportTab })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  },
);

it("waits for a running run to finish instead of claiming retained artifacts are empty", async () => {
  const { api } = mount(async () => ({ records: [] }), "running");
  await selectRun("first-run", "running");
  expect(
    await screen.findByRole("heading", { name: "Replay is not available yet" }),
  ).toBeVisible();
  noReplayData();
  fireEvent.click(screen.getByRole("tab", { name: "Records" }));
  expect(
    screen.getByRole("heading", { name: "Records are not available yet" }),
  ).toBeVisible();
  expect(api.get.mock.calls.some(([path]) => path.includes("/records?"))).toBe(
    false,
  );
});

it("distinguishes pending retrieval from successful empty replay and record windows", async () => {
  const pending = deferred<any>();
  mount(async (path) => ({
    ...(await pending.promise),
    artifact: {
      id: path.includes("artifact=raw.jsonl") ? "raw" : "processed",
      name: path.includes("artifact=raw.jsonl")
        ? "raw.jsonl"
        : "processed.jsonl",
    },
  }));
  await selectRun();
  expect(
    await screen.findByRole("heading", { name: "Loading replay records" }),
  ).toBeVisible();
  noReplayData();
  fireEvent.click(screen.getByRole("tab", { name: "Records" }));
  expect(
    screen.getByRole("heading", { name: "Loading retained records" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("table", { name: "Retained records" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/No retained records in this window/),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next records" })).toBeDisabled();
  await act(async () => pending.resolve({ records: [], next_offset: null }));
  expect(
    screen.getByRole("heading", { name: "No retained records in this window" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Replay" }));
  expect(
    screen.getByRole("heading", { name: "No replay observations" }),
  ).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  noReplayData();
});

it("keeps independent evidence navigation available after missing replay artifacts", async () => {
  mount(async () => {
    throw new Error("Retained artifact not found");
  });
  await selectRun();
  expect(
    await screen.findByRole("heading", { name: "Replay records unavailable" }),
  ).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Retained artifact not found",
  );
  noReplayData();
  await evidenceView("State / topology");
  expect(screen.getByText("Frozen retained topology")).toBeVisible();
  expect(
    screen.queryByText("No state records retained for this run."),
  ).not.toBeInTheDocument();
  await evidenceView("Dataset coverage");
  expect(screen.getByText("Independent coverage for first-run")).toBeVisible();
  await evidenceView("Baseline comparison");
  expect(screen.getByText("Retained measured baseline")).toBeVisible();
  await evidenceView("Deployment tradeoffs");
  expect(
    within(screen.getByRole("tabpanel", { name: "Replay" })).getByText(
      /"latency_ms": 17/,
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Records" }));
  expect(
    screen.getByRole("heading", { name: "Retained records unavailable" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("table", { name: "Retained records" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/loaded window of 0 records/),
  ).not.toBeInTheDocument();
});

it("clears retrieval errors when the user retries replay and the record window independently", async () => {
  let available = false;
  const { api } = mount(async () => {
    if (!available) throw new Error("Storage temporarily unavailable");
    return { records: [observation], next_offset: null };
  });
  await selectRun();
  await screen.findByRole("heading", { name: "Replay records unavailable" });
  available = true;
  fireEvent.click(screen.getByRole("button", { name: "Retry replay records" }));
  expect(
    screen.getByRole("heading", { name: "Loading replay records" }),
  ).toBeVisible();
  expect(await screen.findByTestId("retained-trend")).toHaveTextContent("42");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Records" }));
  expect(
    screen.getByRole("heading", { name: "Retained records unavailable" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Retry records" }));
  const table = await screen.findByRole("table", { name: "Retained records" });
  expect(within(table).getAllByRole("row")).toHaveLength(2);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(api.post).not.toHaveBeenCalled();
});

it("keeps a successful replay source when its counterpart fails without inventing zero records", async () => {
  mount(async (path) => {
    if (path.includes("artifact=raw.jsonl"))
      throw new Error("Original artifact missing");
    return { records: [observation] };
  });
  await selectRun();
  expect(await screen.findByTestId("retained-trend")).toHaveTextContent("42");
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Original artifact missing",
  );
  expect(screen.getByText(/Loaded window:/)).toHaveTextContent(
    "Processed records: 1 loaded · Original records: unavailable",
  );
  expect(
    screen.getByRole("button", { name: "Inspect observation" }),
  ).toBeVisible();
});

it("aborts superseded retrieval and ignores a late response from another run", async () => {
  const old = deferred<any>();
  const signals: AbortSignal[] = [];
  mount(async (path, signal) => {
    if (path.includes("/first-run/")) {
      signals.push(signal!);
      return old.promise;
    }
    return { records: [{ ...observation, value: 73 }] };
  });
  await selectRun();
  await screen.findByRole("heading", { name: "Loading replay records" });
  await selectRun("second-run");
  expect(await screen.findByTestId("retained-trend")).toHaveTextContent("73");
  expect(signals).toHaveLength(3);
  expect(signals.every((signal) => signal.aborted)).toBe(true);
  await act(async () => old.resolve({ records: [observation] }));
  expect(screen.getByTestId("retained-trend")).toHaveTextContent("73");
  expect(screen.getByTestId("retained-trend")).not.toHaveTextContent("42");
});

it("rejects malformed record responses instead of presenting them as empty", async () => {
  mount(async () => ({ detail: "No record payload" }));
  await selectRun();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "did not contain a records array",
  );
  noReplayData();
  fireEvent.click(screen.getByRole("tab", { name: "Records" }));
  expect(screen.getByRole("alert")).toHaveTextContent(
    "did not contain a records array",
  );
  expect(
    screen.queryByRole("heading", {
      name: "No retained records in this window",
    }),
  ).not.toBeInTheDocument();
});

it("recovers the prior record window after a page failure without displaying stale rows", async () => {
  mount(async (path) => {
    if (path.includes("offset=100&limit=100&")) {
      throw new Error("Record page unavailable");
    }
    return { records: [observation], next_offset: 100 };
  });
  await selectRun();
  fireEvent.click(screen.getByRole("tab", { name: "Records" }));
  await screen.findByRole("table", { name: "Retained records" });
  fireEvent.click(screen.getByRole("button", { name: "Next records" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Record page unavailable",
  );
  expect(
    screen.queryByRole("table", { name: "Retained records" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next records" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Previous records" }));
  const table = await screen.findByRole("table", { name: "Retained records" });
  expect(within(table).getAllByRole("row")[1]).toHaveTextContent(/^1/);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("identifies the production absent-artifact response without calling it an inspected empty window", async () => {
  mount(async () => ({ records: [], next_offset: null }));
  await selectRun();
  expect(
    await screen.findByRole("heading", {
      name: "No replay artifacts retained",
    }),
  ).toBeVisible();
  noReplayData();
  expect(
    screen.queryByRole("heading", { name: "No replay observations" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Retry replay records" }),
  ).not.toBeInTheDocument();
  await evidenceView("State / topology");
  expect(screen.getByText("Frozen retained topology")).toBeVisible();
  expect(
    screen.queryByText("No state records retained for this run."),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Records" }));
  expect(
    screen.getByRole("heading", { name: "No retained record artifact" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("heading", {
      name: "No retained records in this window",
    }),
  ).not.toBeInTheDocument();
});

it("uses the retained manifest to qualify an older identity-free empty response", async () => {
  mount(async () => ({ records: [], next_offset: null }), "succeeded", [
    { id: "processed", name: "processed.jsonl", role: "transformed_dataset" },
    { id: "raw", name: "raw.jsonl", role: "normalized_original" },
  ]);
  await selectRun();
  expect(
    await screen.findByRole("heading", { name: "No replay observations" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Records" }));
  expect(
    screen.getByRole("heading", { name: "No retained records in this window" }),
  ).toBeVisible();
});

it("retains a single inspected source while distinguishing its absent counterpart", async () => {
  mount(async (path) =>
    path.includes("artifact=raw.jsonl")
      ? { records: [], next_offset: null }
      : {
          records: [observation],
          next_offset: null,
          artifact: { id: "processed", name: "processed.jsonl" },
        },
  );
  await selectRun();
  expect(await screen.findByTestId("retained-trend")).toHaveTextContent("42");
  expect(
    screen.getByRole("heading", {
      name: "Some replay artifacts were not retained",
    }),
  ).toBeVisible();
  expect(screen.getByText(/Loaded window:/)).toHaveTextContent(
    "Processed records: 1 loaded · Original records: not retained",
  );
  expect(
    screen.queryByRole("button", { name: "Retry replay records" }),
  ).not.toBeInTheDocument();
});

it("keeps healthy replay observations mounted while only the failed source is retried", async () => {
  const retry = deferred<any>();
  let attempts = 0;
  const { api } = mount(async (path) => {
    if (!path.includes("artifact=raw.jsonl"))
      return {
        records: [observation],
        artifact: { id: "processed", name: "processed.jsonl" },
      };
    if (++attempts === 1) throw new Error("Original storage unavailable");
    return retry.promise;
  });
  await selectRun();
  const chart = await screen.findByTestId("retained-trend");
  await screen.findByRole("heading", { name: "Replay records unavailable" });
  fireEvent.click(screen.getByRole("button", { name: "Retry replay records" }));
  expect(
    screen.getByRole("heading", { name: "Loading replay records" }),
  ).toBeVisible();
  expect(screen.getByTestId("retained-trend")).toBe(chart);
  expect(chart).toHaveTextContent("42");
  expect(
    api.get.mock.calls.filter(([path]) =>
      path.includes("limit=1000&artifact=processed.jsonl"),
    ),
  ).toHaveLength(1);
  await act(async () =>
    retry.resolve({
      records: [{ ...observation, value: 73 }],
      artifact: { id: "raw", name: "raw.jsonl" },
    }),
  );
  expect(screen.getByTestId("retained-trend")).toBe(chart);
  expect(chart).toHaveTextContent("42");
  expect(screen.getByText(/Loaded window:/)).toHaveTextContent(
    "Processed records: 1 loaded · Original records: 1 loaded",
  );
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("rejects response artifact identity that disagrees with the selected run manifest", async () => {
  mount(
    async (path) => ({
      records: [observation],
      artifact: {
        id: "wrong-artifact",
        name: path.includes("artifact=raw.jsonl")
          ? "raw.jsonl"
          : "processed.jsonl",
      },
    }),
    "succeeded",
    [
      { id: "processed", name: "processed.jsonl" },
      { id: "raw", name: "raw.jsonl" },
    ],
  );
  await selectRun();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "different retained artifact",
  );
  noReplayData();
  fireEvent.click(screen.getByRole("tab", { name: "Records" }));
  expect(screen.getByRole("alert")).toHaveTextContent(
    "different retained artifact",
  );
  expect(
    screen.queryByRole("table", { name: "Retained records" }),
  ).not.toBeInTheDocument();
});

it("defaults Records to the actual retained JSONL when canonical replay artifacts are absent", async () => {
  const { api } = mount(
    async (path) =>
      path.includes("artifact=messages.jsonl")
        ? {
            records: [],
            next_offset: null,
            artifact: { id: "messages", name: "messages.jsonl" },
          }
        : { records: [], next_offset: null },
    "succeeded",
    [{ id: "messages", name: "messages.jsonl", role: "supervised_dataset" }],
  );
  await selectRun();
  await screen.findByRole("heading", { name: "No replay artifacts retained" });
  fireEvent.click(screen.getByRole("tab", { name: "Records" }));
  expect(
    await screen.findByRole("heading", {
      name: "No retained records in this window",
    }),
  ).toBeVisible();
  expect(api.get).toHaveBeenCalledWith(
    "/api/lab/runs/first-run/records?offset=0&limit=100&artifact=messages.jsonl",
    expect.any(AbortSignal),
  );
  expect(
    screen.queryByRole("heading", { name: "No retained record artifact" }),
  ).not.toBeInTheDocument();
});
