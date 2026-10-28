export interface InputBinding {
  artifact_id: string;
  sha256: string;
  role?: string;
  visibility?: "model" | "evaluation" | "truth";
}
export interface LabComponent {
  id: string;
  kind?: string;
  version?: string;
  label?: string;
  type?: string;
  period?: number;
  position?: { x: number; y: number };
  parameters?: Record<string, unknown>;
}
export interface LabConnection {
  id?: string;
  source: string;
  target: string;
  port?: string;
  target_port?: string;
  version?: string;
  kind?: string;
}
export interface ExperimentDefinition {
  schema_version: 1;
  name: string;
  domain: string;
  engine: string;
  seed: number;
  mode: string;
  operating_mode?: string;
  parameters: Record<string, unknown>;
  pipeline: unknown[];
  inputs: InputBinding[];
  components: LabComponent[];
  connections: LabConnection[];
  learning: Record<string, any> | null;
  execution: {
    profile: string;
    backend?: string;
    max_seconds?: number;
    max_output_bytes?: number;
    [key: string]: unknown;
  };
  objectives: Record<string, unknown>;
  clocks?: Record<string, unknown>;
  scenarios?: unknown[];
  retrieval?: Record<string, unknown> | null;
}
export interface SavedDefinition {
  id: string;
  name: string;
  revision: number;
  definition: ExperimentDefinition;
  created?: string;
}
export interface LabRun {
  id: string;
  definition_id: string;
  state: string;
  target_id?: string;
  created?: string;
  spec_digest?: string;
  spec?: { schedule?: { not_before?: string | null } };
  result?: unknown;
  error?: unknown;
  attempts?: unknown[];
}
export interface InputExpectation {
  role: string;
  description: string;
  required?: boolean;
}
interface Template {
  id: string;
  name: string;
  domain: string;
  parameters: Record<string, unknown>;
  inputs?: InputExpectation[];
  learning?: Record<string, unknown>;
  hint?: string;
}
const input = (
  role: string,
  description: string,
  required = true,
): InputExpectation => ({ role, description, required });
export const templates: Template[] = [
  {
    id: "synthetic.signal",
    name: "Synthetic signal",
    domain: "signals",
    parameters: {
      samples: 1000,
      dt: 0.01,
      frequency: 1,
      amplitude: 1,
      waveform: "sine",
      noise: 0,
    },
  },
  {
    id: "recorded.replay",
    name: "Recorded replay",
    domain: "recorded",
    parameters: {},
    inputs: [input("source", "JSONL with time, value and episode_id")],
  },
  {
    id: "simulation.plant",
    name: "First-order closed-loop plant",
    domain: "control",
    parameters: {
      samples: 1000,
      dt: 0.01,
      time_constant: 1,
      plant_gain: 1,
      controller_gain: 1,
      setpoint: 1,
      initial_state: 0,
    },
  },
  {
    id: "system.cosim",
    name: "Composed signal / controller / plant",
    domain: "control",
    parameters: {},
    hint: "Built-in scalar components execute on explicit clocks. Positive transport delay breaks feedback; algebraic loops require a supported solver or explicit delay.",
  },
  {
    id: "learning.language",
    name: "Language learning from scratch",
    domain: "learning",
    parameters: {
      texts: [
        "The system responds to an applied input.",
        "A controller adjusts the input from measured feedback.",
      ],
      steps: 10,
      learning_rate: 0.003,
    },
    learning: {
      initialization: "scratch",
      family: "transformer",
      objective: "causal_lm",
      representation: "byte",
      update_strategy: "adamw",
      architecture: { hidden_size: 32, layers: 1, heads: 2, context: 32 },
    },
    inputs: [
      input(
        "source",
        "Text or message records; optional when the corpus is authored inline",
        false,
      ),
      input(
        "validation",
        "Held-out validation records; evaluation-only and excluded from updates",
        false,
      ),
      input(
        "initial_weights",
        "Saved language checkpoint; inherit weights and tokenizer with a fresh optimizer",
        false,
      ),
      input(
        "resume",
        "Exact continuation checkpoint; identical corpus, split and configuration",
        false,
      ),
      input(
        "teacher",
        "Frozen teacher checkpoint with a compatible tokenizer and context",
        false,
      ),
    ],
  },
  {
    id: "learning.pretrained",
    name: "Pretrained full / LoRA / QLoRA / DPO",
    domain: "learning",
    parameters: {
      steps: 8,
      validation_fraction: 0.2,
      batch_size: 1,
      learning_rate: 0.0002,
    },
    learning: {
      initialization: "pretrained",
      objective: "sft",
      update_strategy: "full",
      architecture: { context: 128 },
    },
    inputs: [
      input(
        "pretrained_model",
        "Local model/tokenizer archive; no remote model download",
      ),
      input(
        "snapshot",
        "JSON/JSONL or Parquet: exact text for continued pretraining; messages for SFT; chosen/rejected responses for DPO",
      ),
    ],
    hint: "Full, LoRA, QLoRA and DPO use this same engine. Hardware and optional trainer dependencies are checked on the selected target.",
  },
  {
    id: "learning.language_rl",
    name: "Autoregressive language PPO / GRPO",
    domain: "learning",
    parameters: {
      steps: 8,
      group_size: 8,
      lr: 0.003,
      tasks: [{ operation: "add", left: 2, right: 3 }],
      heldout_tasks: [{ operation: "add", left: 5, right: 2 }],
    },
    learning: {
      initialization: "scratch",
      family: "gru",
      objective: "grpo",
      architecture: { hidden_size: 16, layers: 1 },
    },
    inputs: [
      input("resume", "Optional matching language RL checkpoint", false),
    ],
  },
  {
    id: "learning.control_rl",
    name: "Control policy learning",
    domain: "control",
    parameters: {
      steps: 8,
      batch_size: 16,
      agents: 1,
      horizon: 24,
      target: 0,
      lr: 0.003,
    },
    learning: {
      initialization: "scratch",
      objective: "ppo",
      architecture: { hidden_size: 16 },
    },
    inputs: [
      input(
        "demonstrations",
        "Required for behavioral cloning / offline DQN; transition JSONL",
        false,
      ),
      input("resume", "Optional compatible policy checkpoint", false),
    ],
  },
  {
    id: "learning.tool_rl",
    name: "Categorical tool-bandit policy",
    domain: "learning",
    parameters: { steps: 8, batch_size: 16 },
    learning: {
      initialization: "scratch",
      objective: "grpo_tool_bandit",
      architecture: { hidden_size: 16 },
    },
  },
  {
    id: "learning.timeseries",
    name: "Time-series learning",
    domain: "learning",
    parameters: { steps: 10 },
    inputs: [
      input("source", "Numeric time-series records with grouped episodes"),
    ],
  },
  {
    id: "learning.multimodal",
    name: "Multimodal learning",
    domain: "learning",
    parameters: { steps: 10 },
    inputs: [input("source", "Paired numeric / text records")],
  },
  {
    id: "retrieval.index",
    name: "Lexical / semantic / hybrid retrieval",
    domain: "retrieval",
    parameters: { mode: "lexical", queries: ["pressure"], top_k: 5 },
    inputs: [
      input("source", "Document, structured or episode JSON/JSONL evidence"),
      input(
        "embedding_model",
        "Required only for semantic/hybrid; local encoder archive",
        false,
      ),
    ],
  },
  {
    id: "data.pipeline",
    name: "Scientific data preparation",
    domain: "data",
    parameters: {},
    inputs: [
      input(
        "source",
        "Timestamped records with units, observed state and separable episodes",
      ),
    ],
    hint: "Preparation does not request dataset splits. Opt in to grouped or qualified temporal partitions; fitted transformations require training data.",
  },
  {
    id: "evaluation.compare",
    name: "Independent evaluation / paired comparison",
    domain: "evaluation",
    parameters: {},
    inputs: [
      input(
        "examples",
        "Independently specified expected/actual evaluation examples",
      ),
      input("candidate", "Candidate paired records", false),
      input("baseline", "Matching baseline paired records", false),
      input("provenance", "Retained data lineage", false),
    ],
  },
  {
    id: "deployment.benchmark",
    name: "Measured target benchmark / package",
    domain: "deployment",
    parameters: {
      target: "desktop.cpu",
      warmup: 2,
      repetitions: 10,
      max_new_tokens: 16,
      threads: 1,
      prompts: ["pressure"],
    },
    inputs: [
      input(
        "model",
        "One compatible model archive or checkpoint; measurements concern the executing host",
      ),
    ],
    hint: "Selecting a deployment target does not establish device qualification.",
  },
  {
    id: "simulation.simpy",
    name: "SimPy queue",
    domain: "discrete_event",
    parameters: { jobs: 10, arrival_interval: 1, service_time: 2, capacity: 1 },
  },
  {
    id: "optimization.ortools",
    name: "OR-Tools knapsack",
    domain: "optimization",
    parameters: { weights: [2, 3, 4], values: [3, 4, 5], capacity: 5 },
  },
  {
    id: "testing.hypothesis",
    name: "Hypothesis bounded generation",
    domain: "testing",
    parameters: { samples: 100, minimum: -100, maximum: 100 },
  },
  {
    id: "protocol.modbus",
    name: "Modbus offline packet fixture",
    domain: "protocols",
    parameters: { registers: [0, 109, 65535] },
    hint: "Offline packet encoding only; no physical endpoint is connected.",
  },
  {
    id: "protocol.mqtt",
    name: "MQTT offline packet fixture",
    domain: "protocols",
    parameters: { topic: "lab/value", payload: "42" },
    hint: "Offline QoS0 packet encoding only; no broker is connected.",
  },
  {
    id: "simulation.boptest",
    name: "BOPTEST local simulator",
    domain: "buildings",
    parameters: {
      base_url: "http://127.0.0.1:8000",
      testcase: "bestest_hydronic",
      steps: 10,
      step: 300,
    },
    hint: "Requires a separately provisioned loopback BOPTEST service on the execution target.",
  },
  {
    id: "simulation.ngspice",
    name: "ngspice divider",
    domain: "circuits",
    parameters: { voltage: 10, resistance: 1000 },
  },
  {
    id: "embedded.tinygo",
    name: "TinyGo firmware build",
    domain: "embedded",
    parameters: { target: "wasm" },
    inputs: [input("source", "Go source; compiler and native grant required")],
  },
  {
    id: "embedded.wokwi_tinygo",
    name: "Wokwi CI",
    domain: "embedded",
    parameters: { expect_text: "Hello", simulation_ms: 1000 },
    inputs: [
      input("firmware", "Compiled ELF firmware"),
      input("diagram", "Wokwi diagram JSON"),
    ],
    hint: "Requires operator-granted cloud simulation, native execution and a runner-owned Wokwi token.",
  },
  {
    id: "embedded.renode",
    name: "Renode firmware simulation",
    domain: "embedded",
    parameters: { simulation_seconds: 1, uart: "sysbus.uart" },
    inputs: [
      input("firmware", "ELF firmware"),
      input("platform", "Matching REPL platform description"),
    ],
  },
  {
    id: "embedded.qemu",
    name: "QEMU BIOS serial capture",
    domain: "embedded",
    parameters: { capture_seconds: 1 },
    inputs: [
      input("firmware", "x86 PC BIOS image; no guest network or host monitor"),
    ],
  },
  {
    id: "simulation.verilator",
    name: "Verilator build and run",
    domain: "digital",
    parameters: { top_module: "testbench" },
    inputs: [
      input(
        "source",
        "Self-terminating SystemVerilog testbench; omit inputs only for catalog lint fixture",
      ),
    ],
  },
  {
    id: "simulation.energyplus",
    name: "EnergyPlus weather/model simulation",
    domain: "buildings",
    parameters: {},
    inputs: [
      input("model", "IDF model requesting CSV observations"),
      input("weather", "EPW weather file"),
    ],
  },
  {
    id: "simulation.fmi",
    name: "FMI uploaded FMU",
    domain: "industrial",
    parameters: { output: "x", duration: 1, samples: 100 },
    inputs: [input("fmu", "Platform-compatible FMU with named numeric output")],
    hint: "FMPy executes the uploaded FMU; OpenModelica source compilation is a separate engine.",
  },
  {
    id: "simulation.openmodelica",
    name: "OpenModelica source simulation",
    domain: "industrial",
    parameters: {
      model: "Decay",
      output: "x",
      stop_time: 1,
      samples: 101,
      tolerance: 1e-6,
      solver: "dassl",
      overrides: { k: { type: "real", value: 1 } },
    },
    inputs: [
      input("model", "Immutable .mo source defining the selected class"),
    ],
  },
  {
    id: "simulation.pandapower",
    name: "pandapower two-bus power flow",
    domain: "energy",
    parameters: {},
    inputs: [
      input(
        "network",
        "Optional typed two-bus network JSON; default fixture otherwise",
        false,
      ),
    ],
  },
  {
    id: "simulation.pybamm",
    name: "PyBaMM single-particle battery",
    domain: "batteries",
    parameters: { duration: 60, samples: 20 },
    inputs: [
      input(
        "parameters",
        "Optional JSON constant current / temperature values",
        false,
      ),
    ],
  },
  {
    id: "network.mininet",
    name: "Mininet isolated network fixture",
    domain: "networks",
    parameters: {},
    hint: "Requires Linux, explicit native and isolated-network-namespace grants; no physical network wiring.",
  },
  {
    id: "network.ns3",
    name: "ns-3 prebuilt first example",
    domain: "networks",
    parameters: {},
    hint: "Requires the prebuilt Linux ns-3 fixture and native grant.",
  },
  {
    id: "robotics.gazebo",
    name: "Gazebo headless world",
    domain: "robotics",
    parameters: { iterations: 100 },
    inputs: [
      input(
        "world",
        "Optional local SDF world; built-in bounded world otherwise",
        false,
      ),
    ],
  },
  {
    id: "simulation.matlab",
    name: "Licensed MATLAB / Simulink",
    domain: "industrial",
    parameters: { duration: 1 },
    inputs: [input("model", "Local SLX model")],
    hint: "Requires licensed MATLAB and separate runner-owned native/license grants.",
  },
  {
    id: "environment.custom",
    name: "Custom environment",
    domain: "custom",
    parameters: {},
    hint: "No arbitrary custom-code adapter is assumed. Inspect target capability before execution.",
  },
];
export function inputExpectations(
  definition: ExperimentDefinition,
): InputExpectation[] {
  const rows = templates.find((t) => t.id === definition.engine)?.inputs ?? [];
  const selected = (value: unknown) =>
    value && typeof value === "object" && "kind" in value
      ? String(value.kind)
      : String(value ?? "");
  return rows.map((row) => ({
    ...row,
    required:
      row.required ||
      (definition.engine === "learning.language" &&
        ((selected(definition.learning?.initialization) === "weights" &&
          row.role === "initial_weights") ||
          (selected(definition.learning?.initialization) === "resume" &&
            row.role === "resume") ||
          (selected(definition.learning?.objective) === "distillation" &&
            row.role === "teacher"))) ||
      (definition.engine === "retrieval.index" &&
        ["semantic", "hybrid"].includes(String(definition.parameters.mode)) &&
        row.role === "embedding_model") ||
      (definition.engine === "learning.control_rl" &&
        ["dqn", "behavioral_cloning"].includes(
          String(definition.learning?.objective),
        ) &&
        row.role === "demonstrations"),
  }));
}
export function defaultVisibility(
  role: string,
): "model" | "evaluation" | "truth" {
  return ["truth", "ground_truth", "final_test"].includes(role)
    ? "truth"
    : [
          "examples",
          "candidate",
          "baseline",
          "provenance",
          "evaluation",
          "validation",
        ].includes(role)
      ? "evaluation"
      : "model";
}
export function makeDefinition(
  engine = "synthetic.signal",
): ExperimentDefinition {
  const template = templates.find((t) => t.id === engine);
  const result: ExperimentDefinition = {
    schema_version: 1,
    name: template?.name ?? engine,
    domain: template?.domain ?? "custom",
    engine,
    seed: 42,
    mode: "offline",
    operating_mode: engine === "recorded.replay" ? "replay" : "simulation",
    parameters: structuredClone(template?.parameters ?? {}),
    pipeline: [],
    inputs: [],
    components: [],
    connections: [],
    learning: structuredClone(template?.learning ?? null),
    execution: {
      profile: "trusted_native",
      backend: "native",
      max_seconds: 300,
      max_output_bytes: 536870912,
    },
    objectives: {},
    clocks: {},
    scenarios: [],
  };
  if (engine === "system.cosim") {
    result.clocks = {
      step: 0.1,
      steps: 100,
      hold: "zero_order",
      tolerance: 1e-9,
    };
    result.components = [
      {
        id: "reference",
        kind: "signal",
        version: "1",
        parameters: { waveform: "constant", amplitude: 1 },
      },
      {
        id: "plant",
        kind: "plant",
        version: "1",
        parameters: { time_constant: 1, gain: 1, initial_state: 0 },
      },
      {
        id: "sensor",
        kind: "sensor",
        version: "1",
        parameters: { bias: 0, noise_std: 0 },
      },
      {
        id: "transport",
        kind: "transport",
        version: "1",
        parameters: { delay_steps: 1, initial_value: 0 },
      },
      {
        id: "controller",
        kind: "controller",
        version: "1",
        parameters: { gain: 1 },
      },
    ];
    result.connections = [
      { source: "reference", target: "controller", port: "reference" },
      { source: "plant", target: "sensor", port: "input" },
      { source: "sensor", target: "transport", port: "input" },
      { source: "transport", target: "controller", port: "measurement" },
      { source: "controller", target: "plant", port: "input" },
    ].map((c) => ({ ...c, version: "1" }));
  }
  return result;
}
export function parseDefinition(text: string): ExperimentDefinition {
  const value = JSON.parse(text);
  const object = (item: unknown): item is Record<string, any> =>
    !!item && typeof item === "object" && !Array.isArray(item);
  if (
    !object(value) ||
    value.schema_version !== 1 ||
    typeof value.name !== "string" ||
    !value.name.trim() ||
    typeof value.engine !== "string" ||
    !value.engine.trim() ||
    !object(value.parameters)
  )
    throw new Error(
      "A definition needs schema_version 1, a name, an engine, and a parameters object.",
    );
  if (
    typeof value.domain !== "string" ||
    !Number.isInteger(value.seed) ||
    value.seed < 0 ||
    value.seed > 2 ** 32 - 1 ||
    !["offline", "online"].includes(value.mode)
  )
    throw new Error(
      "A definition needs a domain, bounded integer seed and offline/online mode.",
    );
  if (
    !Array.isArray(value.inputs) ||
    !Array.isArray(value.pipeline) ||
    !Array.isArray(value.components) ||
    !Array.isArray(value.connections) ||
    !object(value.execution) ||
    typeof value.execution.profile !== "string" ||
    !value.execution.profile
  )
    throw new Error(
      "A definition needs input/component/connection arrays and an explicit execution profile.",
    );
  for (const ref of value.inputs) {
    if (
      !object(ref) ||
      typeof ref.artifact_id !== "string" ||
      !ref.artifact_id ||
      typeof ref.sha256 !== "string" ||
      !ref.sha256 ||
      (ref.role !== undefined && typeof ref.role !== "string") ||
      (ref.visibility !== undefined &&
        !["model", "evaluation", "truth"].includes(ref.visibility))
    )
      throw new Error(
        "Inputs require an immutable artifact identity, checksum, role and valid visibility.",
      );
    if (
      ["truth", "ground_truth", "final_test"].includes(ref.role) &&
      (ref.visibility ?? "model") === "model"
    )
      throw new Error(
        "Truth and final-test artifacts require evaluation or truth visibility.",
      );
  }
  if (
    new Set(value.inputs.map((ref) => ref.artifact_id)).size !==
    value.inputs.length
  )
    throw new Error("Bind each artifact once with an explicit role.");
  if (
    value.components.some(
      (c) =>
        !object(c) ||
        typeof c.id !== "string" ||
        !c.id ||
        (c.parameters !== undefined && !object(c.parameters)),
    )
  )
    throw new Error("Components require identities and parameter objects.");
  const ids = new Set(value.components.map((c) => c.id));
  if (
    ids.size !== value.components.length ||
    value.connections.some(
      (c) =>
        !object(c) ||
        typeof c.source !== "string" ||
        typeof c.target !== "string" ||
        !ids.has(c.source) ||
        !ids.has(c.target),
    )
  )
    throw new Error("Connections require existing unique component endpoints.");
  if (
    value.learning !== null &&
    value.learning !== undefined &&
    !object(value.learning)
  )
    throw new Error("Learning configuration must be an object or null.");
  for (const key of ["max_seconds", "max_output_bytes"])
    if (
      value.execution[key] !== undefined &&
      (!Number.isInteger(value.execution[key]) || value.execution[key] <= 0)
    )
      throw new Error("Execution budgets must be positive integers.");
  return value as ExperimentDefinition;
}
export const isTerminal = (state: string) =>
  [
    "succeeded",
    "completed",
    "failed",
    "cancelled",
    "canceled",
    "lost",
    "recovery_required",
  ].includes(state);
