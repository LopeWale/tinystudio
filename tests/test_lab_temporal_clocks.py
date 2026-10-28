"""Temporal isolation requires a shared coordinate, not equal numeric labels."""
import pytest

from tinystudio.lab.data import partition_records


def records():
    return [{"source_record_id": f"{episode}-{sample}", "episode_id": episode,
             "time": offset + sample, "available_at": offset + sample,
             "clock_domain": "capture", "clock_units": "s", "clock_origin": "start",
             "clock_session": "session", "clock_reset_id": "reset"}
            for episode, offset in zip("ABC", (0, 10, 20)) for sample in (0, 1)]


@pytest.mark.parametrize("field,value", [("clock_origin", "another-start"), ("clock_units", "ms"),
    ("clock_session", "another-session"), ("clock_reset_id", "another-reset")])
def test_temporal_rejects_incompatible_coordinates_despite_shared_domain(field, value):
    rows = records()
    rows[-1][field] = value
    with pytest.raises(ValueError, match="grouped.*alignment"):
        partition_records(rows, {"partition_policy": {"strategy": "temporal"}}, 42)


def test_temporal_rejects_incomplete_clock_even_without_capture_identity():
    rows = records()
    for row in rows:
        del row["clock_origin"]
        del row["clock_session"]
    with pytest.raises(ValueError, match="grouped.*alignment"):
        partition_records(rows, {"partition_policy": {"strategy": "temporal"}}, 42)


def test_qualified_shared_clock_preserves_temporal_isolation_and_labels():
    rows = records()
    for row in rows:
        row["partition"] = {"A": "train", "B": "validation", "C": "test"}[row["episode_id"]]
    actual, manifest = partition_records(rows, {"partition_policy": {"strategy": "temporal"}}, 42)
    assert [row["partition"] for row in actual] == [row["partition"] for row in rows]
    assert manifest["temporal_isolation"] is True
    assert manifest["partitions"]["train"]["available_at_max"] == 1
    assert manifest["partitions"]["validation"]["event_time_min"] == 10


def test_grouped_independent_coordinates_do_not_report_aggregate_numeric_bounds():
    rows = records()
    for row in rows:
        row["clock_origin"] = row["episode_id"]
    actual, manifest = partition_records(rows, {"partition_policy": {"strategy": "grouped"}}, 42)
    assert len(actual) == len(rows)
    assert manifest["temporal_isolation"] is False
    assert manifest["clock_domain"] is None
    for partition in manifest["partitions"].values():
        assert partition["event_time_min"] is None
        assert partition["available_at_max"] is None
        assert partition["clock_bounds"]
        assert set(partition["clock_bounds"]) <= set(manifest["clock_references"])


def test_affine_alignment_uses_explicit_target_coordinate_and_preserves_source(tmp_path):
    import json
    from tinystudio.lab.data import execute_data

    rows = records()
    for row in rows:
        row.update(value=1, units="V", clock_domain=f"device-{row['episode_id']}",
                   clock_units="ms", clock_origin=f"start-{row['episode_id']}",
                   clock_session=f"session-{row['episode_id']}", clock_reset_id=f"reset-{row['episode_id']}")
        row["time"] *= 1000
        row["available_at"] *= 1000
    target = {"scale": 0.001, "offset": 5, "target": "aligned", "units": "s",
              "clock_origin": "aligned-start", "clock_session": "aligned-session", "clock_reset_id": "aligned-reset"}
    parameters = {"records": rows, "partition_policy": {"strategy": "temporal"},
                  "clock_alignment": {f"device-{episode}": dict(target) for episode in "ABC"}}
    execute_data({"definition": {"engine": "data.pipeline", "mode": "offline", "parameters": parameters}},
                 tmp_path, lambda event: None, lambda: False)
    actual = [json.loads(line) for line in (tmp_path / "transformed.jsonl").read_text().splitlines()]
    assert [row["time"] for row in actual] == [5, 6, 15, 16, 25, 26]
    assert all(row["clock_domain"] == "aligned" and row["clock_units"] == "s" for row in actual)
    assert all(row["clock_origin"] == "aligned-start" and row["clock_session"] == "aligned-session"
               and row["clock_reset_id"] == "aligned-reset" for row in actual)
    source = actual[0]["clock_alignment"]["source_clock"]
    assert {field: source[field] for field in ("clock_domain", "clock_units", "clock_origin", "clock_session", "clock_reset_id")} == {
        "clock_domain": "device-A", "clock_units": "ms", "clock_origin": "start-A",
        "clock_session": "session-A", "clock_reset_id": "reset-A"}


@pytest.mark.parametrize("missing", ["units", "clock_origin", "clock_session"])
def test_affine_alignment_does_not_inherit_source_qualification(tmp_path, missing):
    from tinystudio.lab.data import execute_data

    rows = [{**row, "value": 1, "units": "V"} for row in records()]
    target = {"target": "aligned", "units": "s", "clock_origin": "aligned-start", "clock_session": "aligned-session"}
    del target[missing]
    with pytest.raises(ValueError, match="grouped.*alignment"):
        execute_data({"definition": {"engine": "data.pipeline", "mode": "offline", "parameters": {
            "records": rows, "partition_policy": {"strategy": "temporal"}, "clock_alignment": {"capture": target}}}},
            tmp_path, lambda event: None, lambda: False)


def test_purge_seconds_converts_to_millisecond_coordinate_duration():
    rows = records()
    for row, event in zip(rows, (0, 1000, 1300, 2300, 4000, 5000)):
        row.update(time=event, available_at=event, clock_units="ms")
    # A ends at 1000 ms and B starts at 1300 ms. A 500 ms exclusion
    # overlaps B; a 200 ms exclusion leaves the groups disjoint.
    with pytest.raises(ValueError, match="availability overlaps"):
        partition_records(rows, {"partition_policy": {"strategy": "temporal", "purge_seconds": 0.5}}, 42)
    _, manifest = partition_records(rows, {"partition_policy": {"strategy": "temporal", "purge_seconds": 0.2}}, 42)
    assert manifest["purge_seconds"] == 0.2
    assert manifest["purge_coordinate_duration"] == 200


@pytest.mark.parametrize("unit", ["ticks", "V"])
def test_nonzero_purge_requires_supported_time_units(unit):
    rows = [{**row, "clock_units": unit} for row in records()]
    with pytest.raises(ValueError, match="purge_seconds.*supported time units.*alignment"):
        partition_records(rows, {"partition_policy": {"strategy": "temporal", "purge_seconds": 0.5}}, 42)
    _, manifest = partition_records(rows, {"partition_policy": {"strategy": "temporal", "purge_seconds": 0}}, 42)
    assert manifest["temporal_isolation"] is True
