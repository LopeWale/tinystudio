"""C workflow expectations, independent of training-dataset construction."""
import json
import hashlib

import pytest

from tinystudio.lab.data import execute_data
from tinystudio.lab import bridge
from tinystudio.lab.service import LabError


def recording():
    return [
        {"source_record_id": "sensor:0", "episode_id": "capture", "time": 0,
         "clock_domain": "sensor-clock", "value": -1.25, "units": "V"},
        {"source_record_id": "sensor:1", "episode_id": "capture", "time": 1,
         "clock_domain": "sensor-clock", "value": None, "units": "V",
         "observed": False, "missing_mask": {"value": True}},
        {"source_record_id": "sensor:2", "episode_id": "capture", "time": 2,
         "clock_domain": "sensor-clock", "value": 0.125, "units": "V"},
    ]


def run_recording(tmp_path, rows=None, **parameters):
    return execute_data({"definition": {
        "engine": "data.pipeline", "mode": "offline", "seed": 42,
        "parameters": {"records": recording() if rows is None else rows, **parameters},
        "pipeline": [{"kind": "impute", "parameters": {"method": "forward_fill"}}],
    }}, tmp_path, lambda event: None, lambda: False)


def output_rows(tmp_path):
    return [json.loads(line) for line in (tmp_path / "transformed.jsonl").read_text().splitlines()]


def test_one_recording_can_be_cleaned_before_a_dataset_is_requested(tmp_path):
    result = run_recording(tmp_path)
    rows = output_rows(tmp_path)
    assert [row["value"] for row in rows] == [-1.25, -1.25, 0.125]
    assert [row["source_record_id"] for row in rows] == ["sensor:0", "sensor:1", "sensor:2"]
    assert rows[1]["source_missing_mask"] == {"value": True}
    assert rows[1]["missing_mask"] == {"value": False}
    assert rows[1]["observed"] is False and rows[1]["estimated"] is True
    assert result["qualification"]["dataset_ready"] is False
    assert all(item["visibility"] != "model" for item in result["artifacts"])
    assert not any(item["role"].startswith("prepared_") for item in result["artifacts"])


def test_source_final_test_marker_survives_preparation_without_partitioning(tmp_path):
    rows = [{**row, "partition": "test"} for row in recording()]
    result = run_recording(tmp_path, rows)
    assert {row["partition"] for row in output_rows(tmp_path)} == {"test"}
    assert result["qualification"]["dataset_ready"] is False
    assert all(item["visibility"] != "model" for item in result["artifacts"])


def test_dataset_request_still_requires_disjoint_heldout_groups(tmp_path):
    with pytest.raises(ValueError, match="disjoint.*groups"):
        run_recording(tmp_path, partition_policy={"strategy": "grouped", "group_by": ["episode_id"]})


def dataset_rows(*, messages=False):
    rows = []
    for partition, episode, start in [("train", "A", 0), ("validation", "B", 10), ("test", "C", 20)]:
        for index in range(2):
            row = {"source_record_id": f"{episode}:{index}", "episode_id": episode,
                   "partition": partition, "time": start + index * 2, "value": index * 4,
                   "units": "V", "clock_domain": "reference", "clock_units": "s",
                   "clock_origin": "workflow-shared-start", "clock_session": "workflow-shared-session"}
            if messages:
                row["messages"] = [{"role": "user", "content": f"Return raw measurement {episode}:{index}."},
                                   {"role": "assistant", "content": f"{index * 4} V", "authored_by": "fixture"}]
            rows.append(row)
    return rows


@pytest.mark.parametrize("visibility", ["evaluation", "truth"])
def test_source_binding_protection_cannot_become_training_visibility(tmp_path, visibility):
    source = tmp_path / "protected.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in dataset_rows()))
    definition = {"engine": "data.pipeline", "mode": "offline", "parameters": {},
                  "inputs": [{"artifact_id": "protected", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                              "role": "source", "visibility": visibility}]}
    with pytest.raises(ValueError, match="bound observations"):
        execute_data({"definition": definition, "input_paths": {"protected": str(source)}},
                     tmp_path / "out", lambda event: None, lambda: False)


@pytest.mark.parametrize("markers", [
    {"partition": "train", "bucket": "final"},
    {"partition": "train", "split": "held_out"},
    {"partition": "train", "bucket": "ground-truth"},
])
def test_chat_bridge_rejects_conflicting_protected_aliases(markers):
    row = {**dataset_rows(messages=True)[0], **markers}
    with pytest.raises(LabError, match="partition"):
        bridge.chat_rows([row])


def test_partial_labels_do_not_fall_back_to_an_unassigned_capture(tmp_path):
    rows = recording()
    rows[0]["partition"] = "test"
    with pytest.raises(ValueError, match="every row must declare"):
        run_recording(tmp_path, rows)


