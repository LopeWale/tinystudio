import type { ApiClient } from "../../api/client";
import type { RetainedArtifact } from "./evidence";
import { loadRetainedJson, retainedArtifactPath } from "./retained-json";

type JsonObject = Record<string, unknown>;
export const HARDWARE_DISCOVERY_ARTIFACT_ROLE = "device_discovery";
export const HARDWARE_DISCOVERY_WIRE_ROLE = "discovery_wire";
export interface HardwareDiscoveryMethod extends JsonObject {
  id: string;
  label?: string;
  description?: string;
  required?: string[];
  schema?: JsonObject;
  parameters?: JsonObject;
}
export type HardwareDiscoveryStatus =
  "complete" | "partial" | "not_found" | "failed" | "unavailable" | "cancelled";
export interface DiscoveryModel extends JsonObject {
  model_id: number;
  instance: number;
  header_address: number;
  payload_address: number;
  length: number;
  header_registers: number[];
  registers: unknown[];
  data_reads: JsonObject[];
  status: string;
}
export interface DiscoveryTrial extends JsonObject {
  base_address: number;
  device_id: number;
  register_kind: string;
  status: string;
  complete: boolean;
  headers: { address: number; registers: number[] }[];
  models: DiscoveryModel[];
  reads: JsonObject[];
}
export interface HardwareDiscoveryReport extends JsonObject {
  format: "tinystudio.hardware-discovery.v1";
  engine: "hardware.discover";
  method: "serial.enumerate" | "can.enumerate" | "sunspec.scan";
  binding_id: string;
  status: HardwareDiscoveryStatus;
  requested_configuration: JsonObject;
  effective_configuration: JsonObject;
  resource_limits: JsonObject;
  budget_scope: JsonObject;
  source: JsonObject;
  discovery: (JsonObject & { trials?: DiscoveryTrial[] }) | null;
  clock: JsonObject;
  runtime: JsonObject;
  implementation_sha256: string;
  qualification: {
    qualified: false;
    physical_validation: false;
    scope: string;
  };
}
const object = (value: unknown): value is JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string =>
  typeof value === "string" && !!value.trim();
const integer = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const finiteJson = (value: unknown): boolean =>
  typeof value === "number"
    ? Number.isFinite(value)
    : Array.isArray(value)
      ? value.every(finiteJson)
      : object(value)
        ? Object.values(value).every(finiteJson)
        : value === null || ["string", "boolean"].includes(typeof value);
const arrayOfObjects = (value: unknown): value is JsonObject[] =>
  Array.isArray(value) && value.every(object);
const header = (value: unknown) =>
  object(value) &&
  integer(value.address) &&
  Array.isArray(value.registers) &&
  value.registers.length === 2 &&
  value.registers.every((item) => integer(item) && item <= 65535);
const read = (value: unknown) =>
  object(value) &&
  text(value.kind) &&
  integer(value.address) &&
  integer(value.count) &&
  integer(value.device_id) &&
  text(value.status) &&
  Array.isArray(value.registers);
const model = (value: unknown) =>
  object(value) &&
  ["model_id", "instance", "header_address", "payload_address", "length"].every(
    (key) => integer(value[key]),
  ) &&
  text(value.status) &&
  Array.isArray(value.header_registers) &&
  value.header_registers.length === 2 &&
  value.header_registers.every(integer) &&
  Array.isArray(value.registers) &&
  Array.isArray(value.data_reads) &&
  value.data_reads.every(read);
const trial = (value: unknown) =>
  object(value) &&
  ["base_address", "device_id", "registers_read"].every((key) =>
    integer(value[key]),
  ) &&
  ["holding_registers", "input_registers"].includes(
    String(value.register_kind),
  ) &&
  text(value.status) &&
  typeof value.complete === "boolean" &&
  (value.signature === null || Array.isArray(value.signature)) &&
  Array.isArray(value.headers) &&
  value.headers.every(header) &&
  Array.isArray(value.models) &&
  value.models.every(model) &&
  Array.isArray(value.reads) &&
  value.reads.every(read) &&
  (value.end_marker === null || header(value.end_marker));

