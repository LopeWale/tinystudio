"""Analytical fixtures for immutable evidence, causal transforms and split leakage."""
import hashlib
import json
from pathlib import Path

import pytest

from tinystudio.lab.data import execute_data


def rows_fixture():
    rows = []
    for partition, episode, start in [("train", "A", 0), ("validation", "B", 10), ("test", "C", 20)]:
        for offset in range(3):
            rows.append({"source_record_id": f"{episode}:{offset}", "episode_id": episode,
                "device_id": f"device-{episode}", "scenario_id": f"scenario-{episode}",
                "partition": partition, "time": start + offset * 2, "available_at": start + offset * 2,
                "clock_domain": "reference", "clock_units": "s", "clock_origin": "fixture-reference-start",
                "clock_session": "fixture-common-reference",
                "value": float(offset * 4), "units": "kPa", "observed": True})
    return rows


def spec(rows=None, stages=None, **parameters):
    return {"definition": {"engine": "data.pipeline", "seed": 3, "mode": "offline",
        "parameters": {"records": rows_fixture() if rows is None else rows, **parameters}, "pipeline": stages or []}}


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_causal_resampling_has_analytic_values_and_no_future_availability(tmp_path):
    result = execute_data(spec(stages=[{"kind": "resample", "parameters": {"dt": 1, "method": "forward_fill"}}]),
        tmp_path, lambda row: None, lambda: False)
    transformed = read_rows(tmp_path / "transformed.jsonl")
    train = [row for row in transformed if row["partition"] == "train"]
    assert [row["time"] for row in train] == [0, 1, 2, 3, 4]
    assert [row["value"] for row in train] == [0, 0, 4, 4, 8]
    assert train[1]["source_record_ids"] == ["A:0"]
    assert train[1]["available_at"] == 1
    assert train[1]["estimated"] and not train[1]["observed"]
    assert not train[1]["future_aware"]
    assert result["qualification"]["causal"]
    assert (tmp_path / "original-inline.jsonl").read_bytes() != (tmp_path / "transformed.jsonl").read_bytes()


def test_linear_interpolation_requires_label_and_records_future_inputs(tmp_path):
    stage = {"kind": "resample", "parameters": {"dt": 1, "method": "linear"}}
    with pytest.raises(ValueError, match="future_aware"):
        execute_data(spec(stages=[stage]), tmp_path / "unlabelled", lambda row: None, lambda: False)
    stage["parameters"]["future_aware"] = True
    result = execute_data(spec(stages=[stage]), tmp_path / "labelled", lambda row: None, lambda: False)
    row = read_rows(tmp_path / "labelled/transformed.jsonl")[1]
    assert row["value"] == 2
    assert row["available_at"] == 2 > row["time"]
    assert row["source_record_ids"] == ["A:0", "A:1"]
    assert row["future_aware"] is True
    assert result["qualification"]["causal"] is False


def test_missing_masks_survive_imputation_and_units_convert_explicitly(tmp_path):
    rows = rows_fixture()
    rows[1].update(value=None, observed=False, missing_mask={"value": True})
    stages = [{"kind": "impute", "parameters": {"method": "forward_fill"}},
              {"kind": "scale", "parameters": {"factor": 1000, "source_units": "kPa", "units": "Pa"}}]
    execute_data(spec(rows, stages), tmp_path, lambda row: None, lambda: False)
    transformed = read_rows(tmp_path / "transformed.jsonl")
    assert transformed[1]["value"] == 0
    assert transformed[1]["source_missing_mask"] == {"value": True}
    assert transformed[1]["missing_mask"] == {"value": False}
    assert transformed[1]["observed"] is False
    assert transformed[2]["value"] == 8000 and transformed[2]["units"] == "Pa"
    assert transformed[1]["source_record_ids"] == ["A:0", "A:1"]
    assert read_rows(tmp_path / "raw-normalized.jsonl")[1]["value"] is None


