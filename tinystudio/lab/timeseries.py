"""Local numeric forecasting and explicitly configured small multimodal learners."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Callable

from .learning import artifact
from .rl import read_records


def _checkpoint_model(architecture: dict):
    import torch
    from torch import nn
    class ResearchModel(nn.Module):
        def __init__(self):
            super().__init__()
            hidden = architecture["hidden_size"]
            self.family = architecture["family"]
            if self.family == "multimodal":
                self.encoders = nn.ModuleDict({name: nn.Sequential(nn.Linear(size, hidden), nn.ReLU())
                                              for name, size in architecture["modalities"].items()})
                self.head = nn.Linear(hidden * len(self.encoders), architecture["output_size"])
            elif self.family == "gru":
                self.core = nn.GRU(architecture["input_size"], hidden, num_layers=architecture.get("layers", 1), batch_first=True)
                self.head = nn.Linear(hidden, architecture["output_size"])
            elif self.family == "transformer":
                self.embedding = nn.Linear(architecture["input_size"], hidden)
                self.position = nn.Embedding(architecture["context"], hidden)
                layer = nn.TransformerEncoderLayer(hidden, architecture["heads"], hidden * 2,
                    dropout=0, batch_first=True)
                self.core = nn.TransformerEncoder(layer, architecture.get("layers", 1), enable_nested_tensor=False)
                self.head = nn.Linear(hidden, architecture["output_size"])
            else:
                blocks = [nn.Flatten(), nn.Linear(architecture["input_size"] * architecture["context"], hidden), nn.Tanh()]
                for _ in range(architecture.get("layers", 1) - 1):
                    blocks.extend([nn.Linear(hidden, hidden), nn.Tanh()])
                self.core = nn.Sequential(*blocks)
                self.head = nn.Linear(hidden, architecture["output_size"])

        def forward(self, value):
            if self.family == "multimodal":
                encoded = torch.cat([module(value[name]) for name, module in self.encoders.items()], dim=-1)
            elif self.family == "gru":
                encoded = self.core(value)[0][:, -1]
            elif self.family == "transformer":
                embeddings = self.embedding(value) + self.position(torch.arange(value.shape[1]))
                encoded = self.core(embeddings)[:, -1]
            else:
                encoded = self.core(value)
            return self.head(encoded)
    return ResearchModel().float()


def _flat(value):
    if not isinstance(value, (list, tuple)):
        return [float(value)]
    result = []
    for item in value:
        result.extend(_flat(item))
    return result


def _modality_features(row: dict, modalities: list[str]) -> dict:
    features = {}
    for name in modalities:
        if name == "text":
            if not isinstance(row.get("text"), str):
                raise ValueError("text modality requires a string")
            encoded = row["text"].encode()
            histogram = [0.0] * 256
            for byte in encoded:
                histogram[byte] += 1 / max(1, len(encoded))
            features[name] = histogram
        elif name in {"numeric", "image", "audio"}:
            value = row.get(name)
            if not isinstance(value, (list, tuple)) or not value:
                raise ValueError(f"{name} modality requires nonempty numeric arrays")
            features[name] = _flat(value)
        else:
            raise ValueError(f"unsupported modality {name!r}")
        if not features[name] or len(features[name]) > 65536 or any(not math.isfinite(value) for value in features[name]):
            raise ValueError("modality arrays must be finite and bounded")
    return features


def load_research_checkpoint(path):
    import torch
    state = torch.load(path, map_location="cpu", weights_only=True)
    if state.get("format") != "tinystudio.timeseries.v1":
        raise ValueError("unsupported research checkpoint")
    model = _checkpoint_model(state["architecture"])
    model.load_state_dict(state["model"])
    model.eval()
    return model, state


def predict_from_checkpoint(path, values):
    """Use saved transforms and a newly loaded forecasting or fusion model."""
    import torch
    model, state = load_research_checkpoint(path)
    if state["architecture"]["family"] == "multimodal":
        rows = [_modality_features(row, list(state["architecture"]["modalities"])) for row in values]
        inputs = {}
        for name in state["architecture"]["modalities"]:
            tensor = torch.tensor([row[name] for row in rows], dtype=torch.float32)
            inputs[name] = (tensor - state["normalization"][name]["mean"]) / state["normalization"][name]["std"]
    else:
        tensor = torch.tensor(values, dtype=torch.float32)
        if tensor.ndim == 2:
            tensor = tensor.unsqueeze(-1)
        if list(tensor.shape[1:]) != [state["architecture"]["context"], state["architecture"]["input_size"]]:
            raise ValueError("forecast inputs must match saved context and feature size")
        inputs = (tensor - state["normalization"]["mean"]) / state["normalization"]["std"]
    inference_inputs = inputs.values() if isinstance(inputs, dict) else [inputs]
    if any(not torch.isfinite(tensor).all() for tensor in inference_inputs):
        raise ValueError("checkpoint inference inputs must be finite")
    with torch.no_grad():
        outputs = model(inputs)
    if not torch.isfinite(outputs).all():
        raise ValueError("checkpoint inference produced non-finite outputs")
    if state["objective"] == "classification":
        return [state["classes"][index] for index in outputs.argmax(-1).tolist()]
    return outputs.tolist()


def _run(spec: dict, output_dir: Path, emit: Callable[[dict], None], cancelled: Callable[[], bool], multimodal: bool) -> dict:
    import torch
    from torch.nn import functional as F

    definition = spec["definition"]
    params = dict(definition.get("parameters", {}))
    learning = definition.get("learning") or {}
    from .policy_selection import validate_policy_selection, resume_configuration, matching_resume_configuration
    from .learning_initialization import checkpoint_binding
    from .learning_axes import learning_rate
    selected = validate_policy_selection(definition, "learning.multimodal" if multimodal else "learning.timeseries")
    objective, family = selected["objective"], selected["family"]
    resume_binding, _ = checkpoint_binding(spec, selected["initialization"])
    if resume_binding:
        params["resume_from"] = resume_binding["path"]
    dimensions = selected["dimensions"]
    hidden, context, heads = dimensions["hidden_size"], dimensions.get("context", 1), dimensions.get("heads", 1)
    steps, horizon = int(params.get("steps", 40)), int(params.get("horizon", 1))
    lr = learning_rate(params, 0.01)
    seed = int(definition.get("seed", 42))
    if not (1 <= horizon <= 128 and 1 <= steps <= 10000 and 0 < lr <= 1):
        raise ValueError("research dimensions, steps or learning rate exceed bounds")
    if family == "transformer" and hidden % heads:
        raise ValueError("hidden_size must be divisible by heads")
    from .research_partitions import forecast_window_plan, record_partition_plan
    if "leakage_policy" in params:
        raise ValueError("leakage_policy is unsupported; use group_by and temporal_split")
    temporal_split = params.get("temporal_split", False)
    if type(temporal_split) is not bool:
        raise ValueError("temporal_split must be boolean")
    if multimodal and temporal_split:
        raise ValueError("temporal_split is only supported for forecasting")
    if "batch_size" in params:
        raise ValueError("numeric/fusion backend uses full-batch updates; batch_size selection is unsupported")
    fraction = float(params.get("validation_fraction", 0.2))
    if not 0 < fraction < 1:
        raise ValueError("validation_fraction must be between zero and one")
    records = read_records(spec)
    values = params.get("values")
    if values is not None:
        if multimodal or records:
            raise ValueError("values and records are mutually exclusive forecasting inputs")
        if not isinstance(values, list):
            raise ValueError("forecasting values must be a numeric list")
        records = [{"source_record_id": f"inline:values:{index}", "value": value}
                   for index, value in enumerate(values)]
    normalization, classes = {}, []
    if multimodal:
        split = record_partition_plan(records, validation_fraction=fraction, seed=seed,
                                      group_by=params.get("group_by"))
        train_ids, validation_ids = split["train"], split["validation"]
        active_ids = sorted(train_ids + validation_ids)
        if len(active_ids) < 4:
            raise ValueError("multimodal training needs at least four examples")
        modalities = params.get("modalities", ["numeric", "text"])
        if len(set(modalities)) < 2:
            raise ValueError("multimodal training requires at least two different modalities")
        features = {index: _modality_features(records[index], modalities) for index in active_ids}
        modality_sizes = {name: len(features[active_ids[0]][name]) for name in modalities}
        if any(len(row[name]) != size for row in features.values() for name, size in modality_sizes.items()):
            raise ValueError("each modality must have consistent dimensions")
        inputs = {}
        for name, size in modality_sizes.items():
            tensor = torch.zeros((len(records), size), dtype=torch.float32)
            tensor[active_ids] = torch.tensor([features[index][name] for index in active_ids], dtype=torch.float32)
            inputs[name] = tensor
        for name, tensor in inputs.items():
            mean = tensor[train_ids].mean(0)
            std = tensor[train_ids].std(0, unbiased=False).clamp_min(1e-6)
            normalization[name] = {"mean": mean, "std": std}
            inputs[name] = (tensor - mean) / std
        target_key = params.get("target_key", "target")
        if objective == "classification":
            classes = sorted(set(str(records[index][target_key]) for index in train_ids))
            if len(classes) < 2:
                raise ValueError("classification training requires at least two training classes")
            if any(str(records[index][target_key]) not in classes for index in active_ids):
                raise ValueError("validation contains a class unseen during training")
            targets = torch.zeros(len(records), dtype=torch.long)
            targets[active_ids] = torch.tensor([classes.index(str(records[index][target_key])) for index in active_ids])
            output_size = len(classes)
        else:
            active_targets = torch.tensor([_flat(records[index][target_key]) for index in active_ids], dtype=torch.float32)
            output_size = active_targets.shape[1]
            targets = torch.zeros((len(records), output_size), dtype=torch.float32)
            targets[active_ids] = active_targets
        architecture = {"family": family, "hidden_size": hidden, "layers": dimensions["layers"], "modalities": modality_sizes, "output_size": output_size}
        raw_probe = [records[index] for index in validation_ids]
        prediction_sources = [[index] for index in validation_ids]
    else:
        if len(records) > 500000:
            raise ValueError("forecasting requires a bounded numeric series longer than context plus horizon")
        split = forecast_window_plan(records, context=context, horizon=horizon, validation_fraction=fraction,
                                     seed=seed, group_by=params.get("group_by"), temporal_split=temporal_split)
        train_ids, validation_ids = split["train"], split["validation"]
        active_ids = sorted(split["record_partitions"]["train"] + split["record_partitions"]["validation"])
        active_matrix = torch.tensor([_flat(records[index].get("features", records[index].get("value")))
                                      for index in active_ids], dtype=torch.float32)
        if active_matrix.ndim != 2 or not torch.isfinite(active_matrix).all():
            raise ValueError("time-series values must be finite feature vectors")
        matrix = torch.zeros((len(records), active_matrix.shape[1]), dtype=torch.float32)
        matrix[active_ids] = active_matrix
        target_feature = int(params.get("target_feature", 0))
        if not 0 <= target_feature < matrix.shape[1]:
            raise ValueError("target_feature is outside the input feature vector")
        fitting = matrix[split["normalization_rows"]]
        mean, std = fitting.mean(0), fitting.std(0, unbiased=False).clamp_min(1e-6)
        normalization = {"mean": mean, "std": std}
        normalized = (matrix - mean) / std
        inputs = torch.stack([normalized[window["context_rows"]] for window in split["windows"]])
        targets = torch.stack([matrix[window["target_rows"], target_feature] for window in split["windows"]])
        architecture = {"family": family, "hidden_size": hidden, "input_size": matrix.shape[1], "context": context,
                        "output_size": horizon, "heads": heads, "layers": dimensions["layers"]}
        raw_probe = [matrix[split["windows"][index]["context_rows"]].tolist() for index in validation_ids]
        prediction_sources = [split["windows"][index]["target_rows"] for index in validation_ids]
    if objective != "classification" and not torch.isfinite(targets).all():
        raise ValueError("training targets must be finite")
    corpus = {"records": [records[index] for index in active_ids], "split": split}
    corpus_digest = hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest()
    configuration = {"learning": {key: value for key, value in learning.items() if key != "initialization"},
        "parameters": {key: value for key, value in params.items() if key not in {"steps", "resume_from"}}, "seed": seed}
    configuration = resume_configuration(configuration)
    config_digest = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    def select(indices):
        return {name: tensor[indices] for name, tensor in inputs.items()} if multimodal else inputs[indices]
    train_input, validation_input = select(train_ids), select(validation_ids)
    for selected_input in (train_input, validation_input):
        tensors = selected_input.values() if isinstance(selected_input, dict) else [selected_input]
        if any(not torch.isfinite(tensor).all() for tensor in tensors):
            raise ValueError("normalized research inputs must be finite")
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_threads = torch.get_num_threads()
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        torch.set_num_threads(1)
        try:
            model = _checkpoint_model(architecture)
            initial = {key: value.clone() for key, value in model.state_dict().items()}
            optimizer = torch.optim.Adam(model.parameters(), lr=lr)
            start = 0
            if params.get("resume_from"):
                model, saved = load_research_checkpoint(params["resume_from"])
                if (saved["architecture"] != architecture or saved["objective"] != objective
                    or saved["backend"] != "cpu" or saved["torch_version"] != str(torch.__version__)
                    or not matching_resume_configuration(saved, configuration) or saved["corpus_sha256"] != corpus_digest):
                    raise ValueError("resume model does not match corpus, configuration or backend")
                optimizer = torch.optim.Adam(model.parameters(), lr=lr)
                optimizer.load_state_dict(saved["optimizer"])
                torch.set_rng_state(saved["torch_rng"])
                start = saved["step"]
                if steps <= start:
                    raise ValueError("resume steps must exceed checkpoint step")
                initial = {key: value.clone() for key, value in model.state_dict().items()}
            criterion = F.cross_entropy if objective == "classification" else F.mse_loss
            model.eval()
            with torch.no_grad():
                initial_validation_loss = float(criterion(model(validation_input), targets[validation_ids]))
            updates, completed = [], start
            model.train()
            for step in range(start, steps):
                if cancelled():
                    break
                optimizer.zero_grad(set_to_none=True)
                loss = criterion(model(train_input), targets[train_ids])
                if not torch.isfinite(loss):
                    raise ValueError("research training produced non-finite loss")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                completed = step + 1
                row = {"step": completed, "loss": float(loss.detach())}
                updates.append(row)
                emit({"type": "metric", **row})
            model.eval()
            with torch.no_grad():
                predictions = model(validation_input)
                validation_loss = float(criterion(predictions, targets[validation_ids]))
            if not all(math.isfinite(value) for value in (initial_validation_loss, validation_loss)):
                raise ValueError("validation losses must be finite")
            changed = any(not torch.equal(initial[key], value) for key, value in model.state_dict().items())
            checkpoint = output_dir / "model.pt"
            state = {"format": "tinystudio.timeseries.v1", "architecture": architecture, "objective": objective,
                "model": model.state_dict(), "optimizer": optimizer.state_dict(), "torch_rng": torch.get_rng_state(),
                "step": completed, "normalization": normalization, "classes": classes, "split": split,
                "seed": seed, "backend": "cpu", "torch_version": str(torch.__version__), "corpus_sha256": corpus_digest,
                "configuration": configuration, "config_sha256": config_digest}
            torch.save(state, checkpoint)
            del model, optimizer
            reloaded, _ = load_research_checkpoint(checkpoint)
            with torch.no_grad():
                reloaded_predictions = reloaded(validation_input)
                if not torch.isfinite(predictions).all() or not torch.isfinite(reloaded_predictions).all():
                    raise ValueError("validation predictions must be finite")
                reload_equal = torch.equal(predictions, reloaded_predictions)
            result_path = output_dir / "predictions.json"
            raw_predictions = predict_from_checkpoint(checkpoint, raw_probe)
            expected_predictions = ([classes[index] for index in reloaded_predictions.argmax(-1).tolist()]
                                    if objective == "classification" else reloaded_predictions.tolist())
            if raw_predictions != expected_predictions or not reload_equal:
                raise ValueError("raw checkpoint inference disagrees with validation predictions")
            raw_targets = targets[validation_ids].tolist()
            if not raw_predictions or not (len(raw_predictions) == len(raw_targets) == len(prediction_sources) == len(validation_ids)):
                raise ValueError("validation predictions require exact nonzero case count")
            prediction_records = []
            for prediction, target, source_indices in zip(raw_predictions, raw_targets, prediction_sources, strict=True):
                row = {"prediction": prediction, "target": target, "partition": "validation",
                       "source_record_ids": [split["source_record_ids"][index] for index in source_indices]}
                if objective == "classification":
                    row.update(target=classes[target], target_index=target)
                if "episode_id" in records[source_indices[0]]:
                    row["episode_id"] = records[source_indices[0]]["episode_id"]
                prediction_records.append(row)
            report = {"predictions": raw_predictions, "targets": raw_targets,
                "records": prediction_records, "split": split, "corpus_sha256": corpus_digest}
            if objective == "classification":
                # Preserve the existing encoded target array and make its mapping explicit.
                report.update(classes=classes, target_encoding="class_index",
                              target_labels=[classes[index] for index in raw_targets])
            result_path.write_text(json.dumps(report, indent=2, allow_nan=False))
            manifest = output_dir / "training.json"
            manifest.write_text(json.dumps(updates, indent=2, allow_nan=False))
            metrics = {"steps": completed, "initial_validation_loss": initial_validation_loss,
                       "validation_loss": validation_loss, "parameters_changed": changed,
                       "reload_predictions_equal": reload_equal, "training_examples": len(train_ids),
                       "validation_examples": len(validation_ids), "corpus_sha256": corpus_digest}
            if objective == "classification":
                metrics["validation_accuracy"] = float((predictions.argmax(-1) == targets[validation_ids]).float().mean())
            else:
                metrics["validation_mae"] = float((predictions - targets[validation_ids]).abs().mean())
                metrics["persistence_baseline_mse"] = float(((matrix[torch.tensor([split["windows"][index]["context_rows"][-1] for index in validation_ids]), target_feature, None] - targets[validation_ids]) ** 2).mean()) if not multimodal else None
            from .numeric_evaluation import build_numeric_evaluation
            def original_flat(value):
                if not isinstance(value, (list, tuple)):
                    return [value]
                return [item for nested in value for item in original_flat(nested)]
            cases, persistence = [], []
            for index, source_indices in zip(validation_ids, prediction_sources, strict=True):
                context_indices = [] if multimodal else split["windows"][index]["context_rows"]
                original_target = (records[index][target_key] if multimodal else
                    [original_flat(records[source].get("features", records[source].get("value")))[target_feature]
                     for source in source_indices])
                case = {"case_id": f"validation:{index}",
                    "source_record_ids": [split["source_record_ids"][source] for source in source_indices],
                    "context_source_record_ids": [split["source_record_ids"][source] for source in context_indices],
                    "raw_target_json": json.dumps(original_target, allow_nan=False)}
                if "episode_id" in records[source_indices[0]]:
                    case["episode_id"] = records[source_indices[0]]["episode_id"]
                cases.append(case)
                if not multimodal:
                    persistence.append([float(matrix[context_indices[-1], target_feature])] * horizon)
            evaluation = build_numeric_evaluation(
                task=objective if multimodal else "forecasting", output_count=1 if objective == "classification" else output_size if multimodal else horizon,
                predictions=raw_predictions,
                targets=[classes[index] for index in raw_targets] if objective == "classification" else raw_targets,
                cases=cases,
                training_targets=([classes[index] for index in targets[train_ids].tolist()] if objective == "classification"
                                  else targets[train_ids].tolist()) if multimodal else [],
                training_source_ids=[split["source_record_ids"][index] for index in train_ids] if multimodal else [],
                persistence=persistence, model_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                corpus_sha256=corpus_digest, completed=completed == steps, reload_agreement=reload_equal)
            evaluation_path = output_dir / "numeric-evaluation.json"
            evaluation_path.write_text(json.dumps(evaluation, indent=2, allow_nan=False))
            return {"records": updates, "baseline_comparison": {key: evaluation[key] for key in ("format", "metrics", "qualification")}, "metrics": metrics, "artifacts": [artifact(checkpoint, "model_checkpoint", "application/octet-stream"),
                artifact(result_path, "held_out_predictions"), artifact(manifest, "training_metrics"), artifact(evaluation_path, "independent_evaluation")],
                "qualification": {"status": "completed" if completed == steps else "cancelled", "backend": "cpu", "dtype": "float32",
                    "save_unload_reload": reload_equal, "chronological_targets": not multimodal,
                    "scope": "small scratch fusion using byte-histogram text and raw numeric/image/audio arrays" if multimodal else "local supervised numeric forecasting; no state-of-the-art or generalization guarantee",
                    "resume": "exact same CPU backend, corpus and configuration at update boundaries"}}
        finally:
            torch.set_num_threads(previous_threads)


def execute_timeseries(spec, output_dir, emit, cancelled):
    return _run(spec, output_dir, emit, cancelled, multimodal=False)


def execute_multimodal(spec, output_dir, emit, cancelled):
    return _run(spec, output_dir, emit, cancelled, multimodal=True)
