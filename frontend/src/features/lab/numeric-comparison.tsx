import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import {
  AdvancedTable,
  type TableColumn,
} from "../../components/studio/data-table";
import { Badge, Button, Evidence } from "../../components/studio/primitives";
import {
  InspectionWorkspace,
  WorkspaceDisclosure,
} from "../../components/studio/workspace";
import { SegmentedControl } from "../../components/affectui/controls";
import { Chart } from "./chart";
import { LabChoice } from "./inputs";

type Scores = { mae: number; mse: number } | { accuracy: number };
type Case = {
  case_id: string;
  source_record_ids: string[];
  context_source_record_ids: string[];
  episode_id?: unknown;
  prediction: number[] | string;
  target: number[] | string;
  baseline: number[] | string;
  raw_target_json: string;
};
export type NumericReport = {
  format: "tinystudio.numeric-evaluation.v1";
  task: "forecasting" | "regression" | "classification";
  partition: "validation";
  output_count: number;
  model_sha256: string;
  corpus_sha256: string;
  baseline: {
    kind: "persistence" | "training_mean" | "training_majority";
    label: string;
    fit_source_record_ids: string[];
    fit_case_count: number;
    constant?: number[] | string;
    tie_break?: string;
  };
  records: Case[];
  metrics: {
    cases: number;
    scalar_values: number;
    aggregation: string;
    model: Scores;
    baseline: Scores;
    per_output: {
      index: number;
      unit: null;
      model: Scores;
      baseline: Scores;
    }[];
  };
  qualification: {
    status: "completed" | "cancelled";
    comparison_complete: boolean;
    evaluation_valid: true;
    validation_only: true;
    scope: string;
  };
};
const fail = (path: string, reason: string): never => {
  throw new Error(`Numeric evaluation ${path}: ${reason}.`);
};
const obj = (value: unknown, path: string): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : fail(path, "must be an object");
const str = (value: unknown, path: string) =>
  typeof value === "string" && value.trim().length > 0
    ? value
    : fail(path, "must be a nonempty string");
const count = (value: unknown, path: string, min = 0) =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= min
    ? value
    : fail(path, `must be an integer of at least ${min}`);
