"""Exact identity and complete bounded scans against local retained CAS bytes.

Database authorization lookups are a deliberate unit boundary. Materialization,
checksum verification, JSONL iteration and all matching/counting remain real.
"""
import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

from tinystudio.lab import inspection, service as svc
from tinystudio.lab.annotations import observation_checksum
from tinystudio.lab.representations import RepresentationEncoder
from tinystudio.storage import ArtifactStorage


@pytest.fixture
def retained(tmp_path, monkeypatch):
    store = ArtifactStorage(root=tmp_path / "objects")
    monkeypatch.setattr(svc, "storage", lambda: store)
    artifacts = {}
    run = SimpleNamespace(id="run", project_id="project", result={"artifacts": []})

    def get_run(db, project, run_id):
        if (project, run_id) != ("project", "run"):
            raise svc.LabError("run not found", 404)
        return run

    def get_artifact(db, project, identity):
        if project != "project" or identity not in artifacts:
            raise svc.LabError("artifact not found", 404)
        return artifacts[identity]

    monkeypatch.setattr(svc, "get_run", get_run)
    monkeypatch.setattr(svc, "get_artifact", get_artifact)
    monkeypatch.setattr(svc, "artifact_dict", lambda a: {"id": a.id, "sha256": a.sha256, "role": a.role})

    def add(identity, rows, role="transformed_dataset"):
        path = tmp_path / (identity + ".jsonl")
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        artifact = SimpleNamespace(id=identity, name=path.name, role=role, size=path.stat().st_size,
                                   sha256=hashlib.sha256(path.read_bytes()).hexdigest(), storage_key=identity)
        store.upload_file(path, identity)
        artifacts[identity] = artifact
        run.result["artifacts"].append({"id": identity})
        return artifact
    return add, artifacts, store


def test_representation_selection_is_absolute_row_and_fingerprint_not_time(retained):
    add, _, _ = retained
    a = {"source_record_id": "same", "time": 4, "value": -0.125, "units": "V", "wire_hex": "0080ff"}
    b = {**a, "value": 12.75}
    add("observations", [a, b])
    numeric = RepresentationEncoder({"kind": "numeric_text", "name": "precise"}).encode(a)
    binary = RepresentationEncoder({"kind": "raw_bytes", "name": "wire", "max_tokens": 2, "truncation": "prefix"}).encode(a)
    wrong = RepresentationEncoder("numeric_text").encode(b)
    legacy = {**numeric, "observation_sha256": None}
    add("codecs", [wrong, legacy, numeric, binary], "representation_evidence")
    output = inspection.representations(None, "project", "run", "observations", 0)
    assert output["identity"]["row_index"] == 0
    assert output["identity"]["observation_sha256"] == observation_checksum(a)
    assert [m["row_index"] for m in output["matches"]] == [2, 3]
    assert output["legacy_unqualified_records"] == 1
    text, wire = [m["record"] for m in output["matches"]]
    assert '"value":-0.125' in text["model_input_text"]
    assert bytes(text["token_ids"]) == base64.b64decode(text["model_input_base64"])
    assert wire["token_ids"] == [0, 128]
    assert wire["model_input_text"] is None
    assert base64.b64decode(wire["model_input_base64"]) == b"\x00\x80"
    assert wire["loss"]["omitted_tokens"] == 1
    assert wire["input_sha256"] == hashlib.sha256(b"\x00\x80").hexdigest()
    second = inspection.representations(None, "project", "run", "observations", 1)
    assert [m["row_index"] for m in second["matches"]] == [0]


def test_encoder_recomputes_fingerprint_instead_of_trusting_authored_claim():
    row = {"source_record_id": "one", "time": 1, "value": -1.25, "observation_sha256": "stale"}
    assert RepresentationEncoder("numeric_text").encode(row)["observation_sha256"] == observation_checksum(row)


def test_original_and_derived_differing_observations_are_unmatched(retained):
    add, _, _ = retained
    a = {"source_record_id": "same", "time": 4, "value": -0.125}
    add("original", [a], "raw_dataset")
    add("codecs", [RepresentationEncoder("numeric_text").encode({**a, "value": 12.75})], "representation_evidence")
    result = inspection.representations(None, "project", "run", "original", 0)
    assert result["status"] == "unmatched" and result["matches"] == []


