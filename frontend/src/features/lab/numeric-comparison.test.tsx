import { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NumericComparison, validateNumericReport } from "./numeric-comparison";
vi.mock("../../app/StudioContext", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../app/StudioContext")>()),
  useStudio: () => ({
    evidence: { register: () => () => {} },
    route: "/lab",
    project: "project-1",
    user: { id: "user-1" },
  }),
}));
const chart = vi.hoisted(() => ({ current: undefined as any, mounts: 0 }));
vi.mock("./chart", () => ({
  Chart: (props: any) => {
    const [mount] = useState(() => ++chart.mounts);
    chart.current = { ...props, mount };
    return (
      <div role="group" aria-label={props.label} onKeyDown={props.onKeyDown} />
    );
  },
}));
vi.mock("./inputs", () => ({
  LabChoice: (props: any) => (
    <select
      aria-label={props.label}
      value={props.value}
      onChange={(event) => props.onChange(event.target.value)}
    >
      {props.options.map((option: any) => (
        <option key={option.id} value={option.id}>
          {option.name}
        </option>
      ))}
    </select>
  ),
}));
afterEach(() => {
  cleanup();
  chart.current = undefined;
});
const report = (): any => ({
  format: "tinystudio.numeric-evaluation.v1",
  task: "forecasting",
  partition: "validation",
  output_count: 2,
  model_sha256: "a".repeat(64),
  corpus_sha256: "b".repeat(64),
  baseline: {
    kind: "persistence",
    label: "Persistence",
    fit_source_record_ids: [],
    fit_case_count: 0,
  },
  records: [
    {
      case_id: "case-z",
      source_record_ids: ["target-z", "target-z-2"],
      context_source_record_ids: ["context-z"],
      prediction: [4, 8],
      target: [2, 4],
      baseline: [1, 1],
      raw_target_json: "[2, 4]",
    },
    {
      case_id: "case-a",
      source_record_ids: ["target-a", "target-a-2"],
      context_source_record_ids: ["context-a"],
      prediction: [5, 10],
      target: [5, 8],
      baseline: [3, 3],
      raw_target_json: "[5,8]",
    },
  ],
  metrics: {
    cases: 2,
    scalar_values: 4,
    aggregation: "flattened scalar mean",
    model: { mae: 2, mse: 6 },
    baseline: { mae: 2.75, mse: 9.75 },
    per_output: [
      {
        index: 0,
        unit: null,
        model: { mae: 1, mse: 2 },
        baseline: { mae: 1.5, mse: 2.5 },
      },
      {
        index: 1,
        unit: null,
        model: { mae: 3, mse: 10 },
        baseline: { mae: 4, mse: 17 },
      },
    ],
  },
  qualification: {
    status: "completed",
    comparison_complete: true,
    evaluation_valid: true,
    validation_only: true,
    scope: "Held-out validation only",
  },
});
const mount = (r = report(), extra = {}) =>
  render(
    <NumericComparison
      report={r}
      runId="run-1"
      scopeKey="project-1"
      runState="succeeded"
      {...extra}
    />,
  );