const strings = (value: unknown, path: string, nonempty = false) => {
  if (!Array.isArray(value) || (nonempty && !value.length))
    fail(path, "must be an array of source record IDs");
  (value as unknown[]).forEach((entry, index) =>
    str(entry, `${path}[${index}]`),
  );
};
/** Validate the retained artifact at the display boundary; never coerce or drop corrupt cases. */
export function validateNumericReport(value: unknown): NumericReport {
  const r = obj(value, "report");
  if (r.format !== "tinystudio.numeric-evaluation.v1")
    fail("format", "unsupported or missing format");
  if (
    !["forecasting", "regression", "classification"].includes(r.task as string)
  )
    fail("task", "unsupported task");
  if (r.partition !== "validation") fail("partition", "must be validation");
  const classification = r.task === "classification";
  const width = count(r.output_count, "output_count", 1);
  if (classification && width !== 1)
    fail("output_count", "classification must have one output");
  for (const key of ["model_sha256", "corpus_sha256"])
    if (!/^[a-fA-F0-9]{64}$/.test(str(r[key], key)))
      fail(key, "must be a SHA-256 digest");
  const vector = (entry: unknown, path: string) => {
    if (classification) {
      if (typeof entry !== "string")
        fail(path, "must be a canonical string label");
      return;
    }
    if (
      !Array.isArray(entry) ||
      entry.length !== width ||
      !entry.every((n) => typeof n === "number" && Number.isFinite(n))
    )
      fail(path, `must contain ${width} finite numbers`);
  };
  const b = obj(r.baseline, "baseline");
  const expectedKind = classification
    ? "training_majority"
    : r.task === "forecasting"
      ? "persistence"
      : "training_mean";
  if (b.kind !== expectedKind)
    fail("baseline.kind", `must be ${expectedKind} for ${String(r.task)}`);
  str(b.label, "baseline.label");
  strings(b.fit_source_record_ids, "baseline.fit_source_record_ids");
  count(b.fit_case_count, "baseline.fit_case_count");
  if (
    b.kind !== "persistence" &&
    (!(b.fit_source_record_ids as unknown[]).length || !b.fit_case_count)
  )
    fail("baseline", "training baseline requires fit provenance");
  if (b.fit_case_count !== (b.fit_source_record_ids as unknown[]).length)
    fail("baseline.fit_case_count", "must match fit_source_record_ids count");
  if (b.kind === "persistence" && b.fit_case_count !== 0)
    fail("baseline", "persistence must have no training fit provenance");
  if (b.kind !== "persistence" || b.constant !== undefined)
    vector(b.constant, "baseline.constant");
  if (b.kind === "training_majority" || b.tie_break !== undefined)
    str(b.tie_break, "baseline.tie_break");
  if (!Array.isArray(r.records) || !r.records.length)
    fail("records", "must contain validation cases");
  const ids = new Set<string>();
  (r.records as unknown[]).forEach((entry, index) => {
    const path = `records[${index}]`,
      record = obj(entry, path),
      id = str(record.case_id, `${path}.case_id`);
    if (ids.has(id)) fail(`${path}.case_id`, "duplicate case identity");
    ids.add(id);
    strings(record.source_record_ids, `${path}.source_record_ids`, true);
    strings(
      record.context_source_record_ids,
      `${path}.context_source_record_ids`,
    );
    if (
      (record.source_record_ids as unknown[]).length !==
      (r.task === "forecasting" ? width : 1)
    )
      fail(
        `${path}.source_record_ids`,
        r.task === "forecasting"
          ? "must contain one source ID per forecast output"
          : "must contain exactly one source ID",
      );
    if (
      r.task === "forecasting"
        ? !(record.context_source_record_ids as unknown[]).length
        : (record.context_source_record_ids as unknown[]).length !== 0
    )
      fail(
        `${path}.context_source_record_ids`,
        r.task === "forecasting"
          ? "requires forecast context provenance"
          : "must be empty for fusion cases",
      );
    for (const key of ["prediction", "target", "baseline"])
      vector(record[key], `${path}.${key}`);
    if (!classification) {
      for (const key of ["prediction", "baseline"]) {
        if (
          !(record[key] as number[]).every((n, i) =>
            Number.isFinite(n - (record.target as number[])[i]),
          )
        )
          fail(`${path}.${key}`, "error against target must remain finite");
      }
    }
    str(record.raw_target_json, `${path}.raw_target_json`);
    try {
      JSON.parse(record.raw_target_json as string);
    } catch {
      fail(`${path}.raw_target_json`, "must preserve valid original JSON");
    }
  });
  const metrics = obj(r.metrics, "metrics");
  if (count(metrics.cases, "metrics.cases", 1) !== ids.size)
    fail("metrics.cases", "must match the retained case count");
  if (
    count(metrics.scalar_values, "metrics.scalar_values", 1) !==
    ids.size * width
  )
    fail("metrics.scalar_values", "must equal cases times output_count");
  str(metrics.aggregation, "metrics.aggregation");
  const scores = (value: unknown, path: string) => {
    const s = obj(value, path);
    for (const key of classification ? ["accuracy"] : ["mae", "mse"]) {
      const n = s[key];
      if (
        typeof n !== "number" ||
        !Number.isFinite(n) ||
        n < 0 ||
        (classification && n > 1)
      )
        fail(`${path}.${key}`, "must be a finite valid metric");
    }
  };
  scores(metrics.model, "metrics.model");
  scores(metrics.baseline, "metrics.baseline");
  if (
    !Array.isArray(metrics.per_output) ||
    metrics.per_output.length !== (classification ? 0 : width)
  )
    fail(
      "metrics.per_output",
      "must match output_count (empty for classification)",
    );
  (metrics.per_output as unknown[]).forEach((entry, index) => {
    const p = obj(entry, `metrics.per_output[${index}]`);
    if (p.index !== index || p.unit !== null)
      fail(
        `metrics.per_output[${index}]`,
        "requires ordered zero-based index and null unit",
      );
    scores(p.model, `metrics.per_output[${index}].model`);
    scores(p.baseline, `metrics.per_output[${index}].baseline`);
  });
  const q = obj(r.qualification, "qualification");
  if (
    !["completed", "cancelled"].includes(q.status as string) ||
    typeof q.comparison_complete !== "boolean" ||
    q.evaluation_valid !== true ||
    q.validation_only !== true
  )
    fail(
      "qualification",
      "requires valid validation-only evaluation qualification",
    );
  if (q.status === "cancelled" && q.comparison_complete)
    fail("qualification", "cancelled evaluation cannot be complete");
  str(q.scope, "qualification.scope");
  return value as NumericReport;
}
const exact = (value: unknown) => JSON.stringify(value);
const scoreText = (scores: Scores) =>
  "accuracy" in scores
    ? `Accuracy ${scores.accuracy}`
    : `MAE ${scores.mae} · MSE ${scores.mse}`;
