import { useEffect, useRef, useState } from "react";
import { useResource, useStudio } from "../../app/StudioContext";
import { Input } from "../../components/boardui/base/input/input";
import {
  Button,
  EmptyState,
  Evidence,
} from "../../components/studio/primitives";
import { SettingsSection } from "../../components/studio/settings-section";
import { SimpleTable } from "../../components/studio/data-table";
import { DraftField } from "./data-preparation";
import { LabChoice } from "./inputs";
import type { ExperimentDefinition } from "./definition";
import type { RetainedArtifact } from "./evidence";

type Value = Record<string, unknown>;
type Field = {
  name: string;
  label: string;
  type: "number" | "string" | "choice";
  required: boolean;
  choices?: { id: string; name: string }[];
  when?: { solve_for: string[] };
  description?: string;
};
type Method = {
  id: string;
  label: string;
  description: string;
  fields: Field[];
  assumptions: string[];
  references: string[];
};
type Catalog = { format: string; methods: Method[] };
const object = (value: unknown): Value =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Value)
    : {};
const isObject = (value: unknown): value is Value =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const isStrings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === "string");
const json = (value: unknown) => JSON.stringify(value, null, 2);
const applicable = (field: Field, goal: string) =>
  !field.when || field.when.solve_for.includes(goal);
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

function planningProblems(p: Value, fields: Field[]): string[] {
  const problems: string[] = [];
  for (const field of fields) {
    const value = p[field.name];
    if (
      field.required &&
      (value === undefined || value === null || value === "")
    )
      problems.push(`${field.label} is required.`);
    else if (value !== undefined) {
      if (field.type === "number" && !finite(value))
        problems.push(`${field.label} must be a finite number.`);
      if (
        field.type === "string" &&
        (typeof value !== "string" || !value.trim())
      )
        problems.push(`${field.label} must be nonempty text.`);
      if (
        field.type === "choice" &&
        !field.choices?.some((choice) => choice.id === value)
      )
        problems.push(`${field.label} must use a declared choice.`);
    }
  }
  for (const key of [
    "alpha",
    "power",
    "p1",
    "p2",
    "expected_proportion",
    "half_width",
  ])
    if (
      fields.some((field) => field.name === key) &&
      finite(p[key]) &&
      !(p[key] > 0 && p[key] < 1)
    )
      problems.push(`${key.replaceAll("_", " ")} must be between 0 and 1.`);
  for (const key of ["sd", "ratio"])
    if (
      fields.some((field) => field.name === key) &&
      finite(p[key]) &&
      p[key] <= 0
    )
      problems.push(`${key.replaceAll("_", " ")} must be positive.`);
  const minimumN = [
    "means.independent",
    "means.paired",
    "proportions.independent",
  ].includes(String(p.method))
    ? 2
    : 1;
  if (
    fields.some((field) => field.name === "n") &&
    finite(p.n) &&
    (!Number.isSafeInteger(p.n) || p.n < minimumN)
  )
    problems.push(
      `Analysis sample size must be a safe integer of at least ${minimumN}.`,
    );
  if (
    fields.some((field) => field.name === "power") &&
    finite(p.power) &&
    finite(p.alpha) &&
    p.power <= p.alpha
  )
    problems.push(
      "Target power must exceed alpha for nonzero-effect planning.",
    );
  const difference = finite(p.delta)
    ? p.delta
    : finite(p.p1) && finite(p.p2)
      ? p.p1 - p.p2
      : undefined;
  if (
    (p.solve_for ?? "sample_size") === "sample_size" &&
    p.method !== "proportion.precision" &&
    difference !== undefined
  ) {
    if (difference === 0)
      problems.push(
        "A nonzero difference is required for sample-size planning.",
      );
    if (
      (p.alternative === "greater" && difference < 0) ||
      (p.alternative === "less" && difference > 0)
    )
      problems.push(
        "The supplied difference points away from the one-sided alternative; target power is unattainable.",
      );
  }
  if (finite(p.attrition) && !(p.attrition >= 0 && p.attrition < 1))
    problems.push("Expected attrition must be at least 0 and below 1.");
  if (
    p.input_sources !== undefined &&
    (!isObject(p.input_sources) ||
      Object.entries(p.input_sources).some(
        ([key, value]) =>
          !key.trim() || typeof value !== "string" || !value.trim(),
      ))
  )
    problems.push(
      "Input sources must map nonempty field names to nonempty source notes.",
    );
  return problems;
}