const series = () => chart.current.option.series as any[];
it("plots known multioutput values and errors as unconnected ordinal points", () => {
  mount();
  expect(series().map((s) => s.data.map((p: any) => p.value))).toEqual([
    [
      [1, 2],
      [2, 5],
    ],
    [
      [1, 4],
      [2, 5],
    ],
    [
      [1, 1],
      [2, 3],
    ],
  ]);
  expect(series().every((s) => s.lineStyle.opacity === 0)).toBe(true);
  expect(chart.current.option.xAxis.name).toContain("ordinal");
  fireEvent.change(screen.getByLabelText("Output component"), {
    target: { value: "1" },
  });
  expect(series()[0].data[1].value).toEqual([2, 8]);
  fireEvent.change(screen.getByLabelText("Numeric plot metric"), {
    target: { value: "error" },
  });
  expect(series().map((s) => s.data.map((p: any) => p.value))).toEqual([
    [
      [1, 4],
      [2, 2],
    ],
    [
      [1, 3],
      [2, 5],
    ],
  ]);
});
it("links point and keyboard selection to exact evidence, retains on output switch, clears on run and project", () => {
  const r = report(),
    v = mount(r);
  act(() => chart.current.onPoint(series()[1].data[1]));
  expect(screen.getByText("Validation case case-a")).toBeInTheDocument();
  expect(
    screen.getByText("Original target JSON").nextElementSibling,
  ).toHaveTextContent("[5,8]");
  const old = chart.current.mount;
  fireEvent.change(screen.getByLabelText("Output component"), {
    target: { value: "1" },
  });
  expect(chart.current.mount).not.toBe(old);
  expect(screen.getByText("Validation case case-a")).toBeInTheDocument();
  const p = screen.getByRole("group", {
    name: "Numeric validation comparison chart",
  });
  fireEvent.keyDown(p, { key: "Home" });
  expect(screen.getByText("Validation case case-z")).toBeInTheDocument();
  fireEvent.keyDown(p, { key: "End" });
  expect(screen.getByText("Validation case case-a")).toBeInTheDocument();
  fireEvent.keyDown(p, { key: "ArrowLeft" });
  expect(screen.getByText("Validation case case-z")).toBeInTheDocument();
  v.rerender(
    <NumericComparison report={r} runId="run-1" scopeKey="project-2" />,
  );
  expect(screen.queryByText("Validation case case-z")).not.toBeInTheDocument();
  act(() => chart.current.onPoint(series()[0].data[0]));
  v.rerender(
    <NumericComparison report={r} runId="run-2" scopeKey="project-2" />,
  );
  expect(screen.queryByText("Validation case case-z")).not.toBeInTheDocument();
});
it("preserves original classification JSON apart from canonical labels and has no continuous graph", () => {
  const r = report();
  r.task = "classification";
  r.output_count = 1;
  r.baseline = {
    kind: "training_majority",
    label: "Training majority",
    fit_source_record_ids: ["train-1"],
    fit_case_count: 1,
    constant: "1",
    tie_break: "lexicographic",
  };
  r.records = [
    {
      ...r.records[0],
      source_record_ids: ["target-z"],
      context_source_record_ids: [],
      prediction: "1",
      target: "1",
      baseline: "1",
      raw_target_json: "1.00",
    },
  ];
  r.metrics = {
    cases: 1,
    scalar_values: 1,
    aggregation: "case accuracy",
    model: { accuracy: 1 },
    baseline: { accuracy: 1 },
    per_output: [],
  };
  mount(r);
  expect(
    screen.queryByRole("group", {
      name: "Numeric validation comparison chart",
    }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("1.00")).toBeInTheDocument();
  expect(screen.getByText("Canonical target label")).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("row", { name: "Inspect validation case case-z" }),
  );
  expect(screen.getAllByText('"1"').length).toBeGreaterThan(0);
});
it.each([
  [
    "format",
    (r: any) => {
      r.format = "legacy";
    },
  ],
  [
    "partition",
    (r: any) => {
      r.partition = "training";
    },
  ],
  [
    "duplicate",
    (r: any) => {
      r.records[1].case_id = r.records[0].case_id;
    },
  ],
  [
    "width",
    (r: any) => {
      r.records[0].prediction = [1];
    },
  ],
  [
    "nonfinite",
    (r: any) => {
      r.records[0].target[0] = Infinity;
    },
  ],
  [
    "metric",
    (r: any) => {
      r.metrics.model.mae = NaN;
    },
  ],
  [
    "case count",
    (r: any) => {
      r.metrics.cases = 3;
    },
  ],
  [
    "scalar count",
    (r: any) => {
      r.metrics.scalar_values = 3;
    },
  ],
  [
    "index",
    (r: any) => {
      r.metrics.per_output[1].index = 0;
    },
  ],
  [
    "source",
    (r: any) => {
      r.records[0].source_record_ids = [];
    },
  ],
  [
    "raw JSON",
    (r: any) => {
      r.records[0].raw_target_json = "invalid";
    },
  ],
  [
    "digest",
    (r: any) => {
      r.model_sha256 = "missing";
    },
  ],
  [
    "cancelled complete",
    (r: any) => {
      r.qualification.status = "cancelled";
    },
  ],
])("rejects corrupt %s without fake completion", (_n, corrupt) => {
  const r = report();
  corrupt(r);
  expect(() => validateNumericReport(r)).toThrow(/Numeric evaluation/);
  mount(r);
  expect(screen.getByRole("alert")).toBeInTheDocument();
  expect(screen.queryByText("Complete")).not.toBeInTheDocument();
});
it("requires successful run and distinguishes cancelled retained evidence", () => {
  const r = report(),
    v = mount(r, { runState: "running" });
  expect(screen.getByText("Incomplete")).toBeInTheDocument();
  r.qualification = {
    ...r.qualification,
    status: "cancelled",
    comparison_complete: false,
  };
  v.rerender(
    <NumericComparison
      report={{ ...r }}
      runId="run-1"
      scopeKey="project-1"
      runState="cancelled"
    />,
  );
  expect(screen.getByText("Cancelled · incomplete")).toBeInTheDocument();
});
it("preserves actual ECharts zoom across point selection", async () => {
  const e = await import("echarts");
  e.setPlatformAPI({ measureText: (text) => ({ width: text.length * 7 }) });
  const runtime = e.init(null, undefined, {
    renderer: "svg",
    ssr: true,
    width: 800,
    height: 360,
  });
  mount();
  const merge = {
    replaceMerge: ["series", "grid", "xAxis", "yAxis", "dataZoom", "visualMap"],
  };
  try {
    runtime.setOption(chart.current.option, merge);
    runtime.dispatchAction({ type: "dataZoom", start: 25, end: 75 });
    act(() => chart.current.onPoint(series()[0].data[1]));
    runtime.setOption(chart.current.option, merge);
    expect(
      (runtime.getOption().dataZoom as any[]).map((z) => [z.start, z.end]),
    ).toEqual([
      [25, 75],
      [25, 75],
    ]);
  } finally {
    runtime.dispose();
  }
});
it("keeps stable case selection after sorting the records workspace", () => {
  mount();
  fireEvent.click(screen.getByRole("radio", { name: "Records" }));
  expect(
    screen.queryByRole("group", {
      name: "Numeric validation comparison chart",
    }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("row", { name: "Inspect validation case case-a" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Case" }));
  expect(
    screen.getByRole("row", { name: "Inspect validation case case-a" }),
  ).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("Validation case case-a")).toBeInTheDocument();
});
it("preserves empty and whitespace canonical class labels as distinct strings", () => {
  const r = report();
  r.task = "classification";
  r.output_count = 1;
  r.baseline = {
    kind: "training_majority",
    label: "Training majority",
    fit_source_record_ids: ["train-1"],
    fit_case_count: 1,
    constant: "",
    tie_break: "lexicographic",
  };
  r.records = [
    {
      ...r.records[0],
      source_record_ids: ["target-z"],
      context_source_record_ids: [],
      prediction: "",
      target: "",
      baseline: "",
      raw_target_json: '\"\"',
    },
    {
      ...r.records[1],
      source_record_ids: ["target-a"],
      context_source_record_ids: [],
      prediction: " ",
      target: " ",
      baseline: "",
      raw_target_json: '\" \"',
    },
  ];
  r.metrics = {
    cases: 2,
    scalar_values: 2,
    aggregation: "case accuracy",
    model: { accuracy: 1 },
    baseline: { accuracy: 0.5 },
    per_output: [],
  };
  expect(validateNumericReport(r).records.map((row) => row.target)).toEqual([
    "",
    " ",
  ]);
  mount(r);
  expect(
    screen.getByRole("row", { name: "Inspect validation case case-z" }),
  ).toHaveTextContent('""');
  expect(
    screen.getByRole("row", { name: "Inspect validation case case-a" }),
  ).toHaveTextContent('" "');
});

it.each([
  [
    "fit count mismatch",
    (r: any) => {
      r.baseline.fit_case_count = 1;
    },
  ],
  [
    "persistence training provenance",
    (r: any) => {
      r.baseline.fit_case_count = 1;
      r.baseline.fit_source_record_ids = ["train-1"];
    },
  ],
  [
    "forecast source width",
    (r: any) => {
      r.records[0].source_record_ids = ["one"];
    },
  ],
  [
    "forecast missing context",
    (r: any) => {
      r.records[0].context_source_record_ids = [];
    },
  ],
])("rejects %s provenance corruption", (_name, corrupt) => {
  const r = report();
  corrupt(r);
  expect(() => validateNumericReport(r)).toThrow(/Numeric evaluation/);
});
it("requires training constants and majority tie-break and fusion source custody", () => {
  const r = report();
  r.task = "regression";
  r.baseline = {
    kind: "training_mean",
    label: "Mean",
    fit_source_record_ids: ["train-1"],
    fit_case_count: 1,
    constant: [1, 3],
  };
  r.records.forEach((row: any) => {
    row.source_record_ids = ["same-source-valid-across-episodes"];
    row.context_source_record_ids = [];
  });
  expect(validateNumericReport(r)).toBe(r);
  delete r.baseline.constant;
  expect(() => validateNumericReport(r)).toThrow(/constant/);
  r.baseline.constant = [1, 3];
  r.records[0].source_record_ids.push("extra");
  expect(() => validateNumericReport(r)).toThrow(/source_record_ids/);
  r.records[0].source_record_ids.pop();
  r.records[0].context_source_record_ids = ["invalid-context"];
  expect(() => validateNumericReport(r)).toThrow(/context_source_record_ids/);
  r.records[0].context_source_record_ids = [];
  r.task = "classification";
  r.output_count = 1;
  r.baseline = {
    kind: "training_majority",
    label: "Majority",
    fit_source_record_ids: ["train-1"],
    fit_case_count: 1,
    constant: "",
  };
  r.records.forEach((row: any) => {
    row.prediction = "";
    row.target = "";
    row.baseline = "";
  });
  r.metrics = {
    cases: 2,
    scalar_values: 2,
    aggregation: "accuracy",
    model: { accuracy: 1 },
    baseline: { accuracy: 1 },
    per_output: [],
  };
  expect(() => validateNumericReport(r)).toThrow(/tie_break/);
  r.baseline.tie_break = "lexicographic";
  expect(validateNumericReport(r)).toBe(r);
});
