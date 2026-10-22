"""Fine-tuning jobs and experiments.

A job pins a snapshot. Each experiment under it pins a base model and a
training configuration, then produces either a LoRA adapter (``adapter/``) or
a full set of weights (``model/``). The lifecycle mirrors Studio's
``pending -> running -> succeeded | failed`` and is written to
``status.json`` so you can poll it from another shell. Every experiment gets
a model alias ``ft-<job>-<n>`` that the server and the ``chat``/``eval``
commands accept as a model id once it has succeeded.

Training uses TRL's ``SFTTrainer`` on the conversational ``messages`` format,
so the chat template is applied for you, and PEFT for LoRA. On Apple Silicon
the run goes through PyTorch's MPS backend in fp32: bf16 is switched off
explicitly because TRL defaults it on, 4-bit QLoRA is unavailable because
bitsandbytes has no Metal backend, and the fused AdamW is replaced by the
plain one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .data import load_snapshot, snapshot_meta
from .project import Project, ProjectError, now, read_json, write_json
from .recommend import LORA_TARGETS, catalog_entry

ALIAS = re.compile(r"^ft-(?P<job>[A-Za-z0-9][A-Za-z0-9._-]{0,63})-(?P<number>\d+)$")


@dataclass
class TrainConfig:
    base_model: str
    training_type: str = "lora"  # "lora" | "full"
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

    def __post_init__(self) -> None:
        if self.training_type not in ("lora", "full"):
            raise ProjectError("training_type must be 'lora' or 'full' (QLoRA needs bitsandbytes, which has no Apple GPU backend)")
        if not self.lora_targets:
            entry = catalog_entry(self.base_model)
            self.lora_targets = LORA_TARGETS.get(entry.family, []) if entry else []

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TrainConfig":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in value.items() if k in known})


def alias_for(job: str, number: int) -> str:
    return f"ft-{job}-{number}"


def parse_alias(model_id: str) -> tuple[str, int] | None:
    match = ALIAS.match(model_id)
    return (match["job"], int(match["number"])) if match else None


def create_job(project: Project, name: str, snapshot: str, goal: str = "", reasoning: bool = False) -> dict[str, Any]:
    snapshot_meta(project, snapshot)
    job_dir = project.job_dir(name)
    if (job_dir / "job.json").exists():
        raise ProjectError(f"job {name!r} already exists; add an experiment to it with `finetune --job {name}`")
    job = {"name": name, "snapshot": snapshot, "goal": goal, "reasoning": reasoning, "created": now(), "experiments": []}
    write_json(job_dir / "job.json", job)
    return job


def job_meta(project: Project, name: str) -> dict[str, Any]:
    path = project.job_dir(name) / "job.json"
    if not path.exists():
        raise ProjectError(f"job {name!r} does not exist")
    return read_json(path)


def delete_job(project: Project, name: str) -> None:
    job_meta(project, name)
    shutil.rmtree(project.job_dir(name))


def experiment_dir(project: Project, job: str, number: int) -> Path:
    return project.job_dir(job) / "experiments" / str(number)


def list_experiments(project: Project, job: str) -> list[dict[str, Any]]:
    base = project.job_dir(job) / "experiments"
    if not base.exists():
        return []
    rows = []
    for child in sorted(base.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
        if (child / "status.json").exists() and (child / "config.json").exists():
            status = read_json(child / "status.json")
            rows.append({"number": int(child.name), "alias": alias_for(job, int(child.name)), **status, "config": read_json(child / "config.json")})
    return rows


def delete_experiment(project: Project, job: str, number: int) -> None:
    exp_dir = experiment_dir(project, job, number)
    if not exp_dir.exists():
        raise ProjectError(f"experiment {job}/{number} does not exist")
    status = read_json(exp_dir / "status.json") if (exp_dir / "status.json").exists() else {}
    if status.get("state") == "running":
        raise ProjectError(f"experiment {job}/{number} is running; stop its task first")
    shutil.rmtree(exp_dir)
    meta = job_meta(project, job)
    meta["experiments"] = [n for n in meta["experiments"] if n != number]
    write_json(project.job_dir(job) / "job.json", meta)


def claim_experiment_dir(project: Project, job: str) -> tuple[int, Path]:
    """Create the next free experiments/<n>/ directory: max existing number + 1, skipping any that already exist."""
    base = project.job_dir(job) / "experiments"
    base.mkdir(parents=True, exist_ok=True)
    taken = [int(child.name) for child in base.iterdir() if child.name.isdigit()]
    number = max(taken, default=0) + 1
    while True:
        exp_dir = base / str(number)
        try:
            exp_dir.mkdir(exist_ok=False)
            return number, exp_dir
        except FileExistsError:
            number += 1


def _set_status(exp_dir: Path, state: str, **extra: Any) -> dict[str, Any]:
    status = read_json(exp_dir / "status.json") if (exp_dir / "status.json").exists() else {}
    status.update({"state": state, "updated": now(), **extra})
    write_json(exp_dir / "status.json", status)
    return status


def create_pending_experiment(project: Project, job: str, config: TrainConfig) -> int:
    """Register an experiment without training it (Studio's "configure, then Start Experiments")."""
    meta = job_meta(project, job)
    number, exp_dir = claim_experiment_dir(project, job)
    write_json(exp_dir / "config.json", asdict(config))
    _set_status(exp_dir, "pending", created=now(), job=job, snapshot=meta["snapshot"], alias=alias_for(job, number))
    meta["experiments"].append(number)
    write_json(project.job_dir(job) / "job.json", meta)
    return number


def run_experiment(project: Project, job: str, config: TrainConfig, progress: Any = print) -> dict[str, Any]:
    """Create and train one experiment synchronously. Returns the final status document."""
    number = create_pending_experiment(project, job, config)
    return run_experiment_number(project, job, number, progress)


def run_pending_experiments(project: Project, job: str, progress: Any = print) -> list[dict[str, Any]]:
    """Train every pending experiment of a job in order (one at a time; there is one GPU)."""
    results = []
    for exp in list_experiments(project, job):
        if exp.get("state") == "pending":
            progress(f"=== experiment {job}/{exp['number']} ({exp['config']['base_model']}, {exp['config']['training_type']})")
            results.append(run_experiment_number(project, job, exp["number"], progress))
    return results


def run_experiment_number(project: Project, job: str, number: int, progress: Any = print) -> dict[str, Any]:
    meta = job_meta(project, job)
    exp_dir = experiment_dir(project, job, number)
    if not (exp_dir / "config.json").exists():
        raise ProjectError(f"experiment {job}/{number} does not exist")
    config = TrainConfig.from_dict(read_json(exp_dir / "config.json"))
    train_rows, val_rows = load_snapshot(project, meta["snapshot"])
    started = time.perf_counter()
    try:
        _set_status(exp_dir, "running", started_at=now())
        result = _train(exp_dir, config, train_rows, val_rows, progress)
        status = _set_status(exp_dir, "succeeded", seconds=round(time.perf_counter() - started, 1), **result)
    except KeyboardInterrupt:
        status = _set_status(exp_dir, "failed", error="interrupted", seconds=round(time.perf_counter() - started, 1))
    except Exception as cause:  # noqa: BLE001 - the state file must record any failure reason
        status = _set_status(
            exp_dir,
            "failed",
            error=f"{type(cause).__name__}: {cause}",
            traceback=traceback.format_exc()[-4000:],
            seconds=round(time.perf_counter() - started, 1),
        )
    status["number"] = number
    return status


def _train(exp_dir: Path, config: TrainConfig, train_rows: list[dict[str, Any]], val_rows: list[dict[str, Any]], progress: Any) -> dict[str, Any]:
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import torch
    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer, PrinterCallback, TrainerCallback
    from trl import SFTConfig, SFTTrainer

    from .runner import pick_device

    device = pick_device()
    progress(f"device={device} base_model={config.base_model} type={config.training_type} train={len(train_rows)} val={len(val_rows)}")

    tokenizer = AutoTokenizer.from_pretrained(config.base_model)
    model = AutoModelForCausalLM.from_pretrained(config.base_model, dtype=torch.float32)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    train_ds = Dataset.from_list([{"messages": row["messages"]} for row in train_rows])
    eval_ds = Dataset.from_list([{"messages": row["messages"]} for row in val_rows]) if val_rows else None

    peft_config = None
    if config.training_type == "lora":
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
        optim="adamw_torch",  # the fused default is CUDA-oriented
        bf16=False,
        fp16=False,
        logging_steps=config.logging_steps,
        logging_first_step=True,
        eval_strategy=("steps" if config.eval_steps else "epoch") if eval_ds is not None else "no",
        eval_steps=config.eval_steps or None,
        save_strategy="no",
        report_to="none",
        dataloader_pin_memory=False,
        seed=config.seed,
        disable_tqdm=True,
    )

    loss_path = exp_dir / "loss.jsonl"

    class LossLog(TrainerCallback):
        def on_log(self, args: Any, state: Any, control: Any, logs: dict[str, float] | None = None, **kwargs: Any) -> None:
            if not logs:
                return
            row = {"step": state.global_step, "epoch": round(state.epoch or 0, 3), "time": now(), **{k: v for k, v in logs.items() if isinstance(v, (int, float))}}
            with loss_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            shown = {k: round(v, 4) for k, v in row.items() if k in ("loss", "eval_loss", "learning_rate", "mean_token_accuracy", "eval_mean_token_accuracy")}
            if shown:
                progress(f"step {row['step']} epoch {row['epoch']} " + " ".join(f"{k}={v:g}" for k, v in shown.items()))

    trainer = SFTTrainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        processing_class=tokenizer,
        peft_config=peft_config,
        callbacks=[LossLog()],
    )
    trainer.remove_callback(PrinterCallback)  # the loss log above replaces the default stdout dump
    trainable, total = _count_parameters(trainer.model)
    progress(f"trainable parameters: {trainable:,} of {total:,} ({trainable / total:.2%})")

    train_output = trainer.train()

    final_eval: dict[str, float] = {}
    if eval_ds is not None:
        final_eval = {k: float(v) for k, v in trainer.evaluate().items() if isinstance(v, (int, float))}

    out = exp_dir / ("adapter" if config.training_type == "lora" else "model")
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


def read_loss_curve(project: Project, job: str, number: int) -> list[dict[str, Any]]:
    path = experiment_dir(project, job, number) / "loss.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def export_merged(project: Project, job: str, number: int, destination: Path) -> Path:
    """Merge a LoRA adapter into its base model and save a standalone model directory."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    exp_dir = experiment_dir(project, job, number)
    config = read_json(exp_dir / "config.json")
    if destination.exists() and any(destination.iterdir()):
        raise ProjectError(f"{destination} is not empty")
    if (exp_dir / "model").exists():
        shutil.copytree(exp_dir / "model", destination, dirs_exist_ok=True)
        return destination
    from peft import PeftModel

    base = AutoModelForCausalLM.from_pretrained(config["base_model"], dtype=torch.float32)
    merged = PeftModel.from_pretrained(base, str(exp_dir / "adapter")).merge_and_unload()
    merged.save_pretrained(str(destination))
    AutoTokenizer.from_pretrained(str(exp_dir / "adapter")).save_pretrained(str(destination))
    return destination
