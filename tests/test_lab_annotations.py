"""Supervision is an explicit, versioned mapping to the observation reviewed."""
import json
from copy import deepcopy

import pytest

from tinystudio.lab.data import execute_data


def definition():
    return {"engine": "data.pipeline", "mode": "offline", "parameters": {"records": [
        {"source_record_id": "a", "episode_id": "E", "time": 0, "available_at": 0, "value": 0, "units": "V",
         "messages": [{"role": "user", "content": "Read at 0"}, {"role": "assistant", "content": "0 V"}]},
        {"source_record_id": "b", "episode_id": "E", "time": 2, "available_at": 2, "value": 4, "units": "V",
         "messages": [{"role": "user", "content": "Read at 2"}, {"role": "assistant", "content": "4 V"}]}]},
        "pipeline": [{"kind": "resample", "parameters": {"dt": 1, "method": "linear", "future_aware": True}}]}


def execute(tmp_path, body):
    result = execute_data({"definition": body}, tmp_path, lambda event: None, lambda: False)
    rows = [json.loads(line) for line in (tmp_path / "transformed.jsonl").read_text().splitlines()]
    return result, rows


def annotation(row):
    return {"record_id": row["record_id"], "observation_sha256": row["observation_sha256"],
            "labels": {"state": "interpolated"},
            "label_source": {"kind": "human", "actor": "fixture-reviewer", "reference": "review:1"},
            "target_available_at": 2,
            "messages": [{"role": "user", "content": "Read interpolated voltage at 1"},
                         {"role": "assistant", "content": "2 V"}]}


def test_resampled_observation_requires_a_matching_explicit_supervision_mapping(tmp_path):
    body = definition()
    _, initial = execute(tmp_path / "inspect", body)
    mapping = annotation(initial[1])
    body["parameters"].update(annotations=[mapping], representation="chat")
    result, prepared = execute(tmp_path / "labeled", body)
    middle = prepared[1]
    assert middle["value"] == 2
    assert middle["labels"] == {"state": "interpolated"}
    assert middle["label_source"] == mapping["label_source"]
    assert middle["target_available_at"] == 2
    assert middle["messages"] == mapping["messages"]
    assert middle["supervision_status"]["status"] == "explicitly_mapped"
    chats = [json.loads(line) for line in (tmp_path / "labeled/chat-evidence.jsonl").read_text().splitlines()]
    assert chats[1]["message_origin"] == "explicit_annotation"
    assert chats[1]["messages"] == mapping["messages"]
    assert chats[1]["source_record_ids"] == ["a", "b"]
    manifest = json.loads((tmp_path / "labeled/partitions.json").read_text())
    assert manifest["annotations"]["applied"] == 1
    assert result["qualification"]["authored_annotations"] == 1


def test_annotation_becomes_stale_when_the_scientific_observation_changes(tmp_path):
    body = definition()
    _, initial = execute(tmp_path / "inspect", body)
    body["parameters"]["annotations"] = [annotation(initial[1])]
    body["pipeline"].append({"kind": "scale", "parameters": {"factor": 2}})
    with pytest.raises(ValueError, match="annotation.*changed|annotation.*checksum"):
        execute(tmp_path / "changed", body)


@pytest.mark.parametrize("change", [{"label_source": {}}, {"observation_sha256": "0" * 64},
                                    {"record_id": "missing"}])
def test_annotation_cannot_silently_target_unreviewed_or_missing_evidence(tmp_path, change):
    body = definition()
    _, initial = execute(tmp_path / "inspect", body)
    body["parameters"]["annotations"] = [{**annotation(initial[1]), **change}]
    with pytest.raises(ValueError, match="annotation|label source"):
        execute(tmp_path / "reject", body)


def test_duplicate_annotations_are_rejected_instead_of_last_writer_wins(tmp_path):
    body = definition()
    _, initial = execute(tmp_path / "inspect", body)
    mapping = annotation(initial[1])
    body["parameters"]["annotations"] = [mapping, deepcopy(mapping)]
    with pytest.raises(ValueError, match="duplicate annotation"):
        execute(tmp_path / "reject", body)


def test_mapping_rejects_changed_fit_dependencies_even_when_value_is_unchanged(tmp_path):
    from tinystudio.lab.annotations import apply_annotations
    _, rows = execute(tmp_path / "inspect", definition())
    original = rows[1]
    changed = {**original, "fit_dependencies": [{"stage": 1, "sha256": "a" * 64, "fit_partition": "train"}]}
    assert changed["value"] == original["value"]
    with pytest.raises(ValueError, match="checksum changed"):
        apply_annotations([changed], [annotation(original)])


@pytest.mark.parametrize("field", ["clock_origin", "clock_epoch", "time_origin", "clock_session", "session_id", "clock_reset_id"])
def test_annotation_invalidates_when_only_clock_interpretation_changes(tmp_path, field):
    from tinystudio.lab.annotations import apply_annotations
    _, rows = execute(tmp_path / "inspect", definition())
    changed = {**rows[1], field: "changed-clock-reference"}
    with pytest.raises(ValueError, match="checksum changed"):
        apply_annotations([changed], [annotation(rows[1])])
