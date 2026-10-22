"""Load a base model or a fine-tuned experiment and generate chat replies.

One ``ModelRunner`` per resolved model (see :func:`tinystudio.services.models.resolve`). Shared by the
inference service, evaluations, and the CLI so all produce the same text for the same input.
"""

from __future__ import annotations

import os

import json
import queue
import re
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOOL_CALL_BLOCK = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)

from tinystudio.core.project import ProjectError

STREAM_TIMEOUT_SECONDS = 300.0


def pick_device() -> str:
    """TINYSTUDIO_DEVICE when set (cuda, cuda:1, mps, cpu); otherwise the first accelerator found."""
    import torch

    override = os.environ.get("TINYSTUDIO_DEVICE", "").strip().split("#", 1)[0].strip()
    if override and override.lower() != "auto":
        try:
            torch.device(override)
            return override
        except RuntimeError:
            import warnings

            warnings.warn(f"TINYSTUDIO_DEVICE={override!r} is not a valid device; using automatic selection", stacklevel=2)
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def pick_dtype(device: str) -> Any:
    """Weights dtype for a device: TINYSTUDIO_DTYPE (fp32, bf16, fp16) when set; bf16 on CUDA GPUs that support it; fp32 elsewhere (MPS and CPU)."""
    import torch

    name = os.environ.get("TINYSTUDIO_DTYPE", "auto").strip().lower()
    table = {"fp32": torch.float32, "float32": torch.float32, "bf16": torch.bfloat16, "bfloat16": torch.bfloat16, "fp16": torch.float16, "float16": torch.float16}
    if name in table:
        return table[name]
    if device.startswith("cuda") and torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        return torch.bfloat16
    return torch.float32


