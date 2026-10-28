"""Fine-tuning: SFT (full or LoRA, 4-bit QLoRA on CUDA) and DPO with TRL and PEFT.

This module trains into an experiment directory and reports through callbacks; jobs, experiments, and their
state live in PostgreSQL (see :mod:`tinystudio.services.jobs`). On Apple Silicon the run goes through PyTorch's
MPS backend in fp32: bf16 is switched off, 4-bit QLoRA is unavailable because bitsandbytes has no Metal backend,
and the fused AdamW is replaced by the plain one. On CUDA the run uses bf16 and the fused optimizer.
"""

from __future__ import annotations

import json
import gc
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tinystudio.core.project import ProjectError, now, read_json
from tinystudio.training.recommend import LORA_TARGETS, catalog_entry
from tinystudio.training.methods import resolve_method
from tinystudio.training.runtime import CheckedPlacementMixin, TrainingPlacement, training_session

ALIAS = re.compile(r"^ft-(?P<job>[A-Za-z0-9][A-Za-z0-9._-]{0,63})-(?P<number>\d+)$")


@dataclass
class TrainConfig:
    base_model: str
    training_type: str | None = None  # legacy alias; omitted defaults to LoRA
    objective: str | None = None
    update_strategy: str | None = None
    data_format: str | None = None
    device: str | None = None
    dtype: str | None = None
    epochs: float = 3
    learning_rate: float = 2e-4
    batch_size: int = 8
    gradient_accumulation_steps: int = 2
    max_length: int = 512
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    lora_targets: list[str] = field(default_factory=list)
    assistant_only_loss: bool = False
    warmup_steps: int = 0
    seed: int = 42
    max_steps: int = -1  # >0 overrides epochs; handy for smoke tests
    logging_steps: int = 5
    eval_steps: int = 0  # 0 = evaluate once per epoch when validation data exists
    refinetune_from: str | None = None  # job/number or ft-job-number: continue from that experiment's weights
    dpo_beta: float = 0.1
    checkpoint_every_steps: int = 0  # >0 saves weights-only checkpoints under checkpoints/ for drift studies
    keep_checkpoints: int = 0  # 0 keeps every checkpoint

    def __post_init__(self) -> None:
        try:
            method = resolve_method(training_type=self.training_type, objective=self.objective,
                                    update_strategy=self.update_strategy, data_format=self.data_format)
        except ValueError as exc:
            raise ProjectError(str(exc)) from exc
        self.training_type = self.training_type or method.update_strategy
        self.objective, self.update_strategy, self.data_format = method.objective, method.update_strategy, method.data_format
        if self.data_format == "raw_text" and self.assistant_only_loss:
            raise ProjectError("assistant_only_loss is incompatible with raw_text causal LM training")
        if not self.lora_targets:
            entry = catalog_entry(self.base_model)
            self.lora_targets = LORA_TARGETS.get(entry.family, []) if entry else []

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TrainConfig":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in value.items() if k in known})

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.update_strategy == "qlora":
            from .quantization import POLICY_FORMAT
            result["quantization_format"] = POLICY_FORMAT
        return result


def alias_for(job: str, number: int) -> str:
    return f"ft-{job}-{number}"


def parse_alias(model_id: str) -> tuple[str, int] | None:
    match = ALIAS.match(model_id)
    return (match["job"], int(match["number"])) if match else None


def parse_experiment_ref(reference: str) -> tuple[str, int]:
    """'job/number' or 'ft-job-number' -> (job, number)."""
    alias = parse_alias(reference)
    if alias:
        return alias
    job, _, number = reference.partition("/")
    if not job or not number.isdigit():
        raise ProjectError(f"expected job/number or ft-job-number, got {reference!r}")
    return job, int(number)


