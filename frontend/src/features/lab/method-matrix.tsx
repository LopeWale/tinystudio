import { useMemo, useState } from "react";
import { useResource } from "../../app/StudioContext";
import { Input } from "../../components/boardui/base/input/input";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
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
import { LabChoice, type LabInput } from "./inputs";
import type { ExperimentDefinition } from "./definition";
import "./method-matrix.css";

type Row = Record<string, unknown>;
const obj = (value: unknown): Row =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Row)
    : {};
const rows = (value: unknown): Row[] =>
  Array.isArray(value) ? value.map(obj) : [];
const show = (value: unknown): string =>
  value === undefined || value === null
    ? "Not recorded"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
/** Exact companions preserve accepted Python integers across JSON number decoding. */
export function exactExpected(record: Row): unknown {
  const companion = record.expected_display;
  if (typeof companion === "string") return companion;
  if (
    companion !== null &&
    typeof companion === "object" &&
    typeof obj(companion).value === "string"
  )
    return companion;
  const value = record.expected;
  const scalar =
    value !== null && typeof value === "object" ? obj(value).value : value;
  if (
    typeof scalar === "number" &&
    (!Number.isFinite(scalar) ||
      (Number.isInteger(scalar) && !Number.isSafeInteger(scalar)))
  )
    return "Exact target unavailable in this report; inspect the original retained artifact.";
  return value;
}
export function unsafeExpectedWithoutDisplay(record: Row): boolean {
  const expected = record.expected;
  const scalar =
    expected !== null && typeof expected === "object"
      ? obj(expected).value
      : expected;
  const companion = record.expected_display;
  const exact =
    typeof companion === "string" ||
    (companion !== null &&
      typeof companion === "object" &&
      typeof obj(companion).value === "string");
  return (
    typeof scalar === "number" &&
    (!Number.isFinite(scalar) ||
      (Number.isInteger(scalar) && !Number.isSafeInteger(scalar))) &&
    !exact
  );
}
const displayRecord = (record: Row | undefined) =>
  record ? { ...record, expected: exactExpected(record) } : undefined;

const sameCase = (a: Row, b: Row) =>
  a.source_id === b.source_id && a.episode_id === b.episode_id;
