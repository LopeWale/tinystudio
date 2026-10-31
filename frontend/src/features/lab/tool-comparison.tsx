import { useMemo, useState } from "react";
import {
  AdvancedTable,
  type TableColumn,
} from "../../components/studio/data-table";
import { Badge, Button, Evidence } from "../../components/studio/primitives";
import {
  InspectionWorkspace,
  WorkspaceDisclosure,
} from "../../components/studio/workspace";

type Operation = "add" | "subtract" | "multiply" | "divide";
type Task = { operation: Operation; left: number; right: number };
type Trace = {
  task: Task;
  policy_version: string;
  partition: "heldout";
  tool: {
    name: Operation;
    arguments: { left: number; right: number };
    result: number | null;
    error: null | { type: string; message: string };
  };
  evaluation: {
    expected: number;
    actual: number | null;
    correct: boolean;
    tool_error: boolean;
  };
};
type Case = {
  case_id: string;
  task: Task;
  expected: number;
  initial: Trace | null;
  final: Trace | null;
};
type Scores = {
  evaluated: number;
  correct: number;
  errors: number;
  accuracy: number | null;
};
export type ToolReport = {
  format: "tinystudio.tool-evaluation.v1";
  policy_kind: "categorical" | "autoregressive";
  checkpoint_sha256: string;
  baseline: string;
  records: Case[];
  metrics: { cases: number; initial: Scores; final: Scores };
  qualification: {
    status: "completed" | "cancelled";
    comparison_complete: boolean;
    heldout_tasks_used_for_updates: false;
    weights_unchanged: true;
    scope: string;
  };
};
const operations = ["add", "subtract", "multiply", "divide"];
const fail = (path: string, message: string): never => {
  throw new Error(`Invalid tool comparison ${path}: ${message}`);
};
const object = (value: unknown, path: string): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : fail(path, "must be an object");
const finite = (value: unknown, path: string): number =>
  typeof value === "number" && Number.isFinite(value)
    ? value
    : fail(path, "must be a finite number");
const string = (value: unknown, path: string): string =>
  typeof value === "string" && value.trim().length > 0
    ? value
    : fail(path, "must be a nonempty string");
const digest = (value: unknown, path: string) => {
  if (!/^[a-f0-9]{64}$/.test(string(value, path)))
    fail(path, "must be a lowercase SHA-256 digest");
};
const calculate = (operation: Operation, left: number, right: number) =>
  operation === "add"
    ? left + right
    : operation === "subtract"
      ? left - right
      : operation === "multiply"
        ? left * right
        : left / right;
const close = (a: number, b: number) =>
  Math.abs(a - b) <= Math.max(1e-6, 1e-9 * Math.max(Math.abs(a), Math.abs(b)));
