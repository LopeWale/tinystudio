import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { NumericLearning } from "./numeric-learning";
import { makeDefinition, type ExperimentDefinition } from "./definition";

afterEach(cleanup);
function Harness({
  initial = makeDefinition("learning.timeseries"),
}: {
  initial?: ExperimentDefinition;
}) {
  const [definition, setDefinition] = useState(initial);
  const [valid, setValid] = useState(true);
  return (
    <>
      <NumericLearning
        definition={definition}
        onChange={(patch) =>
          setDefinition((current) => ({ ...current, ...patch }))
        }
        onValidityChange={setValid}
      />
      <output data-testid="definition">{JSON.stringify(definition)}</output>
      <button disabled={!valid}>Save definition</button>
      <textarea
        aria-label="External JSON"
        value={JSON.stringify(definition)}
        onChange={(event) => setDefinition(JSON.parse(event.target.value))}
      />
    </>
  );
}
const current = (): ExperimentDefinition =>
  JSON.parse(screen.getByTestId("definition").textContent!);
const edit = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const save = () => screen.getByRole("button", { name: "Save definition" });
async function select(label: string, name: string) {
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp(`${label}$`) }),
  );
  await userEvent.click(await screen.findByRole("option", { name }));
}

it("authors exact numeric axes without requiring attached inputs or inventing unsupported settings", async () => {
  render(<Harness />);
  expect(save()).toBeEnabled();
  await select("Model family", "transformer");
  edit("Hidden size", "24");
  edit("Attention heads", "3");
  edit("Layers", "2");
  edit("Context length", "12");
  edit("Training steps", "30");
  edit("Learning rate", "0.02");
  edit("Forecast horizon", "4");
  edit("Target feature index", "1");
  await select("Learning objective", "regression");
  await select("Representation", "numeric");
  await select("Update strategy", "adam");
  expect(current().learning).toEqual({
    family: "transformer",
    architecture: { hidden_size: 24, heads: 3, layers: 2, context: 12 },
    objective: "regression",
    update_strategy: "adam",
  });
  expect(current().parameters).toEqual({
    steps: 30,
    lr: 0.02,
    horizon: 4,
    target_feature: 1,
  });
  expect(save()).toBeEnabled();
  expect(
    screen.queryByLabelText(/Batch size|Environment/),
  ).not.toBeInTheDocument();
});

it("reconciles existing parameter aliases while preserving siblings, records and provenance", async () => {
  const initial = makeDefinition("learning.timeseries");
  initial.learning = {
    family: { kind: "gru" },
    architecture: { hidden_size: 16 },
    initialization: "scratch",
  };
  initial.parameters = {
    family: "gru",
    hidden_size: 16,
    steps: 5,
    lr: 0.01,
    learning_rate: 0.01,
    records: [
      { features: [1, 2], source_record_id: "source-1", partition: "train" },
    ],
    custom: { retained: true },
  };
  initial.inputs = [
    { artifact_id: "records", sha256: "a".repeat(64), role: "source" },
  ];
  render(<Harness initial={initial} />);
  await select("Model family", "mlp");
  edit("Hidden size", "32");
  edit("Learning rate", "0.03");
  expect(current().learning).toEqual({
    family: { kind: "mlp" },
    architecture: { hidden_size: 32 },
    initialization: "scratch",
  });
  expect(current().parameters).toEqual({
    ...initial.parameters,
    family: "mlp",
    hidden_size: 32,
    lr: 0.03,
    learning_rate: 0.03,
  });
  expect(current().inputs).toEqual(initial.inputs);
  expect(save()).toBeEnabled();
});

