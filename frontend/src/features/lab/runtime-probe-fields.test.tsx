import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { scientificJson } from "../../api/scientific-json";
import { makeDefinition, type ExperimentDefinition } from "./definition";
import {
  RuntimeProbeFields,
  type RuntimeProbeMethod,
} from "./runtime-probe-fields";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const blank = (): ExperimentDefinition => ({
  ...makeDefinition("runtime.qualify"),
  parameters: {},
});
function Harness({
  initial = blank(),
  methods,
  onSave = vi.fn(),
}: {
  initial?: ExperimentDefinition;
  methods?: readonly RuntimeProbeMethod[];
  onSave?: (definition: ExperimentDefinition) => void;
}) {
  const [definition, setDefinition] = useState(initial);
  return (
    <>
      <RuntimeProbeFields
        definition={definition}
        methods={methods}
        onChange={(patch) =>
          setDefinition((current) => ({ ...current, ...patch }))
        }
      />
      <output data-testid="typed-definition">
        {scientificJson.stringify(definition)}
      </output>
      <button onClick={() => onSave(definition)}>Save typed definition</button>
    </>
  );
}
const current = () =>
  scientificJson.parse(
    screen.getByTestId("typed-definition").textContent!,
  ) as ExperimentDefinition;
async function select(label: string, name: string) {
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp(`${label}$`) }),
  );
  await userEvent.click(await screen.findByRole("option", { name }));
}
const edit = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const save = () =>
  fireEvent.click(
    screen.getByRole("button", { name: "Save typed definition" }),
  );

it("authors the required runtime settings explicitly through the native Lab form without guessing a device", async () => {
  const onSave = vi.fn(),
    fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(<Harness onSave={onSave} />);
  expect(screen.getByLabelText("Selected probe device")).toHaveValue("");
  expect(current().parameters).toEqual({});
  expect(current().domain).toBe("runtime");
  expect(current().operating_mode).toBe("replay");
  await select("Runtime probe method", "Measure NF4 training and reload");
  await select("Probe training objective", "Supervised fine-tuning (SFT)");
  edit("Selected probe device", "my-target-device");
  await select("Probe data type", "float32");
  await select("Probe fixture", "Small diagnostic model fixture");
  await select("NF4 CPU inference behavior", "Portable CPU execution");
  save();
  expect(onSave).toHaveBeenCalledWith(
    expect.objectContaining({
      engine: "runtime.qualify",
      parameters: {
        method: "nf4.training",
        objective: "sft",
        device: "my-target-device",
        dtype: "float32",
        fixture: "tiny_llama",
        nf4_execution: { cpu_inference: "portable" },
      },
    }),
  );
  expect(current().parameters).not.toHaveProperty("training");
  expect(current().parameters).not.toHaveProperty("reload_comparison");
  expect(screen.getByText(/diagnostic composition/)).toHaveTextContent(
    /not a minimum study sample size/,
  );
  expect(
    screen.getByText(/selected hardware remains unprobed/i),
  ).toHaveTextContent(/retained runtime probe receipt/);
  expect(fetcher).not.toHaveBeenCalled();
}, 15000);

it("preserves advanced parameters, exact numbers, input bindings and nested policy siblings when fields change", async () => {
  const initial = blank(),
    onSave = vi.fn();
  initial.inputs = [
    {
      artifact_id: "retained-model",
      sha256: "a".repeat(64),
      role: "pretrained_model",
    },
  ];
  initial.parameters = {
    method: "nf4.training",
    objective: "causal_lm",
    device: "authored-device",
    dtype: "bf16",
    fixture: "tiny_llama",
    nf4_execution: {
      cpu_inference: "library_default",
      custom_policy: { retained: true },
      signed_zero: -0,
    },
    training: { steps: 7, learning_rate: 1e-4, custom_coefficient: -0 },
    fixture_config: { hidden_size: 128, vendor_layout: "retained" },
    reload_comparison: {
      mode: "allclose",
      rtol: 1e-7,
      atol: -0,
      custom_note: "Keep my tolerance policy",
    },
    custom: { approximate_float: 1e30, exact_integer: "18446744073709551615" },
  };
  render(<Harness initial={initial} onSave={onSave} />);
  await select(
    "Probe training objective",
    "Direct preference optimization (DPO)",
  );
  edit("Selected probe device", " another explicit device ");
  await select("NF4 CPU inference behavior", "Portable CPU execution");
  await select("Probe fixture", "Retained model and data artifacts");
  save();
  const saved = onSave.mock.calls[0][0] as ExperimentDefinition;
  expect(saved.parameters).toMatchObject({
    objective: "dpo",
    device: " another explicit device ",
    dtype: "bf16",
    fixture: "retained",
    nf4_execution: {
      cpu_inference: "portable",
      custom_policy: { retained: true },
    },
  });
  for (const key of [
    "training",
    "fixture_config",
    "reload_comparison",
    "custom",
  ])
    expect(saved.parameters[key]).toBe(initial.parameters[key]);
  expect(saved.inputs).toBe(initial.inputs);
  expect(
    Object.is((saved.parameters.nf4_execution as any).signed_zero, -0),
  ).toBe(true);
  expect(Object.is((saved.parameters.reload_comparison as any).atol, -0)).toBe(
    true,
  );
  expect(
    screen.getByLabelText("Current reload comparison policy"),
  ).toHaveTextContent("allclose");
  expect(
    screen.getByLabelText("Current reload comparison policy"),
  ).toHaveTextContent("atol: -0.0");
  expect(screen.getByText(/pretrained_model and snapshot roles/)).toBeVisible();
});