/** Discovery describes retained structure; a successful job never supplies physical qualification. */
export function validateHardwareDiscoveryReport(
  value: unknown,
): asserts value is HardwareDiscoveryReport {
  if (
    !object(value) ||
    value.format !== "tinystudio.hardware-discovery.v1" ||
    value.engine !== "hardware.discover" ||
    !["serial.enumerate", "can.enumerate", "sunspec.scan"].includes(
      String(value.method),
    ) ||
    !text(value.binding_id) ||
    ![
      "complete",
      "partial",
      "not_found",
      "failed",
      "unavailable",
      "cancelled",
    ].includes(String(value.status)) ||
    !object(value.requested_configuration) ||
    !object(value.effective_configuration) ||
    value.requested_configuration.method !== value.method ||
    value.effective_configuration.method !== value.method ||
    value.requested_configuration.binding_id !== value.binding_id ||
    value.effective_configuration.binding_id !== value.binding_id ||
    !object(value.resource_limits) ||
    !object(value.budget_scope) ||
    !object(value.source) ||
    value.source.binding_id !== value.binding_id ||
    !(value.discovery === null || object(value.discovery)) ||
    !object(value.clock) ||
    value.clock.basis !== "execution_host_observation" ||
    !object(value.clock.started) ||
    !object(value.clock.finished) ||
    ![value.clock.started, value.clock.finished].every(
      (stamp) =>
        typeof stamp.wall_time === "number" &&
        Number.isFinite(stamp.wall_time) &&
        typeof stamp.monotonic === "number" &&
        Number.isFinite(stamp.monotonic),
    ) ||
    !object(value.runtime) ||
    !text(value.runtime.python) ||
    !text(value.runtime.platform) ||
    !arrayOfObjects(value.runtime.dependencies) ||
    typeof value.implementation_sha256 !== "string" ||
    !/^[a-f\d]{64}$/i.test(value.implementation_sha256) ||
    !object(value.qualification) ||
    value.qualification.qualified !== false ||
    value.qualification.physical_validation !== false ||
    !text(value.qualification.scope) ||
    !finiteJson(value)
  )
    throw new Error(
      "Hardware discovery has an invalid or inconsistent report envelope.",
    );
  const discovery = value.discovery;
  if (discovery === null) {
    if (["complete", "partial", "not_found"].includes(String(value.status)))
      throw new Error(
        "Hardware discovery has an inconsistent discovery outcome.",
      );
    return;
  }
  if (
    !text(discovery.status) ||
    (value.method === "serial.enumerate" &&
      (discovery.format !== "tinystudio.serial-enumeration.v1" ||
        !arrayOfObjects(discovery.ports))) ||
    (value.method === "can.enumerate" &&
      (discovery.format !== "tinystudio.can-enumeration.v1" ||
        !arrayOfObjects(discovery.configurations) ||
        !Array.isArray(discovery.interfaces) ||
        !discovery.interfaces.every(text))) ||
    (value.method === "sunspec.scan" &&
      (discovery.format !== "tinystudio.sunspec-discovery.v1" ||
        !object(discovery.config) ||
        typeof discovery.complete !== "boolean" ||
        !integer(discovery.registers_read) ||
        !Array.isArray(discovery.trials) ||
        !discovery.trials.every(trial)))
  )
    throw new Error(
      "Hardware discovery has invalid or inconsistent structural discovery evidence.",
    );
}

export function loadHardwareDiscoveryReport(
  api: ApiClient,
  artifact: RetainedArtifact,
  signal: AbortSignal,
) {
  return loadRetainedJson(api, artifact, signal, {
    label: "Hardware discovery report",
    validate: validateHardwareDiscoveryReport,
  });
}