function task(value: unknown, path: string): Task {
  const t = object(value, path);
  if (!operations.includes(String(t.operation)))
    fail(path, "unsupported operation");
  const left = finite(t.left, `${path}.left`),
    right = finite(t.right, `${path}.right`);
  if (
    Math.max(Math.abs(left), Math.abs(right)) > 1e6 ||
    (t.operation === "divide" && right === 0)
  )
    fail(path, "invalid bounded arithmetic task");
  return { operation: t.operation as Operation, left, right };
}
/** Validate retained arithmetic independently; never infer evaluation from training updates. */
export function validateToolReport(value: unknown): ToolReport {
  const r = object(value, "report");
  if (r.format !== "tinystudio.tool-evaluation.v1")
    fail("format", "unsupported or missing format");
  if (!["categorical", "autoregressive"].includes(String(r.policy_kind)))
    fail("policy_kind", "unsupported policy kind");
  digest(r.checkpoint_sha256, "checkpoint_sha256");
  if (r.baseline !== "Incoming policy before this attempt's updates (greedy)")
    fail(
      "baseline",
      "must identify the incoming policy before this attempt's updates",
    );
  if (!Array.isArray(r.records) || !r.records.length || r.records.length > 1000)
    fail("records", "requires 1 to 1000 heldout cases");
  const ids = new Set<string>(),
    tasks = new Set<string>();
  const versions = { initial: new Set<string>(), final: new Set<string>() };
  const counts = {
    initial: { evaluated: 0, correct: 0, errors: 0 },
    final: { evaluated: 0, correct: 0, errors: 0 },
  };
  (r.records as unknown[]).forEach((entry, index) => {
    const p = `records[${index}]`,
      row = object(entry, p),
      t = task(row.task, `${p}.task`);
    const id = string(row.case_id, `${p}.case_id`),
      key = JSON.stringify([t.operation, t.left, t.right]);
    if (id !== `heldout:${index}` || ids.has(id))
      fail(`${p}.case_id`, "requires unique authored heldout order");
    if (tasks.has(key)) fail(`${p}.task`, "duplicate heldout task");
    ids.add(id);
    tasks.add(key);
    const expected = calculate(t.operation, t.left, t.right);
    if (finite(row.expected, `${p}.expected`) !== expected)
      fail(`${p}.expected`, "does not match requested arithmetic");
    for (const arm of ["initial", "final"] as const) {
      if (row[arm] === null) continue;
      const ap = `${p}.${arm}`,
        trace = object(row[arm], ap),
        tt = task(trace.task, `${ap}.task`);
      if (JSON.stringify(tt) !== JSON.stringify(t))
        fail(ap, "trace task does not match case");
      if (trace.partition !== "heldout") fail(ap, "requires heldout partition");
      digest(trace.policy_version, `${ap}.policy_version`);
      versions[arm].add(trace.policy_version as string);
      const tool = object(trace.tool, `${ap}.tool`),
        args = object(tool.arguments, `${ap}.tool.arguments`),
        evaluation = object(trace.evaluation, `${ap}.evaluation`);
      if (!operations.includes(String(tool.name)))
        fail(ap, "unsupported tool name");
      const left = finite(args.left, `${ap}.tool.arguments.left`),
        right = finite(args.right, `${ap}.tool.arguments.right`);
      const error = tool.error !== null;
      if (error) {
        const details = object(tool.error, `${ap}.tool.error`);
        string(details.type, `${ap}.tool.error.type`);
        string(details.message, `${ap}.tool.error.message`);
        if (tool.result !== null) fail(ap, "tool error requires null result");
      } else {
        const result = finite(tool.result, `${ap}.tool.result`);
        if (
          Math.max(Math.abs(left), Math.abs(right)) > 1e6 ||
          (tool.name === "divide" && right === 0) ||
          result !== calculate(tool.name as Operation, left, right)
        )
          fail(ap, "tool result does not match called arithmetic");
      }
      if (
        finite(evaluation.expected, `${ap}.evaluation.expected`) !== expected ||
        evaluation.actual !== tool.result ||
        evaluation.tool_error !== error ||
        evaluation.correct !==
          (!error && close(tool.result as number, expected))
      )
        fail(ap, "inconsistent evaluation outcome");
      counts[arm].evaluated++;
      counts[arm].correct += Number(evaluation.correct);
      counts[arm].errors += Number(error);
    }
  });
  for (const arm of ["initial", "final"] as const)
    if (versions[arm].size > 1)
      fail(arm, "evaluation pass must use one immutable policy");
  const metrics = object(r.metrics, "metrics");
  if (metrics.cases !== ids.size)
    fail("metrics.cases", "must match retained cases");
  for (const arm of ["initial", "final"] as const) {
    const scores = object(metrics[arm], `metrics.${arm}`),
      expected = counts[arm];
    for (const key of ["evaluated", "correct", "errors"] as const)
      if (scores[key] !== expected[key])
        fail(`metrics.${arm}.${key}`, "does not match retained traces");
    if (
      scores.accuracy !==
      (expected.evaluated ? expected.correct / expected.evaluated : null)
    )
      fail(
        `metrics.${arm}.accuracy`,
        "must use evaluated calls as denominator",
      );
  }
  const q = object(r.qualification, "qualification");
  if (
    !["completed", "cancelled"].includes(String(q.status)) ||
    typeof q.comparison_complete !== "boolean" ||
    q.heldout_tasks_used_for_updates !== false ||
    q.weights_unchanged !== true
  )
    fail(
      "qualification",
      "requires immutable heldout evaluation qualification",
    );
  string(q.scope, "qualification.scope");
  if (
    q.comparison_complete &&
    (q.status !== "completed" ||
      counts.initial.evaluated !== ids.size ||
      counts.final.evaluated !== ids.size)
  )
    fail(
      "qualification",
      "complete comparison requires both complete evaluation passes",
    );
  return value as ToolReport;
}
const taskText = (t: Task) => `${t.operation}(${t.left}, ${t.right})`;
const call = (t: Trace | null) =>
  t
    ? `${t.tool.name}(${t.tool.arguments.left}, ${t.tool.arguments.right})`
    : "Unavailable";
const result = (t: Trace | null) =>
  t
    ? t.tool.error
      ? `${t.tool.error.type}: ${t.tool.error.message}`
      : String(t.tool.result)
    : "Unavailable";
const outcome = (t: Trace | null) =>
  !t
    ? "Unavailable"
    : t.evaluation.tool_error
      ? "Tool error"
      : t.evaluation.correct
        ? "Correct"
        : "Incorrect";
