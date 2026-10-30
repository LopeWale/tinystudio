"""Retrieval evidence boundaries: authored restrictions must survive indexing."""
import hashlib
import json
from pathlib import Path

import pytest

from tinystudio.lab.retrieval import build_index, execute_retrieval, query_index

OVERRIDE = {"kind": "retrieval.include_restricted_evidence", "reason": "leakage sensitivity research"}


@pytest.mark.parametrize("metadata", [
    {"partition": "final"}, {"split": "held-out"}, {"bucket": "finaltest"},
    {"partition": "eval"}, {"source_visibility": "truth"}, {"source_role": "heldout"},
    {"role": "ground-truth"}, {"payload": {"visibility": "truth"}},
    {"record": {"true_value": 12}},
])
def test_restricted_metadata_requires_override_before_publication(tmp_path, metadata):
    output = tmp_path / "index"
    with pytest.raises(ValueError, match="research override"):
        build_index([], output, records=[{"text": "pump pressure", **metadata}])
    assert not output.exists()


@pytest.mark.parametrize("metadata", [{"partition": "unknown"}, {"partition": "train", "split": "final"}])
def test_invalid_explicit_partition_cannot_be_overridden(tmp_path, metadata):
    with pytest.raises(ValueError, match="partition"):
        build_index([], tmp_path / "index", records=[metadata], research_override=OVERRIDE)
    assert not (tmp_path / "index").exists()


def specification(tmp_path, *, declared=None, resolved=None, content=None):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(content or [{"text": "pressure", "visibility": "model", "partition": "train"}]))
    binding = {"artifact_id": "corpus", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), **(declared or {})}
    return {"definition": {"inputs": [binding], "parameters": {"query": "pressure"}},
            "input_paths": {"corpus": {"path": str(source), **(resolved or {})}}}


@pytest.mark.parametrize("declared,resolved", [
    ({"role": "final_test"}, {}), ({"visibility": "evaluation"}, {}),
    ({"source_role": "truth"}, {}), ({"partition": "final"}, {}),
    ({"visibility": "model"}, {"source_visibility": "truth"}),
])
def test_frozen_binding_cannot_be_weakened_by_model_visible_rows(tmp_path, declared, resolved):
    spec = specification(tmp_path, declared=declared, resolved=resolved)
    with pytest.raises(ValueError, match="research override"):
        execute_retrieval(spec, tmp_path / "out", lambda _: None, lambda: False)
    assert not (tmp_path / "out").exists()


def test_source_container_restrictions_survive_row_extraction(tmp_path):
    spec = specification(tmp_path, content={"visibility": "truth", "records": [{"text": "pressure", "visibility": "model"}]})
    with pytest.raises(ValueError, match="research override"):
        execute_retrieval(spec, tmp_path / "out", lambda _: None, lambda: False)


def test_override_keeps_binding_restrictions_and_unqualified_trace(tmp_path):
    spec = specification(tmp_path, declared={"role": "held-out", "visibility": "evaluation"})
    spec["definition"]["research_overrides"] = [OVERRIDE]
    result = execute_retrieval(spec, tmp_path / "out", lambda _: None, lambda: False)
    assert result["qualification"]["research_only"] is True
    assert result["qualification"]["qualified"] is False
    trace = result["records"][0]
    assert trace["trace"]["research_only"] is True
    assert trace["trace"]["research_override"] == OVERRIDE
    binding = trace["hits"][0]["provenance"]["binding"]
    assert binding["role"] == "held-out"
    assert binding["visibility"] == "evaluation"
    assert binding["artifact_id"] == "corpus"
    assert trace["hits"][0]["provenance"]["protection"] == "evaluation"
    payload = json.loads(Path(result["artifacts"][0]["path"]).read_text())
    assert payload["sources"][0]["binding"] == binding
    assert payload["restricted_evidence"]


def test_generic_external_scientific_content_is_not_reinterpreted(tmp_path):
    row = {"text": "The final held-out evaluation discusses ground_truth fields.",
           "article": {"partition": "a physical membrane", "ground_truth": "scientific definition"}}
    index = build_index([], tmp_path / "out", records=[row])
    trace = query_index(index["path"], "membrane")
    assert trace["hits"][0]["content"] == row
    assert trace["trace"]["research_only"] is False
    assert "partition" not in trace["hits"][0]["content"]


@pytest.mark.parametrize("override", [None, {}, {"kind": OVERRIDE["kind"], "reason": "  "},
                                       {"kind": "other", "reason": "research"}])
def test_override_requires_exact_kind_and_nonempty_reason(tmp_path, override):
    with pytest.raises(ValueError, match="research override"):
        build_index([], tmp_path / "out", records=[{"partition": "heldout"}], research_override=override)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("mode", ["lexical", "structured", "episode"])
def test_source_truth_dominates_evaluation_binding_and_row_in_existing_modes(tmp_path, mode):
    spec = specification(tmp_path, declared={"visibility": "evaluation"},
                         content={"source_visibility": "truth", "rows": [{"text": "pressure", "partition": "eval"}]})
    spec["definition"]["parameters"]["mode"] = mode
    spec["definition"]["research_overrides"] = [OVERRIDE]
    result = execute_retrieval(spec, tmp_path / "out", lambda _: None, lambda: False)
    assert result["records"][0]["hits"][0]["provenance"]["protection"] == "truth"
    assert result["qualification"]["qualified"] is False


def test_external_unassigned_file_keeps_content_and_is_qualified(tmp_path):
    spec = specification(tmp_path, content=[{"text": "pressure", "split": "unassigned"}])
    spec["definition"].pop("inputs")
    result = execute_retrieval(spec, tmp_path / "out", lambda _: None, lambda: False)
    assert result["records"][0]["hits"][0]["content"] == {"text": "pressure", "split": "unassigned"}
    assert result["qualification"]["qualified"] is True


@pytest.mark.parametrize("override", [False, True])
def test_execution_dispatch_preserves_restricted_corpus_bindings(tmp_path, override):
    from tinystudio.lab.execution import execute

    bindings, paths = [], {}
    for name, visibility in (("ordinary", "model"), ("heldout", "evaluation")):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps([{"text": name + " pressure", "partition": "train"}]))
        bindings.append({"artifact_id": name, "role": "source", "visibility": visibility,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        paths[name] = str(path)
    spec = {"definition": {"engine": "retrieval.index", "inputs": bindings,
                           "parameters": {"query": "pressure"},
                           "research_overrides": [OVERRIDE] if override else []}}
    if not override:
        with pytest.raises(ValueError, match="research override"):
            execute(spec, tmp_path / "run", lambda _: None, lambda: False, context={"input_paths": paths})
        return
    result = execute(spec, tmp_path / "run", lambda _: None, lambda: False, context={"input_paths": paths})
    assert result["qualification"]["qualified"] is False
    assert result["qualification"]["research_only"] is True
    trace_file = next(item for item in result["artifacts"] if item["role"] == "retrieval_trace")
    trace = json.loads(Path(trace_file["path"]).read_text())[0]
    assert {hit["provenance"]["binding"]["artifact_id"] for hit in trace["hits"]} == {"ordinary", "heldout"}
    assert next(hit for hit in trace["hits"] if hit["source"].endswith("heldout.json"))["provenance"]["protection"] == "evaluation"
