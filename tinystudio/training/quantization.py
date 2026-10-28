"""NF4 state, precision and persistence contracts shared by the existing trainer."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
from tinystudio.core.file_manifest import directory_manifest

CPU_STACK = {
    "torch": "2.15.0+cpu", "transformers": "5.21.0", "peft": "0.21.2", "trl": "1.16.0",
    "accelerate": "1.15.0", "tokenizers": "0.23.3", "safetensors": "0.8.0",
    "bitsandbytes": "0.50.2", "datasets": "5.1.0",
}
POLICY_FORMAT = "tinystudio.quantized-policy.v1"


def is_retained_quantized(config, status):
    return bool(config.get("quantization_format") or status.get("quantization_format") or status.get("quantization"))


def has_quantized_marker(directory):
    if (directory / "quantized-base").exists() or (directory / "nf4-policy.json").exists():
        return True
    manifest = directory / "pretrained.json"
    if manifest.exists():
        try:
            value = json.loads(manifest.read_text())
            return value.get("format") == "tinystudio.pretrained.v2" or value.get("output_kind") == "quantized_adapter"
        except (ValueError, AttributeError):
            # Unknown retained metadata cannot authorize a float-base fallback.
            return True
    return False


def dependency_versions():
    result = {}
    for name in CPU_STACK:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return result


def cpu_pack_compatible():
    return (platform.system() == "Linux" and platform.machine() == "aarch64"
            and sys.version_info[:2] == (3, 12) and dependency_versions() == CPU_STACK)


def base_state(model):
    """Canonical frozen base tensors, including packed NF4 scales/code/nested state."""
    if hasattr(model, "peft_config"):
        model = model.get_base_model()
    result = {}
    for name, value in model.state_dict().items():
        if "lora_" in name:
            continue
        name = name.replace(".base_layer.", ".")
        if name in result:
            raise ValueError("ambiguous canonical base tensor identity")
        result[name] = value
    return result


def state_digest(model, *, base_only=False):
    import torch
    tensors = base_state(model) if base_only else model.state_dict()
    digest = hashlib.sha256()
    for name, value in sorted(tensors.items()):
        digest.update(json.dumps([name, list(value.shape), str(value.dtype)], separators=(",", ":")).encode())
        digest.update(value.detach().cpu().contiguous().reshape(-1).view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def nf4_settings(model):
    import torch
    from bitsandbytes.nn import Linear4bit
    modules = [module for module in model.modules() if isinstance(module, Linear4bit)]
    if not modules:
        raise ValueError("QLoRA requires actual packed NF4 modules")
    for module in modules:
        state = module.weight.quant_state
        if module.weight.dtype != torch.uint8 or state is None or state.quant_type != "nf4" or not state.nested:
            raise ValueError("QLoRA base must retain packed NF4 and nested quantization state")
    return {"modules": len(modules), "storage_dtype": "uint8", "quant_type": "nf4", "double_quantization": True,
            "compute_dtypes": sorted({str(module.compute_dtype).replace("torch.", "") for module in modules})}


def prepare_adapter_precision(trainer, runtime):
    """Honor the selected precision after TRL's documented QLoRA bf16 cast.

    This occurs before optimizer creation, so no optimizer state is reinterpreted.
    Frozen packed/base tensors are never cast by this operation.
    """
    if trainer.optimizer is not None:
        raise ValueError("adapter precision must be established before creating its optimizer")
    cast = []
    for name, parameter in trainer.model.named_parameters():
        if parameter.requires_grad:
            if "lora_" not in name:
                raise ValueError("QLoRA optimizer may update only explicitly selected LoRA parameters")
            if parameter.dtype != runtime.dtype:
                cast.append({"name": name, "from": str(parameter.dtype).replace("torch.", ""), "to": runtime.dtype_name})
                parameter.data = parameter.data.to(runtime.dtype)
    settings = nf4_settings(trainer.model)
    if settings["compute_dtypes"] != [runtime.dtype_name]:
        raise ValueError("actual NF4 compute precision differs from selected training precision")
    return {"adapter_dtype": runtime.dtype_name, "post_trainer_precision_corrections": cast, **settings}


def assert_adapter_optimizer(trainer):
    expected = {id(parameter) for name, parameter in trainer.model.named_parameters() if parameter.requires_grad}
    actual = {id(parameter) for group in trainer.optimizer.param_groups for parameter in group["params"]}
    if not expected or actual != expected or any(parameter.requires_grad and "lora_" not in name
            for name, parameter in trainer.model.named_parameters()):
        raise ValueError("QLoRA optimizer differs from the selected adapter parameters")


def retain_base(model, directory, runtime, check_cancel):
    check_cancel()
    base = directory / "quantized-base"
    if base.exists():
        raise ValueError("quantized base retention requires a new directory")
    (directory / "nf4-policy.json").write_text(json.dumps({"format": POLICY_FORMAT, "base_directory": base.name}))
    settings = nf4_settings(model)
    model.save_pretrained(base)
    check_cancel()
    return {"format": "tinystudio.nf4-base.v1", "directory": base.name,
            "files": directory_manifest(base), "state_sha256": state_digest(model, base_only=True),
            "settings": settings, "runtime": {"os": platform.system(), "machine": platform.machine(),
                "python": platform.python_version(), "device": runtime.device, "dtype": runtime.dtype_name,
                "dependencies": dependency_versions()}}


def retained_base_path(root, receipt):
    if not isinstance(receipt, dict) or receipt.get("format") != "tinystudio.nf4-base.v1":
        raise ValueError("quantized adapter requires a retained NF4 base contract")
    name = receipt.get("directory")
    if name != "quantized-base":
        raise ValueError("quantized adapter requires its own retained quantized-base directory")
    base = root / name
    if directory_manifest(base) != receipt.get("files"):
        raise ValueError("retained quantized base checksum mismatch")
    return base


def capture_probes(model, tokenizer, directory: Path, device: str, context: int):
    import torch
    from safetensors.torch import save_file
    model.eval()
    tensors, inputs = {}, []
    for index, prompt in enumerate(("pressure normal", "-1.25 V", "sensor ready")):
        encoded = tokenizer(prompt, return_tensors="pt", add_special_tokens=False, truncation=True, max_length=context)
        if encoded["input_ids"].numel() == 0:
            raise ValueError("reload probe requires nonempty tokenized input")
        row = {name: value.tolist() for name, value in encoded.items() if name in {"input_ids", "attention_mask"}}
        batch = {name: torch.tensor(value, dtype=torch.long, device=device) for name, value in row.items()}
        with torch.inference_mode():
            tensors[f"probe_{index}"] = model(**batch).logits[0, -1].detach().cpu().contiguous()
        inputs.append(row)
    path = directory / "reload-probes.safetensors"
    save_file(tensors, str(path))
    return {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "inputs": inputs,
            "comparison": "bitwise_equal_last_token_logits", "mode": "eval; no autocast; no sampling"}


def verify_probes(model, root: Path, receipt, device):
    import torch
    from safetensors.torch import load_file
    path = checked_file(root, receipt.get("file"))
    if hashlib.sha256(path.read_bytes()).hexdigest() != receipt.get("sha256"):
        raise ValueError("reload probe checksum mismatch")
    expected = load_file(str(path))
    inputs = receipt.get("inputs")
    if not isinstance(inputs, list) or len(inputs) != 3 or len(expected) != 3:
        raise ValueError("reload probe contract requires three fixed sequences")
    model.eval()
    for index, row in enumerate(inputs):
        batch = {name: torch.tensor(value, dtype=torch.long, device=device) for name, value in row.items()}
        with torch.inference_mode():
            actual = model(**batch).logits[0, -1].detach().cpu()
        if not torch.equal(expected[f"probe_{index}"], actual):
            raise ValueError("reloaded quantized policy differs from the live trained policy")


def checked_file(root: Path, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("retained model paths must be relative regular files")
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or not path.resolve().is_relative_to(root.resolve()) or path.is_symlink() or not path.is_file():
        raise ValueError("retained model file is missing or unsafe")
    return path