export function ToolComparison(props: {
  report: unknown;
  runId: string;
  scopeKey: string;
  runState?: string;
}) {
  const parsed = useMemo(() => {
    try {
      return { report: validateToolReport(props.report) };
    } catch (error) {
      return { error: error instanceof Error ? error.message : String(error) };
    }
  }, [props.report]);
  if (!parsed.report)
    return (
      <p role="alert" className="text-body-2-regular text-text-error-primary">
        {parsed.error}
      </p>
    );
  return (
    <ToolWorkspace
      key={JSON.stringify([props.scopeKey, props.runId])}
      {...props}
      report={parsed.report}
    />
  );
}
function ToolWorkspace({
  report: r,
  runId,
  scopeKey,
  runState,
}: {
  report: ToolReport;
  runId: string;
  scopeKey: string;
  runState?: string;
}) {
  const [selectedId, setSelectedId] = useState<string>();
  const selected = r.records.find((row) => row.case_id === selectedId);
  const complete =
    runState === "succeeded" &&
    r.qualification.status === "completed" &&
    r.qualification.comparison_complete;
  const columns: TableColumn<Case>[] = [
    { id: "task", label: "Matched task", value: (row) => taskText(row.task) },
    { id: "expected", label: "Expected", value: (row) => row.expected },
    ...(["initial", "final"] as const).flatMap((arm) => [
      {
        id: `${arm}-call`,
        label: `${arm === "initial" ? "Incoming" : "Final"} call`,
        value: (row: Case) => call(row[arm]),
      },
      {
        id: `${arm}-result`,
        label: `${arm === "initial" ? "Incoming" : "Final"} result`,
        value: (row: Case) => result(row[arm]),
      },
      {
        id: `${arm}-outcome`,
        label: `${arm === "initial" ? "Incoming" : "Final"} outcome`,
        value: (row: Case) => outcome(row[arm]),
      },
    ]),
  ];
  return (
    <section
      aria-label="Tool policy comparison"
      className="flex flex-col gap-4"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-headline-medium">Heldout tool policy comparison</h2>
        <Badge>
          {complete
            ? "Complete"
            : r.qualification.status === "cancelled"
              ? "Cancelled · incomplete"
              : "Incomplete"}
        </Badge>
        <Badge>Heldout only</Badge>
      </div>
      <p className="text-body-2-regular text-text-secondary">
        {r.baseline}. Final policy reloaded from retained checkpoint. Execution
        completion and task correctness are separate outcomes.{" "}
        {r.qualification.scope}
      </p>
      <div className="metrics-strip">
        {(["initial", "final"] as const).map((arm) => (
          <div key={arm} className="metric-cell">
            <span className="text-caption-1-semibold text-text-secondary">
              {arm === "initial" ? "Incoming policy" : "Final policy"}
            </span>
            <strong className="text-body-2-medium">
              {r.metrics[arm].correct}/{r.metrics[arm].evaluated} correct ·{" "}
              {r.metrics[arm].errors} errors · accuracy{" "}
              {r.metrics[arm].accuracy ?? "Unavailable"}
            </strong>
            <span className="text-caption-1-regular">
              {r.metrics[arm].evaluated}/{r.metrics.cases} tasks evaluated
            </span>
          </div>
        ))}
      </div>
      <InspectionWorkspace
        label="Selected tool evaluation evidence"
        inspector={
          selected ? (
            <Evidence
              label={`Heldout tool case ${selected.case_id}`}
              data={{
                run_id: runId,
                case: selected,
                checkpoint_sha256: r.checkpoint_sha256,
                baseline: r.baseline,
              }}
            >
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setSelectedId(undefined)}
              >
                Close case
              </Button>
              <h3 className="text-headline-medium">
                Heldout tool case {selected.case_id}
              </h3>
              <p className="text-body-2-regular">
                {taskText(selected.task)} · expected {selected.expected}
              </p>
              {(["initial", "final"] as const).map((arm) => (
                <WorkspaceDisclosure
                  key={arm}
                  title={
                    arm === "initial"
                      ? "Incoming policy trace"
                      : "Final policy trace"
                  }
                  summary={`${call(selected[arm])} · ${outcome(selected[arm])}`}
                >
                  <pre className="whitespace-pre-wrap break-all text-caption-1-medium">
                    {selected[arm]
                      ? JSON.stringify(selected[arm], null, 2)
                      : "Unavailable: this evaluation call was not retained."}
                  </pre>
                </WorkspaceDisclosure>
              ))}
            </Evidence>
          ) : undefined
        }
      >
        <div className="baseline-table-frame">
          <AdvancedTable
            label="Heldout tool tasks"
            layout="fill"
            rows={r.records}
            columns={columns}
            getRowId={(row) => row.case_id}
            scopeKey={JSON.stringify([scopeKey, runId])}
            rowProps={(row) => ({
              tabIndex: 0,
              "aria-label": `Inspect heldout tool case ${row.case_id}`,
              "aria-selected": row.case_id === selectedId,
              onClick: () => setSelectedId(row.case_id),
              onKeyDown: (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setSelectedId(row.case_id);
                }
              },
            })}
          />
        </div>
      </InspectionWorkspace>
      <WorkspaceDisclosure
        title="Reported metrics and qualification"
        summary="Retained heldout evidence"
      >
        <pre className="whitespace-pre-wrap break-all text-caption-1-medium">
          {JSON.stringify(
            {
              metrics: r.metrics,
              qualification: r.qualification,
              checkpoint_sha256: r.checkpoint_sha256,
            },
            null,
            2,
          )}
        </pre>
      </WorkspaceDisclosure>
    </section>
  );
}
