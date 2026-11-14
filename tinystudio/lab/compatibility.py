"""Immutable lab bindings to TinyStudio's existing pretrained training backend.

Authored revisions select artifact IDs/roles. Only the trusted runner resolves
filesystem locations. This adapter never creates operational Job/Experiment rows;
server-owned linkage supplied in the execution spec is retained in its manifest.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from tinystudio.core.file_manifest import directory_manifest

from .learning import artifact
from .partitioning import isolation_groups, partition_labels


def extract_model_archive(source: str | Path, destination: Path, *, max_bytes: int = 8 * 1024 ** 3,
                          max_files: int = 100000) -> Path:
    """Extract only bounded regular files/directories without trusting archive paths."""
    source = Path(source)
    if destination.exists():
        raise ValueError("model extraction destination must be new")
    destination.mkdir(parents=True)
    total, count, seen = 0, 0, set()
    def target_for(name, size, regular):
        nonlocal total, count
        relative = PurePosixPath(name)
        if (not name or "\\" in name or relative.is_absolute() or ".." in relative.parts
            or any(":" in part for part in relative.parts)):
            raise ValueError("unsafe model archive path")
        path = destination.joinpath(*relative.parts)
        if not path.resolve().is_relative_to(destination.resolve()):
            raise ValueError("model archive path escapes destination")
        count += 1
        if count > max_files:
            raise ValueError("model archive exceeds extraction bounds")
        if regular:
            if relative.as_posix() in seen:
                raise ValueError("duplicate model archive member")
            seen.add(relative.as_posix())
            total += size
            if size < 0 or total > max_bytes:
                raise ValueError("model archive exceeds extraction bounds")
        return path
    try:
        # A tar may contain an embedded ZIP checkpoint; ZIP end-record probing
        # can recognize that inner file. Prefer the valid outer tar container.
        if not tarfile.is_tarfile(source) and zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as archive:
                for member in archive.infolist():
                    kind = stat.S_IFMT(member.external_attr >> 16)
                    if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                        raise ValueError("model archive may contain only regular files and directories")
                    path = target_for(member.filename, member.file_size, not member.is_dir())
                    if member.is_dir():
                        path.mkdir(parents=True, exist_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as stream, path.open("xb") as output:
                            shutil.copyfileobj(stream, output, 1024 * 1024)
                        if path.stat().st_size != member.file_size:
                            raise ValueError("model archive file size mismatch")
        else:
            with tarfile.open(source, "r:*") as archive:
                for member in archive:
                    if not member.isfile() and not member.isdir():
                        raise ValueError("model archive may contain only regular files and directories")
                    path = target_for(member.name, member.size, member.isfile())
                    if member.isdir():
                        path.mkdir(parents=True, exist_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(member) as stream, path.open("xb") as output:
                            shutil.copyfileobj(stream, output, 1024 * 1024)
                        if path.stat().st_size != member.size:
                            raise ValueError("model archive file size mismatch")
        directory_manifest(destination)
        return destination
    except (ValueError, tarfile.TarError, zipfile.BadZipFile, OSError):
        shutil.rmtree(destination)
        raise


def _model_root(extracted: Path) -> Path:
    if (extracted / "pretrained.json").is_file():
        manifest = json.loads((extracted / "pretrained.json").read_text())
        relative = PurePosixPath(manifest["trained_directory"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
            raise ValueError("unsafe trained directory in model bundle")
        if manifest["training_type"] != "full":
            raise ValueError("adapter bundles need an explicit merged-model export before use as a pretrained source")
        root = extracted.joinpath(*relative.parts)
    else:
        root = extracted
        while not (root / "config.json").is_file():
            children = list(root.iterdir())
            if len(children) != 1 or not children[0].is_dir():
                raise ValueError("model archive must contain one Hugging Face model directory")
            root = children[0]
    if not (root / "config.json").is_file():
        raise ValueError("model archive has no model config.json")
    return root


def resolve_role(spec: dict, role: str, *, directory: bool = False, required: bool = True,
                 extract_dir: Path | None = None) -> dict | None:
    """Resolve one declared artifact role, verifying outer and extracted hashes."""
    inputs = spec["definition"].get("inputs", [])
    selected = [item for item in inputs if item.get("role") == role]
    if not selected:
        if required:
            raise ValueError(f"an immutable {role!r} input artifact is required")
        return None
    if len(selected) != 1:
        raise ValueError(f"exactly one {role!r} input artifact is required")
    item = selected[0]
    if item.get("visibility", "model") != "model":
        raise ValueError(f"{role} requires a model-visible artifact binding")
    paths = spec.get("input_paths", {})
    raw = paths.get(item["artifact_id"]) if isinstance(paths, dict) else paths[inputs.index(item)]
    if not raw:
        raise ValueError(f"server did not resolve {role!r} artifact")
    raw = raw if isinstance(raw, dict) else {"path": str(raw)}
    path = Path(raw["path"])
    if not path.is_file() or path.is_symlink():
        raise ValueError("input must be a server-resolved regular artifact file")
    expected = item.get("sha256")
    if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"{role} input checksum mismatch")
    binding = {"path": str(path), "artifact_id": item["artifact_id"], "role": role, "sha256": expected}
    if directory:
        if extract_dir is None:
            raise ValueError("model archive resolution requires an owned extraction directory")
        extracted = extract_model_archive(path, extract_dir)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            shutil.rmtree(extracted)
            raise ValueError("model archive changed while extracting")
        root = _model_root(extracted)
        manifest = directory_manifest(root)
        binding.update(path=str(root), archive_path=str(path), directory_sha256=manifest["sha256"], files=manifest["files"])
    return binding


def _rows(binding: dict, minimum: int = 3, forced_partition: str | None = None, *, data_format="conversational") -> list[dict]:
    path = Path(binding["path"])
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != binding["sha256"]:
        raise ValueError("snapshot changed while reading")
    if data.startswith(b"PAR1"):
        import pyarrow as pa
        import pyarrow.parquet as pq
        rows = pq.read_table(pa.BufferReader(data)).to_pylist()
    elif path.suffix in {".jsonl", ".ndjson"}:
        rows = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
    else:
        try:
            rows = json.loads(data)
        except json.JSONDecodeError:
            rows = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
        if isinstance(rows, dict):
            rows = rows.get("records", rows.get("rows", [rows]))
    if not isinstance(rows, list) or len(rows) < minimum:
        raise ValueError(f"pretrained training requires at least {minimum} snapshot rows")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("snapshot rows must be objects")
        if row.get("visibility") == "truth" or any(field in row for field in ("ground_truth", "true_value", "injected_fault")):
            raise ValueError("truth evidence cannot enter pretrained training/validation text")
        if forced_partition:
            labels = [str(row[name]).lower() for name in ("partition", "split", "bucket") if row.get(name) is not None]
            if any(label not in {"validation", "val", "dev", "eval"} for label in labels):
                raise ValueError("validation artifact contains non-validation partition rows")
            row.update(partition=forced_partition, split=forced_partition, visibility="evaluation")
        partition = row.get("partition", row.get("split", row.get("bucket")))
        if row.get("visibility") == "evaluation" and str(partition).lower() not in {"validation", "val", "dev", "eval", "test", "final_test", "final-test"}:
            raise ValueError("evaluation-visible snapshot rows require explicit held-out partitions")
        if data_format == "raw_text":
            if not isinstance(row.get("text"), str) or not row["text"] or "messages" in row:
                raise ValueError("raw_text snapshot rows require exact nonempty text and no messages")
            continue
        if not isinstance(row.get("messages"), list) or not row["messages"]:
            raise ValueError("snapshot rows must contain conversational messages")
        if any(not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"} or not isinstance(message.get("content"), str)
               for message in row["messages"]):
            raise ValueError("snapshot messages require supported roles and string content")
    return rows


def partition_snapshot(rows: list[dict], seed: int, validation_fraction: float, leakage_policy=None, *, min_training_rows: int = 2) -> dict:
    """Preserve declared partitions; otherwise allocate entire isolation groups."""
    policy = leakage_policy or {}
    if isinstance(policy, str):
        policy = {"group_by": [policy]}
    fields = policy.get("group_by")
    if isinstance(fields, str):
        fields = [fields]
    if fields is None:
        fields = next(([name] for name in ("episode_id", "episode", "device_id", "device", "scenario_id", "scenario")
                       if any(name in row for row in rows)), [])
    record_ids = [str(row.get("source_record_id", row.get("record_id", f"row:{index}"))) for index, row in enumerate(rows)]
    declared = partition_labels(rows)
    groups = isolation_groups(rows, fields)
    if declared and declared[0] is not None:
        for group in groups:
            if len({declared[index] for index in group}) > 1:
                raise ValueError("snapshot isolation group overlaps train/validation/final-test partitions")
        strategy = "preserved_explicit_partitions"
        assignments = declared
    else:
        strategy = "deterministic_whole_group_split"
        order = list(range(len(groups)))
        random.Random(seed).shuffle(order)
        desired = max(1, int(len(rows) * validation_fraction))
        validation_groups, count = set(), 0
        for group in order:
            if count >= desired:
                break
            if len(rows) - count - len(groups[group]) >= min_training_rows:
                validation_groups.add(group)
                count += len(groups[group])
        assignments = ["train"] * len(rows)
        for group in validation_groups:
            for index in groups[group]:
                assignments[index] = "validation"
    split = {name: [index for index, partition in enumerate(assignments) if partition == name]
             for name in ("train", "validation", "test")}
    if len(split["train"]) < min_training_rows or not split["validation"]:
        raise ValueError(f"snapshot needs at least {min_training_rows} training rows and one validation row in disjoint groups")
    partitions = {}
    for name, indices in split.items():
        records = [{"source_record_id": record_ids[index],
                    "sha256": hashlib.sha256(json.dumps(rows[index], sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
                   for index in indices]
        partitions[name] = {"indices": indices, "records": records,
            "sha256": hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    return {"strategy": strategy, "group_fields": fields, "groups": [[record_ids[index] for index in group] for group in groups],
            "group_isolation": "each configured field independently via connected components" if fields else "source-row fallback; no episode/device isolation metadata available",
            "partitions": partitions, "train": split["train"], "validation": split["validation"],
            "excluded_test": split["test"], "tokenizer_fitted": False}


def load_pretrained_output(path: str | Path, *, device: str | None = None, comparison_policy=None):
    """Load retained outputs; quantized reloads return measured runtime evidence.

    comparison_policy selects bitwise or explicitly toleranced logits checks.
    The returned manifest adds runtime_verification without editing disk.
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from tinystudio.training.quantization import has_quantized_marker
    root = Path(path)
    manifest = json.loads((root / "pretrained.json").read_text())
    quantized = (manifest.get("output_kind") == "quantized_adapter"
                 or manifest.get("format") == "tinystudio.pretrained.v2" or has_quantized_marker(root))
    if quantized:
        from copy import deepcopy
        from tinystudio.training.quantization import (retained_base_path, nf4_settings, state_digest,
            verify_probes, ComparisonPolicy, ReloadVerificationError, runtime_provenance,
            verification_failure, adapter_state_digest)
        from tinystudio.training.runtime import TrainingPlacement
        receipt = manifest.get("quantization")
        target_runtime = receipt.get("runtime", {}) if isinstance(receipt, dict) else {}
        verification = {"format": "tinystudio.quantized-runtime-verification.v1", "status": "pending",
            "retained_runtime": deepcopy(target_runtime), "observed_runtime": runtime_provenance(device, None),
            "comparison_policy": None, "checks": {}, "diagnostics": []}
        try:
            retained_policy = manifest.get("reload_comparison")
            verification["comparison_policy_source"] = ("explicit_request" if comparison_policy is not None else
                "retained_artifact" if retained_policy is not None else "default_bitwise")
            policy = ComparisonPolicy.resolve(comparison_policy if comparison_policy is not None else retained_policy)
            verification["comparison_policy"] = policy.report()
            if (manifest.get("format"), manifest.get("output_kind"), manifest.get("update_strategy"), manifest.get("training_type")) != (
                    "tinystudio.pretrained.v2", "quantized_adapter", "qlora", "qlora"):
                raise ValueError("inconsistent quantized output format, kind or update strategy")
            base_path = retained_base_path(root, receipt)
            verification["checks"]["retained_base_files"] = True
            if (not isinstance(target_runtime, dict) or not isinstance(target_runtime.get("dtype"), str)
                    or target_runtime["dtype"] not in {"float32", "bfloat16"}):
                raise ValueError("quantized reload requires an explicit retained precision contract")
            placement = TrainingPlacement.resolve(device or target_runtime.get("device"), target_runtime["dtype"])
            verification["observed_runtime"] = runtime_provenance(placement.device, placement.dtype_name)
            if manifest.get("trained_directory") != "adapter" or directory_manifest(root / "adapter") != manifest.get("trained_files"):
                raise ValueError("retained quantized adapter checksum mismatch")
            verification["checks"]["retained_adapter_files"] = True
            if not isinstance(manifest.get("reload_probes"), dict):
                raise ValueError("quantized output requires live-policy reload probes")
            precision = receipt.get("precision")
            adapter_dtype = precision.get("adapter_dtype") if isinstance(precision, dict) else None
            if adapter_dtype != placement.dtype_name:
                raise ValueError("retained adapter precision differs from the retained inference dtype")
            from peft import PeftModel, prepare_model_for_kbit_training
            tokenizer = AutoTokenizer.from_pretrained(root / "adapter", local_files_only=True)
            base = AutoModelForCausalLM.from_pretrained(base_path, local_files_only=True, dtype=torch.float32,
                                                      device_map={"": placement.device})
            base = prepare_model_for_kbit_training(base, use_gradient_checkpointing=False)
            from tinystudio.training.nf4_execution import NF4ExecutionPolicy
            execution_policy = NF4ExecutionPolicy.resolve(receipt.get("execution_policy"))
            verification["execution_policy"] = execution_policy.to_dict()
            if "execution_policy" in receipt:
                verification["execution_configuration"] = execution_policy.apply(base, placement.device)
            verification["checks"]["nf4_settings"] = nf4_settings(base) == receipt.get("settings")
            verification["checks"]["base_state"] = state_digest(base, base_only=True) == receipt.get("state_sha256")
            if not verification["checks"]["nf4_settings"] or not verification["checks"]["base_state"]:
                raise ValueError("restored quantized base state differs from the retained starting base")
            model = PeftModel.from_pretrained(base, root / "adapter", is_trainable=False, local_files_only=True,
                                             autocast_adapter_dtype=False)
            restored_precision = []
            adapter_parameters = []
            for name, parameter in model.named_parameters():
                if "lora_" in name:
                    adapter_parameters.append(parameter)
                    if parameter.dtype != placement.dtype:
                        restored_precision.append({"name": name, "from": str(parameter.dtype).replace("torch.", ""),
                                                   "to": placement.dtype_name})
                        parameter.data = parameter.data.to(placement.dtype)
            if not adapter_parameters or any(parameter.dtype != placement.dtype for parameter in adapter_parameters):
                raise ValueError("reloaded adapters do not preserve the retained precision contract")
            verification["adapter_precision_restorations"] = restored_precision
            verification["adapter_state_sha256"] = adapter_state_digest(model)
            verification["checks"]["adapter_precision"] = True
            verification["checks"]["adapter_preserved_base_state"] = state_digest(model, base_only=True) == receipt.get("state_sha256")
            if not verification["checks"]["adapter_preserved_base_state"]:
                raise ValueError("adapter restoration changed the retained quantized base state")
            placement.check_model(model)
            buffer_devices = set()
            for buffer in model.buffers():
                placement.check_device(buffer.device)
                buffer_devices.add(str(buffer.device))
            verification["placement"] = {**placement.report(), "buffer_devices": sorted(buffer_devices)}
            verification["checks"]["model_placement"] = True
            model.eval()
            verification["probes"] = verify_probes(model, root, manifest["reload_probes"], placement.device,
                                                    comparison_policy=policy)
            verification["status"] = "passed"
            manifest = deepcopy(manifest)
            manifest["runtime_verification"] = verification
            return model, tokenizer, manifest
        except Exception as exc:
            if isinstance(exc, ReloadVerificationError):
                verification["probes"] = exc.receipt
            raise verification_failure(str(exc), verification, code="quantized_reload_failed") from exc
    if comparison_policy is not None:
        raise ValueError("comparison_policy requires retained quantized reload probes")
    target = root / manifest["trained_directory"]
    tokenizer = AutoTokenizer.from_pretrained(str(target), local_files_only=True)
    if manifest.get("output_kind", "model" if manifest["training_type"] == "full" else "adapter") == "model":
        model = AutoModelForCausalLM.from_pretrained(str(target), local_files_only=True, dtype=torch.float32)
    else:
        from peft import PeftModel
        base = AutoModelForCausalLM.from_pretrained(str(root / "source-model"), local_files_only=True, dtype=torch.float32)
        model = PeftModel.from_pretrained(base, str(target), is_trainable=False, local_files_only=True)
    model.eval()
    return model, tokenizer, manifest


