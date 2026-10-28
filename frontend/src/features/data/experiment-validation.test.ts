import { describe, expect, it } from "vitest";
import { validateExperimentDraft } from "./experiment-validation";

const valid = {
  base_model: "small/model",
  training_type: "lora",
  epochs: 3,
  learning_rate: 0.0002,
  batch_size: 8,
  gradient_accumulation_steps: 2,
  max_length: 512,
  lora_r: 16,
  lora_alpha: 32,
  assistant_only_loss: false,
  refinetune_from: null,
  checkpoint_every_steps: 0,
  keep_checkpoints: 0,
};

// Independent boundary table transcribed from ExperimentSpec, not the validator.
const bounds = [
  ["epochs", 0.1, 50, 0.099, 50.001],
  ["batch_size", 1, 64, 0, 65],
  ["gradient_accumulation_steps", 1, 64, 0, 65],
  ["max_length", 64, 8192, 63, 8193],
  ["lora_r", 1, 256, 0, 257],
  ["lora_alpha", 1, 512, 0, 513],
  ["checkpoint_every_steps", 0, 100000, -1, 100001],
  ["keep_checkpoints", 0, 1000, -1, 1001],
] as const;
const numericKeys = ["learning_rate", ...bounds.map(([key]) => key)];

describe("completed experiment draft validation", () => {
  it("accepts legacy defaults without changing or filling the payload", () => {
    const extra = Object.freeze({ opaque: "preserve me" });
    const value = Object.freeze({
      ...valid,
      objective: "sft",
      device: "cpu",
      extra,
    });
    expect(validateExperimentDraft(value)).toBeNull();
    expect(value).toEqual({ ...valid, objective: "sft", device: "cpu", extra });
    expect(value.extra).toBe(extra);
    expect(value).not.toHaveProperty("max_steps");
  });
  it.each(["lora", "qlora", "full", "dpo"])(
    "accepts %s training",
    (training_type) => {
      expect(validateExperimentDraft({ ...valid, training_type })).toBeNull();
    },
  );
  it.each([undefined, null, "", "unsupported", 1])(
    "rejects unsupported legacy training type %s",
    (training_type) => {
      expect(validateExperimentDraft({ ...valid, training_type })).toMatch(
        /supported training type/i,
      );
    },
  );
  it.each([null, undefined, [], "configuration"])(
    "rejects non-object draft %s",
    (value) => {
      expect(validateExperimentDraft(value)).toMatch(/configuration/i);
    },
  );
  it.each(["", " \n\t", null, 1])(
    "requires a nonempty model %s",
    (base_model) => {
      expect(validateExperimentDraft({ ...valid, base_model })).toMatch(
        /base model/i,
      );
    },
  );
  it.each(bounds)(
    "enforces inclusive API bounds for %s",
    (key, min, max, below, above) => {
      expect(validateExperimentDraft({ ...valid, [key]: min })).toBeNull();
      expect(validateExperimentDraft({ ...valid, [key]: max })).toBeNull();
      expect(validateExperimentDraft({ ...valid, [key]: below })).toMatch(
        /between/i,
      );
      expect(validateExperimentDraft({ ...valid, [key]: above })).toMatch(
        /between/i,
      );
    },
  );
  it.each(numericKeys)(
    "rejects blank, coerced, missing, and nonfinite %s",
    (key) => {
      for (const invalid of [
        "",
        " ",
        "2",
        undefined,
        null,
        false,
        NaN,
        Infinity,
        -Infinity,
      ]) {
        expect(validateExperimentDraft({ ...valid, [key]: invalid })).toMatch(
          /finite number/i,
        );
      }
    },
  );
  it.each(bounds.filter(([key]) => key !== "epochs"))(
    "requires integer counts for %s",
    (key, min) => {
      expect(validateExperimentDraft({ ...valid, [key]: min + 0.5 })).toMatch(
        /whole number/i,
      );
    },
  );
  it("accepts fractional epochs and arbitrary positive tiny learning rates", () => {
    for (const learning_rate of [Number.MIN_VALUE, 5e-9, 0.000123456789, 1]) {
      expect(
        validateExperimentDraft({ ...valid, epochs: 0.125, learning_rate }),
      ).toBeNull();
    }
    for (const learning_rate of [0, -0.1, 1.00001]) {
      expect(validateExperimentDraft({ ...valid, learning_rate })).toMatch(
        /Learning rate/,
      );
    }
  });
  it("requires a boolean assistant loss flag and nullable string refinement", () => {
    for (const assistant_only_loss of [true, false])
      expect(
        validateExperimentDraft({ ...valid, assistant_only_loss }),
      ).toBeNull();
    for (const assistant_only_loss of [undefined, null, 0, "false"])
      expect(
        validateExperimentDraft({ ...valid, assistant_only_loss }),
      ).toMatch(/Assistant only loss/);
    for (const refinetune_from of [null, "", "ft-existing-1", "job/1"])
      expect(validateExperimentDraft({ ...valid, refinetune_from })).toBeNull();
    for (const refinetune_from of [undefined, false, 1, {}])
      expect(validateExperimentDraft({ ...valid, refinetune_from })).toMatch(
        /model reference/,
      );
  });
  it("validates optional maximum steps as an unrestricted API integer", () => {
    for (const max_steps of [-100, -1, 0, 1, 100000])
      expect(validateExperimentDraft({ ...valid, max_steps })).toBeNull();
    for (const max_steps of [undefined, null, "", "1", 1.5, Infinity, NaN])
      expect(validateExperimentDraft({ ...valid, max_steps })).toMatch(
        /Maximum steps/,
      );
  });
  it("reports a deterministic first error without editing any invalid draft", () => {
    const value = Object.freeze({
      ...valid,
      base_model: "",
      epochs: "",
      learning_rate: "",
    });
    expect(validateExperimentDraft(value)).toBe("Enter a base model.");
    expect(value.epochs).toBe("");
    expect(value.learning_rate).toBe("");
  });
});
