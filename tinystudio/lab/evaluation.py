"""Versioned, dependency-free laboratory validators and provenance audits.

Quantities use strict Decimal parsing and exact rational conversions. Supported
unit definitions follow NIST SP 811 Appendix B; unknown units fail closed.
Qualification covers only the supplied records and declared provenance. It does
not infer missing lineage or certify an external dataset or deployment.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

FORMAT = "tinystudio.evaluation.v1"
_NUMERIC = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
HELDOUT = frozenset({"validation", "test", "heldout"})
PARTITIONS = frozenset({"train", *HELDOUT})

# Canonical = value * scale + offset. Temperature intervals have separate units.
_UNITS: dict[str, tuple[str, Fraction, Fraction]] = {}
for dimension, scales in {
    "dimensionless": {"": "1", "1": "1", "%": ".01"},
    "length": {"m": "1", "cm": ".01", "mm": ".001", "km": "1000"},
    "time": {"s": "1", "ms": ".001", "us": ".000001"},
    "mass": {"kg": "1", "g": ".001"},
    "pressure": {"Pa": "1", "kPa": "1000", "MPa": "1000000", "bar": "100000"},
    "voltage": {"V": "1", "mV": ".001"},
    "current": {"A": "1", "mA": ".001"},
    "frequency": {"Hz": "1", "kHz": "1000"},
    "energy": {"J": "1", "kJ": "1000"},
    "power": {"W": "1", "kW": "1000"},
    "temperature_interval": {"delta_K": "1", "delta_degC": "1"},
}.items():
    for symbol, scale in scales.items():
        _UNITS[symbol] = (dimension, Fraction(scale), Fraction(0))
for symbol in ("K", "degC", "°C", "degF", "°F"):
    scale = Fraction(5, 9) if symbol in {"degF", "°F"} else Fraction(1)
    offset = Fraction("273.15") - 32 * scale if symbol in {"degF", "°F"} else Fraction(0 if symbol == "K" else "273.15")
    _UNITS[symbol] = ("temperature", scale, offset)
_UNITS["delta_degF"] = ("temperature_interval", Fraction(5, 9), Fraction(0))


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("quantity must be a finite decimal, not a boolean or structured value")
    text = str(value)
    if len(text) > 1024 or not _NUMERIC.fullmatch(text):
        raise ValueError("invalid decimal syntax; signs, decimals and exponent syntax are not normalized")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal") from exc
    if not number.is_finite() or abs(number.adjusted()) > 10000:
        raise ValueError("quantity must be finite with bounded decimal exponent")
    return number


def _quantity(value: Any, unit: str | None = None) -> tuple[Fraction, str, str]:
    if isinstance(value, Mapping):
        if "value" not in value or "unit" not in value:
            raise ValueError("typed quantity needs value and unit")
        if unit is not None and unit != value["unit"]:
            raise ValueError("conflicting declared quantity units")
        unit = value["unit"]
        value = value["value"]
    symbol = "" if unit is None else unit
    if not isinstance(symbol, str) or symbol not in _UNITS:
        raise ValueError(f"unsupported unit {symbol!r}; provide an explicit supported unit")
    dimension, scale, offset = _UNITS[symbol]
    canonical = Fraction(_decimal(value)) * scale + offset
    if dimension == "temperature" and canonical < 0:
        raise ValueError("absolute temperature is below zero kelvin")
    return canonical, dimension, symbol


def _text(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = 50
        return str(Decimal(value.numerator) / Decimal(value.denominator))


def _check(check_id: str, passed: bool, code: str, **details: Any) -> dict:
    return {"id": check_id, "passed": bool(passed), "code": code, "details": details}


def _report(checks: list[dict], *, metrics: dict | None = None,
            research_overrides: Iterable[Mapping] = ()) -> dict:
    if not checks:
        checks = [_check("evidence", False, "missing_evidence")]
    overrides = list(research_overrides)
    failed = {check["id"] for check in checks if not check["passed"]}
    accepted = []
    for index, override in enumerate(overrides):
        valid = (isinstance(override, Mapping) and isinstance(override.get("check_id"), str) and override["check_id"] in failed
                 and isinstance(override.get("reason"), str) and bool(override["reason"].strip())
                 and isinstance(override.get("approved_by"), str) and bool(override["approved_by"].strip()))
        if valid:
            accepted.append(dict(override))
        else:
            check = _check(f"override:{index}", False, "invalid_research_override",
                           requirement="exact failed check_id, reason and approved_by are required")
            checks.append(check)
            failed.add(check["id"])
    waived = {override["check_id"] for override in accepted}
    blocking = sorted(failed - waived)
    status = "failed" if blocking else "research_only" if failed else "qualified"
    return {"format": FORMAT, "passed": not failed, "checks": checks,
            "qualification": {"status": status, "blocking_checks": blocking,
                              "research_overrides": accepted,
                              "scope": "supplied evidence and declared provenance only"},
            "metrics": metrics or {}}


def validate_numeric(actual: Any, expected: Any, *, actual_unit: str | None = None,
                     expected_unit: str | None = None, absolute_tolerance: Any = "0",
                     relative_tolerance: Any = "0") -> dict:
    """Compare quantities; absolute tolerance is in the expected unit's interval.

    Relative tolerance is asymmetric against abs(expected), measured in the
    expected unit. This avoids assigning a Kelvin offset to Celsius tolerance.
    Input spelling is retained in evidence; equality is mathematical (1.0 = 1).
    """
    checks = []
    try:
        left, left_dimension, _ = _quantity(actual, actual_unit)
        right, right_dimension, right_symbol = _quantity(expected, expected_unit)
        checks.append(_check("numeric:dimension", left_dimension == right_dimension,
                             "unit_dimension", actual=left_dimension, expected=right_dimension))
        atol, rtol = Fraction(_decimal(absolute_tolerance)), Fraction(_decimal(relative_tolerance))
        if atol < 0 or rtol < 0:
            raise ValueError("tolerances must be nonnegative")
        if left_dimension != right_dimension:
            return _report(checks)
        _, scale, offset = _UNITS[right_symbol]
        expected_native = (right - offset) / scale
        allowed = (atol + rtol * abs(expected_native)) * scale
        difference = abs(left - right)
        checks.append(_check("numeric:value", difference <= allowed, "numeric_tolerance",
                             actual=str(actual), expected=str(expected), canonical_actual=_text(left),
                             canonical_expected=_text(right), canonical_absolute_error=_text(difference),
                             canonical_allowed_error=_text(allowed), dimension=left_dimension))
        return _report(checks, metrics={"absolute_error": _text(difference), "dimension": left_dimension})
    except (ValueError, TypeError, OverflowError) as exc:
        checks.append(_check("numeric:input", False, "invalid_quantity", error=str(exc)))
        return _report(checks)


def _exact(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_exact(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_exact(a, b) for a, b in zip(left, right))
    return left == right


def _field(value: Any, path: str) -> tuple[bool, Any]:
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return False, None
        value = value[part]
    return True, value


def validate_packet(actual: Any, expected: Any, *, decoded: Mapping | None = None,
                    expected_decoded: Mapping | None = None,
                    required_fields: Iterable[str] = ()) -> dict:
    """Raw exact equality plus explicit decoded field checks, with no normalization.

    Decode with the real protocol parser before calling this function. A matching
    raw packet cannot substitute for missing required decoded fields.
    """
    supported = (bytes, bytearray, str, Mapping, list, tuple)
    checks = [_check("packet:input", isinstance(actual, supported) and isinstance(expected, supported), "packet_evidence_required"),
              _check("packet:exact", _exact(actual, expected), "packet_exact",
                     actual_type=type(actual).__name__, expected_type=type(expected).__name__)]
    effective = decoded if decoded is not None else actual if isinstance(actual, Mapping) else None
    truth = expected_decoded if expected_decoded is not None else expected if isinstance(expected, Mapping) else None
    paths = sorted(set(required_fields) | (set(truth) if truth is not None else set()))
    for path in paths:
        present, value = _field(effective, path)
        known, expected_value = _field(truth, path)
        checks.append(_check(f"packet:field:{path}", present and known and _exact(value, expected_value),
                             "decoded_field", field=path, actual_present=present, expected_present=known,
                             actual_type=type(value).__name__, expected_type=type(expected_value).__name__))
    return _report(checks)


def _references(value: Any, records: list[Mapping]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("stage provenance references must be a list")
    result = []
    for item in value:
        if isinstance(item, Mapping):
            item = item.get("source_id", item.get("record_id"))
        if not isinstance(item, str) or not item:
            raise ValueError("provenance references require nonempty stable source ids")
        if item in PARTITIONS:
            matched = [row["source_id"] for row in records if row.get("partition") == item]
            if not matched:
                raise ValueError(f"partition reference {item!r} has no retained source records")
            result.extend(matched)
        else:
            result.append(item)
    return result


def audit_leakage(records: Iterable[Mapping], *, stages: Iterable[Mapping] = (),
                  research_overrides: Iterable[Mapping] = ()) -> dict:
    """Audit declared split and stage lineage; unknown references fail closed.

    fit_on, corpus, reward_sources, scenario_sources and inputs contain stable
    source ids (or explicit partition names). Temporal inputs require available_at
    and inference_time in the same declared clock domain.
    """
    rows, stages = list(records), list(stages)
    checks = []
    invalid = [index for index, row in enumerate(rows) if not isinstance(row, Mapping)
               or not isinstance(row.get("source_id"), str) or not row["source_id"]
               or row.get("partition") not in PARTITIONS]
    checks.append(_check("provenance:records", bool(rows) and not invalid, "record_provenance", invalid_rows=invalid))
    if invalid:
        return _report(checks, research_overrides=research_overrides)
    by_source: dict[str, list[Mapping]] = defaultdict(list)
    for row in rows:
        by_source[row["source_id"]].append(row)
    for field in ("source_id", "group_id", "episode_id", "device_id", "scenario_id"):
        groups: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            if row.get(field) is not None:
                groups[str(row[field])].add(row["partition"])
        collisions = {key: sorted(partitions) for key, partitions in sorted(groups.items()) if len(partitions) > 1}
        checks.append(_check(f"split:{field}", not collisions, "cross_partition_identity", field=field, collisions=collisions, observed_groups=len(groups), records_with_identity=sum(row.get(field) is not None for row in rows)))
    seen_stages = set()
    for index, stage in enumerate(stages):
        if not isinstance(stage, Mapping):
            checks.append(_check(f"stage:{index}:shape", False, "invalid_stage_provenance"))
            continue
        stage_id = str(stage.get("id", index))
        prefix = f"stage:{stage_id}"
        checks.append(_check(f"{prefix}:identity", stage_id not in seen_stages, "unique_stage_identity"))
        seen_stages.add(stage_id)
        kinds = {"preprocess": "fit_on", "retrieval": "corpus", "reward": "reward_sources", "scenario_selection": "scenario_sources", "inference": "inputs"}
        kind = stage.get("kind")
        known_kind = isinstance(kind, str) and kind in kinds
        checks.append(_check(f"{prefix}:kind", known_kind, "supported_stage_kind", kind=kind))
        required = kinds.get(kind) if known_kind else None
        if required:
            checks.append(_check(f"{prefix}:declared", required in stage, "required_stage_lineage", field=required))
        try:
            for key in ("fit_on", "corpus", "reward_sources", "scenario_sources", "inputs"):
                refs = _references(stage.get(key), rows)
                unknown = sorted(set(refs) - by_source.keys())
                checks.append(_check(f"{prefix}:{key}:known", not unknown, "unknown_source_reference", references=refs, unknown=unknown))
                if key in {"fit_on", "corpus", "reward_sources", "scenario_sources"}:
                    leaked = sorted({source for source in refs for row in by_source.get(source, []) if row["partition"] in HELDOUT})
                    checks.append(_check(f"{prefix}:{key}:heldout", not leaked, "heldout_stage_leakage", stage_kind=stage.get("kind"), purpose=key, source_ids=leaked))
                if (key == "inputs" and stage.get("kind") == "inference") or "inference_time" in stage:
                    for source in sorted(set(refs) & by_source.keys()):
                        for source_index, row in enumerate(by_source[source]):
                            temporal_id = f"{prefix}:{key}:availability:{source}:{source_index}"
                            try:
                                if "available_at" not in row or "inference_time" not in stage:
                                    raise ValueError("available_at and inference_time are required")
                                if row.get("clock_domain") != stage.get("clock_domain"):
                                    raise ValueError("input and inference clock domains must match")
                                available, inference = _decimal(row["available_at"]), _decimal(stage["inference_time"])
                                checks.append(_check(temporal_id, available <= inference, "future_information", source_id=source, available_at=str(available), inference_time=str(inference)))
                            except ValueError as exc:
                                checks.append(_check(temporal_id, False, "invalid_temporal_provenance", source_id=source, error=str(exc)))
        except (ValueError, TypeError) as exc:
            checks.append(_check(f"{prefix}:provenance", False, "invalid_stage_provenance", error=str(exc)))
    digest = hashlib.sha256(json.dumps({"records": rows, "stages": stages}, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    return _report(checks, metrics={"records": len(rows), "stages": len(stages), "provenance_sha256": digest}, research_overrides=research_overrides)


def compare_baselines(candidate: Iterable[Mapping], baseline: Iterable[Mapping]) -> dict:
    """Compare matched held-out numeric predictions, including episode-level MAE.

    Rows require source_id, episode_id, partition, prediction, expected and may
    carry unit. Typed {value,unit} quantities are accepted. A target mismatch,
    duplicate/unmatched source, or mixed physical dimensions fails qualification.
    """
    left, right = list(candidate), list(baseline)
    checks = []
    def indexed(rows, label):
        result = {}
        for index, row in enumerate(rows):
            valid = (isinstance(row, Mapping) and isinstance(row.get("source_id"), str) and row["source_id"]
                     and isinstance(row.get("episode_id"), str) and row["episode_id"] and row.get("partition") in HELDOUT)
            checks.append(_check(f"{label}:row:{index}", bool(valid), "heldout_pair_identity"))
            if not valid:
                continue
            key = (row["source_id"], row["episode_id"])
            checks.append(_check(f"{label}:unique:{index}", key not in result, "duplicate_paired_source", source_id=key[0], episode_id=key[1]))
            result[key] = row
        return result
    a, b = indexed(left, "candidate"), indexed(right, "baseline")
    checks.append(_check("comparison:matched", bool(a) and a.keys() == b.keys(), "matched_heldout_examples",
                         candidate_only=sorted(a.keys() - b.keys()), baseline_only=sorted(b.keys() - a.keys())))
    paired = []
    dimensions = set()
    for source, episode in sorted(a.keys() & b.keys()):
        item, reference = a[(source, episode)], b[(source, episode)]
        pair_id = f"pair:{source}:{episode}"
        try:
            prediction, dimension, _ = _quantity(item["prediction"], item.get("unit"))
            target, target_dimension, _ = _quantity(item["expected"], item.get("unit"))
            baseline_prediction, baseline_dimension, _ = _quantity(reference["prediction"], reference.get("unit"))
            baseline_target, baseline_target_dimension, _ = _quantity(reference["expected"], reference.get("unit"))
            valid = (dimension == target_dimension == baseline_dimension == baseline_target_dimension
                     and target == baseline_target and item["partition"] == reference["partition"])
            checks.append(_check(pair_id, valid, "matched_target_and_dimension", source_id=source, episode_id=episode))
            if valid:
                dimensions.add(dimension)
                paired.append((source, episode, abs(prediction - target), abs(baseline_prediction - target)))
        except (KeyError, ValueError, TypeError) as exc:
            checks.append(_check(pair_id, False, "invalid_comparison_quantity", error=str(exc)))
    checks.append(_check("comparison:dimension", len(dimensions) <= 1, "aggregate_dimension", dimensions=sorted(dimensions)))
    def summary(values):
        candidate_mean = sum((item[2] for item in values), Fraction(0)) / len(values)
        baseline_mean = sum((item[3] for item in values), Fraction(0)) / len(values)
        return {"examples": len(values), "candidate_mae": _text(candidate_mean), "baseline_mae": _text(baseline_mean),
                "candidate_minus_baseline_mae": _text(candidate_mean - baseline_mean)}
    metrics: dict = {"metric": "mean_absolute_error", "units": "canonical dimension units", "dimension": next(iter(dimensions)) if len(dimensions) == 1 else None}
    # Never emit a seemingly comparable aggregate for incomplete or invalid pairs.
    if all(check["passed"] for check in checks) and paired:
        metrics["aggregate"] = summary(paired)
        episodes = {episode: [item for item in paired if item[1] == episode] for episode in sorted({item[1] for item in paired})}
        metrics["per_episode"] = [{"episode_id": episode, **summary(values)} for episode, values in episodes.items()]
        metrics["per_example"] = [{"source_id": source, "episode_id": episode, "candidate_absolute_error": _text(error), "baseline_absolute_error": _text(baseline_error)} for source, episode, error, baseline_error in paired]
        candidate_macro = sum((sum((item[2] for item in values), Fraction(0)) / len(values) for values in episodes.values()), Fraction(0)) / len(episodes)
        baseline_macro = sum((sum((item[3] for item in values), Fraction(0)) / len(values) for values in episodes.values()), Fraction(0)) / len(episodes)
        metrics["episode_macro_candidate_mae"] = _text(candidate_macro)
        metrics["episode_macro_baseline_mae"] = _text(baseline_macro)
        metrics["episode_macro_candidate_minus_baseline_mae"] = _text(candidate_macro - baseline_macro)
    return _report(checks, metrics=metrics)


def evaluate_examples(examples: Iterable[Mapping], *, provenance: Iterable[Mapping] = (),
                      stages: Iterable[Mapping] = (), research_overrides: Iterable[Mapping] = ()) -> dict:
    """Compose validators without replacing detailed failures with an accuracy score."""
    results, checks = [], []
    for index, row in enumerate(examples):
        if not isinstance(row, Mapping):
            checks.append(_check(f"example:{index}:shape", False, "invalid_example"))
            continue
        validator = row.get("validator", "numeric")
        options = row.get("options", {})
        try:
            if validator == "numeric":
                result = validate_numeric(row.get("actual"), row.get("expected"), **options)
            elif validator == "packet":
                result = validate_packet(row.get("actual"), row.get("expected"), **options)
            else:
                result = _report([_check("validator", False, "unsupported_validator", validator=validator)])
        except (TypeError, ValueError) as exc:
            result = _report([_check("validator", False, "invalid_validator_options", error=str(exc))])
        results.append({"source_id": row.get("source_id"), **result})
        checks.extend({**check, "id": f"example:{index}:{check['id']}"} for check in result["checks"])
    checks.append(_check("examples:present", bool(results), "evaluation_examples_required"))
    audit = audit_leakage(provenance, stages=stages)
    checks.extend({**check, "id": f"audit:{check['id']}"} for check in audit["checks"])
    report = _report(checks, metrics={"examples": len(results), "passed_examples": sum(result["passed"] for result in results)}, research_overrides=research_overrides)
    report["examples"] = results
    report["provenance"] = audit
    return report
