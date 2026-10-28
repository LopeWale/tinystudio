import type { ExperimentDefinition } from "./definition";

const kind = (value: unknown): string | undefined =>
  value === undefined
    ? undefined
    : String(
        typeof value === "object" && value
          ? (value as { kind?: unknown }).kind
          : value,
      );

/** Interpret old aliases without rewriting a retained definition merely by opening it. */
export function pretrainedAxes(definition: ExperimentDefinition) {
  const learning = definition.learning ?? {},
    parameters = definition.parameters;
  const update =
    kind(
      learning.update_strategy ??
        parameters.update_strategy ??
        parameters.training_type,
    ) ?? "full";
  const legacyDpo =
    update === "dpo" || kind(parameters.training_type) === "dpo";
  const objective =
    kind(learning.objective ?? parameters.objective) ??
    (legacyDpo ? "dpo" : "sft");
  return {
    objective: ["next_token", "cross_entropy"].includes(objective)
      ? "causal_lm"
      : objective,
    update_strategy:
      (
        {
          dpo: "lora",
          full_parameter: "full",
          full_finetune: "full",
          sft: "full",
        } as Record<string, string>
      )[update] ?? update,
  };
}

/** Editing either selector canonicalizes both axes, preserving the other selection. */
export function editPretrainedAxes(
  definition: ExperimentDefinition,
  patch: Partial<ReturnType<typeof pretrainedAxes>>,
) {
  const axes = { ...pretrainedAxes(definition), ...patch };
  const parameters = { ...definition.parameters };
  delete parameters.training_type;
  delete parameters.update_strategy;
  delete parameters.objective;
  parameters.data_format =
    axes.objective === "causal_lm" ? "raw_text" : "conversational";
  return { learning: { ...definition.learning, ...axes }, parameters };
}
