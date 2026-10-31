"""Validation-only numeric comparisons with explicit source custody and units.

Classification uses the learner's existing str(target) classes. Original target
JSON is retained separately because numeric 1 and string "1" share that class.
Source IDs must be usable nonempty strings; malformed IDs fail explicitly rather
than receiving replacement identities. IDs may recur across episodes or windows.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import re

FORMAT = "tinystudio.numeric-evaluation.v1"


def _source_ids(value, name):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be an array of nonempty source ID strings")


def _invalid_json_constant(value):
    raise ValueError(f"invalid JSON constant {value}")


def _vectors(rows, count, width, name):
    if len(rows) != count or count < 1:
        raise ValueError(f"{name} requires exact nonzero case count")
    for row in rows:
        if not isinstance(row, list) or len(row) != width:
            raise ValueError(f"{name} output width mismatch")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in row):
            raise ValueError(f"{name} must be finite numeric vectors")


def _errors(predictions, targets):
    errors = [float(p) - float(t) for p, t in zip(predictions, targets, strict=True)]
    try:
        result = {"mae": math.fsum(abs(value) / len(errors) for value in errors),
                  "mse": math.fsum(value * value / len(errors) for value in errors)}
    except OverflowError as exc:
        raise ValueError("numeric evaluation metrics must be finite") from exc
    if not all(math.isfinite(value) for value in result.values()):
        raise ValueError("numeric evaluation metrics must be finite")
    return result


def build_numeric_evaluation(*, task, output_count, predictions, targets, cases,
                             training_targets, training_source_ids, persistence,
                             model_sha256, corpus_sha256, completed, reload_agreement):
    """Compare exact validation cases; fit fusion constants on training only."""
    count = len(cases)
    if task not in {"forecasting", "regression", "classification"}:
        raise ValueError("unsupported numeric evaluation task")
    if type(output_count) is not int or output_count < 1:
        raise ValueError("numeric evaluation requires positive output_count")
    if not count or len(predictions) != count or len(targets) != count:
        raise ValueError("numeric evaluation requires exact nonzero case count")
    for name, digest in (("model_sha256", model_sha256), ("corpus_sha256", corpus_sha256)):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError(f"{name} must be a SHA-256 hex digest")
    if type(completed) is not bool or type(reload_agreement) is not bool:
        raise ValueError("completed and reload_agreement must be boolean")
    _source_ids(training_source_ids, "training_source_ids")
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("numeric evaluation cases must be objects")
        sources, context = case.get("source_record_ids"), case.get("context_source_record_ids")
        _source_ids(sources, "source_record_ids")
        _source_ids(context, "context_source_record_ids")
        if task == "forecasting":
            if len(sources) != output_count or not context:
                raise ValueError("forecast provenance requires one target source per output and nonempty context")
        elif len(sources) != 1 or context:
            raise ValueError("fusion provenance requires exactly one target source and empty context")
        raw_target = case.get("raw_target_json")
        if not isinstance(raw_target, str):
            raise ValueError("raw_target_json must be valid JSON text")
        try:
            json.loads(raw_target, parse_constant=_invalid_json_constant)
        except ValueError as exc:
            raise ValueError("raw_target_json must be valid JSON text") from exc
    identities = [case.get("case_id") for case in cases]
    if any(not isinstance(value, str) or not value.strip() for value in identities) or len(set(identities)) != count:
        raise ValueError("numeric evaluation case IDs must be unique")
    baseline = {"fit_source_record_ids": [], "fit_case_count": 0}
    if task == "classification":
        if output_count != 1 or any(not isinstance(value, str) for value in [*predictions, *targets, *training_targets]):
            raise ValueError("classification requires canonical string labels and output_count 1")
        if not training_targets or len(training_targets) != len(training_source_ids):
            raise ValueError("training baseline requires exact nonzero case count")
        frequencies = Counter(training_targets)
        majority = min(frequencies, key=lambda label: (-frequencies[label], label))
        baseline.update(kind="training_majority", label="Training majority class", constant=majority,
                        tie_break="lexicographically smallest canonical str(target) label",
                        fit_source_record_ids=list(training_source_ids), fit_case_count=len(training_targets))
        baseline_values = [majority] * count
        metrics = {"model": {"accuracy": sum(p == t for p, t in zip(predictions, targets, strict=True)) / count},
                   "baseline": {"accuracy": sum(majority == t for t in targets) / count}, "per_output": []}
    else:
        _vectors(predictions, count, output_count, "predictions")
        _vectors(targets, count, output_count, "targets")
        if task == "forecasting":
            _vectors(persistence, count, output_count, "persistence")
            baseline.update(kind="persistence", label="Last context target feature repeated across horizon")
            baseline_values = persistence
        else:
            _vectors(training_targets, len(training_source_ids), output_count, "training targets")
            constant = [math.fsum(row[index] / len(training_targets) for row in training_targets)
                        for index in range(output_count)]
            baseline.update(kind="training_mean", label="Per-output training target mean", constant=constant,
                            fit_source_record_ids=list(training_source_ids), fit_case_count=len(training_targets))
            baseline_values = [constant[:] for _ in cases]
        metrics = {"model": _errors([v for row in predictions for v in row], [v for row in targets for v in row]),
                   "baseline": _errors([v for row in baseline_values for v in row], [v for row in targets for v in row]),
                   "per_output": [{"index": index, "unit": None,
                       "model": _errors([row[index] for row in predictions], [row[index] for row in targets]),
                       "baseline": _errors([row[index] for row in baseline_values], [row[index] for row in targets])}
                       for index in range(output_count)]}
    metrics.update(cases=count, scalar_values=count * output_count,
                   aggregation="unweighted case/component mean; validation only; units not established")
    records = [{**case, "prediction": predictions[index], "target": targets[index], "baseline": baseline_values[index]}
               for index, case in enumerate(cases)]
    return {"format": FORMAT, "task": task, "partition": "validation", "output_count": output_count,
            "model_sha256": model_sha256, "corpus_sha256": corpus_sha256, "baseline": baseline,
            "records": records, "metrics": metrics,
            "qualification": {"status": "completed" if completed else "cancelled",
                "comparison_complete": bool(completed and reload_agreement), "evaluation_valid": True,
                "validation_only": True,
                "scope": "Validation comparison only; no final-test, physical-unit, or generalization claim. Classification labels use str(target); original JSON is retained separately."}}