it.each(["", "-", "-1", "0", "1.5", "Infinity"])(
  "retains an invalid integer draft %j locally and recovers",
  (value) => {
    render(<Harness />);
    edit("Training steps", value);
    expect(screen.getByLabelText("Training steps")).toHaveValue(value);
    expect(current().parameters.steps).toBe(10);
    expect(save()).toBeDisabled();
    edit("Forecast horizon", "2");
    expect(screen.getByLabelText("Training steps")).toHaveValue(value);
    expect(save()).toBeDisabled();
    edit("Training steps", "20");
    expect(save()).toBeEnabled();
  },
);

it.each([
  ["Hidden size", "513"],
  ["Hidden size", "3"],
  ["Layers", "9"],
  ["Context length", "513"],
  ["Context length", "0"],
  ["Forecast horizon", "129"],
  ["Training steps", "10001"],
  ["Target feature index", "-1"],
  ["Target feature index", "0.5"],
  ["Learning rate", "0"],
  ["Learning rate", "-0.2"],
  ["Learning rate", "1.1"],
])("blocks out-of-contract %s=%s", (label, value) => {
  render(<Harness />);
  edit(label, value);
  expect(save()).toBeDisabled();
});

it("accepts zero target index, minimum dimensions and fractional positive learning rate", () => {
  render(<Harness />);
  edit("Target feature index", "0");
  edit("Context length", "1");
  edit("Hidden size", "4");
  edit("Learning rate", "0.0001");
  expect(save()).toBeEnabled();
});

it("requires explicit removal of retained Transformer heads on a family change", async () => {
  const initial = makeDefinition("learning.timeseries");
  initial.learning = {
    family: "transformer",
    architecture: { hidden_size: 16, heads: 2, context: 8 },
  };
  initial.parameters.heads = 2;
  render(<Harness initial={initial} />);
  await select("Model family", "gru");
  expect(save()).toBeDisabled();
  expect(current().learning!.architecture.heads).toBe(2);
  await userEvent.click(
    screen.getByRole("button", { name: "Remove unsupported attention heads" }),
  );
  expect(current().learning!.architecture).toEqual({
    hidden_size: 16,
    context: 8,
  });
  expect(current().parameters).not.toHaveProperty("heads");
  expect(save()).toBeEnabled();
});

it("reports family and architecture conflicts, and checks head divisibility", async () => {
  const initial = makeDefinition("learning.timeseries");
  initial.learning = {
    family: "transformer",
    architecture: { hidden_size: 17, heads: 2 },
  };
  initial.parameters.family = "gru";
  initial.parameters.hidden_size = 16;
  render(<Harness initial={initial} />);
  expect(save()).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Conflicting learning.family",
  );
  expect(screen.getByRole("alert")).toHaveTextContent("divisible by heads");
  await userEvent.click(
    screen.getByRole("button", {
      name: "Use displayed model family for both aliases",
    }),
  );
  edit("Hidden size", "18");
  expect(save()).toBeEnabled();
  expect(current().parameters.hidden_size).toBe(18);
});

it.each(["lstm", null, { wrong: "gru" }, { kind: "gru", custom: true }])(
  "preserves invalid external family %j and reports it",
  (family) => {
    const initial = makeDefinition("learning.timeseries");
    initial.learning = { family };
    render(<Harness initial={initial} />);
    expect(current().learning!.family).toEqual(family);
    expect(save()).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent(/family/);
  },
);

it("authors fusion classification and regression, retaining raw targets and custom configuration", async () => {
  const initial = makeDefinition("learning.multimodal");
  initial.parameters.records = [
    {
      numeric: [1],
      text: "one",
      target: "class A",
      source_record_id: "original",
    },
  ];
  initial.parameters.custom = { source: "keep" };
  render(<Harness initial={initial} />);
  await select("Learning objective", "classification");
  edit("Target key", "label");
  edit("Hidden size", "20");
  expect(current().learning).toEqual({
    objective: "classification",
    architecture: { hidden_size: 20 },
  });
  expect(current().parameters).toEqual({
    ...initial.parameters,
    target_key: "label",
  });
  expect(save()).toBeEnabled();
  expect(screen.getByText(/UTF-8 byte histogram/)).toBeVisible();
  expect(screen.queryByLabelText("Context length")).not.toBeInTheDocument();
  edit("Layers", "2");
  expect(save()).toBeDisabled();
  edit("Layers", "1");
  edit("Target key", "");
  expect(save()).toBeDisabled();
  edit("Target key", "target");
  await select("Learning objective", "regression");
  expect(save()).toBeEnabled();
});

