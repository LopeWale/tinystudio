import type { ApiClient } from "../../api/client";
import { scientificJson } from "../../api/scientific-json";
import type { RetainedArtifact } from "./evidence";
import { loadRetainedJson } from "./retained-json";

type JsonObject = Record<string, unknown>;
export const RUNTIME_PROBE_ARTIFACT_ROLE = "runtime_probe";
export type RuntimeProbeStatus =
  "verified" | "failed" | "unavailable" | "cancelled";
export interface RuntimeProbeInput extends JsonObject {
  artifact_id: string;
  role: string;
  sha256: string;
}
export interface RuntimeProbeConfiguration extends JsonObject {
  method: string;
  objective: string;
  device: string;
  dtype: string;
  fixture: string;
  seed: number | string;
  inputs: RuntimeProbeInput[];
  training: JsonObject;
  fixture_config: JsonObject;
  nf4_execution: JsonObject;
  reload_comparison: JsonObject;
}
export type RuntimeProbeScope = Partial<RuntimeProbeConfiguration> & {
  generalizes_to_other_models?: false;
};
export interface RuntimeProbeReport extends JsonObject {
  format: "tinystudio.runtime-probe.v1";
  method_version: string;
  implementation_sha256: string;
  integer_encoding?: "safe_numbers_or_decimal_strings";
  status: RuntimeProbeStatus;
  stage: string;
  requested_configuration: JsonObject;
  configuration: RuntimeProbeConfiguration | null;
  prerequisites: JsonObject | null;
  stages: { stage: string; status: "started" | "completed" | "failed" }[];
  diagnostics: {
    stage: string;
    code: string;
    type?: string;
    message: string;
  }[];
  error: { type: string; message: string } | null;
  receipt: unknown;
  scope: RuntimeProbeScope;
  qualification: {
    kind: "runtime_compatibility_probe";
    status: RuntimeProbeStatus;
    qualified: boolean;
    scope: RuntimeProbeScope;
    generalization?: string;
  };
  evidence: JsonObject;
  fixture?: JsonObject;
}
const object = (value: unknown): value is JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string =>
  typeof value === "string" && !!value.trim();
const hash = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f\d]{64}$/i.test(value);
const exactInteger = (value: unknown): value is number | string =>
  typeof value === "number"
    ? Number.isSafeInteger(value)
    : typeof value === "string" && /^(?:0|-?[1-9]\d*)$/.test(value);
const finiteJson = (value: unknown): boolean =>
  typeof value === "number"
    ? Number.isFinite(value)
    : Array.isArray(value)
      ? value.every(finiteJson)
      : object(value)
        ? Object.values(value).every(finiteJson)
        : value === null || ["string", "boolean"].includes(typeof value);
const inputs = (value: unknown): value is RuntimeProbeInput[] =>
  Array.isArray(value) &&
  value.every(
    (item) =>
      object(item) &&
      text(item.artifact_id) &&
      text(item.role) &&
      hash(item.sha256),
  );
function configuration(value: unknown): value is RuntimeProbeConfiguration {
  return (
    object(value) &&
    ["method", "objective", "device", "dtype", "fixture"].every((key) =>
      text(value[key]),
    ) &&
    exactInteger(value.seed) &&
    inputs(value.inputs) &&
    ["training", "fixture_config", "nf4_execution", "reload_comparison"].every(
      (key) => object(value[key]),
    )
  );
}
function canonical(value: unknown): string {
  const sorted = (item: unknown): unknown =>
    Array.isArray(item)
      ? item.map(sorted)
      : object(item)
        ? Object.fromEntries(
            Object.keys(item)
              .sort()
              .map((key) => [key, sorted(item[key])]),
          )
        : item;
  return scientificJson.stringify(sorted(value));
}
const equal = (left: unknown, right: unknown) =>
  left !== undefined &&
  right !== undefined &&
  canonical(left) === canonical(right);

