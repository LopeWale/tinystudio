"""Interpretability: per-neuron MLP activations of a model over a dataset, with the tests from
Cammarata's "Neural Networks Are Already Clean" (Goodfire, 2026) that fit on one machine.

What a *capture* records, per MLP layer and neuron, over every token of a dataset's chat-templated
conversations:

- the top-k positive and top-k negative activations (the input of ``down_proj``, which for SwiGLU is
  ``silu(gate) * value``), each with its gate pre-activation ``g`` and value ``v`` so the four sign
  regimes (+/+, +/-, -/+, -/-) are visible; the -/- regime is the "double-negative echo" the essay
  describes: a closed gate (negative g, silu(g) >= -0.278) times a negative value gives a small
  positive activation that looks like the negative pole;
- a top-k restricted to open gates (g > 0), so the echo can be hidden;
- the per-datapoint maximum and minimum, used for before/after comparisons and drift;
- summary statistics (max, min, gate-open max, echo share, density).

Everything is stored under ``interp/captures/<name>/`` as JSON plus one ``.npz`` per layer. The
helper LLM is only needed for explanations and the blind-maximum / near-miss tests.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from tinystudio.core.llm import LLMClient, LLMError
from tinystudio.core.project import ProjectError, now, read_json, write_json

REGIMES = {3: "+/+", 2: "+/-", 1: "-/+", 0: "-/-"}
STRONG_RATIO = 0.3  # the essay counts a response as strong at 30% of the neuron's strongest corpus example
CONTEXT_TOKENS = 12
SAFE_CHARS = re.compile(r"[^A-Za-z0-9 ._-]+")


class InterpStore(Protocol):
    """What the capture code needs from the application: where artifacts go, rows, model resolution, registration."""

    captures_dir: Path
    diffs_dir: Path

    def rows_for(self, dataset: str | None, snapshot: str | None) -> list[dict[str, Any]]: ...

    def resolve_model(self, model_id: str) -> Any: ...

    def experiment_checkpoints(self, job: str, number: int) -> list[dict[str, Any]]: ...

    def experiment_succeeded(self, job: str, number: int) -> bool: ...

    def register(self, kind: str, name: str, meta: dict[str, Any]) -> None: ...

    def unregister(self, kind: str, name: str) -> None: ...


class LocalStore:
    """A store over a plain directory with no database: base models only (tests, ad-hoc analysis)."""

    def __init__(self, root: Path, rows: list[dict[str, Any]] | None = None) -> None:
        self.captures_dir = root / "captures"
        self.diffs_dir = root / "diffs"
        self._rows = rows or []

    def rows_for(self, dataset: str | None, snapshot: str | None) -> list[dict[str, Any]]:
        return list(self._rows)

    def resolve_model(self, model_id: str) -> Any:
        from tinystudio.serving.runner import ResolvedModel

        local = Path(model_id).expanduser()
        if local.is_dir() and (local / "config.json").exists():
            return ResolvedModel(model_id, str(local), None, local, "full")
        return ResolvedModel(model_id, model_id, None, None, "base")

    def experiment_checkpoints(self, job: str, number: int) -> list[dict[str, Any]]:
        return []

    def experiment_succeeded(self, job: str, number: int) -> bool:
        return False

    def register(self, kind: str, name: str, meta: dict[str, Any]) -> None:
        return None

    def unregister(self, kind: str, name: str) -> None:
        return None


class InterpError(ProjectError):
    """Capture, diff, or test could not run; the message names the missing piece."""


# ------------------------------------------------------------------ specs and paths
@dataclass
class CaptureSpec:
    model_id: str
    dataset: str | None = None
    snapshot: str | None = None
    layers: list[int] = field(default_factory=list)  # empty = all layers
    topk: int = 40
    batch_size: int = 0  # 0 = as many rows as fit about 8k tokens per forward pass
    max_length: int = 1024
    name: str = ""

    def source(self) -> str:
        return self.dataset or self.snapshot or ""


@dataclass
class DiffSpec:
    experiment: str
    base: str | None = None  # default: the experiment's own base weights
    dataset: str | None = None
    snapshot: str | None = None
    layers: list[int] = field(default_factory=list)
    batch_size: int = 0  # 0 = automatic token budget
    max_length: int = 1024
    name: str = ""
    top_delta: int = 5


def safe_part(text: str) -> str:
    return SAFE_CHARS.sub("-", text.replace("/", "-").replace("@", "-step")).strip("-") or "x"


def captures_dir(project: InterpStore) -> Path:
    return project.captures_dir


def capture_dir(project: InterpStore, name: str) -> Path:
    prepare = getattr(project, "artifact_directory", None)
    if prepare:
        return prepare("capture", name)
    if not name or Path(name).name != name or name in {".", ".."} or "\\" in name:
        raise InterpError("invalid capture name")
    return captures_dir(project) / name


def diffs_dir(project: InterpStore) -> Path:
    return project.diffs_dir


def diff_dir(project: InterpStore, name: str) -> Path:
    prepare = getattr(project, "artifact_directory", None)
    if prepare:
        return prepare("diff", name)
    if not name or Path(name).name != name or name in {".", ".."} or "\\" in name:
        raise InterpError("invalid comparison name")
    return diffs_dir(project) / name


def default_capture_name(model_id: str, source: str) -> str:
    return f"{safe_part(model_id)}--{safe_part(source)}"


def list_captures(project: InterpStore) -> list[dict[str, Any]]:
    listing = getattr(project, "list_artifacts", None)
    if listing:
        return listing("capture")
    base = captures_dir(project)
    if not base.exists():
        return []
    out = []
    for child in sorted(base.iterdir()):
        if (child / "meta.json").exists():
            meta = read_json(child / "meta.json")
            meta["explained"] = len(latest_records(child / "explanations.jsonl"))
            meta["tested"] = len(latest_records(child / "tests.jsonl"))
            out.append(meta)
    return out


def capture_meta(project: InterpStore, name: str) -> dict[str, Any]:
    path = capture_dir(project, name) / "meta.json"
    if not path.exists():
        raise InterpError(f"capture {name!r} does not exist; run `tinystudio interp capture MODEL --dataset D` first")
    return read_json(path)


def delete_capture(project: InterpStore, name: str) -> None:
    import shutil

    delete = getattr(project, "delete_artifact", None)
    if delete:
        delete("capture", name)
        return
    directory = capture_dir(project, name)
    if not (directory / "meta.json").exists():
        raise InterpError(f"capture {name!r} does not exist")
    shutil.rmtree(directory)
    project.unregister("capture", name)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_capture_record(project: InterpStore, name: str, filename: str, record: dict[str, Any], meta: dict[str, Any]) -> None:
    append = getattr(project, "append_record", None)
    if append:
        append(name, filename, record, meta)
    else:
        _append_jsonl(capture_dir(project, name) / filename, record)
        project.register("capture", name, meta)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# ------------------------------------------------------------------ rows, models, hooks
def rows_for(project: InterpStore, dataset: str | None, snapshot: str | None) -> list[dict[str, Any]]:
    if not dataset and not snapshot:
        raise InterpError("pass a dataset or a snapshot")
    return project.rows_for(dataset, snapshot)


def _device() -> str:
    from tinystudio.serving.runner import pick_device

    return pick_device()


def load_model(project: InterpStore | None, model_id: str, device: str | None = None) -> dict[str, Any]:
    """Load a model for activation capture: fp32, adapters left unmerged so ``disable_adapter`` gives the base."""
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from tinystudio.serving.runner import ResolvedModel

    resolved = project.resolve_model(model_id) if project is not None else ResolvedModel(model_id, model_id, None, None, "base")
    device = device or _device()
    source = str(resolved.model_dir) if resolved.model_dir else resolved.base_model
    tokenizer = AutoTokenizer.from_pretrained(source)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    from tinystudio.serving.runner import pick_dtype

    model = AutoModelForCausalLM.from_pretrained(source, dtype=pick_dtype(device))
    peft = None
    if resolved.adapter_dir is not None:
        from peft import PeftModel

        peft = PeftModel.from_pretrained(model, str(resolved.adapter_dir))
        model = peft
    model.eval().to(device)
    return {"model": model, "tokenizer": tokenizer, "resolved": resolved, "peft": peft, "device": device}


def decoder_layers(model: Any) -> Any:
    """The decoder's ModuleList for a plain or PEFT-wrapped Llama/Qwen2 causal LM (PeftModel -> LoraModel -> CausalLM -> Model)."""
    import torch

    queue, seen = [model], set()
    while queue:
        module = queue.pop(0)
        if id(module) in seen:
            continue
        seen.add(id(module))
        layers = getattr(module, "layers", None)
        if isinstance(layers, torch.nn.ModuleList):
            return layers
        for attr in ("base_model", "model"):
            nxt = getattr(module, attr, None)
            if isinstance(nxt, torch.nn.Module) and nxt is not module:
                queue.append(nxt)
    raise InterpError("could not find the decoder layers on this model; only Llama/Qwen2-style causal LMs are supported")