it("keeps incomplete modality JSON local across other edits and validates supported modality arrays", () => {
  render(<Harness initial={makeDefinition("learning.multimodal")} />);
  edit("Modalities JSON", '["numeric",');
  edit("Training steps", "25");
  expect(screen.getByLabelText("Modalities JSON")).toHaveValue('["numeric",');
  expect(current().parameters).not.toHaveProperty("modalities");
  expect(save()).toBeDisabled();
  edit("Modalities JSON", '["numeric"]');
  expect(save()).toBeDisabled();
  edit("Modalities JSON", '["numeric","video"]');
  expect(save()).toBeDisabled();
  edit("Modalities JSON", '["numeric","text"]');
  expect(save()).toBeEnabled();
  expect(current().parameters.modalities).toEqual(["numeric", "text"]);
});

it("retains and reports unsupported settings and artifact roles without rewriting them", () => {
  const initial = makeDefinition("learning.multimodal");
  initial.parameters = {
    steps: 10,
    batch_size: 2,
    environment: "cartpole",
    custom: { keep: true },
  };
  initial.learning = { architecture: { context: 8, custom: "keep" } };
  initial.inputs = [
    { artifact_id: "model", sha256: "b".repeat(64), role: "pretrained_model" },
  ];
  render(<Harness initial={initial} />);
  expect(save()).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("parameters.batch_size");
  expect(screen.getByRole("alert")).toHaveTextContent("pretrained_model input");
  expect(current()).toEqual(initial);
});

it("supports retained resume authoring with its immutable binding", () => {
  const initial = makeDefinition("learning.timeseries");
  initial.learning = { initialization: "resume" };
  initial.inputs = [
    { artifact_id: "checkpoint", sha256: "c".repeat(64), role: "resume" },
  ];
  render(<Harness initial={initial} />);
  expect(save()).toBeEnabled();
  edit("Training steps", "50");
  expect(current().learning!.initialization).toBe("resume");
  expect(current().inputs).toEqual(initial.inputs);
});

it("reports invalidity on external changes and resets validity on unmount", () => {
  const callback = vi.fn();
  const initial = makeDefinition("learning.timeseries");
  const { rerender, unmount } = render(
    <NumericLearning
      definition={initial}
      onChange={vi.fn()}
      onValidityChange={callback}
    />,
  );
  expect(callback).toHaveBeenLastCalledWith(true);
  rerender(
    <NumericLearning
      definition={{ ...initial, parameters: { steps: 0 } }}
      onChange={vi.fn()}
      onValidityChange={callback}
    />,
  );
  expect(callback).toHaveBeenLastCalledWith(false);
  unmount();
  expect(callback).toHaveBeenLastCalledWith(true);
});

it("keeps an incomplete architecture draft mounted by blocking family changes", async () => {
  const initial = makeDefinition("learning.timeseries");
  initial.learning = { family: "transformer", architecture: { heads: 2 } };
  render(<Harness initial={initial} />);
  edit("Attention heads", "-");
  expect(screen.getByRole("button", { name: /Model family$/ })).toBeDisabled();
  expect(screen.getByLabelText("Attention heads")).toHaveValue("-");
  edit("Training steps", "20");
  expect(screen.getByLabelText("Attention heads")).toHaveValue("-");
  expect(save()).toBeDisabled();
  edit("Attention heads", "2");
  expect(screen.getByRole("button", { name: /Model family$/ })).toBeEnabled();
  await select("Model family", "gru");
  expect(current().learning!.family).toBe("gru");
  expect(save()).toBeDisabled();
});

it("repairs an unsupported retained representation through the visible selector", async () => {
  const initial = makeDefinition("learning.timeseries");
  initial.learning = { representation: "images" };
  render(<Harness initial={initial} />);
  expect(save()).toBeDisabled();
  await select("Representation", "numeric");
  expect(current().learning!.representation).toBe("numeric");
  expect(save()).toBeEnabled();
});

it.each(["steps", "horizon", "target_feature", "lr", "hidden_size", "context"])(
  "preserves and rejects null or boolean numeric %s",
  (key) => {
    const initial = makeDefinition("learning.timeseries");
    initial.parameters[key] = null;
    const { unmount } = render(<Harness initial={initial} />);
    expect(save()).toBeDisabled();
    expect(current().parameters[key]).toBeNull();
    unmount();
    initial.parameters[key] = true;
    render(<Harness initial={initial} />);
    expect(save()).toBeDisabled();
    expect(current().parameters[key]).toBe(true);
  },
);

it.each(["target_key", "modalities"])(
  "retains and rejects explicit null fusion %s",
  (key) => {
    const initial = makeDefinition("learning.multimodal");
    initial.parameters[key] = null;
    render(<Harness initial={initial} />);
    expect(save()).toBeDisabled();
    expect(current().parameters[key]).toBeNull();
  },
);

it.each([
  "family",
  "objective",
  "representation",
  "update_strategy",
  "initialization",
  "device",
  "dtype",
  "training_dtype",
])("rejects nonstring kind values for retained %s", (key) => {
  const valid: Record<string, string> = {
    family: "gru",
    objective: "forecasting",
    representation: "numeric",
    update_strategy: "full",
    initialization: "scratch",
    device: "cpu",
    dtype: "float32",
    training_dtype: "fp32",
  };
  const initial = makeDefinition("learning.timeseries");
  initial.learning = { [key]: { kind: [valid[key]] } };
  initial.parameters.custom = { keep: true };
  render(<Harness initial={initial} />);
  expect(save()).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent(
    `${key} requires a nonempty string`,
  );
  expect(current()).toEqual(initial);
});

it("corrects malformed family kind through the visible family selector without discarding siblings", async () => {
  const initial = makeDefinition("learning.timeseries");
  initial.learning = { family: { kind: ["gru"] }, initialization: "scratch" };
  initial.parameters.custom = { keep: true };
  render(<Harness initial={initial} />);
  expect(save()).toBeDisabled();
  await select("Model family", "gru");
  expect(current().learning).toEqual({
    family: { kind: "gru" },
    initialization: "scratch",
  });
  expect(current().parameters.custom).toEqual({ keep: true });
  expect(save()).toBeEnabled();
});

it("preserves unknown learning keys and blocks actions until explicitly corrected in Definition JSON", () => {
  const initial = makeDefinition("learning.timeseries");
  initial.learning = { family: "gru", vendor_options: { keep: true } };
  initial.parameters.vendor_options = { keep: "parameter sibling" };
  render(<Harness initial={initial} />);
  expect(save()).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "learning.vendor_options is unsupported",
  );
  expect(current()).toEqual(initial);
  const corrected = { ...initial, learning: { family: "gru" } };
  edit("External JSON", JSON.stringify(corrected));
  expect(save()).toBeEnabled();
  expect(current().parameters.vendor_options).toEqual({
    keep: "parameter sibling",
  });
});

it.each([
  { kind: true },
  { kind: 1 },
  { kind: null },
  { kind: "" },
  { kind: { nested: "gru" } },
])("rejects malformed kind %j", (family) => {
  const initial = makeDefinition("learning.timeseries");
  initial.learning = { family };
  render(<Harness initial={initial} />);
  expect(save()).toBeDisabled();
  expect(current().learning!.family).toEqual(family);
});
