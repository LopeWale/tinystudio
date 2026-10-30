"""Matched held-out method execution over immutable shared cases.

Each model arm delegates to the existing frozen generation executor. Affine
recipes consume only authored scalar coordinates; they never fit coefficients,
read answers during prediction, or infer coordinate units.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

from .contracts import canonical
from .engine import ExecutionCancelled, digest
from .evaluation import compare_baselines, evaluate_examples
from .evaluation_generation import (
    _cases, execute_generation_evaluation, strict_scalar,
    validate_features, validate_generation_selection,
)

FORMAT = "tinystudio.method-matrix.v1"
RECIPE = "tinystudio.affine.v1"
_METHOD_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")


def _project_definition(definition, method, bindings):
    projected = deepcopy(definition)
    roles = {row["role"]: row for row in bindings.values()}
    ids = [method["artifact_id"], roles["cases"]["artifact_id"]]
    if method["retrieval"]:
        ids.extend(roles[role]["artifact_id"] for role in ("retrieval_index", "embedding_model") if role in roles)
    projected["inputs"] = [deepcopy(bindings[identity]) for identity in ids]
    params = definition["parameters"]
    projected["parameters"] = {"operation": "generate", "compare_retrieval": False,
        "max_new_tokens": params.get("max_new_tokens", 16), "device": params.get("device", "cpu")}
    if not method["retrieval"]:
        projected.pop("retrieval", None)
    return projected


def validate_matrix_selection(definition):
    """Pure admission checks; contents and pinned hashes are checked at execution."""
    params = definition.get("parameters", {})
    if set(params) - {"operation", "methods", "baseline_method", "max_new_tokens", "device"}:
        raise ValueError("unsupported method matrix parameters")
    if params.get("operation") != "matrix":
        raise ValueError("matrix operation is required")
    if definition.get("learning") or definition.get("pipeline") or definition.get("rewards"):
        raise ValueError("method matrices do not train, transform cases or optimize rewards")
    maximum, device = params.get("max_new_tokens", 16), params.get("device", "cpu")
    if type(maximum) is not int or not 1 <= maximum <= 512:
        raise ValueError("max_new_tokens must be an integer between 1 and 512")
    if not isinstance(device, str) or device not in {"cpu", "mps", "cuda"} and not (
            device.startswith("cuda:") and device[5:].isdigit()):
        raise ValueError("generation requires an explicit cpu, mps or cuda device")
    methods = params.get("methods")
    if not isinstance(methods, list) or not 2 <= len(methods) <= 16:
        raise ValueError("matrix requires 2..16 methods")
    identities, model_ids = set(), set()
    for method in methods:
        if not isinstance(method, dict):
            raise ValueError("matrix methods must be objects")
        identity, label, kind = method.get("id"), method.get("label"), method.get("kind")
        if not isinstance(identity, str) or not _METHOD_ID.fullmatch(identity) or identity in identities:
            raise ValueError("method IDs must be unique portable lowercase identifiers of at most 48 characters")
        identities.add(identity)
        if not isinstance(label, str) or not label.strip() or len(label) > 120:
            raise ValueError("method labels must be nonempty and at most 120 characters")
        if kind == "model":
            if set(method) != {"id", "label", "kind", "artifact_id", "retrieval"} or type(method["retrieval"]) is not bool:
                raise ValueError("model methods require artifact_id and an explicit retrieval boolean")
            if not isinstance(method["artifact_id"], str) or not method["artifact_id"]:
                raise ValueError("model method requires an artifact_id")
            model_ids.add(method["artifact_id"])
        elif kind == "affine":
            if set(method) != {"id", "label", "kind", "coefficients", "intercept"}:
                raise ValueError("affine methods require only coefficients and intercept")
            validate_features(method["coefficients"])
            strict_scalar(method["intercept"])
        else:
            raise ValueError("method kind must be model or affine")
    if not isinstance(params.get("baseline_method"), str) or params["baseline_method"] not in identities:
        raise ValueError("baseline_method must name an existing method")
    bindings, roles = {}, {}
    for binding in definition.get("inputs", []):
        identity, role = binding.get("artifact_id"), binding.get("role")
        if not isinstance(identity, str) or not identity or identity in bindings:
            raise ValueError("matrix bindings require unique artifact IDs")
        if role not in {"model", "checkpoint", "cases", "retrieval_index", "embedding_model"}:
            raise ValueError("unsupported matrix input role")
        if role not in {"model", "checkpoint"} and role in roles:
            raise ValueError("matrix requires unique cases, retrieval_index and embedding_model roles")
        visibility = binding.get("visibility", "model")
        if visibility not in ({"evaluation", "truth"} if role == "cases" else {"model"}):
            raise ValueError("cases require evaluation/truth visibility; all other matrix inputs require model visibility")
        bindings[identity], roles[role] = binding, binding
    if "cases" not in roles:
        raise ValueError("matrix requires exactly one shared cases artifact")
    bound_models = {identity for identity, row in bindings.items() if row["role"] in {"model", "checkpoint"}}
    if model_ids != bound_models:
        raise ValueError("matrix model references must match model/checkpoint bindings with no unused bindings")
    retrieval_used = any(method.get("retrieval", False) for method in methods)
    if not retrieval_used and (definition.get("retrieval") or "retrieval_index" in roles or "embedding_model" in roles):
        raise ValueError("retrieval inputs/configuration require a retrieval-enabled model method")
    for method in methods:
        if method["kind"] == "model":
            if method["retrieval"] and "retrieval_index" not in roles:
                raise ValueError("retrieval-enabled method requires a frozen retrieval_index")
            validate_generation_selection(_project_definition(definition, method, bindings))
    return {"methods": deepcopy(methods), "baseline_method": params["baseline_method"],
            "bindings": bindings, "roles": roles, "requires_model": bool(model_ids)}


def _exact_decimal(value):
    """Serialize a terminating Fraction without a Decimal context rounding it."""
    denominator, twos, fives = value.denominator, 0, 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ValueError("affine decimal arithmetic must terminate")
    places = max(twos, fives)
    scaled = value.numerator * 2 ** (places - twos) * 5 ** (places - fives)
    sign = "-" if scaled < 0 else ""
    digits = str(abs(scaled)).rjust(places + 1, "0")
    return sign + (digits[:-places] + "." + digits[-places:] if places else digits)


def _affine_prediction(features, method):
    # This signature deliberately excludes the case's target and evaluator fields.
    coefficients = method["coefficients"]
    if set(coefficients) != set(features):
        raise ValueError("affine coefficients must match the exact case feature key set")
    result = Fraction(strict_scalar(method["intercept"]))
    for key in sorted(features):
        result += Fraction(strict_scalar(features[key])) * Fraction(strict_scalar(coefficients[key]))
    return _exact_decimal(result)


def _expected_display(expected):
    """Carry exact target text across JSON clients without changing scored data."""
    if isinstance(expected, dict):
        return {"value": str(expected["value"]), "unit": expected["unit"]}
    return str(expected)


def execute_method_matrix(spec, output_dir, emit, cancelled):
    selection = validate_matrix_selection(spec["definition"])
    def checkpoint():
        if cancelled():
            raise ExecutionCancelled("method matrix cancelled")
    checkpoint()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    case_binding = selection["roles"]["cases"]
    cases = _cases(spec, case_binding)
    has_affine = any(method["kind"] == "affine" for method in selection["methods"])
    for case in cases:
        expected = case["expected"]
        expected = expected.get("value") if isinstance(expected, dict) else expected
        if isinstance(expected, float):
            raise ValueError("matrix expected values require exact integers or decimal strings, never JSON floats")
        if has_affine:
            validate_features(case.get("features"))
            for method in selection["methods"]:
                if method["kind"] == "affine" and set(method["coefficients"]) != set(case["features"]):
                    raise ValueError("affine coefficients must match the exact case feature key set")
    cases_receipt = {key: case_binding[key] for key in ("artifact_id", "sha256")}
    identities = [{key: row[key] for key in ("source_id", "episode_id", "partition")} for row in cases]
    case_hashes = { (row["source_id"], row["episode_id"]): hashlib.sha256(canonical(row)).hexdigest() for row in cases }
    expected_cases = {(row["source_id"], row["episode_id"]): row for row in cases}
    methods, records, artifacts, predictions, research_only = [], [], [], {}, False
    for method in selection["methods"]:
        checkpoint()
        identity = method["id"]
        receipt = {key: method[key] for key in ("id", "label", "kind")}
        method_records, method_artifacts = [], []
        if method["kind"] == "model":
            definition = _project_definition(spec["definition"], method, selection["bindings"])
            child_spec = {**deepcopy(spec), "definition": definition,
                "input_paths": {row["artifact_id"]: deepcopy(spec["input_paths"][row["artifact_id"]]) for row in definition["inputs"]}}
            child_spec.pop("evaluation_input_paths", None)
            def child_emit(event):
                emit({**event, "method_id": identity, "payload": {**event.get("payload", {}), "method_id": identity}})
            child = execute_generation_evaluation(child_spec, output_dir / identity, child_emit, cancelled)
            checkpoint()
            method_records = child["records"]
            research_only |= child["qualification"]["research_only"]
            for ordinal, artifact in enumerate(child["artifacts"]):
                renamed = {**artifact, "name": f"method-{identity}-{ordinal}-{Path(artifact['name']).name}",
                    "role": "method_evaluation" if artifact["role"] == "independent_evaluation" else artifact["role"]}
                method_artifacts.append(renamed)
                if artifact["role"] == "independent_evaluation":
                    receipt["metadata"] = json.loads(Path(artifact["path"]).read_bytes())["model"]
            receipt["model"] = {key: selection["bindings"][method["artifact_id"]][key] for key in ("artifact_id", "sha256")}
            receipt["retrieval"] = method["retrieval"]
        else:
            recipe = {"format": RECIPE, "coefficients": deepcopy(method["coefficients"]),
                "intercept": method["intercept"], "fitting": "none", "unit_inference": "none",
                "formula": "intercept + sum(coefficients[name] * features[name])",
                "coordinates": "raw authored scalar features; output uses the case declared unit"}
            receipt["metadata"] = recipe
            for case in cases:
                checkpoint()
                prediction = _affine_prediction(case["features"], method)
                method_records.append({**{key: case[key] for key in ("source_id", "episode_id", "partition", "expected", "unit")},
                    "prediction": prediction, "cases": cases_receipt,
                    "output": {"completion": prediction, "recipe": recipe, "features": deepcopy(case["features"])}})
                emit({"kind": "evaluation_prediction", "method_id": identity,
                    "payload": {"method_id": identity, "arm": identity, "source_id": case["source_id"], "episode_id": case["episode_id"]}})
                checkpoint()
        checkpoint()
        observed = set()
        for row in method_records:
            case_id = (row.get("source_id"), row.get("episode_id"))
            case = expected_cases.get(case_id)
            if (case is None or case_id in observed or
                    any(row.get(key) != case[key] for key in ("partition", "expected", "unit")) or
                    row.get("cases") != cases_receipt):
                raise ValueError("method output must preserve unique shared case identities, targets, units and artifact hashes")
            observed.add(case_id)
            row.update(method_id=identity, arm=identity,
                case_sha256=case_hashes[case_id], expected_display=_expected_display(case["expected"]))
        if observed != expected_cases.keys():
            raise ValueError("method output does not cover the exact shared case identity set")
        predictions[identity] = method_records
        records.extend(method_records)
        receipt["artifacts"] = [{key: artifact[key] for key in ("name", "sha256", "role")} for artifact in method_artifacts]
        methods.append(receipt)
        artifacts.extend(method_artifacts)
    checkpoint()
    evaluations = {identity: evaluate_examples([{**row, "actual": row["prediction"], "options": {
        "actual_unit": row["unit"], "expected_unit": row["unit"]}} for row in rows], provenance=identities)
        for identity, rows in predictions.items()}
    # evaluate_examples returns one result per input mapping in input order but
    # retains only source_id. Preserve the complete case join in this report;
    # paired comparability does not establish an individual prediction's accuracy.
    for identity, evaluation in evaluations.items():
        rows = predictions[identity]
        if len(evaluation["examples"]) != len(rows):
            raise ValueError("method evaluation lost case result coverage")
        for result, row in zip(evaluation["examples"], rows):
            if result["source_id"] != row["source_id"]:
                raise ValueError("method evaluation changed case result ordering")
            result["episode_id"] = row["episode_id"]
    baseline = selection["baseline_method"]
    comparisons = {identity: compare_baselines(rows, predictions[baseline]) for identity, rows in predictions.items() if identity != baseline}
    expected_coverage = len(cases) * len(methods)
    coverage_complete = all(
        len(rows) == len(expected_cases) and
        {(row["source_id"], row["episode_id"]) for row in rows} == expected_cases.keys() and
        all(row["partition"] == expected_cases[(row["source_id"], row["episode_id"])]["partition"] and
            row["case_sha256"] == case_hashes[(row["source_id"], row["episode_id"])] for row in rows)
        for rows in predictions.values())
    qualification = {"execution": "completed", "coverage_complete": coverage_complete, "research_only": research_only,
        "training_overlap": "not_established", "neural_superiority_required": False,
        "evaluation_passed": all(report["passed"] for report in evaluations.values()),
        "comparison_passed": all(report["passed"] for report in comparisons.values())}
    report = {"format": FORMAT, "methods": methods, "baseline_method": baseline,
        "cases": cases_receipt, "case_count": len(cases), "method_count": len(methods),
        "expected_coverage": expected_coverage, "records": records, "evaluations": evaluations,
        "comparisons": comparisons, "qualification": qualification}
    checkpoint()
    target = output_dir / "method-matrix.json"
    target.write_bytes(canonical(report))
    artifact_hash = digest(target)
    try:
        checkpoint()
    except ExecutionCancelled:
        target.unlink(missing_ok=True)
        raise
    # Publication of the aggregate is last. Cancellation after the final model
    # completion or affine event is checked before any aggregate is returned.
    artifacts.append({"name": target.name, "path": str(target), "sha256": artifact_hash,
        "media_type": "application/json", "role": "independent_evaluation"})
    summary = {key: report[key] for key in ("format", "baseline_method", "cases", "case_count",
        "method_count", "expected_coverage", "qualification")}
    return {"records": records, "metrics": {"cases": len(cases), "methods": len(methods),
        "predictions": len(records), "expected_coverage": expected_coverage},
        "baseline_comparison": summary, "artifacts": artifacts, "qualification": qualification}
