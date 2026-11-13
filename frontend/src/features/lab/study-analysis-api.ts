import { withScientificJson, type ApiClient } from "../../api/client";
import { scientificJson } from "../../api/scientific-json";
import type { LabRun, SavedDefinition } from "./definition";
import type { RetainedArtifact } from "./evidence";
import { waitForOperation, type LabOperation } from "./operations";

const enc = encodeURIComponent;
export const studyAnalysisPaths = {
  methods: "/api/lab/statistics/analysis-methods",
  definitions: "/api/lab/statistics/analyses",
  inputs: "/api/lab/inputs",
  targets: "/api/lab/targets",
  preflight: "/api/lab/preflight",
  runs: "/api/lab/runs",
  run: (id: string) => `/api/lab/runs/${enc(id)}`,
  snapshot: (id: string) => `/api/lab/studies/${enc(id)}/analysis-inputs`,
  results: (id: string) => `/api/lab/studies/${enc(id)}/analysis-results`,
  artifact: (id: string) => `/api/lab/artifacts/${enc(id)}`,
};
export interface StudyAnalysisStudy {
  id: string;
  revision: number;
  last_sequence: number;
  study: { independent_unit?: string; name?: string };
}
/** Available retained history is independent of the protocol currently being viewed. */
export type StudyAnalysisHead = Pick<
  StudyAnalysisStudy,
  "revision" | "last_sequence"
>;
export interface AnalysisMethod {
  id: string;
  version: string;
  label: string;
  description: string;
  assumptions: string[];
  references: string[];
  dependencies: string[];
}
export interface AnalysisMethodCatalog {
  format: "tinystudio.analysis-methods.v1";
  methods: AnalysisMethod[];
}
export interface AnalysisSource extends Record<string, unknown> {
  study_id: string;
  revision: number;
  through_seq: number;
  protocol_digest: string;
  sha256?: string;
}
export interface AnalysisSnapshot {
  artifact: RetainedArtifact & { sha256: string };
  source: AnalysisSource;
}
export interface AnalysisDefinitionRequest {
  name: string;
  snapshot_artifact_id: string;
  parameters: Record<string, unknown>;
  execution: Record<string, unknown>;
  custom_code_artifact_id?: string;
  extra_artifact_ids?: string[];
}
export interface AnalysisCompatibility {
  compatible: boolean;
  checks: { code: string; passed: boolean; message: string }[];
  limitations?: string[];
  [key: string]: unknown;
}
export interface AnalysisDiagnostic {
  code: string;
  message: string;
  [key: string]: unknown;
}
export interface AnalysisInterval {
  confidence: number;
  lower: number | null;
  upper: number | null;
  lower_unbounded: boolean;
  upper_unbounded: boolean;
}
export interface AnalysisReport {
  format: "tinystudio.study-analysis.v1";
  source: AnalysisSource & { sha256: string };
  request: Record<string, unknown>;
  method: {
    id: string;
    version: string;
    label?: string;
    name?: string;
    assumptions: string[];
    references: string[];
    [key: string]: unknown;
  };
  result: {
    estimate?: number | null;
    standard_error?: number | null;
    degrees_of_freedom?: number | null;
    interval?: AnalysisInterval;
    counts: Record<string, number>;
    diagnostics?: AnalysisDiagnostic[];
    custom_output?: unknown;
    [key: string]: unknown;
  };
  selection: {
    counts: Record<string, number>;
    diagnostics: AnalysisDiagnostic[];
    [key: string]: unknown;
  };
  qualification: unknown;
  [key: string]: unknown;
}

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const hash = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f\d]{64}$/i.test(value);
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const text = (value: unknown): value is string =>
  typeof value === "string" && !!value.trim();
const strings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === "string");
const counts = (value: unknown): value is Record<string, number> =>
  object(value) &&
  Object.keys(value).length > 0 &&
  Object.values(value).every(
    (n) => finite(n) && Number.isSafeInteger(n) && n >= 0,
  );