/** A successful Laboratory job never substitutes for measured, consistent qualification evidence. */
export function validateRuntimeProbeReport(
  value: unknown,
): asserts value is RuntimeProbeReport {
  if (
    !object(value) ||
    value.format !== "tinystudio.runtime-probe.v1" ||
    !text(value.method_version) ||
    !hash(value.implementation_sha256) ||
    !["verified", "failed", "unavailable", "cancelled"].includes(
      String(value.status),
    ) ||
    !text(value.stage) ||
    !object(value.requested_configuration) ||
    !(value.configuration === null || configuration(value.configuration)) ||
    !(value.prerequisites === null || object(value.prerequisites)) ||
    !Array.isArray(value.stages) ||
    !value.stages.length ||
    !value.stages.every(
      (item) =>
        object(item) &&
        text(item.stage) &&
        ["started", "completed", "failed"].includes(String(item.status)),
    ) ||
    !Array.isArray(value.diagnostics) ||
    !value.diagnostics.every(
      (item) =>
        object(item) &&
        text(item.stage) &&
        text(item.code) &&
        typeof item.message === "string",
    ) ||
    !(
      value.error === null ||
      (object(value.error) &&
        text(value.error.type) &&
        typeof value.error.message === "string")
    ) ||
    !("receipt" in value) ||
    !object(value.scope) ||
    !object(value.evidence) ||
    !object(value.qualification) ||
    value.qualification.kind !== "runtime_compatibility_probe" ||
    value.qualification.status !== value.status ||
    value.qualification.qualified !== (value.status === "verified") ||
    !object(value.qualification.scope) ||
    !finiteJson(value)
  )
    throw new Error(
      "Runtime probe has an invalid or inconsistent report envelope.",
    );
  const scope = value.scope;
  if (
    !equal(scope, value.qualification.scope) ||
    (Object.keys(scope).length > 0 &&
      (!configuration(scope) || scope.generalizes_to_other_models !== false)) ||
    value.stages.at(-1)?.stage !== value.stage
  )
    throw new Error(
      "Runtime probe has an invalid or inconsistent qualification scope or stage history.",
    );
  if (value.status !== "verified") return;
  const prerequisites = value.prerequisites,
    evidence = value.evidence,
    normalized = value.configuration;
  const verification = evidence.runtime_verification,
    optimization = evidence.optimization;
  if (
    !configuration(scope) ||
    !normalized ||
    !prerequisites ||
    prerequisites.format !== "tinystudio.runtime-prerequisites.v1" ||
    prerequisites.status !== "unprobed" ||
    prerequisites.qualified !== false ||
    prerequisites.eligible !== true ||
    !object(prerequisites.inventory) ||
    !object(prerequisites.inventory.dependencies) ||
    !Object.values(prerequisites.inventory.dependencies).every(
      (version) => version === null || typeof version === "string",
    ) ||
    !object(prerequisites.selection) ||
    prerequisites.selection.device !== scope.device ||
    prerequisites.selection.dtype !== scope.dtype ||
    value.error !== null ||
    value.stages.some((stage) => stage.status !== "completed") ||
    ![
      "method",
      "objective",
      "fixture",
      "dtype",
      "seed",
      "training",
      "fixture_config",
      "nf4_execution",
      "reload_comparison",
    ].every((key) => equal(normalized[key], scope[key])) ||
    !inputs(scope.inputs) ||
    !scope.inputs.length ||
    !inputs(evidence.source_inputs) ||
    !equal(scope.inputs, evidence.source_inputs) ||
    !hash(evidence.pretrained_manifest_sha256) ||
    !object(optimization) ||
    optimization.adapter_changed !== true ||
    optimization.optimizer_parameters_verified !== true ||
    optimization.frozen_base_verified !== true ||
    !hash(optimization.adapter_before_sha256) ||
    !hash(optimization.adapter_after_sha256) ||
    optimization.adapter_before_sha256 === optimization.adapter_after_sha256 ||
    !object(verification) ||
    verification.status !== "passed" ||
    !object(verification.observed_runtime) ||
    verification.observed_runtime.device !== scope.device ||
    verification.observed_runtime.dtype !== scope.dtype ||
    !equal(verification.comparison_policy, scope.reload_comparison) ||
    !equal(evidence.execution_policy, scope.nf4_execution)
  )
    throw new Error(
      "Runtime probe has invalid or inconsistent verified measurement evidence.",
    );
}

export function loadRuntimeProbeReport(
  api: ApiClient,
  artifact: RetainedArtifact,
  signal: AbortSignal,
) {
  return loadRetainedJson(api, artifact, signal, {
    label: "Runtime probe",
    validate: validateRuntimeProbeReport,
  });
}
