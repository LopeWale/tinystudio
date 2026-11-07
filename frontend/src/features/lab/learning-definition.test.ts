import { describe, expect, it } from "vitest";
import { artifactRoles } from "./inputs";
import {
  defaultVisibility,
  inputExpectations,
  makeDefinition,
  parseDefinition,
  controlObjective,
  normalizeControlObjective,
} from "./definition";

describe("control input requirements", () => {
  it.each(["constructor", "__proto__", "toString"])(
    "preserves the unsupported objective %s as a literal string",
    (objective) => {
      const definition = makeDefinition("learning.control_rl");
      definition.learning = { objective };
      expect(normalizeControlObjective(objective)).toBe(objective);
      expect(controlObjective(definition)).toBe(objective);
    },
  );
  it.each([
    [{ kind: "dqn" }, {}, "dqn"],
    [{ kind: "imitation" }, {}, "behavioral_cloning"],
    ["bc", {}, "behavioral_cloning"],
    [undefined, { objective: { kind: "dqn" } }, "dqn"],
    [undefined, { algorithm: { kind: "offline_rl" } }, "dqn"],
  ])(
    "uses the effective objective %j and legacy selections for required demonstrations",
    (objective, parameters, expected) => {
      const definition = makeDefinition("learning.control_rl");
      definition.learning = objective === undefined ? {} : { objective };
      definition.parameters = parameters;
      expect(controlObjective(definition)).toBe(expected);
      expect(
        inputExpectations(definition).find(
          (row) => row.role === "demonstrations",
        )?.required,
      ).toBe(true);
    },
  );
  it.each([
    [{ kind: ["dqn"] }, {}],
    [{ kind: "dqn", extra: true }, {}],
    [false, {}],
    ["dqn", { objective: "offline_rl" }],
    ["ppo", { algorithm: "dqn" }],
  ])(
    "does not silently coerce an invalid or conflicting objective %j",
    (objective, parameters) => {
      const definition = makeDefinition("learning.control_rl");
      definition.learning = { objective };
      definition.parameters = parameters;
      expect(controlObjective(definition)).toBeUndefined();
    },
  );
});

describe("immutable language learning bindings", () => {
  it.each([
    ["scratch", "causal_lm", []],
    ["weights", "causal_lm", ["initial_weights"]],
    ["resume", "causal_lm", ["resume"]],
    ["weights", "distillation", ["initial_weights", "teacher"]],
  ])(
    "requires only the selected %s / %s checkpoint roles",
    (initialization, objective, expected) => {
      const definition = makeDefinition("learning.language");
      definition.learning = {
        ...definition.learning,
        initialization,
        objective,
      };
      expect(
        inputExpectations(definition)
          .filter((row) => row.required)
          .map((row) => row.role),
      ).toEqual(expected);
    },
  );

  it("retains independent axes and immutable lineage through the code view", () => {
    const definition = makeDefinition("learning.language");
    definition.learning = {
      ...definition.learning,
      initialization: { kind: "resume" },
      objective: { kind: "distillation" },
      representation: { kind: "bpe", vocab_size: 300 },
    };
    definition.inputs = [
      {
        artifact_id: "parent",
        sha256: "a".repeat(64),
        role: "resume",
        visibility: "model",
      },
      {
        artifact_id: "oracle",
        sha256: "b".repeat(64),
        role: "teacher",
        visibility: "model",
      },
      {
        artifact_id: "held-out",
        sha256: "c".repeat(64),
        role: "validation",
        visibility: "evaluation",
      },
    ];
    const restored = parseDefinition(JSON.stringify(definition));
    expect(restored).toEqual(definition);
    expect(
      inputExpectations(restored)
        .filter((row) => row.required)
        .map((row) => row.role),
    ).toEqual(["resume", "teacher"]);
  });

  it("exposes all checkpoint roles and defaults validation to its evaluator boundary", () => {
    expect(artifactRoles).toEqual(
      expect.arrayContaining([
        "source",
        "initial_weights",
        "resume",
        "teacher",
        "validation",
      ]),
    );
    expect(defaultVisibility("initial_weights")).toBe("model");
    expect(defaultVisibility("teacher")).toBe("model");
    expect(defaultVisibility("validation")).toBe("evaluation");
    expect(defaultVisibility("final_test")).toBe("truth");
  });
});
