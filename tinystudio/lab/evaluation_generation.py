"""Frozen model/retrieval inference with answer-isolated, paired numeric evaluation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import canonical
from .engine import ExecutionCancelled, digest
from .evaluation import HELDOUT, _quantity, compare_baselines, evaluate_examples


def validate_generation_selection(definition):
    """Validate authored choices without importing model dependencies or opening files."""
    params = definition.get("parameters", {})
    operation = params.get("operation", "precomputed")
    if operation == "precomputed":
        if definition.get("retrieval") or any(item.get("role") in {
                "model", "checkpoint", "cases", "retrieval_index", "embedding_model"} for item in definition.get("inputs", [])):
            raise ValueError("model, cases and retrieval bindings require operation=generate")
        return None
    if operation != "generate":
        raise ValueError("evaluation operation must be precomputed or generate")
    if set(params) - {"operation", "max_new_tokens", "device", "compare_retrieval"}:
        raise ValueError("unsupported generation evaluation parameters")
    maximum = params.get("max_new_tokens", 16)
    if type(maximum) is not int or not 1 <= maximum <= 512:
        raise ValueError("max_new_tokens must be an integer between 1 and 512")
    compare = params.get("compare_retrieval", False)
    if type(compare) is not bool:
        raise ValueError("compare_retrieval must be a boolean")
    device = params.get("device", "cpu")
    if not isinstance(device, str) or device not in {"cpu", "mps", "cuda"} and not (
            device.startswith("cuda:") and device[5:].isdigit()):
        raise ValueError("generation requires an explicit cpu, mps or cuda device")
    if definition.get("learning") or definition.get("pipeline") or definition.get("rewards"):
        raise ValueError("generation evaluation does not train, transform cases or optimize rewards")
    inputs = definition.get("inputs", [])
    roles = {}
    for item in inputs:
        role = item.get("role")
        if role not in {"model", "checkpoint", "cases", "retrieval_index", "embedding_model"} or role in roles:
            raise ValueError("generation evaluation requires unique supported input roles")
        visibility = item.get("visibility", "model")
        if visibility not in ({"evaluation", "truth"} if role == "cases" else {"model"}):
            raise ValueError("cases require evaluation/truth visibility; models and indexes require model visibility")
        roles[role] = item
    if len(set(roles) & {"model", "checkpoint"}) != 1 or "cases" not in roles:
        raise ValueError("generation requires one model or checkpoint and one cases artifact")
    retrieval = definition.get("retrieval") or {}
    if set(retrieval) - {"top_k", "filters", "fields"}:
        raise ValueError("generation retrieval accepts top_k, filters and explicit content fields; mode comes from the frozen index")
    if "retrieval_index" not in roles and (retrieval or compare or "embedding_model" in roles):
        raise ValueError("retrieval configuration/comparison requires a retained retrieval_index")
    top_k = retrieval.get("top_k", 5)
    fields = retrieval.get("fields", ["text"])
    filters = retrieval.get("filters", {})
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise ValueError("retrieval top_k must be between 1 and 100")
    if not isinstance(filters, dict) or any(not isinstance(key, str) for key in filters):
        raise ValueError("retrieval filters must be an object with string keys")
    if (not isinstance(fields, list) or not fields or len(fields) > 32 or
            any(not isinstance(field, str) or not field for field in fields) or len(set(fields)) != len(fields)):
        raise ValueError("retrieval fields must be unique nonempty content field names")
    return {"max_new_tokens": maximum, "device": device, "compare": compare, "roles": roles,
            "top_k": top_k, "filters": filters, "fields": fields}


def _cases(spec, binding):
    raw = spec.get("input_paths", {}).get(binding["artifact_id"])
    path = Path(raw["path"] if isinstance(raw, dict) else raw) if raw else None
    if path is None or path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024**2:
        raise ValueError("cases require a resolved regular artifact of at most 64 MiB")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != binding["sha256"]:
        raise ValueError("cases artifact checksum mismatch")
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {"format", "cases"} or payload["format"] != "tinystudio.evaluation-cases.v1":
        raise ValueError("cases require the tinystudio.evaluation-cases.v1 envelope")
    rows, identities = payload["cases"], set()
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("evaluation requires 1..1000 cases")
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"source_id", "episode_id", "partition", "prompt", "query", "expected", "unit"}:
            raise ValueError("unsupported evaluation case fields")
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("source_id", "episode_id", "prompt")):
            raise ValueError("cases require nonempty source_id, episode_id and prompt")
        if row.get("partition") not in HELDOUT or len(row["prompt"]) > 65536:
            raise ValueError("cases require held-out partitions and bounded prompts")
        if "query" in row and (not isinstance(row["query"], str) or not row["query"].strip() or len(row["query"]) > 65536):
            raise ValueError("case query must be a nonempty bounded string")
        identity = (row["source_id"], row["episode_id"])
        if identity in identities:
            raise ValueError("duplicate held-out case identity")
        identities.add(identity)
        # Use the validator's effective symbol for both predictions and targets,
        # including typed quantities with omitted or explicitly null outer units.
        row["unit"] = _quantity(row.get("expected"), row.get("unit"))[2]
    return rows


def _declared_overlap(value, sources, episodes):
    """Inspect named provenance envelopes, not arbitrary nested domain objects."""
    if not isinstance(value, dict):
        return False
    for key in ("source_id", "source_record_id"):
        if isinstance(value.get(key), str) and value[key] in sources:
            return True
    if isinstance(value.get("episode_id"), str) and value["episode_id"] in episodes:
        return True
    if isinstance(value.get("source_record_ids"), list) and any(
            isinstance(identity, str) and identity in sources for identity in value["source_record_ids"]):
        return True
    for key in ("metadata", "provenance", "binding", "resolved_source", "payload", "record", "raw_row"):
        nested = value.get(key)
        rows = nested if isinstance(nested, list) else [nested]
        if any(_declared_overlap(row, sources, episodes) for row in rows):
            return True
    return False


def _render(prompt, hits, fields):
    evidence = []
    for hit in hits:
        content = hit["content"]
        if any(field not in content for field in fields):
            raise ValueError("retrieved document is missing an explicitly selected content field")
        evidence.append(canonical({field: content[field] for field in fields}).decode())
    return "Evidence:\n" + "\n".join(evidence) + "\n\nQuestion:\n" + prompt + "\nAnswer:\n"


def execute_generation_evaluation(spec, output_dir, emit, cancelled):
    selection = validate_generation_selection(spec["definition"])
    if selection is None:
        raise ValueError("generation operation is required")
    from .compatibility import resolve_role
    from .model_session import ModelSession
    from .retrieval import execute_retrieval

    if cancelled():
        raise ExecutionCancelled("evaluation cancelled")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    roles = selection["roles"]
    cases = _cases(spec, roles["cases"])
    model = resolve_role(spec, "model" if "model" in roles else "checkpoint")
    retrieved, traces, overlap = None, [], []
    if "retrieval_index" in roles:
        refs = [roles[key] for key in ("retrieval_index", "embedding_model") if key in roles]
        retrieval_spec = {"definition": {"inputs": refs, "parameters": {"operation": "query",
            "queries": [row.get("query", row["prompt"]) for row in cases], "top_k": selection["top_k"],
            "filters": selection["filters"]}}, "input_paths": {
                ref["artifact_id"]: spec["input_paths"][ref["artifact_id"]] for ref in refs}}
        retrieved = execute_retrieval(retrieval_spec, output_dir / "retrieval", emit, cancelled)
        traces = retrieved["records"]
        if len(traces) != len(cases) or cancelled():
            raise ExecutionCancelled("evaluation retrieval cancelled")
        # Audit all indexed identities, not only successful hits. Missing lineage is
        # reported separately; freezing a corpus does not establish its independence.
        index = json.loads(Path(retrieved["artifacts"][0]["path"]).read_bytes())
        sources, episodes = {row["source_id"] for row in cases}, {row["episode_id"] for row in cases}
        for document in index["documents"]:
            if _declared_overlap({"record": document["content"], "provenance": document["provenance"]}, sources, episodes):
                overlap.append(document["id"])
    records, arms = [], {"retrieval": [], "without_retrieval": []}
    with ModelSession(Path(model["path"]), model["sha256"], output_dir / "model-work", device=selection["device"]) as session:
        metadata = session.metadata
        for index, case in enumerate(cases):
            trace = traces[index] if retrieved else None
            variants = ["without_retrieval", "retrieval"] if selection["compare"] else ["retrieval" if retrieved else "without_retrieval"]
            for arm in variants:
                if cancelled():
                    raise ExecutionCancelled("evaluation generation cancelled")
                hits = trace["hits"] if arm == "retrieval" else []
                # The model receives only this projection; case answers and evaluator
                # metadata never cross the session boundary.
                prompt = _render(case["prompt"], hits, selection["fields"])
                output = session.generate(prompt, selection["max_new_tokens"], cancelled=cancelled)
                prediction = {key: case[key] for key in ("source_id", "episode_id", "partition", "expected")}
                prediction["unit"] = case["unit"]
                prediction["prediction"] = output["completion"]  # Strict decimal parsing; never extract a convenient number.
                arms[arm].append(prediction)
                records.append({**prediction, "arm": arm, "output": output,
                    "model": {key: model[key] for key in ("artifact_id", "sha256")},
                    "cases": {key: roles["cases"][key] for key in ("artifact_id", "sha256")},
                    "retrieval": {"query": trace["query"], "index": trace["trace"]["origin_index"],
                        "retrieved_ids": trace["trace"]["selected_ids"], "included_ids": [hit["id"] for hit in hits],
                        "fields": selection["fields"]} if trace else None,
                    "formatting": "tinystudio.evidence-question.v1"})
                emit({"kind": "evaluation_prediction", "payload": {"source_id": case["source_id"], "episode_id": case["episode_id"], "arm": arm}})
    reports = {arm: evaluate_examples([{**row, "actual": row["prediction"], "options": {
        "actual_unit": row.get("unit"), "expected_unit": row.get("unit")}} for row in predictions],
        provenance=[{key: row[key] for key in ("source_id", "episode_id", "partition")} for row in cases])
        for arm, predictions in arms.items() if predictions}
    comparison = compare_baselines(arms["retrieval"], arms["without_retrieval"]) if selection["compare"] else None
    research_only = bool(overlap or (retrieved and retrieved["qualification"]["research_only"]))
    report = {"format": "tinystudio.generation-evaluation.v1", "model": metadata,
        "reports": reports, "comparison": comparison, "records": records,
        "lineage": {"overlapping_retrieval_ids": overlap, "research_only": research_only,
            "scope": "Recorded retrieval restrictions and declared case/source/episode identities; undeclared lineage and model training overlap are not established."},
        "scoring": "Strict entire-completion decimal in the case's declared unit; invalid outputs remain failed cases."}
    target = output_dir / "generation-evaluation.json"
    target.write_bytes(canonical(report))
    return {"records": records, "metrics": {"cases": len(cases), "predictions": len(records)},
        "baseline_comparison": comparison,
        "artifacts": [*(retrieved["artifacts"] if retrieved else []), {"name": target.name, "path": str(target),
            "sha256": digest(target), "media_type": "application/json", "role": "independent_evaluation"}],
        "qualification": {"execution": "completed", "research_only": research_only,
            "evaluation_passed": all(report["passed"] for report in reports.values()),
            "comparison_passed": comparison["passed"] if comparison else None,
            "training_overlap": "not_established", "neural_superiority_required": False}}
