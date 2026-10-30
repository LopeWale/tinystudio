"""Actual CPU learners must respect author custody before tensors are built."""
import copy
import hashlib
import json

import pytest
import torch

from tinystudio.lab.rl import execute_rl, load_policy
from tinystudio.lab.timeseries import execute_multimodal, execute_timeseries, load_research_checkpoint


def experiment(tmp_path, rows, *, backend, retained=False):
    parameters = {"steps": 2, "hidden_size": 8}
    learning = {}
    if backend == "forecast":
        parameters["context"] = 2
        learning["family"] = "mlp"
    elif backend == "fusion":
        parameters["modalities"] = ["numeric", "text"]
        learning["objective"] = "regression"
    else:
        parameters["batch_size"] = 32
        learning["objective"] = backend
    key = "records" if backend in {"forecast", "fusion"} else "transitions"
    spec = {"definition": {"seed": 13, "learning": learning, "parameters": parameters}}
    if retained:
        path = tmp_path / "source.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({key: rows}))
        spec["definition"]["inputs"] = [{"artifact_id": "dataset", "role": "source", "visibility": "model",
                                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]
        spec["input_paths"] = {"dataset": str(path)}
    else:
        parameters[key] = rows
    return spec


def forecast_rows(poison=10000):
    return [{"source_record_id": f"{episode}:{index}", "episode_id": episode,
             "device_id": episode, "scenario_id": episode, "time": index,
             "partition": label, "value": index + offset}
            for episode, label, count, offset in [("t", "train", 6, 0), ("v", "dev", 4, 10),
                                                   ("x", "held_out", 3, poison), ("z", "truth", 3, -poison)]
            for index in range(count)]


def fusion_rows(poison=10000):
    return [{"source_record_id": f"{label}:{index}", "partition": label,
             "numeric": [index + offset], "text": "source", "target": index + offset}
            for label, offset in [("train", 0), ("validation", 10), ("test", poison), ("truth", -poison)]
            for index in range(4)]


def transitions(poison=10000):
    return [{"source_record_id": f"{label}:{index}", "partition": label,
             "observation": [offset + index, 0, 0], "action": index % 3,
             "next_observation": [offset + index + .1, 0, 0], "reward": offset + .1, "done": False}
            for label, offset in [("train", 0), ("validation", 10), ("held-out", poison), ("truth", -poison)]
            for index in range(4)]


def run_numeric(tmp_path, rows, backend, retained):
    spec = experiment(tmp_path, rows, backend=backend, retained=retained)
    output = tmp_path / "output"
    execute = execute_timeseries if backend == "forecast" else execute_multimodal
    execute(spec, output, lambda event: None, lambda: False)
    state = load_research_checkpoint(output / "model.pt")[1]
    predictions = json.loads((output / "predictions.json").read_text())
    return state, predictions


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("backend", ["forecast", "fusion"])
def test_numeric_excluded_partition_poison_cannot_change_model_or_validation(tmp_path, backend, retained):
    rows = forecast_rows if backend == "forecast" else fusion_rows
    first, first_predictions = run_numeric(tmp_path / "first", rows(10000), backend, retained)
    second, second_predictions = run_numeric(tmp_path / "second", rows(20000), backend, retained)
    normalization = first["normalization"] if backend == "forecast" else first["normalization"]["numeric"]
    assert normalization["mean"].tolist() == pytest.approx([2.5 if backend == "forecast" else 1.5])
    second_normalization = second["normalization"] if backend == "forecast" else second["normalization"]["numeric"]
    assert torch.equal(normalization["mean"], second_normalization["mean"])
    assert torch.equal(normalization["std"], second_normalization["std"])
    assert first["model"].keys() == second["model"].keys()
    assert all(torch.equal(tensor, second["model"][name]) for name, tensor in first["model"].items())
    assert first_predictions["predictions"] == second_predictions["predictions"]
    assert first_predictions["targets"] == second_predictions["targets"]
    records = first_predictions["records"]
    assert all(row["partition"] == "validation" for row in records)
    expected = [["v:2"], ["v:3"]] if backend == "forecast" else [[f"validation:{index}"] for index in range(4)]
    assert [row["source_record_ids"] for row in records] == expected
    if backend == "forecast":
        assert first_predictions["targets"] == [[12.0], [13.0]]
        assert all(row["episode_id"] == "v" for row in records)
        source_rows = rows(10000)
        for window in first["split"]["windows"]:
            original_indices = window["context_rows"] + window["target_rows"]
            assert len({source_rows[index]["episode_id"] for index in original_indices}) == 1


@pytest.mark.parametrize("backend", ["forecast", "fusion"])
def test_numeric_declared_group_overlap_is_rejected_before_training(tmp_path, backend):
    rows = forecast_rows() if backend == "forecast" else fusion_rows()
    for index, row in enumerate(rows):
        row["device_id"] = "same-device"
        if backend == "forecast":
            row["time"] = index
    execute = execute_timeseries if backend == "forecast" else execute_multimodal
    with pytest.raises(ValueError, match="isolation|crosses|overlap"):
        execute(experiment(tmp_path, rows, backend=backend), tmp_path / "output", lambda event: None, lambda: False)


def test_forecast_globally_increasing_time_does_not_hide_partition_leakage(tmp_path):
    rows = forecast_rows()
    for index, row in enumerate(rows):
        row["time"] = index
    state, predictions = run_numeric(tmp_path, rows, "forecast", False)
    assert state["normalization"]["mean"].tolist() == pytest.approx([2.5])
    assert predictions["targets"] == [[12.0], [13.0]]


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("backend", ["dqn", "imitation"])
def test_offline_sampling_excludes_validation_test_and_truth_sources(tmp_path, backend, retained):
    checkpoints = []
    for name, poison in [("first", 10000), ("second", 20000)]:
        root = tmp_path / name
        spec = experiment(root, transitions(poison), backend=backend, retained=retained)
        output = root / "output"
        execute_rl(spec, output, lambda event: None, lambda: False)
        sampled = [json.loads(line) for line in (output / "transitions.jsonl").read_text().splitlines()]
        assert sampled
        assert {row["source_record_id"] for row in sampled} <= {f"train:{index}" for index in range(4)}
        assert {row["source_index"] for row in sampled} <= {0, 1, 2, 3}
        checkpoints.append(load_policy(output / "policy.pt")[1])
    assert all(torch.equal(tensor, checkpoints[1]["model"][name])
               for name, tensor in checkpoints[0]["model"].items())


@pytest.mark.parametrize("backend", ["forecast", "fusion", "dqn"])
@pytest.mark.parametrize("source", [{"role": "held_out", "visibility": "model"},
                                     {"role": "source", "visibility": "truth"},
                                     {"role": "source", "visibility": "evaluation"}])
def test_retained_source_role_cannot_be_downgraded_by_row_train_label(tmp_path, backend, source):
    rows = forecast_rows() if backend == "forecast" else fusion_rows() if backend == "fusion" else transitions()
    # Every row claims training while its immutable source declares held-out custody.
    rows = copy.deepcopy(rows)
    for index, row in enumerate(rows):
        row["partition"] = "train"
        if backend == "forecast":
            row["time"] = index
    spec = experiment(tmp_path, rows, backend=backend, retained=True)
    spec["definition"]["inputs"][0].update(source)
    execute = execute_timeseries if backend == "forecast" else execute_multimodal if backend == "fusion" else execute_rl
    with pytest.raises(ValueError):
        execute(spec, tmp_path / "output", lambda event: None, lambda: False)


@pytest.mark.parametrize("backend", ["fusion", "dqn"])
def test_inline_truth_visibility_cannot_be_downgraded_by_train_label(tmp_path, backend):
    rows = fusion_rows() if backend == "fusion" else transitions()
    rows[0]["source_visibility"] = "truth"
    execute = execute_multimodal if backend == "fusion" else execute_rl
    with pytest.raises(ValueError, match="truth|protection"):
        execute(experiment(tmp_path, rows, backend=backend), tmp_path / "output", lambda event: None, lambda: False)