def test_train_only_imputation_excludes_heldout_outliers(tmp_path):
    rows = rows_fixture()
    rows[0]["value"], rows[1]["value"], rows[2]["value"] = 1, 2, 3
    rows[3].update(value=None, observed=False)
    rows[4]["value"], rows[5]["value"] = 1000, 10000
    execute_data(spec(rows, [{"kind": "impute", "parameters": {"method": "mean"}}]), tmp_path, lambda row: None, lambda: False)
    output = read_rows(tmp_path / "transformed.jsonl")
    imputed = next(row for row in output if row["source_record_id"] == "B:0")
    assert imputed["value"] == 2
    manifest = json.loads((tmp_path / "partitions.json").read_text())
    fit = manifest["fitted_transforms"][0]["by_units"]["kPa"]
    assert fit["source_record_ids"] == ["A:0", "A:1", "A:2"]
    assert fit["mean"] == 2
    assert imputed["future_aware"] is False


def test_connected_group_overlap_and_delayed_availability_are_rejected(tmp_path):
    rows = rows_fixture()
    rows[3]["device_id"] = rows[0]["device_id"]
    with pytest.raises(ValueError, match="overlaps"):
        execute_data(spec(rows), tmp_path / "groups", lambda row: None, lambda: False)
    rows = rows_fixture()
    rows[2]["available_at"] = 15
    with pytest.raises(ValueError, match="availability overlaps"):
        execute_data(spec(rows), tmp_path / "time", lambda row: None, lambda: False)


def test_temporal_purge_removes_entire_connected_group(tmp_path):
    rows = rows_fixture()
    extra = [{**rows[index], "source_record_id": f"D:{index}", "episode_id": "D", "device_id": "device-D", "scenario_id": "scenario-D",
              "time": 5 + index, "available_at": 15} for index in range(3)]
    rows += extra
    result = execute_data(spec(rows, partition_policy={"boundary_action": "purge"}), tmp_path, lambda row: None, lambda: False)
    assert result["metrics"]["purged_records"] == 3
    manifest = json.loads((tmp_path / "partitions.json").read_text())
    assert {row["source_record_id"] for row in manifest["purged"]} == {"D:0", "D:1", "D:2"}
    assert {row["episode_id"] for row in read_rows(tmp_path / "prepared-train.jsonl")} == {"A"}


def test_clock_alignment_is_explicit_and_retains_original_clock(tmp_path):
    rows = rows_fixture()
    for row in rows:
        row["clock_domain"] = "milliseconds"
        row["clock_units"] = "ms"
        row["time"] *= 1000
        row["available_at"] *= 1000
    execute_data(spec(rows, clock_alignment={"milliseconds": {"scale": 0.001, "offset": 7, "target": "reference",
        "units": "s", "clock_origin": "aligned-reference-start", "clock_session": "aligned-common-reference"}}),
        tmp_path, lambda row: None, lambda: False)
    output = read_rows(tmp_path / "transformed.jsonl")
    assert output[1]["time"] == 9
    assert output[1]["clock_alignment"]["source_time"] == 2000
    assert output[1]["clock_domain"] == "reference"
    rows[0]["clock_domain"] = "other-clock"
    with pytest.raises(ValueError, match="alignment"):
        execute_data(spec(rows), tmp_path / "bad", lambda row: None, lambda: False)