def _bundle(root: Path, paths: list[Path], target: Path):
    with tarfile.open(target, "w") as bundle:
        for path in paths:
            for source in sorted(path.rglob("*")) if path.is_dir() else [path]:
                if source.is_file():
                    info = bundle.gettarinfo(str(source), arcname=source.relative_to(root).as_posix())
                    info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
                    with source.open("rb") as handle:
                        bundle.addfile(info, handle)


class _Cancelled(RuntimeError):
    pass


def execute_pretrained(spec: dict, output_dir: Path, emit: Callable[[dict], None], cancelled: Callable[[], bool]) -> dict:
    from tinystudio.training.train import TrainConfig, train_experiment
    import torch

    definition = spec["definition"]
    params = dict(definition.get("parameters", {}))
    learning = dict(definition.get("learning") or {})
    # Resolve every independent axis before opening any supplied artifact.
    from .learning_axes import architecture as requested_architecture, runtime_axes, selector, learning_rate, selected_axis, pretrained_method
    from .learning import evaluator_validation_inputs
    from tinystudio.training.runtime import TrainingPlacement
    if any(key in params for key in {"base_model", "weights", "resume_from", "teacher_checkpoint", "refinetune_from"}):
        raise ValueError("pretrained sources must use immutable input artifact roles")
    method = pretrained_method(learning, params)
    requested_dtype = [selector(container[name], name).lower() for container in (learning, params)
                       for name in ("dtype", "training_dtype") if name in container]
    normalized_dtype = [{"fp32": "float32", "bf16": "bfloat16", "fp16": "float16"}.get(value, value) for value in requested_dtype]
    if len(set(normalized_dtype)) > 1:
        raise ValueError("conflicting requested training dtype axes")
    runtime = TrainingPlacement.resolve(selected_axis(learning, params, "device"), normalized_dtype[0] if normalized_dtype else None)
    actual_device, actual_dtype = runtime.device, runtime.dtype_name
    for container in (learning, params):
        container["update_strategy"] = method.update_strategy
        if "device" in container:
            container["device"] = actual_device
    axes = runtime_axes(learning, params, strategies={"full", "lora", "qlora"}, default_strategy="full",
        initializations={"pretrained", "from_pretrained"}, default_initialization="pretrained",
        actual_device=actual_device, actual_dtype=actual_dtype)
    strategy, objective = method.update_strategy, method.objective
    from tinystudio.training.quantization import ComparisonPolicy
    if strategy != "qlora" and "reload_comparison" in params:
        raise ValueError("NF4 reload comparison applies only to QLoRA outputs")
    reload_comparison = ComparisonPolicy.resolve(params.get("reload_comparison")).report() if strategy == "qlora" else None
    if definition.get("retrieval"):
        raise ValueError("pretrained training cannot silently consume a retrieval selection; compose a frozen retrieval run explicitly")
    if "family" in learning and selector(learning["family"], "family") not in {"pretrained", "causal_lm"}:
        raise ValueError("pretrained family is selected by its immutable artifact; omit family or use pretrained/causal_lm")
    if "family" in params and selector(params["family"], "family") not in {"pretrained", "causal_lm"}:
        raise ValueError("pretrained family is selected by its immutable artifact")
    if "representation" in learning and selector(learning["representation"], "representation") not in {"pretrained_tokenizer", "model_tokenizer", "tokenizer"}:
        raise ValueError("pretrained training uses the immutable model tokenizer; fitting a replacement representation is unsupported")
    context_params = dict(params)
    if "max_length" in params:
        if "context" in params and params["context"] != params["max_length"]:
            raise ValueError("conflicting context and max_length")
        context_params["context"] = params["max_length"]
    dimensions = requested_architecture(learning, context_params, {"context": (128, 2, 4096)})
    rate = learning_rate(params, 0.0002)
    if "steps" in params and "max_steps" in params and params["steps"] != params["max_steps"]:
        raise ValueError("conflicting steps and max_steps")
    settings = {key: value for key, value in params.items() if key in TrainConfig.__dataclass_fields__}
    settings.update(base_model="<immutable pretrained_model input>", training_type=strategy,
        objective=objective, update_strategy=strategy, data_format=method.data_format, device=actual_device, dtype=actual_dtype,
        seed=int(definition.get("seed", 42)), logging_steps=1, max_length=dimensions["context"], learning_rate=rate)
    settings.setdefault("batch_size", 1)
    settings.setdefault("gradient_accumulation_steps", 1)
    settings.setdefault("max_steps", int(params.get("steps", 8)))
    config = TrainConfig.from_dict(settings)
    if not (1 <= config.max_steps <= 100000 and 1 <= config.batch_size <= 128
        and 1 <= config.gradient_accumulation_steps <= 128 and math.isfinite(config.epochs)
        and 0 < config.epochs <= 100 and 1 <= config.lora_r <= 256):
        raise ValueError("pretrained training parameters exceed local experiment bounds")
    if strategy == "qlora":
        from tinystudio.training.train import qlora_support
        supported, reason = qlora_support(actual_device)
        if not supported:
            raise ValueError(reason)
    validation_bindings = evaluator_validation_inputs(spec)
    model_input = resolve_role(spec, "pretrained_model", directory=True, extract_dir=output_dir / "pretrained-input")
    model_config = json.loads((Path(model_input["path"]) / "config.json").read_text())
    text_config = model_config.get("text_config") or model_config
    capacity = text_config.get("max_position_embeddings", text_config.get("n_positions"))
    if isinstance(capacity, int) and dimensions["context"] > capacity:
        raise ValueError("requested training context exceeds the immutable pretrained model capacity")
    snapshot = resolve_role(spec, "snapshot")
    rows = _rows(snapshot, minimum=2 if validation_bindings else 3, data_format=method.data_format)
    for index, row in enumerate(rows):
        row.setdefault("source_record_id", f"{snapshot['artifact_id']}:{index}")
        if validation_bindings and row.get("partition", row.get("split", row.get("bucket"))) is None:
            row["partition"] = "train"
    for binding in validation_bindings:
        held_out = _rows(binding, minimum=1, forced_partition="validation", data_format=method.data_format)
        for index, row in enumerate(held_out):
            row.setdefault("source_record_id", f"{binding['artifact_id']}:{index}")
            rows.append(row)
    output_dir.mkdir(parents=True, exist_ok=True)
    if cancelled():
        return {"records": [], "metrics": {}, "artifacts": [], "qualification": {"status": "cancelled"}}
    source = output_dir / "source-model"
    if source.exists():
        raise ValueError("pretrained output directory already contains a source model")
    shutil.copytree(model_input["path"], source)
    if directory_manifest(source)["sha256"] != model_input["directory_sha256"]:
        raise ValueError("source model changed while freezing")
    snapshot_copy = output_dir / ("snapshot" + Path(snapshot["path"]).suffix)
    shutil.copyfile(snapshot["path"], snapshot_copy)
    if hashlib.sha256(snapshot_copy.read_bytes()).hexdigest() != snapshot["sha256"]:
        raise ValueError("snapshot changed while freezing")
    seed = int(definition.get("seed", 42))
    fraction = float(params.get("validation_fraction", 0.2))
    if not 0 < fraction < 1:
        raise ValueError("validation_fraction must be between zero and one")
    split = partition_snapshot(rows, seed, fraction, params.get("leakage_policy", learning.get("leakage_policy")))
    val_ids, train_ids = split["validation"], split["train"]
    split_path = output_dir / "partitions.json"
    split_path.write_text(json.dumps(split, indent=2))
    config.base_model = str(source)
    records = []
    def cancellation_result():
        for name in ("model", "adapter", "checkpoints", "quantized-base"):
            shutil.rmtree(output_dir / name, ignore_errors=True)
        for name in ("pretrained.json", "pretrained-model.tar", "reload-probes.safetensors", "nf4-policy.json"):
            (output_dir / name).unlink(missing_ok=True)
        return {"records": records, "metrics": {}, "artifacts": [],
                "qualification": {"status": "cancelled", "trainer": "existing TinyStudio train_experiment",
                                  "partial_model_published": False}}
    def check_cancel():
        if cancelled():
            raise _Cancelled("pretrained run cancelled at trainer callback boundary")
    def progress(message):
        check_cancel()
        emit({"type": "progress", "message": message})
    def on_log(kind, row):
        check_cancel()
        records.append({"type": kind, **row})
        emit({"type": kind, **row})
    try:
        result = train_experiment(output_dir, config, [rows[index] for index in train_ids],
            [rows[index] for index in val_ids], progress=progress, weights=source, on_log=on_log,
            runtime=runtime, check_cancel=check_cancel)
        check_cancel()
    except _Cancelled:
        return cancellation_result()
    trained = Path(result["output"])
    manifest = {"format": "tinystudio.pretrained.v2" if result.get("quantization") else "tinystudio.pretrained.v1", "training_type": strategy,
        "objective": objective, "update_strategy": strategy, "output_kind": result["output_kind"],
        **({"quantization": result["quantization"], "reload_probes": result["reload_probes"]} if result.get("quantization") else {}),
        **({"reload_comparison": reload_comparison} if result.get("quantization") else {}),
        "data_format": method.data_format,
        "axes": {**axes, "objective": objective, "architecture": dimensions, "architecture_source": "immutable pretrained artifact", "tokenizer_source": "immutable pretrained artifact",
                 "context_semantics": "training sequence cap; source positional embeddings are not resized", "source_context_capacity": capacity},
        "validation_inputs": validation_bindings,
        "trained_directory": trained.relative_to(output_dir).as_posix(), "model_input": model_input,
        "snapshot_input": snapshot, "snapshot_file": snapshot_copy.name,
        "split": split, "partition_manifest_sha256": hashlib.sha256(split_path.read_bytes()).hexdigest(), "config": config.as_dict(),
        "compatibility": spec.get("compatibility", params.get("compatibility", {})),
        "run_link": spec.get("run", {}), "metrics": result,
        "source_files": directory_manifest(source), "trained_files": directory_manifest(trained)}
    manifest_path = output_dir / "pretrained.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    # Explicit reloading proves the final saved output is consumable; the fixture
    # independently compares predictions and changed parameters as well.
    reloaded, tokenizer, verified_manifest = load_pretrained_output(output_dir, device=actual_device,
        comparison_policy=params.get("reload_comparison"))
    runtime_verification = verified_manifest.get("runtime_verification")
    verification_artifacts = []
    if runtime_verification is not None:
        manifest["runtime_verification"] = runtime_verification
        manifest_path.write_text(json.dumps(manifest, indent=2))
        from tinystudio.training.runtime_compatibility import RuntimeEvidencePaths
        verification_path = RuntimeEvidencePaths(output_dir).verification_manifest
        verification_path.write_text(json.dumps(runtime_verification, allow_nan=False, indent=2))
        verification_artifacts.append({**artifact(verification_path, "runtime_verification"), "visibility": "evaluation"})
    probe_device = next(reloaded.parameters()).device
    probe = {key: value.to(probe_device) for key, value in tokenizer("user: pressure", return_tensors="pt").items()}
    with torch.no_grad():
        logits = reloaded(**probe).logits
    if not torch.isfinite(logits).all():
        raise ValueError("reloaded pretrained model produces non-finite logits")
    del reloaded
    target = output_dir / "pretrained-model.tar"
    if cancelled():
        return cancellation_result()
    retained_paths = [source, trained, manifest_path, split_path]
    if result.get("quantization"):
        retained_paths.extend([output_dir / "quantized-base", output_dir / "reload-probes.safetensors", output_dir / "nf4-policy.json"])
    _bundle(output_dir, retained_paths, target)
    if cancelled():
        return cancellation_result()
    return {"records": records, "metrics": {**result.get("metrics", {}), "steps": result["steps"],
        "trainable_parameters": result["trainable_parameters"], "total_parameters": result["total_parameters"],
        "reload_inference_finite": True, "model_sha256": model_input["sha256"], "snapshot_sha256": snapshot["sha256"]},
        "artifacts": [artifact(target, "pretrained_model_bundle", "application/x-tar"),
                      {**artifact(manifest_path, "pretrained_manifest"), "visibility": "evaluation"},
                      {**artifact(snapshot_copy, "training_snapshot"), "visibility": "evaluation"},
                      {**artifact(split_path, "partition_manifest"), "visibility": "evaluation"}, *verification_artifacts],
        "qualification": {"status": "completed", "training_type": strategy, "backend": result["device"], "dtype": actual_dtype,
            "requested_axes_honored": True, "structural_architecture_source": "immutable pretrained artifact",
            "validation_bindings": "evaluator-only map; never used for tokenizer fitting or optimizer updates",
            "trainer": "existing TinyStudio train_experiment", "saved_model_reloaded": True,
            "live_policy_reload_equal": (True if runtime_verification and
                runtime_verification.get("comparison_policy", {}).get("mode") == "bitwise" and
                runtime_verification.get("status") == "passed" else None),
            "live_policy_reload_verified": bool(runtime_verification and runtime_verification.get("status") == "passed"),
            "runtime_verification": runtime_verification,
            "resume": "legacy backend saves weights; exact optimizer continuation is not provided",
            "cancellation": "checked at every optimizer step, progress/log callback and before model publication", "network_downloads": False,
            "partition_strategy": split["strategy"], "group_isolation": split["group_isolation"],
            "excluded_final_test_records": len(split["excluded_test"]), "tokenizer_fitted": False}}
