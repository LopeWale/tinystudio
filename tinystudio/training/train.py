"""Fine-tuning: SFT (full or LoRA, 4-bit QLoRA on CUDA) and DPO with TRL and PEFT.

This module trains into an experiment directory and reports through callbacks; jobs, experiments, and their
state live in PostgreSQL (see :mod:`tinystudio.services.jobs`). On Apple Silicon the run goes through PyTorch's
MPS backend in fp32: bf16 is switched off, 4-bit QLoRA is unavailable because bitsandbytes has no Metal backend,
and the fused AdamW is replaced by the plain one. On CUDA the run uses bf16 and the fused optimizer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tinystudio.core.project import ProjectError, now, read_json
from tinystudio.training.recommend import LORA_TARGETS, catalog_entry

ALIAS = re.compile(r"^ft-(?P<job>[A-Za-z0-9][A-Za-z0-9._-]{0,63})-(?P<number>\d+)$")


@dataclass
class TrainConfig:
    base_model: str
    training_type: str = "lora"  # "lora" | "full" | "qlora" | "dpo"
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
        if self.training_type not in ("lora", "full", "qlora", "dpo"):
            raise ProjectError("training_type must be 'lora', 'full', 'qlora', or 'dpo'")
        if not self.lora_targets:
            entry = catalog_entry(self.base_model)
            self.lora_targets = LORA_TARGETS.get(entry.family, []) if entry else []

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TrainConfig":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in value.items() if k in known})

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def train_experiment(exp_dir: Path, config: TrainConfig, train_rows: list[dict[str, Any]], val_rows: list[dict[str, Any]], progress: Any = print, weights: Path | None = None, on_log: Any = None) -> dict[str, Any]:
    """Train into ``exp_dir`` (adapter/ or model/, checkpoints/). ``on_log(kind, row)`` receives loss points and checkpoint records."""
    exp_dir.mkdir(parents=True, exist_ok=True)
    return _train(exp_dir, config, train_rows, val_rows, progress, weights, on_log or (lambda kind, row: None))


def preference_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Datapoints with a `rejected` answer, in TRL's conversational preference format (prompt / chosen / rejected)."""
    pairs = []
    for row in rows:
        rejected = row.get("rejected")
        chosen = row["messages"][-1]["content"]
        if isinstance(rejected, str) and rejected.strip() and rejected.strip() != chosen.strip():
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


def qlora_support() -> tuple[bool, str]:
    """Whether 4-bit QLoRA can run here: it needs a CUDA GPU and the bitsandbytes package."""
    import importlib.util

    import torch

    if not torch.cuda.is_available():
        return False, "QLoRA needs a CUDA GPU (bitsandbytes has no Apple GPU or CPU backend); use lora or full here, or run tinystudio on a CUDA machine"
    if importlib.util.find_spec("bitsandbytes") is None:
        return False, "QLoRA needs the bitsandbytes package: pip install bitsandbytes"
    return True, "ok"


def _load_4bit(source: str, device: str, progress: Any) -> Any:
    ok, reason = qlora_support()
    if not ok:
        raise ProjectError(reason)
    import torch
    from peft import prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    progress("loading the base model in 4-bit NF4 (QLoRA)")
    model = AutoModelForCausalLM.from_pretrained(source, quantization_config=quant, device_map={"": device})
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