def train_experiment(exp_dir: Path, config: TrainConfig, train_rows: list[dict[str, Any]], val_rows: list[dict[str, Any]], progress: Any = print, weights: Path | None = None, on_log: Any = None, *, runtime: TrainingPlacement | None = None, check_cancel: Any = None) -> dict[str, Any]:
    """Train into ``exp_dir`` (adapter/ or model/, checkpoints/). ``on_log(kind, row)`` receives loss points and checkpoint records."""
    with training_session():
        placement = runtime or TrainingPlacement.resolve(config.device, config.dtype)
        exp_dir.mkdir(parents=True, exist_ok=True)
        return _train(exp_dir, config, train_rows, val_rows, progress, weights,
                      on_log or (lambda kind, row: None), placement, check_cancel or (lambda: None))


def preference_rows(rows: list[dict[str, Any]], *, strict: bool = False) -> list[dict[str, Any]]:
    """Datapoints with a `rejected` answer, in TRL's conversational preference format (prompt / chosen / rejected)."""
    pairs = []
    for index, row in enumerate(rows):
        messages = row.get("messages")
        valid = (isinstance(messages, list) and len(messages) >= 2
                 and all(isinstance(message, dict) and message.get("role") in {"system", "user", "assistant", "tool"}
                         and isinstance(message.get("content"), str) for message in messages)
                 and messages[-1]["role"] == "assistant")
        rejected = row.get("rejected")
        chosen = messages[-1]["content"] if valid else None
        valid = valid and isinstance(rejected, str) and bool(rejected.strip()) and bool(chosen.strip()) and rejected != chosen
        if strict and not valid:
            raise ProjectError(f"DPO row {index} requires a prompt, a final assistant response, and a distinct nonempty rejected response")
        if valid:
            pairs.append({"prompt": row["messages"][:-1], "chosen": [row["messages"][-1]], "rejected": [{"role": "assistant", "content": rejected}]})
    return pairs


def _checkpoint_logger(exp_dir: Path, progress: Any, on_log: Any) -> Any:
    """Record every periodic checkpoint (step, epoch, last loss, path) to checkpoints.jsonl."""
    from transformers import TrainerCallback

    class CheckpointLog(TrainerCallback):
        def on_save(self, args: Any, state: Any, control: Any, **kwargs: Any) -> None:
            last = next((h for h in reversed(state.log_history) if "loss" in h), {})
            path = exp_dir / "checkpoints" / f"checkpoint-{state.global_step}"
            row = {"step": int(state.global_step), "epoch": round(float(state.epoch or 0), 3), "loss": last.get("loss"), "path": str(path), "time": now()}
            with (exp_dir / "checkpoints.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            on_log("checkpoint", row)
            progress(f"checkpoint saved at step {row['step']} (epoch {row['epoch']})")

    return CheckpointLog()


def _checkpoint_args(config: "TrainConfig") -> dict[str, Any]:
    if config.checkpoint_every_steps > 0:
        return {"save_strategy": "steps", "save_steps": config.checkpoint_every_steps, "save_total_limit": config.keep_checkpoints or None, "save_only_model": True}
    return {"save_strategy": "no"}


def qlora_support(device: str | None = None) -> tuple[bool, str]:
    """Eligibility is target-specific; a successful fixture establishes qualification."""
    import importlib.util

    import torch

    try:
        from tinystudio.serving.runner import pick_device
        selected = torch.device(device or pick_device()).type
    except (RuntimeError, TypeError, ValueError):
        return False, f"Invalid training device {device!r}; select cpu, mps or cuda[:index] on a compatible target"
    if selected == "cpu":
        from .quantization import cpu_pack_compatible
        if cpu_pack_compatible():
            return True, "pinned Linux ARM64 CPU NF4 pack"
    if selected != "cuda" or not torch.cuda.is_available():
        return False, "QLoRA requires a CUDA target with bitsandbytes, or the pinned Linux ARM64 Python 3.12 CPU NF4 pack. Native Apple execution remains unqualified; select lora/full or a compatible execution target."
    if importlib.util.find_spec("bitsandbytes") is None:
        return False, "QLoRA needs the bitsandbytes package: pip install bitsandbytes"
    return True, "ok"


def _load_4bit(source: str, device: str, progress: Any, dtype=None) -> Any:
    ok, reason = qlora_support(device)
    if not ok:
        raise ProjectError(reason)
    import torch
    from peft import prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype or torch.bfloat16)
    progress("loading the base model in 4-bit NF4 (QLoRA)")
    model = AutoModelForCausalLM.from_pretrained(source, dtype=dtype or torch.bfloat16, quantization_config=quant, device_map={"": device}, local_files_only=True)
    return prepare_model_for_kbit_training(model)


