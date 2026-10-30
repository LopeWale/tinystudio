"""Matched-method contract: shared inputs, exact formulae and actual inference."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tinystudio.lab.contracts import ExperimentDefinition
from tinystudio.lab.engine import ExecutionCancelled
from tinystudio.lab.evaluation_matrix import execute_method_matrix, validate_matrix_selection
from tinystudio.lab.preflight import execution_checks
from tinystudio.lab.research import execute_evaluation
from tinystudio.lab.retrieval import build_index


def retained_report(result):
    artifact = next(row for row in result["artifacts"] if row["role"] == "independent_evaluation")
    content = Path(artifact["path"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == artifact["sha256"]
    return json.loads(content)


def bind(spec, path, role, identity=None, visibility="model"):
    identity = identity or role
    item = {"artifact_id": identity, "role": role, "visibility": visibility,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    spec["definition"]["inputs"].append(item)
    spec["input_paths"][identity] = str(path)
    return item


def write_cases(spec, tmp_path, rows):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"format": "tinystudio.evaluation-cases.v1", "cases": rows}))
    old = next((row for row in spec["definition"]["inputs"] if row["role"] == "cases"), None)
    if old:
        old["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        bind(spec, path, "cases", "shared-cases", "truth")


def affine(identity, coefficients=None, intercept="1"):
    return {"id": identity, "label": identity, "kind": "affine",
            "coefficients": coefficients or {"x": "2", "z": "3"}, "intercept": intercept}


def model(identity, artifact_id="model", retrieval=False):
    return {"id": identity, "label": identity, "kind": "model", "artifact_id": artifact_id, "retrieval": retrieval}


@pytest.fixture
def example(tmp_path):
    spec = {"definition": {"name": "Matched methods", "engine": "evaluation.compare", "inputs": [],
        "parameters": {"operation": "matrix", "methods": [affine("reference"), affine("other")],
                       "baseline_method": "reference", "max_new_tokens": 1},
        "execution": {"profile": "trusted_native"}}, "input_paths": {}}
    write_cases(spec, tmp_path, [{"source_id": "heldout-secret", "episode_id": "episode-secret", "partition": "test",
        "prompt": "Compute the output", "features": {"z": "4", "x": "5"}, "expected": "23", "unit": "Pa"}])
    return spec


@pytest.fixture
def boundary(monkeypatch):
    from tinystudio.lab import model_session
    state = {"calls": [], "completion": "23", "closed": 0}
    class Session:
        def __init__(self, path, sha, work_dir, device):
            assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
            self.metadata = {"kind": "actual-fixture", "sha256": sha, "device": device}
        def __enter__(self): return self
        def __exit__(self, *args): state["closed"] += 1
        def generate(self, prompt, max_new_tokens, cancelled):
            state["calls"].append(prompt)
            return {"completion": state["completion"], "prompt": prompt, "token_ids": [1]}
    monkeypatch.setattr(model_session, "ModelSession", Session)
    return state


def add_model(spec, tmp_path, identity="model", role="model"):
    path = tmp_path / (identity + ".pt")
    path.write_bytes(identity.encode())
    return bind(spec, path, role, identity)


def add_index(spec, tmp_path):
    result = build_index([], tmp_path / "corpus", records=[{"text": "Compute known evidence", "source_id": "training"}])
    return bind(spec, Path(result["path"]), "retrieval_index")


def test_affine_only_dispatch_exact_recipe_coverage_and_no_torch_requirement(example, tmp_path):
    original = deepcopy(example)
    checks = execution_checks(ExperimentDefinition.model_validate(example["definition"]), {"runtime": {"python": "3"}})
    assert next(row for row in checks if row["code"] == "evaluation_selection")["passed"]
    result = execute_evaluation(example, tmp_path / "out", lambda _: None, lambda: False)
    assert example == original
    report = retained_report(result)
    assert report["format"] == "tinystudio.method-matrix.v1"
    assert report["case_count"] == 1 and report["method_count"] == 2 and report["expected_coverage"] == 2
    assert result["qualification"]["coverage_complete"]
    assert report["comparisons"]["other"]["metrics"]["aggregate"]["candidate_mae"] == "0"
    assert all(row["prediction"] == "23" for row in result["records"])
    assert len({row["case_sha256"] for row in result["records"]}) == 1
    assert report["methods"][0]["metadata"]["format"] == "tinystudio.affine.v1"
    assert report["methods"][0]["metadata"]["fitting"] == "none"
    assert result["artifacts"][-1]["role"] == "independent_evaluation"
    assert not result["qualification"]["neural_superiority_required"]


def test_exact_arithmetic_beyond_float_and_fifty_digits_and_key_order(example, tmp_path):
    # Independently checked identity: (10^60 + 1)*2 + 3*4 + 1 = 2*10^60 + 15.
    value = "1" + "0" * 59 + "1"
    expected = "2" + "0" * 58 + "15"
    write_cases(example, tmp_path, [{"source_id": "s", "episode_id": "e", "partition": "heldout", "prompt": "p",
        "features": {"x": value, "z": "4"}, "expected": expected}])
    example["definition"]["parameters"]["methods"][1]["coefficients"] = {"z": "3", "x": "2"}
    result = execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    assert [row["prediction"] for row in result["records"]] == [expected, expected]
    assert result["qualification"]["evaluation_passed"]


def test_exact_fractional_cancellation_keeps_last_digit(example, tmp_path):
    tiny = "0." + "0" * 79 + "1"
    write_cases(example, tmp_path, [{"source_id": "s", "episode_id": "e", "partition": "test", "prompt": "p",
        "features": {"x": tiny}, "expected": tiny}])
    example["definition"]["parameters"]["methods"] = [affine("reference", {"x": "1"}, "0"), affine("other", {"x": "1"}, "0")]
    result = execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    assert all(row["prediction"] == tiny for row in result["records"])


@pytest.mark.parametrize("change", ["reference", "visibility", "duplicate_cases", "unused", "missing_index", "baseline", "leftover", "learning", "bad_id", "duplicate_id", "bad_role", "duplicate_artifact", "unused_index"])
def test_invalid_authored_matrix_rejected_purely(example, tmp_path, change):
    definition = example["definition"]
    methods = definition["parameters"]["methods"]
    if change == "reference": methods[1] = model("other", "unbound")
    if change == "visibility": definition["inputs"][0]["visibility"] = "model"
    if change == "duplicate_cases": definition["inputs"].append({**definition["inputs"][0], "artifact_id": "second-cases"})
    if change == "unused": add_model(example, tmp_path)
    if change == "missing_index":
        add_model(example, tmp_path); methods[1] = model("other", retrieval=True)
    if change == "baseline": definition["parameters"]["baseline_method"] = "absent"
    if change == "leftover": definition["parameters"]["compare_retrieval"] = True
    if change == "learning": definition["learning"] = {"foo": "bar"}
    if change == "bad_id": methods[1]["id"] = "../outside"
    if change == "duplicate_id": methods[1]["id"] = methods[0]["id"]
    if change == "bad_role": definition["inputs"][0]["role"] = "examples"
    if change == "duplicate_artifact": definition["inputs"].append(deepcopy(definition["inputs"][0]))
    if change == "unused_index": add_index(example, tmp_path)
    with pytest.raises(ValueError): validate_matrix_selection(definition)


@pytest.mark.parametrize("value", [1, True, 1.0, {"value": "1", "unit": "m"}, "NaN", " 1", "1e129", "1" * 129, "0" * 257])
def test_invalid_coordinate_or_coefficient_fails_before_model_load(example, tmp_path, boundary, value):
    example["definition"]["parameters"]["methods"][0]["coefficients"]["x"] = value
    with pytest.raises(ValueError): execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    assert boundary["calls"] == []


@pytest.mark.parametrize("change", ["float_expected", "typed_float_expected", "feature_float", "feature_units", "missing", "unused", "tamper"])
def test_content_validation_before_any_method(example, tmp_path, boundary, change):
    path = Path(example["input_paths"]["shared-cases"])
    payload = json.loads(path.read_text()); case = payload["cases"][0]
    if change == "float_expected": case["expected"] = 23.0
    if change == "typed_float_expected": case["expected"] = {"value": 23.0, "unit": "Pa"}
    if change == "feature_float": case["features"]["x"] = 5.0
    if change == "feature_units": case["features"]["x"] = {"value": "5", "unit": "m"}
    if change == "missing": case["features"].pop("x")
    if change == "unused": case["features"]["extra"] = "1"
    path.write_text(json.dumps(payload) + ("\n" if change == "tamper" else ""))
    if change != "tamper": example["definition"]["inputs"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError): execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    assert not (tmp_path / "out/method-matrix.json").exists()


def test_real_projection_same_features_no_answers_no_disabled_retrieval(example, tmp_path, boundary, monkeypatch):
    from tinystudio.lab import evaluation_matrix
    add_model(example, tmp_path, "one"); add_model(example, tmp_path, "two")
    index = add_index(example, tmp_path)
    example["definition"]["retrieval"] = {"fields": ["text"], "top_k": 1}
    example["definition"]["parameters"]["methods"] = [model("plain", "one"), model("second", "two"), model("rag", "one", True), affine("reference")]
    original = deepcopy(example); projected = []; events = []
    executor = evaluation_matrix.execute_generation_evaluation
    def observe(spec, *args):
        projected.append(deepcopy(spec))
        return executor(spec, *args)
    monkeypatch.setattr(evaluation_matrix, "execute_generation_evaluation", observe)
    result = execute_method_matrix(example, tmp_path / "out", events.append, lambda: False)
    assert example == original
    plain, second, rag = boundary["calls"]
    assert plain == second
    assert 'Features:\n{"x":"5","z":"4"}' in plain
    assert "known evidence" not in plain and "known evidence" in rag
    for prompt in boundary["calls"]:
        assert "23" not in prompt and "heldout-secret" not in prompt and "episode-secret" not in prompt
    for child in projected[:2]:
        assert "retrieval" not in child["definition"]
        assert {row["role"] for row in child["definition"]["inputs"]} == {"model", "cases"}
        assert set(child["input_paths"]) in ({"one", "shared-cases"}, {"two", "shared-cases"})
    assert projected[2]["definition"]["parameters"]["operation"] == "generate"
    assert not projected[2]["definition"]["parameters"]["compare_retrieval"]
    assert all(event["payload"]["method_id"] in {"plain", "second", "rag", "reference"} for event in events)
    assert retained_report(result)["methods"][0]["metadata"]["kind"] == "actual-fixture"
    artifacts = result["artifacts"]
    assert len({row["name"] for row in artifacts}) == len(artifacts)
    assert sum(row["role"] == "independent_evaluation" for row in artifacts) == 1
    assert sum(row["role"] == "method_evaluation" for row in artifacts) == 3
    for row in artifacts:
        assert hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest() == row["sha256"]
    index_copy = next(row for row in artifacts if row["role"] == "retrieval_index")
    assert index_copy["sha256"] == index["sha256"]
    for receipt in retained_report(result)["methods"][:3]:
        assert receipt["artifacts"]
        assert all(any(ref["name"] == artifact["name"] and ref["sha256"] == artifact["sha256"] for artifact in artifacts) for ref in receipt["artifacts"])


def test_arbitrary_model_baseline_invalid_completion_suppresses_aggregate(example, tmp_path, boundary):
    add_model(example, tmp_path)
    example["definition"]["parameters"]["methods"][0] = model("reference")
    boundary["completion"] = "answer: 23"
    result = execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    report = retained_report(result)
    assert report["records"][0]["prediction"] == "answer: 23"
    assert not report["evaluations"]["reference"]["passed"]
    assert "aggregate" not in report["comparisons"]["other"]["metrics"]
    assert result["qualification"]["coverage_complete"]


@pytest.mark.parametrize("kind", ["affine", "model"])
@pytest.mark.parametrize("after", [1, 2])
def test_cancellation_between_methods_and_after_final_prediction(example, tmp_path, boundary, kind, after):
    events = []
    if kind == "model":
        add_model(example, tmp_path)
        example["definition"]["parameters"]["methods"] = [model("reference"), model("other")]
    with pytest.raises(ExecutionCancelled):
        execute_method_matrix(example, tmp_path / "out", events.append,
            lambda: len([event for event in events if event["kind"] == "evaluation_prediction"]) >= after)
    assert not (tmp_path / "out/method-matrix.json").exists()
    if kind == "model": assert boundary["closed"] == after


def test_bound_model_artifact_id_may_equal_singleton_role_name(example, tmp_path, boundary):
    add_model(example, tmp_path, "cases")
    example["definition"]["parameters"]["methods"][1] = model("other", "cases")
    result = execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    assert retained_report(result)["methods"][1]["model"]["artifact_id"] == "cases"


def test_matrix_uses_actual_owned_model_session(example, tmp_path):
    import torch
    from tinystudio.lab.learning import ByteTokenizer, build_model
    tokenizer = ByteTokenizer()
    architecture = {"family": "gru", "hidden_size": 8, "layers": 1, "context": 256}
    owned = build_model(architecture, tokenizer.vocab_size)
    with torch.no_grad():
        for parameter in owned.parameters(): parameter.zero_()
        owned.head.bias[ord("7")] = 1
    path = tmp_path / "actual.pt"
    torch.save({"format": "tinystudio.learning.v1", "architecture": architecture,
        "tokenizer": tokenizer.state(), "model": owned.state_dict()}, path)
    bind(example, path, "checkpoint", "actual")
    example["definition"]["parameters"]["methods"] = [model("reference", "actual"), model("other", "actual")]
    result = execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    assert [row["prediction"] for row in result["records"]] == ["7", "7"]
    assert all(method["metadata"]["kind"] == "scratch_language" for method in retained_report(result)["methods"])
    assert all(row["output"]["input_token_ids"] for row in result["records"])
    assert not list((tmp_path / "out/reference/model-work").iterdir())
    assert not list((tmp_path / "out/other/model-work").iterdir())


@pytest.mark.parametrize("fault", ["duplicate", "missing", "partition", "target", "cases_hash"])
def test_child_regression_cannot_claim_matching_coverage(example, tmp_path, monkeypatch, fault):
    from tinystudio.lab import evaluation_matrix
    add_model(example, tmp_path)
    example["definition"]["parameters"]["methods"][1] = model("other")
    first = json.loads(Path(example["input_paths"]["shared-cases"]).read_text())["cases"][0]
    second = {**first, "source_id": "second-heldout"}
    write_cases(example, tmp_path, [first, second])
    cases_ref = example["definition"]["inputs"][0]
    def broken_child(*args):
        rows = [{**{key: case[key] for key in ("source_id", "episode_id", "partition", "expected", "unit")},
            "prediction": "23", "cases": {key: cases_ref[key] for key in ("artifact_id", "sha256")}} for case in [first, second]]
        if fault == "duplicate": rows[1] = deepcopy(rows[0])  # Same row count, incomplete identity set.
        if fault == "missing": rows.pop()
        if fault == "partition": rows[1]["partition"] = "validation"
        if fault == "target": rows[1]["expected"] = "24"
        if fault == "cases_hash": rows[1]["cases"]["sha256"] = "0" * 64
        return {"records": rows, "artifacts": [], "qualification": {"research_only": False}}
    monkeypatch.setattr(evaluation_matrix, "execute_generation_evaluation", broken_child)
    with pytest.raises(ValueError, match="method output"):
        execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    assert not (tmp_path / "out/method-matrix.json").exists()


def test_late_cancellation_after_aggregate_digest_removes_unpublished_report(example, tmp_path, monkeypatch):
    from tinystudio.lab import evaluation_matrix
    late = {"cancelled": False}
    original_digest = evaluation_matrix.digest
    def digest_then_cancel(path):
        result = original_digest(path)
        if Path(path).name == "method-matrix.json": late["cancelled"] = True
        return result
    monkeypatch.setattr(evaluation_matrix, "digest", digest_then_cancel)
    with pytest.raises(ExecutionCancelled):
        execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: late["cancelled"])
    assert not (tmp_path / "out/method-matrix.json").exists()


def test_cancel_between_shared_cases(example, tmp_path):
    first = json.loads(Path(example["input_paths"]["shared-cases"]).read_text())["cases"][0]
    write_cases(example, tmp_path, [first, {**first, "source_id": "second"}])
    events = []
    with pytest.raises(ExecutionCancelled):
        execute_method_matrix(example, tmp_path / "out", events.append, lambda: bool(events))
    assert len(events) == 1
    assert not (tmp_path / "out/method-matrix.json").exists()


def test_two_rag_methods_reuse_identical_frozen_index_and_propagate_research_only(example, tmp_path, boundary):
    add_model(example, tmp_path)
    index = build_index([], tmp_path / "restricted", records=[{"text": "Compute evidence", "partition": "test"}],
        research_override={"kind": "retrieval.include_restricted_evidence", "reason": "leakage control"})
    binding = bind(example, Path(index["path"]), "retrieval_index")
    example["definition"]["parameters"]["methods"] = [model("reference", retrieval=True), model("other", retrieval=True)]
    result = execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    copies = [row for row in result["artifacts"] if row["role"] == "retrieval_index"]
    assert len(copies) == 2
    assert all(row["sha256"] == binding["sha256"] for row in copies)
    assert result["qualification"]["research_only"]


@pytest.mark.parametrize("expected", [23, "23", {"value": "23", "unit": "Pa"}])
def test_exact_expected_representations_remain_accepted(example, tmp_path, expected):
    case = json.loads(Path(example["input_paths"]["shared-cases"]).read_text())["cases"][0]
    case["expected"] = expected
    write_cases(example, tmp_path, [case])
    result = execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    assert result["qualification"]["evaluation_passed"]


def test_matrix_model_preflight_requires_torch_and_import_remains_dependency_free(example, tmp_path):
    import subprocess
    import sys
    subprocess.run([sys.executable, "-c", "import tinystudio.lab.evaluation_matrix; import sys; assert 'torch' not in sys.modules"],
                   check=True, timeout=15)
    add_model(example, tmp_path)
    example["definition"]["parameters"]["methods"][1] = model("other")
    checks = execution_checks(ExperimentDefinition.model_validate(example["definition"]), {"runtime": {"python": "3"}})
    selected = next(row for row in checks if row["code"] == "evaluation_selection")
    assert not selected["passed"] and "torch" in selected["message"]


def test_maximum_affine_matrix_keeps_result_metadata_small_and_full_evidence_retained(example, tmp_path):
    from tinystudio.lab.contracts import canonical
    from tinystudio.lab.execution import execute
    rows = [{"source_id": f"heldout-{index}", "episode_id": "shared-episode", "partition": "test",
             "prompt": "Compute the output", "features": {"x": "5", "z": "4"}, "expected": "23", "unit": "Pa"}
            for index in range(1000)]
    write_cases(example, tmp_path, rows)
    methods = [affine(f"method-{index}") for index in range(16)]
    example["definition"]["parameters"].update(methods=methods, baseline_method="method-0")
    result = execute(example, tmp_path / "out", lambda _: None, lambda: False,
                     context={"input_paths": example["input_paths"]})
    # The same metadata object is sent to service._canonical_result after upload.
    # The common executor must leave no embedded per-case evidence in it.
    assert len(canonical(result)) < 1024**2
    summary = result["baseline_comparison"]
    assert set(summary) == {"format", "baseline_method", "cases", "case_count", "method_count",
                           "expected_coverage", "qualification"}
    assert summary["case_count"] == 1000 and summary["method_count"] == 16
    assert summary["expected_coverage"] == result["records"] == result["record_count"] == 16000
    assert summary["qualification"]["coverage_complete"]
    aggregate = next(row for row in result["artifacts"] if row["role"] == "independent_evaluation")
    assert aggregate == result["artifacts"][-1]
    assert Path(aggregate["path"]).stat().st_size > 1024**2
    report = retained_report(result)
    assert len(report["records"]) == 16000
    assert {row["method_id"] for row in report["records"]} == {method["id"] for method in methods}
    assert all(len(report["evaluations"][method["id"]]["examples"]) == 1000 for method in methods)
    assert all(len(comparison["metrics"]["per_example"]) == 1000 for comparison in report["comparisons"].values())
    assert all(row["prediction"] == "23" and row["output"]["features"] == {"x": "5", "z": "4"}
               for row in report["records"])
    sidecar = next(row for row in result["artifacts"] if row["role"] == "derived_records")
    with Path(sidecar["path"]).open() as stream:
        assert sum(1 for line in stream if line.strip()) == 16000


@pytest.mark.parametrize("target", [9007199254740993, -9007199254740993, "9007199254740993",
    "-9007199254740993", {"value": 9007199254740993, "unit": "Pa"},
    {"value": -9007199254740993, "unit": "Pa"}, {"value": "-9007199254740993", "unit": "Pa"}])
def test_exact_target_display_keeps_original_targets_and_case_hashes(example, tmp_path, target):
    from tinystudio.lab.contracts import canonical
    original_case = {"source_id": "s", "episode_id": "e", "partition": "test", "prompt": "p",
        "features": {"x": "5", "z": "4"}, "expected": target, "unit": "Pa"}
    write_cases(example, tmp_path, [original_case])
    frozen = deepcopy(example)
    original_bytes = Path(example["input_paths"]["shared-cases"]).read_bytes()
    expected_case_hash = hashlib.sha256(canonical(original_case)).hexdigest()
    result = execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    report = retained_report(result)
    expected_display = {"value": str(target["value"]), "unit": target["unit"]} if isinstance(target, dict) else str(target)
    for row in report["records"]:
        assert row["expected_display"] == expected_display
        assert row["expected"] == target
        assert type(row["expected"]) is type(target)
        assert row["case_sha256"] == expected_case_hash
        assert row["cases"]["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert example == frozen
    assert Path(example["input_paths"]["shared-cases"]).read_bytes() == original_bytes


@pytest.mark.parametrize("invalid_method", ["reference", "other"])
def test_individual_accuracy_join_preserves_same_source_distinct_episode_with_invalid_pair(example, tmp_path, boundary, invalid_method):
    add_model(example, tmp_path)
    methods = [affine("reference"), affine("other")]
    methods[0 if invalid_method == "reference" else 1] = model(invalid_method)
    example["definition"]["parameters"]["methods"] = methods
    first = {"source_id": "repeated-source", "episode_id": "episode-a", "partition": "test", "prompt": "p",
        "features": {"x": "5", "z": "4"}, "expected": "23", "unit": "Pa"}
    # The affine method is correct for episode-a and wrong for episode-b.
    second = {**first, "episode_id": "episode-b", "expected": "24"}
    write_cases(example, tmp_path, [first, second])
    boundary["completion"] = "answer: 23"
    result = execute_method_matrix(example, tmp_path / "out", lambda _: None, lambda: False)
    report = retained_report(result)
    exact = "other" if invalid_method == "reference" else "reference"
    exact_examples = report["evaluations"][exact]["examples"]
    invalid_examples = report["evaluations"][invalid_method]["examples"]
    assert [(row["source_id"], row["episode_id"], row["passed"]) for row in exact_examples] == [
        ("repeated-source", "episode-a", True), ("repeated-source", "episode-b", False)]
    assert [(row["source_id"], row["episode_id"], row["passed"]) for row in invalid_examples] == [
        ("repeated-source", "episode-a", False), ("repeated-source", "episode-b", False)]
    assert "aggregate" not in report["comparisons"]["other"]["metrics"]
    assert not report["comparisons"]["other"]["passed"]
    # Each report result now joins by the full (method, source, episode) identity.
    for record in report["records"]:
        matching = [evaluation for evaluation in report["evaluations"][record["method_id"]]["examples"]
                    if (evaluation["source_id"], evaluation["episode_id"]) ==
                       (record["source_id"], record["episode_id"])]
        assert len(matching) == 1
