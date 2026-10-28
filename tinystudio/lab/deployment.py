"""Portable runtime bundles and measured host inference, with explicit target limits."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import pickle
import platform
import shutil
import statistics
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Callable

from .compatibility import directory_manifest, extract_model_archive, load_pretrained_output, resolve_role
from .learning import artifact, load_checkpoint


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def peak_rss() -> dict:
    """Process-lifetime maximum, never presented as isolated model RAM."""
    try:
        import resource
        raw = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (ImportError, OSError):
        return {"raw": None, "raw_unit": None, "bytes": None,
                "source": "resource.getrusage(RUSAGE_SELF).ru_maxrss unavailable"}
    system = platform.system()
    unit = "bytes" if system == "Darwin" else "KiB" if system == "Linux" else "platform-specific"
    return {"raw": raw, "raw_unit": unit, "bytes": raw if system == "Darwin" else raw * 1024 if system == "Linux" else None,
            "source": "resource.getrusage(RUSAGE_SELF).ru_maxrss",
            "scope": "whole process lifetime high-water; includes prior work, imports, validation, loading and inference"}


def runtime_manifest() -> dict:
    import torch
    versions = {}
    for name in ("torch", "transformers", "tokenizers", "peft", "tinystudio"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    implementation = {name: _hash(Path(__file__).with_name(name)) for name in
                      ("deployment.py", "learning.py", "compatibility.py")}
    runtime = {"python": {"version": sys.version, "implementation": sys.implementation.name,
                          "executable_sha256": _hash(Path(sys.executable))},
               "libraries": versions, "implementation_sha256": implementation,
               "torch_build": torch.__config__.show(), "device": "cpu", "dtype": "float32",
               "intraop_threads": torch.get_num_threads(), "interop_threads": torch.get_num_interop_threads()}
    hardware = {"system": platform.system(), "release": platform.release(), "version": platform.version(),
                "machine": platform.machine(), "processor": platform.processor(), "logical_cpu_count": os.cpu_count()}
    return {"runtime": runtime, "runtime_sha256": hashlib.sha256(_canonical(runtime)).hexdigest(),
            "hardware": hardware, "hardware_sha256": hashlib.sha256(_canonical(hardware)).hexdigest()}


def target_compatibility(target, kind: str, host: dict) -> dict:
    requested = target if isinstance(target, dict) else {"id": target}
    target_id = requested.get("id", requested.get("kind", "desktop.cpu"))
    target_id = {"desktop": "desktop.cpu", "gateway": "gateway.linux.cpu", "linux_gateway": "gateway.linux.cpu",
                 "raspberry_pi": "gateway.linux.cpu", "cortex_m": "mcu.cortex_m", "mcu": "mcu.cortex_m"}.get(target_id, target_id)
    operations = ["embedding", "linear/matmul", "softmax", "normalization", "GRU or causal attention", "UTF-8/tokenizer operations"]
    reports = []
    for profile in ("desktop.cpu", "gateway.linux.cpu", "mcu.cortex_m"):
        supported = profile != "mcu.cortex_m"
        on_target = supported and (profile == "desktop.cpu" or host["system"] == "Linux")
        reports.append({"target": profile, "package_supported": supported, "measured_on_this_target_class": on_target,
            "runtime": "CPython + matching TinyStudio/PyTorch CPU; Transformers/PEFT for pretrained bundles" if supported else None,
            "prerequisites": ["compatible Python/PyTorch binaries for target OS and ISA", "installed TinyStudio implementation matching package hashes",
                "sufficient measured target RAM/storage; neither is certified here"] if supported else
                ["model conversion to a specific MCU inference runtime", "implemented and qualified kernels for every operator",
                 "fixed tensor arenas and tokenizer integration", "quantization/accuracy validation", "board measurements of SRAM, flash, latency and energy"],
            "unsupported_reason": None if supported else "This adapter produces Python/PyTorch artifacts, not MCU firmware or MCU-compatible operator graphs"})
    selected = next((row for row in reports if row["target"] == target_id), None)
    if selected is None:
        selected = {"target": target_id, "package_supported": False, "measured_on_this_target_class": False,
                    "unsupported_reason": "No runtime/package adapter is implemented for this target", "prerequisites": []}
    if requested.get("os") and requested["os"].casefold() != host["system"].casefold():
        selected["measured_on_this_target_class"] = False
    if requested.get("architecture") and requested["architecture"].casefold() != host.get("machine", "").casefold():
        selected["measured_on_this_target_class"] = False
    return {"requested": requested, "selected": selected, "portfolio": reports, "model_kind": kind,
            "operator_requirements": operations, "physical_deployment_verified": False,
            "ram_bytes": None, "flash_bytes": None, "energy_joules": None,
            "estimates": {}, "note": "A host benchmark does not qualify a different OS, ISA, gateway or MCU board"}


def _load_runner(model_file: Path, scratch: Path):
    import torch
    # Torch checkpoints are zip containers too; identify the restricted data format
    # before treating an uploaded zip/tar as an HF model archive.
    state = None
    try:
        state = torch.load(model_file, map_location="cpu", weights_only=True)
    except (RuntimeError, ValueError, EOFError, KeyError, TypeError, pickle.UnpicklingError):
        pass
    if isinstance(state, dict) and state.get("format") == "tinystudio.learning.v1":
        model, tokenizer, state = load_checkpoint(model_file)
        model.eval()
        def infer(prompt, max_new_tokens):
            ids = tokenizer.encode(prompt) or [tokenizer.eos_id]
            generated = []
            with torch.inference_mode():
                for _ in range(max_new_tokens):
                    inputs = torch.tensor([ids[-state["architecture"]["context"]:]], dtype=torch.long)
                    token = int(model(inputs)[0, -1].argmax())
                    if token == tokenizer.eos_id:
                        break
                    generated.append(token)
                    ids.append(token)
            return {"prompt": prompt, "completion": tokenizer.decode(generated), "token_ids": generated,
                    "generated_tokens": len(generated)}
        kind = "scratch_language"
        provenance = {"format": state["format"], "architecture": state["architecture"],
                      "training_corpus_sha256": state.get("corpus_sha256")}
    else:
        extracted = extract_model_archive(model_file, scratch / "model-unpacked")
        if (extracted / "pretrained.json").is_file():
            model, tokenizer, source_manifest = load_pretrained_output(extracted)
            provenance = {"format": source_manifest["format"], "training_type": source_manifest["training_type"],
                          "model_input": source_manifest["model_input"], "snapshot_input": source_manifest["snapshot_input"]}
        else:
            root = extracted
            while not (root / "config.json").is_file():
                children = list(root.iterdir())
                if len(children) != 1 or not children[0].is_dir():
                    raise ValueError("deployment model archive must contain a compatible Hugging Face model")
                root = children[0]
            from transformers import AutoModelForCausalLM, AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(str(root), local_files_only=True)
            model = AutoModelForCausalLM.from_pretrained(str(root), local_files_only=True, dtype=torch.float32)
            model.eval()
            provenance = {"format": "huggingface.causal_lm", "model_files": directory_manifest(root)}
        kind = "pretrained_language"
        def infer(prompt, max_new_tokens):
            encoded = tokenizer(prompt, return_tensors="pt")
            capacity = getattr(model.config, "max_position_embeddings", None) or getattr(model.config, "n_positions", None)
            if isinstance(capacity, int) and encoded["input_ids"].shape[1] + max_new_tokens > capacity:
                raise ValueError("prompt plus requested generation exceeds the pretrained model context")
            with torch.inference_mode():
                outputs = model.generate(**encoded, do_sample=False, max_new_tokens=max_new_tokens,
                    pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id)
            generated = outputs[0, encoded["input_ids"].shape[1]:].tolist()
            return {"prompt": prompt, "completion": tokenizer.decode(generated, skip_special_tokens=True),
                    "token_ids": generated, "generated_tokens": len(generated)}
    tensor_bytes = sum(value.numel() * value.element_size() for value in model.state_dict().values())
    return infer, {"kind": kind, "loaded_state_dict_tensor_bytes": tensor_bytes, "provenance": provenance}


def _percentile(samples: list[float], fraction: float) -> float:
    values = sorted(samples)
    position = fraction * (len(values) - 1)
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def run_packaged_inference(package_dir: str | Path, prompt: str, max_new_tokens: int = 16) -> dict:
    """Run an extracted deployment package with this installed TinyStudio runtime."""
    if not isinstance(prompt, str) or not 1 <= max_new_tokens <= 512:
        raise ValueError("inference requires a string prompt and 1..512 new tokens")
    root = Path(package_dir)
    manifest = json.loads((root / "target.json").read_text())
    model_file = root / "model.artifact"
    if _hash(model_file) != manifest["model"]["sha256"]:
        raise ValueError("packaged model checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="inference-", dir=root) as work:
        import torch
        previous_threads = torch.get_num_threads()
        try:
            torch.set_num_threads(manifest["runtime"]["intraop_threads"])
            infer, _ = _load_runner(model_file, Path(work))
            return infer(prompt, max_new_tokens)
        finally:
            torch.set_num_threads(previous_threads)


def _package(root: Path, names: list[str], target: Path):
    with tarfile.open(target, "w") as archive:
        for name in sorted(names):
            source = root / name
            info = archive.gettarinfo(str(source), arcname=name)
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            with source.open("rb") as stream:
                archive.addfile(info, stream)


def execute_deployment(spec: dict, output_dir: Path, emit: Callable[[dict], None], cancelled: Callable[[], bool]) -> dict:
    import torch
    params = spec["definition"].get("parameters", {})
    if any(key in params for key in {"model_path", "checkpoint_path", "base_model", "weights"}):
        raise ValueError("deployment models must use immutable model/checkpoint artifact roles")
    warmup, repetitions = int(params.get("warmup", 2)), int(params.get("repetitions", params.get("reps", 10)))
    max_new_tokens = int(params.get("max_new_tokens", 16))
    threads = int(params.get("threads", 1))
    prompts = params.get("prompts", [params.get("prompt", "pressure")])
    if (not 0 <= warmup <= 100 or not 2 <= repetitions <= 10000 or not 1 <= max_new_tokens <= 512
        or not 1 <= threads <= 64 or not isinstance(prompts, list) or not prompts
        or len(prompts) > 1000 or any(not isinstance(prompt, str) or not prompt or len(prompt) > 65536 for prompt in prompts)):
        raise ValueError("deployment timing/generation parameters exceed bounds")
    if cancelled():
        return {"records": [], "metrics": {}, "artifacts": [], "qualification": {"status": "cancelled", "host_inference_measured": False}}
    rss_before = peak_rss()
    models = [item for item in spec["definition"].get("inputs", []) if item.get("role") in {"model", "checkpoint"}]
    if len(models) != 1:
        raise ValueError("deployment requires exactly one immutable model or checkpoint input")
    binding = resolve_role(spec, models[0]["role"])
    output_dir.mkdir(parents=True, exist_ok=True)
    frozen = output_dir / "model.artifact"
    shutil.copyfile(binding["path"], frozen)
    if _hash(frozen) != binding["sha256"]:
        raise ValueError("model changed while freezing deployment input")
    previous_threads = torch.get_num_threads()
    samples, warmup_outputs = [], []
    try:
        torch.set_num_threads(threads)
        started = time.perf_counter_ns()
        infer, model = _load_runner(frozen, output_dir)
        load_ms = (time.perf_counter_ns() - started) / 1e6
        rss_loaded = peak_rss()
        for index in range(warmup):
            if cancelled():
                break
            warmup_outputs.append(infer(prompts[index % len(prompts)], max_new_tokens))
        if len(warmup_outputs) == warmup:
            for index in range(repetitions):
                if cancelled():
                    break
                prompt = prompts[index % len(prompts)]
                start = time.perf_counter_ns()
                output = infer(prompt, max_new_tokens)
                latency = (time.perf_counter_ns() - start) / 1e6
                row = {"repetition": index + 1, "latency_ms": latency, "output": output}
                samples.append(row)
                emit({"type": "benchmark", "repetition": index + 1, "latency_ms": latency,
                      "generated_tokens": output["generated_tokens"]})
        rss_after = peak_rss()
        runtime = runtime_manifest()
    finally:
        torch.set_num_threads(previous_threads)
    latencies = [row["latency_ms"] for row in samples]
    timing = {"clock": "time.perf_counter_ns", "scope": "resident-model tokenization plus greedy autoregressive inference and decoding; model loading excluded",
        "clock_resolution_seconds": time.get_clock_info("perf_counter").resolution, "batch_size": 1,
        "warmup_requested": warmup, "warmup_completed": len(warmup_outputs), "repetitions_requested": repetitions,
        "repetitions_completed": len(samples), "prompt_schedule": "round-robin", "prompts": prompts,
        "max_new_tokens": max_new_tokens, "sampling": False, "load_ms": load_ms,
        "latency_ms": {"min": min(latencies), "mean": statistics.mean(latencies), "p50": _percentile(latencies, 0.5),
                       "p90": _percentile(latencies, 0.9), "p95": _percentile(latencies, 0.95), "max": max(latencies)} if latencies else None,
        "percentile_method": "linear interpolation of sorted samples at p*(n-1)",
        "previous_process_intraop_threads": previous_threads}
    memory = {"before_load": rss_before, "after_load": rss_loaded, "after_benchmark": rss_after,
        "process_high_water_growth_bytes": max(0, rss_after["bytes"] - rss_before["bytes"]) if rss_after["bytes"] is not None and rss_before["bytes"] is not None else None,
        "model_ram_bytes": None, "explanation": "High-water growth is not peak model RAM or an isolated allocation delta"}
    compatibility = target_compatibility(params.get("target", "desktop.cpu"), model["kind"], runtime["hardware"])
    architecture = model["provenance"].get("architecture", {})
    family = architecture.get("family")
    if family == "gru":
        compatibility["operator_requirements"] = ["byte/token embedding", "GRU matrix products and sigmoid/tanh gates", "linear output head", "argmax", "UTF-8 byte tokenizer"]
    elif family == "transformer":
        compatibility["operator_requirements"] = ["token/position embeddings", "causal multi-head attention and softmax", "GELU feedforward", "layer normalization", "linear output head", "argmax", "UTF-8 byte tokenizer"]
    compatibility["operator_inventory_method"] = "architecture requirements; no MCU operator graph has been exported or qualified"
    model.update(artifact_id=binding["artifact_id"], sha256=binding["sha256"], artifact_bytes=frozen.stat().st_size)
    manifest = {"format": "tinystudio.deployment.v1", "target": compatibility["requested"], "compatibility": compatibility,
                "model": model, **runtime, "measurements": {"timing": timing, "memory": memory},
                "unmeasured": {"target_ram_bytes": None, "target_flash_bytes": None, "energy_joules": None}}
    target_path = output_dir / "target.json"
    target_path.write_text(json.dumps(manifest, indent=2))
    benchmark_path = output_dir / "benchmark.json"
    benchmark_path.write_text(json.dumps({"samples": samples, "warmup_outputs": warmup_outputs, "timing": timing, "memory": memory}, indent=2))
    compatibility_path = output_dir / "compatibility.json"
    compatibility_path.write_text(json.dumps(compatibility, indent=2))
    entry = output_dir / "infer.py"
    entry.write_text('from pathlib import Path\nimport argparse, json\nfrom tinystudio.lab.deployment import run_packaged_inference\np = argparse.ArgumentParser()\np.add_argument("prompt")\np.add_argument("--max-new-tokens", type=int, default=16)\na = p.parse_args()\nprint(json.dumps(run_packaged_inference(Path(__file__).parent, a.prompt, a.max_new_tokens)))\n')
    readme = output_dir / "RUNTIME.txt"
    readme.write_text("Install the matching TinyStudio implementation and compatible Python/PyTorch runtime recorded in target.json.\nFor pretrained bundles also install matching Transformers, Tokenizers, and PEFT.\nAfter extracting, run: python infer.py 'pressure' --max-new-tokens 16\nThis package includes model artifacts and a Python entrypoint, not a bundled interpreter, MCU firmware, or target qualification.\n")
    package = output_dir / "deployment-package.tar"
    _package(output_dir, ["model.artifact", "target.json", "compatibility.json", "infer.py", "RUNTIME.txt"], package)
    complete = len(samples) == repetitions
    return {"records": samples, "metrics": {"model_artifact_bytes": frozen.stat().st_size,
        "loaded_state_dict_tensor_bytes": model["loaded_state_dict_tensor_bytes"], "package_bytes": package.stat().st_size,
        "inference_repetitions": len(samples), "latency_ms": timing["latency_ms"],
        "process_peak_rss_bytes": rss_after["bytes"], "model_ram_bytes": None, "flash_bytes": None, "energy_joules": None},
        "artifacts": [artifact(package, "deployment_package", "application/x-tar"), artifact(target_path, "target_manifest"),
                      artifact(benchmark_path, "inference_benchmark"), artifact(compatibility_path, "target_compatibility")],
        "qualification": {"status": "completed" if complete else "cancelled", "host_inference_measured": bool(samples),
            "target_package_supported": compatibility["selected"]["package_supported"],
            "physical_target_verified": False, "mcu_ready": False, "runtime_bundled": False,
            "scope": "measured current-process CPU inference; portable Python package with explicit runtime prerequisites"}}
