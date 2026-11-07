import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ControlLearning } from "./control-learning";
import { makeDefinition, type ExperimentDefinition } from "./definition";

afterEach(cleanup);
function Harness({
  initial = makeDefinition("learning.control_rl"),
}: {
  initial?: ExperimentDefinition;
}) {
  const [definition, setDefinition] = useState(initial);
  const [valid, setValid] = useState(true);
  return (
    <>
      <ControlLearning
        definition={definition}
        onChange={(patch) =>
          setDefinition((current) =>
            JSON.parse(JSON.stringify({ ...current, ...patch })),
          )
        }
        onValidityChange={setValid}
      />
      <output data-testid="definition">{JSON.stringify(definition)}</output>
      <button disabled={!valid}>Save definition</button>
      <button
        onClick={() => setDefinition((current) => ({ ...current, inputs: [] }))}
      >
        Detach inputs
      </button>
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

it("keeps a prototype-named objective as a literal unsupported selection and permits explicit correction", async () => {
  const initial = makeDefinition("learning.control_rl");
  initial.learning!.objective = "constructor";
  render(<Harness initial={initial} />);
  expect(current().learning!.objective).toBe("constructor");
  expect(
    screen.getByRole("button", { name: /constructor.*Control objective$/ }),
  ).toBeVisible();
  expect(save()).toBeDisabled();
  await select("Control objective", "PPO · environment interactions");
  expect(current().learning!.objective).toBe("ppo");
  expect(save()).toBeEnabled();
});

it("edits runtime and method aliases explicitly without dropping custom retained parameters", async () => {
  const initial = makeDefinition("learning.control_rl");
  initial.parameters = {
    ...initial.parameters,
    algorithm: { kind: "reinforcement_learning" },
    objective: "ppo",
    vendor: { keep: true },
  };
  initial.learning!.representation = "state_vector";
  render(<Harness initial={initial} />);
  expect(save()).toBeEnabled();
  await select("Control objective", "Offline DQN · retained transitions");
  expect(current().learning!.objective).toBe("dqn");
  expect(current().parameters.algorithm).toEqual({ kind: "dqn" });
  expect(current().parameters.objective).toBe("dqn");
  expect(current().parameters.vendor).toEqual({ keep: true });
  expect(current().learning!.representation).toBe("state_vector");
  expect(screen.getByText(/Attach retained transition data/)).toHaveTextContent(
    "demonstrations role in Inputs",
  );
  expect(save()).toBeEnabled();
  await select("Environment runtime", "TorchRL EnvBase");
  expect(current().parameters.environment).toEqual({
    kind: "control",
    runtime: "torchrl",
  });
  await select("Environment runtime", "Native control environment");
  expect(current().parameters.environment).toEqual({
    kind: "control",
    runtime: "native",
  });
  await select(
    "Control objective",
    "Behavioral cloning · demonstration actions",
  );
  expect(current().parameters.algorithm).toEqual({
    kind: "behavioral_cloning",
  });
  expect(screen.queryByLabelText("Discount factor")).not.toBeInTheDocument();
});

it("preserves nested siblings and reconciles owned architecture and rate aliases", () => {
  const initial = makeDefinition("learning.control_rl");
  initial.parameters = {
    ...initial.parameters,
    hidden_size: 16,
    learning_rate: 0.003,
    reward: { distance_weight: 1, action_cost: 0.01, vendor: { keep: true } },
  };
  initial.learning!.architecture = {
    hidden_size: 16,
    layers: 2,
    vendor: { keep: true },
  };
  render(<Harness initial={initial} />);
  edit("Policy hidden size", "32");
  edit("Learning rate", "0.01");
  edit("Distance reward weight", "0");
  expect(current().learning!.architecture).toEqual({
    hidden_size: 32,
    layers: 2,
    vendor: { keep: true },
  });
  expect(current().parameters.hidden_size).toBe(32);
  expect(current().parameters.lr).toBe(0.01);
  expect(current().parameters.learning_rate).toBe(0.01);
  expect(current().parameters.reward).toEqual({
    distance_weight: 0,
    action_cost: 0.01,
    vendor: { keep: true },
  });
  // The runtime rejects unknown architecture axes; preservation cannot imply validity.
  expect(save()).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("architecture.vendor");
});

it("accepts zero and signed targets and keeps incomplete numeric drafts through rerenders", () => {
  render(<Harness />);
  edit("Target position", "-1.25");
  expect(current().parameters.target).toBe(-1.25);
  edit("Target position", "0");
  expect(current().parameters.target).toBe(0);
  for (const draft of ["", "-", "1e"]) {
    edit("Target position", draft);
    expect(screen.getByLabelText("Target position")).toHaveValue(draft);
    expect(current().parameters.target).toBe(0);
    expect(save()).toBeDisabled();
    edit("Action cost", "0");
    expect(screen.getByLabelText("Target position")).toHaveValue(draft);
  }
  edit("Target position", "-2");
  expect(save()).toBeEnabled();
  edit("Episode horizon", "1.5");
  expect(screen.getByLabelText("Episode horizon")).toHaveValue("1.5");
  expect(current().parameters.horizon).toBe(24);
  expect(save()).toBeDisabled();
  edit("Episode horizon", "10000");
  expect(save()).toBeEnabled();
  edit("Agent count", "9");
  expect(save()).toBeDisabled();
  edit("Agent count", "8");
  expect(save()).toBeEnabled();
});

it.each([
  "[]",
  "[42]",
  "[1,1]",
  "[-1]",
  "[1.5]",
  '["3"]',
  `[${Array.from({ length: 65 }, (_, i) => i + 100).join(",")}]`,
])("rejects invalid independent seed selection %s and recovers", (seeds) => {
  render(<Harness />);
  edit("Evaluation seeds JSON", seeds);
  expect(save()).toBeDisabled();
  expect(current().parameters.evaluation).toEqual({ seeds: JSON.parse(seeds) });
  edit("Evaluation seeds JSON", "[0, 101]");
  expect(save()).toBeEnabled();
});

it("keeps invalid JSON drafts local and restores validity when corrected", () => {
  render(<Harness />);
  edit("Evaluation seeds JSON", "[100,");
  expect(screen.getByLabelText("Evaluation seeds JSON")).toHaveValue("[100,");
  expect(current().parameters.evaluation).toBeUndefined();
  expect(save()).toBeDisabled();
  edit("Policy training steps", "10");
  expect(screen.getByLabelText("Evaluation seeds JSON")).toHaveValue("[100,");
  edit("Evaluation seeds JSON", "[100,101]");
  expect(save()).toBeEnabled();
});

it.each([
  { environment: { kind: "control", runtime: "imaginary" } },
  { environment: { kind: "physical", runtime: "native" } },
  { evaluation: { seeds: [100], vendor: true } },
  { algorithm: "dqn" },
])(
  "preserves and reports invalid external retained configuration %j",
  (parameters) => {
    const initial = makeDefinition("learning.control_rl");
    Object.assign(initial.parameters, parameters);
    render(<Harness initial={initial} />);
    expect(save()).toBeDisabled();
    expect(current().parameters).toEqual(initial.parameters);
    expect(screen.getByRole("alert")).toBeVisible();
  },
);

it("validates external selectors and restores parent validity on unmount without rerender loops", () => {
  const initial = makeDefinition("learning.control_rl");
  initial.learning!.objective = "unavailable";
  const change = vi.fn(),
    valid = vi.fn();
  const view = render(
    <ControlLearning
      definition={initial}
      onChange={change}
      onValidityChange={valid}
    />,
  );
  expect(valid).toHaveBeenLastCalledWith(false);
  expect(
    screen.getByRole("button", {
      name: /unavailable · retained selection Control objective$/,
    }),
  ).toBeVisible();
  view.rerender(
    <ControlLearning
      definition={initial}
      onChange={change}
      onValidityChange={valid}
    />,
  );
  expect(change).not.toHaveBeenCalled();
  expect(valid).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(valid).toHaveBeenLastCalledWith(true);
});

it("reports contradictory checkpoint bindings while allowing unattached resume authoring", async () => {
  const initial = makeDefinition("learning.control_rl");
  initial.inputs = [
    { artifact_id: "saved", sha256: "a".repeat(64), role: "resume" },
  ];
  render(<Harness initial={initial} />);
  expect(save()).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "resume input conflicts with scratch",
  );
  expect(current().inputs).toEqual(initial.inputs);
  await userEvent.click(screen.getByRole("button", { name: "Detach inputs" }));
  expect(save()).toBeEnabled();
  cleanup();
  initial.learning!.initialization = "resume";
  initial.inputs = [];
  render(<Harness initial={initial} />);
  expect(save()).toBeEnabled();
});

it("requires explicit correction of demonstrations when switching from DQN to PPO", async () => {
  const initial = makeDefinition("learning.control_rl");
  initial.learning!.objective = "dqn";
  initial.inputs = [
    { artifact_id: "data", sha256: "b".repeat(64), role: "demonstrations" },
  ];
  render(<Harness initial={initial} />);
  expect(save()).toBeEnabled();
  await select("Control objective", "PPO · environment interactions");
  expect(save()).toBeDisabled();
  expect(current().inputs).toEqual(initial.inputs);
  expect(screen.getByRole("alert")).toHaveTextContent(
    "demonstrations input conflicts with PPO",
  );
  await select("Control objective", "Offline DQN · retained transitions");
  expect(save()).toBeEnabled();
  await select("Control objective", "PPO · environment interactions");
  await userEvent.click(screen.getByRole("button", { name: "Detach inputs" }));
  expect(save()).toBeEnabled();
});

it.each([
  { kind: "control", runtime: { kind: "native" } },
  { kind: "control", runtime: null },
  { kind: { kind: "control" }, runtime: "native" },
  { kind: null, runtime: "native" },
])(
  "preserves malformed environment literals and recovers through explicit selection %j",
  async (environment) => {
    const initial = makeDefinition("learning.control_rl");
    initial.parameters.environment = environment;
    render(<Harness initial={initial} />);
    expect(save()).toBeDisabled();
    expect(current().parameters.environment).toEqual(environment);
    if (typeof environment.runtime !== "string")
      expect(
        screen.getByRole("button", {
          name: /retained selection Environment runtime$/,
        }),
      ).toBeVisible();
    if (typeof environment.runtime === "string")
      await select("Environment runtime", "TorchRL EnvBase");
    await select("Environment runtime", "Native control environment");
    expect(current().parameters.environment).toEqual({
      kind: "control",
      runtime: "native",
    });
    expect(save()).toBeEnabled();
  },
);

it("accepts the largest representable PPO epsilon below one and rejects one", () => {
  render(<Harness />);
  edit("PPO clipping epsilon", "0.9999999999999999");
  expect(save()).toBeEnabled();
  expect(current().parameters.clip_epsilon).toBe(0.9999999999999999);
  edit("PPO clipping epsilon", "1");
  expect(save()).toBeDisabled();
  edit("PPO clipping epsilon", "0.9999999999999999");
  expect(save()).toBeEnabled();
});

it("preserves evaluator-only validation bindings and requires correction for PPO", async () => {
  const initial = makeDefinition("learning.control_rl");
  initial.learning!.objective = "dqn";
  initial.inputs = [
    {
      artifact_id: "validation",
      sha256: "c".repeat(64),
      role: "validation",
      visibility: "evaluation",
    },
  ];
  render(<Harness initial={initial} />);
  expect(save()).toBeEnabled();
  await select("Control objective", "PPO · environment interactions");
  expect(save()).toBeDisabled();
  expect(current().inputs).toEqual(initial.inputs);
  expect(screen.getByRole("alert")).toHaveTextContent(
    "validation input conflicts with PPO independent environment evaluation",
  );
  await select("Control objective", "Offline DQN · retained transitions");
  expect(save()).toBeEnabled();
  await select("Control objective", "PPO · environment interactions");
  await userEvent.click(screen.getByRole("button", { name: "Detach inputs" }));
  expect(save()).toBeEnabled();
});

it.each(["device", "dtype", "training_dtype"])(
  "rejects malformed %s selectors in either container and accepts explicit corrections",
  (key) => {
    for (const container of ["learning", "parameters"] as const) {
      const initial = makeDefinition("learning.control_rl");
      const supported = key === "device" ? "cpu" : "float32";
      initial[container]![key] = { kind: [supported] };
      const valid = vi.fn(),
        change = vi.fn();
      const view = render(
        <ControlLearning
          definition={initial}
          onChange={change}
          onValidityChange={valid}
        />,
      );
      expect(valid).toHaveBeenLastCalledWith(false);
      expect(screen.getByRole("alert")).toHaveTextContent(
        `Retained ${key} is unavailable`,
      );
      expect(initial[container]![key]).toEqual({ kind: [supported] });
      expect(change).not.toHaveBeenCalled();
      const corrected = {
        ...initial,
        [container]: { ...initial[container], [key]: { kind: supported } },
      };
      view.rerender(
        <ControlLearning
          definition={corrected}
          onChange={change}
          onValidityChange={valid}
        />,
      );
      expect(valid).toHaveBeenLastCalledWith(true);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      view.unmount();
    }
  },
);