function Fields({ values }: { values: Record<string, unknown> }) {
  return (
    <dl className="flex flex-col gap-3">
      {Object.entries(values).map(([name, value]) => (
        <div key={name}>
          <dt className="text-caption-1-semibold text-text-secondary">
            {name}
          </dt>
          <dd className="break-all whitespace-pre-wrap text-body-2-regular">
            {typeof value === "string" ? value : JSON.stringify(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
export function NumericComparison(props: {
  report: unknown;
  runId: string;
  scopeKey: string;
  runState?: string;
}) {
  const parsed = useMemo(() => {
    try {
      return { report: validateNumericReport(props.report) };
    } catch (cause) {
      return { error: cause instanceof Error ? cause.message : String(cause) };
    }
  }, [props.report]);
  if (!parsed.report)
    return (
      <p role="alert" className="text-body-2-regular text-text-error-primary">
        {parsed.error}
      </p>
    );
  return (
    <NumericWorkspace
      key={JSON.stringify([props.scopeKey, props.runId])}
      {...props}
      report={parsed.report}
    />
  );
}
function NumericWorkspace({
  report: r,
  runId,
  scopeKey,
  runState,
}: {
  report: NumericReport;
  runId: string;
  scopeKey: string;
  runState?: string;
}) {
  const [selectedId, setSelectedId] = useState<string>();
  const [view, setView] = useState("plot"),
    [component, setComponent] = useState("0"),
    [metric, setMetric] = useState("value");
  const selected = r.records.find((record) => record.case_id === selectedId),
    index = Number(component),
    classification = r.task === "classification";
  const complete =
    runState === "succeeded" &&
    r.qualification.status === "completed" &&
    r.qualification.comparison_complete;
  const option = useMemo<EChartsOption>(
    () => ({
      animation: false,
      grid: { left: 76, right: 24, top: 48, bottom: 90 },
      legend: { top: 0 },
      tooltip: { trigger: "item", renderMode: "richText" },
      xAxis: {
        type: "value",
        name: "Validation case ordinal (artifact order)",
        nameLocation: "middle",
        nameGap: 28,
        minInterval: 1,
      },
      yAxis: {
        type: "value",
        name:
          metric === "error"
            ? "Absolute error · unit not recorded"
            : "Value · unit not recorded",
        nameLocation: "middle",
        nameGap: 50,
      },
      dataZoom: [
        { id: "numeric-inside", type: "inside", filterMode: "none" },
        { id: "numeric-slider", type: "slider", bottom: 0, filterMode: "none" },
      ],
      series: (metric === "error"
        ? ["prediction", "baseline"]
        : ["target", "prediction", "baseline"]
      ).map((key) => ({
        name:
          key === "prediction"
            ? "Model"
            : key === "target"
              ? "Target"
              : r.baseline.label,
        type: "line",
        lineStyle: { opacity: 0 },
        showSymbol: true,
        data: r.records.map((record, ordinal) => ({
          caseId: record.case_id,
          name: record.case_id,
          symbolSize: record.case_id === selectedId ? 11 : 6,
          value: [
            ordinal + 1,
            metric === "error"
              ? Math.abs(
                  (record[key as "prediction"] as number[])[index] -
                    (record.target as number[])[index],
                )
              : (record[key as "prediction"] as number[])[index],
          ],
        })),
      })),
    }),
    [r, index, metric, selectedId],
  );
  const columns: TableColumn<Case>[] = [
    { id: "case", label: "Case", value: (row) => row.case_id },
    {
      id: "original",
      label: "Original target JSON",
      value: (row) => row.raw_target_json,
    },
    {
      id: "target",
      label: classification ? "Canonical target label" : "Target outputs",
      value: (row) => exact(row.target),
    },
    {
      id: "model",
      label: classification ? "Model label" : "Model outputs",
      value: (row) => exact(row.prediction),
    },
    {
      id: "baseline",
      label: classification ? "Baseline label" : "Baseline outputs",
      value: (row) => exact(row.baseline),
    },
  ];
  return (
    <section
      aria-label="Numeric baseline comparison"
      className="flex flex-col gap-4"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-headline-medium">Validation model and baseline</h2>
        <Badge>
          {complete
            ? "Complete"
            : r.qualification.status === "cancelled"
              ? "Cancelled · incomplete"
              : "Incomplete"}
        </Badge>
        <Badge>Validation only</Badge>
      </div>
      <p role="status" className="text-body-2-regular text-text-secondary">
        {r.records.length} validation cases · {r.metrics.scalar_values} scalar
        values. {r.metrics.aggregation}. {r.qualification.scope}
      </p>
      <div className="flex flex-wrap gap-4 text-body-2-regular">
        <span>Model: {scoreText(r.metrics.model)}</span>
        <span>
          {r.baseline.label}: {scoreText(r.metrics.baseline)}
        </span>
      </div>
      {!classification && (
        <div className="flex flex-wrap items-end gap-3">
          <SegmentedControl
            aria-label="Numeric comparison view"
            size="sm"
            value={view}
            onChange={setView}
            options={[
              { value: "plot", label: "Plot" },
              { value: "records", label: "Records" },
            ]}
          />
          <LabChoice
            label="Output component"
            value={component}
            onChange={setComponent}
            options={Array.from({ length: r.output_count }, (_, i) => ({
              id: String(i),
              name:
                r.task === "forecasting"
                  ? `Horizon ${i + 1}`
                  : `Output ${i + 1}`,
            }))}
          />
          {view === "plot" && (
            <LabChoice
              label="Numeric plot metric"
              value={metric}
              onChange={setMetric}
              options={[
                { id: "value", name: "Target, model and baseline" },
                { id: "error", name: "Absolute error" },
              ]}
            />
          )}
        </div>
      )}
      {!classification && (
        <p className="text-caption-1-medium text-text-secondary">
          {r.task === "forecasting"
            ? `Horizon ${index + 1}`
            : `Output ${index + 1}`}{" "}
          · model {scoreText(r.metrics.per_output[index].model)} · baseline{" "}
          {scoreText(r.metrics.per_output[index].baseline)}. Units not recorded.
        </p>
      )}
      <InspectionWorkspace
        label="Selected numeric evaluation evidence"
        inspector={
          selected ? (
            <Evidence
              label={`Validation case ${selected.case_id}`}
              data={{
                run_id: runId,
                case: selected,
                model_sha256: r.model_sha256,
                corpus_sha256: r.corpus_sha256,
                baseline_provenance: r.baseline,
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
                Validation case {selected.case_id}
              </h3>
              <Fields
                values={{
                  "Original target JSON": selected.raw_target_json,
                  [classification
                    ? "Canonical target label"
                    : "Target outputs"]: exact(selected.target),
                  "Model prediction": exact(selected.prediction),
                  "Baseline prediction": exact(selected.baseline),
                }}
              />
              <WorkspaceDisclosure
                title="Case and baseline provenance"
                summary="Source records and artifact identity"
              >
                <Fields
                  values={{
                    "Source records": selected.source_record_ids,
                    "Context records": selected.context_source_record_ids,
                    ...(selected.episode_id !== undefined
                      ? { Episode: selected.episode_id }
                      : {}),
                    "Model SHA-256": r.model_sha256,
                    "Corpus SHA-256": r.corpus_sha256,
                    Baseline: r.baseline.label,
                    "Fit case count": r.baseline.fit_case_count,
                    "Fit source records": r.baseline.fit_source_record_ids,
                  }}
                />
              </WorkspaceDisclosure>
            </Evidence>
          ) : undefined
        }
      >
        {classification || view === "records" ? (
          <div className="baseline-table-frame">
            <AdvancedTable
              label="Numeric validation cases"
              layout="fill"
              rows={r.records}
              columns={columns}
              getRowId={(row) => row.case_id}
              scopeKey={JSON.stringify([scopeKey, runId])}
              rowProps={(row) => ({
                tabIndex: 0,
                "aria-label": `Inspect validation case ${row.case_id}`,
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
        ) : (
          <div className="flex flex-col gap-2">
            <p className="text-caption-1-medium text-text-secondary">
              Unconnected validation cases in artifact order; ordinal is not
              time. Arrow keys, Home and End select case evidence.
            </p>
            <Chart
              key={`${component}:${metric}`}
              label="Numeric validation comparison chart"
              height={360}
              option={option}
              onPoint={(data) => {
                if (
                  data &&
                  typeof data === "object" &&
                  "caseId" in data &&
                  r.records.some((record) => record.case_id === data.caseId)
                )
                  setSelectedId(data.caseId as string);
              }}
              onKeyDown={(event) => {
                if (
                  ![
                    "ArrowLeft",
                    "ArrowRight",
                    "ArrowUp",
                    "ArrowDown",
                    "Home",
                    "End",
                  ].includes(event.key)
                )
                  return;
                event.preventDefault();
                const current = r.records.findIndex(
                  (record) => record.case_id === selectedId,
                );
                const next =
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? r.records.length - 1
                      : current < 0
                        ? 0
                        : Math.max(
                            0,
                            Math.min(
                              r.records.length - 1,
                              current +
                                (["ArrowLeft", "ArrowUp"].includes(event.key)
                                  ? -1
                                  : 1),
                            ),
                          );
                setSelectedId(r.records[next].case_id);
              }}
            />
          </div>
        )}
      </InspectionWorkspace>
      <WorkspaceDisclosure
        title="Reported metrics and qualification"
        summary="Retained validation evidence"
      >
        <pre className="whitespace-pre-wrap break-all text-caption-1-medium">
          {JSON.stringify(
            {
              metrics: r.metrics,
              qualification: r.qualification,
              baseline: r.baseline,
            },
            null,
            2,
          )}
        </pre>
      </WorkspaceDisclosure>
    </section>
  );
}
