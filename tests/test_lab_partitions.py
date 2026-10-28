"""A grouped generalization split is distinct from chronological forecasting."""
import json

import pytest

from tinystudio.lab.data import execute_data


def episodes(*, different_clocks=False):
    return [{"source_record_id": f"{episode}:{index}", "episode_id": episode,
             "device_id": f"device-{episode}", "scenario_id": f"scenario-{episode}",
             "clock_domain": episode if different_clocks else "simulation", "time": index,
             "available_at": index, "value": index - 0.125, "units": "V"}
            for episode in "ABCDEF" for index in range(3)]


def prepare(tmp_path, rows, policy):
    result = execute_data({"definition": {"engine": "data.pipeline", "mode": "offline", "seed": 42,
                          "parameters": {"records": rows, "partition_policy": policy}}},
                         tmp_path, lambda event: None, lambda: False)
    actual = [json.loads(line) for line in (tmp_path / "transformed.jsonl").read_text().splitlines()]
    manifest = json.loads((tmp_path / "partitions.json").read_text())
    return result, actual, manifest


def test_grouped_split_accepts_independent_episode_clocks_that_restart(tmp_path):
    _, rows, manifest = prepare(tmp_path, episodes(), {"strategy": "grouped"})
    assert {row["partition"] for row in rows} == {"train", "validation", "test"}
    for field in ("episode_id", "device_id", "scenario_id"):
        for identity in {row[field] for row in rows}:
            assert len({row["partition"] for row in rows if row[field] == identity}) == 1
        assert manifest["overlap_report"][field] == {}
    assert manifest["strategy"] == "seeded_connected_groups"
    assert manifest["temporal_isolation"] is False


def test_grouped_split_is_reproducible_on_frozen_input_and_preserves_explicit_labels(tmp_path):
    _, first, _ = prepare(tmp_path / "first", episodes(), {"strategy": "grouped"})
    _, second, manifest = prepare(tmp_path / "second", first, {"strategy": "grouped"})
    assert [row["partition"] for row in first] == [row["partition"] for row in second]
    assert manifest["strategy"] == "preserved_explicit_grouped"


def test_grouped_clock_bounds_do_not_claim_comparable_times(tmp_path):
    _, _, manifest = prepare(tmp_path, episodes(different_clocks=True), {"strategy": "grouped"})
    assert manifest["clock_domain"] is None and manifest["clock_domains"] == list("ABCDEF")
    for partition in manifest["partitions"].values():
        assert partition["event_time_min"] is None and partition["available_at_max"] is None
        assert partition["clock_bounds"]
        assert all(bound == {"event_time_min": 0.0, "available_at_max": 2.0}
                   for bound in partition["clock_bounds"].values())


def test_temporal_split_does_not_silently_become_a_grouped_split(tmp_path):
    rows = episodes()
    for row in rows:
        row.update(clock_units="s", clock_origin="shared-start", clock_session="shared-session")
    with pytest.raises(ValueError, match="availability overlaps"):
        prepare(tmp_path, rows, {"strategy": "temporal"})


def test_unknown_split_strategy_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="strategy"):
        prepare(tmp_path, episodes(), {"strategy": "random-rows"})


def test_temporal_controls_are_not_ignored_in_a_grouped_split(tmp_path):
    with pytest.raises(ValueError, match="temporal"):
        prepare(tmp_path, episodes(), {"strategy": "grouped", "purge_seconds": 1})


def test_grouped_unassigned_allocation_repeats_with_the_same_frozen_seed(tmp_path):
    _, first, _ = prepare(tmp_path / "first", episodes(), {"strategy": "grouped"})
    _, second, _ = prepare(tmp_path / "second", episodes(), {"strategy": "grouped"})
    assert [(row["source_record_id"], row["partition"]) for row in first] == [
        (row["source_record_id"], row["partition"]) for row in second]


def test_grouped_fit_does_not_fabricate_availability_across_independent_clocks(tmp_path):
    body = {"definition": {"engine": "data.pipeline", "mode": "offline", "seed": 42,
            "parameters": {"records": episodes(different_clocks=True), "partition_policy": {"strategy": "grouped"}},
            "pipeline": [{"kind": "standardize", "parameters": {}}]}}
    execute_data(body, tmp_path, lambda event: None, lambda: False)
    rows = [json.loads(line) for line in (tmp_path / "transformed.jsonl").read_text().splitlines()]
    assert all(row["available_at"] == row["time"] for row in rows)
    assert all(row["fit_availability"]["clock_comparable"] is False for row in rows)
    assert all(row["future_aware"] is True for row in rows)
    manifest = json.loads((tmp_path / "partitions.json").read_text())
    fit = manifest["fitted_transforms"][0]["by_units"]["V"]
    assert fit["available_at"] is None and fit["clock_domain"] is None
    assert len(fit["availability_by_clock"]) >= 2


@pytest.mark.parametrize("clock_field", ["clock_units", "clock_origin", "clock_session", "clock_reset_id"])
def test_fitted_availability_does_not_compare_different_coordinates_with_the_same_domain(tmp_path, clock_field):
    rows = episodes()
    for row in rows:
        row.update(clock_units="s", clock_origin="reference-start", clock_session="common-session", clock_reset_id="common-reset")
        row[clock_field] = ("ms" if row["episode_id"] in "ABC" else "s") if clock_field == "clock_units" else f"{clock_field}-{row['episode_id']}"
    execute_data({"definition": {"engine": "data.pipeline", "mode": "offline", "seed": 42,
        "parameters": {"records": rows, "partition_policy": {"strategy": "grouped"}},
        "pipeline": [{"kind": "standardize", "parameters": {}}]}}, tmp_path, lambda event: None, lambda: False)
    actual = [json.loads(line) for line in (tmp_path / "transformed.jsonl").read_text().splitlines()]
    assert all(row["available_at"] == row["time"] for row in actual)
    assert all(row["fit_availability"]["clock_comparable"] is False for row in actual)
