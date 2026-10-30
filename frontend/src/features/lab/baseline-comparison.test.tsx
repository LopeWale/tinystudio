import { Blob } from "node:buffer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import {
  BaselineComparison,
  RetainedBaselineComparison,
} from "./baseline-comparison";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
const records = [
  {
    source_id: "case-1",
    episode_id: "heldout-1",
    arm: "retrieval",
    expected: "-0.12500000000000000001",
    prediction: "-0.12500000000000000001",
    unit: "V",
    model: { artifact_id: "model-1", sha256: "fixed-model-hash" },
    cases: { artifact_id: "cases-1", sha256: "fixed-cases-hash" },
    retrieval: { retrieved_ids: ["doc-1", "doc-2"], included_ids: ["doc-1"] },
    output: { completion: "-0.12500000000000000001" },
  },
  {
    source_id: "invalid-2",
    episode_id: "heldout-1",
    arm: "retrieval",
    expected: "-2",
    prediction: "Answer: -2",
  },
];
const comparison = {
  passed: true,
  qualification: { status: "qualified" },
  metrics: {
    aggregate: {
      examples: 1,
      candidate_mae: "0",
      baseline_mae: "0.00000000000000000001",
      candidate_minus_baseline_mae: "-0.00000000000000000001",
    },
    per_example: [
      {
        source_id: "case-1",
        episode_id: "heldout-1",
        candidate_absolute_error: "0",
        baseline_absolute_error: "0.00000000000000000001",
      },
    ],
  },
  checks: [],
};
function view(
  report: unknown = comparison,
  rows: readonly unknown[] = records,
  scopeKey = "project",
  runId = "run",
  execution = "completed",
  evidence = new EvidenceRegistry(),
) {
  return (
    <StudioContext.Provider
      value={
        {
          route: "/lab",
          project: scopeKey,
          user: { id: "user" },
          scopeKey,
          evidence,
        } as unknown as StudioRuntime
      }
    >
      <BaselineComparison
        comparison={report}
        records={rows}
        scopeKey={scopeKey}
        runId={runId}
        execution={execution}
      />
    </StudioContext.Provider>
  );
}
it("preserves exact retained predictions and signed differences independently of execution success", () => {
  render(view());
  expect(
    screen.getByText(/Comparable · matched evaluation evidence is complete/),
  ).toBeVisible();
  expect(screen.getAllByText("-0.00000000000000000001")[0]).toBeVisible();
  const table = screen.getByRole("table", { name: "Evaluation cases" });
  expect(within(table).getAllByText("-0.12500000000000000001")).toHaveLength(2);
  expect(within(table).getByText("Answer: -2")).toBeVisible();
  expect(within(table).getByText("Partial")).toBeVisible();
});
it("keeps invalid parsing visible and supports result filters", () => {
  render(
    view({
      ...comparison,
      passed: false,
      metrics: {},
      qualification: { status: "failed" },
      checks: [
        {
          id: "pair:invalid-2:heldout-1",
          passed: false,
          code: "invalid_comparison_quantity",
          details: { error: "invalid decimal syntax" },
        },
      ],
    }),
  );
  expect(
    screen.getByText(/Incomplete or unqualified comparison/),
  ).toBeVisible();
  expect(screen.getByText(/Execution: completed/)).toBeVisible();
  const filter = screen.getByRole("radiogroup", {
    name: "Evaluation result filter",
  });
  fireEvent.click(within(filter).getByRole("radio", { name: "Fail" }));
  const table = screen.getByRole("table", { name: "Evaluation cases" });
  expect(within(table).getByText("Answer: -2")).toBeVisible();
  expect(within(table).queryByText("case-1")).toBeNull();
});
it("opens provenance with Enter and Space and resets selection on scope and run changes", () => {
  const mounted = render(view());
  fireEvent.keyDown(
    screen.getByRole("row", { name: "Inspect case-1 retrieval" }),
    { key: "Enter" },
  );
  expect(
    screen.getByRole("heading", { name: "Selected evaluation evidence" }),
  ).toBeVisible();
  expect(screen.getByText("fixed-model-hash")).toBeVisible();
  expect(screen.getByText('["doc-1","doc-2"]')).toBeVisible();
  expect(screen.getByText('["doc-1"]')).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Close case" }));
  fireEvent.keyDown(
    screen.getByRole("row", { name: "Inspect case-1 retrieval" }),
    { key: " " },
  );
  expect(screen.getByText("fixed-cases-hash")).toBeVisible();
  mounted.rerender(view(comparison, records, "next-project"));
  expect(screen.queryByText("fixed-model-hash")).toBeNull();
  fireEvent.keyDown(
    screen.getByRole("row", { name: "Inspect case-1 retrieval" }),
    { key: "Enter" },
  );
  mounted.rerender(view(comparison, records, "next-project", "next-run"));
  expect(screen.queryByText("fixed-model-hash")).toBeNull();
});
it("reports missing comparisons and missing retained cases honestly", () => {
  const mounted = render(view(null, []));
  expect(
    screen.getByText(/No baseline comparison evidence is attached/),
  ).toBeVisible();
  mounted.rerender(view(comparison, []));
  expect(
    screen.getByText(/No retained per-case records are attached/),
  ).toBeVisible();
});
it("labels cancelled control comparisons incomplete without scoring transitions as answers", () => {
  render(
    view(
      {
        format: "tinystudio.control-evaluation.v1",
        metrics: {
          policy: {
            mean_absolute_tracking_error: 0.25,
            constraint_violations: 0,
            mean_reward: -0.25,
            transitions: 1,
          },
          baseline: { mean_absolute_tracking_error: null, transitions: 0 },
          paired_initial_states: false,
          paired_episodes: 0,
        },
        qualification: {
          comparison_complete: false,
          status: "cancelled",
          seeds: [20],
          baseline: "quantized PD",
        },
      },
      [
        {
          episode: 0,
          step: 0,
          arm: "policy",
          reward: -0.25,
          absolute_tracking_error: 0.25,
          constraint_violation: false,
        },
      ],
      "project",
      "run",
      "cancelled",
    ),
  );
  expect(
    screen.getByText(/Incomplete or unqualified comparison/),
  ).toBeVisible();
  expect(screen.getByText(/Execution: cancelled/)).toBeVisible();
  expect(
    screen.getByRole("table", { name: "Control transitions" }),
  ).toBeVisible();
  expect(screen.queryByRole("columnheader", { name: "Score" })).toBeNull();
});
it("shows complete control pairing and separate policy and baseline metrics", () => {
  render(
    view(
      {
        format: "tinystudio.control-evaluation.v1",
        metrics: {
          policy: { mean_absolute_tracking_error: 0.2 },
          baseline: { mean_absolute_tracking_error: 0.1 },
          paired_initial_states: true,
          paired_episodes: 4,
        },
        qualification: { comparison_complete: true, status: "completed" },
      },
      [],
    ),
  );
  expect(
    screen.getByText(/Comparable · matched evaluation evidence is complete/),
  ).toBeVisible();
  expect(screen.getAllByText("0.2")[0]).toBeVisible();
  expect(screen.getAllByText("0.1")[0]).toBeVisible();
});

