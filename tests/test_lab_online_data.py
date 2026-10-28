"""Arrival replay uses the information available at the recorded decision."""
import json

import pytest

from tinystudio.lab.data import execute_data


def arrivals(late_value=100):
    return [
        {"source_record_id": "first", "episode_id": "E", "time": 0, "available_at": 0,
         "value": 1, "units": "V", "clock_domain": "clock"},
        {"source_record_id": "late", "episode_id": "E", "time": 1, "available_at": 3,
         "value": late_value, "units": "V", "clock_domain": "clock"},
        {"source_record_id": "gap", "episode_id": "E", "time": 2, "available_at": 2,
         "value": None, "observed": False, "units": "V", "clock_domain": "clock"},
    ]


def run(tmp_path, rows=None, stages=None, **params):
    result = execute_data({"definition": {"engine": "data.pipeline", "mode": "online",
                          "parameters": {"records": arrivals() if rows is None else rows, **params},
                          "pipeline": [{"kind": "impute", "parameters": {"method": "forward_fill"}}]
                                      if stages is None else stages}},
                         tmp_path, lambda event: None, lambda: False)
    output = [json.loads(line) for line in (tmp_path / "transformed.jsonl").read_text().splitlines()]
    return result, output


def test_late_measurement_is_not_used_before_arrival(tmp_path):
    result, rows = run(tmp_path)
    assert [row["source_record_id"] for row in rows] == ["first", "gap", "late"]
    gap = rows[1]
    assert gap["value"] == 1 and gap["source_record_ids"] == ["first", "gap"]
    assert gap["source_missing_mask"] == {"value": True} and gap["missing_mask"] == {"value": False}
    assert gap["decision_at"] == gap["time"] == 2 and gap["event_time"] == 2
    assert rows[2]["event_time"] == 1 and rows[2]["time"] == rows[2]["available_at"] == 3
    assert all(row["available_at"] <= row["decision_at"] and not row["future_aware"] for row in rows)
    assert result["qualification"]["online_replay"]["timeline_basis"] == "availability"
    assert result["qualification"]["online_replay"]["live_acquisition"] is False


@pytest.mark.parametrize("future", [arrivals(900), arrivals() + [
    {"source_record_id": "future", "episode_id": "E", "time": 4, "available_at": 4,
     "value": -500, "units": "V", "clock_domain": "clock"}],
    [row for row in arrivals() if row["source_record_id"] != "late"]])
def test_future_changes_additions_and_deletions_leave_decision_prefix_unchanged(tmp_path, future):
    _, baseline = run(tmp_path / "baseline")
    _, modified = run(tmp_path / "modified", future)
    # A changed original file must have a new checksum. Causal observations and
    # donor identities, rather than the containing file's identity, stay fixed.
    fields = ("value", "available_at", "decision_at", "event_time", "observed", "estimated",
              "quality", "units", "source_record_ids", "missing_mask", "source_missing_mask",
              "arrival_ordinal", "future_aware", "clock_domain")
    prefix = lambda rows: [{field: row[field] for field in fields} for row in rows if row["decision_at"] <= 2]
    assert prefix(modified) == prefix(baseline)
    assert modified[0]["source_sha256"] != baseline[0]["source_sha256"]


def test_online_window_uses_arrival_order_and_records_tie_policy(tmp_path):
    rows = arrivals()
    rows[-1].update(value=3, observed=True)
    _, result = run(tmp_path, rows, [{"kind": "moving_average", "parameters": {"window": 2}}])
    assert [row["value"] for row in result] == [1, 2, 51.5]
    assert [row["arrival_ordinal"] for row in result] == [0, 1, 2]
    assert {row["tie_order"] for row in result} == {"event_time_then_source_record_id"}


@pytest.mark.parametrize("stage", [{"kind": "resample", "parameters": {"dt": 1}},
    {"kind": "standardize", "parameters": {}}, {"kind": "impute", "parameters": {"method": "mean"}}])
def test_online_rejects_operations_without_a_causal_contract(tmp_path, stage):
    with pytest.raises(ValueError, match="online replay"):
        run(tmp_path, stages=[stage])


def test_online_does_not_clean_using_a_later_duplicate(tmp_path):
    rows = arrivals() + [{**arrivals()[0], "source_record_id": "correction", "available_at": 4, "value": 99}]
    with pytest.raises(ValueError, match="duplicate"):
        run(tmp_path, rows, cleaning={"duplicates": "last"})


def test_future_aware_source_is_rejected_online(tmp_path):
    rows = arrivals()
    rows[0]["future_aware"] = True
    with pytest.raises(ValueError, match="future-aware"):
        run(tmp_path, rows)


def test_missing_availability_requires_explicit_qualified_assumption(tmp_path):
    rows = arrivals()
    rows[0].pop("available_at")
    with pytest.raises(ValueError, match="availability"):
        run(tmp_path / "reject", rows)
    result, _ = run(tmp_path / "selected", rows,
                    online_policy={"allow_assumed_availability": True, "assumption_reason": "Synthetic zero-delay reference"})
    assert result["qualification"]["online_replay"]["assumed_availability_records"] == 1
    assert result["qualification"]["online_replay"]["arrival_measurements_qualified"] is False


def test_incomparable_availability_clock_is_not_treated_as_event_clock(tmp_path):
    rows = arrivals()
    rows[0]["availability_clock_domain"] = "unmapped-other-clock"
    with pytest.raises(ValueError, match="availability clock"):
        run(tmp_path, rows)


def test_same_arrival_tie_order_is_deterministic_without_claiming_measured_order(tmp_path):
    rows = arrivals()
    rows[1]["available_at"] = 2
    _, left = run(tmp_path / "left", rows)
    _, right = run(tmp_path / "right", list(reversed(rows)))
    assert [(row["source_record_id"], row["value"], row["arrival_ordinal"]) for row in left] == [
        (row["source_record_id"], row["value"], row["arrival_ordinal"]) for row in right]
    assert left[-1]["value"] == 100


def test_incompatible_online_operation_is_explained_before_submission():
    from tinystudio.lab.contracts import ExperimentDefinition
    from tinystudio.lab.preflight import execution_checks
    definition = ExperimentDefinition(name="arrival replay", engine="data.pipeline", mode="online",
        execution={"profile": "trusted_native"}, pipeline=[{"kind": "resample", "parameters": {"dt": 1}}])
    caps = {"engines": ["data.pipeline"], "runtime": {"python": "3.12"}}
    checks = {item["code"]: item for item in execution_checks(definition, caps)}
    assert checks["data_pipeline"]["passed"] is False
    assert "horizon" in checks["data_pipeline"]["message"]
    definition.pipeline = [{"kind": "impute", "parameters": {"method": "forward_fill"}}]
    assert all(item["passed"] for item in execution_checks(definition, caps))
