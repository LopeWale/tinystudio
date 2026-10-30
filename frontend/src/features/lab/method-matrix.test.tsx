import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import type { ExperimentDefinition } from "./definition";
import {
  MethodMatrixEditor,
  MatrixComparison,
  matrixCoverageIssues,
  exactExpected,
  unsafeExpectedWithoutDisplay,
} from "./method-matrix";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
beforeEach(() =>
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  ),
);
const definition = {
  parameters: {
    operation: "matrix",
    methods: [],
    baseline_method: "",
    max_new_tokens: 37,
    device: "cpu",
  },
  inputs: [{ artifact_id: "frozen-model", sha256: "hash", role: "model" }],
  retrieval: null,
} as unknown as ExperimentDefinition;
const runtime = {
  api: {
    get: vi
      .fn()
      .mockResolvedValue([
        { id: "frozen-model", name: "Attached model", sha256: "hash" },
      ]),
  },
  scopeKey: "project",
  route: "/lab",
  project: "project",
  user: { id: "user" },
  evidence: new EvidenceRegistry(),
} as unknown as StudioRuntime;
function Editor({
  initial = definition,
  changed = vi.fn(),
}: {
  initial?: ExperimentDefinition;
  changed?: (parameters: Record<string, unknown>) => void;
}) {
  const [current, setCurrent] = useState(initial);
  return (
    <StudioContext.Provider value={runtime}>
      <MethodMatrixEditor
        definition={current}
        onChange={(parameters) => {
          changed(parameters);
          setCurrent({ ...current, parameters });
        }}
      />
    </StudioContext.Provider>
  );
}
it("authors exact affine strings, preserving unrelated shared parameters and canonical input bindings", () => {
  const changed = vi.fn();
  render(<Editor changed={changed} />);
  fireEvent.click(
    screen.getByRole("button", { name: "Add conventional formula" }),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Method label" }), {
    target: { value: "Measured recipe" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Intercept" }), {
    target: { value: "-0.00000000000000000001" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add feature" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Feature 1 name" }), {
    target: { value: "position" },
  });
  fireEvent.change(
    screen.getByRole("textbox", { name: "Feature 1 coefficient" }),
    { target: { value: "-2.5000000000000000001" } },
  );
  const next = changed.mock.lastCall![0];
  expect(next).toEqual({
    operation: "matrix",
    methods: [
      {
        id: "affine-1",
        label: "Measured recipe",
        kind: "affine",
        coefficients: { position: "-2.5000000000000000001" },
        intercept: "-0.00000000000000000001",
      },
    ],
    baseline_method: "",
    max_new_tokens: 37,
    device: "cpu",
  });
  expect(definition.inputs).toHaveLength(1);
});
it("keeps baseline explicit and prevents removal until another baseline is selected", () => {
  const initial = {
    ...definition,
    parameters: {
      ...definition.parameters,
      methods: [
        {
          id: "anchor",
          label: "Reference",
          kind: "affine",
          coefficients: {},
          intercept: "0",
        },
        {
          id: "candidate",
          label: "Candidate",
          kind: "affine",
          coefficients: {},
          intercept: "0",
        },
      ],
      baseline_method: "anchor",
    },
  };
  const changed = vi.fn();
  render(<Editor initial={initial} changed={changed} />);
  fireEvent.keyDown(screen.getByRole("row", { name: "Edit Reference" }), {
    key: "Enter",
  });
  expect(screen.getByRole("button", { name: "Remove method" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: /Comparison baseline/ }));
  fireEvent.click(screen.getByRole("option", { name: "Candidate" }));
  expect(screen.getByRole("button", { name: "Remove method" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Remove method" }));
  expect(changed.mock.lastCall![0].baseline_method).toBe("candidate");
  expect(changed.mock.lastCall![0].methods).toHaveLength(1);
});
it("selects only attached models and permits shared artifacts across distinct methods", async () => {
  const changed = vi.fn();
  render(<Editor changed={changed} />);
  fireEvent.click(screen.getByRole("button", { name: "Add model" }));
  fireEvent.click(
    screen.getByRole("button", { name: /Attached model or checkpoint/ }),
  );
  fireEvent.click(
    await screen.findByRole("option", { name: "Attached model" }),
  );
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Include retrieval evidence" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Add model" }));
  fireEvent.click(
    screen.getByRole("button", { name: /Attached model or checkpoint/ }),
  );
  fireEvent.click(screen.getByRole("option", { name: "Attached model" }));
  expect(
    changed.mock.lastCall![0].methods.map(
      (method: Record<string, unknown>) => method.artifact_id,
    ),
  ).toEqual(["frozen-model", "frozen-model"]);
  expect(changed.mock.lastCall![0].methods[0].retrieval).toBe(true);
  expect(definition.inputs).toHaveLength(1);
});
it("enforces method and coefficient editing limits", () => {
  const methods = Array.from({ length: 16 }, (_, index) => ({
    id: `recipe-${index}`,
    label: `Recipe ${index}`,
    kind: "affine",
    intercept: "0",
    coefficients: Object.fromEntries(
      Array.from({ length: 32 }, (_, feature) => [`x${feature}`, "0"]),
    ),
  }));
  render(
    <Editor
      initial={{
        ...definition,
        parameters: { ...definition.parameters, methods },
      }}
    />,
  );
  expect(screen.getByRole("button", { name: "Add model" })).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Add conventional formula" }),
  ).toBeDisabled();
  fireEvent.keyDown(screen.getByRole("row", { name: "Edit Recipe 0" }), {
    key: " ",
  });
  expect(screen.getByRole("button", { name: "Add feature" })).toBeDisabled();
});
const base = {
  format: "tinystudio.evaluation.v1",
  passed: true,
  checks: [],
  qualification: { status: "qualified" },
  metrics: {},
};
export const matrixReport = {
  format: "tinystudio.method-matrix.v1",
  baseline_method: "yardstick",
  case_count: 2,
  method_count: 2,
  expected_coverage: 4,
  methods: [
    {
      id: "yardstick",
      label: "Conventional reference",
      kind: "affine",
      metadata: { fitting: "none", coefficients: { x: "1" }, intercept: "0" },
    },
    {
      id: "probe",
      label: "Renamed candidate",
      kind: "model",
      model: { artifact_id: "actual-model", sha256: "actual-hash" },
      metadata: { architecture: "actual architecture" },
      retrieval: false,
    },
  ],
  records: [
    {
      method_id: "yardstick",
      arm: "yardstick",
      source_id: "a",
      episode_id: "e1",
      expected: "-0.12500000000000000001",
      prediction: "-0.12500000000000000001",
      unit: "V",
    },
    {
      method_id: "yardstick",
      arm: "yardstick",
      source_id: "b",
      episode_id: "e2",
      expected: "-2",
      prediction: "-2",
      unit: "V",
    },
    {
      method_id: "probe",
      arm: "probe",
      source_id: "a",
      episode_id: "e1",
      expected: "-0.12500000000000000001",
      prediction: "-0.12500000000000000001",
      unit: "V",
    },
    {
      method_id: "probe",
      arm: "probe",
      source_id: "b",
      episode_id: "e2",
      expected: "-2",
      prediction: "Answer: -2",
      unit: "V",
    },
  ],
  evaluations: {
    yardstick: {
      ...base,
      examples: [
        { source_id: "a", passed: true },
        { source_id: "b", passed: true },
      ],
    },
    probe: {
      ...base,
      passed: false,
      examples: [
        { source_id: "a", passed: true },
        { source_id: "b", passed: false },
      ],
    },
  },
  comparisons: {
    probe: {
      ...base,
      passed: false,
      checks: [
        { id: "pair:b:e2", passed: false, code: "invalid_comparison_quantity" },
      ],
    },
  },
  qualification: {
    coverage_complete: true,
    execution: "completed",
    research_only: false,
  },
};
function matrixView(
  report = matrixReport,
  scopeKey = "project",
  runState = "succeeded",
) {
  return (
    <StudioContext.Provider value={runtime}>
      <MatrixComparison
        report={report}
        runId="run"
        scopeKey={scopeKey}
        runState={runState}
      />
    </StudioContext.Provider>
  );
}
it("uses the explicit arbitrary baseline ID and retains failed outputs with actual method provenance", () => {
  render(matrixView());
  expect(screen.getByText(/Complete matched coverage/)).toBeVisible();
  const table = screen.getByRole("table", { name: "Method evaluation cases" });
  expect(within(table).getByText("Answer: -2")).toBeVisible();
  expect(within(table).getByText("Fail")).toBeVisible();
  expect(screen.getByText("actual-hash")).toBeVisible();
  fireEvent.keyDown(
    screen.getByRole("row", { name: "Inspect method Conventional reference" }),
    { key: "Enter" },
  );
  expect(
    within(
      screen.getByRole("table", { name: "Method evaluation cases" }),
    ).queryByText("Fail"),
  ).toBeNull();
});
it("does not qualify duplicates, unmatched methods, missing coverage, or cancelled originating runs", () => {
  expect(
    matrixCoverageIssues({
      ...matrixReport,
      records: [...matrixReport.records.slice(0, 3), matrixReport.records[2]],
    }).join(" "),
  ).toMatch(/duplicate or unmatched/);
  expect(
    matrixCoverageIssues({
      ...matrixReport,
      records: matrixReport.records.map((row, index) =>
        index === 3 ? { ...row, method_id: "unknown" } : row,
      ),
    }).join(" "),
  ).toMatch(/unknown method/);
  expect(
    matrixCoverageIssues({
      ...matrixReport,
      qualification: {
        ...matrixReport.qualification,
        coverage_complete: false,
      },
    }).join(" "),
  ).toMatch(/did not qualify/);
  render(matrixView(matrixReport, "project", "cancelled"));
  expect(screen.getByText(/Incomplete or unqualified matrix/)).toBeVisible();
  expect(screen.queryByText(/Complete matched coverage/)).toBeNull();
});
it("resets method and case selection on scope changes and supports keyboard case inspection", () => {
  const mounted = render(matrixView());
  fireEvent.keyDown(screen.getByRole("row", { name: "Inspect case b e2" }), {
    key: " ",
  });
  expect(screen.getAllByText("Answer: -2").length).toBeGreaterThan(1);
  fireEvent.keyDown(
    screen.getByRole("row", { name: "Inspect method Conventional reference" }),
    { key: " " },
  );
  mounted.rerender(matrixView(matrixReport, "next-project"));
  expect(
    screen.getByRole("row", { name: "Inspect method Renamed candidate" }),
  ).toHaveAttribute("aria-selected", "true");
  expect(
    screen.getByRole("row", { name: "Inspect case b e2" }),
  ).toHaveAttribute("aria-selected", "false");
});

