"""Frozen observation selection, provenance and explicit inference-unit decisions."""
from __future__ import annotations

import hashlib
import importlib.util
import json

import pytest

from tinystudio.lab.contracts import canonical, checksum


def module():
    assert importlib.util.find_spec("tinystudio.lab.analysis_selection") is not None, "observed-data selection is missing"
    from tinystudio.lab import analysis_selection
    return analysis_selection


def parameters(**patch):
    return {"method": "means.welch", "confidence": .95, "alternative": "two-sided",
            "selection": {"mode": "all", "record_ids": [], "exclude_ids": [], "kinds": ["observation"]},
            "outcome_path": "/data/response", "condition_path": "/condition", "unit_path": "/unit_id",
            "measurement_unit": "V", "repeated": "error", "missing": "error", **patch}


def ledger(tmp_path, rows=None):
    protocol = {"schema_version": 1, "study_id": "study", "revision": 1,
                "study": {"name": "Emulated observations", "independent_unit": "researcher-declared device"},
                "author_id": "author", "recorded_at": "2026-11-13T00:00:00+00:00"}
    records = []
    for index, item in enumerate(rows or [("a1", "A", 1), ("a2", "A", 3), ("b1", "B", 2), ("b2", "B", 4)], 1):
        unit, condition, value = item[:3]
        record = {"schema_version": 1, "id": "r"+str(index), "study_id": "study", "seq": index,
                  "revision": 1, "protocol_digest": checksum(protocol), "kind": "observation", "unit_id": unit,
                  "condition": condition, "data": {"response": value, "source": {"kind": "emulated", "emulator": "sil", "seed": 7}},
                  "observed_at": "2026-11-13T00:00:00+00:00", "supersedes": item[3] if len(item) > 3 else None,
                  "run_references": [], "artifact_references": [], "author_id": "author",
                  "recorded_at": "2026-11-13T00:00:00+00:00"}
        records.append({**record, "digest": checksum(record)})
    header = {"format": "tinystudio.study-tracking.v1", "kind": "manifest", "study_id": "study",
              "revision": 1, "through_seq": len(records), "protocol_digest": checksum(protocol), "external_evidence_bytes": False}
    documents = [header, {"kind": "protocol", "snapshot": protocol, "digest": checksum(protocol)},
                 *[{"kind": "record", "record": row} for row in records]]
    prefix = b"".join(canonical(document)+b"\n" for document in documents)
    footer = {"kind": "export_summary", "sha256": hashlib.sha256(prefix).hexdigest(),
              "protocol_revisions": 1, "records": len(records)}
    path = tmp_path / "source.jsonl"
    path.write_bytes(prefix+canonical(footer)+b"\n")
    return path, records


