import { Input } from "@/components/boardui/base/input/input";
import { SettingsSection } from "../../components/studio/settings-section";
import { scientificJson } from "../../api/scientific-json";
import type { ExperimentDefinition } from "./definition";
import { LabChoice } from "./inputs";

type JsonObject = Record<string, unknown>;
export interface RuntimeProbeMethod {
  id: string;
  label?: string;
  version?: string;
  description?: string;
  parameters?: JsonObject;
  schema?: JsonObject;
  [key: string]: unknown;
}
export interface RuntimeProbeFieldsProps {
  definition: ExperimentDefinition;
  onChange: (patch: Partial<ExperimentDefinition>) => void;
  methods?: readonly RuntimeProbeMethod[];
}
const object = (value: unknown): JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
const selected = (value: unknown) => (typeof value === "string" ? value : "");
const enumeration = (value: unknown): string[] => {
  const values = object(value).enum;
  return Array.isArray(values) &&
    values.every((item) => typeof item === "string")
    ? [...new Set(values)]
    : [];
};
const labels: Record<string, string> = {
  sft: "Supervised fine-tuning (SFT)",
  causal_lm: "Causal language modeling",
  dpo: "Direct preference optimization (DPO)",
  fp32: "fp32 (alias for float32)",
  bf16: "bf16 (alias for bfloat16)",
  tiny_llama: "Small diagnostic model fixture",
  retained: "Retained model and data artifacts",
  library_default: "Library default CPU execution",
  portable: "Portable CPU execution",
};
const choices = (values: string[]) =>
  values.map((id) => ({
    id,
    name: Object.hasOwn(labels, id) ? labels[id] : id,
  }));
const builtinMethod: RuntimeProbeMethod = {
  id: "nf4.training",
  label: "Measure NF4 training and reload",
  parameters: {
    objective: { enum: ["sft", "causal_lm", "dpo"] },
    dtype: { enum: ["float32", "bfloat16", "fp32", "bf16"] },
    fixture: { enum: ["tiny_llama", "retained"] },
    nf4_execution: {
      properties: { cpu_inference: { enum: ["library_default", "portable"] } },
    },
  },
};
function summaryJson(value: unknown) {
  try {
    return scientificJson.stringify(value);
  } catch (failure) {
    return `invalid value (${failure instanceof Error ? failure.message : String(failure)})`;
  }
}
function comparisonSummary(value: unknown) {
  if (value === undefined)
    return "No explicit reload comparison policy is authored.";
  const policy = object(value);
  if (policy.mode === "allclose") {
    const tolerance = (key: string) =>
      policy[key] === undefined ? "not provided" : summaryJson(policy[key]);
    return `allclose · rtol: ${tolerance("rtol")} · atol: ${tolerance("atol")}`;
  }
  if (policy.mode === "bitwise") return "bitwise · exact comparison";
  return summaryJson(value);
}

/** Native controls edit only the selected field; advanced configuration remains researcher-owned. */
export function RuntimeProbeFields({
  definition,
  onChange,
  methods,
}: RuntimeProbeFieldsProps) {
  const p = definition.parameters;
  const catalog = (methods === undefined ? [builtinMethod] : methods).filter(
    (method) => method && typeof method.id === "string" && !!method.id,
  );
  const currentMethod = catalog.find((method) => method.id === p.method);
  const choiceMethod =
    currentMethod ?? catalog.find((method) => method.id === "nf4.training");
  const properties = {
    ...object(object(choiceMethod?.schema).properties),
    ...object(choiceMethod?.parameters),
  };
  const nf4 = object(p.nf4_execution);
  const cpuOptions = enumeration(
    object(object(properties.nf4_execution).properties).cpu_inference,
  );
  const patch = (key: string, value: unknown) =>
    onChange({ parameters: { ...p, [key]: value } });
  const choice = (key: string, label: string) => {
    const options = choices(enumeration(properties[key]));
    return (
      <LabChoice
        label={label}
        value={selected(p[key])}
        onChange={(value) => patch(key, value)}
        options={options}
        disabled={!options.length}
        placeholder="Choose explicitly"
      />
    );
  };
  return (
    <SettingsSection
      title="Runtime compatibility measurement"
      description="Author the measurement for your chosen target. Selected hardware remains unprobed until a retained runtime probe receipt reports the measurements."
    >
      <div className="grid gap-4 md:grid-cols-2">
        <LabChoice
          label="Runtime probe method"
          value={selected(p.method)}
          onChange={(value) => patch("method", value)}
          options={catalog.map((method) => ({
            id: method.id,
            name: method.label || method.id,
          }))}
          disabled={!catalog.length}
          placeholder="Choose explicitly"
        />
        {choice("objective", "Probe training objective")}
        <Input
          label="Selected probe device"
          value={selected(p.device)}
          onChange={(value) => patch("device", value)}
          hint="Enter the exact device selector for the target you intend to measure."
        />
        {choice("dtype", "Probe data type")}
        {choice("fixture", "Probe fixture")}
        <LabChoice
          label="NF4 CPU inference behavior"
          value={selected(nf4.cpu_inference)}
          onChange={(value) =>
            patch("nf4_execution", { ...nf4, cpu_inference: value })
          }
          options={choices(cpuOptions)}
          disabled={!cpuOptions.length}
          placeholder="Choose explicitly"
        />
      </div>
      {!catalog.length && (
        <p>
          No runtime probe methods are advertised by this catalog. Existing
          parameter values remain retained.
        </p>
      )}
      {currentMethod?.description && <p>{currentMethod.description}</p>}
      <p className="text-body-2-regular text-text-secondary">
        The small built-in fixture has a fixed diagnostic composition, not a
        minimum study sample size.
      </p>
      {p.fixture === "retained" && (
        <p>
          Attach your retained model and data in the pretrained_model and
          snapshot roles using Inputs. Existing advanced fixture settings remain
          unchanged; review them for the selected fixture.
        </p>
      )}
      <p className="text-caption-1-regular text-text-secondary">
        The receipt records whether the selected CPU inference policy applied on
        the chosen device.
      </p>
      <p aria-label="Current reload comparison policy">
        Current reload comparison policy:{" "}
        {comparisonSummary(p.reload_comparison)}
      </p>
      <p className="text-body-2-regular text-text-secondary">
        Use Definition JSON to edit advanced training settings, fixture
        structure and the reload comparison policy. An allclose comparison
        requires explicit rtol and atol tolerances; these controls do not infer
        them.
      </p>
    </SettingsSection>
  );
}