def _train_dpo(exp_dir: Path, config: TrainConfig, train_rows: list[dict[str, Any]], val_rows: list[dict[str, Any]], progress: Any, weights: Path | None, on_log: Any) -> dict[str, Any]:
    """Direct Preference Optimization with LoRA adapters; the adapter-free base acts as the reference model."""
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, PrinterCallback
    from trl import DPOConfig, DPOTrainer

    from tinystudio.serving.runner import pick_device, pick_dtype

    train_pairs, val_pairs = preference_rows(train_rows), preference_rows(val_rows)
    if len(train_pairs) < 2:
        raise ProjectError(
            f"DPO needs training datapoints with a 'rejected' answer; this snapshot has {len(train_pairs)}. "
            "Correct traces and run `tinystudio trace promote DATASET --preference`, or add datapoints with a `rejected` field."
        )
    device = pick_device()
    dtype = pick_dtype(device)
    source = str(weights) if weights else config.base_model
    progress(f"device={device} dtype={str(dtype).replace('torch.', '')} base_model={config.base_model} weights={source} type=dpo pairs train={len(train_pairs)} val={len(val_pairs)} beta={config.dpo_beta}")

    tokenizer = AutoTokenizer.from_pretrained(source)
    model = AutoModelForCausalLM.from_pretrained(source, dtype=dtype)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    train_ds = Dataset.from_list(train_pairs)
    eval_ds = Dataset.from_list(val_pairs) if val_pairs else None
    peft_config = LoraConfig(r=config.lora_r, lora_alpha=config.lora_alpha, lora_dropout=config.lora_dropout, target_modules=config.lora_targets or "all-linear", task_type="CAUSAL_LM")
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
    trainer = DPOTrainer(model=model, ref_model=None, args=args, train_dataset=train_ds, eval_dataset=eval_ds, processing_class=tokenizer, peft_config=peft_config, callbacks=[_loss_logger(exp_dir, progress, on_log), _checkpoint_logger(exp_dir, progress, on_log)])
    trainer.remove_callback(PrinterCallback)
    trainable, total = _count_parameters(trainer.model)
    progress(f"trainable parameters: {trainable:,} of {total:,} ({trainable / total:.2%})")
    train_output = trainer.train()
    final_eval: dict[str, float] = {}
    if eval_ds is not None:
        final_eval = {k: float(v) for k, v in trainer.evaluate().items() if isinstance(v, (int, float))}
    out = exp_dir / "adapter"
    trainer.model.save_pretrained(str(out))
    tokenizer.save_pretrained(str(out))
    return {
        "output": str(out),
        "device": device,
        "trainable_parameters": trainable,
        "total_parameters": total,
        "steps": int(train_output.global_step),
        "train_loss": float(train_output.training_loss),
        "preference_pairs": {"train": len(train_pairs), "validation": len(val_pairs)},
        "metrics": {**{k: float(v) for k, v in train_output.metrics.items() if isinstance(v, (int, float))}, **final_eval},
    }


def _train(exp_dir: Path, config: TrainConfig, train_rows: list[dict[str, Any]], val_rows: list[dict[str, Any]], progress: Any, weights: Path | None, on_log: Any) -> dict[str, Any]:
    if config.training_type == "dpo":
        return _train_dpo(exp_dir, config, train_rows, val_rows, progress, weights, on_log)
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import torch
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer, PrinterCallback
    from trl import SFTConfig, SFTTrainer

    from tinystudio.serving.runner import pick_device, pick_dtype

    device = pick_device()
    dtype = pick_dtype(device)
    source = str(weights) if weights else config.base_model
    progress(f"device={device} dtype={str(dtype).replace('torch.', '')} base_model={config.base_model} weights={source} type={config.training_type} train={len(train_rows)} val={len(val_rows)}")

    tokenizer = AutoTokenizer.from_pretrained(source)
    if config.training_type == "qlora":
        model = _load_4bit(source, device, progress)
    else:
        model = AutoModelForCausalLM.from_pretrained(source, dtype=dtype)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    train_ds = Dataset.from_list([{"messages": row["messages"]} for row in train_rows])
    eval_ds = Dataset.from_list([{"messages": row["messages"]} for row in val_rows]) if val_rows else None

    peft_config = None
    if config.training_type in ("lora", "qlora"):
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

    trainer = SFTTrainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        processing_class=tokenizer,
        peft_config=peft_config,
        callbacks=[_loss_logger(exp_dir, progress, on_log), _checkpoint_logger(exp_dir, progress, on_log)],
    )
    trainer.remove_callback(PrinterCallback)  # the loss log above replaces the default stdout dump
    trainable, total = _count_parameters(trainer.model)
    progress(f"trainable parameters: {trainable:,} of {total:,} ({trainable / total:.2%})")

    train_output = trainer.train()

    final_eval: dict[str, float] = {}
    if eval_ds is not None:
        final_eval = {k: float(v) for k, v in trainer.evaluate().items() if isinstance(v, (int, float))}

    out = exp_dir / ("adapter" if config.training_type in ("lora", "qlora") else "model")
    trainer.model.save_pretrained(str(out))
    tokenizer.save_pretrained(str(out))

    return {
        "output": str(out),
        "device": device,
        "trainable_parameters": trainable,
        "total_parameters": total,
        "steps": int(train_output.global_step),
        "train_loss": float(train_output.training_loss),
        "metrics": {**{k: float(v) for k, v in train_output.metrics.items() if isinstance(v, (int, float))}, **final_eval},
    }


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