const diagnostics = (value: unknown): value is AnalysisDiagnostic[] =>
  Array.isArray(value) &&
  value.every((item) => object(item) && text(item.code) && text(item.message));
const finiteJson = (value: unknown): boolean =>
  typeof value === "number"
    ? Number.isFinite(value)
    : Array.isArray(value)
      ? value.every(finiteJson)
      : object(value)
        ? Object.values(value).every(finiteJson)
        : value === null || ["string", "boolean"].includes(typeof value);
function validSource(value: unknown): value is AnalysisSource {
  return (
    object(value) &&
    text(value.study_id) &&
    finite(value.revision) &&
    Number.isSafeInteger(value.revision) &&
    value.revision >= 1 &&
    finite(value.through_seq) &&
    Number.isSafeInteger(value.through_seq) &&
    value.through_seq >= 0 &&
    hash(value.protocol_digest)
  );
}

export async function retainAnalysisSnapshot(
  api: ApiClient,
  studyId: string,
  boundary: { revision: number; through_seq: number },
  idempotencyKey: string,
  signal: AbortSignal,
  onState: (operation: LabOperation) => void,
): Promise<AnalysisSnapshot> {
  const operation = await api.post<LabOperation>(
    studyAnalysisPaths.snapshot(studyId),
    withScientificJson({ ...boundary, idempotency_key: idempotencyKey }),
    signal,
  );
  const finished = await waitForOperation(api, operation, signal, onState);
  const result = finished.result;
  if (
    !object(result) ||
    !object(result.artifact) ||
    !text(result.artifact.id) ||
    !text(result.artifact.name) ||
    !hash(result.artifact.sha256) ||
    !validSource(result.source) ||
    result.source.study_id !== studyId ||
    result.source.revision !== boundary.revision ||
    result.source.through_seq !== boundary.through_seq
  )
    throw new Error(
      "Snapshot operation returned an invalid study boundary or artifact.",
    );
  return result as unknown as AnalysisSnapshot;
}
export function saveStudyAnalysis(
  api: ApiClient,
  body: AnalysisDefinitionRequest,
  signal: AbortSignal,
) {
  return api.post<SavedDefinition>(
    studyAnalysisPaths.definitions,
    withScientificJson(body),
    signal,
  );
}
export function checkStudyAnalysis(
  api: ApiClient,
  saved: SavedDefinition,
  targetId: string,
  signal: AbortSignal,
) {
  return api.post<AnalysisCompatibility>(
    studyAnalysisPaths.preflight,
    withScientificJson({ definition: saved.definition, target_id: targetId }),
    signal,
  );
}
export async function submitStudyAnalysis(
  api: ApiClient,
  saved: SavedDefinition,
  targetId: string,
  idempotencyKey: string,
  signal: AbortSignal,
) {
  const run = await api.post<unknown>(
    studyAnalysisPaths.runs,
    withScientificJson({
      definition_id: saved.id,
      revision: saved.revision,
      target_id: targetId,
      idempotency_key: idempotencyKey,
    }),
    signal,
  );
  if (
    !object(run) ||
    !text(run.id) ||
    run.definition_id !== saved.id ||
    !text(run.state)
  )
    throw new Error(
      "Run admission returned an invalid or mismatched acknowledgement. Retry the same request.",
    );
  return run as unknown as LabRun;
}
export function appendStudyAnalysisResult(
  api: ApiClient,
  studyId: string,
  runId: string,
  idempotencyKey: string,
  notes: string,
  signal: AbortSignal,
) {
  return api.post<{ id: string; seq: number }>(
    studyAnalysisPaths.results(studyId),
    withScientificJson({
      run_id: runId,
      idempotency_key: idempotencyKey,
      ...(notes.trim() ? { notes } : {}),
    }),
    signal,
  );
}