it("uses catalog choices without adopting catalog defaults or modifying unrelated parameters", async () => {
  const methods: RuntimeProbeMethod[] = [
    {
      id: "nf4.training",
      label: "Site runtime measurement",
      parameters: {
        objective: { enum: ["causal_lm"] },
        dtype: { enum: ["bf16"] },
        fixture: { enum: ["retained"] },
        nf4_execution: {
          properties: { cpu_inference: { enum: ["portable"] } },
          default: { cpu_inference: "library_default" },
        },
        training: { default: { steps: 999 } },
        reload_comparison: {
          default: { mode: "allclose", rtol: 0.5, atol: 0.5 },
        },
      },
    },
  ];
  render(<Harness methods={methods} />);
  expect(current().parameters).toEqual({});
  await select("Runtime probe method", "Site runtime measurement");
  await userEvent.click(
    screen.getByRole("button", { name: /Probe training objective$/ }),
  );
  expect(
    screen.queryByRole("option", { name: "Supervised fine-tuning (SFT)" }),
  ).toBeNull();
  await userEvent.click(
    screen.getByRole("option", { name: "Causal language modeling" }),
  );
  await select("Probe data type", "bf16 (alias for bfloat16)");
  await select("Probe fixture", "Retained model and data artifacts");
  await userEvent.click(
    screen.getByRole("button", { name: /NF4 CPU inference behavior$/ }),
  );
  expect(
    screen.queryByRole("option", { name: "Library default CPU execution" }),
  ).toBeNull();
  await userEvent.click(
    screen.getByRole("option", { name: "Portable CPU execution" }),
  );
  expect(current().parameters).toEqual({
    method: "nf4.training",
    objective: "causal_lm",
    dtype: "bf16",
    fixture: "retained",
    nf4_execution: { cpu_inference: "portable" },
  });
});

it("reads choices from the catalog JSON schema and keeps missing tolerances explicitly missing", async () => {
  const initial = blank();
  initial.parameters = {
    method: "nf4.training",
    reload_comparison: { mode: "allclose" },
    custom: "retained",
  };
  const methods: RuntimeProbeMethod[] = [
    {
      id: "nf4.training",
      label: "Schema method",
      schema: {
        properties: {
          objective: { enum: ["sft"] },
          dtype: { enum: ["fp32"] },
          fixture: { enum: ["tiny_llama"] },
          nf4_execution: {
            properties: { cpu_inference: { enum: ["library_default"] } },
          },
        },
      },
    },
  ];
  render(<Harness initial={initial} methods={methods} />);
  expect(
    screen.getByLabelText("Current reload comparison policy"),
  ).toHaveTextContent("rtol: not provided");
  expect(
    screen.getByLabelText("Current reload comparison policy"),
  ).toHaveTextContent("atol: not provided");
  await select("Probe data type", "fp32 (alias for float32)");
  expect(current().parameters.reload_comparison).toEqual({ mode: "allclose" });
  expect(current().parameters.custom).toBe("retained");
  expect(screen.getByText(/Definition JSON/)).toHaveTextContent(
    /training settings.*fixture.*tolerances/i,
  );
});

it("does not manufacture a method or comparison policy for an empty advertised catalog", () => {
  const onChange = vi.fn();
  render(
    <RuntimeProbeFields
      definition={blank()}
      methods={[]}
      onChange={onChange}
    />,
  );
  expect(
    screen.getByText(/No runtime probe methods are advertised/),
  ).toBeVisible();
  expect(
    screen.getByLabelText("Current reload comparison policy"),
  ).toHaveTextContent(/No explicit reload comparison policy/);
  expect(onChange).not.toHaveBeenCalled();
});

it("keeps malformed advanced comparison values in the draft while basic fields remain editable", () => {
  const definition = blank(),
    onChange = vi.fn();
  definition.parameters.reload_comparison = {
    mode: "allclose",
    rtol: Infinity,
    atol: -0,
  };
  render(<RuntimeProbeFields definition={definition} onChange={onChange} />);
  expect(
    screen.getByLabelText("Current reload comparison policy"),
  ).toHaveTextContent(/finite numbers/);
  edit("Selected probe device", "explicit-device");
  const patch = onChange.mock.calls[0][0] as Partial<ExperimentDefinition>;
  expect(patch.parameters!.reload_comparison).toBe(
    definition.parameters.reload_comparison,
  );
  expect((patch.parameters!.reload_comparison as any).rtol).toBe(Infinity);
});