@pytest.mark.parametrize("format", ["jsonl", "csv", "parquet"])
def test_opaque_immutable_sources_import_without_losing_metadata(tmp_path, format):
    rows = rows_fixture()
    source = tmp_path / "opaque-hash-path"
    if format == "jsonl":
        source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    elif format == "csv":
        import csv
        with source.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    else:
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.Table.from_pylist(rows), source)
    experiment = spec(rows=[])
    experiment["definition"]["inputs"] = [{"artifact_id": "source-1", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]
    experiment["input_paths"] = {"source-1": str(source)}
    result = execute_data(experiment, tmp_path / "out", lambda row: None, lambda: False)
    assert result["metrics"]["source_records"] == 9
    retained = next(item for item in result["artifacts"] if item["role"] == "original_evidence")
    assert Path(retained["path"]).read_bytes() == source.read_bytes()
    output = read_rows(tmp_path / "out/transformed.jsonl")
    assert output[0]["source_record_id"] == "A:0"
    assert output[0]["device_id"] == "device-A"


def test_truth_separation_and_chat_partition_visibility(tmp_path):
    rows = rows_fixture()
    for row in rows:
        row.update(true_value=99, ground_truth={"fault": True}, injected_fault="oracle-fault")
        row["messages"] = [{"role": "user", "content": "Return the authored measurement."},
                           {"role": "assistant", "content": f"{row['value']} {row['units']}"}]
    result = execute_data(spec(rows, representation="chat"), tmp_path, lambda row: None, lambda: False)
    assert read_rows(tmp_path / "truth.jsonl")[0]["true_value"] == 99
    for filename in ("transformed.jsonl", "raw-normalized.jsonl", "chat-train.jsonl"):
        text = (tmp_path / filename).read_text()
        assert "oracle-fault" not in text and '"true_value"' not in text and '"ground_truth"' not in text
    train = read_rows(tmp_path / "chat-train.jsonl")
    assert {row["partition"] for row in train} == {"train"}
    assert all(row["visibility"] == "model" and row["source_record_id"].startswith("A:") for row in train)
    assert next(item for item in result["artifacts"] if item["role"] == "chat_test")["visibility"] == "evaluation"
    assert next(item for item in result["artifacts"] if item["role"] == "separated_truth")["visibility"] == "truth"
    assert next(item for item in result["artifacts"] if item["role"] == "original_evidence")["visibility"] == "truth"


def test_filter_preserves_gaps_and_reports_actual_units(tmp_path):
    rows = rows_fixture()
    rows[1].update(value=None, observed=False)
    execute_data(spec(rows, [{"kind": "moving_average", "parameters": {"window": 2}}]), tmp_path, lambda row: None, lambda: False)
    output = read_rows(tmp_path / "transformed.jsonl")
    assert output[1]["value"] is None
    assert output[2]["value"] == 8
    profile = json.loads((tmp_path / "profile.json").read_text())
    assert profile["transformed"]["missing_values"] == 1
    assert set(profile["transformed"]["units"]) == {"kPa"}


def test_unpartitioned_shared_id_components_stay_together_in_time(tmp_path):
    rows = rows_fixture()
    for row in rows:
        row.pop("partition")
    # A and D connect through their device; D and E connect through scenario.
    extra = []
    for episode, start, device, scenario in [("D", 5, "device-A", "bridge"), ("E", 6, "device-E", "bridge"), ("F", 30, "device-F", "scenario-F")]:
        extra.append({**rows[0], "source_record_id": f"{episode}:0", "episode_id": episode, "device_id": device,
                      "scenario_id": scenario, "time": start, "available_at": start})
    rows += extra
    execute_data(spec(rows, partition_policy={"group_by": ["episode_id", "device_id", "scenario_id"]}),
                 tmp_path, lambda row: None, lambda: False)
    output = read_rows(tmp_path / "transformed.jsonl")
    assert {row["partition"] for row in output if row["episode_id"] in {"A", "D", "E"}} == {"train"}
    manifest = json.loads((tmp_path / "partitions.json").read_text())
    assert all(not overlap for overlap in manifest["overlap_report"].values())
    assert manifest["partitions"]["train"]["available_at_max"] < manifest["partitions"]["validation"]["event_time_min"]


def test_mixed_units_and_altered_evidence_are_rejected(tmp_path):
    rows = rows_fixture()
    rows[1]["units"] = "bar"
    with pytest.raises(ValueError, match="mix units"):
        execute_data(spec(rows), tmp_path / "units", lambda row: None, lambda: False)
    source = tmp_path / "data.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows_fixture()))
    experiment = spec(rows=[])
    experiment["definition"]["inputs"] = [{"artifact_id": "source", "sha256": "0" * 64}]
    experiment["input_paths"] = {"source": str(source)}
    with pytest.raises(ValueError, match="checksum"):
        execute_data(experiment, tmp_path / "bad", lambda row: None, lambda: False)
