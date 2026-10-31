import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ToolComparison, validateToolReport } from "./tool-comparison";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
afterEach(cleanup);
function report(): any {
  const task = { operation: "add", left: 2, right: 3 };
  const trace = (name: string, result: number, correct: boolean) => ({
    task,
    policy_version: "a".repeat(64),
    partition: "heldout",
    tool: { name, arguments: { left: 2, right: 3 }, result, error: null },
    evaluation: { expected: 5, actual: result, correct, tool_error: false },
  });
  return {
    format: "tinystudio.tool-evaluation.v1",
    policy_kind: "categorical",
    checkpoint_sha256: "a".repeat(64),
    baseline: "Incoming policy before this attempt's updates (greedy)",
    records: [
      {
        case_id: "heldout:0",
        task,
        expected: 5,
        initial: trace("subtract", -1, false),
        final: trace("add", 5, true),
      },
    ],
    metrics: {
      cases: 1,
      initial: { evaluated: 1, correct: 0, errors: 0, accuracy: 0 },
      final: { evaluated: 1, correct: 1, errors: 0, accuracy: 1 },
    },
    qualification: {
      status: "completed",
      comparison_complete: true,
      heldout_tasks_used_for_updates: false,
      weights_unchanged: true,
      scope: "Local heldout tasks",
    },
  };
}
it("accepts independently checked wrong incoming call and correct final call", () => {
  expect(validateToolReport(report()).metrics.final.accuracy).toBe(1);
});
it.each([
  ["nonfinite operand", (r: any) => (r.records[0].task.left = Infinity)],
  ["false expected arithmetic", (r: any) => (r.records[0].expected = 6)],
  ["false tool arithmetic", (r: any) => (r.records[0].final.tool.result = 6)],
  [
    "false correctness",
    (r: any) => (r.records[0].initial.evaluation.correct = true),
  ],
  ["wrong actual", (r: any) => (r.records[0].final.evaluation.actual = 4)],
  [
    "wrong error flag",
    (r: any) => (r.records[0].final.evaluation.tool_error = true),
  ],
  ["wrong metrics", (r: any) => (r.metrics.final.correct = 0)],
  ["wrong denominator", (r: any) => (r.metrics.final.accuracy = 0.5)],
  [
    "missing final",
    (r: any) => {
      r.records[0].final = null;
      r.metrics.final = { evaluated: 0, correct: 0, errors: 0, accuracy: null };
    },
  ],
  [
    "invalid policy digest",
    (r: any) => (r.records[0].final.policy_version = "invalid"),
  ],
  ["cancelled complete", (r: any) => (r.qualification.status = "cancelled")],
  [
    "duplicate task",
    (r: any) => r.records.push({ ...r.records[0], case_id: "heldout:1" }),
  ],
  ["duplicate identity", (r: any) => r.records.push(r.records[0])],
])("rejects %s", (_, mutate) => {
  const r = report();
  mutate(r);
  expect(() => validateToolReport(r)).toThrow(/Invalid tool comparison/);
});
it("preserves errors and missing cancelled calls", () => {
  const r = report();
  r.qualification = {
    ...r.qualification,
    status: "cancelled",
    comparison_complete: false,
  };
  r.records[0].initial.tool = {
    name: "divide",
    arguments: { left: 2, right: 0 },
    result: null,
    error: { type: "ZeroDivisionError", message: "division by zero" },
  };
  r.records[0].initial.evaluation = {
    expected: 5,
    actual: null,
    correct: false,
    tool_error: true,
  };
  r.metrics.initial.errors = 1;
  r.records[0].final = null;
  r.metrics.final = { evaluated: 0, correct: 0, errors: 0, accuracy: null };
  expect(validateToolReport(r).records[0].initial?.tool.error?.type).toBe(
    "ZeroDivisionError",
  );
  render(
    <ToolComparison report={r} runId="r" scopeKey="p" runState="cancelled" />,
  );
  expect(screen.getByText("Cancelled · incomplete")).toBeVisible();
  expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
  expect(screen.getByText("Tool error")).toBeVisible();
});
it("keyboard selects native evidence and project scope resets selection", () => {
  const evidence = new EvidenceRegistry(),
    r = report();
  const ui = (scope: string, state = "succeeded") => (
    <StudioContext.Provider
      value={
        {
          evidence,
          route: "/lab",
          project: scope,
          scopeKey: scope,
        } as unknown as StudioRuntime
      }
    >
      <ToolComparison report={r} runId="r" scopeKey={scope} runState={state} />
    </StudioContext.Provider>
  );
  const view = render(ui("p"));
  fireEvent.keyDown(
    screen.getByRole("row", { name: "Inspect heldout tool case heldout:0" }),
    { key: "Enter" },
  );
  expect(evidence.snapshot()[0].data).toMatchObject({
    run_id: "r",
    case: r.records[0],
  });
  expect(screen.getByRole("button", { name: "Close case" })).toBeVisible();
  view.rerender(ui("other", "failed"));
  expect(screen.queryByRole("button", { name: "Close case" })).toBeNull();
  expect(screen.getByText("Incomplete")).toBeVisible();
});
it("reports invalid envelopes", () => {
  render(<ToolComparison report={{}} runId="r" scopeKey="p" />);
  expect(screen.getByRole("alert")).toHaveTextContent(
    "unsupported or missing format",
  );
});

it("keeps checkpoint byte identity distinct from the policy weight digest", () => {
  const r = report();
  r.checkpoint_sha256 = "b".repeat(64);
  expect(validateToolReport(r).checkpoint_sha256).toBe("b".repeat(64));
});
it("sorting preserves matched task and trace identities and scope resets table sorting", () => {
  const r = report();
  const second = structuredClone(r.records[0]);
  second.case_id = "heldout:1";
  second.task = { operation: "add", left: 1, right: 4 };
  for (const arm of ["initial", "final"]) {
    second[arm].task = second.task;
    second[arm].tool.arguments = { left: 1, right: 4 };
    if (arm === "initial") {
      second[arm].tool.result = -3;
      second[arm].evaluation.actual = -3;
    }
  }
  r.records.push(second);
  r.metrics.cases = 2;
  r.metrics.initial.evaluated = 2;
  r.metrics.final.evaluated = 2;
  r.metrics.final.correct = 2;
  const evidence = new EvidenceRegistry();
  const ui = (scope: string) => (
    <StudioContext.Provider
      value={
        { evidence, route: "/lab", project: scope } as unknown as StudioRuntime
      }
    >
      <ToolComparison
        report={r}
        runId="r"
        scopeKey={scope}
        runState="succeeded"
      />
    </StudioContext.Provider>
  );
  const view = render(ui("p"));
  fireEvent.click(screen.getByRole("button", { name: "Matched task" }));
  let rows = screen
    .getAllByRole("row")
    .filter((row) => row.hasAttribute("aria-selected"));
  expect(rows[0]).toHaveAttribute(
    "aria-label",
    "Inspect heldout tool case heldout:1",
  );
  expect(rows[0]).toHaveTextContent("subtract(1, 4)");
  expect(rows[0]).toHaveTextContent("-3");
  fireEvent.keyDown(rows[0], { key: " " });
  expect(
    screen.getByRole("heading", { name: "Heldout tool case heldout:1" }),
  ).toBeVisible();
  view.rerender(ui("new"));
  rows = screen
    .getAllByRole("row")
    .filter((row) => row.hasAttribute("aria-selected"));
  expect(rows[0]).toHaveAttribute(
    "aria-label",
    "Inspect heldout tool case heldout:0",
  );
});