/** Wire receipts are JSONL bytes, so validate their identity without reserializing them. */
export async function loadHardwareDiscoveryArtifact(
  api: ApiClient,
  artifact: RetainedArtifact,
  signal: AbortSignal,
) {
  const current = () => {
    if (signal.aborted)
      throw new DOMException(
        "Hardware discovery download cancelled",
        "AbortError",
      );
  };
  current();
  if (
    !artifact.sha256 ||
    !/^[a-f\d]{64}$/i.test(artifact.sha256) ||
    !globalThis.crypto?.subtle
  )
    throw new Error(
      "Hardware discovery artifact requires a verifiable SHA-256 checksum.",
    );
  const blob = await api.download(retainedArtifactPath(artifact.id), signal);
  const bytes = await blob.arrayBuffer();
  current();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  current();
  const actual = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  if (actual !== artifact.sha256.toLowerCase())
    throw new Error("Hardware discovery artifact checksum mismatch.");
  return blob;
}

/** Check only authored values against the advertised schema; never fill SDK defaults. */
export function discoveryParameterProblems(
  value: unknown,
  schema: JsonObject,
  path = "parameters",
): string[] {
  const problems: string[] = [];
  const types = Array.isArray(schema.type)
    ? schema.type
    : schema.type
      ? [schema.type]
      : [];
  const isType = (type: unknown) =>
    type === "null"
      ? value === null
      : type === "integer"
        ? typeof value === "number" && Number.isSafeInteger(value)
        : type === "number"
          ? typeof value === "number" && Number.isFinite(value)
          : type === "object"
            ? object(value)
            : type === "array"
              ? Array.isArray(value)
              : typeof value === type;
  if (types.length && !types.some(isType))
    return [`${path} must have type ${types.join(" or ")}.`];
  if (Object.hasOwn(schema, "const") && value !== schema.const)
    problems.push(`${path} must match the advertised choice.`);
  if (Array.isArray(schema.enum) && !schema.enum.includes(value))
    problems.push(`${path} must match an advertised choice.`);
  if (
    typeof value === "string" &&
    typeof schema.minLength === "number" &&
    value.trim().length < schema.minLength
  )
    problems.push(`${path} requires an explicit nonempty value.`);
  if (typeof value === "number") {
    if (typeof schema.minimum === "number" && value < schema.minimum)
      problems.push(`${path} must be at least ${schema.minimum}.`);
    if (typeof schema.maximum === "number" && value > schema.maximum)
      problems.push(`${path} must be at most ${schema.maximum}.`);
  }
  if (Array.isArray(value)) {
    if (typeof schema.minItems === "number" && value.length < schema.minItems)
      problems.push(
        `${path} requires at least ${schema.minItems} authored item(s).`,
      );
    if (
      schema.uniqueItems === true &&
      new Set(value.map((item) => JSON.stringify(item))).size !== value.length
    )
      problems.push(`${path} contains duplicate items.`);
    if (object(schema.items))
      value.forEach((item, index) =>
        problems.push(
          ...discoveryParameterProblems(
            item,
            schema.items as JsonObject,
            `${path}[${index}]`,
          ),
        ),
      );
  }
  if (object(value)) {
    const properties = object(schema.properties) ? schema.properties : {};
    if (Array.isArray(schema.required))
      for (const key of schema.required) {
        if (typeof key === "string" && !Object.hasOwn(value, key))
          problems.push(`${path}.${key} requires an explicit authored value.`);
      }
    for (const [key, item] of Object.entries(value)) {
      if (object(properties[key]))
        problems.push(
          ...discoveryParameterProblems(
            item,
            properties[key],
            `${path}.${key}`,
          ),
        );
      else if (schema.additionalProperties === false)
        problems.push(
          `Unsupported field ${path}.${key}; correct or remove it explicitly in JSON.`,
        );
    }
  }
  return problems;
}