MLP_SHAPES = (
    # kind, gate (or fused gate+up, or plain input projection), up (gated only), down/output projection
    ("gated", "gate_proj", "up_proj", "down_proj"),  # Llama, Mistral, Qwen2/3, Gemma, SmolLM, Phi-4
    ("fused", "gate_up_proj", None, "down_proj"),  # Phi-3 (gate and up share one projection, gate first)
    ("plain", "fc1", None, "fc2"),  # Phi-2, OPT, GPT-NeoX-style
    ("plain", "c_fc", None, "c_proj"),  # GPT-2
    ("plain", "dense_h_to_4h", None, "dense_4h_to_h"),  # Bloom, Falcon
)


def mlp_shape(mlp: Any) -> dict[str, Any]:
    """Which SwiGLU-or-not layout an MLP module uses, and the modules that give g, v, and the neuron activation."""
    for kind, gate, up, down in MLP_SHAPES:
        if hasattr(mlp, gate) and hasattr(mlp, down) and (up is None or hasattr(mlp, up)):
            return {"kind": kind, "gate": getattr(mlp, gate), "up": getattr(mlp, up) if up else None, "down": getattr(mlp, down), "names": (gate, up, down)}
    raise InterpError(f"unsupported MLP layout ({', '.join(n for n, _ in mlp.named_children())}); supported: gate_proj/up_proj/down_proj, gate_up_proj/down_proj, fc1/fc2, c_fc/c_proj, dense_h_to_4h/dense_4h_to_h")


def mlp_of(model: Any, layer: int) -> Any:
    layers = decoder_layers(model)
    if not (0 <= layer < len(layers)):
        raise InterpError(f"layer {layer} out of range; the model has {len(layers)} layers")
    mlp = getattr(layers[layer], "mlp", None) or getattr(layers[layer], "feed_forward", None) or getattr(layers[layer], "ff", None)
    if mlp is None:
        raise InterpError(f"layer {layer} has no mlp/feed_forward module")
    mlp_shape(mlp)
    return mlp


def neuron_width(mlp: Any) -> int:
    down = mlp_shape(mlp)["down"]
    return int(getattr(down, "in_features", None) or getattr(down, "nf", None) or down.weight.shape[1])


class Hooks:
    """Record g (gate_proj output), v (up_proj output), and p (down_proj input) for chosen layers.

    With ``on_layer``, the three tensors of a layer are handed to the callback as soon as ``down_proj`` runs and
    then dropped, so a pass over many layers never holds more than one layer's activations. Without it they stay
    in ``store`` until ``clear()``; ``keep`` limits which of g, v, p are retained.
    """

    def __init__(self, model: Any, layers: list[int], on_layer: Any = None, keep: tuple[str, ...] = ("g", "v", "p")) -> None:
        self.store: dict[int, dict[str, Any]] = {}
        self.handles: list[Any] = []
        self.on_layer = on_layer
        self.keep = set(keep)
        self.kinds: dict[int, str] = {}
        for i in layers:
            shape = mlp_shape(mlp_of(model, i))
            self.kinds[i] = shape["kind"]
            want_gate = "g" in self.keep or "v" in self.keep or on_layer is not None
            if shape["kind"] == "gated":
                if "g" in self.keep or on_layer is not None:
                    self.handles.append(shape["gate"].register_forward_hook(self._out(i, "g")))
                if "v" in self.keep or on_layer is not None:
                    self.handles.append(shape["up"].register_forward_hook(self._out(i, "v")))
            elif shape["kind"] == "fused" and want_gate:
                self.handles.append(shape["gate"].register_forward_hook(self._split(i)))
            elif want_gate:  # plain: the activation's sign is the only regime; v is treated as +1
                self.handles.append(shape["gate"].register_forward_hook(self._plain(i)))
            self.handles.append(shape["down"].register_forward_pre_hook(self._in(i)))

    def _out(self, i: int, key: str) -> Any:
        def hook(module: Any, args: Any, output: Any) -> None:
            self.store.setdefault(i, {})[key] = output.detach()

        return hook

    def _split(self, i: int) -> Any:
        def hook(module: Any, args: Any, output: Any) -> None:
            g, v = output.detach().chunk(2, dim=-1)
            self.store.setdefault(i, {}).update(g=g, v=v)

        return hook

    def _plain(self, i: int) -> Any:
        def hook(module: Any, args: Any, output: Any) -> None:
            g = output.detach()
            self.store.setdefault(i, {}).update(g=g, v=torch_ones_like(g))

        return hook

    def _in(self, i: int) -> Any:
        def hook(module: Any, args: Any) -> None:
            p = args[0].detach()
            if self.on_layer is not None:
                rec = self.store.pop(i, {})
                self.on_layer(i, rec.get("g"), rec.get("v"), p)
                return
            rec = self.store.setdefault(i, {})
            if "p" in self.keep:
                rec["p"] = p

        return hook

    def clear(self) -> None:
        self.store.clear()

    def remove(self) -> None:
        for handle in self.handles:
            handle.remove()
        self.handles.clear()

    def __enter__(self) -> "Hooks":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.remove()


def torch_ones_like(tensor: Any) -> Any:
    import torch

    return torch.ones_like(tensor)


def encode_rows(tokenizer: Any, rows: list[dict[str, Any]], max_length: int) -> dict[str, Any]:
    """Chat-template every conversation (assistant turn included) and tokenize with right padding."""
    texts = [tokenizer.apply_chat_template(row["messages"], tokenize=False) for row in rows]
    enc = tokenizer(texts, padding=True, truncation=True, max_length=max_length, return_tensors="pt")
    tokens = [[tokenizer.decode([int(t)]) for t, m in zip(ids, mask) if int(m)] for ids, mask in zip(enc["input_ids"], enc["attention_mask"])]
    return {"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"], "tokens": tokens, "prefix_len": shared_prefix_len(enc["input_ids"], enc["attention_mask"])}


def shared_prefix_len(input_ids: Any, attention_mask: Any) -> int:
    """Length of the token prefix every row shares (the chat template header and a common system prompt).

    Causal attention makes activations on a shared prefix identical across datapoints, so those positions
    would fill every top-k list with the same token repeated once per datapoint. They are excluded from the
    top-k and per-datapoint statistics; the count is recorded in the capture's meta.
    """
    if input_ids.shape[0] < 2:
        return 0
    lengths = attention_mask.sum(dim=1)
    shortest = int(lengths.min())
    first = input_ids[0, :shortest]
    same = (input_ids[:, :shortest] == first[None, :]).all(dim=0)
    prefix = 0
    for flag in same.tolist():
        if not flag:
            break
        prefix += 1
    return prefix if prefix < shortest - 1 else 0


def content_mask(attention_mask: Any, prefix_len: int) -> Any:
    """Attention mask with the shared prefix removed."""
    mask = attention_mask.bool()
    if prefix_len > 0:
        mask = mask.clone()
        mask[:, :prefix_len] = False
    return mask