/** Catalog-driven authoring keeps installed methods extensible and assumptions explicit. */
export function StatisticsPlanning({
  definition,
  onChange,
  onValidityChange,
}: {
  definition: ExperimentDefinition;
  onChange: (patch: Partial<ExperimentDefinition>) => void;
  onValidityChange?: (valid: boolean) => void;
}) {
  const catalog = useResource<Catalog>("/api/lab/statistics/methods");
  const p = definition.parameters;
  const methods =
    catalog.data?.format === "tinystudio.statistics-methods.v1" &&
    Array.isArray(catalog.data.methods)
      ? catalog.data.methods.filter((method) => Array.isArray(method.fields))
      : [];
  const method = methods.find((item) => item.id === p.method);
  const goal = String(p.solve_for ?? "sample_size");
  const fields =
    method?.fields.filter((field) => applicable(field, goal)) ?? [];
  const [invalid, setInvalid] = useState<Set<string>>(() => new Set());
  const [report] = useState(
    () => (key: string, valid: boolean) =>
      setInvalid((previous) => {
        if (previous.has(key) === !valid) return previous;
        const next = new Set(previous);
        if (valid) next.delete(key);
        else next.add(key);
        return next;
      }),
  );
  const problems = planningProblems(p, fields);
  if (
    p.sensitivity !== undefined &&
    (!Array.isArray(p.sensitivity) ||
      p.sensitivity.some(
        (value) =>
          !isObject(value) ||
          !Object.keys(value).length ||
          ["method", "solve_for", "sensitivity"].some((key) => key in value),
      ))
  )
    problems.push(
      "Sensitivity scenarios must be nonempty input override objects for the same method and calculation goal.",
    );
  if (Array.isArray(p.sensitivity)) {
    const allowed = new Set([
      ...fields.map((field) => field.name),
      "input_sources",
    ]);
    for (const [index, patch] of p.sensitivity.entries()) {
      if (!isObject(patch)) continue;
      if (Object.keys(patch).some((key) => !allowed.has(key)))
        problems.push(
          `Sensitivity ${index + 1} contains an input not declared for this method and goal.`,
        );
      problems.push(
        ...planningProblems({ ...p, ...patch }, fields).map(
          (problem) => `Sensitivity ${index + 1}: ${problem}`,
        ),
      );
    }
  }
  const valid =
    !!method &&
    !catalog.loading &&
    !catalog.error &&
    !invalid.size &&
    !problems.length;
  const callback = useRef(onValidityChange);
  callback.current = onValidityChange;
  useEffect(() => callback.current?.(valid), [valid]);
  const patch = (key: string, value: unknown) => {
    const parameters = { ...p };
    if (value === undefined) delete parameters[key];
    else parameters[key] = value;
    onChange({ parameters });
  };
  const choose = (selected: Method, selectedGoal: string) => {
    let parameters: Value = {
      ...p,
      method: selected.id,
      solve_for: selectedGoal,
    };
    const allFields = new Set(
      methods.flatMap((item) => item.fields.map((field) => field.name)),
    );
    const allowed = new Set(
      selected.fields
        .filter((field) => applicable(field, selectedGoal))
        .map((field) => field.name),
    );
    for (const key of allFields) if (!allowed.has(key)) delete parameters[key];
    const reset = new Set<string>();
    if ((p.method === "means.paired") !== (selected.id === "means.paired"))
      reset.add("sd");
    const analysisUnit = (method: unknown) =>
      method === "means.paired"
        ? "complete-pairs"
        : method === "proportion.precision"
          ? "bernoulli-observations"
          : method === "means.independent" ||
              method === "proportions.independent"
            ? "group-1-observations"
            : method;
    if (analysisUnit(p.method) !== analysisUnit(selected.id)) reset.add("n");
    const clearChangedMeaning = (record: Value) => {
      const next = { ...record };
      for (const key of reset) delete next[key];
      if (isObject(next.input_sources)) {
        const sources = { ...next.input_sources };
        for (const key of reset) delete sources[key];
        if (Object.keys(sources).length) next.input_sources = sources;
        else delete next.input_sources;
      }
      return next;
    };
    if (reset.size) {
      parameters = clearChangedMeaning(parameters);
      if (Array.isArray(parameters.sensitivity))
        parameters.sensitivity = parameters.sensitivity.map((scenario) =>
          isObject(scenario) ? clearChangedMeaning(scenario) : scenario,
        );
    }
    onChange({ parameters });
  };
  return (
    <SettingsSection
      title="Statistical planning"
      description="Choose a design and calculation. New experiments start with illustrative values; supply your own study assumptions. Planning estimates do not guarantee experimental success."
    >
      {catalog.loading && <p role="status">Loading statistical methods…</p>}
      {catalog.error && <p role="alert">{catalog.error.message}</p>}
      {!catalog.loading && !catalog.error && !methods.length && (
        <p role="alert">
          The statistical method catalog is unavailable or invalid.
        </p>
      )}
      {(catalog.error || (!catalog.loading && !methods.length)) && (
        <Button onClick={catalog.reload}>Retry statistical methods</Button>
      )}
      {!!methods.length && (
        <>
          <LabChoice
            label="Statistical method"
            value={String(p.method ?? "")}
            options={methods.map((item) => ({ id: item.id, name: item.label }))}
            onChange={(id) => {
              const next = methods.find((item) => item.id === id)!;
              const goals =
                next.fields.find((field) => field.name === "solve_for")
                  ?.choices ?? [];
              choose(
                next,
                goals.some((choice) => choice.id === goal)
                  ? goal
                  : (goals[0]?.id ?? "sample_size"),
              );
            }}
          />
          {!method && (
            <p role="alert">
              This retained method is not declared by the installed statistical
              catalog. Inspect its definition or select an available method.
            </p>
          )}
          {method && (
            <>
              <p className="text-body-2-regular text-text-secondary">
                {method.description}
              </p>
              <p className="text-caption-1-regular text-text-secondary">
                When a design changes the meaning of a standard deviation or
                sample count, enter it again. Its previous source note and
                sensitivity overrides are cleared too.
              </p>
              <div className="lab-fields">
                {fields.map((field) => (
                  <div key={`${method.id}:${goal}:${field.name}`}>
                    {field.type === "choice" ? (
                      <LabChoice
                        label={field.label}
                        value={String(p[field.name] ?? "")}
                        options={field.choices ?? []}
                        onChange={(value) =>
                          field.name === "solve_for"
                            ? choose(method, value)
                            : patch(field.name, value)
                        }
                      />
                    ) : field.type === "number" ? (
                      <DraftField
                        label={field.label}
                        value={p[field.name]}
                        onChange={(value) => patch(field.name, value)}
                        report={report}
                        fieldKey={`statistics:${field.name}`}
                        optional={!field.required}
                      />
                    ) : (
                      <Input
                        label={field.label}
                        value={String(p[field.name] ?? "")}
                        onChange={(value) => patch(field.name, value)}
                      />
                    )}
                    {field.description && (
                      <p className="text-caption-1-regular text-text-secondary">
                        {field.description}
                      </p>
                    )}
                  </div>
                ))}
              </div>
              {!!problems.length && <p role="alert">{problems.join(" ")}</p>}
              <div className="lab-fields">
                <DraftField
                  label="Input sources (JSON)"
                  kind="object"
                  value={p.input_sources ?? {}}
                  onChange={(value) => patch("input_sources", value)}
                  report={report}
                  fieldKey="statistics:input_sources"
                />
                <DraftField
                  label="Sensitivity scenarios (JSON)"
                  kind="array"
                  value={p.sensitivity ?? []}
                  onChange={(value) => patch("sensitivity", value)}
                  report={report}
                  fieldKey="statistics:sensitivity"
                />
              </div>
              <p className="text-caption-1-regular text-text-secondary">
                Each sensitivity scenario overrides declared inputs for the same
                method and goal. Describe pilot estimates or other sources
                rather than inventing missing inputs.
              </p>
              <details>
                <summary>Method assumptions and references</summary>
                <ul>
                  {method.assumptions.map((text) => (
                    <li key={text}>{text}</li>
                  ))}
                </ul>
                <ul>
                  {method.references.map((text) => (
                    <li key={text}>{text}</li>
                  ))}
                </ul>
              </details>
            </>
          )}
        </>
      )}
    </SettingsSection>
  );
}

