"""Pure normalization of training intent, including the original DPO alias."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrainingMethod:
    objective: str
    update_strategy: str
    data_format: str

    @property
    def output_kind(self) -> str:
        return "model" if self.update_strategy == "full" else "adapter"


def resolve_method(*, training_type: str | None = None, objective: str | None = None,
                   update_strategy: str | None = None, data_format: str | None = None,
                   default_update: str = "lora") -> TrainingMethod:
    """Legacy ``dpo`` means LoRA preference updates; explicit axes must agree.

    A new full-weight DPO request uses objective=dpo and update_strategy=full.
    The historical training_type field remains a compatibility alias, not an
    extra axis allowed to silently override an explicit selection.
    """
    aliases = {"full_parameter": "full", "full_finetune": "full", "sft": "full"}
    training_type = aliases.get(training_type, training_type)
    update_strategy = aliases.get(update_strategy, update_strategy)
    objectives = {"next_token": "causal_lm", "cross_entropy": "causal_lm"}
    objective = objectives.get(objective, objective)
    preference_alias = training_type == "dpo" or update_strategy == "dpo"
    if preference_alias:
        if objective is not None and objective != "dpo":
            raise ValueError("incompatible objective: legacy dpo requires objective=dpo")
        objective = "dpo"
    if training_type == "dpo":
        training_type = "lora"
    if update_strategy == "dpo":
        update_strategy = "lora"
    for name, value in (("training_type", training_type), ("update_strategy", update_strategy)):
        if value is not None and value not in {"full", "lora", "qlora"}:
            raise ValueError(f"unsupported {name} {value!r}; supported: full, lora, qlora (legacy dpo alias)")
    if training_type is not None and update_strategy is not None and training_type != update_strategy:
        raise ValueError("conflicting training_type and update_strategy")
    strategy = update_strategy or training_type or default_update
    objective = objective or "sft"
    if objective not in {"sft", "causal_lm", "dpo"}:
        raise ValueError("unsupported objective; supported: sft, causal_lm, dpo")
    expected_format = "raw_text" if objective == "causal_lm" else "conversational"
    if data_format is not None and data_format != expected_format:
        raise ValueError(f"objective={objective} requires data_format={expected_format}")
    return TrainingMethod(objective, strategy, expected_format)