def batch_size_for(requested: int, padded_len: int, token_budget: int = 8192) -> int:
    """Rows per forward pass: the requested size, or (when 0) as many rows as fit a token budget on MPS."""
    if requested > 0:
        return requested
    return max(1, min(64, token_budget // max(1, padded_len)))


def _common_system_prompt(rows: list[dict[str, Any]]) -> str | None:
    """The system prompt shared by every row, or None (tests wrap their exchanges in it so the template matches the corpus)."""
    prompts = {row["messages"][0]["content"] if row["messages"] and row["messages"][0]["role"] == "system" else None for row in rows}
    return next(iter(prompts)) if len(prompts) == 1 else None


# ------------------------------------------------------------------ capture
def _topk_merge(cur: dict[str, Any], vals: Any, dp: Any, pos: Any, g: Any, v: Any, k: int, largest: bool) -> dict[str, Any]:
    """Keep the k largest (or smallest) activations per neuron across batches, with their metadata."""
    import torch

    cat = lambda key, new: torch.cat([cur[key], new], dim=0)  # noqa: E731
    merged_vals = cat("val", vals)
    top = merged_vals.topk(min(k, merged_vals.shape[0]), dim=0, largest=largest).indices
    out = {"val": merged_vals.gather(0, top)}
    for key, new in (("dp", dp), ("pos", pos), ("g", g), ("v", v)):
        out[key] = cat(key, new).gather(0, top)
    return out


def _empty_top(k: int, width: int, device: str, largest: bool) -> dict[str, Any]:
    import torch

    fill = -1e9 if largest else 1e9
    return {
        "val": torch.full((k, width), fill, device=device),
        "dp": torch.full((k, width), -1, device=device, dtype=torch.long),
        "pos": torch.full((k, width), -1, device=device, dtype=torch.long),
        "g": torch.zeros((k, width), device=device),
        "v": torch.zeros((k, width), device=device),
    }


def capture(project: InterpStore, spec: CaptureSpec, progress: Any = print) -> dict[str, Any]:
    """Run the dataset through the model and record per-neuron activation summaries for the chosen layers."""
    import numpy as np
    import torch

    rows = rows_for(project, spec.dataset, spec.snapshot)
    if not rows:
        raise InterpError("the dataset is empty")
    name = spec.name or default_capture_name(spec.model_id, spec.source())
    out_dir = capture_dir(project, name)
    if (out_dir / "meta.json").exists():
        raise InterpError(f"capture {name!r} already exists; delete it or pass --name")
    loaded = load_model(project, spec.model_id)
    model, tokenizer, device = loaded["model"], loaded["tokenizer"], loaded["device"]
    n_layers = len(decoder_layers(model))
    layers = spec.layers or list(range(n_layers))
    for i in layers:
        mlp_of(model, i)
    started = time.perf_counter()
    enc = encode_rows(tokenizer, rows, spec.max_length)
    n_rows = len(rows)
    width = neuron_width(mlp_of(model, layers[0]))
    mlp_kind = mlp_shape(mlp_of(model, layers[0]))["kind"]
    prefix_len = enc["prefix_len"]
    progress(f"capture {name}: model={spec.model_id} rows={n_rows} tokens={int(enc['attention_mask'].sum())} shared prefix={prefix_len} tokens (excluded) layers={len(layers)} neurons/layer={width} mlp={mlp_kind} device={device}")

    k = spec.topk
    batch_size = batch_size_for(spec.batch_size, int(enc["input_ids"].shape[1]))
    state = {i: {"pos": _empty_top(k, width, device, True), "neg": _empty_top(k, width, device, False), "open": _empty_top(k, width, device, True)} for i in layers}
    dp_max = {i: np.zeros((n_rows, width), dtype=np.float16) for i in layers}
    dp_min = {i: np.zeros((n_rows, width), dtype=np.float16) for i in layers}
    batch: dict[str, Any] = {}

    def record(i: int, g: Any, v: Any, p_full: Any) -> None:  # runs inside the forward pass, one layer at a time
        mask, dp_ids, pos_ids, start = batch["mask"], batch["dp"], batch["pos"], batch["start"]
        p, gm, vm = p_full[mask], g[mask], v[mask]  # [tokens, width]
        dpx = dp_ids[:, None].expand_as(p)
        psx = pos_ids[:, None].expand_as(p)
        st = state[i]
        st["pos"] = _topk_merge(st["pos"], p, dpx, psx, gm, vm, k, True)
        st["neg"] = _topk_merge(st["neg"], p, dpx, psx, gm, vm, k, False)
        st["open"] = _topk_merge(st["open"], p.masked_fill(gm <= 0, -1e9), dpx, psx, gm, vm, k, True)
        b = mask.shape[0]
        dp_max[i][start : start + b] = p_full.masked_fill(~mask[..., None], -1e9).max(dim=1).values.float().cpu().numpy().astype(np.float16)
        dp_min[i][start : start + b] = p_full.masked_fill(~mask[..., None], 1e9).min(dim=1).values.float().cpu().numpy().astype(np.float16)

    with Hooks(model, layers, on_layer=record) as hooks, torch.inference_mode():
        for start in range(0, n_rows, batch_size):
            ids = enc["input_ids"][start : start + batch_size].to(device)
            am = enc["attention_mask"][start : start + batch_size].to(device)
            mask = content_mask(am, prefix_len)
            b, t = mask.shape
            batch.update(mask=mask, start=start, dp=(torch.arange(b, device=device)[:, None] + start).expand(b, t)[mask], pos=torch.arange(t, device=device)[None, :].expand(b, t)[mask])
            model(input_ids=ids, attention_mask=am)
            progress(f"  {min(start + batch_size, n_rows)}/{n_rows} datapoints")

    # second pass: token density (share of content tokens above 10% of each neuron's maximum) and first-token peaks
    maxes = {i: state[i]["pos"]["val"][0].clamp_min(1e-6) for i in layers}
    above = {i: torch.zeros(width, device=device) for i in layers}
    first_peak = {i: torch.zeros(width, device=device) for i in layers}
    n_tokens = 0

    def density(i: int, g: Any, v: Any, p: Any) -> None:
        mask = batch["mask"]
        above[i] += (p[mask] > 0.1 * maxes[i]).sum(dim=0)
        if prefix_len < p.shape[1]:
            first = p[:, prefix_len, :].masked_fill(~mask[:, prefix_len][:, None], -1e9)
            first_peak[i] += (first.max(dim=0).values >= maxes[i] - 1e-6).float()

    with Hooks(model, layers, on_layer=density) as hooks, torch.inference_mode():
        for start in range(0, n_rows, batch_size):
            ids = enc["input_ids"][start : start + batch_size].to(device)
            am = enc["attention_mask"][start : start + batch_size].to(device)
            batch["mask"] = content_mask(am, prefix_len)
            n_tokens += int(batch["mask"].sum())
            model(input_ids=ids, attention_mask=am)

    out_dir.mkdir(parents=True, exist_ok=True)
    labels = [list(row.get("labels", [])) for row in rows]
    prefix_ids = enc["input_ids"][0, :prefix_len].tolist() if prefix_len else []
    write_json(out_dir / "tokens.json", {"tokens": enc["tokens"], "labels": labels, "buckets": [row.get("bucket", "uncategorized") for row in rows], "users": [_last_user(row) for row in rows], "system": _common_system_prompt(rows), "prefix_ids": prefix_ids})
    layer_stats = {}
    for i in layers:
        st = state[i]
        arrays: dict[str, Any] = {}
        for pole in ("pos", "neg", "open"):
            for key in ("val", "dp", "pos", "g", "v"):
                arrays[f"{pole}_{key}"] = st[pole][key].cpu().numpy()
        arrays["dp_max"], arrays["dp_min"] = dp_max[i], dp_min[i]
        mx, mn, open_max = arrays["pos_val"][0], arrays["neg_val"][0], arrays["open_val"][0]
        regime = (arrays["pos_g"] > 0).astype(np.int8) * 2 + (arrays["pos_v"] > 0).astype(np.int8)
        echo_share = (regime == 0).mean(axis=0)
        density = (above[i].cpu().numpy() / max(1, n_tokens)).astype(np.float32)
        dp_density = (dp_max[i].astype(np.float32) > 0.1 * np.maximum(mx, 1e-6)[None, :]).mean(axis=0)
        arrays["stats_max"], arrays["stats_min"], arrays["stats_open_max"] = mx, mn, open_max
        arrays["stats_echo_share"], arrays["stats_density"], arrays["stats_dp_density"] = echo_share, density, dp_density
        arrays["stats_echo_top"] = (open_max < 0.5 * np.maximum(mx, 1e-6)).astype(np.int8)
        arrays["stats_first_peak"] = first_peak[i].cpu().numpy().astype(np.int8)
        np.savez_compressed(out_dir / f"layer-{i}.npz", **arrays)
        layer_stats[str(i)] = {
            "neurons": int(width),
            "max_median": float(np.median(mx)),
            "max_p99": float(np.quantile(mx, 0.99)),
            "echo_share_mean": float(echo_share.mean()),
            "echo_top_share": float(arrays["stats_echo_top"].mean()),
            "never_open": int((open_max < -1e8).sum()),
            "first_token_peaks": int(arrays["stats_first_peak"].sum()),
            "near_dead": int(((mx < 1e-3) & (mn > -1e-3)).sum()),
        }
    meta = {
        "name": name,
        "model_id": spec.model_id,
        "kind": loaded["resolved"].kind,
        "base_model": loaded["resolved"].base_model,
        "dataset": spec.dataset,
        "snapshot": spec.snapshot,
        "source": spec.source(),
        "rows": n_rows,
        "tokens": int(enc["attention_mask"].sum()),
        "layers": layers,
        "num_layers": n_layers,
        "neurons_per_layer": int(width),
        "mlp_kind": mlp_kind,
        "topk": k,
        "prefix_len": prefix_len,
        "content_tokens": n_tokens,
        "batch_size": batch_size,
        "device": device,
        "seconds": round(time.perf_counter() - started, 1),
        "created": now(),
        "layer_stats": layer_stats,
    }
    write_json(out_dir / "meta.json", meta)
    project.register("capture", name, meta)
    progress(f"capture {name}: done in {meta['seconds']}s -> {out_dir}")
    _release(loaded)
    return meta


def _last_user(row: dict[str, Any]) -> str:
    return next((m["content"] for m in reversed(row["messages"]) if m["role"] == "user"), "")


def _release(loaded: dict[str, Any]) -> None:
    import torch

    loaded.clear()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


# ------------------------------------------------------------------ reading captures
def _layer_arrays(project: InterpStore, name: str, layer: int) -> Any:
    import numpy as np

    path = capture_dir(project, name) / f"layer-{layer}.npz"
    if not path.exists():
        raise InterpError(f"capture {name!r} has no layer {layer}; captured layers: {capture_meta(project, name)['layers']}")
    return np.load(path)


def _tokens(project: InterpStore, name: str) -> dict[str, Any]:
    return read_json(capture_dir(project, name) / "tokens.json")


def _open_max(value: float) -> float | None:
    """The gate-open maximum, or None when the gate never opened on a content token."""
    return None if value < -1e8 else float(value)


def label_shares(entries: list[dict[str, Any]]) -> list[list[Any]]:
    """Share of the distinct datapoints among `entries` that carry each label (a datapoint with two labels counts once per label)."""
    by_dp: dict[int, list[str]] = {}
    for e in entries:
        by_dp.setdefault(e["datapoint"], list(e.get("labels") or ["(unlabelled)"]))
    total = max(1, len(by_dp))
    counts: Counter[str] = Counter(label for labels in by_dp.values() for label in set(labels))
    return [[label, round(c / total, 3)] for label, c in counts.most_common(12)]


def latest_records(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for rec in _read_jsonl(path):
        out[(int(rec["layer"]), int(rec["neuron"]))] = rec
    return out


def neuron_table(project: InterpStore, name: str, layer: int, sort: str = "max", limit: int = 100, offset: int = 0, query: str = "") -> dict[str, Any]:
    """Rows for the neuron table of one layer, sortable by max, min, open_max, echo_share, density."""
    import numpy as np

    arrays = _layer_arrays(project, name, layer)
    width = int(arrays["stats_max"].shape[0])
    explanations = latest_records(capture_dir(project, name) / "explanations.jsonl")
    tests = latest_records(capture_dir(project, name) / "tests.jsonl")
    keys = {"max": arrays["stats_max"], "min": -arrays["stats_min"], "open_max": arrays["stats_open_max"], "echo_share": arrays["stats_echo_share"], "density": arrays["stats_density"]}
    if query and not explanations:
        return {"capture": name, "layer": layer, "neurons": width, "sort": sort, "total": 0, "rows": []}
    if sort not in keys:
        raise InterpError(f"sort must be one of {sorted(keys)}")
    order = np.argsort(-keys[sort], kind="stable")
    rows = []
    for n in order.tolist():
        exp = explanations.get((layer, n))
        if query and not (exp and query.lower() in exp["explanation"].lower()):
            continue
        tst = tests.get((layer, n))
        rows.append({
            "neuron": n,
            "max": float(arrays["stats_max"][n]),
            "min": float(arrays["stats_min"][n]),
            "open_max": _open_max(float(arrays["stats_open_max"][n])),
            "echo_share": float(arrays["stats_echo_share"][n]),
            "echo_top": bool(arrays["stats_echo_top"][n]),
            "density": float(arrays["stats_density"][n]),
            "first_token_peak": bool(arrays["stats_first_peak"][n]) if "stats_first_peak" in arrays else False,
            "explanation": exp["explanation"] if exp else None,
            "verdict": tst.get("verdict") if tst else None,
            "blind_best": tst.get("blind_best") if tst else None,
        })
    return {"capture": name, "layer": layer, "neurons": width, "sort": sort, "total": len(rows), "rows": rows[offset : offset + limit]}


def _entry(arrays: Any, pole: str, j: int, n: int, tokens: dict[str, Any], context: int, prefix_len: int = 0) -> dict[str, Any] | None:
    dp, pos = int(arrays[f"{pole}_dp"][j, n]), int(arrays[f"{pole}_pos"][j, n])
    if dp < 0:
        return None
    g, v = float(arrays[f"{pole}_g"][j, n]), float(arrays[f"{pole}_v"][j, n])
    toks = tokens["tokens"][dp]
    return {
        "datapoint": dp,
        "position": pos,
        "value": float(arrays[f"{pole}_val"][j, n]),
        "g": g,
        "v": v,
        "regime": REGIMES[int(g > 0) * 2 + int(v > 0)],
        "first_token": pos == prefix_len,
        "before": "".join(toks[max(0, pos - context) : pos]),
        "token": toks[pos] if pos < len(toks) else "",
        "after": "".join(toks[pos + 1 : pos + 1 + context]),
        "labels": tokens["labels"][dp],
        "bucket": tokens["buckets"][dp],
        "user": tokens["users"][dp],
    }


def neuron_view(project: InterpStore, name: str, layer: int, neuron: int, context: int = CONTEXT_TOKENS, limit: int = 20) -> dict[str, Any]:
    """Everything the neuron panel shows: poles, regimes, label shares, explanation, tests, drift hints."""
    arrays = _layer_arrays(project, name, layer)
    width = int(arrays["stats_max"].shape[0])
    if not (0 <= neuron < width):
        raise InterpError(f"neuron {neuron} out of range (0..{width - 1})")
    tokens = _tokens(project, name)
    meta = capture_meta(project, name)
    prefix_len = int(meta.get("prefix_len", 0))
    k = int(arrays["pos_val"].shape[0])
    full: dict[str, list[dict[str, Any]]] = {}
    for pole in ("pos", "neg", "open"):
        full[pole] = [e for e in (_entry(arrays, pole, j, neuron, tokens, context, prefix_len) for j in range(k)) if e is not None and (pole != "open" or e["value"] > -1e8)]
    regimes = Counter(e["regime"] for e in full["pos"])
    neg_regimes = Counter(e["regime"] for e in full["neg"])
    explanations = latest_records(capture_dir(project, name) / "explanations.jsonl")
    tests = latest_records(capture_dir(project, name) / "tests.jsonl")
    return {
        "capture": name,
        "layer": layer,
        "neuron": neuron,
        "topk": k,
        "stats": {
            "max": float(arrays["stats_max"][neuron]),
            "min": float(arrays["stats_min"][neuron]),
            "open_max": _open_max(float(arrays["stats_open_max"][neuron])),
            "echo_share": float(arrays["stats_echo_share"][neuron]),
            "echo_top": bool(arrays["stats_echo_top"][neuron]),
            "density": float(arrays["stats_density"][neuron]),
            "first_token_peak": bool(arrays["stats_first_peak"][neuron]) if "stats_first_peak" in arrays else False,
        },
        "positives": full["pos"][:limit],
        "negatives": full["neg"][:limit],
        "gate_open": full["open"][:limit],
        "datapoints": {"positives": sorted({e["datapoint"] for e in full["pos"]}), "gate_open": sorted({e["datapoint"] for e in full["open"]})},
        "regimes": {REGIMES[r]: regimes.get(REGIMES[r], 0) for r in (3, 2, 1, 0)},
        "negative_regimes": {REGIMES[r]: neg_regimes.get(REGIMES[r], 0) for r in (3, 2, 1, 0)},
        "label_share": label_shares(full["open"] or full["pos"]),
        "label_share_all": label_shares(full["pos"]),
        "explanation": explanations.get((layer, neuron)),
        "test": tests.get((layer, neuron)),
    }


# ------------------------------------------------------------------ explanations and tests (helper LLM)
def _show(e: dict[str, Any]) -> str:
    return f"{e['before']}[{e['token']}]{e['after']}".replace("\n", "⏎")


def explain_prompt(view: dict[str, Any], positives: int = 12, negatives: int = 6) -> str:
    pos = (view["gate_open"] or view["positives"])[:positives]
    neg = view["negatives"][:negatives]
    lines = [
        "You are labelling one MLP neuron of a small chat model fine-tuned on a narrow domain.",
        *(["NOTE: this neuron's gate never opened on the corpus; its positive activations are the double-negative echo of its negative pole (closed gate times negative value), so describe the negative pole as its real job."] if not view["gate_open"] else []),
        "Below are the contexts where it fires most strongly. The token in [brackets] is where the activation occurs;",
        "activations are often placed on the token just before what matters, or on punctuation that summarises the sentence.",
        "Describe the shared pattern as a rule about what the neuron detects or separates (a verb-like job),",
        "not as a list of nouns. If two unrelated things seem mixed, say so instead of forcing one story.",
        "",
        "STRONGEST POSITIVE CONTEXTS (value, then context):",
    ]
    lines += [f"{i + 1}. ({e['value']:.2f}) {_show(e)}" for i, e in enumerate(pos)]
    lines += ["", "STRONGEST NEGATIVE CONTEXTS (the other pole):"]
    lines += [f"{i + 1}. ({e['value']:.2f}) {_show(e)}" for i, e in enumerate(neg)]
    lines += [
        "",
        'Return JSON only: {"explanation": "<one line, under 25 words>", "negative_pole": "<one line>", "confidence": <0-1>}',
    ]
    return "\n".join(lines)


def explain(project: InterpStore, name: str, layer: int, neuron: int, client: LLMClient, progress: Any = print) -> dict[str, Any]:
    meta = capture_meta(project, name)
    view = neuron_view(project, name, layer, neuron)
    prompt = explain_prompt(view)
    reply = client.chat_json([{"role": "user", "content": prompt}], temperature=0.1)
    if not isinstance(reply, dict) or not str(reply.get("explanation", "")).strip():
        raise LLMError(f"the helper LLM returned no explanation for layer {layer} neuron {neuron}: {str(reply)[:200]}")
    record = {
        "layer": layer,
        "neuron": neuron,
        "explanation": str(reply["explanation"]).strip(),
        "negative_pole": str(reply.get("negative_pole", "")).strip(),
        "confidence": float(reply.get("confidence", 0) or 0),
        "gate_never_open": not view["gate_open"],
        "helper": client.model or "(server default)",
        "time": now(),
        "prompt": prompt,
        "raw_reply": reply,
    }
    _append_capture_record(project, name, "explanations.jsonl", record, meta)
    progress(f"L{layer} N{neuron}: {record['explanation']}")
    return record


def explain_top(project: InterpStore, name: str, layer: int, client: LLMClient, top: int = 20, sort: str = "max", progress: Any = print) -> list[dict[str, Any]]:
    """Explain the first `top` neurons by `sort` that have no explanation yet (re-running moves on to the next ones)."""
    table = neuron_table(project, name, layer, sort=sort, limit=100000)
    pending = [row["neuron"] for row in table["rows"] if row["explanation"] is None][:top]
    return [explain(project, name, layer, n, client, progress) for n in pending]


EXCHANGE_NOTE = (
    "The neuron was measured on whole chat exchanges (system prompt, user message, assistant reply), so write each example as "
    'an exchange {"user": "...", "assistant": "..."}; the assistant reply must be the kind of answer this model gives. '
    "Leave \"assistant\" empty only if the pattern lives entirely in the user message."
)


def blind_prompt(explanation: str, system: str | None, n: int) -> str:
    setting = f"The model's system prompt is: {system!r}. " if system else ""
    return (
        f"{setting}An MLP neuron in that model has this explanation: {explanation!r}.\n"
        f"Without seeing any examples, write {n} short exchanges that should activate this neuron as strongly as possible, "
        "each aiming at the centre of what the explanation describes. "
        f"{EXCHANGE_NOTE}\n"
        'Return JSON only: {"sentences": [{"user": "...", "assistant": "..."}, ...]}'
    )


def near_miss_prompt(explanation: str, system: str | None, n: int) -> str:
    setting = f"The model's system prompt is: {system!r}. " if system else ""
    return (
        f"{setting}An MLP neuron in that model has this explanation: {explanation!r}.\n"
        f"Write {n} new exchanges that SHOULD fire it, {n} near misses that are similar in wording or topic but should NOT fire it, "
        f"and {n} exchanges about the most distant, unrelated concept you can think of. {EXCHANGE_NOTE}\n"
        'Return JSON only: {"positives": [{"user": "...", "assistant": "..."}, ...], "near_misses": [...], "distant": [...]}'
    )


def _exchanges(value: Any, n: int) -> list[dict[str, str]]:
    """Normalise helper output: strings become user-only exchanges; dicts keep user and assistant."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, dict):
            user, assistant = str(item.get("user", "")).strip(), str(item.get("assistant", "")).strip()
        else:
            user, assistant = str(item).strip(), ""
        if user:
            out.append({"user": user, "assistant": assistant})
    return out[:n]


def exchange_text(x: dict[str, str]) -> str:
    return f"{x['user']} ⟶ {x['assistant']}" if x.get("assistant") else x["user"]


def score_texts(loaded: dict[str, Any], layer: int, neurons: list[int], texts: list[str | dict[str, str]], system: str | None, max_length: int = 1024, prefix_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Per text or {user, assistant} exchange, in the same chat template: each neuron's max activation over the tokens that
    do not belong to the capture's shared prefix (matched token by token), plus the gate sign there and the gate-open max."""
    import torch

    model, tokenizer, device = loaded["model"], loaded["tokenizer"], loaded["device"]
    exchanges = [t if isinstance(t, dict) else {"user": t, "assistant": ""} for t in texts]
    rows = []
    for x in exchanges:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": x["user"]}]
        if x.get("assistant"):
            messages.append({"role": "assistant", "content": x["assistant"]})
        rows.append({"messages": messages})
    enc = encode_rows(tokenizer, rows, max_length)
    prefix = torch.tensor(prefix_ids or [], dtype=enc["input_ids"].dtype)
    results = []
    with Hooks(model, [layer], keep=("g", "p")) as hooks, torch.inference_mode():
        for start in range(0, len(rows), 8):
            ids = enc["input_ids"][start : start + 8]
            am = enc["attention_mask"][start : start + 8]
            mask = am.bool().clone()
            for b in range(ids.shape[0]):  # drop only the leading tokens that really equal the capture's prefix
                n = min(len(prefix), int(am[b].sum()) - 1)
                while n > 0 and not torch.equal(ids[b, :n], prefix[:n]):
                    n -= 1
                mask[b, :n] = False
            hooks.clear()
            model(input_ids=ids.to(device), attention_mask=am.to(device))
            p = hooks.store[layer]["p"]  # [b, t, width]
            g = hooks.store[layer]["g"]
            dmask = mask.to(device)
            masked = p.masked_fill(~dmask[..., None], -1e9)
            open_masked = masked.masked_fill(g <= 0, -1e9)
            for b in range(ids.shape[0]):
                toks = enc["tokens"][start + b]
                per = {}
                for n in neurons:
                    val, pos = masked[b, :, n].max(dim=0)
                    open_val = open_masked[b, :, n].max(dim=0).values
                    per[n] = {"max": float(val), "position": int(pos), "token": toks[int(pos)] if int(pos) < len(toks) else "", "gate_open": bool(g[b, int(pos), n] > 0), "open_max": _open_max(float(open_val))}
                results.append({"text": exchange_text(exchanges[start + b]), "exchange": exchanges[start + b], "neurons": per})
    return results


def test(project: InterpStore, name: str, layer: int, neuron: int, client: LLMClient, loaded: dict[str, Any] | None = None, n_blind: int = 3, n_each: int = 5, progress: Any = print) -> dict[str, Any]:
    """Blind-maximum and near-miss tests of the latest explanation of one neuron."""
    explanations = latest_records(capture_dir(project, name) / "explanations.jsonl")
    exp = explanations.get((layer, neuron))
    if exp is None:
        exp = explain(project, name, layer, neuron, client, progress)
    meta = capture_meta(project, name)
    tokens = _tokens(project, name)
    system = tokens.get("system")
    arrays = _layer_arrays(project, name, layer)
    corpus_max = float(arrays["stats_max"][neuron])
    open_max = _open_max(float(arrays["stats_open_max"][neuron]))
    # the reference is the gate-open corpus maximum when the gate ever opened, so echo-only hits cannot count as firing
    reference = max(open_max if open_max is not None else corpus_max, 1e-6)
    prompts = {"blind": blind_prompt(exp["explanation"], system, n_blind), "near_miss": near_miss_prompt(exp["explanation"], system, n_each)}
    blind_reply = client.chat_json([{"role": "user", "content": prompts["blind"]}], temperature=0.7)
    blind = _exchanges((blind_reply if isinstance(blind_reply, dict) else {}).get("sentences"), n_blind)
    sets = client.chat_json([{"role": "user", "content": prompts["near_miss"]}], temperature=0.7)
    sets = sets if isinstance(sets, dict) else {}
    positives, near, distant = _exchanges(sets.get("positives"), n_each), _exchanges(sets.get("near_misses"), n_each), _exchanges(sets.get("distant"), n_each)
    if not blind and not positives:
        raise LLMError(f"the helper LLM returned no test sentences for layer {layer} neuron {neuron}")
    own = loaded is None
    loaded = loaded or load_model(project, meta["model_id"])
    try:
        scored = score_texts(loaded, layer, [neuron], blind + positives + near + distant, system, 1024, list(tokens.get("prefix_ids") or []))
    finally:
        if own:
            _release(loaded)

    def pack(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for s in items:
            r = s["neurons"][neuron]
            measured = r["open_max"] if open_max is not None else r["max"]
            measured = measured if measured is not None else 0.0
            out.append({"text": s["text"], "max": r["max"], "gate_open": r["gate_open"], "open_max": r["open_max"], "ratio": round(measured / reference, 3), "ratio_all": round(r["max"] / max(corpus_max, 1e-6), 3), "token": r["token"], "strong": measured >= STRONG_RATIO * reference})
        return out

    i0, i1, i2 = len(blind), len(blind) + len(positives), len(blind) + len(positives) + len(near)
    blind_r, pos_r, near_r, far_r = pack(scored[:i0]), pack(scored[i0:i1]), pack(scored[i1:i2]), pack(scored[i2:])
    pos_strong, near_strong, far_strong = sum(x["strong"] for x in pos_r), sum(x["strong"] for x in near_r), sum(x["strong"] for x in far_r)
    complete = len(pos_r) == len(near_r) == n_each
    verdict = "incomplete" if not complete else "win" if pos_strong > near_strong else "tie" if pos_strong == near_strong else "loss"
    blind_best = max((x["ratio"] for x in blind_r), default=None)
    record = {
        "layer": layer,
        "neuron": neuron,
        "explanation": exp["explanation"],
        "corpus_max": corpus_max,
        "open_max": open_max,
        "reference": "gate-open max" if open_max is not None else "overall max (gate never open)",
        "strong_ratio": STRONG_RATIO,
        "blind": blind_r,
        "blind_best": blind_best,
        "beats_corpus": bool(blind_best is not None and blind_best >= 1.0),
        "positives": pos_r,
        "near_misses": near_r,
        "distant": far_r,
        "counts": {"positives_strong": pos_strong, "near_misses_strong": near_strong, "distant_strong": far_strong, "n": len(pos_r), "n_near": len(near_r), "n_distant": len(far_r)},
        "verdict": verdict,
        "helper": client.model or "(server default)",
        "time": now(),
        "prompts": prompts,
        "raw_replies": {"blind": blind_reply, "near_miss": sets},
    }
    _append_capture_record(project, name, "tests.jsonl", record, meta)
    progress(f"L{layer} N{neuron}: blind best {blind_best} x corpus max; positives strong {pos_strong}/{len(pos_r)}, near misses {near_strong}/{len(near_r)}, distant {far_strong}/{len(far_r)} -> {verdict}")
    return record


def test_many(project: InterpStore, name: str, layer: int, neurons: list[int], client: LLMClient, progress: Any = print) -> list[dict[str, Any]]:
    meta = capture_meta(project, name)
    loaded = load_model(project, meta["model_id"])
    try:
        return [test(project, name, layer, n, client, loaded, progress=progress) for n in neurons]
    finally:
        _release(loaded)


# ------------------------------------------------------------------ before / after
def list_diffs(project: InterpStore) -> list[dict[str, Any]]:
    listing = getattr(project, "list_artifacts", None)
    if listing:
        return listing("diff")
    base = diffs_dir(project)
    if not base.exists():
        return []
    return [read_json(child / "meta.json") for child in sorted(base.iterdir()) if child.is_dir() and (child / "meta.json").exists()]


def diff_meta(project: InterpStore, name: str) -> dict[str, Any]:
    path = diff_dir(project, name) / "meta.json"
    if not path.exists():
        raise InterpError(f"comparison {name!r} does not exist")
    return read_json(path)


def delete_diff(project: InterpStore, name: str) -> None:
    import shutil

    delete = getattr(project, "delete_artifact", None)
    if delete:
        delete("diff", name)
        return
    directory = diff_dir(project, name)
    if not (directory / "meta.json").exists():
        raise InterpError(f"comparison {name!r} does not exist")
    shutil.rmtree(directory)
    project.unregister("diff", name)


def diff(project: InterpStore, spec: DiffSpec, progress: Any = print) -> dict[str, Any]:
    """Compare per-neuron activations of an experiment against its base on the same tokens."""
    import numpy as np
    import torch

    rows = rows_for(project, spec.dataset, spec.snapshot)
    source = spec.dataset or spec.snapshot or ""
    name = spec.name or f"{safe_part(spec.experiment)}--vs-base--{safe_part(source)}"
    out_dir = diff_dir(project, name)
    if (out_dir / "meta.json").exists():
        raise InterpError(f"comparison {name!r} already exists; delete it or pass --name")
    exp = load_model(project, spec.experiment)
    base_id = spec.base or exp["resolved"].base_model
    base = None if (exp["peft"] is not None and spec.base is None) else load_model(project, base_id, exp["device"])
    model, tokenizer, device = exp["model"], exp["tokenizer"], exp["device"]
    n_layers = len(decoder_layers(model))
    layers = spec.layers or list(range(n_layers))
    started = time.perf_counter()
    enc = encode_rows(tokenizer, rows, spec.max_length)
    width = neuron_width(mlp_of(model, layers[0]))
    n_rows = len(rows)
    prefix_len = enc["prefix_len"]
    progress(f"compare {name}: {spec.experiment} vs {base_id} on {n_rows} rows, {len(layers)} layers ({'adapter toggle' if base is None else 'two models'}); shared prefix of {prefix_len} tokens excluded")
    batch_size = batch_size_for(spec.batch_size, int(enc["input_ids"].shape[1]))
    acc = {i: {key: np.zeros(width, dtype=np.float64) for key in ("n", "sx", "sy", "sxx", "syy", "sxy", "sdd")} for i in layers}
    top = {i: _empty_top(spec.top_delta, width, device, True) for i in layers}
    dp_delta = {i: np.zeros((n_rows, width), dtype=np.float32) for i in layers}
    batch: dict[str, Any] = {}
    before: dict[int, Any] = {}  # the base pass keeps only p, parked on the CPU, one tensor per layer

    def park(i: int, g: Any, v: Any, p: Any) -> None:
        before[i] = p.cpu()

    def compare(i: int, g: Any, v: Any, after_full: Any) -> None:  # the experiment pass reduces one layer at a time
        mask, dp_ids, pos_ids, start = batch["mask"], batch["dp"], batch["pos"], batch["start"]
        before_full = before.pop(i).to(device)
        x, y = before_full[mask], after_full[mask]
        d = y - x
        a = acc[i]
        a["n"] += x.shape[0]
        a["sx"] += x.sum(0).cpu().double().numpy()
        a["sy"] += y.sum(0).cpu().double().numpy()
        a["sxx"] += (x * x).sum(0).cpu().double().numpy()
        a["syy"] += (y * y).sum(0).cpu().double().numpy()
        a["sxy"] += (x * y).sum(0).cpu().double().numpy()
        a["sdd"] += (d * d).sum(0).cpu().double().numpy()
        top[i] = _topk_merge(top[i], d.abs(), dp_ids[:, None].expand_as(d), pos_ids[:, None].expand_as(d), x, y, spec.top_delta, True)
        per_dp = (after_full - before_full).abs().masked_fill(~mask[..., None], 0).sum(dim=1)
        dp_delta[i][start : start + mask.shape[0]] = per_dp.float().cpu().numpy()

    import contextlib

    # with a LoRA experiment both passes run through the same module, so one set of hooks switches between park and compare;
    # with two models each gets its own hooks
    base_hooks = Hooks(base["model"], layers, on_layer=park) if base is not None else contextlib.nullcontext()
    with base_hooks, Hooks(model, layers, on_layer=compare) as exp_hooks, torch.inference_mode():
        for start in range(0, n_rows, batch_size):
            ids = enc["input_ids"][start : start + batch_size].to(device)
            am = enc["attention_mask"][start : start + batch_size].to(device)
            mask = content_mask(am, prefix_len)
            b, t = mask.shape
            batch.update(mask=mask, start=start, dp=(torch.arange(b, device=device)[:, None] + start).expand(b, t)[mask], pos=torch.arange(t, device=device)[None, :].expand(b, t)[mask])
            if base is None:
                exp_hooks.on_layer = park
                with exp["peft"].disable_adapter():
                    model(input_ids=ids, attention_mask=am)
                exp_hooks.on_layer = compare
            else:
                base["model"](input_ids=ids, attention_mask=am)
            model(input_ids=ids, attention_mask=am)
            if before:
                raise InterpError("internal error: base activations were not consumed layer by layer")
            progress(f"  {min(start + batch_size, n_rows)}/{n_rows} datapoints")

    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "tokens.json", {"tokens": enc["tokens"], "labels": [list(r.get("labels", [])) for r in rows], "buckets": [r.get("bucket", "uncategorized") for r in rows], "users": [_last_user(r) for r in rows]})
    layer_summary = {}
    for i in layers:
        a = acc[i]
        n = a["n"]
        cov = n * a["sxy"] - a["sx"] * a["sy"]
        varx = n * a["sxx"] - a["sx"] ** 2
        vary = n * a["syy"] - a["sy"] ** 2
        with np.errstate(invalid="ignore", divide="ignore"):
            defined = (varx > 1e-12) & (vary > 1e-12)
            r = np.where(defined, cov / np.sqrt(np.maximum(varx * vary, 1e-30)), np.nan)
            rel = np.sqrt(a["sdd"]) / np.sqrt(np.maximum(a["sxx"], 1e-30))
        # one number to rank by: decorrelation plus scale change; a neuron that went constant counts as fully changed
        change = np.where(np.isnan(r), 1.0, 1.0 - np.clip(r, -1, 1)) + np.minimum(rel, 1.0)
        arrays = {"r": r.astype(np.float32), "rel_change": rel.astype(np.float32), "change": change.astype(np.float32), "dp_delta": dp_delta[i]}
        for key in ("val", "dp", "pos", "g", "v"):  # here g=base activation, v=experiment activation at the top delta
            arrays[f"top_{key}"] = top[i][key].cpu().numpy()
        np.savez_compressed(out_dir / f"layer-{i}.npz", **arrays)
        finite = r[~np.isnan(r)]
        hist, edges = np.histogram(np.clip(finite, -1, 1), bins=10, range=(-1, 1))
        layer_summary[str(i)] = {"median_r": float(np.median(finite)) if finite.size else None, "below_0_9": int((finite < 0.9).sum()), "below_0_5": int((finite < 0.5).sum()), "constant": int(np.isnan(r).sum()), "hist": {"bins": edges.tolist(), "counts": hist.tolist()}, "rel_change_median": float(np.median(rel)), "change_median": float(np.median(change))}
    meta = {
        "name": name,
        "experiment": spec.experiment,
        "base": base_id,
        "mode": "adapter_toggle" if base is None else "two_models",
        "dataset": spec.dataset,
        "snapshot": spec.snapshot,
        "source": source,
        "rows": n_rows,
        "tokens": int(enc["attention_mask"].sum()),
        "layers": layers,
        "neurons_per_layer": int(width),
        "prefix_len": prefix_len,
        "batch_size": batch_size,
        "seconds": round(time.perf_counter() - started, 1),
        "created": now(),
        "layer_summary": layer_summary,
    }
    write_json(out_dir / "meta.json", meta)
    project.register("diff", name, meta)
    progress(f"compare {name}: done in {meta['seconds']}s")
    _release(exp)
    if base is not None:
        _release(base)
    return meta


def _finite(value: float) -> float | None:
    return None if math.isnan(value) else float(value)


def diff_layer(project: InterpStore, name: str, layer: int, limit: int = 50, context: int = CONTEXT_TOKENS, sort: str = "change") -> dict[str, Any]:
    """Most-changed neurons of one layer with the token where each changed most. sort: change (1 - r plus relative change), r, rel_change."""
    import numpy as np

    path = diff_dir(project, name) / f"layer-{layer}.npz"
    if not path.exists():
        raise InterpError(f"comparison {name!r} has no layer {layer}")
    arrays = np.load(path)
    tokens = read_json(diff_dir(project, name) / "tokens.json")
    if sort == "r":
        order = np.argsort(np.nan_to_num(arrays["r"], nan=-2.0), kind="stable")
    elif sort == "rel_change":
        order = np.argsort(-arrays["rel_change"], kind="stable")
    elif sort == "change":
        order = np.argsort(-arrays["change"], kind="stable")
    else:
        raise InterpError("sort must be change, r, or rel_change")
    rows = []
    for n in order[:limit].tolist():
        dp, pos = int(arrays["top_dp"][0, n]), int(arrays["top_pos"][0, n])
        toks = tokens["tokens"][dp] if dp >= 0 else []
        rows.append({
            "neuron": n,
            "r": _finite(float(arrays["r"][n])),
            "change": float(arrays["change"][n]),
            "rel_change": float(arrays["rel_change"][n]),
            "max_delta": float(arrays["top_val"][0, n]),
            "before": float(arrays["top_g"][0, n]),
            "after": float(arrays["top_v"][0, n]),
            "datapoint": dp,
            "context": f"{''.join(toks[max(0, pos - context):pos])}[{toks[pos] if 0 <= pos < len(toks) else ''}]{''.join(toks[pos + 1:pos + 1 + context])}" if dp >= 0 else "",
            "labels": tokens["labels"][dp] if dp >= 0 else [],
        })
    meta = diff_meta(project, name)
    return {"comparison": name, "layer": layer, "sort": sort, "summary": meta["layer_summary"].get(str(layer)), "rows": rows}


def diff_neuron(project: InterpStore, name: str, layer: int, neuron: int, limit: int = 10, context: int = CONTEXT_TOKENS) -> dict[str, Any]:
    """Which datapoints and tokens drove one neuron's change."""
    import numpy as np

    path = diff_dir(project, name) / f"layer-{layer}.npz"
    if not path.exists():
        raise InterpError(f"comparison {name!r} has no layer {layer}; compared layers: {diff_meta(project, name)['layers']}")
    arrays = np.load(path)
    if not (0 <= neuron < int(arrays["r"].shape[0])):
        raise InterpError(f"neuron {neuron} out of range (0..{int(arrays['r'].shape[0]) - 1})")
    tokens = read_json(diff_dir(project, name) / "tokens.json")
    col = arrays["dp_delta"][:, neuron].astype(np.float32)
    order = np.argsort(-col, kind="stable")[:limit]
    datapoints = [{"datapoint": int(d), "delta_sum": float(col[d]), "user": tokens["users"][d], "labels": tokens["labels"][d], "bucket": tokens["buckets"][d]} for d in order.tolist() if col[d] > 0]
    top_tokens = []
    for j in range(arrays["top_val"].shape[0]):
        dp, pos = int(arrays["top_dp"][j, neuron]), int(arrays["top_pos"][j, neuron])
        if dp < 0:
            continue
        toks = tokens["tokens"][dp]
        top_tokens.append({"datapoint": dp, "position": pos, "delta": float(arrays["top_val"][j, neuron]), "before": float(arrays["top_g"][j, neuron]), "after": float(arrays["top_v"][j, neuron]), "context": f"{''.join(toks[max(0, pos - context):pos])}[{toks[pos] if pos < len(toks) else ''}]{''.join(toks[pos + 1:pos + 1 + context])}"})
    return {"comparison": name, "layer": layer, "neuron": neuron, "r": _finite(float(arrays["r"][neuron])), "change": float(arrays["change"][neuron]), "rel_change": float(arrays["rel_change"][neuron]), "datapoints": datapoints, "tokens": top_tokens}


# ------------------------------------------------------------------ checkpoints and drift
def experiment_checkpoints(project: InterpStore, job: str, number: int) -> list[dict[str, Any]]:
    """Checkpoints saved during an experiment, as the application reports them."""
    return project.experiment_checkpoints(job, number)


def capture_checkpoints(project: InterpStore, job: str, number: int, dataset: str | None, snapshot: str | None, layers: list[int], topk: int = 40, progress: Any = print) -> list[dict[str, Any]]:
    """Capture every saved checkpoint of an experiment plus its final weights, so drift can be read."""
    checkpoints = experiment_checkpoints(project, job, number)
    ids = [c["model_id"] for c in checkpoints]
    if project.experiment_succeeded(job, number):
        ids.append(f"{job}/{number}")
    if not ids:
        raise InterpError(f"experiment {job}/{number} has no checkpoints (train with --checkpoint-every N) and has not succeeded")
    metas = []
    for model_id in ids:
        name = default_capture_name(model_id, dataset or snapshot or "")
        if (capture_dir(project, name) / "meta.json").exists():
            existing = capture_meta(project, name)
            wanted = set(layers) if layers else set(range(existing["num_layers"]))
            if wanted <= set(existing["layers"]):
                progress(f"capture {name} exists with the requested layers; skipping")
                metas.append(existing)
                continue
            raise InterpError(f"capture {name!r} exists but only has layers {existing['layers']}; delete it or request those layers")
        metas.append(capture(project, CaptureSpec(model_id=model_id, dataset=dataset, snapshot=snapshot, layers=layers, topk=topk, name=name), progress))
    return metas


def _step_of(model_id: str) -> int | None:
    _, _, step = model_id.partition("@")
    return int(step) if step.isdigit() else None


def drift(project: InterpStore, job: str, number: int, source: str, layer: int, neuron: int, limit: int = 12) -> dict[str, Any]:
    """How one neuron's strongest examples move across an experiment's checkpoints (final weights last)."""
    prefix = f"{job}/{number}"
    captures = [m for m in list_captures(project) if (m["model_id"] == prefix or m["model_id"].startswith(prefix + "@")) and m["source"] == source and layer in m["layers"]]
    if not captures:
        raise InterpError(f"no captures of {prefix} on {source!r} with layer {layer}; run `tinystudio interp capture-checkpoints {prefix} --dataset {source} --layers {layer}`")
    captures.sort(key=lambda m: (_step_of(m["model_id"]) is None, _step_of(m["model_id"]) or 0))
    views = [(m, neuron_view(project, m["name"], layer, neuron, limit=max(limit, int(m.get("topk", limit))))) for m in captures]
    # one list type for every step: gate-open when every capture has gate-open entries, otherwise all positives
    use_open = all(v["gate_open"] for _, v in views)
    key = "gate_open" if use_open else "positives"
    sets = [set(v["datapoints"][key]) for _, v in views]
    steps = []
    seen_labels: set[str] = set()
    first_seen: dict[str, int | None] = {}
    for idx, (m, view) in enumerate(views):
        entries = view[key]
        step = _step_of(m["model_id"])
        labels_now = {label for e in entries for label in e["labels"]}
        new_labels = labels_now - seen_labels
        for label in sorted(new_labels):
            first_seen[label] = step
        offshoots = [] if idx == 0 else [e for e in entries if set(e["labels"]) & new_labels]
        current = sets[idx]
        steps.append({
            "capture": m["name"],
            "model_id": m["model_id"],
            "step": step,
            "final": step is None,
            "max": view["stats"]["max"],
            "open_max": view["stats"]["open_max"],
            "list": key,
            "label_share": view["label_share"] if use_open else view["label_share_all"],
            "entries": entries[:limit],
            "datapoints": len(current),
            "jaccard_prev": round(len(current & sets[idx - 1]) / max(1, len(current | sets[idx - 1])), 3) if idx > 0 else None,
            "jaccard_final": round(len(current & sets[-1]) / max(1, len(current | sets[-1])), 3),
            "new_labels": sorted(new_labels) if idx > 0 else [],
            "offshoot_candidates": [{"datapoint": e["datapoint"], "labels": e["labels"], "token": e["token"], "value": e["value"]} for e in offshoots],
            "explanation": (view.get("explanation") or {}).get("explanation"),
        })
        seen_labels |= labels_now
    return {"job": job, "number": number, "source": source, "layer": layer, "neuron": neuron, "topk": views[0][0].get("topk"), "list": key, "core_labels": sorted(label for label, st in first_seen.items() if st == steps[0]["step"]), "first_seen": first_seen, "steps": steps}