type PlanRecord = {
  scenario_id: string;
  inputs: Value;
  result: {
    analysis_n: Record<string, number>;
    recruitment_n: Record<string, number>;
    total_analysis_n: number;
    total_recruitment_n: number;
    achieved_power: number | null;
    target_power: number | null;
    effect_size: number | null;
    expected_half_width: number | null;
    detectable_delta?: number | null;
    warnings: string[];
    rounding: string;
  };
};
type PlanReport = {
  format: "tinystudio.statistics-plan.v1";
  method: {
    id: string;
    label: string;
    version: string;
    assumptions: string[];
    references: string[];
  };
  request: Value;
  request_sha256: string;
  source_code_sha256: string;
  runtime: Value;
  records: PlanRecord[];
  qualification: { kind: "planning_estimate" };
};
/** Reject malformed retained numbers rather than presenting them as a planning result. */
export function validateStatisticsReport(
  value: unknown,
): asserts value is PlanReport {
  const report = object(value),
    method = object(report.method);
  const hash = (value: unknown) =>
    typeof value === "string" && /^[a-f\d]{64}$/i.test(value);
  const counts = (value: unknown) =>
    isObject(value) &&
    Object.keys(value).length > 0 &&
    Object.values(value).every(
      (n) => finite(n) && Number.isSafeInteger(n) && n > 0,
    );
  if (
    report.format !== "tinystudio.statistics-plan.v1" ||
    !isObject(report.request) ||
    !hash(report.request_sha256) ||
    !hash(report.source_code_sha256) ||
    !isObject(report.runtime) ||
    !["id", "label", "version"].every(
      (key) => typeof method[key] === "string" && !!method[key],
    ) ||
    !isStrings(method.assumptions) ||
    !isStrings(method.references) ||
    object(report.qualification).kind !== "planning_estimate" ||
    !Array.isArray(report.records) ||
    !report.records.length
  )
    throw new Error("Statistical plan has an invalid report envelope.");
  const ids = new Set<string>();
  for (const [index, value] of report.records.entries()) {
    const row = object(value),
      result = object(row.result);
    if (
      typeof row.scenario_id !== "string" ||
      ids.has(row.scenario_id) ||
      (index === 0 && row.scenario_id !== "primary") ||
      !isObject(row.inputs) ||
      !counts(result.analysis_n) ||
      !counts(result.recruitment_n)
    )
      throw new Error("Statistical plan has invalid scenario records.");
    ids.add(row.scenario_id);
    const analysis = result.analysis_n as Record<string, number>,
      recruitment = result.recruitment_n as Record<string, number>;
    if (
      Object.keys(analysis).sort().join() !==
        Object.keys(recruitment).sort().join() ||
      Object.keys(analysis).some((key) => recruitment[key] < analysis[key]) ||
      Object.values(analysis).reduce((a, b) => a + b, 0) !==
        result.total_analysis_n ||
      Object.values(recruitment).reduce((a, b) => a + b, 0) !==
        result.total_recruitment_n ||
      !isStrings(result.warnings) ||
      typeof result.rounding !== "string"
    )
      throw new Error("Statistical plan has invalid sample counts or totals.");
    for (const key of [
      "achieved_power",
      "target_power",
      "effect_size",
      "expected_half_width",
    ])
      if (result[key] !== null && !finite(result[key]))
        throw new Error("Statistical plan has invalid numeric results.");
    for (const key of ["achieved_power", "target_power"])
      if (finite(result[key]) && (result[key] < 0 || result[key] > 1))
        throw new Error("Statistical plan has invalid probability results.");
    if (
      result.detectable_delta !== undefined &&
      result.detectable_delta !== null &&
      !finite(result.detectable_delta)
    )
      throw new Error("Statistical plan has invalid detectable effect.");
  }
}

