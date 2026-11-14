import { scientificJson } from "../../api/scientific-json";
import type {
  AnalysisDefinitionRequest,
  StudyAnalysisStudy,
} from "./study-analysis-api";

export type AnalysisDraft = ReturnType<typeof createAnalysisDraft>;

/** Boundary values come from the retained study; scientific choices stay unselected. */
export function createAnalysisDraft(
  study: Pick<StudyAnalysisStudy, "revision" | "last_sequence">,
) {
  return {
    name: "",
    revision: String(study.revision),
    throughSeq: String(study.last_sequence),
    method: "",
    selectionMode: "",
    recordIds: "",
    excludeIds: "",
    kinds: "",
    confidence: "",
    alternative: "",
    conditionMode: "text",
    groupA: "",
    groupB: "",
    outcomePath: "",
    conditionPath: "",
    unitPath: "",
    measurementMode: "",
    measurementUnit: "",
    repeated: "",
    missing: "",
    incompletePairs: "",
    notes: "",
    assumptions: "",
    inputSources: "{}",
    customName: "",
    customVersion: "",
    customCodeArtifactId: "",
    customOptions: "{}",
    extraArtifactIds: "",
    execution: '{\n  "profile": "trusted_native"\n}',
  };
}

export function analysisLines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}
function uniqueLines(text: string, label: string) {
  const values = analysisLines(text);
  if (new Set(values).size !== values.length)
    throw new Error(`${label} must be unique.`);
  return values;
}
const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
function parsedObject(text: string, label: string): Record<string, unknown> {
  const value = scientificJson.parse(text);
  if (!isObject(value)) throw new Error(`${label} must be a JSON object.`);
  return value;
}
function required(text: string, label: string) {
  if (!text.trim()) throw new Error(`${label} is required.`);
  return text.trim();
}
function choice(value: string, values: string[], label: string) {
  if (!values.includes(value)) throw new Error(`Choose ${label}.`);
  return value;
}
function pointer(value: string, label: string) {
  if (!value.startsWith("/") || /~(?![01])/.test(value))
    throw new Error(`${label} must be a JSON Pointer beginning with /.`);
  return value;
}
function boundaryInteger(text: string, minimum: number, label: string) {
  if (!/^(0|[1-9]\d*)$/.test(text.trim()))
    throw new Error(`${label} must be an exact nonnegative integer.`);
  const value = scientificJson.parse(text.trim());
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < minimum
  )
    throw new Error(
      `${label} must be an exact integer of at least ${minimum}.`,
    );
  return value;
}
function conditionKey(value: unknown): string {
  const normalized = (item: unknown): unknown =>
    Array.isArray(item)
      ? item.map(normalized)
      : isObject(item)
        ? Object.fromEntries(
            Object.keys(item)
              .sort()
              .map((key) => [key, normalized(item[key])]),
          )
        : item;
  return scientificJson.stringify(normalized(value));
}
export function parseSnapshotBoundary(
  draft: Pick<AnalysisDraft, "revision" | "throughSeq">,
) {
  return {
    revision: boundaryInteger(draft.revision, 1, "Protocol revision"),
    through_seq: boundaryInteger(
      draft.throughSeq,
      0,
      "Last included ledger sequence",
    ),
  };
}

export function parseAnalysisDraft(
  draft: AnalysisDraft,
): Omit<AnalysisDefinitionRequest, "snapshot_artifact_id"> {
  const name = required(draft.name, "Analysis name");
  const method = choice(
    draft.method,
    ["means.welch", "means.paired", "custom.python"],
    "an analysis method",
  );
  const mode = choice(
    draft.selectionMode,
    ["all", "ids"],
    "which records to include",
  );
  const parameters: Record<string, unknown> = {
    method,
    selection: {
      mode,
      record_ids:
        mode === "ids" ? uniqueLines(draft.recordIds, "Record IDs") : [],
      exclude_ids: uniqueLines(draft.excludeIds, "Excluded record IDs"),
      kinds: uniqueLines(draft.kinds, "Record kinds"),
    },
  };
  if (draft.notes.trim()) parameters.notes = draft.notes;
  if (draft.assumptions.trim())
    parameters.assumptions = analysisLines(draft.assumptions);
  const sources = parsedObject(draft.inputSources, "Input sources");
  if (
    Object.entries(sources).some(
      ([key, value]) =>
        !key.trim() || typeof value !== "string" || !value.trim(),
    )
  )
    throw new Error(
      "Input sources must map field names to nonempty source notes.",
    );
  if (Object.keys(sources).length) parameters.input_sources = sources;
  const execution = parsedObject(draft.execution, "Execution settings");
  required(
    typeof execution.profile === "string" ? execution.profile : "",
    "Execution profile",
  );
  if (method === "custom.python") {
    parameters.custom_method = {
      name: required(draft.customName, "Custom method name"),
      version: required(draft.customVersion, "Custom method version"),
    };
    parameters.options = parsedObject(
      draft.customOptions,
      "Custom method options",
    );
    return {
      name,
      parameters,
      execution,
      custom_code_artifact_id: required(
        draft.customCodeArtifactId,
        "Python source-code artifact",
      ),
      extra_artifact_ids: uniqueLines(
        draft.extraArtifactIds,
        "Additional artifact IDs",
      ),
    };
  }
  required(draft.confidence, "Confidence probability");
  const confidence = scientificJson.parse(draft.confidence);
  if (
    typeof confidence !== "number" ||
    !Number.isFinite(confidence) ||
    confidence <= 0 ||
    confidence >= 1
  )
    throw new Error("Confidence probability must be between zero and one.");
  const groups =
    draft.conditionMode === "json"
      ? [scientificJson.parse(draft.groupA), scientificJson.parse(draft.groupB)]
      : [
          required(draft.groupA, "Condition A value") && draft.groupA,
          required(draft.groupB, "Condition B value") && draft.groupB,
        ];
  // Compare object content without changing authored numerical values.
  if (conditionKey(groups[0]) === conditionKey(groups[1]))
    throw new Error("Choose two distinct condition values.");
  choice(
    draft.measurementMode,
    ["named", "dimensionless"],
    "an outcome measurement unit or dimensionless values",
  );
  Object.assign(parameters, {
    confidence,
    alternative: choice(
      draft.alternative,
      ["two-sided", "less", "greater"],
      "an interval alternative",
    ),
    groups,
    outcome_path: pointer(draft.outcomePath, "Outcome path"),
    condition_path: pointer(draft.conditionPath, "Condition path"),
    unit_path: pointer(draft.unitPath, "Independent-unit path"),
    measurement_unit:
      draft.measurementMode === "dimensionless"
        ? ""
        : required(draft.measurementUnit, "Outcome measurement unit"),
    repeated: choice(
      draft.repeated,
      ["error", "mean", "median", "first", "last"],
      "a repeated-measurement policy",
    ),
    missing: choice(draft.missing, ["error", "omit"], "a missing-value policy"),
  });
  if (method === "means.paired")
    parameters.incomplete_pairs = choice(
      draft.incompletePairs,
      ["error", "omit"],
      "an incomplete-pair policy",
    );
  return { name, parameters, execution };
}