it("joins method, source and episode identities and preserves tiny nonzero reported errors", () => {
  const report = {
    ...matrixReport,
    records: matrixReport.records.map((record) => ({
      ...record,
      source_id: "same-source",
    })),
    comparisons: {
      probe: {
        ...base,
        metrics: {
          per_example: [
            {
              source_id: "same-source",
              episode_id: "e1",
              candidate_absolute_error: "0",
              baseline_absolute_error: "0",
            },
            {
              source_id: "same-source",
              episode_id: "e2",
              candidate_absolute_error: "0.0000000000000000000000001",
              baseline_absolute_error: "0",
            },
          ],
        },
      },
    },
  };
  report.evaluations = {
    yardstick: {
      ...base,
      examples: [
        { source_id: "same-source", episode_id: "e1", passed: true },
        { source_id: "same-source", episode_id: "e2", passed: true },
      ],
    },
    probe: {
      ...base,
      passed: false,
      examples: [
        { source_id: "same-source", episode_id: "e1", passed: true },
        { source_id: "same-source", episode_id: "e2", passed: false },
      ],
    },
  } as unknown as typeof report.evaluations;
  render(matrixView(report as unknown as typeof matrixReport));
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case same-source e1" }),
    ).getByText("Pass"),
  ).toBeVisible();
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case same-source e2" }),
    ).getByText("Fail"),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole("row", { name: "Inspect method Conventional reference" }),
  );
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case same-source e2" }),
    ).getByText("Pass"),
  ).toBeVisible();
});