def prepare(path, params, output):
    return module().prepare_selection(path, params, output)


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_small_study_selection_retains_source_provenance_without_training_requirements(tmp_path):
    source, records = ledger(tmp_path)
    selected = prepare(source, parameters(groups=["A", "B"]), tmp_path / "analysis")
    assert selected.group_a == [1, 3] and selected.group_b == [2, 4]
    assert selected.source["study_id"] == "study" and selected.source["through_seq"] == 4
    assert selected.source["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    retained = lines(selected.paths.observations)
    assert retained == records
    assert retained[0]["data"]["source"] == {"kind": "emulated", "emulator": "sil", "seed": 7}
    decisions = [row for row in lines(selected.paths.selection) if row.get("kind") == "decision"]
    assert [row["record_id"] for row in decisions if row["used"]] == ["r1", "r2", "r3", "r4"]


def test_corrections_are_explicit_selection_not_automatic_replacement(tmp_path):
    source, _ = ledger(tmp_path, [("a1", "A", 1), ("a2", "A", 3), ("b1", "B", 2), ("b2", "B", 4), ("a1", "A", 99, "r1")])
    params = parameters(groups=["A", "B"], selection={"mode": "ids", "record_ids": ["r2", "r3", "r4", "r5"], "exclude_ids": [], "kinds": []})
    selected = prepare(source, params, tmp_path / "selected")
    assert selected.group_a == [3, 99]
    decisions = [row for row in lines(selected.paths.selection) if row.get("kind") == "decision"]
    assert decisions[0]["selected"] is False
    with pytest.raises(ValueError, match="repeated"):
        prepare(source, parameters(groups=["A", "B"]), tmp_path / "all")
    averaged = prepare(source, parameters(groups=["A", "B"], repeated="mean"), tmp_path / "mean")
    assert averaged.group_a == [50, 3]
    assert any(row["code"] == "included_corrections" for row in averaged.diagnostics)


def test_pair_alignment_and_missing_pairs_require_explicit_policy(tmp_path):
    source, _ = ledger(tmp_path, [("u2", "B", 8), ("u1", "A", 4), ("u2", "A", 6), ("u1", "B", 3), ("u3", "A", 100)])
    params = parameters(method="means.paired", groups=["A", "B"], incomplete_pairs="error")
    with pytest.raises(ValueError, match="incomplete"):
        prepare(source, params, tmp_path / "error")
    selected = prepare(source, {**params, "incomplete_pairs": "omit"}, tmp_path / "omitted")
    assert sorted(zip(selected.group_a, selected.group_b)) == [(4, 3), (6, 8)]
    decisions = [row for row in lines(selected.paths.selection) if row.get("kind") == "decision"]
    assert decisions[-1]["used"] is False and decisions[-1]["reason"] == "incomplete_pair"


def test_missing_outcomes_are_retained_as_explicit_exclusions(tmp_path):
    source, _ = ledger(tmp_path, [("a1", "A", None), ("a2", "A", 3), ("b1", "B", 2), ("b2", "B", 4)])
    with pytest.raises(ValueError, match="outcome"):
        prepare(source, parameters(groups=["A", "B"]), tmp_path / "error")
    selected = prepare(source, parameters(groups=["A", "B"], missing="omit"), tmp_path / "omit")
    assert selected.group_a == [3] and selected.group_b == [2, 4]
    decisions = [row for row in lines(selected.paths.selection) if row.get("kind") == "decision"]
    assert decisions[0]["selected"] is False and decisions[0]["reason"] == "missing_outcome"


def test_source_checksum_trailing_bytes_and_missing_selected_ids_are_rejected(tmp_path):
    source, _ = ledger(tmp_path)
    original = source.read_bytes()
    source.write_bytes(original.replace(b'"response":1', b'"response":9'))
    with pytest.raises(ValueError, match="checksum|digest"):
        prepare(source, parameters(groups=["A", "B"]), tmp_path / "corrupt")
    source.write_bytes(original+b"{}\n")
    with pytest.raises(ValueError, match="trailing|after.*summary"):
        prepare(source, parameters(groups=["A", "B"]), tmp_path / "trailing")
    source.write_bytes(original)
    with pytest.raises(ValueError, match="selected.*not found|unknown.*record"):
        prepare(source, parameters(groups=["A", "B"], selection={"mode": "ids", "record_ids": ["missing"], "exclude_ids": [], "kinds": []}), tmp_path / "unknown")


@pytest.mark.parametrize("patch", [{"confidence": True}, {"confidence": 1}, {"groups": ["A", "A"]},
    {"outcome_path": "data.value"}, {"unit_path": "/data/~2invalid"}, {"repeated": "automatic"}, {"missing": "automatic"}])
def test_analysis_choices_are_validated_without_guessing(patch):
    request = parameters(groups=["A", "B"])
    request.update(patch)
    with pytest.raises(ValueError):
        module().normalize_request(request)


def test_decimal_outcome_conversion_is_disclosed_without_changing_retained_values(tmp_path):
    source, records = ledger(tmp_path, [("a1", "A", "9007199254740993"), ("a2", "A", "0.1"), ("b1", "B", "2")])
    selected = prepare(source, parameters(groups=["A", "B"]), tmp_path / "converted")
    assert lines(selected.paths.observations) == records
    decisions = [row for row in lines(selected.paths.selection) if row.get("kind") == "decision"]
    conversions = [row["numeric_conversion"] for row in decisions]
    assert [row["rounded"] for row in conversions] == [True, True, False]
    assert conversions[0]["source_decimal"] == "9007199254740993"
    assert float.fromhex(conversions[0]["float64_hex"]) == selected.group_a[0]
    diagnostic = next(row for row in selected.diagnostics if row["code"] == "float64_rounding")
    assert diagnostic["records"] == 2


@pytest.mark.parametrize("field,value", [("assumptions", "unchecked"), ("references", [1])])
def test_custom_method_metadata_has_a_portable_contract(field, value):
    request = {"method": "custom.python", "selection": {"mode": "all"},
               "custom_method": {"name": "Custom", "version": "1", field: value}}
    with pytest.raises(ValueError, match=field):
        module().normalize_request(request)


def test_request_preserves_unsafe_integers_as_decimal_strings():
    request = {"method": "custom.python", "selection": {"mode": "all"},
               "custom_method": {"name": "Custom", "version": "1"}, "options": {"seed": 2**53+1}}
    with pytest.raises(ValueError, match="decimal string|exact JSON"):
        module().normalize_request(request)
    request["options"]["seed"] = str(2**53+1)
    assert module().normalize_request(request)["options"]["seed"] == str(2**53+1)


@pytest.mark.parametrize("target,field,value", [("manifest", "study_id", None), ("protocol", "revision", True),
    ("record", "seq", True), ("record", "revision", True), ("record", "id", None),
    ("record", "supersedes", "r4"), ("export_summary", "protocol_revisions", True)])
def test_snapshot_identity_validation_rejects_rehashed_invalid_history(tmp_path, target, field, value):
    source, _ = ledger(tmp_path)
    documents = lines(source)
    document = next(row for row in documents if row["kind"] == target)
    body = document["snapshot"] if target == "protocol" else document["record"] if target == "record" else document
    body[field] = value
    if target in {"protocol", "record"}:
        if target == "record":
            body["digest"] = checksum({key: item for key, item in body.items() if key != "digest"})
        else:
            document["digest"] = checksum(body)
    prefix = b"".join(canonical(row)+b"\n" for row in documents[:-1])
    documents[-1]["sha256"] = hashlib.sha256(prefix).hexdigest()
    source.write_bytes(prefix+canonical(documents[-1])+b"\n")
    with pytest.raises(ValueError, match="identity|sequence|boundary|protocol|correction"):
        module().validate_snapshot(source)
