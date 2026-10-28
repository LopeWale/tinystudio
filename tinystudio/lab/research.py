"""Executable independent evaluation and paired comparisons over pinned evidence."""
import json
from pathlib import Path

from .contracts import canonical
from .engine import ExecutionCancelled, digest
from .evaluation import audit_leakage, compare_baselines, evaluate_examples


def execute_evaluation(spec, output_dir, emit, cancelled):
    definition = spec["definition"]
    parameters = definition.get("parameters", {})
    inputs = {}
    for ref in definition.get("inputs", []):
        if cancelled():
            raise ExecutionCancelled("evaluation cancelled")
        raw = spec["input_paths"][ref["artifact_id"]]
        path = Path(raw["path"] if isinstance(raw, dict) else raw)
        if path.stat().st_size > 64 * 1024**2 or digest(path) != ref["sha256"]:
            raise ValueError("evaluation source missing, altered or exceeds 64 MiB")
        content = path.read_text()
        try:
            rows = json.loads(content)
        except json.JSONDecodeError:
            rows = [json.loads(line) for line in content.splitlines() if line.strip()]
        if isinstance(rows, dict):
            rows = rows.get("records", [rows])
        if not isinstance(rows, list) or len(rows) > 100000:
            raise ValueError("evaluation input must contain at most 100000 records")
        role = ref.get("role", "examples")
        if role in inputs:
            raise ValueError("evaluation requires unique input roles")
        inputs[role] = rows
    provenance = inputs.get("provenance", parameters.get("provenance", []))
    stages = parameters.get("stages", [])
    overrides = definition.get("research_overrides", [])
    report = evaluate_examples(inputs.get("examples", parameters.get("examples", [])), provenance=provenance,
                               stages=stages, research_overrides=overrides)
    comparison = None
    if "candidate" in inputs or "baseline" in inputs or "candidate" in parameters:
        comparison = compare_baselines(inputs.get("candidate", parameters.get("candidate", [])),
                                       inputs.get("baseline", parameters.get("baseline", [])))
    audit = audit_leakage(provenance, stages=stages, research_overrides=overrides)
    report["comparison"], report["lineage"] = comparison, audit
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "evaluation.json"
    target.write_bytes(canonical(report))
    emit({"kind": "evaluation_complete", "payload": {"checks": len(report["checks"]), "passed": report["passed"]}})
    return {"records": report.get("examples", []), "metrics": report["metrics"],
            "baseline_comparison": comparison,
            "artifacts": [{"name": target.name, "path": str(target), "sha256": digest(target),
                           "role": "independent_evaluation", "media_type": "application/json"}],
            "qualification": {"execution": "completed", "evaluation_passed": report["passed"],
                              "comparison_passed": comparison["passed"] if comparison else None,
                              "leakage_passed": audit["passed"], "neural_superiority_required": False}}