def _loss_logger(exp_dir: Path, progress: Any, on_log: Any) -> Any:
    from transformers import TrainerCallback

    loss_path = exp_dir / "loss.jsonl"
    shown_keys = ("loss", "eval_loss", "learning_rate", "mean_token_accuracy", "eval_mean_token_accuracy", "rewards/accuracies", "rewards/margins", "eval_rewards/accuracies")

    class LossLog(TrainerCallback):
        def on_log(self, args: Any, state: Any, control: Any, logs: dict[str, float] | None = None, **kwargs: Any) -> None:
            if not logs:
                return
            row = {"step": state.global_step, "epoch": round(state.epoch or 0, 3), "time": now(), **{k: v for k, v in logs.items() if isinstance(v, (int, float))}}
            with loss_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            on_log("loss", row)
            shown = {k: (v if k == "learning_rate" else round(v, 4)) for k, v in row.items() if k in shown_keys}
            if shown:
                progress(f"step {row['step']} epoch {row['epoch']} " + " ".join(f"{k}={v:.3g}" for k, v in shown.items()))

    return LossLog()


def _cancellation_callback(check_cancel):
    from transformers import TrainerCallback

    class CancellationCheck(TrainerCallback):
        def on_step_begin(self, *args, **kwargs):
            check_cancel()

        def on_step_end(self, *args, **kwargs):
            check_cancel()

    return CancellationCheck()


def _parameter_digest(model, *, base_only=False):
    from .quantization import state_digest
    return state_digest(model, base_only=base_only)


def _quantized_trainer_setup(trainer, retained, runtime, progress):
    if retained is None:
        return
    from .quantization import prepare_adapter_precision, state_digest
    retained["precision"] = prepare_adapter_precision(trainer, runtime)
    if state_digest(trainer.model, base_only=True) != retained["state_sha256"]:
        raise ProjectError("trainer setup changed the retained quantized starting base")
    progress(f"NF4 compute and adapter precision: {runtime.dtype_name}; retained base verified")


def _quantized_training_result(trainer, retained, tokenizer, directory, runtime, context):
    if retained is None:
        return {}
    from .quantization import assert_adapter_optimizer, state_digest, capture_probes
    assert_adapter_optimizer(trainer)
    if state_digest(trainer.model, base_only=True) != retained["state_sha256"]:
        raise ProjectError("QLoRA changed frozen base or quantization state")
    return {"quantization": retained,
            "reload_probes": capture_probes(trainer.model, tokenizer, directory, runtime.device, context)}


def raw_sequence_rows(rows, tokenizer, max_length):
    """Tokenize exact text, append one EOS if absent, then truncate on the right.

    Pretokenized input prevents TRL from applying chat templates or text cleanup.
    Raw UTF-8 identities and truncation are recorded separately from token IDs.
    """
    import hashlib
    prepared, receipts = [], []
    for index, row in enumerate(rows):
        text = row.get("text")
        if not isinstance(text, str) or not text or "messages" in row:
            raise ProjectError(f"raw_text row {index} requires an exact nonempty text string and no messages")
        ids = tokenizer(text, add_special_tokens=False)["input_ids"]
        eos = tokenizer.eos_token_id
        if eos is None:
            raise ProjectError("raw_text training requires a tokenizer EOS token")
        appended = not ids or ids[-1] != eos
        if appended:
            ids = [*ids, eos]
        total = len(ids)
        ids = ids[:max_length]
        if len(ids) < 2:
            raise ProjectError(f"raw_text row {index} has fewer than two learning tokens")
        prepared.append({"input_ids": ids, "attention_mask": [1] * len(ids)})
        receipts.append({"source_record_id": row.get("source_record_id"),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "tokens_before_truncation": total, "tokens": len(ids), "truncated_tokens": total - len(ids),
            "eos_appended": appended, "token_ids_sha256": hashlib.sha256(json.dumps(ids).encode()).hexdigest()})
    return prepared, receipts


