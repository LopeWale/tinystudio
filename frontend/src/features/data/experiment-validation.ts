/** Numeric bounds mirror ExperimentSpec in tinystudio/api/routes/training.py.
 * This validates a completed legacy form draft; it never coerces or fills values.
 */
const numericFields = [
  ["epochs", "Number of epochs", 0.1, 50, false],
  ["learning_rate", "Learning rate", 0, 1, false],
  ["batch_size", "Batch size", 1, 64, true],
  ["gradient_accumulation_steps", "Gradient accumulation", 1, 64, true],
  ["max_length", "Max length (tokens)", 64, 8192, true],
  ["lora_r", "LoRA rank", 1, 256, true],
  ["lora_alpha", "LoRA alpha", 1, 512, true],
  ["checkpoint_every_steps", "Checkpoint every N steps", 0, 100000, true],
  ["keep_checkpoints", "Keep newest K checkpoints", 0, 1000, true],
] as const;

export function validateExperimentDraft(value: any): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return "Enter an experiment configuration.";
  if (typeof value.base_model !== "string" || !value.base_model.trim())
    return "Enter a base model.";
  if (!["lora", "full", "qlora", "dpo"].includes(value.training_type))
    return "Choose a supported training type: lora, full, qlora, or dpo.";
  for (const [key, label, min, max, integer] of numericFields) {
    const number = value[key];
    if (typeof number !== "number" || !Number.isFinite(number))
      return `${label} must be a finite number.`;
    if (integer && !Number.isInteger(number))
      return `${label} must be a whole number.`;
    if (key === "learning_rate" && number <= 0)
      return "Learning rate must be greater than 0 and at most 1.";
    if (number < min || number > max)
      return `${label} must be between ${min} and ${max}.`;
  }
  if (typeof value.assistant_only_loss !== "boolean")
    return "Assistant only loss must be enabled or disabled.";
  if (
    value.refinetune_from !== null &&
    typeof value.refinetune_from !== "string"
  )
    return "Refine from finished experiment must be a model reference or empty.";
  // ExperimentSpec declares an unrestricted int; -1 is its default sentinel.
  if (
    Object.prototype.hasOwnProperty.call(value, "max_steps") &&
    (typeof value.max_steps !== "number" ||
      !Number.isFinite(value.max_steps) ||
      !Number.isInteger(value.max_steps))
  )
    return "Maximum steps must be a finite whole number.";
  return null;
}
