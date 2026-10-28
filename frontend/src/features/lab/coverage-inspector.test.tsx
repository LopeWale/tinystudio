import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { ComponentActionRegistry } from "../../controllers/componentActions";
import { ReplayViews } from "./replay";
import { ComponentActionsProvider } from "../assistant/actions";
import {
  CoverageInspector,
  coverageOption,
  type CoverageInspection,
} from "./coverage-inspector";
vi.mock("./chart", () => ({
  Chart: ({ onPoint }: any) => (
    <button onClick={() => onPoint({ cellIndex: 1 })}>
      Pick heatmap fault
    </button>
  ),
}));
vi.mock("./trend-workbench", () => ({
  TrendWorkbench: ({ controller }: any) => (
    <output data-testid="chart-pin">
      {controller.selected?.identity.rowIndex ?? "none"}
    </output>
  ),
}));
vi.mock("./graph", () => ({ DefinitionGraph: () => null }));
afterEach(cleanup);
const source = {
  identity: { run_id: "run", artifact_id: "retained", row_index: 1004 },
  record: { value: -0.125, scenario_id: "fault" },
};
const data: CoverageInspection = {
  run_id: "run",
  row_axis: "scenario_id",
  column_axis: "partition",
  rows: ["baseline", "fault", "Not recorded"],
  columns: ["train", "final_test"],
  counts: { records: 1005, observed: 1004, estimated: 1, missing: 1 },
  partition_counts: { train: 1004, test: 1 },
  cells: [
    { row: "baseline", column: "train", count: 1004, sources: [] },
    { row: "fault", column: "final_test", count: 1, sources: [source] },
  ],
  artifacts: [{ id: "retained", sha256: "fixed" }],
  complete: true,
  qualification: "All retained transformed rows scanned.",
};
function view(
  get: any,
  onSelect = vi.fn(),
  registry = new ComponentActionRegistry(),
  scopeKey = "project",
) {
  registry.setScope({ user: "user", project: scopeKey, route: "/lab" });
  const runtime = {
    api: { get },
    scopeKey,
    route: "/lab",
    project: scopeKey,
    user: { id: "user" },
    evidence: new EvidenceRegistry(),
  } as unknown as StudioRuntime;
  return (
    <StudioContext.Provider value={runtime}>
      <ComponentActionsProvider registry={registry}>
        <CoverageInspector runId="run" onSelect={onSelect} />
      </ComponentActionsProvider>
    </StudioContext.Provider>
  );
}
it("builds a heatmap from complete cells and keeps missing metadata explicit", () => {
  const option = coverageOption(data) as any;
  expect(option.xAxis.data).toEqual(["train", "final_test"]);
  expect(option.yAxis.data).toContain("Not recorded");
  expect(option.series[0].data).toEqual([
    { value: [0, 0, 1004], cellIndex: 0 },
    { value: [1, 1, 1], cellIndex: 1 },
  ]);
});
it("shows counts beyond 1000 and opens an exact retained row from a heatmap cell", async () => {
  const get = vi.fn().mockResolvedValue(data),
    onSelect = vi.fn();
  render(view(get, onSelect));
  await screen.findByText(/1005 retained transformed records/);
  fireEvent.click(screen.getByText("Pick heatmap fault"));
  fireEvent.click(screen.getByText("Inspect retained row 1004"));
  expect(onSelect).toHaveBeenCalledWith(source);
  expect(get.mock.calls[0][0]).toContain(
    "coverage?row_axis=scenario_id&column_axis=partition",
  );
  expect(screen.getByText(/"test": 1/)).toBeInTheDocument();
});
it("lets assistant actions change axes and discards obsolete coverage responses", async () => {
  let resolveOld!: (data: CoverageInspection) => void;
  const get = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValue({
      ...data,
      row_axis: "device_id",
      counts: { records: 23 },
    });
  const registry = new ComponentActionRegistry();
  render(view(get, vi.fn(), registry));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
  const signal = get.mock.calls[0][1] as AbortSignal;
  const action = registry
    .inspect()
    .actions.find((a) => a.id === "lab.coverage-row-axis")!;
  await act(async () => {
    await registry.execute(action.ref, "device_id", false);
  });
  expect(signal.aborted).toBe(true);
  await screen.findByText(/23 retained transformed records/);
  await act(async () => resolveOld(data));
  expect(
    screen.queryByText(/1005 retained transformed records/),
  ).not.toBeInTheDocument();
  expect(get.mock.calls[1][0]).toContain("row_axis=device_id");
});
it("renders actionable scan failures without partial coverage", async () => {
  render(
    view(
      vi
        .fn()
        .mockRejectedValue(
          new Error(
            "coverage axis device_id exceeds 256 values; choose a lower-cardinality axis or export",
          ),
        ),
    ),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "exceeds 256 values",
  );
  expect(screen.queryByText("Pick heatmap fault")).not.toBeInTheDocument();
});

it("keeps the loaded chart pin while inspecting a coverage row beyond its window", async () => {
  const get = vi.fn(async (path: string) =>
    path.includes("/coverage?")
      ? data
      : {
          identity: source.identity,
          observation: source.record,
          observation_jsonl: '{"value":-0.125,"scenario_id":"fault"}',
          matches: [],
          legacy_unqualified_records: 0,
          qualification: "Exact fingerprint matching",
        },
  );
  const runtime = {
    api: { get },
    scopeKey: "project",
    route: "/lab",
    project: "project",
    user: { id: "user" },
    evidence: new EvidenceRegistry(),
  } as unknown as StudioRuntime;
  render(
    <StudioContext.Provider value={runtime}>
      <ReplayViews
        records={[
          {
            time: 1,
            value: 2,
            pressure: 3,
            units: "V",
            source_record_id: "first",
          },
        ]}
        rawRecords={[]}
        events={[]}
        run={
          {
            id: "run",
            result: {
              artifacts: [
                {
                  id: "retained",
                  name: "processed.jsonl",
                  role: "transformed_dataset",
                },
              ],
            },
          } as any
        }
      />
    </StudioContext.Provider>,
  );
  const initialPin = screen.getByTestId("chart-pin").textContent;
  fireEvent.click(screen.getByRole("button", { name: "Dataset coverage" }));
  await screen.findByText(/1005 retained transformed records/);
  fireEvent.click(screen.getByText("Pick heatmap fault"));
  fireEvent.click(screen.getByText("Inspect retained row 1004"));
  expect(
    await screen.findByText(
      /outside the loaded chart window; the chart pin has not moved/,
    ),
  ).toBeInTheDocument();
  expect(screen.getByTestId("chart-pin")).toHaveTextContent(initialPin!);
  await waitFor(() =>
    expect(
      get.mock.calls.some(([path]) =>
        path.endsWith("representations?artifact_id=retained&row=1004"),
      ),
    ).toBe(true),
  );
});

it("rejects a malformed successful response without crashing the coverage lens", async () => {
  render(view(vi.fn().mockResolvedValue({ id: "run", state: "succeeded" })));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Coverage response is invalid",
  );
  expect(screen.queryByText("Pick heatmap fault")).not.toBeInTheDocument();
});