def _train_dpo(exp_dir: Path, config: TrainConfig, train_rows: list[dict[str, Any]], val_rows: list[dict[str, Any]], progress: Any, weights: Path | None, on_log: Any, runtime: TrainingPlacement, check_cancel) -> dict[str, Any]:
    """DPO objective with independently selected full or adapter updates."""
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, PrinterCallback
    from trl import DPOConfig, DPOTrainer

    train_pairs, val_pairs = preference_rows(train_rows, strict=True), preference_rows(val_rows, strict=True)
    if len(train_pairs) < 2:
        raise ProjectError(
            f"DPO needs training datapoints with a 'rejected' answer; this snapshot has {len(train_pairs)}. "
            "Correct traces and run `tinystudio trace promote DATASET --preference`, or add datapoints with a `rejected` field."
        )
    device, dtype = runtime.device, runtime.dtype
    source = str(weights) if weights else config.base_model
    progress(f"device={device} dtype={str(dtype).replace('torch.', '')} base_model={config.base_model} weights={source} type=dpo pairs train={len(train_pairs)} val={len(val_pairs)} beta={config.dpo_beta}")

    tokenizer = AutoTokenizer.from_pretrained(source)
    if (Path(source) / "adapter_config.json").exists():
        raise ProjectError("DPO requires a complete immutable starting model; merge and retain any existing adapter first")
    model = (_load_4bit(source, device, progress, dtype) if config.update_strategy == "qlora" else
             AutoModelForCausalLM.from_pretrained(source, dtype=dtype).to(device))
    quantized = None
    if config.update_strategy == "qlora":
        from .quantization import retain_base
        quantized = retain_base(model, exp_dir, runtime, check_cancel)
    reference = None
    if config.update_strategy == "full":
        reference = AutoModelForCausalLM.from_pretrained(source, dtype=dtype).to(device)
        reference.requires_grad_(False).eval()
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    train_ds = Dataset.from_list(train_pairs)
    eval_ds = Dataset.from_list(val_pairs) if val_pairs else None
    peft_config = (LoraConfig(r=config.lora_r, lora_alpha=config.lora_alpha, lora_dropout=config.lora_dropout,
        target_modules=config.lora_targets or "all-linear", task_type="CAUSAL_LM") if config.update_strategy != "full" else None)
    args = DPOConfig(
        output_dir=str(exp_dir / "checkpoints"),
        num_train_epochs=config.epochs,
        max_steps=config.max_steps,
        learning_rate=config.learning_rate,
        beta=config.dpo_beta,
        per_device_train_batch_size=config.batch_size,
        per_device_eval_batch_size=config.batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        max_length=config.max_length,
        warmup_steps=config.warmup_steps,
        lr_scheduler_type="cosine",
        optim="adamw_torch_fused" if device.startswith("cuda") else "adamw_torch",
        bf16=dtype == torch.bfloat16,
        fp16=False,
        use_cpu=device == "cpu",
        logging_steps=config.logging_steps,
        logging_first_step=True,
        eval_strategy=("steps" if config.eval_steps else "epoch") if eval_ds is not None else "no",
        eval_steps=config.eval_steps or None,
        report_to="none",
        dataloader_pin_memory=device.startswith("cuda"),
        seed=config.seed,
        disable_tqdm=True,
        **_checkpoint_args(config),
    )
    class PlacedDPOTrainer(CheckedPlacementMixin, DPOTrainer):
        pass
    trainer = PlacedDPOTrainer(model=model, ref_model=reference, args=args, train_dataset=train_ds, eval_dataset=eval_ds, processing_class=tokenizer, peft_config=peft_config, callbacks=[_loss_logger(exp_dir, progress, on_log), _checkpoint_logger(exp_dir, progress, on_log), _cancellation_callback(check_cancel)])
    _quantized_trainer_setup(trainer, quantized, runtime, progress)
    trainer.tinystudio_placement = runtime
    runtime.check_device(trainer.args.device)
    runtime.check_model(trainer.model)
    reference_model = trainer.ref_model if trainer.ref_model is not None else trainer.model
    base_only = trainer.ref_model is None
    reference_before = _parameter_digest(reference_model, base_only=base_only)
    if trainer.ref_model is not None:
        runtime.check_model(trainer.ref_model)
        if any(parameter.requires_grad for parameter in trainer.ref_model.parameters()):
            raise ProjectError("DPO reference parameters must be frozen")
    trainer.remove_callback(PrinterCallback)
    trainable, total = _count_parameters(trainer.model)
    progress(f"trainable parameters: {trainable:,} of {total:,} ({trainable / total:.2%})")
    train_output = trainer.train()
    runtime.check_model(trainer.model)
    reference_after = _parameter_digest(reference_model, base_only=base_only)
    if reference_after != reference_before:
        raise ProjectError("DPO reference weights changed during policy optimization")
    optimizer_ids = {id(parameter) for group in trainer.optimizer.param_groups for parameter in group["params"]}
    if optimizer_ids != {id(parameter) for parameter in trainer.model.parameters() if parameter.requires_grad}:
        raise ProjectError("DPO optimizer differs from the selected trainable parameters")
    if base_only and any(parameter.requires_grad and "lora_" not in name for name, parameter in trainer.model.named_parameters()):
        raise ProjectError("adapter DPO cannot update frozen reference parameters")
    if trainer.ref_model is not None and any(id(parameter) in optimizer_ids for parameter in trainer.ref_model.parameters()):
        raise ProjectError("DPO reference parameters entered the policy optimizer")
    final_eval: dict[str, float] = {}
    if eval_ds is not None:
        final_eval = {k: float(v) for k, v in trainer.evaluate().items() if isinstance(v, (int, float))}
    check_cancel()
    quantized_result = _quantized_training_result(trainer, quantized, tokenizer, exp_dir, runtime, config.max_length)
    check_cancel()
    out = exp_dir / ("model" if config.update_strategy == "full" else "adapter")
    trainer.model.save_pretrained(str(out))
    tokenizer.save_pretrained(str(out))
    result = {
        "output": str(out),
        "device": device,
        "objective": config.objective, "update_strategy": config.update_strategy,
        "output_kind": "quantized_adapter" if quantized is not None else "model" if config.update_strategy == "full" else "adapter",
        **quantized_result,
        "placement": runtime.report(),
        "reference": {"kind": "separate_frozen_model" if reference is not None else "adapter_disabled_starting_model",
            "sha256_before": reference_before, "sha256_after": reference_after, "unchanged": True,
            "optimizer_disjoint": True},
        "trainable_parameters": trainable,
        "total_parameters": total,
        "steps": int(train_output.global_step),
        "train_loss": float(train_output.training_loss),
        "preference_pairs": {"train": len(train_pairs), "validation": len(val_pairs)},
        "metrics": {**{k: float(v) for k, v in train_output.metrics.items() if isinstance(v, (int, float))}, **final_eval},
    }
    del trainer, model, reference, reference_model
    gc.collect()
    return result


