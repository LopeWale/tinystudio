"""Local numeric forecasting and explicitly configured small multimodal learners."""
from __future__ import annotations

import hashlib
import json
import math
import random
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
    with torch.no_grad():
        outputs = model(inputs)
    if state["objective"] == "classification":
        return [state["classes"][index] for index in outputs.argmax(-1).tolist()]
    return outputs.tolist()


def _run(spec: dict, output_dir: Path, emit: Callable[[dict], None], cancelled: Callable[[], bool], multimodal: bool) -> dict:
    import torch
    from torch.nn import functional as F

    definition = spec["definition"]
    params = dict(definition.get("parameters", {}))
    if "resume_from" in params:
        raise ValueError("checkpoint sources must use immutable input artifact roles")
    from .compatibility import resolve_role
    resume_binding = resolve_role(spec, "resume", required=False)
    if resume_binding:
        params["resume_from"] = resume_binding["path"]
    learning = definition.get("learning") or {}
    from .learning_axes import architecture as requested_architecture, runtime_axes, selector, learning_rate
    objective = selector(learning.get("objective", "regression" if multimodal else "forecasting"), "objective")
    if objective not in ({"regression", "classification"} if multimodal else {"forecasting", "regression"}):
        raise ValueError("unsupported research objective")
    axes = runtime_axes(learning, params, strategies={"full", "adam", "full_parameter", "full_finetune"},
        default_strategy="full", initializations={"scratch", "from_scratch", "random", "resume"})
    initialization = axes["initialization"]
    if initialization == "resume" and not params.get("resume_from"):
        raise ValueError("resume initialization requires an immutable resume artifact")
    requested_family = selector(learning.get("family", params.get("family", "multimodal" if multimodal else "gru")), "family")
    if "family" in learning and "family" in params and learning["family"] != params["family"]:
        raise ValueError("conflicting family axes")
    family = requested_family
    if family not in ({"multimodal"} if multimodal else {"gru", "transformer", "mlp"}):
        raise ValueError("unsupported family for the selected numeric/fusion engine")
    representation = learning.get("representation")
    if representation is not None and selector(representation, "representation") not in ({"multimodal", "raw_arrays", "byte_histogram_fusion"} if multimodal else {"numeric"}):
        raise ValueError("unsupported representation for the selected numeric/fusion learner")
    fields = {"hidden_size": (16, 4, 512), "layers": (1, 1, 8)}
    if multimodal:
        # Encoders currently have exactly one hidden layer.
        fields["layers"] = (1, 1, 1)
    else:
        fields["context"] = (8, 1, 512)
        if family == "transformer":
            fields["heads"] = (2, 1, 16)
    dimensions = requested_architecture(learning, params, fields)
    hidden, context, heads = dimensions["hidden_size"], dimensions.get("context", 1), dimensions.get("heads", 1)
    steps, horizon = int(params.get("steps", 40)), int(params.get("horizon", 1))
    lr = learning_rate(params, 0.01)
    seed = int(definition.get("seed", 42))
    if not (1 <= horizon <= 128 and 1 <= steps <= 10000 and 0 < lr <= 1):
        raise ValueError("research dimensions, steps or learning rate exceed bounds")
    if family == "transformer" and hidden % heads:
        raise ValueError("hidden_size must be divisible by heads")
    from .learning import evaluator_validation_inputs
    if evaluator_validation_inputs(spec):
        raise ValueError("numeric/fusion learners currently require their documented internal holdout; external validation artifacts are unsupported")
    if "batch_size" in params:
        raise ValueError("numeric/fusion backend uses full-batch updates; batch_size selection is unsupported")
    fraction = float(params.get("validation_fraction", 0.2))
    if not 0 < fraction < 1:
        raise ValueError("validation_fraction must be between zero and one")
    records = read_records(spec)
    values = params.get("values")
    if not multimodal and values is None:
        if records and all("time" in row for row in records):
            if any(float(left["time"]) >= float(right["time"]) for left, right in zip(records, records[1:])):
                raise ValueError("forecast rows must be strictly chronological")
        values = [row.get("features", row.get("value")) for row in records]
    normalization, classes = {}, []
    if multimodal:
        if len(records) < 4:
            raise ValueError("multimodal training needs at least four examples")
        modalities = params.get("modalities", ["numeric", "text"])
        if len(set(modalities)) < 2:
            raise ValueError("multimodal training requires at least two different modalities")
        features = [_modality_features(row, modalities) for row in records]
        modality_sizes = {name: len(features[0][name]) for name in modalities}
        if any(len(row[name]) != size for row in features for name, size in modality_sizes.items()):
            raise ValueError("each modality must have consistent dimensions")
        order = list(range(len(records)))
        random.Random(seed).shuffle(order)
        validation_count = max(1, int(len(records) * fraction))
        validation_ids, train_ids = order[:validation_count], order[validation_count:]
        inputs = {name: torch.tensor([row[name] for row in features], dtype=torch.float32) for name in modalities}
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
            if any(str(row[target_key]) not in classes for row in records):
                raise ValueError("validation contains a class unseen during training")
            targets = torch.tensor([classes.index(str(row[target_key])) for row in records])
            output_size = len(classes)
        else:
            targets = torch.tensor([_flat(row[target_key]) for row in records], dtype=torch.float32)
            output_size = targets.shape[1]
        architecture = {"family": family, "hidden_size": hidden, "layers": dimensions["layers"], "modalities": modality_sizes, "output_size": output_size}
        corpus = records
        split = {"train": train_ids, "validation": validation_ids, "strategy": "seeded_document_split"}
        raw_probe = [records[index] for index in validation_ids]
    else:
        if not isinstance(values, list) or len(values) < context + horizon + 3 or len(values) > 500000:
            raise ValueError("forecasting requires a bounded numeric series longer than context plus horizon")
        matrix = torch.tensor([_flat(value) for value in values], dtype=torch.float32)
        if matrix.ndim != 2 or not torch.isfinite(matrix).all():
            raise ValueError("time-series values must be finite feature vectors")
        cutoff = int(len(values) * (1 - fraction))
        target_feature = int(params.get("target_feature", 0))
        if not 0 <= target_feature < matrix.shape[1]:
            raise ValueError("target_feature is outside the input feature vector")
        mean, std = matrix[:cutoff].mean(0), matrix[:cutoff].std(0, unbiased=False).clamp_min(1e-6)
        normalization = {"mean": mean, "std": std}
        normalized = (matrix - mean) / std
        windows, target_rows, positions = [], [], []
        for position in range(context, len(values) - horizon + 1):
            # Drop windows straddling the held-out target boundary.
            if position < cutoff < position + horizon:
                continue
            windows.append(normalized[position - context:position])
            target_rows.append(matrix[position:position + horizon, target_feature])
            positions.append(position)
        train_ids = [index for index, position in enumerate(positions) if position + horizon <= cutoff]
        validation_ids = [index for index, position in enumerate(positions) if position >= cutoff]
        if not train_ids or not validation_ids:
            raise ValueError("series cannot form disjoint chronological train/validation targets")
        inputs, targets = torch.stack(windows), torch.stack(target_rows)
        architecture = {"family": family, "hidden_size": hidden, "input_size": matrix.shape[1], "context": context,
                        "output_size": horizon, "heads": heads, "layers": dimensions["layers"]}
        corpus = values
        split = {"train": train_ids, "validation": validation_ids, "strategy": "chronological_target_split",
                 "cutoff": cutoff, "target_positions": positions, "normalization_rows": list(range(cutoff))}
        raw_probe = [matrix[positions[index] - context:positions[index]].tolist() for index in validation_ids]
    if objective != "classification" and not torch.isfinite(targets).all():
        raise ValueError("training targets must be finite")
    corpus_digest = hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest()
    configuration = {"learning": {key: value for key, value in learning.items() if key != "initialization"},
        "parameters": {key: value for key, value in params.items() if key not in {"steps", "resume_from"}}, "seed": seed}
    config_digest = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    def select(indices):
        return {name: tensor[indices] for name, tensor in inputs.items()} if multimodal else inputs[indices]
    train_input, validation_input = select(train_ids), select(validation_ids)
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
                    or saved["config_sha256"] != config_digest or saved["corpus_sha256"] != corpus_digest):
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
                reload_equal = torch.equal(predictions, reloaded(validation_input))
            result_path = output_dir / "predictions.json"
            raw_predictions = predict_from_checkpoint(checkpoint, raw_probe)
            result_path.write_text(json.dumps({"predictions": raw_predictions, "targets": targets[validation_ids].tolist(),
                "split": split, "corpus_sha256": corpus_digest}, indent=2))
            manifest = output_dir / "training.json"
            manifest.write_text(json.dumps(updates, indent=2))
            metrics = {"steps": completed, "initial_validation_loss": initial_validation_loss,
                       "validation_loss": validation_loss, "parameters_changed": changed,
                       "reload_predictions_equal": reload_equal, "training_examples": len(train_ids),
                       "validation_examples": len(validation_ids), "corpus_sha256": corpus_digest}
            if objective == "classification":
                metrics["validation_accuracy"] = float((predictions.argmax(-1) == targets[validation_ids]).float().mean())
            else:
                metrics["validation_mae"] = float((predictions - targets[validation_ids]).abs().mean())
                metrics["persistence_baseline_mse"] = float(((matrix[torch.tensor([split["target_positions"][index] - 1 for index in validation_ids]), target_feature, None] - targets[validation_ids]) ** 2).mean()) if not multimodal else None
            return {"records": updates, "metrics": metrics, "artifacts": [artifact(checkpoint, "model_checkpoint", "application/octet-stream"),
                artifact(result_path, "held_out_predictions"), artifact(manifest, "training_metrics")],
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