it("keeps candidate correctness independent of invalid baseline pairs for repeated sources", () => {
  const report = {
    ...matrixReport,
    records: matrixReport.records.map((record, index) => ({
      ...record,
      source_id: "repeated",
      prediction:
        index === 0
          ? "invalid baseline"
          : index === 3
            ? "invalid candidate"
            : record.expected,
    })),
    evaluations: {
      yardstick: {
        ...base,
        passed: false,
        examples: [
          { source_id: "repeated", episode_id: "e1", passed: false },
          { source_id: "repeated", episode_id: "e2", passed: true },
        ],
      },
      probe: {
        ...base,
        passed: false,
        examples: [
          { source_id: "repeated", episode_id: "e1", passed: true },
          { source_id: "repeated", episode_id: "e2", passed: false },
        ],
      },
    },
    comparisons: {
      probe: {
        ...base,
        passed: false,
        checks: [
          {
            id: "pair:repeated:e1",
            passed: false,
            code: "invalid_comparison_quantity",
          },
          {
            id: "pair:repeated:e2",
            passed: false,
            code: "invalid_comparison_quantity",
          },
        ],
      },
    },
  };
  render(matrixView(report));
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case repeated e1" }),
    ).getByText("Pass"),
  ).toBeVisible();
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case repeated e1" }),
    ).getByText("Unqualified pair"),
  ).toBeVisible();
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case repeated e2" }),
    ).getByText("Fail"),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole("row", { name: "Inspect method Conventional reference" }),
  );
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case repeated e1" }),
    ).getByText("Fail"),
  ).toBeVisible();
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case repeated e2" }),
    ).getByText("Pass"),
  ).toBeVisible();
});
it("leaves ambiguous legacy source-only evaluations unscored regardless of failed pairs", () => {
  const report = {
    ...matrixReport,
    records: matrixReport.records.map((record) => ({
      ...record,
      source_id: "repeated",
    })),
    evaluations: {
      yardstick: {
        ...base,
        examples: [
          { source_id: "repeated", passed: true },
          { source_id: "repeated", passed: true },
        ],
      },
      probe: {
        ...base,
        examples: [
          { source_id: "repeated", passed: true },
          { source_id: "repeated", passed: false },
        ],
      },
    },
    comparisons: {
      probe: {
        ...base,
        passed: false,
        checks: [
          {
            id: "pair:repeated:e1",
            passed: false,
            code: "invalid_comparison_quantity",
          },
        ],
      },
    },
  };
  render(matrixView(report));
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case repeated e1" }),
    ).getByText("Unscored"),
  ).toBeVisible();
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case repeated e2" }),
    ).getByText("Unscored"),
  ).toBeVisible();
});

it.each(["1" + "0".repeat(309), "-1" + "0".repeat(309)].flatMap((raw) => [
  raw, `{"value":${raw},"unit":"Pa"}`,
]))("does not display overflowing decoded target %s", (raw) => {
  const record = { expected: JSON.parse(raw) };
  expect(unsafeExpectedWithoutDisplay(record)).toBe(true);
  expect(exactExpected(record)).toBe("Exact target unavailable in this report; inspect the original retained artifact.");
});
it("uses the exact companion when the original numeric target overflows", () => {
  const exact = "1" + "0".repeat(309);
  const record = { expected: JSON.parse(exact), expected_display: exact };
  expect(unsafeExpectedWithoutDisplay(record)).toBe(false);
  expect(exactExpected(record)).toBe(exact);
});