def _train(exp_dir: Path, config: TrainConfig, train_rows: list[dict[str, Any]], val_rows: list[dict[str, Any]], progress: Any, weights: Path | None, on_log: Any, runtime: TrainingPlacement, check_cancel) -> dict[str, Any]:
    check_cancel()
    if config.update_strategy == "qlora":
        supported, reason = qlora_support(runtime.device)
        if not supported:
            raise ProjectError(reason)
    if config.objective == "dpo":
        return _train_dpo(exp_dir, config, train_rows, val_rows, progress, weights, on_log, runtime, check_cancel)
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import torch
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer, PrinterCallback
    from trl import SFTConfig, SFTTrainer

    device, dtype = runtime.device, runtime.dtype
    source = str(weights) if weights else config.base_model
    progress(f"device={device} dtype={str(dtype).replace('torch.', '')} base_model={config.base_model} weights={source} type={config.training_type} train={len(train_rows)} val={len(val_rows)}")

    tokenizer = AutoTokenizer.from_pretrained(source)
    if config.update_strategy == "qlora":
        model = _load_4bit(source, device, progress, dtype)
    else:
        model = AutoModelForCausalLM.from_pretrained(source, dtype=dtype).to(device)
    quantized = None
    if config.update_strategy == "qlora":
        from .quantization import retain_base
        quantized = retain_base(model, exp_dir, runtime, check_cancel)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    data_contract = {"format": config.data_format, "objective": config.objective}
    if config.data_format == "raw_text":
        train_data, train_receipts = raw_sequence_rows(train_rows, tokenizer, config.max_length)
        val_data, val_receipts = raw_sequence_rows(val_rows, tokenizer, config.max_length)
        data_contract.update(train=train_receipts, validation=val_receipts, special_tokens="append EOS only if absent",
                             truncation="right", chat_template_applied=False)
    else:
        train_data = [{"messages": row["messages"]} for row in train_rows]
        val_data = [{"messages": row["messages"]} for row in val_rows]
    train_ds = Dataset.from_list(train_data)
    eval_ds = Dataset.from_list(val_data) if val_data else None

    peft_config = None
    if config.update_strategy in ("lora", "qlora"):
        from peft import LoraConfig

        peft_config = LoraConfig(
            r=config.lora_r,
            lora_alpha=config.lora_alpha,
            lora_dropout=config.lora_dropout,
            target_modules=config.lora_targets or "all-linear",
            task_type="CAUSAL_LM",
        )

    assistant_only = config.assistant_only_loss
    template = tokenizer.chat_template or ""
    if assistant_only and "{% generation %}" not in template and "{%- generation %}" not in template:
        progress("warning: the chat template has no {% generation %} markers; TRL will patch known families (Qwen2.5) and fail for others")

    args = SFTConfig(
        output_dir=str(exp_dir / "checkpoints"),
        num_train_epochs=config.epochs,
        max_steps=config.max_steps,
        learning_rate=config.learning_rate,
        per_device_train_batch_size=config.batch_size,
        per_device_eval_batch_size=config.batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        max_length=config.max_length,
        assistant_only_loss=assistant_only,
        warmup_steps=config.warmup_steps,
        lr_scheduler_type="cosine",
        optim="adamw_torch_fused" if device.startswith("cuda") else "adamw_torch",  # the fused optimizer is CUDA-only
        bf16=dtype == torch.bfloat16,
        fp16=False,
        use_cpu=device == "cpu",
        dataset_kwargs={"skip_prepare_dataset": True} if config.data_format == "raw_text" else None,
        logging_steps=config.logging_steps,
        logging_first_step=True,
        eval_strategy=("steps" if config.eval_steps else "epoch") if eval_ds is not None else "no",
        eval_steps=config.eval_steps or None,
        report_to="none",
        dataloader_pin_memory=device.startswith("cuda"),
        seed=config.seed,
        disable_tqdm=True,
        **_checkpoint_args(config),
    )

    class PlacedSFTTrainer(CheckedPlacementMixin, SFTTrainer):
        pass
    trainer = PlacedSFTTrainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        processing_class=tokenizer,
        peft_config=peft_config,
        callbacks=[_loss_logger(exp_dir, progress, on_log), _checkpoint_logger(exp_dir, progress, on_log), _cancellation_callback(check_cancel)],
    )
    _quantized_trainer_setup(trainer, quantized, runtime, progress)
    trainer.tinystudio_placement = runtime
    runtime.check_device(trainer.args.device)
    runtime.check_model(trainer.model)
    trainer.remove_callback(PrinterCallback)  # the loss log above replaces the default stdout dump
    trainable, total = _count_parameters(trainer.model)
    progress(f"trainable parameters: {trainable:,} of {total:,} ({trainable / total:.2%})")

    train_output = trainer.train()

    final_eval: dict[str, float] = {}
    if eval_ds is not None:
        final_eval = {k: float(v) for k, v in trainer.evaluate().items() if isinstance(v, (int, float))}

    runtime.check_model(trainer.model)
    check_cancel()
    quantized_result = _quantized_training_result(trainer, quantized, tokenizer, exp_dir, runtime, config.max_length)
    check_cancel()
    out = exp_dir / ("adapter" if config.update_strategy in ("lora", "qlora") else "model")
    trainer.model.save_pretrained(str(out))
    tokenizer.save_pretrained(str(out))

    result = {
        "output": str(out),
        "device": device,
        "objective": config.objective, "update_strategy": config.update_strategy,
        "output_kind": "quantized_adapter" if quantized is not None else "model" if config.update_strategy == "full" else "adapter",
        **quantized_result,
        "placement": runtime.report(), "data_contract": data_contract,
        "trainable_parameters": trainable,
        "total_parameters": total,
        "steps": int(train_output.global_step),
        "train_loss": float(train_output.training_loss),
        "metrics": {**{k: float(v) for k, v in train_output.metrics.items() if isinstance(v, (int, float))}, **final_eval},
    }
    del trainer, model
    gc.collect()
    return result