it("uses arm-specific reported errors without rounding tiny nonzero scores to a pass", () => {
  render(
    view(
      {
        ...comparison,
        metrics: {
          ...comparison.metrics,
          per_example: [
            {
              source_id: "case-1",
              episode_id: "heldout-1",
              candidate_absolute_error: "1e-1000",
              baseline_absolute_error: "0",
            },
          ],
        },
      },
      [records[0], { ...records[0], arm: "without_retrieval" }],
    ),
  );
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case-1 retrieval" }),
    ).getByText("Fail"),
  ).toBeVisible();
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case-1 without_retrieval" }),
    ).getByText("Pass"),
  ).toBeVisible();
});
it("publishes the selected case provenance to native evidence picking and clears it on scope change", () => {
  const evidence = new EvidenceRegistry();
  const mounted = render(
    view(comparison, records, "project", "run", "completed", evidence),
  );
  fireEvent.click(
    screen.getByRole("row", { name: "Inspect case-1 retrieval" }),
  );
  expect(evidence.snapshot()).toHaveLength(1);
  expect(evidence.snapshot()[0].data).toMatchObject({
    run_id: "run",
    record: {
      model: { sha256: "fixed-model-hash" },
      retrieval: { included_ids: ["doc-1"] },
    },
  });
  mounted.rerender(
    view(comparison, records, "other-project", "run", "completed", evidence),
  );
  expect(evidence.snapshot()).toHaveLength(0);
});

it("loads control records from the independent artifact instead of training updates", async () => {
  const download = vi.fn().mockResolvedValue(
    new Blob([
      JSON.stringify({
        format: "tinystudio.control-evaluation.v1",
        metrics: {},
        qualification: { comparison_complete: false, status: "cancelled" },
        records: [
          {
            source: "heldout_environment",
            episode: 8,
            step: 0,
            arm: "policy",
            reward: -0.4,
            absolute_tracking_error: 0.4,
          },
          { update: 1, loss: 10 },
        ],
      }),
    ]),
  );
  render(retainedView(download));
  expect(screen.getByRole("status")).toHaveTextContent(
    "Loading retained independent evaluation report",
  );
  expect(
    await screen.findByRole("row", { name: "Inspect 8 policy" }),
  ).toBeVisible();
  expect(
    within(
      screen.getByRole("table", { name: "Control transitions" }),
    ).queryByText("10"),
  ).toBeNull();
  expect(download).toHaveBeenCalledWith(
    "/api/lab/artifacts/evaluation%2F1",
    expect.any(AbortSignal),
  );
});
it("discards late report downloads after scope changes and reports download failures honestly", async () => {
  let resolveOld!: (value: Blob) => void;
  const oldDownload = vi.fn().mockReturnValue(
    new Promise<Blob>((resolve) => {
      resolveOld = resolve;
    }),
  );
  const mounted = render(retainedView(oldDownload));
  const currentDownload = vi
    .fn()
    .mockRejectedValue(new Error("Artifact not found"));
  mounted.rerender(retainedView(currentDownload, "other-project"));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Artifact not found",
  );
  await act(async () =>
    resolveOld(
      new Blob([
        JSON.stringify({
          format: "tinystudio.control-evaluation.v1",
          records: [
            {
              source: "heldout_environment",
              episode: 99,
              step: 0,
              arm: "policy",
            },
          ],
        }),
      ]),
    ),
  );
  expect(screen.queryByRole("table")).toBeNull();
  expect(oldDownload.mock.calls[0][1].aborted).toBe(true);
});
it("rejects malformed report envelopes and checksum mismatches instead of falling back to updates", async () => {
  const malformed = vi
    .fn()
    .mockResolvedValue(
      new Blob([JSON.stringify({ format: "unknown", records: [] })]),
    );
  const mounted = render(retainedView(malformed));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "unsupported or invalid record envelope",
  );
  const digest = vi.fn().mockResolvedValue(new Uint8Array(32).buffer);
  vi.stubGlobal("crypto", { subtle: { digest } });
  const valid = vi.fn().mockResolvedValue(
    new Blob([
      JSON.stringify({
        format: "tinystudio.control-evaluation.v1",
        records: [],
      }),
    ]),
  );
  mounted.rerender(retainedView(valid, "other-project", "f".repeat(64)));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "checksum mismatch",
  );
  expect(screen.queryByRole("table")).toBeNull();
});
function retainedView(
  download: unknown,
  scopeKey = "project",
  sha256?: string,
) {
  return (
    <StudioContext.Provider
      value={
        {
          api: { download },
          route: "/lab",
          project: scopeKey,
          user: { id: "user" },
          scopeKey,
          evidence: new EvidenceRegistry(),
        } as unknown as StudioRuntime
      }
    >
      <RetainedBaselineComparison
        artifacts={[
          {
            id: "evaluation/1",
            name: "control-evaluation.json",
            role: "independent_evaluation",
            sha256,
          },
        ]}
        comparison={{ format: "tinystudio.control-evaluation.v1" }}
        records={[{ update: 1, loss: 10 }]}
        runId="run"
        scopeKey={scopeKey}
      />
    </StudioContext.Provider>
  );
}

it("loads every generation record from the verified report beyond the replay window", async () => {
  const report = JSON.stringify({
    format: "tinystudio.generation-evaluation.v1",
    comparison,
    records: Array.from({ length: 1001 }, (_, index) => ({
      ...records[0],
      source_id: `case-${index}`,
    })),
  });
  const { createHash, webcrypto } = await import("node:crypto");
  vi.stubGlobal("crypto", webcrypto);
  const download = vi.fn().mockResolvedValue(new Blob([report]));
  render(
    retainedView(
      download,
      "project",
      createHash("sha256").update(report).digest("hex"),
    ),
  );
  expect(
    await screen.findByText(/SHA-256 verified · 1001 evaluation records/),
  ).toBeVisible();
  expect(screen.getByRole("table", { name: "Evaluation cases" })).toBeVisible();
});
it("never treats training updates as held-out control records when the report is missing", () => {
  render(
    <StudioContext.Provider
      value={{ api: {}, scopeKey: "project" } as unknown as StudioRuntime}
    >
      <RetainedBaselineComparison
        artifacts={[]}
        comparison={{
          format: "tinystudio.control-evaluation.v1",
          metrics: {},
          qualification: { comparison_complete: false },
        }}
        records={[{ update: 1, loss: 10 }]}
        runId="run"
        scopeKey="project"
      />
    </StudioContext.Provider>,
  );
  expect(
    screen.getByText(/No retained per-case records are attached/),
  ).toBeVisible();
  expect(screen.queryByRole("row", { name: /Inspect/ })).toBeNull();
});

it("accepts numeric zero scores and keeps unrecognized score values partial", () => {
  render(
    view(
      {
        ...comparison,
        metrics: {
          per_example: [
            {
              source_id: "case-1",
              episode_id: "heldout-1",
              candidate_absolute_error: 0,
              baseline_absolute_error: "unknown",
            },
          ],
        },
      },
      [records[0], { ...records[0], arm: "without_retrieval" }],
    ),
  );
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case-1 retrieval" }),
    ).getByText("Pass"),
  ).toBeVisible();
  expect(
    within(
      screen.getByRole("row", { name: "Inspect case-1 without_retrieval" }),
    ).getByText("Partial"),
  ).toBeVisible();
});

it("preserves the precomputed evaluate_examples report comparison without inventing predictions", async () => {
  // evaluation._report envelope; research.execute_evaluation adds examples,
  // provenance, comparison and lineage, without retaining prediction records.
  const baseReport = {
    format: "tinystudio.evaluation.v1",
    passed: true,
    checks: [
      {
        id: "examples:present",
        passed: true,
        code: "evaluation_examples_required",
        details: {},
      },
    ],
    qualification: {
      status: "qualified",
      blocking_checks: [],
      research_overrides: [],
      scope: "supplied evidence and declared provenance only",
    },
    metrics: {},
  };
  const legacy = {
    ...baseReport,
    metrics: { examples: 1, passed_examples: 1 },
    examples: [{ ...baseReport, source_id: "precomputed-case" }],
    provenance: baseReport,
    comparison: { ...baseReport, metrics: comparison.metrics },
    lineage: baseReport,
  };
  const download = vi
    .fn()
    .mockResolvedValue(new Blob([JSON.stringify(legacy)]));
  render(retainedView(download));
  expect(
    await screen.findByText(
      /Comparable · matched evaluation evidence is complete/,
    ),
  ).toBeVisible();
  expect(screen.getAllByText("-0.00000000000000000001")[0]).toBeVisible();
  expect(
    screen.getByText(/No retained per-case records are attached/),
  ).toBeVisible();
  expect(screen.queryByRole("row", { name: /Inspect/ })).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});
it("rejects a precomputed format label without its actual validator report envelope", async () => {
  const download = vi.fn().mockResolvedValue(
    new Blob([
      JSON.stringify({
        format: "tinystudio.evaluation.v1",
        examples: [],
        comparison,
        lineage: {},
      }),
    ]),
  );
  render(retainedView(download));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "unsupported or invalid record envelope",
  );
  expect(screen.queryByRole("table")).toBeNull();
});

it("loads a complete checksummed matrix and requires the originating run to succeed", async () => {
  const report = {
    format: "tinystudio.method-matrix.v1",
    baseline_method: "reference-id",
    case_count: 1,
    method_count: 2,
    expected_coverage: 2,
    methods: [
      { id: "reference-id", label: "Reference", kind: "affine" },
      { id: "test-id", label: "Test method", kind: "affine" },
    ],
    records: [
      {
        method_id: "reference-id",
        source_id: "case",
        episode_id: "episode",
        expected: "-2.00000000000000000001",
        prediction: "-2.00000000000000000001",
      },
      {
        method_id: "test-id",
        source_id: "case",
        episode_id: "episode",
        expected: "-2.00000000000000000001",
        prediction: "-2.00000000000000000001",
      },
    ],
    evaluations: {
      "reference-id": {
        format: "tinystudio.evaluation.v1",
        checks: [],
        passed: true,
      },
      "test-id": {
        format: "tinystudio.evaluation.v1",
        checks: [],
        passed: true,
      },
    },
    comparisons: {
      "test-id": {
        format: "tinystudio.evaluation.v1",
        checks: [],
        passed: true,
        metrics: {
          aggregate: {
            candidate_mae: "0",
            baseline_mae: "0",
            candidate_minus_baseline_mae: "0",
          },
          per_example: [
            {
              source_id: "case",
              episode_id: "episode",
              candidate_absolute_error: "0",
              baseline_absolute_error: "0",
            },
          ],
        },
      },
    },
    qualification: { coverage_complete: true, execution: "completed" },
  };
  const encoded = JSON.stringify(report);
  const { createHash, webcrypto } = await import("node:crypto");
  vi.stubGlobal("crypto", webcrypto);
  const download = vi.fn().mockResolvedValue(new Blob([encoded]));
  const api = { download };
  const view = (runState: string) => (
    <StudioContext.Provider
      value={
        {
          api,
          route: "/lab",
          project: "project",
          scopeKey: "project",
          user: { id: "user" },
          evidence: new EvidenceRegistry(),
        } as unknown as StudioRuntime
      }
    >
      <RetainedBaselineComparison
        artifacts={[
          {
            id: "matrix",
            name: "method-matrix.json",
            role: "independent_evaluation",
            sha256: createHash("sha256").update(encoded).digest("hex"),
          },
        ]}
        comparison={null}
        records={[]}
        runId="run"
        scopeKey="project"
        execution="completed"
        runState={runState}
      />
    </StudioContext.Provider>
  );
  const mounted = render(view("cancelled"));
  expect(
    await screen.findByText(/SHA-256 verified · 2 evaluation records/),
  ).toBeVisible();
  expect(screen.getByText(/Incomplete or unqualified matrix/)).toBeVisible();
  expect(screen.queryByText(/Complete matched coverage/)).toBeNull();
  mounted.rerender(view("succeeded"));
  expect(screen.getByText(/Complete matched coverage/)).toBeVisible();
  expect(
    within(
      screen.getByRole("table", { name: "Method evaluation cases" }),
    ).getAllByText("-2.00000000000000000001"),
  ).toHaveLength(3);
});