@dataclass
class ResolvedModel:
    model_id: str
    base_model: str
    adapter_dir: Path | None
    model_dir: Path | None
    kind: str  # "base" | "lora" | "full"


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
        model = AutoModelForCausalLM.from_pretrained(source, dtype=pick_dtype(self.device))
        if resolved.adapter_dir is not None:
            from peft import PeftModel

            model = PeftModel.from_pretrained(model, str(resolved.adapter_dir))
            model = model.merge_and_unload()  # returns a new module; not in place
        model.eval()
        self.model = model.to(self.device)
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

    def _encode(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Any:
        kwargs: dict[str, Any] = {"add_generation_prompt": True, "return_tensors": "pt", "return_dict": True}
        if tools:
            kwargs["tools"] = tools  # templates without tool support ignore the variable
        encoded = self.tokenizer.apply_chat_template(messages, **kwargs)
        return {key: value.to(self.device) for key, value in encoded.items()}

    def count_tokens(self, messages: list[dict[str, Any]], completion: str) -> tuple[int, int]:
        """Prompt and completion token counts for a finished (streamed) exchange."""
        # transformers 5 returns a BatchEncoding dict from apply_chat_template(tokenize=True);
        # return_dict=False gives the plain token-id list on both 4.x and 5.x.
        encoded = self.tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=True, return_dict=False)
        prompt = len(encoded["input_ids"]) if isinstance(encoded, dict) else len(encoded)
        return prompt, len(self.tokenizer(completion, add_special_tokens=False)["input_ids"])

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

    def generate(self, messages: list[dict[str, Any]], max_new_tokens: int = 256, temperature: float = 0.0, top_p: float = 0.95, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        import torch

        with self.lock:
            inputs = self._encode(messages, tools)
            prompt_tokens = int(inputs["input_ids"].shape[1])
            started = time.perf_counter()
            with torch.no_grad():
                output = self.model.generate(**inputs, **self._generation_kwargs(max_new_tokens, temperature, top_p))
            elapsed = time.perf_counter() - started
        new_tokens = output[0, prompt_tokens:]
        text = self.tokenizer.decode(new_tokens, skip_special_tokens=True)
        completion_tokens = int(new_tokens.shape[0])
        finish = "length" if completion_tokens >= max_new_tokens else "stop"
        result = {
            "content": text.strip(),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "finish_reason": finish,
            "seconds": round(elapsed, 3),
        }
        if tools:
            calls = parse_tool_calls(text)
            if calls:
                result.update(tool_calls=calls, finish_reason="tool_calls", content="")
        return result

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

    def generate_json(self, messages: list[dict[str, Any]], response_format: dict[str, Any], max_new_tokens: int = 256, temperature: float = 0.0, top_p: float = 0.95, attempts: int = 3) -> dict[str, Any]:
        """JSON mode by instruction, validation, and retry (tiny models have no constrained decoding here)."""
        from tinystudio.core.llm import LLMError, extract_json

        schema = (response_format.get("json_schema") or {}).get("schema") if response_format.get("type") == "json_schema" else None
        instruction = "Respond with only a valid JSON object and no prose, markdown, or code fences."
        if schema:
            instruction += " The object must match this JSON Schema: " + json.dumps(schema, separators=(",", ":"))
        last: dict[str, Any] = {}
        for attempt in range(1, attempts + 1):
            prompt = _with_system_suffix(messages, instruction if attempt == 1 else instruction + " Your previous answer was not valid JSON for the schema; output the JSON object only.")
            last = self.generate(prompt, max_new_tokens, temperature if attempt == 1 else 0.0, top_p)
            try:
                value = extract_json(last["content"])
            except LLMError:
                continue
            problems = validate_schema(value, schema) if schema else ([] if isinstance(value, dict) else ["top-level value is not an object"])
            if not problems:
                return {**last, "content": json.dumps(value, ensure_ascii=False), "json_valid": True, "attempts": attempt}
        return {**last, "json_valid": False, "attempts": attempts}

    def unload(self) -> None:
        import gc

        import torch

        del self.model
        gc.collect()
        if self.device == "mps":
            torch.mps.empty_cache()


def _with_system_suffix(messages: list[dict[str, Any]], suffix: str) -> list[dict[str, Any]]:
    rows = [dict(m) for m in messages]
    if rows and rows[0].get("role") == "system":
        rows[0]["content"] = f"{rows[0]['content']}\n\n{suffix}"
    else:
        rows.insert(0, {"role": "system", "content": suffix})
    return rows


def parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """Recover function calls from the formats small chat templates emit (Hermes/Qwen/SmolLM `<tool_call>` blocks, Llama 3 bare JSON)."""
    candidates: list[Any] = []
    for block in TOOL_CALL_BLOCK.findall(text):
        try:
            candidates.append(json.loads(block))
        except json.JSONDecodeError:
            continue
    if not candidates:
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                candidates.append(json.loads(stripped))
            except json.JSONDecodeError:
                pass
    calls = []
    for item in candidates:
        if not isinstance(item, dict) or "name" not in item:
            continue
        arguments = item.get("arguments", item.get("parameters", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"raw": arguments}
        calls.append({"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function", "function": {"name": str(item["name"]), "arguments": json.dumps(arguments if isinstance(arguments, dict) else {"value": arguments}, ensure_ascii=False)}})
    return calls


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Small JSON Schema subset: type, properties, required, additionalProperties, items, enum."""
    problems: list[str] = []
    expected = schema.get("type")
    types = expected if isinstance(expected, list) else ([expected] if expected else [])
    checks = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list), "string": lambda v: isinstance(v, str), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool), "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool), "boolean": lambda v: isinstance(v, bool), "null": lambda v: v is None}
    if types and not any(checks.get(t, lambda v: True)(value) for t in types):
        return [f"{path}: expected {types}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: not one of {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{path}.{key}: required")
        for key, sub in props.items():
            if key in value:
                problems += validate_schema(value[key], sub, f"{path}.{key}")
        if schema.get("additionalProperties") is False:
            problems += [f"{path}.{key}: not allowed" for key in value if key not in props]
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            problems += validate_schema(item, schema["items"], f"{path}[{i}]")
    return problems