def _count_parameters(model: Any) -> tuple[int, int]:
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    return trainable, total


def read_loss_curve(exp_dir: Path) -> list[dict[str, Any]]:
    path = exp_dir / "loss.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def export_merged(exp_dir: Path, destination: Path, base_model: str, weights_source: str | None = None) -> Path:
    """Merge a LoRA adapter into the weights it was trained on and save a standalone model directory."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from .quantization import has_quantized_marker
    if has_quantized_marker(exp_dir):
        raise ProjectError("Quantized policy merged export is not qualified; retain its laboratory model bundle with the NF4 base and adapter instead")
    if destination.exists() and any(destination.iterdir()):
        raise ProjectError(f"{destination} is not empty")
    if (exp_dir / "model").exists():
        shutil.copytree(exp_dir / "model", destination, dirs_exist_ok=True)
        return destination
    from peft import PeftModel

    source = weights_source or base_model  # a refined adapter sits on its parent's merged weights
    base = AutoModelForCausalLM.from_pretrained(source, dtype=torch.float32)
    merged = PeftModel.from_pretrained(base, str(exp_dir / "adapter")).merge_and_unload()
    merged.save_pretrained(str(destination))
    AutoTokenizer.from_pretrained(str(exp_dir / "adapter")).save_pretrained(str(destination))
    return destination


def checkpoints_on_disk(exp_dir: Path) -> list[dict[str, Any]]:
    """Checkpoints saved during training, joined with checkpoints.jsonl records."""
    logged: dict[int, dict[str, Any]] = {}
    log_path = exp_dir / "checkpoints.jsonl"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                logged[int(rec["step"])] = rec
    found = []
    ck_dir = exp_dir / "checkpoints"
    if ck_dir.exists():
        for child in ck_dir.iterdir():
            m = re.fullmatch(r"checkpoint-(\d+)", child.name)
            if m and child.is_dir() and ((child / "adapter_config.json").exists() or (child / "config.json").exists()):
                step = int(m.group(1))
                rec = logged.get(step, {})
                size = sum(p.stat().st_size for p in child.rglob("*") if p.is_file())
                found.append({"step": step, "epoch": rec.get("epoch"), "loss": rec.get("loss"), "path": str(child), "bytes": size})
    return sorted(found, key=lambda r: r["step"])


def write_ollama_modelfile(model_dir: Path, destination: Path, system_prompt: str | None = None) -> Path:
    """Write an Ollama Modelfile that imports a merged safetensors directory (`ollama create NAME -f Modelfile`)."""
    lines = [f"FROM {model_dir.resolve()}"]
    if system_prompt:
        lines.append("SYSTEM " + json.dumps(system_prompt))
    lines += ["PARAMETER temperature 0", ""]
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination
