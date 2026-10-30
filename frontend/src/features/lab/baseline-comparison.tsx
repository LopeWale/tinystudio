import { useEffect, useMemo, useState } from "react";
import { useStudio } from "../../app/StudioContext";
import type { RetainedArtifact } from "./evidence";
import {
  AdvancedTable,
  SimpleTable,
  type TableColumn,
} from "../../components/studio/data-table";
import {
  Badge,
  Button,
  EmptyState,
  Evidence,
} from "../../components/studio/primitives";
import {
  InspectionWorkspace,
  WorkspaceDisclosure,
} from "../../components/studio/workspace";
import { SegmentedControl } from "../../components/affectui/controls";
import "./baseline-comparison.css";

type RecordValue = Record<string, unknown>;
const object = (value: unknown): RecordValue =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as RecordValue)
    : {};
const objects = (value: unknown): RecordValue[] =>
  Array.isArray(value) ? value.map(object) : [];
// Preserve decimal strings and typed quantities exactly; display never rescales or scores them.
const text = (value: unknown): string =>
  value === undefined || value === null
    ? "Not recorded"
    : typeof value === "string"
      ? value
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
const label = (key: string) => key.replaceAll("_", " ");
const scoreStatus = (value: unknown): "Pass" | "Partial" | "Fail" => {
  if (typeof value === "number")
    return Number.isFinite(value) && value >= 0
      ? value === 0
        ? "Pass"
        : "Fail"
      : "Partial";
  if (
    typeof value !== "string" ||
    !/^[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/.test(value)
  )
    return "Partial";
  return /^[+]?(?:0+(?:\.0*)?|\.0+)(?:[eE][+-]?\d+)?$/.test(value)
    ? "Pass"
    : "Fail";
};
type CaseRow = {
  id: string;
  record: RecordValue;
  score: RecordValue;
  check: RecordValue;
  status: "Pass" | "Partial" | "Fail";
};

function Details({ value }: { value: unknown }) {
  const entries = Object.entries(object(value));
  return entries.length ? (
    <dl className="baseline-details">
      {entries.map(([key, item]) => (
        <div key={key}>
          <dt className="text-caption-1-semibold text-text-secondary">
            {label(key)}
          </dt>
          <dd className="text-body-2-regular">
            {item !== null &&
            typeof item === "object" &&
            !Array.isArray(item) ? (
              <Details value={item} />
            ) : (
              text(item)
            )}
          </dd>
        </div>
      ))}
    </dl>
  ) : (
    <span>{text(value)}</span>
  );
}

/** Load the retained independent report, not the run's training-update records. */
export function RetainedBaselineComparison({
  artifacts,
  ...props
}: {
  artifacts: readonly RetainedArtifact[];
  comparison: unknown;
  records: readonly unknown[];
  runId: string;
  scopeKey: string;
  execution?: string;
}) {
  const artifact = artifacts.find(
    (item) => item.role === "independent_evaluation",
  );
  const identity = JSON.stringify([
    props.scopeKey,
    props.runId,
    artifact?.id,
    artifact?.sha256,
  ]);
  return <RetainedReport key={identity} {...props} artifact={artifact} />;
}
function RetainedReport({
  artifact,
  ...props
}: {
  artifact?: RetainedArtifact;
  comparison: unknown;
  records: readonly unknown[];
  runId: string;
  scopeKey: string;
  execution?: string;
}) {
  const { api } = useStudio();
  const [state, setState] = useState<{ report?: RecordValue; error?: string }>(
    {},
  );
  useEffect(() => {
    if (!artifact) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const blob = await api.download(
          `/api/lab/artifacts/${encodeURIComponent(artifact.id)}`,
          controller.signal,
        );
        const bytes = await blob.arrayBuffer();
        if (controller.signal.aborted) return;
        if (artifact.sha256) {
          if (
            !/^[a-f\d]{64}$/i.test(artifact.sha256) ||
            !globalThis.crypto?.subtle
          )
            throw new Error(
              "Retained report checksum cannot be verified in this environment.",
            );
          const digest = await crypto.subtle.digest("SHA-256", bytes);
          const actual = Array.from(new Uint8Array(digest), (value) =>
            value.toString(16).padStart(2, "0"),
          ).join("");
          if (actual !== artifact.sha256.toLowerCase())
            throw new Error("Retained report checksum mismatch.");
        }
        const report = object(JSON.parse(new TextDecoder().decode(bytes)));
        const control = report.format === "tinystudio.control-evaluation.v1";
        const generation =
          report.format === "tinystudio.generation-evaluation.v1";
        if (
          (!control && !generation) ||
          !Array.isArray(report.records) ||
          report.records.some(
            (row) =>
              row === null || typeof row !== "object" || Array.isArray(row),
          )
        )
          throw new Error(
            "Retained evaluation report has an unsupported or invalid record envelope.",
          );
        if (!controller.signal.aborted) setState({ report });
      } catch (error) {
        if (!controller.signal.aborted)
          setState({
            error:
              error instanceof Error
                ? error.message
                : "Retained evaluation report could not be loaded.",
          });
      }
    })();
    return () => controller.abort();
  }, [api, artifact?.id, artifact?.sha256]);
  if (!artifact)
    return (
      <BaselineComparison
        {...props}
        records={
          object(props.comparison).format === "tinystudio.control-evaluation.v1"
            ? []
            : props.records
        }
      />
    );
  if (state.error)
    return <div role="alert">Evaluation report unavailable: {state.error}</div>;
  if (!state.report)
    return <p role="status">Loading retained independent evaluation report…</p>;
  const report = state.report;
  const control = report.format === "tinystudio.control-evaluation.v1";
  const records = objects(report.records).filter((row) =>
    control
      ? ["policy", "baseline"].includes(String(row.arm)) &&
        row.source === "heldout_environment" &&
        row.episode !== undefined &&
        row.step !== undefined
      : row.source_id !== undefined &&
        row.episode_id !== undefined &&
        row.prediction !== undefined,
  );
  return (
    <>
      <p className="text-caption-1-regular text-text-secondary">
        Retained report: {artifact.name} ·{" "}
        {artifact.sha256 ? "SHA-256 verified" : "Checksum not recorded"} ·{" "}
        {records.length} evaluation records
      </p>
      <BaselineComparison
        {...props}
        comparison={control ? report : (report.comparison ?? props.comparison)}
        records={records}
      />
    </>
  );
}