def test_unknown_retained_ids_rows_and_project_are_rejected(retained):
    add, _, _ = retained
    add("rows", [{"source_record_id": "x", "value": 1}])
    for project, identity, index in [("other", "rows", 0), ("project", "unknown", 0), ("project", "rows", 8)]:
        with pytest.raises(svc.LabError, match="not found|not retained"):
            inspection.representations(None, project, "run", identity, index)
    with pytest.raises(svc.LabError, match="row index"):
        inspection.representations(None, "project", "run", "rows", -1)


def test_corruption_fails_before_coverage_or_matched_evidence_is_returned(retained):
    add, _, store = retained
    row = {"source_record_id": "x", "value": 1}
    add("rows", [row])
    add("codecs", [RepresentationEncoder("numeric_text").encode(row)], "representation_evidence")
    store._path("codecs").write_text('{}\n')
    with pytest.raises(svc.LabError, match="checksum"):
        inspection.representations(None, "project", "run", "rows", 0)
    store._path("rows").write_text('{}\n')
    with pytest.raises(svc.LabError, match="checksum"):
        inspection.coverage(None, "project", "run")


def test_coverage_scans_beyond_chart_window_and_preserves_explicit_partitions(retained):
    add, _, _ = retained
    rows = [{"source_record_id": f"x{i}", "value": i / 8, "observed": True,
             "scenario_id": "baseline", "partition": "train"} for i in range(1001)]
    rows += [{"source_record_id": "late", "scenario_id": "fault", "partition": "final_test", "estimated": True, "value": None},
             {"source_record_id": "unknown", "value": 2, "split": "val"}]
    add("rows", rows)
    add("other", [{"value": -1, "observed": False, "units": "V"}])
    output = inspection.coverage(None, "project", "run")
    assert output["counts"] == {"records": 1004, "observed": 1001, "estimated": 1, "missing": 2, "observation_status_not_recorded": 1}
    assert output["partition_counts"] == {"train": 1001, "test": 1, "validation": 1, "Not recorded": 1}
    cell = next(c for c in output["cells"] if c["row"] == "fault")
    assert cell["column"] == "final_test" and cell["count"] == 1
    assert cell["sources"][0]["identity"]["row_index"] == 1001
    assert cell["sources"][0]["identity"]["artifact_id"] == "rows"
    assert len(next(c for c in output["cells"] if c["row"] == "baseline")["sources"]) == 8
    assert output["complete"] is True
    assert "Not recorded" in output["rows"]


def test_coverage_axes_and_limits_are_actionable_without_partial_counts(retained, monkeypatch):
    add, artifacts, _ = retained
    add("rows", [{"device_id": f"device{i}"} for i in range(257)])
    with pytest.raises(svc.LabError, match="exceeds 256"):
        inspection.coverage(None, "project", "run", "device_id", "units")
    with pytest.raises(svc.LabError, match="unsupported coverage axis"):
        inspection.coverage(None, "project", "run", "made_up", "units")
    artifacts["rows"].size = inspection.MAX_BYTES + 1
    with pytest.raises(svc.LabError, match="128 MiB"):
        inspection.coverage(None, "project", "run")


def test_selected_jsonl_preserves_retained_decimal_lexemes_and_negative_zero(retained):
    add, artifacts, store = retained
    add("literal", [{"value": 0}])
    literal = b'{"source_record_id":"literal","time":0.000,"value":-0.000000,"units":"V"}\n'
    store._path("literal").write_bytes(literal)
    artifacts["literal"].size = len(literal)
    artifacts["literal"].sha256 = hashlib.sha256(literal).hexdigest()
    output = inspection.representations(None, "project", "run", "literal", 0)
    assert output["observation_jsonl"] == literal.decode()
    assert output["matches"] == []


def test_nonfinite_checksum_valid_jsonl_is_rejected_as_invalid_evidence(retained):
    add, _, _ = retained
    add("invalid", [{"value": float("nan")}])
    with pytest.raises(svc.LabError, match="invalid JSONL"):
        inspection.coverage(None, "project", "run")