const caseKey = (row: Row) => JSON.stringify([row.source_id, row.episode_id]);
function Fields({ value }: { value: unknown }) {
  return (
    <dl className="method-matrix-details">
      {Object.entries(obj(value)).map(([key, item]) => (
        <div key={key}>
          <dt className="text-caption-1-semibold text-text-secondary">
            {key.replaceAll("_", " ")}
          </dt>
          <dd className="text-body-2-regular">
            {item !== null &&
            typeof item === "object" &&
            !Array.isArray(item) ? (
              <Fields value={item} />
            ) : (
              show(item)
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
type Method = Row & { id: string; label: string; kind: string };
/** Parameter ownership stays in the authoring form; bindings remain canonical inputs. */
export function MethodMatrixEditor({
  definition,
  onChange,
}: {
  definition: ExperimentDefinition;
  onChange: (parameters: Record<string, unknown>) => void;
}) {
  const inputs = useResource<LabInput[]>("/api/lab/inputs");
  const methods = rows(definition.parameters.methods) as Method[];
  const baseline = String(definition.parameters.baseline_method ?? "");
  const [selectedId, setSelectedId] = useState<string>();
  const selected = methods.find((method) => method.id === selectedId);
  const [error, setError] = useState("");
  const update = (next: Method[]) =>
    onChange({ ...definition.parameters, operation: "matrix", methods: next });
  const edit = (patch: Row) =>
    selected &&
    update(
      methods.map((method) =>
        method.id === selected.id ? { ...method, ...patch } : method,
      ),
    );
  const add = (kind: "model" | "affine") => {
    if (methods.length >= 16) return;
    let ordinal = 1;
    while (methods.some((method) => method.id === `${kind}-${ordinal}`))
      ordinal++;
    const id = `${kind}-${ordinal}`;
    update([
      ...methods,
      kind === "model"
        ? {
            id,
            label: `Model ${ordinal}`,
            kind,
            artifact_id: "",
            retrieval: false,
          }
        : {
            id,
            label: `Conventional ${ordinal}`,
            kind,
            coefficients: {},
            intercept: "0",
          },
    ]);
    setSelectedId(id);
  };
  const modelOptions = definition.inputs
    .filter(
      (binding) => binding.role === "model" || binding.role === "checkpoint",
    )
    .map((binding) => ({
      id: binding.artifact_id,
      name:
        inputs.data?.find((input) => input.id === binding.artifact_id)?.name ??
        binding.artifact_id,
    }));
  const features = Object.entries(obj(selected?.coefficients));
  const editor = selected ? (
    <div className="method-matrix-form">
      <Input
        label="Method label"
        size="small"
        value={selected.label}
        maxLength={120}
        onChange={(label) => edit({ label })}
      />
      <p className="text-caption-1-regular text-text-secondary">
        Method ID: {selected.id}
      </p>
      {selected.kind === "model" ? (
        <>
          <LabChoice
            label="Attached model or checkpoint"
            value={String(selected.artifact_id ?? "")}
            options={modelOptions}
            onChange={(artifact_id) => edit({ artifact_id })}
            placeholder="Attach a model in Inputs"
          />
          {inputs.loading && <p role="status">Loading artifact names…</p>}
          {inputs.error && (
            <p role="status">
              Artifact names unavailable; retained IDs remain selectable.
            </p>
          )}
          <Checkbox
            size="sm"
            isSelected={selected.retrieval === true}
            onChange={(retrieval) => edit({ retrieval })}
          >
            Include retrieval evidence
          </Checkbox>
        </>
      ) : (
        <>
          <p className="text-body-2-regular text-text-secondary">
            Author the formula in raw scalar feature coordinates. Its output
            uses the case’s declared unit. Coefficients are not fitted and units
            are not automatically converted.
          </p>
          <Input
            label="Intercept"
            size="small"
            value={show(selected.intercept)}
            onChange={(intercept) => edit({ intercept })}
          />
          <SimpleTable
            label="Affine coefficients"
            rows={features.map(([feature, coefficient], index) => ({
              feature,
              coefficient,
              index,
            }))}
            getRowId={(row) => String(row.index)}
            columns={[
              {
                id: "feature",
                label: "Feature",
                value: (row) => row.feature,
                render: (row) => (
                  <Input
                    aria-label={`Feature ${row.index + 1} name`}
                    maxLength={120}
                    size="small"
                    value={row.feature}
                    onChange={(name) => {
                      if (
                        name !== row.feature &&
                        Object.hasOwn(obj(selected.coefficients), name)
                      ) {
                        setError("Feature names must be unique.");
                        return;
                      }
                      setError("");
                      edit({
                        coefficients: Object.fromEntries(
                          features.map(([key, value], index) => [
                            index === row.index ? name : key,
                            value,
                          ]),
                        ),
                      });
                    }}
                  />
                ),
              },
              {
                id: "coefficient",
                label: "Coefficient",
                value: (row) => show(row.coefficient),
                render: (row) => (
                  <Input
                    aria-label={`Feature ${row.index + 1} coefficient`}
                    size="small"
                    value={show(row.coefficient)}
                    onChange={(coefficient) =>
                      edit({
                        coefficients: {
                          ...obj(selected.coefficients),
                          [row.feature]: coefficient,
                        },
                      })
                    }
                  />
                ),
              },
              {
                id: "remove",
                label: "Actions",
                value: () => "",
                render: (row) => (
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove feature ${row.feature}`}
                    onClick={() =>
                      edit({
                        coefficients: Object.fromEntries(
                          features.filter(([key]) => key !== row.feature),
                        ),
                      })
                    }
                  >
                    Remove
                  </Button>
                ),
              },
            ]}
          />
          <Button
            size="sm"
            disabled={features.length >= 32}
            onClick={() => {
              let index = 1;
              while (
                Object.hasOwn(obj(selected.coefficients), `feature_${index}`)
              )
                index++;
              edit({
                coefficients: {
                  ...obj(selected.coefficients),
                  [`feature_${index}`]: "0",
                },
              });
            }}
          >
            Add feature
          </Button>
        </>
      )}
      <Button
        size="sm"
        variant="ghost"
        disabled={selected.id === baseline}
        onClick={() => {
          update(methods.filter((method) => method.id !== selected.id));
          setSelectedId(undefined);
        }}
      >
        Remove method
      </Button>
      {selected.id === baseline && (
        <p className="text-caption-1-regular text-text-secondary">
          Choose another baseline before removing this method.
        </p>
      )}
    </div>
  ) : (
    <EmptyState>Select a method to edit its settings.</EmptyState>
  );
  return (
    <section className="method-matrix" aria-label="Method matrix editor">
      <div className="method-matrix-toolbar">
        <Button
          size="sm"
          disabled={methods.length >= 16}
          onClick={() => add("model")}
        >
          Add model
        </Button>
        <Button
          size="sm"
          disabled={methods.length >= 16}
          onClick={() => add("affine")}
        >
          Add conventional formula
        </Button>
        <span className="text-caption-1-regular text-text-secondary">
          {methods.length} of 16 methods · requires at least 2
        </span>
      </div>
      <LabChoice
        label="Comparison baseline"
        value={baseline}
        options={methods.map((method) => ({
          id: method.id,
          name: method.label,
        }))}
        onChange={(baseline_method) =>
          onChange({
            ...definition.parameters,
            operation: "matrix",
            baseline_method,
          })
        }
        placeholder="Choose a baseline explicitly"
      />
      {error && <p role="alert">{error}</p>}
      <InspectionWorkspace label="Method settings" inspector={editor}>
        <div className="method-matrix-editor-table">
          <SimpleTable
            layout="fill"
            label="Authored methods"
            rows={methods}
            getRowId={(method) => method.id}
            rowProps={(method) => ({
              tabIndex: 0,
              "aria-label": `Edit ${method.label}`,
              "aria-selected": method.id === selectedId,
              onClick: () => setSelectedId(method.id),
              onKeyDown: (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setSelectedId(method.id);
                }
              },
            })}
            columns={[
              { id: "label", label: "Method", value: (method) => method.label },
              { id: "kind", label: "Kind", value: (method) => method.kind },
              {
                id: "baseline",
                label: "Baseline",
                value: (method) =>
                  method.id === baseline ? "Baseline" : "Candidate",
              },
            ]}
            empty="Add 2–16 methods and choose a named baseline. Attach model artifacts in Inputs; a shared model binding can serve multiple methods."
          />
        </div>
      </InspectionWorkspace>
    </section>
  );
}

/** Coverage is checked against retained identities; scientific scores stay backend-owned. */
export function matrixCoverageIssues(report: Row): string[] {
  const methods = rows(report.methods),
    records = rows(report.records);
  const ids = methods.map((method) => method.id);
  const issues: string[] = [];
  if (
    methods.length < 2 ||
    methods.length > 16 ||
    ids.some((id) => typeof id !== "string") ||
    new Set(ids).size !== ids.length
  )
    issues.push("Method identities are missing or duplicated.");
  if (!ids.includes(report.baseline_method))
    issues.push("The named baseline is missing.");
  if (
    !Number.isSafeInteger(report.case_count) ||
    Number(report.case_count) < 1 ||
    report.method_count !== methods.length ||
    report.expected_coverage !== Number(report.case_count) * methods.length ||
    records.length !== report.expected_coverage
  )
    issues.push("Reported coverage counts do not match the retained records.");
  const baselineRecords = records.filter(
    (row) => row.method_id === report.baseline_method,
  );
  const baselineKeys = new Set(baselineRecords.map(caseKey));
  for (const id of ids) {
    const matching = records.filter((row) => row.method_id === id);
    const keys = matching.map(caseKey);
    if (
      matching.length !== report.case_count ||
      new Set(keys).size !== keys.length ||
      keys.some((key) => !baselineKeys.has(key))
    )
      issues.push(`Incomplete, duplicate or unmatched cases for ${show(id)}.`);
    const evaluation = obj(obj(report.evaluations)[String(id)]);
    const comparison = obj(obj(report.comparisons)[String(id)]);
    const retainedReport = (value: Row) =>
      value.format === "tinystudio.evaluation.v1" &&
      typeof value.passed === "boolean" &&
      Array.isArray(value.checks);
    if (!retainedReport(evaluation))
      issues.push(`Evaluation report missing for ${show(id)}.`);
    if (id !== report.baseline_method && !retainedReport(comparison))
      issues.push(`Comparison report missing for ${show(id)}.`);
  }
  if (
    records.some(
      (record) =>
        !ids.includes(record.method_id) ||
        typeof record.source_id !== "string" ||
        typeof record.episode_id !== "string",
    )
  )
    issues.push("Records contain an unknown method or missing case identity.");
  if (
    Object.keys(obj(report.evaluations)).some((id) => !ids.includes(id)) ||
    Object.keys(obj(report.comparisons)).some(
      (id) => !ids.includes(id) || id === report.baseline_method,
    )
  )
    issues.push("Evaluation maps contain unexpected method identities.");
  if (records.some(unsafeExpectedWithoutDisplay))
    issues.push(
      "Exact target display is missing for an unsafe integer target.",
    );
  if (obj(report.qualification).execution !== "completed")
    issues.push("The retained executor did not report completed execution.");
  if (obj(report.qualification).coverage_complete !== true)
    issues.push("The executor did not qualify complete coverage.");
  return issues;
}

export function MatrixComparison({
  report,
  runId,
  scopeKey,
  runState,
}: {
  report: Row;
  runId: string;
  scopeKey: string;
  runState?: string;
}) {
  const key = JSON.stringify([scopeKey, runId]);
  return (
    <MatrixResults
      key={key}
      report={report}
      runId={runId}
      scopeKey={scopeKey}
      runState={runState}
    />
  );
}
function MatrixResults({
  report,
  runId,
  scopeKey,
  runState,
}: {
  report: Row;
  runId: string;
  scopeKey: string;
  runState?: string;
}) {
  const methods = rows(report.methods),
    records = rows(report.records),
    baseline = report.baseline_method;
  const [methodId, setMethodId] = useState<string>(() =>
    String(
      methods.find((method) => method.id !== baseline)?.id ?? baseline ?? "",
    ),
  );
  const [selectedIndex, setSelectedIndex] = useState<number>();
  const selectedMethod = methods.find((method) => method.id === methodId);
  const selectedRows = useMemo(
    () =>
      records
        .map((record, index) => ({ record, index }))
        .filter((item) => item.record.method_id === methodId),
    [report.records, methodId],
  );
  const issues = matrixCoverageIssues(report);
  const complete = issues.length === 0 && runState === "succeeded";
  const evaluation = obj(obj(report.evaluations)[methodId]);
  const comparison = obj(obj(report.comparisons)[methodId]);
  const aggregate = obj(obj(comparison.metrics).aggregate);
  const selected = selectedRows.find((item) => item.index === selectedIndex);
  const reference = (record: Row) => {
    const matching = records.filter(
      (row) => row.method_id === baseline && sameCase(row, record),
    );
    return matching.length === 1 ? matching[0] : undefined;
  };
  const status = (record: Row) => {
    const exact = rows(evaluation.examples).filter((example) =>
      sameCase(example, record),
    );
    if (exact.length === 1 && typeof exact[0].passed === "boolean")
      return exact[0].passed ? "Pass" : "Fail";
    // Older matrix evaluators retained only source_id. An absent episode may
    // qualify a result only when the source is unique within this method.
    const sourceRecords = records.filter(
      (item) =>
        item.method_id === methodId && item.source_id === record.source_id,
    );
    const legacy = rows(evaluation.examples).filter(
      (example) =>
        example.source_id === record.source_id &&
        example.episode_id === undefined,
    );
    if (
      sourceRecords.length === 1 &&
      legacy.length === 1 &&
      typeof legacy[0].passed === "boolean"
    )
      return legacy[0].passed ? "Pass" : "Fail";
    return "Unscored";
  };
  const columns: TableColumn<{ record: Row; index: number }>[] = [
    {
      id: "source",
      label: "Input / source",
      value: ({ record }) => show(record.source_id),
    },
    {
      id: "episode",
      label: "Episode",
      value: ({ record }) => show(record.episode_id),
    },
    {
      id: "expected",
      label: "Expected",
      value: ({ record }) => show(exactExpected(record)),
    },
    {
      id: "output",
      label: "Output",
      value: ({ record }) => show(record.prediction),
    },
    {
      id: "baseline",
      label: "Baseline output",
      value: ({ record }) => show(reference(record)?.prediction),
    },
    { id: "unit", label: "Unit", value: ({ record }) => show(record.unit) },
    {
      id: "pair",
      label: "Pair comparability",
      value: ({ record }) => {
        const check = rows(comparison.checks).find(
          (item) => item.id === `pair:${record.source_id}:${record.episode_id}`,
        );
        return check?.passed === false
          ? "Unqualified pair"
          : check?.passed === true
            ? "Comparable pair"
            : "Not recorded";
      },
    },
    {
      id: "score",
      label: "Validation",
      value: ({ record }) => status(record),
      render: ({ record }) => <Badge>{status(record)}</Badge>,
    },
  ];
  return (
    <section className="method-matrix" aria-label="Method matrix results">
      <header>
        <h2 className="text-headline-medium">Matched method comparison</h2>
        <p className="text-body-2-regular text-text-secondary">
          {complete
            ? "Complete matched coverage · originating run succeeded."
            : "Incomplete or unqualified matrix · completion and superiority are not established."}{" "}
          Baseline:{" "}
          {show(
            methods.find((method) => method.id === baseline)?.label ?? baseline,
          )}
          . Run state: {show(runState)}.
        </p>
      </header>
      {issues.length > 0 && <div role="alert">{issues.join(" ")}</div>}
      <SimpleTable<Row & { rowId: string }>
        label="Evaluated methods"
        rows={methods.map((method, index) => ({
          ...method,
          rowId: String(index),
        }))}
        getRowId={(method) => method.rowId}
        rowProps={(method) => ({
          tabIndex: 0,
          "aria-label": `Inspect method ${show(method.label)}`,
          "aria-selected": method.id === methodId,
          onClick: () => {
            setMethodId(String(method.id));
            setSelectedIndex(undefined);
          },
          onKeyDown: (event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              setMethodId(String(method.id));
              setSelectedIndex(undefined);
            }
          },
        })}
        columns={[
          {
            id: "method",
            label: "Method",
            value: (method) => show(method.label),
          },
          {
            id: "kind",
            label: "Retained kind",
            value: (method) => show(method.kind),
          },
          {
            id: "baseline",
            label: "Role",
            value: (method) =>
              method.id === baseline ? "Named baseline" : "Candidate",
          },
          {
            id: "evaluation",
            label: "Evaluation",
            value: (method) =>
              obj(obj(report.evaluations)[String(method.id)]).passed === true
                ? "Passed"
                : "Failed or unavailable",
          },
          {
            id: "count",
            label: "Records",
            value: (method) =>
              records.filter((record) => record.method_id === method.id).length,
          },
        ]}
      />
      <div className="metrics-strip">
        {Object.entries({
          "Candidate MAE": complete ? aggregate.candidate_mae : undefined,
          "Baseline MAE": complete ? aggregate.baseline_mae : undefined,
          "Candidate minus baseline MAE": complete
            ? aggregate.candidate_minus_baseline_mae
            : undefined,
        }).map(([label, value]) => (
          <div className="metric-cell" key={label}>
            <span className="text-caption-1-semibold text-text-secondary">
              {label}
            </span>
            <strong className="text-title-3-semibold">{show(value)}</strong>
          </div>
        ))}
      </div>
      <InspectionWorkspace
        label="Retained method and case evidence"
        inspector={
          <Evidence
            label={`Method matrix ${methodId}`}
            data={{
              run_id: runId,
              method: selectedMethod,
              record: displayRecord(selected?.record),
              baseline_record: selected
                ? displayRecord(reference(selected.record))
                : undefined,
              comparison,
              evaluation,
            }}
          >
            <Fields
              value={{
                method: selectedMethod,
                ...(selected
                  ? {
                      record: displayRecord(selected.record),
                      baseline_record: displayRecord(
                        reference(selected.record),
                      ),
                    }
                  : {}),
              }}
            />
          </Evidence>
        }
      >
        <div className="method-matrix-table">
          <AdvancedTable
            label="Method evaluation cases"
            rows={selectedRows}
            columns={columns}
            scopeKey={JSON.stringify([scopeKey, runId, methodId])}
            getRowId={(item) => String(item.index)}
            layout="fill"
            rowProps={(item) => ({
              tabIndex: 0,
              "aria-label": `Inspect case ${show(item.record.source_id)} ${show(item.record.episode_id)}`,
              "aria-selected": item.index === selectedIndex,
              onClick: () => setSelectedIndex(item.index),
              onKeyDown: (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setSelectedIndex(item.index);
                }
              },
            })}
            empty="No retained records for this method."
          />
        </div>
      </InspectionWorkspace>
      <WorkspaceDisclosure title="Matrix qualification and selected method checks">
        <Fields
          value={{
            qualification: report.qualification,
            evaluation,
            comparison,
          }}
        />
      </WorkspaceDisclosure>
    </section>
  );
}
