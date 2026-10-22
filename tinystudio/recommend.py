"""Base-model and hyperparameter recommendations.

Studio asks for the goal and the dataset, then recommends base models and a
training configuration. Here the same decision is a small, inspectable
heuristic over three facts you can read yourself: dataset size, the longest
conversations in it, and how much unified memory this Mac has.

Memory estimates are rough. Full fine-tuning with AdamW in fp32 needs about
16 bytes per parameter (weights 4, gradients 4, two optimizer moments 8)
before activations. LoRA keeps the frozen weights in fp32 (4 bytes per
parameter) and trains a few million adapter weights on top.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

GIB = 1024**3


@dataclass(frozen=True)
class BaseModel:
    id: str
    params_b: float  # billions of parameters
    family: str  # architecture family, which fixes the LoRA target module names
    license: str
    notes: str


# Open-weight instruct models small enough to fine-tune on a laptop. Parameter
# counts and licenses come from the model cards (see README for citations).
CATALOG: tuple[BaseModel, ...] = (
    BaseModel("HuggingFaceTB/SmolLM2-135M-Instruct", 0.135, "llama", "apache-2.0", "fastest to iterate; learns formats and narrow domains, weak at reasoning"),
    BaseModel("HuggingFaceTB/SmolLM2-360M-Instruct", 0.36, "llama", "apache-2.0", "good balance for domain Q&A on a laptop"),
    BaseModel("Qwen/Qwen2.5-0.5B-Instruct", 0.49, "qwen2", "apache-2.0", "strongest general ability under 1B; multilingual; chat template supports assistant-only loss"),
    BaseModel("HuggingFaceTB/SmolLM2-1.7B-Instruct", 1.7, "llama", "apache-2.0", "LoRA only on a 24 GB Mac; noticeably better fluency"),
    BaseModel("Qwen/Qwen2.5-1.5B-Instruct", 1.54, "qwen2", "apache-2.0", "LoRA only on a 24 GB Mac; best quality in this list"),
)

# The seven linear projections shared by Llama- and Qwen2-style decoders.
LORA_TARGETS: dict[str, list[str]] = {
    "llama": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    "qwen2": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
}


def catalog_entry(model_id: str) -> BaseModel | None:
    return next((m for m in CATALOG if m.id == model_id), None)


def host_memory_bytes() -> int | None:
    """Unified memory on macOS via sysctl; None when unknown."""
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True, timeout=5)
        return int(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def estimate_chars(rows: list[dict[str, Any]]) -> tuple[int, int]:
    """(average, maximum) characters per conversation; ~4 characters per token for English text."""
    sizes = [sum(len(m["content"]) for m in row["messages"]) for row in rows] or [0]
    return sum(sizes) // len(sizes), max(sizes)


def full_finetune_bytes(params_b: float) -> float:
    return params_b * 1e9 * 16


def lora_bytes(params_b: float) -> float:
    return params_b * 1e9 * 4 + 1.5 * GIB


def recommend(train_rows: list[dict[str, Any]], goal: str = "", memory_bytes: int | None = None) -> dict[str, Any]:
    n = len(train_rows)
    avg_chars, max_chars = estimate_chars(train_rows)
    memory = memory_bytes if memory_bytes is not None else host_memory_bytes()
    budget = (memory or 16 * GIB) * 0.6  # leave headroom for activations, the OS, and your editor

    epochs = 5 if n < 100 else 3 if n < 500 else 2 if n < 5000 else 1
    max_length = min(4096, max(256, ((max_chars // 4) // 128 + 2) * 128))
    batch_size = 8 if max_length <= 512 else 4 if max_length <= 1024 else 2 if max_length <= 2048 else 1
    grad_accum = max(1, 16 // batch_size)

    options: list[dict[str, Any]] = []
    for model in CATALOG:
        fits_full = full_finetune_bytes(model.params_b) <= budget
        fits_lora = lora_bytes(model.params_b) <= budget
        if not fits_lora:
            continue
        # Tiny models with little data benefit from updating every weight;
        # bigger models or bigger corpora are safer and faster with LoRA.
        training_type = "full" if fits_full and model.params_b <= 0.4 and n < 2000 else "lora"
        learning_rate = 5e-5 if training_type == "full" else 2e-4
        options.append(
            {
                "base_model": model.id,
                "params_b": model.params_b,
                "license": model.license,
                "training_type": training_type,
                "full_finetune_fits": fits_full,
                "hyperparameters": {
                    "epochs": epochs,
                    "learning_rate": learning_rate,
                    "batch_size": batch_size,
                    "gradient_accumulation_steps": grad_accum,
                    "max_length": max_length,
                    "lora_r": 16,
                    "lora_alpha": 32,
                    "lora_dropout": 0.05,
                    "lora_targets": LORA_TARGETS[model.family],
                },
                "why": model.notes,
            }
        )

    # Rank: with under a thousand examples, prefer the smallest model (fast
    # loops, less overfitting, and a narrow task rarely needs more); with more
    # data, prefer the most capable model that fits in memory.
    if n < 1000:
        ranked = sorted(options, key=lambda option: option["params_b"])
    else:
        ranked = sorted(options, key=lambda option: -option["params_b"])

    return {
        "goal": goal,
        "dataset": {"training_count": n, "avg_chars": avg_chars, "max_chars": max_chars},
        "host": {"memory_gib": round(memory / GIB, 1) if memory else None, "budget_gib": round(budget / GIB, 1)},
        "recommended": ranked[0] if ranked else None,
        "options": ranked,
        "notes": [
            "QLoRA (4-bit bitsandbytes) is not available on Apple GPUs; use full or lora.",
            "Training runs in fp32 on MPS; expect roughly 1-5 s per step for these sizes at batch 8 x 512 tokens.",
            "Hold out validation data (dataset split) so evaluation measures generalisation, not memorisation.",
        ],
    }


def catalog_as_dicts() -> list[dict[str, Any]]:
    return [asdict(m) for m in CATALOG]
