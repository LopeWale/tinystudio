import { useEffect, useRef, useState } from "react";
import { SettingsSection } from "../../components/studio/settings-section";
import { WorkspaceDisclosure } from "../../components/studio/workspace";
import { DraftField } from "./data-preparation";
import { LabChoice } from "./inputs";
import {
  normalizeControlObjective as normalizeObjective,
  readLearningSelector,
  type ExperimentDefinition,
} from "./definition";

type Options = Record<string, unknown>;
const object = (value: unknown): Options =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Options)
    : {};
const selected = (value: unknown, fallback = "") =>
  value === undefined
    ? fallback
    : typeof value === "string"
      ? value
      : String(object(value).kind ?? JSON.stringify(value));
const changeSelector = (previous: unknown, kind: string) =>
  previous && typeof previous === "object" && !Array.isArray(previous)
    ? { ...object(previous), kind }
    : kind;
const provided = (value: unknown, fallback: unknown) =>
  value === undefined ? fallback : value;
const isObject = (value: unknown) =>
  !!value && typeof value === "object" && !Array.isArray(value);

/** Edits supported control axes without discarding retained configuration. */
export function ControlLearning({
  definition,
  onChange,
  onValidityChange,
}: {
  definition: ExperimentDefinition;
  onChange: (patch: Partial<ExperimentDefinition>) => void;
  onValidityChange?: (valid: boolean) => void;
}) {
  const p = definition.parameters,
    l = definition.learning ?? {};
  const architecture = object(l.architecture),
    reward = object(p.reward),
    environment = object(p.environment),
    evaluation = object(p.evaluation);
  const objective = normalizeObjective(
    selected(l.objective ?? p.objective ?? p.algorithm, "ppo"),
  );
  const runtime =
    environment.runtime === undefined
      ? "native"
      : typeof environment.runtime === "string"
        ? environment.runtime
        : JSON.stringify(environment.runtime);
  const offline = ["dqn", "behavioral_cloning"].includes(objective);
  const [invalid, setInvalid] = useState<Set<string>>(() => new Set());
  const callback = useRef(onValidityChange);
  callback.current = onValidityChange;
  const [report] = useState(
    () => (key: string, valid: boolean) =>
      setInvalid((current) => {
        if (current.has(key) === !valid) return current;
        const next = new Set(current);
        if (valid) next.delete(key);
        else next.add(key);
        return next;
      }),
  );
  const patch = (key: string, value: unknown) =>
    onChange({ parameters: { ...p, [key]: value } });
  const dimension = (key: string, fallback: number) =>
    provided(architecture[key], provided(p[key], fallback));
  const chooseObjective = (value: string) => {
    const parameters = { ...p };
    for (const key of ["objective", "algorithm"])
      if (key in p) parameters[key] = changeSelector(p[key], value);
    onChange({
      learning: { ...l, objective: changeSelector(l.objective, value) },
      parameters,
    });
  };
  const chooseDimension = (key: string, value: unknown) => {
    const parameters = { ...p };
    if (key in p) parameters[key] = value;
    onChange({
      learning: { ...l, architecture: { ...architecture, [key]: value } },
      parameters,
    });
  };
  const seeds = provided(evaluation.seeds, [
    definition.seed + 1000003,
    definition.seed + 1000004,
  ]);
  const conflicts: string[] = [];
  const conflict = (message: string) => conflicts.push(message);
  const checkAxis = (key: string, supported: string[], fallback: string) => {
    for (const [path, container] of [
      ["learning", l],
      ["parameters", p],
    ] as const) {
      if (!(key in container)) continue;
      const value = container[key];
      if (
        (typeof value !== "string" &&
          (!isObject(value) ||
            Object.keys(object(value)).length !== 1 ||
            typeof object(value).kind !== "string")) ||
        !supported.includes(selected(value))
      )
        conflict(
          `Retained ${path}.${key} is unsupported. Review Definition JSON or select a supported value.`,
        );
    }
    if (key in l && key in p && selected(l[key]) !== selected(p[key]))
      conflict(
        `Conflicting learning.${key} and parameters.${key}. Reconcile in Definition JSON.`,
      );
    return selected(l[key] ?? p[key], fallback);
  };
  for (const key of Object.keys(l))
    if (
      ![
        "family",
        "initialization",
        "architecture",
        "representation",
        "objective",
        "update_strategy",
        "device",
        "dtype",
        "training_dtype",
      ].includes(key)
    )
      conflict(
        `Retained learning.${key} is unsupported by control learning. Review Definition JSON.`,
      );
  for (const key of ["device", "dtype", "training_dtype"]) {
    const values = [l[key], p[key]]
      .filter((value) => value !== undefined)
      .map((value) => readLearningSelector(value)?.toLowerCase());
    const expected = key === "device" ? ["cpu"] : ["float32", "fp32"];
    if (
      values.some((value) => value === undefined || !expected.includes(value))
    )
      conflict(
        `Retained ${key} is unavailable: this control path uses CPU / float32. Review Definition JSON.`,
      );
  }
  checkAxis("family", ["mlp"], "mlp");
  checkAxis("representation", ["numeric", "state_vector"], "numeric");
  const initialization = checkAxis(
    "initialization",
    ["scratch", "from_scratch", "random", "resume"],
    "scratch",
  );
  checkAxis(
    "update_strategy",
    ["full", "adam", "full_parameter", "full_finetune"],
    "full",
  );
  if (!["ppo", "dqn", "behavioral_cloning"].includes(objective))
    conflict(
      `Retained control objective ${objective} is unsupported by this editor. Choose PPO, offline DQN or behavioral cloning.`,
    );
  const checkpointInputs = definition.inputs.filter((input) =>
    ["resume", "initial_weights"].includes(input.role ?? ""),
  );
  for (const input of checkpointInputs) {
    if (initialization !== "resume" || input.role !== "resume")
      conflict(
        `Retained ${input.role} input conflicts with ${initialization} initialization. Detach it in Inputs or restore matching initialization in Definition JSON.`,
      );
    if ((input.visibility ?? "model") !== "model")
      conflict(
        `${input.role} requires a model-visible binding. Correct its visibility in Inputs.`,
      );
  }
  if (checkpointInputs.filter((input) => input.role === "resume").length > 1)
    conflict(
      "Exactly one resume input is supported. Detach duplicate bindings in Inputs.",
    );
  for (const input of definition.inputs) {
    if (input.role === "validation") {
      if (input.visibility !== "evaluation")
        conflict(
          "The retained validation input requires evaluation visibility. Correct its visibility in Inputs.",
        );
      if (!offline)
        conflict(
          "Retained validation input conflicts with PPO independent environment evaluation. Detach it in Inputs or choose an offline objective.",
        );
    }
    if (
      [
        "teacher",
        "pretrained_model",
        "embedding_model",
        "retrieval_index",
      ].includes(input.role ?? "")
    )
      conflict(
        `Retained ${input.role} input is unused by this control policy. Detach it or change its role in Inputs.`,
      );
    else if (
      !offline &&
      input.role !== "resume" &&
      (input.visibility ?? "model") === "model"
    )
      conflict(
        `Retained ${input.role ?? "source"} input conflicts with PPO environment learning. Detach model-visible corpus inputs in Inputs or choose an offline objective.`,
      );
  }
  const objectiveAxes: string[] = [];
  for (const [path, value] of [
    ["learning.objective", l.objective],
    ["parameters.objective", p.objective],
    ["parameters.algorithm", p.algorithm],
  ] as const) {
    if (value === undefined) continue;
    if (
      typeof value !== "string" &&
      (!isObject(value) ||
        Object.keys(object(value)).length !== 1 ||
        typeof object(value).kind !== "string")
    )
      conflict(
        `Retained ${path} must name only a supported kind. Review Definition JSON.`,
      );
    objectiveAxes.push(normalizeObjective(selected(value)));
  }
  // learning.objective and parameters.objective must match before algorithm alias normalization.
  if (
    l.objective !== undefined &&
    p.objective !== undefined &&
    selected(l.objective) !== selected(p.objective)
  )
    conflict(
      "Conflicting learning.objective and parameters.objective. Select an objective to reconcile the owned aliases.",
    );
  if (new Set(objectiveAxes).size > 1)
    conflict(
      "Conflicting objective and algorithm axes. Select an objective to reconcile the owned aliases.",
    );
  if (
    p.environment !== undefined &&
    (!isObject(p.environment) ||
      Object.keys(environment).some(
        (key) => !["kind", "runtime"].includes(key),
      ) ||
      (environment.kind !== undefined && environment.kind !== "control") ||
      (environment.runtime !== undefined &&
        (typeof environment.runtime !== "string" ||
          !["native", "torchrl"].includes(environment.runtime))))
  )
    conflict(
      "Retained environment is unsupported. Select native or TorchRL; review any extra environment fields in Definition JSON.",
    );
  if (
    l.architecture !== undefined &&
    l.architecture !== null &&
    !isObject(l.architecture)
  )
    conflict(
      "learning.architecture must be an object. Review Definition JSON.",
    );
  for (const key of Object.keys(architecture))
    if (!["hidden_size", "layers"].includes(key))
      conflict(
        `Retained architecture.${key} is unsupported by the control policy. Review Definition JSON.`,
      );
  for (const key of [
    "heads",
    "context",
    "input_size",
    "output_size",
    "dropout",
  ])
    if (key in p)
      conflict(
        `Retained parameters.${key} is unsupported by the control policy. Review Definition JSON.`,
      );
  for (const key of ["hidden_size", "layers"])
    if (key in architecture && key in p && architecture[key] !== p[key])
      conflict(
        `Conflicting architecture.${key} and parameters.${key}. Edit the dimension to reconcile the owned aliases.`,
      );
  if (p.reward !== undefined && !isObject(p.reward))
    conflict("Control reward must be an object. Review Definition JSON.");
  if (
    p.evaluation !== undefined &&
    (!isObject(p.evaluation) ||
      Object.keys(evaluation).some((key) => key !== "seeds"))
  )
    conflict(
      "Control evaluation accepts only seeds. Review retained evaluation fields in Definition JSON.",
    );
  if (
    !Array.isArray(seeds) ||
    seeds.length < 1 ||
    seeds.length > 64 ||
    seeds.some(
      (seed) => typeof seed !== "number" || !Number.isInteger(seed) || seed < 0,
    ) ||
    new Set(seeds).size !== seeds.length
  )
    conflict("Evaluation seeds must be one to 64 unique nonnegative integers.");
  else if (seeds.includes(definition.seed))
    conflict("Evaluation seeds must exclude the training seed.");
  if (
    p.lr !== undefined &&
    p.learning_rate !== undefined &&
    p.lr !== p.learning_rate
  )
    conflict(
      "Conflicting lr and learning_rate. Edit Learning rate to reconcile the owned aliases.",
    );
  const ranges: [string, unknown, number, number, boolean][] = [
    ["target", provided(p.target, 0), -2, 2, false],
    ["horizon", provided(p.horizon, 24), 1, 10000, true],
    ["agents", provided(p.agents, 1), 1, 8, true],
    ["hidden_size", dimension("hidden_size", 16), 4, 512, true],
    ["layers", dimension("layers", 1), 1, 8, true],
    ["steps", provided(p.steps, 8), 1, 10000, true],
    ["batch_size", provided(p.batch_size, 16), 2, 4096, true],
    ["update_epochs", provided(p.update_epochs, 2), 1, 20, true],
    [
      "learning_rate",
      provided(p.lr, provided(p.learning_rate, 0.003)),
      Number.MIN_VALUE,
      1,
      false,
    ],
    [
      "distance_weight",
      provided(reward.distance_weight, 1),
      0,
      Infinity,
      false,
    ],
    ["action_cost", provided(reward.action_cost, 0.01), 0, Infinity, false],
    ["gamma", provided(p.gamma, 0.95), 0, 1, false],
    ["clip_epsilon", provided(p.clip_epsilon, 0.2), Number.MIN_VALUE, 1, false],
    [
      "target_update_every",
      provided(p.target_update_every, 2),
      1,
      Infinity,
      true,
    ],
  ];
  for (const [key, value, low, high, integer] of ranges)
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      value < low ||
      value > high ||
      (integer && !Number.isInteger(value))
    )
      conflict(
        `${key} must be ${integer ? "an integer" : "a finite number"} between ${low} and ${high}.`,
      );
  if (typeof p.clip_epsilon === "number" && p.clip_epsilon >= 1)
    conflict("clip_epsilon must be strictly less than 1.");
  const signature = conflicts.join("\n");
  useEffect(() => {
    callback.current?.(!invalid.size && !signature);
  }, [invalid, signature]);
  useEffect(() => () => callback.current?.(true), []);
  const numeric = (
    key: string,
    label: string,
    value: unknown,
    change: (value: unknown) => void,
    minimum = 1,
    integer = true,
  ) => (
    <DraftField
      key={key}
      fieldKey={key}
      label={label}
      value={value}
      onChange={change}
      report={report}
      minimum={minimum}
      integer={integer}
    />
  );
  return (
    <section aria-label="Control learning" className="flex flex-col gap-4">
      <SettingsSection
        title="Control policy"
        description="Bounded CPU policy learning in a seeded local research environment."
      >
        <div className="lab-fields">
          <LabChoice
            label="Control objective"
            value={objective}
            disabled={!!invalid.size}
            options={[
              { id: "ppo", name: "PPO · environment interactions" },
              { id: "dqn", name: "Offline DQN · retained transitions" },
              {
                id: "behavioral_cloning",
                name: "Behavioral cloning · demonstration actions",
              },
            ]}
            onChange={chooseObjective}
          />
          <LabChoice
            label="Environment runtime"
            value={runtime}
            disabled={!!invalid.size}
            options={[
              { id: "native", name: "Native control environment" },
              { id: "torchrl", name: "TorchRL EnvBase" },
            ]}
            onChange={(value) =>
              patch("environment", {
                ...environment,
                kind: "control",
                runtime: value,
              })
            }
          />
        </div>
        <p>
          This seeded discrete control fixture is a bounded research
          environment. Results do not qualify a physical controller or establish
          benchmark performance. TorchRL must be available on the execution
          target.
        </p>
        {offline ? (
          <p>
            Attach retained transition data with the demonstrations role in
            Inputs before running. Each row needs observation (three finite
            features) and action (0, 1 or 2). Offline DQN also needs
            next_observation (three finite features), finite reward and boolean
            done. Configurations can be authored before attaching data.
          </p>
        ) : (
          <p>
            PPO learns from environment interactions. Keep demonstrations and
            other model-visible corpus inputs detached for this objective.
          </p>
        )}
      </SettingsSection>
      <WorkspaceDisclosure
        title="Control environment and reward"
        defaultOpen
        summary="Target, trajectory bounds and training reward"
      >
        <div className="lab-fields">
          {numeric(
            "target",
            "Target position",
            provided(p.target, 0),
            (value) => patch("target", value),
            -2,
            false,
          )}
          {numeric(
            "horizon",
            "Episode horizon",
            provided(p.horizon, 24),
            (value) => patch("horizon", value),
          )}
          {numeric("agents", "Agent count", provided(p.agents, 1), (value) =>
            patch("agents", value),
          )}
          {numeric(
            "distance_weight",
            "Distance reward weight",
            provided(reward.distance_weight, 1),
            (value) => patch("reward", { ...reward, distance_weight: value }),
            0,
            false,
          )}
          {numeric(
            "action_cost",
            "Action cost",
            provided(reward.action_cost, 0.01),
            (value) => patch("reward", { ...reward, action_cost: value }),
            0,
            false,
          )}
        </div>
        <p>
          Target position ranges from −2 to 2. Agents share the actuator and
          reward; independent evaluation uses a shared greedy action. Reward
          penalizes weighted absolute tracking error and action effort.
        </p>
      </WorkspaceDisclosure>
      <WorkspaceDisclosure
        title="Control architecture and training"
        defaultOpen
        summary="MLP dimensions and bounded policy updates"
      >
        <div className="lab-fields">
          {numeric(
            "hidden_size",
            "Policy hidden size",
            dimension("hidden_size", 16),
            (value) => chooseDimension("hidden_size", value),
            4,
          )}
          {numeric("layers", "Policy layers", dimension("layers", 1), (value) =>
            chooseDimension("layers", value),
          )}
          {numeric(
            "steps",
            "Policy training steps",
            provided(p.steps, 8),
            (value) => patch("steps", value),
          )}
          {numeric(
            "batch_size",
            "Policy batch size",
            provided(p.batch_size, 16),
            (value) => patch("batch_size", value),
            2,
          )}
          {numeric(
            "update_epochs",
            "Update epochs",
            provided(p.update_epochs, 2),
            (value) => patch("update_epochs", value),
          )}
          {numeric(
            "learning_rate",
            "Learning rate",
            provided(p.lr, provided(p.learning_rate, 0.003)),
            (value) => {
              const parameters: Options = { ...p, lr: value };
              if ("learning_rate" in p) parameters.learning_rate = value;
              onChange({ parameters });
            },
            Number.MIN_VALUE,
            false,
          )}
          {objective !== "behavioral_cloning" &&
            numeric(
              "gamma",
              "Discount factor",
              provided(p.gamma, 0.95),
              (value) => patch("gamma", value),
              0,
              false,
            )}
          {objective === "ppo" &&
            numeric(
              "clip_epsilon",
              "PPO clipping epsilon",
              provided(p.clip_epsilon, 0.2),
              (value) => patch("clip_epsilon", value),
              Number.MIN_VALUE,
              false,
            )}
          {objective === "dqn" &&
            numeric(
              "target_update_every",
              "DQN target update interval",
              provided(p.target_update_every, 2),
              (value) => patch("target_update_every", value),
            )}
        </div>
        <p>
          The policy uses an MLP on CPU with float32. Initialization and other
          retained axes remain in Definition JSON; resume requires a compatible
          immutable resume input, identical configuration, corpus and backend,
          and a step limit above the saved step.
        </p>
      </WorkspaceDisclosure>
      <WorkspaceDisclosure
        title="Independent control evaluation"
        defaultOpen
        summary="Paired held-out trajectories and conventional PD baseline"
      >
        <DraftField
          label="Evaluation seeds JSON"
          fieldKey="evaluation.seeds"
          kind="array"
          value={seeds}
          report={report}
          onChange={(value) =>
            patch("evaluation", { ...evaluation, seeds: value })
          }
        />
        <p>
          Use one to 64 unique nonnegative integer seeds, excluding training
          seed {definition.seed}. Policy and PD baseline receive paired reset
          states in fresh environments. Tracking error and constraint violations
          are measured independently of training reward.
        </p>
      </WorkspaceDisclosure>
      {conflicts.length > 0 && (
        <div role="alert">
          {conflicts.map((message, index) => (
            <p key={`${index}:${message}`}>{message}</p>
          ))}
        </div>
      )}
    </section>
  );
}