/** Undefined intervals remain undefined; custom outputs carry their own researcher contract. */
export function validateAnalysisReport(
  value: unknown,
): asserts value is AnalysisReport {
  if (
    !object(value) ||
    value.format !== "tinystudio.study-analysis.v1" ||
    !validSource(value.source) ||
    !hash(value.source.sha256) ||
    !object(value.request) ||
    !object(value.method) ||
    !text(value.method.id) ||
    value.request.method !== value.method.id ||
    !text(value.method.version) ||
    !(text(value.method.label) || text(value.method.name)) ||
    !strings(value.method.assumptions) ||
    !strings(value.method.references) ||
    !object(value.selection) ||
    !counts(value.selection.counts) ||
    !diagnostics(value.selection.diagnostics) ||
    !object(value.result) ||
    !counts(value.result.counts) ||
    value.qualification === undefined ||
    !finiteJson(value)
  )
    throw new Error(
      "Study analysis has an invalid report envelope or provenance.",
    );
  const result = value.result,
    resultCounts = result.counts as Record<string, number>,
    selectionCounts = value.selection.counts;
  if (
    ![
      "source_records",
      "selected_records",
      "used_records",
      "group_a",
      "group_b",
    ].every((key) => Number.isSafeInteger(selectionCounts[key])) ||
    selectionCounts.source_records !== value.source.through_seq ||
    selectionCounts.selected_records > selectionCounts.source_records ||
    selectionCounts.used_records > selectionCounts.selected_records
  )
    throw new Error("Study analysis has invalid retained selection counts.");
  if (value.method.id === "custom.python") {
    if (
      !("custom_output" in result) ||
      !Number.isSafeInteger(resultCounts.selected_records) ||
      resultCounts.selected_records !== selectionCounts.selected_records
    )
      throw new Error("Custom study analysis has an invalid retained output.");
    return;
  }
  const interval = result.interval;
  if (
    !["means.welch", "means.paired"].includes(value.method.id) ||
    !["estimate", "standard_error", "degrees_of_freedom"].every(
      (key) => result[key] === null || finite(result[key]),
    ) ||
    !Number.isSafeInteger(resultCounts.group_a) ||
    !Number.isSafeInteger(resultCounts.group_b) ||
    resultCounts.group_a !== selectionCounts.group_a ||
    resultCounts.group_b !== selectionCounts.group_b ||
    (value.method.id === "means.paired" &&
      (resultCounts.group_a !== resultCounts.group_b ||
        resultCounts.complete_pairs !== resultCounts.group_a)) ||
    !object(interval) ||
    !finite(interval.confidence) ||
    interval.confidence <= 0 ||
    interval.confidence >= 1 ||
    interval.confidence !== value.request.confidence ||
    !["lower", "upper"].every(
      (key) => interval[key] === null || finite(interval[key]),
    ) ||
    typeof interval.lower_unbounded !== "boolean" ||
    typeof interval.upper_unbounded !== "boolean" ||
    (interval.lower_unbounded && interval.lower !== null) ||
    (interval.upper_unbounded && interval.upper !== null) ||
    (finite(interval.lower) &&
      finite(interval.upper) &&
      interval.lower > interval.upper) ||
    !diagnostics(result.diagnostics)
  )
    throw new Error(
      "Study analysis has invalid estimates, counts, intervals or diagnostics.",
    );
}

export async function loadStudyAnalysisReport(
  api: ApiClient,
  artifact: RetainedArtifact,
  signal: AbortSignal,
): Promise<AnalysisReport> {
  if (!hash(artifact.sha256) || !globalThis.crypto?.subtle)
    throw new Error("Study analysis requires a verifiable SHA-256 checksum.");
  const blob = await api.download(
    studyAnalysisPaths.artifact(artifact.id),
    signal,
  );
  const bytes = await blob.arrayBuffer();
  if (signal.aborted)
    throw new DOMException("Study analysis loading cancelled", "AbortError");
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const actual = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  if (actual !== artifact.sha256.toLowerCase())
    throw new Error("Study analysis checksum mismatch.");
  const report = scientificJson.parse(new TextDecoder().decode(bytes));
  validateAnalysisReport(report);
  if (signal.aborted)
    throw new DOMException("Study analysis loading cancelled", "AbortError");
  return report;
}