def test_training_only_fit_requires_a_validated_dataset(tmp_path):
    spec = {"definition": {"engine": "data.pipeline", "mode": "offline",
            "parameters": {"records": recording()},
            "pipeline": [{"kind": "impute", "parameters": {"method": "mean"}}]}}
    with pytest.raises(ValueError, match="validated training partition"):
        execute_data(spec, tmp_path, lambda event: None, lambda: False)


def test_chat_export_does_not_invent_answers(tmp_path):
    spec = {"definition": {"engine": "data.pipeline", "mode": "offline",
            "parameters": {"records": dataset_rows(), "representation": "chat"}}}
    with pytest.raises(ValueError, match="explicit authored messages"):
        execute_data(spec, tmp_path, lambda event: None, lambda: False)


def test_authored_answers_are_not_repeated_onto_resampled_observations(tmp_path):
    spec = {"definition": {"engine": "data.pipeline", "mode": "offline",
            "parameters": {"records": dataset_rows(messages=True), "representation": "chat"},
            "pipeline": [{"kind": "resample", "parameters": {"dt": 1}}]}}
    with pytest.raises(ValueError, match="explicit supervision mapping"):
        execute_data(spec, tmp_path, lambda event: None, lambda: False)


def test_authored_messages_and_lineage_survive_without_fabrication(tmp_path):
    rows = dataset_rows(messages=True)
    result = execute_data({"definition": {"engine": "data.pipeline", "mode": "offline",
                          "parameters": {"records": rows, "representation": "chat"}}},
                         tmp_path, lambda event: None, lambda: False)
    actual = [json.loads(line) for line in (tmp_path / "chat-dataset.jsonl").read_text().splitlines()]
    assert [row["messages"] for row in actual] == [row["messages"] for row in rows]
    assert [row["message_source_record_id"] for row in actual] == [row["source_record_id"] for row in rows]
    assert {row["message_origin"] for row in actual} == {"authored_source"}
    assert result["qualification"]["dataset_ready"] is True
    combined = next(item for item in result["artifacts"] if item["role"] == "supervised_dataset")
    assert combined["visibility"] == "evaluation"
    assert {row["partition"] for row in actual if row["visibility"] == "model"} == {"train"}


def test_numeric_export_invalidates_answers_on_changed_observations(tmp_path):
    result = execute_data({"definition": {"engine": "data.pipeline", "mode": "offline",
                          "parameters": {"records": dataset_rows(messages=True)},
                          "pipeline": [{"kind": "resample", "parameters": {"dt": 1}}]}},
                         tmp_path, lambda event: None, lambda: False)
    rows = output_rows(tmp_path)
    derived = [row for row in rows if row["quality"] == "resampled"]
    assert derived and all("messages" not in row for row in derived)
    assert all(row["supervision_status"]["status"] == "invalidated" for row in derived)
    original = [json.loads(line) for line in (tmp_path / "original-inline.jsonl").read_text().splitlines()]
    assert all(row["messages"] for row in original)
    prepared = [json.loads(line) for line in (tmp_path / "prepared-train.jsonl").read_text().splitlines()]
    assert all("messages" not in row for row in prepared if row["quality"] == "resampled")
    assert result["qualification"]["dataset_ready"]


@pytest.mark.parametrize("separate_source", [False, True])
def test_discarded_truth_record_keeps_original_artifact_restricted(tmp_path, separate_source):
    # These intentionally duplicated captures have a declared shared clock.
    safe = {**recording()[0], "source_record_id": "a-safe", "clock_units": "s",
            "clock_origin": "duplicate-fixture-start", "clock_session": "duplicate-fixture-session"}
    truth = {**safe, "source_record_id": "z-truth", "visibility": "truth"}
    definition = {"engine": "data.pipeline", "mode": "offline",
                  "parameters": {"cleaning": {"duplicates": "first"}}}
    paths = {}
    if separate_source:
        definition["inputs"] = []
        for name, rows in [("safe", [safe]), ("secret", [truth])]:
            path = tmp_path / f"{name}.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            definition["inputs"].append({"artifact_id": name, "role": "source", "visibility": "model",
                                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            paths[name] = str(path)
    else:
        definition["parameters"]["records"] = [safe, truth]
    result = execute_data({"definition": definition, "input_paths": paths}, tmp_path / "out",
                         lambda event: None, lambda: False)
    originals = [item for item in result["artifacts"] if item["role"] == "original_evidence"]
    restricted = originals[-1]
    assert restricted["visibility"] == "truth"
    assert len(output_rows(tmp_path / "out")) == 1


def test_evaluation_bound_preparation_cannot_be_serialized_as_training(tmp_path):
    rows = [{**row, "partition": "train", "visibility": "evaluation"}
            for row in dataset_rows(messages=True)[:2]]
    execute_data({"definition": {"engine": "data.pipeline", "mode": "offline",
                  "parameters": {"records": rows, "representation": "chat"}}},
                 tmp_path, lambda event: None, lambda: False)
    chats = [json.loads(line) for line in (tmp_path / "chat-evidence.jsonl").read_text().splitlines()]
    with pytest.raises(LabError, match="evaluation.*training"):
        bridge.chat_rows(chats)