/** Scope changes remount the feature-owned filter and selected evidence together. */
export function BaselineComparison(props: {
  comparison: unknown;
  records: readonly unknown[];
  runId: string;
  scopeKey: string;
  execution?: string;
}) {
  return (
    <ComparisonWorkspace
      key={JSON.stringify([props.scopeKey, props.runId])}
      {...props}
    />
  );
}
function ComparisonWorkspace({
  comparison,
  records,
  runId,
  scopeKey,
  execution,
}: {
  comparison: unknown;
  records: readonly unknown[];
  runId: string;
  scopeKey: string;
  execution?: string;
}) {
  const report = object(comparison),
    metrics = object(report.metrics),
    qualification = object(report.qualification);
  const control = report.format === "tinystudio.control-evaluation.v1";
  const checks = objects(report.checks);
  const complete = control
    ? qualification.comparison_complete === true
    : report.passed === true &&
      Object.keys(object(metrics.aggregate)).length > 0;
  const [filter, setFilter] = useState("All");
  const [selectedId, setSelectedId] = useState<string>();
  const rows = useMemo<CaseRow[]>(
    () =>
      records.map((value, index) => {
        const record = object(value);
        const score =
          objects(metrics.per_example).find(
            (item) =>
              item.source_id === record.source_id &&
              item.episode_id === record.episode_id,
          ) ?? {};
        const check =
          checks.find(
            (item) =>
              item.id === `pair:${record.source_id}:${record.episode_id}`,
          ) ?? {};
        const error =
          record.arm === "without_retrieval" || record.arm === "baseline"
            ? score.baseline_absolute_error
            : score.candidate_absolute_error;
        return {
          id: String(index),
          record,
          score,
          check,
          status: check.passed === false ? "Fail" : scoreStatus(error),
        };
      }),
    [records, report],
  );
  const selected = rows.find((row) => row.id === selectedId);
  const visible = rows.filter(
    (row) => filter === "All" || row.status === filter,
  );
  const columns: TableColumn<CaseRow>[] = control
    ? [
        {
          id: "episode",
          label: "Episode",
          value: (row) => text(row.record.episode),
        },
        { id: "step", label: "Step", value: (row) => text(row.record.step) },
        { id: "arm", label: "Arm", value: (row) => text(row.record.arm) },
        {
          id: "error",
          label: "Tracking error",
          value: (row) => text(row.record.absolute_tracking_error),
        },
        {
          id: "reward",
          label: "Reward",
          value: (row) => text(row.record.reward),
        },
        {
          id: "constraint",
          label: "Constraint violation",
          value: (row) => text(row.record.constraint_violation),
        },
      ]
    : [
        {
          id: "source",
          label: "Input / source",
          value: (row) =>
            text(
              row.record.prompt ?? row.record.source_id ?? row.record.source,
            ),
        },
        {
          id: "episode",
          label: "Episode",
          value: (row) => text(row.record.episode_id),
        },
        { id: "arm", label: "Arm", value: (row) => text(row.record.arm) },
        {
          id: "expected",
          label: "Expected",
          value: (row) => text(row.record.expected),
        },
        {
          id: "prediction",
          label: "Output",
          value: (row) =>
            text(row.record.prediction ?? object(row.record.output).completion),
        },
        { id: "unit", label: "Unit", value: (row) => text(row.record.unit) },
        {
          id: "status",
          label: "Score",
          value: (row) => row.status,
          render: (row) => <Badge>{row.status}</Badge>,
        },
      ];
  if (!comparison)
    return (
      <EmptyState>
        Baseline comparison requires two compatible retained runs, a named
        baseline, aligned evaluation inputs, and reported metrics. No baseline
        comparison evidence is attached to this run.
      </EmptyState>
    );
  const aggregate = object(metrics.aggregate);
  const summary = control
    ? {
        "Paired episodes": metrics.paired_episodes,
        "Paired initial states": metrics.paired_initial_states,
        "Policy tracking error": object(metrics.policy)
          .mean_absolute_tracking_error,
        "Baseline tracking error": object(metrics.baseline)
          .mean_absolute_tracking_error,
      }
    : {
        "Matched examples": aggregate.examples,
        "Candidate MAE": aggregate.candidate_mae,
        "Baseline MAE": aggregate.baseline_mae,
        "Candidate minus baseline MAE": aggregate.candidate_minus_baseline_mae,
      };
  return (
    <section className="baseline-comparison" aria-label="Baseline comparison">
      <header className="baseline-heading">
        <div>
          <h2 className="text-headline-medium">
            {control ? "Policy and baseline" : "Matched evaluation results"}
          </h2>
          <p className="text-body-2-regular text-text-secondary">
            {complete
              ? "Comparable · matched evaluation evidence is complete."
              : "Incomplete or unqualified comparison · aggregate superiority is not established."}
          </p>
        </div>
        <Badge>
          {text(
            qualification.status ?? (complete ? "qualified" : "incomplete"),
          )}
        </Badge>
      </header>
      <p className="text-body-2-regular text-text-secondary">
        Execution:{" "}
        {text(
          execution ??
            qualification.execution ??
            (control ? qualification.status : undefined),
        )}
        . Execution completion and answer correctness are separate outcomes.
        {!control &&
          " Pass means reported absolute error is zero; Fail means a reported nonzero error or failed pair check; Partial means no valid per-case score is retained."}
      </p>
      <div className="metrics-strip">
        {Object.entries(summary).map(([name, value]) => (
          <div className="metric-cell" key={name}>
            <span className="text-caption-1-semibold text-text-secondary">
              {name}
            </span>
            <strong className="text-title-3-semibold">{text(value)}</strong>
          </div>
        ))}
      </div>
      <InspectionWorkspace
        label="Selected evaluation evidence"
        inspector={
          selected ? (
            <Evidence
              label={`Evaluation case ${selected.record.source_id ?? selected.record.episode ?? selected.id}`}
              data={{
                run_id: runId,
                record: selected.record,
                score: selected.score,
                check: selected.check,
              }}
            >
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setSelectedId(undefined)}
              >
                Close case
              </Button>
              <Details value={selected.record} />
              <Details
                value={{
                  reported_score: selected.score,
                  pair_check: selected.check,
                }}
              />
            </Evidence>
          ) : undefined
        }
      >
        <div className="baseline-table-frame">
          <AdvancedTable
            label={control ? "Control transitions" : "Evaluation cases"}
            rows={visible}
            columns={columns}
            getRowId={(row) => row.id}
            scopeKey={JSON.stringify([scopeKey, runId])}
            layout="fill"
            toolbarContent={
              !control ? (
                <SegmentedControl
                  aria-label="Evaluation result filter"
                  size="sm"
                  value={filter}
                  onChange={setFilter}
                  options={["All", "Pass", "Partial", "Fail"].map((value) => ({
                    value,
                    label: value,
                  }))}
                />
              ) : undefined
            }
            rowProps={(row) => ({
              tabIndex: 0,
              "aria-label": `Inspect ${text(row.record.source_id ?? row.record.episode ?? row.id)} ${text(row.record.arm)}`,
              "aria-selected": selectedId === row.id,
              onClick: () => setSelectedId(row.id),
              onKeyDown: (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setSelectedId(row.id);
                }
              },
            })}
            empty="No retained per-case records are attached. Inspect the reported metrics and qualification below."
          />
        </div>
      </InspectionWorkspace>
      <WorkspaceDisclosure
        title="Reported metrics and qualification"
        summary={`${checks.filter((check) => check.passed === false).length} failed checks`}
      >
        <Details value={{ metrics, qualification }} />
        {checks.length > 0 && (
          <SimpleTable<RecordValue & { rowId: string }>
            label="Comparison checks"
            rows={checks.map((check, index) => ({
              ...check,
              rowId: String(index),
            }))}
            getRowId={(row) => row.rowId}
            columns={[
              { id: "id", label: "Check", value: (row) => text(row.id) },
              {
                id: "result",
                label: "Result",
                value: (row) => (row.passed === true ? "Pass" : "Fail"),
              },
              { id: "code", label: "Reason", value: (row) => text(row.code) },
              {
                id: "details",
                label: "Details",
                value: (row) => text(row.details),
              },
            ]}
          />
        )}
      </WorkspaceDisclosure>
    </section>
  );
}