it("retains exact positive, negative and typed integer targets in downloaded matrix rows and inspectors", async () => {
  const targets = [
    "9007199254740993",
    "-9007199254740993",
    { value: "9007199254740993", unit: "V" },
  ];
  const methods = [
    { id: "reference", label: "Reference", kind: "affine" },
    { id: "candidate", label: "Candidate", kind: "affine" },
  ];
  const matrix = {
    format: "tinystudio.method-matrix.v1",
    methods,
    baseline_method: "reference",
    case_count: 3,
    method_count: 2,
    expected_coverage: 6,
    records: methods.flatMap((method) =>
      targets.map((expected_display, index) => ({
        method_id: method.id,
        source_id: `case-${index}`,
        episode_id: "e",
        expected:
          index === 0
            ? "RAW_POSITIVE"
            : index === 1
              ? "RAW_NEGATIVE"
              : { value: "RAW_TYPED", unit: "V" },
        expected_display,
        prediction:
          typeof expected_display === "string"
            ? expected_display
            : expected_display.value,
        unit: "V",
        case_sha256: `original-case-${index}`,
      })),
    ),
    evaluations: Object.fromEntries(
      methods.map((method) => [
        method.id,
        {
          format: "tinystudio.evaluation.v1",
          passed: true,
          checks: [],
          examples: targets.map((_, index) => ({
            source_id: `case-${index}`,
            episode_id: "e",
            passed: true,
          })),
        },
      ]),
    ),
    comparisons: {
      candidate: {
        format: "tinystudio.evaluation.v1",
        passed: true,
        checks: [],
        metrics: {},
      },
    },
    qualification: { execution: "completed", coverage_complete: true },
  };
  // Raw JSON numbers emulate Python's accepted integer serialization. A JS
  // Number literal cannot construct these inputs without already rounding them.
  const payload = JSON.stringify(matrix)
    .replaceAll('"RAW_POSITIVE"', "9007199254740993")
    .replaceAll('"RAW_NEGATIVE"', "-9007199254740993")
    .replaceAll('"RAW_TYPED"', "9007199254740993");
  const { createHash, webcrypto } = await import("node:crypto");
  vi.stubGlobal("crypto", webcrypto);
  const download = vi.fn().mockResolvedValue(new Blob([payload]));
  const evidence = new EvidenceRegistry();
  render(
    <StudioContext.Provider
      value={
        {
          api: { download },
          route: "/lab",
          project: "project",
          scopeKey: "project",
          user: { id: "user" },
          evidence,
        } as unknown as StudioRuntime
      }
    >
      <RetainedBaselineComparison
        artifacts={[
          {
            id: "matrix",
            name: "method-matrix.json",
            role: "independent_evaluation",
            sha256: createHash("sha256").update(payload).digest("hex"),
          },
        ]}
        comparison={null}
        records={[]}
        runId="run"
        scopeKey="project"
        runState="succeeded"
      />
    </StudioContext.Provider>,
  );
  await screen.findByRole("table", { name: "Method evaluation cases" });
  for (const [index, expected] of targets.entries()) {
    const row = screen.getByRole("row", {
      name: `Inspect case case-${index} e`,
    });
    expect(
      within(row).getAllByRole("cell", {
        name:
          typeof expected === "string" ? expected : JSON.stringify(expected),
      })[0],
    ).toBeVisible();
    fireEvent.click(row);
    const picked = evidence.snapshot()[0].data as {
      record: { expected: unknown; case_sha256: string };
    };
    expect(picked.record.expected).toEqual(expected);
    expect(picked.record.case_sha256).toBe(`original-case-${index}`);
    expect(screen.queryByText("9007199254740992")).toBeNull();
    expect(screen.queryByText("-9007199254740992")).toBeNull();
  }
});
it("rejects older retained reports with unsafe integer targets and no exact display companion", async () => {
  const download = vi
    .fn()
    .mockResolvedValue(
      new Blob([
        '{"format":"tinystudio.generation-evaluation.v1","records":[{"source_id":"legacy","episode_id":"e","prediction":"9007199254740993","expected":9007199254740993}]}',
      ]),
    );
  render(retainedView(download));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "lacks exact target display",
  );
  expect(screen.queryByRole("table")).toBeNull();
});

it.each(["1" + "0".repeat(309), "-1" + "0".repeat(309)].flatMap((raw) => [
  raw, `{"value":${raw},"unit":"Pa"}`,
]))("rejects overflowing legacy target JSON %s", async (raw) => {
  const download = vi.fn().mockResolvedValue(new Blob([
    `{"format":"tinystudio.generation-evaluation.v1","records":[{"source_id":"overflow","episode_id":"e","prediction":"0","expected":${raw}}]}`,
  ]));
  render(retainedView(download));
  expect(await screen.findByRole("alert")).toHaveTextContent("lacks exact target display");
  expect(screen.queryByRole("table")).toBeNull();
});
