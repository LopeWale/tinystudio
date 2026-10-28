import { useId } from "react";
import {
  Button,
  Disclosure,
  DisclosurePanel,
  Heading,
} from "react-aria-components";
import {
  InputBase,
  TextField,
} from "../../components/boardui/base/input/input";
import { Label } from "../../components/boardui/base/input/label";
import {
  Select,
  SelectItem,
} from "../../components/boardui/base/select/select";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import { NumberField } from "../../components/studio/number-field";
import "./experiment-fields.css";

type NumericDraft = string | number;
export type ExperimentDraft = {
  base_model: string;
  training_type: string;
  refinetune_from: string | null;
  batch_size: NumericDraft;
  learning_rate: NumericDraft;
  epochs: NumericDraft;
  max_length: NumericDraft;
  gradient_accumulation_steps: NumericDraft;
  lora_r: NumericDraft;
  lora_alpha: NumericDraft;
  checkpoint_every_steps: NumericDraft;
  keep_checkpoints: NumericDraft;
  assistant_only_loss: boolean;
};
export type ExperimentFieldsProps = {
  value: ExperimentDraft;
  onChange: (value: ExperimentDraft) => void;
  catalog: { id: string }[];
  models: { kind: string; alias: string }[];
  disabled?: boolean;
};

/** Owns presentation only: drafts and submission remain with the setup flow. */
export function ExperimentFields({
  value,
  onChange,
  catalog,
  models,
  disabled = false,
}: ExperimentFieldsProps) {
  const catalogId = useId();
  const baseRefinementId = `${catalogId}-base`;
  function change<K extends keyof ExperimentDraft>(
    key: K,
    next: ExperimentDraft[K],
  ) {
    onChange({
      ...value,
      [key]: next,
      ...(key === "training_type"
        ? {
            learning_rate:
              next === "dpo" ? 0.000005 : next === "full" ? 0.00005 : 0.0002,
          }
        : {}),
    });
  }
  function number(
    key: keyof Pick<
      ExperimentDraft,
      | "batch_size"
      | "learning_rate"
      | "epochs"
      | "max_length"
      | "gradient_accumulation_steps"
      | "lora_r"
      | "lora_alpha"
      | "checkpoint_every_steps"
      | "keep_checkpoints"
    >,
    label: string,
    min: number,
    max: number,
    step: number | "any" = 1,
  ) {
    return (
      <NumberField
        key={key}
        label={label}
        value={value[key]}
        onChange={(next) => change(key, next)}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
      />
    );
  }
  const refinements = [
    ...new Set(
      models
        .filter((model) => model.kind === "fine-tuned")
        .map((model) => model.alias),
    ),
  ];
  return (
    <div className="experiment-fields">
      <section
        className="experiment-fields-group"
        aria-label="Model configuration"
      >
        <header>
          <h3>Model</h3>
          <p>Choose a starting point and how its weights will change.</p>
        </header>
        <TextField
          value={value.base_model}
          onChange={(next) => change("base_model", next)}
          isDisabled={disabled}
        >
          <Label>Base model</Label>
          <InputBase list={catalogId} fieldClassName="studio-form-control" />
        </TextField>
        <datalist id={catalogId}>
          {[...new Set(catalog.map((model) => model.id))].map((id) => (
            <option key={id} value={id} />
          ))}
        </datalist>
        <div className="experiment-fields-pair">
          <div className="experiment-fields-select">
            <span
              className="text-body-2-medium text-text-primary"
              id={`${catalogId}-type`}
            >
              Training type
            </span>
            <Select
              aria-labelledby={`${catalogId}-type`}
              selectedKey={value.training_type}
              onSelectionChange={(next) =>
                next !== null && change("training_type", String(next))
              }
              isDisabled={disabled}
            >
              {["lora", "full", "qlora", "dpo"].map((type) => (
                <SelectItem key={type} id={type}>
                  {type}
                </SelectItem>
              ))}
            </Select>
          </div>
          <div className="experiment-fields-select">
            <span
              className="text-body-2-medium text-text-primary"
              id={`${catalogId}-refine`}
            >
              Refine from finished experiment
            </span>
            <Select
              aria-labelledby={`${catalogId}-refine`}
              selectedKey={value.refinetune_from || baseRefinementId}
              onSelectionChange={(next) =>
                next !== null &&
                change(
                  "refinetune_from",
                  next === baseRefinementId ? null : String(next),
                )
              }
              isDisabled={disabled}
            >
              <SelectItem id={baseRefinementId}>
                Start from base model
              </SelectItem>
              {refinements.map((alias) => (
                <SelectItem key={alias} id={alias}>
                  {alias}
                </SelectItem>
              ))}
            </Select>
          </div>
        </div>
      </section>
      <section
        className="experiment-fields-group"
        aria-label="Optimization configuration"
      >
        <header>
          <h3>Optimization</h3>
          <p>Control the learning pace and the size of each update.</p>
        </header>
        <div className="experiment-fields-triple">
          {number("batch_size", "Batch size", 1, 64)}
          {number("learning_rate", "Learning rate", 0, 1, "any")}
          {number("epochs", "Number of epochs", 0.1, 50, "any")}
        </div>
        <div className="experiment-fields-pair">
          {number("max_length", "Max length (tokens)", 64, 8192)}
          {number(
            "gradient_accumulation_steps",
            "Gradient accumulation",
            1,
            64,
          )}
        </div>
        <Checkbox
          isSelected={value.assistant_only_loss}
          onChange={(next) => change("assistant_only_loss", next)}
          isDisabled={disabled}
        >
          Assistant only loss
        </Checkbox>
      </section>
      {value.training_type !== "full" && (
        <section
          className="experiment-fields-group"
          aria-label="LoRA configuration"
        >
          <header>
            <h3>LoRA adapters</h3>
            <p>
              {value.training_type === "dpo"
                ? "DPO preference training uses LoRA adapters."
                : "Set the capacity and scaling of the trainable adapters."}
            </p>
          </header>
          <div className="experiment-fields-pair">
            {number("lora_r", "LoRA rank", 1, 256)}
            {number("lora_alpha", "LoRA alpha", 1, 512)}
          </div>
        </section>
      )}
      <Disclosure
        className="experiment-fields-group experiment-fields-checkpoints"
        isDisabled={disabled}
      >
        <Heading>
          <Button slot="trigger" className="experiment-fields-disclosure">
            Checkpoints <span aria-hidden="true">⌄</span>
          </Button>
        </Heading>
        <DisclosurePanel>
          <p className="experiment-fields-hint">
            Save intermediate weights to compare progress. Zero turns saving off
            or retains all saved checkpoints.
          </p>
          <div className="experiment-fields-pair">
            {number(
              "checkpoint_every_steps",
              "Checkpoint every N steps (0 = off)",
              0,
              100000,
            )}
            {number(
              "keep_checkpoints",
              "Keep newest K checkpoints (0 = all)",
              0,
              1000,
            )}
          </div>
        </DisclosurePanel>
      </Disclosure>
    </div>
  );
}
