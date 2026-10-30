"""Partition expectations derive from declared custody and causal chronology."""
import copy

import pytest

from tinystudio.lab.research_partitions import forecast_window_plan, record_partition_plan


def test_declared_partitions_exclude_poison_from_training_and_normalization():
    rows = [{"source_record_id": name, "split": label, "value": value}
            for name, label, value in [("a", "training", 2), ("b", "train", 4),
                                      ("c", "dev", 6), ("d", "held_out", 10**20),
                                      ("e", "ground-truth", -10**20)]]
    original = copy.deepcopy(rows)
    plan = record_partition_plan(rows)
    assert plan["train"] == [0, 1]
    assert plan["validation"] == [2]
    assert plan["test"] == [3]
    assert plan["truth"] == [4]
    assert plan["source_record_ids"] == list("abcde")
    assert plan["normalization_rows"] == [0, 1]
    assert sum(rows[index]["value"] for index in plan["normalization_rows"]) / 2 == 3
    assert rows == original


@pytest.mark.parametrize("bad", [
    {"partition": "train", "split": "test"},
    {"partition": "training", "visibility": "truth"},
    {"partition": "train", "source_visibility": "evaluation"},
    {"partition": "train", "source_role": "heldout"},
    {"partition": "validation", "role": "truth"},
    {"partition": "something"},
])
def test_conflicting_aliases_and_source_restrictions_fail_closed(bad):
    with pytest.raises(ValueError):
        record_partition_plan([bad, {"partition": "validation"}])


def test_mixed_explicit_and_unlabelled_rows_are_not_reallocated():
    with pytest.raises(ValueError, match="every row"):
        record_partition_plan([{"split": "train"}, {}, {"split": "dev"}])


def test_declared_train_only_is_not_resplit_for_validation():
    rows = [{"partition": "train"} for _ in range(8)]
    with pytest.raises(ValueError, match="training and validation"):
        record_partition_plan(rows)
    assert record_partition_plan(rows, require_validation=False)["train"] == list(range(8))
    assert record_partition_plan([{} for _ in range(3)], require_validation=False)["train"] == [0, 1, 2]


def test_duplicate_source_identities_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        record_partition_plan([{"source_record_id": "same"}, {"source_record_id": "same"}])


def test_group_connections_are_transitive_across_device_and_scenario():
    rows = [
        {"episode_id": "a", "device_id": "x", "scenario_id": "one"},
        {"episode_id": "b", "device_id": "x", "scenario_id": "two"},
        {"episode_id": "c", "device_id": "y", "scenario_id": "two"},
        {"episode_id": "d", "device_id": "z", "scenario_id": "three"},
    ]
    plan = record_partition_plan(rows, seed=17)
    assert set(plan["train"]) in ({0, 1, 2}, {3})
    assert set(plan["validation"]) == set(range(4)) - set(plan["train"])
    for row, label in zip(rows, ["train", "train", "validation", "test"]):
        row["partition"] = label
    with pytest.raises(ValueError, match="crosses"):
        record_partition_plan(rows)


def test_incomplete_group_metadata_and_one_connected_group_are_rejected():
    with pytest.raises(ValueError, match="incomplete"):
        record_partition_plan([{"device_id": "a"}, {}])
    with pytest.raises(ValueError, match="two independent"):
        record_partition_plan([{"device_id": "a"}, {"device_id": "a"}])


@pytest.mark.parametrize("group_by", [[], ["custom"]])
def test_custom_fields_cannot_disable_detected_source_group_protection(group_by):
    rows = [{"device_id": "shared", "custom": "a", "partition": "train"},
            {"device_id": "shared", "custom": "b", "partition": "validation"}]
    with pytest.raises(ValueError, match="crosses"):
        record_partition_plan(rows, group_by=group_by)


def test_custom_field_adds_an_isolation_constraint():
    rows = [{"custom": "shared", "partition": "train"},
            {"custom": "shared", "partition": "validation"}]
    with pytest.raises(ValueError, match="crosses"):
        record_partition_plan(rows, group_by=["custom"])


def test_legacy_forecast_retains_original_positions_and_causal_holdout():
    rows = [{"value": index} for index in range(10)]
    plan = forecast_window_plan(rows, context=2, horizon=2, validation_fraction=.3)
    assert plan["cutoff"] == 7
    assert plan["normalization_rows"] == list(range(7))
    assert plan["target_positions"] == [2, 3, 4, 5, 7, 8]
    assert plan["train"] == [0, 1, 2, 3]
    assert plan["validation"] == [4, 5]
    assert plan["windows"][4] == {"context_rows": [5, 6], "target_rows": [7, 8]}


def test_explicit_temporal_episode_allows_causal_history_and_excludes_poison():
    rows = [{"source_record_id": f"s:{i}", "episode_id": "e", "device_id": "d",
             "time": i, "partition": label, "value": i if i < 8 else 10**30}
            for i, label in enumerate(["train"] * 5 + ["validation"] * 3 + ["test", "truth"])]
    with pytest.raises(ValueError, match="crosses"):
        forecast_window_plan(rows, context=2)
    plan = forecast_window_plan(rows, context=2, temporal_split=True)
    assert plan["normalization_rows"] == [0, 1, 2, 3, 4]
    assert plan["record_partitions"]["test"] == [8]
    assert plan["record_partitions"]["truth"] == [9]
    assert plan["windows"][3] == {"context_rows": [3, 4], "target_rows": [5]}
    assert plan["target_positions"] == [2, 3, 4, 5, 6, 7]
    assert all(index < 8 for window in plan["windows"] for indices in window.values() for index in indices)


def test_truth_in_middle_breaks_windows_without_entering_history():
    labels = ["train"] * 5 + ["truth"] + ["validation"] * 4
    plan = forecast_window_plan([{"partition": label} for label in labels], context=2, temporal_split=True)
    assert plan["target_positions"] == [2, 3, 4, 8, 9]


def test_forecast_windows_do_not_join_adjacent_episodes_with_reset_clocks():
    rows = [{"episode_id": episode, "time": i, "partition": label}
            for episode, label in [("a", "train"), ("b", "validation")]
            for i in range(4)]
    plan = forecast_window_plan(rows, context=2)
    assert plan["target_positions"] == [2, 3, 6, 7]
    assert plan["windows"][2] == {"context_rows": [4, 5], "target_rows": [6]}
    assert plan["normalization_rows"] == [0, 1, 2, 3]


@pytest.mark.parametrize("times", [[0, 1, 1, 3, 4, 5], [0, 1, 3, 2, 4, 5], [0, 1, float("nan"), 3, 4, 5]])
def test_noncausal_or_nonfinite_time_is_rejected(times):
    with pytest.raises(ValueError, match="chronological"):
        forecast_window_plan([{"time": time} for time in times], context=1)


def test_temporal_label_reversal_is_rejected():
    with pytest.raises(ValueError, match="training before validation"):
        forecast_window_plan([{"partition": label} for label in
                              ["train", "train", "validation", "train", "validation"]],
                             context=1, temporal_split=True)


@pytest.mark.parametrize("kwargs", [{"context": 5}, {"context": 1, "horizon": 6}])
def test_insufficient_causal_evidence_fails_instead_of_reusing_training(kwargs):
    with pytest.raises(ValueError, match="windows"):
        forecast_window_plan([{} for _ in range(6)], **kwargs)