/** A changed run/project/artifact remounts its loader and synchronously masks old results. */
export function RetainedStatisticsPlanning({
  artifacts,
  runId,
  scopeKey,
}: {
  artifacts: readonly RetainedArtifact[];
  runId: string;
  scopeKey: string;
}) {
  const artifact = artifacts.find((item) => item.role === "statistical_plan");
  if (!artifact)
    return (
      <EmptyState>
        No retained statistical plan is available for this run yet.
      </EmptyState>
    );
  return (
    <RetainedPlan
      key={JSON.stringify([scopeKey, runId, artifact.id, artifact.sha256])}
      artifact={artifact}
      runId={runId}
    />
  );
}
function RetainedPlan({
  artifact,
  runId,
}: {
  artifact: RetainedArtifact;
  runId: string;
}) {
  const { api } = useStudio();
  const [state, setState] = useState<{ report?: PlanReport; error?: string }>(
    {},
  );
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({});
    void (async () => {
      try {
        if (
          !artifact.sha256 ||
          !/^[a-f\d]{64}$/i.test(artifact.sha256) ||
          !globalThis.crypto?.subtle
        )
          throw new Error(
            "Statistical plan requires a verifiable SHA-256 checksum.",
          );
        const blob = await api.download(
          `/api/lab/artifacts/${encodeURIComponent(artifact.id)}`,
          controller.signal,
        );
        const bytes = await blob.arrayBuffer();
        if (controller.signal.aborted) return;
        const digest = await crypto.subtle.digest("SHA-256", bytes);
        const actual = Array.from(new Uint8Array(digest), (byte) =>
          byte.toString(16).padStart(2, "0"),
        ).join("");
        if (actual !== artifact.sha256.toLowerCase())
          throw new Error("Statistical plan checksum mismatch.");
        const report: unknown = JSON.parse(new TextDecoder().decode(bytes));
        validateStatisticsReport(report);
        if (!controller.signal.aborted) setState({ report });
      } catch (error) {
        if (!controller.signal.aborted)
          setState({
            error:
              error instanceof Error
                ? error.message
                : "Statistical plan could not be loaded.",
          });
      }
    })();
    return () => controller.abort();
  }, [api, artifact.id, artifact.sha256, retry]);
  if (state.error)
    return (
      <>
        <p role="alert">Statistical plan unavailable: {state.error}</p>
        <Button onClick={() => setRetry((value) => value + 1)}>
          Retry statistical plan
        </Button>
      </>
    );
  if (!state.report)
    return <p role="status">Loading retained statistical plan…</p>;
  const report = state.report;
  return (
    <Evidence
      label="Statistical planning calculation"
      data={{ run_id: runId, artifact, report }}
    >
      <section
        aria-label="Statistical planning report"
        className="flex flex-col gap-4"
      >
        <p className="text-caption-1-regular text-text-secondary">
          Retained report: {artifact.name} · SHA-256 verified
        </p>
        <h3 className="text-title-3-semibold">{report.method.label}</h3>
        <p className="text-body-2-regular text-text-secondary">
          Planning estimates under the recorded assumptions. These are not
          observed experimental outcomes or guarantees of success.
        </p>
        <SimpleTable<PlanRecord>
          label="Statistical planning scenarios"
          rows={report.records}
          getRowId={(row) => row.scenario_id}
          scopeKey={runId}
          columns={[
            {
              id: "scenario",
              label: "Scenario",
              value: (row) => row.scenario_id,
            },
            {
              id: "analysis",
              label: "Analysis units",
              value: (row) => row.result.total_analysis_n,
            },
            {
              id: "recruitment",
              label: "Recruitment units",
              value: (row) => row.result.total_recruitment_n,
            },
            {
              id: "power",
              label: "Achieved power",
              value: (row) => row.result.achieved_power ?? "Not applicable",
            },
            {
              id: "effect",
              label: "Effect size",
              value: (row) => row.result.effect_size ?? "Not applicable",
            },
            {
              id: "precision",
              label: "Expected half-width",
              value: (row) =>
                row.result.expected_half_width ?? "Not applicable",
            },
          ]}
        />
        <div>
          <h4 className="text-headline-medium">Assumptions</h4>
          <ul>
            {report.method.assumptions.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        </div>
        {report.records.map((row) => (
          <details key={row.scenario_id}>
            <summary>
              {row.scenario_id} · inputs, group counts and rounding
            </summary>
            <p>{row.result.rounding}</p>
            {row.result.warnings.map((text, index) => (
              <p key={index}>{text}</p>
            ))}
            <pre>{json(row)}</pre>
          </details>
        ))}
        <details>
          <summary>Calculation recipe and input sources</summary>
          <pre>
            {json({
              request: report.request,
              request_sha256: report.request_sha256,
              method: report.method,
              runtime: report.runtime,
              source_code_sha256: report.source_code_sha256,
              qualification: report.qualification,
            })}
          </pre>
        </details>
      </section>
    </Evidence>
  );
}
