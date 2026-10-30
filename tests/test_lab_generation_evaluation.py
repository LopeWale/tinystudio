"""Generation composition must preserve held-out identity and isolate answers."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tinystudio.lab.contracts import ExperimentDefinition
from tinystudio.lab.engine import ExecutionCancelled
from tinystudio.lab.evaluation_generation import execute_generation_evaluation, validate_generation_selection
from tinystudio.lab.preflight import execution_checks
from tinystudio.lab.retrieval import build_index


def bind(spec, path, role, visibility="model"):
    item = {"artifact_id": role, "role": role, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "visibility": visibility}
    spec["definition"]["inputs"].append(item)
    spec["input_paths"][role] = str(path)
    return item


@pytest.fixture
def example(tmp_path):
    spec = {"definition": {"name": "Paired numeric evaluation", "engine": "evaluation.compare",
        "parameters": {"operation": "generate", "max_new_tokens": 2, "compare_retrieval": True},
        "execution": {"profile": "trusted_native"}, "inputs": []}, "input_paths": {}}
    model = tmp_path / "model"; model.write_bytes(b"model-boundary-fixture")
    bind(spec, model, "model")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"format": "tinystudio.evaluation-cases.v1", "cases": [
        {"source_id": "held-out", "episode_id": "case-episode", "partition": "test",
         "prompt": "pressure?", "query": "pressure", "expected": "-12.75", "unit": "Pa"}]}))
    bind(spec, cases, "cases", "evaluation")
    index = build_index([], tmp_path / "corpus", records=[{"text": "pressure low", "source_record_id": "training-source"}])
    bind(spec, Path(index["path"]), "retrieval_index")
    return spec


@pytest.fixture
def observed_session(monkeypatch):
    from tinystudio.lab import model_session
    state = {"calls": [], "closed": False, "completion": "-12.75"}
    class BoundarySession:
        def __init__(self, model_path, expected_sha256, work_dir, device):
            assert hashlib.sha256(model_path.read_bytes()).hexdigest() == expected_sha256
            self.metadata = {"device": device}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            state["closed"] = True
        def generate(self, prompt, max_new_tokens, cancelled):
            state["calls"].append(prompt)
            return {"prompt": prompt, "completion": state["completion"], "token_ids": [7], "input_token_ids": [4, 5]}
    monkeypatch.setattr(model_session, "ModelSession", BoundarySession)
    return state


def test_answer_projection_and_matched_retrieval_intervention(example, observed_session, tmp_path):
    original = deepcopy(example)
    result = execute_generation_evaluation(example, tmp_path / "out", lambda _: None, lambda: False)
    assert example == original
    assert observed_session["closed"]
    baseline, candidate = observed_session["calls"]
    assert "pressure low" not in baseline and "pressure low" in candidate
    for prompt in observed_session["calls"]:
        assert "-12.75" not in prompt and "held-out" not in prompt and "training-source" not in prompt
    assert result["baseline_comparison"]["metrics"]["aggregate"]["candidate_mae"] == "0"
    assert result["qualification"]["evaluation_passed"]
    records = result["records"]
    assert records[0]["retrieval"]["included_ids"] == []
    assert records[1]["retrieval"]["included_ids"] == records[1]["retrieval"]["retrieved_ids"]
    assert all(row["model"]["sha256"] == example["definition"]["inputs"][0]["sha256"] for row in records)
    assert all(row["source_id"] == "held-out" and row["episode_id"] == "case-episode" for row in records)
    copied_index = next(row for row in result["artifacts"] if row["role"] == "retrieval_index")
    assert Path(copied_index["path"]).read_bytes() == Path(example["input_paths"]["retrieval_index"]).read_bytes()


@pytest.mark.parametrize("completion", ["value -12.75", "12.75", "-1275", "", "NaN", " -12.75"])
def test_invalid_or_wrong_completion_is_retained_not_cleaned(example, observed_session, tmp_path, completion):
    observed_session["completion"] = completion
    result = execute_generation_evaluation(example, tmp_path / "out", lambda _: None, lambda: False)
    assert not result["qualification"]["evaluation_passed"]
    assert len(result["records"]) == 2
    assert all(row["prediction"] == completion for row in result["records"])
    if completion not in {"12.75", "-1275"}:
        assert "aggregate" not in result["baseline_comparison"]["metrics"]


@pytest.mark.parametrize("change", ["duplicate", "training", "no_prompt", "non_numeric", "tamper"])
def test_invalid_cases_fail_before_model_load(example, observed_session, tmp_path, change):
    path = Path(example["input_paths"]["cases"])
    payload = json.loads(path.read_text())
    if change == "duplicate": payload["cases"] *= 2
    if change == "training": payload["cases"][0]["partition"] = "train"
    if change == "no_prompt": payload["cases"][0].pop("prompt")
    if change == "non_numeric": payload["cases"][0]["expected"] = "hidden answer"
    path.write_text(json.dumps(payload) + "\n")
    if change != "tamper":
        example["definition"]["inputs"][1]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        execute_generation_evaluation(example, tmp_path / "out", lambda _: None, lambda: False)
    assert not observed_session["calls"]


def test_retained_restrictions_and_case_overlap_remain_unqualified(example, observed_session, tmp_path):
    index = build_index([], tmp_path / "restricted", records=[{"text": "pressure low", "partition": "test",
        "source_record_id": "held-out"}], research_override={"kind": "retrieval.include_restricted_evidence", "reason": "leakage control"})
    ref = example["definition"]["inputs"][2]
    ref["sha256"] = hashlib.sha256(Path(index["path"]).read_bytes()).hexdigest()
    example["input_paths"]["retrieval_index"] = index["path"]
    result = execute_generation_evaluation(example, tmp_path / "out", lambda _: None, lambda: False)
    assert result["qualification"]["research_only"]
    report = json.loads((tmp_path / "out/generation-evaluation.json").read_text())
    assert report["lineage"]["overlapping_retrieval_ids"]


@pytest.mark.parametrize("envelope,identity,overlap", [
    ("metadata", {"episode_id": "case-episode"}, True),
    ("provenance", {"source_record_id": "held-out"}, True),
    ("metadata", {"source_record_ids": ["unrelated", "held-out"]}, True),
    ("metadata", {"source_record_id": "unrelated"}, False),
    ("domain_state", {"source_record_id": "held-out"}, False),
])
def test_declared_envelope_overlap_is_not_arbitrary_domain_content(example, observed_session, tmp_path, envelope, identity, overlap):
    index = build_index([], tmp_path / "nested", records=[{"text": "pressure low", envelope: identity}])
    ref = example["definition"]["inputs"][2]
    ref["sha256"] = hashlib.sha256(Path(index["path"]).read_bytes()).hexdigest()
    example["input_paths"]["retrieval_index"] = index["path"]
    result = execute_generation_evaluation(example, tmp_path / "out", lambda _: None, lambda: False)
    assert result["qualification"]["research_only"] is overlap


@pytest.mark.parametrize("identity_location", ["binding", "envelope"])
def test_source_binding_metadata_overlap_is_audited(example, observed_session, tmp_path, identity_location):
    source = tmp_path / "corpus.json"
    rows = [{"text": "pressure low"}]
    source.write_text(json.dumps(rows if identity_location == "binding" else {
        "records": rows, "source_record_id": "held-out"}))
    binding = {"path": str(source)}
    if identity_location == "binding": binding["metadata"] = {"source_record_id": "held-out"}
    index = build_index([binding], tmp_path / "nested")
    ref = example["definition"]["inputs"][2]
    ref["sha256"] = hashlib.sha256(Path(index["path"]).read_bytes()).hexdigest()
    example["input_paths"]["retrieval_index"] = index["path"]
    result = execute_generation_evaluation(example, tmp_path / "out", lambda _: None, lambda: False)
    assert result["qualification"]["research_only"]


@pytest.mark.parametrize("unit", ["omitted", None, "Pa", "V"])
def test_typed_expected_quantity_uses_validated_effective_unit(example, observed_session, tmp_path, unit):
    path = Path(example["input_paths"]["cases"])
    payload = json.loads(path.read_text())
    payload["cases"][0]["expected"] = {"value": "-12.75", "unit": "Pa"}
    if unit == "omitted": payload["cases"][0].pop("unit")
    else: payload["cases"][0]["unit"] = unit
    path.write_text(json.dumps(payload))
    example["definition"]["inputs"][1]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if unit == "V":
        with pytest.raises(ValueError, match="conflicting"):
            execute_generation_evaluation(example, tmp_path / "out", lambda _: None, lambda: False)
        assert not observed_session["calls"]
    else:
        result = execute_generation_evaluation(example, tmp_path / "out", lambda _: None, lambda: False)
        assert result["qualification"]["evaluation_passed"]
        assert result["baseline_comparison"]["passed"]
        assert all(row["unit"] == "Pa" for row in result["records"])


def test_cancellation_does_not_publish_partial_paired_report(example, observed_session, tmp_path):
    with pytest.raises(ExecutionCancelled):
        execute_generation_evaluation(example, tmp_path / "out", lambda _: None,
                                      lambda: len(observed_session["calls"]) == 1)
    assert observed_session["closed"]
    assert not (tmp_path / "out/generation-evaluation.json").exists()


@pytest.mark.parametrize("change", ["visibility", "no_model", "two_models", "no_index", "unknown_parameter", "bad_device", "boolean_tokens"])
def test_preflight_rejects_conflicting_generation_intent(example, change):
    definition = example["definition"]
    if change == "visibility": definition["inputs"][1]["visibility"] = "model"
    if change == "no_model": definition["inputs"].pop(0)
    if change == "two_models": definition["inputs"].append({**definition["inputs"][0], "role": "checkpoint", "artifact_id": "second"})
    if change == "no_index": definition["inputs"].pop(2)
    if change == "unknown_parameter": definition["parameters"]["examples"] = []
    if change == "bad_device": definition["parameters"]["device"] = "automatic"
    if change == "boolean_tokens": definition["parameters"]["max_new_tokens"] = True
    checks = execution_checks(ExperimentDefinition.model_validate(definition), {"runtime": {"torch": "test"}})
    check = next(row for row in checks if row["code"] == "evaluation_selection")
    assert not check["passed"]


def test_precomputed_evaluation_does_not_require_torch_or_model_inputs():
    assert validate_generation_selection({"parameters": {"examples": []}}) is None
    definition = ExperimentDefinition(name="old", engine="evaluation.compare", parameters={"examples": []}, execution={"profile": "trusted_native"})
    checks = execution_checks(definition, {"runtime": {"python": "3.12"}})
    assert next(row for row in checks if row["code"] == "evaluation_selection")["passed"]


def test_precomputed_mode_never_silently_ignores_generation_bindings(example):
    example["definition"]["parameters"]["operation"] = "precomputed"
    with pytest.raises(ValueError, match="operation=generate"):
        validate_generation_selection(example["definition"])


def test_generation_compatibility_is_dependency_free_and_requires_advertised_torch(example):
    import subprocess
    import sys
    code = "from tinystudio.lab.evaluation_generation import validate_generation_selection; import sys; assert 'torch' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, timeout=15)
    checks = execution_checks(ExperimentDefinition.model_validate(example["definition"]), {"runtime": {"python": "3.12"}})
    selected = next(row for row in checks if row["code"] == "evaluation_selection")
    assert not selected["passed"] and "torch" in selected["message"]
