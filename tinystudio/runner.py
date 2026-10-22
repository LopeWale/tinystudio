"""Load a base model or a fine-tuned experiment and generate chat replies.

One ``ModelRunner`` per model id. Ids are either a Hugging Face model id
(``HuggingFaceTB/SmolLM2-135M-Instruct``) for a baseline, or
``<job>/<experiment>`` for something you trained here. Shared by ``serve``,
``eval``, and ``chat`` so all three produce the same text for the same input.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project import SAFE_NAME, Project, ProjectError, read_json
from .train import parse_alias

STREAM_TIMEOUT_SECONDS = 300.0


def pick_device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


@dataclass
class ResolvedModel:
    model_id: str
    base_model: str
    adapter_dir: Path | None
    model_dir: Path | None
    kind: str  # "base" | "lora" | "full"


def resolve_model(project: Project | None, model_id: str) -> ResolvedModel:
    """Map an id to weights on disk: a model directory, ``job/experiment`` inside a project, or a Hub id."""
    local = Path(model_id).expanduser()
    if local.is_dir() and (local / "config.json").exists():
        return ResolvedModel(model_id, str(local), None, local, "full")
    if project is not None:
        alias = parse_alias(model_id)
        if alias is not None and (project.job_dir(alias[0]) / "experiments" / str(alias[1]) / "status.json").exists():
            model_id = f"{alias[0]}/{alias[1]}"
    job, _, experiment = model_id.partition("/")
    if project is not None and experiment.isdigit() and SAFE_NAME.match(job):
        exp_dir = project.job_dir(job) / "experiments" / experiment
        if (exp_dir / "status.json").exists():
            status = read_json(exp_dir / "status.json")
            if status.get("state") != "succeeded":
                raise ProjectError(f"experiment {model_id} is in state {status.get('state')!r}; only succeeded experiments can be loaded")
            config = read_json(exp_dir / "config.json")
            if (exp_dir / "adapter").exists():
                return ResolvedModel(model_id, config["base_model"], exp_dir / "adapter", None, "lora")
            if (exp_dir / "model").exists():
                return ResolvedModel(model_id, config["base_model"], None, exp_dir / "model", "full")
            raise ProjectError(f"experiment {model_id} has no adapter/ or model/ directory")
    return ResolvedModel(model_id, model_id, None, None, "base")


class ModelRunner:
    """A loaded causal LM plus its tokenizer, with a lock so one GPU serves one request at a time."""

    def __init__(self, resolved: ResolvedModel, device: str | None = None) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.resolved = resolved
        self.device = device or pick_device()
        self.lock = threading.Lock()
        source = str(resolved.model_dir) if resolved.model_dir else resolved.base_model
        self.tokenizer = AutoTokenizer.from_pretrained(source)
        model = AutoModelForCausalLM.from_pretrained(source, dtype=torch.float32)
        if resolved.adapter_dir is not None:
            from peft import PeftModel

            model = PeftModel.from_pretrained(model, str(resolved.adapter_dir))
            model = model.merge_and_unload()  # returns a new module; not in place
        model.eval()
        self.model = model.to(self.device)
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

    def _encode(self, messages: list[dict[str, str]]) -> Any:
        encoded = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
        )
        return {key: value.to(self.device) for key, value in encoded.items()}

    def _generation_kwargs(self, max_new_tokens: int, temperature: float, top_p: float) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "pad_token_id": self.tokenizer.pad_token_id,
            "eos_token_id": self.tokenizer.eos_token_id,
        }
        if temperature > 0:
            kwargs.update(do_sample=True, temperature=temperature, top_p=top_p)
        else:
            kwargs.update(do_sample=False)
        return kwargs

    def generate(self, messages: list[dict[str, str]], max_new_tokens: int = 256, temperature: float = 0.0, top_p: float = 0.95) -> dict[str, Any]:
        import torch

        with self.lock:
            inputs = self._encode(messages)
            prompt_tokens = int(inputs["input_ids"].shape[1])
            started = time.perf_counter()
            with torch.no_grad():
                output = self.model.generate(**inputs, **self._generation_kwargs(max_new_tokens, temperature, top_p))
            elapsed = time.perf_counter() - started
        new_tokens = output[0, prompt_tokens:]
        text = self.tokenizer.decode(new_tokens, skip_special_tokens=True)
        completion_tokens = int(new_tokens.shape[0])
        finish = "length" if completion_tokens >= max_new_tokens else "stop"
        return {
            "content": text.strip(),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "finish_reason": finish,
            "seconds": round(elapsed, 3),
        }

    def stream(self, messages: list[dict[str, str]], max_new_tokens: int = 256, temperature: float = 0.0, top_p: float = 0.95) -> Iterator[str]:
        """Yield text deltas as they are produced."""
        import torch
        from transformers import TextIteratorStreamer

        with self.lock:
            inputs = self._encode(messages)
            streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True, timeout=STREAM_TIMEOUT_SECONDS)
            kwargs = {**inputs, **self._generation_kwargs(max_new_tokens, temperature, top_p), "streamer": streamer}
            failure: list[BaseException] = []

            def worker() -> None:
                try:
                    with torch.no_grad():
                        self.model.generate(**kwargs)
                except BaseException as cause:  # noqa: BLE001 - re-raised on the consumer side below
                    failure.append(cause)
                    streamer.end()  # generate() did not reach its own end(); unblock the consumer

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            try:
                for delta in streamer:
                    if delta:
                        yield delta
            except queue.Empty as cause:
                raise RuntimeError(f"generation produced no output for {STREAM_TIMEOUT_SECONDS:g} s") from cause
            finally:
                thread.join(timeout=STREAM_TIMEOUT_SECONDS)
            if failure:
                raise RuntimeError(f"generation failed: {type(failure[0]).__name__}: {failure[0]}") from failure[0]

    def unload(self) -> None:
        import gc

        import torch

        del self.model
        gc.collect()
        if self.device == "mps":
            torch.mps.empty_cache()
