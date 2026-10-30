import math
import hashlib
import json

import pytest
import torch

from tinystudio.lab.timeseries import execute_multimodal, execute_timeseries, load_research_checkpoint, predict_from_checkpoint


def spec(family="gru", **parameters):
    return {"definition": {"seed": 7, "learning": {"family": family},
        "parameters": {"steps": 12, "hidden_size": 8, "context": 4,
                       "values": [math.sin(index / 5) for index in range(50)], **parameters}}}


@pytest.mark.parametrize("family", ["gru", "transformer", "mlp"])
def test_forecasting_trains_and_reloads_with_disjoint_future_targets(tmp_path, family):
    result = execute_timeseries(spec(family), tmp_path, lambda row: None, lambda: False)
    assert result["metrics"]["parameters_changed"]
    assert result["metrics"]["reload_predictions_equal"]
    assert math.isfinite(result["metrics"]["validation_loss"])
    _, state = load_research_checkpoint(tmp_path / "model.pt")
    split = state["split"]
    assert all(split["target_positions"][index] < split["cutoff"] for index in split["train"])
    assert all(split["target_positions"][index] >= split["cutoff"] for index in split["validation"])
    expected_mean = sum(math.sin(index / 5) for index in range(40)) / 40
    assert float(state["normalization"]["mean"][0]) == pytest.approx(expected_mean)
    prediction = predict_from_checkpoint(tmp_path / "model.pt", [[0, 0.1, 0.2, 0.3]])
    assert len(prediction) == 1 and len(prediction[0]) == 1


def test_multistep_split_excludes_boundary_leakage(tmp_path):
    execute_timeseries(spec(horizon=3), tmp_path, lambda row: None, lambda: False)
    _, state = load_research_checkpoint(tmp_path / "model.pt")
    split = state["split"]
    train_targets = {position + offset for index in split["train"] for position in [split["target_positions"][index]] for offset in range(3)}
    validation_targets = {position + offset for index in split["validation"] for position in [split["target_positions"][index]] for offset in range(3)}
    assert train_targets.isdisjoint(validation_targets)
    assert max(train_targets) < min(validation_targets)


def test_forecasting_has_learning_signal(tmp_path):
    result = execute_timeseries(spec("mlp", values=[0.2] * 50, steps=50), tmp_path, lambda row: None, lambda: False)
    assert result["metrics"]["validation_loss"] < result["metrics"]["initial_validation_loss"] * 0.1


def multimodal_spec(objective="regression"):
    records = [{"numeric": [index / 10, 1], "text": "warm" if index % 2 else "cold",
                "image": [[index / 10, 0], [0, 1]], "audio": [0, index / 10, 0, -index / 10],
                "target": "odd" if index % 2 else "even"} for index in range(12)]
    if objective == "regression":
        for index, record in enumerate(records):
            record["target"] = index / 10
    return {"definition": {"seed": 4, "learning": {"objective": objective}, "parameters": {
        "steps": 15, "hidden_size": 8, "modalities": ["numeric", "text", "image", "audio"], "records": records}}}


@pytest.mark.parametrize("objective", ["regression", "classification"])
def test_multimodal_fuses_real_modalities_and_reloads(tmp_path, objective):
    experiment = multimodal_spec(objective)
    result = execute_multimodal(experiment, tmp_path, lambda row: None, lambda: False)
    assert result["metrics"]["parameters_changed"]
    assert result["metrics"]["reload_predictions_equal"]
    _, state = load_research_checkpoint(tmp_path / "model.pt")
    assert set(state["architecture"]["modalities"]) == {"numeric", "text", "image", "audio"}
    prediction = predict_from_checkpoint(tmp_path / "model.pt", experiment["definition"]["parameters"]["records"][:2])
    assert len(prediction) == 2
    if objective == "classification":
        assert set(prediction) <= {"odd", "even"}
        report = json.loads((tmp_path / "predictions.json").read_text())
        expected_labels = [experiment["definition"]["parameters"]["records"][index]["target"]
                           for index in state["split"]["validation"]]
        assert report["target_labels"] == expected_labels
        assert report["classes"] == ["even", "odd"]
        assert report["target_encoding"] == "class_index"
        assert [report["classes"][index] for index in report["targets"]] == expected_labels
        assert [row["target"] for row in report["records"]] == expected_labels
        assert [row["target_index"] for row in report["records"]] == report["targets"]


def test_invalid_data_and_single_modality_fail_explicitly(tmp_path):
    with pytest.raises(ValueError, match="finite"):
        execute_timeseries(spec(values=[float("nan")] * 50), tmp_path, lambda row: None, lambda: False)
    experiment = multimodal_spec()
    experiment["definition"]["parameters"]["modalities"] = ["text"]
    with pytest.raises(ValueError, match="at least two"):
        execute_multimodal(experiment, tmp_path, lambda row: None, lambda: False)


def test_cancellation_retains_partial_model(tmp_path):
    rows = []
    result = execute_timeseries(spec(), tmp_path, rows.append, lambda: len(rows) == 2)
    assert result["qualification"]["status"] == "cancelled"
    assert load_research_checkpoint(tmp_path / "model.pt")[1]["step"] == 2


@pytest.mark.parametrize("family", ["gru", "transformer", "mlp", "multimodal"])
@pytest.mark.parametrize("location", ["learning", "parameters"])
def test_research_resume_matches_uninterrupted_updates(tmp_path, family, location):
    def experiment(steps, resume_from=None):
        value = multimodal_spec() if family == "multimodal" else spec(family)
        value["definition"]["parameters"]["steps"] = steps
        value["definition"][location]["initialization"] = "resume" if resume_from else "scratch"
        if resume_from:
            value["definition"]["inputs"] = [{"artifact_id": "resume", "role": "resume",
                "sha256": hashlib.sha256(resume_from.read_bytes()).hexdigest()}]
            value["input_paths"] = {"resume": str(resume_from)}
        return value
    execute = execute_multimodal if family == "multimodal" else execute_timeseries
    execute(experiment(4), tmp_path / "whole", lambda row: None, lambda: False)
    execute(experiment(2), tmp_path / "first", lambda row: None, lambda: False)
    result = execute(experiment(4, tmp_path / "first/model.pt"), tmp_path / "resumed", lambda row: None, lambda: False)
    expected = load_research_checkpoint(tmp_path / "whole/model.pt")[1]["model"]
    actual = load_research_checkpoint(tmp_path / "resumed/model.pt")[1]["model"]
    assert all(torch.equal(expected[key], value) for key, value in actual.items())
    assert [row["step"] for row in result["records"]] == [3, 4]


def test_nested_forecaster_architecture_is_actual_and_unsupported_strategy_rejects(tmp_path):
    experiment = spec("transformer", steps=1)
    experiment["definition"]["parameters"].pop("hidden_size")
    experiment["definition"]["parameters"].pop("context")
    experiment["definition"]["learning"]["architecture"] = {"hidden_size": 12, "layers": 2, "heads": 3, "context": 5}
    execute_timeseries(experiment, tmp_path, lambda row: None, lambda: False)
    model, state = load_research_checkpoint(tmp_path / "model.pt")
    assert model.embedding.out_features == 12 and model.position.num_embeddings == 5
    assert len(model.core.layers) == 2 and model.core.layers[0].self_attn.num_heads == 3
    assert state["architecture"]["layers"] == 2
    experiment["definition"]["parameters"]["hidden_size"] = 8
    with pytest.raises(ValueError, match="conflicting"):
        execute_timeseries(experiment, tmp_path / "conflict", lambda row: None, lambda: False)
    experiment["definition"]["learning"]["update_strategy"] = "qlora"
    with pytest.raises(ValueError, match="unsupported update_strategy"):
        execute_timeseries(experiment, tmp_path / "strategy", lambda row: None, lambda: False)


@pytest.mark.parametrize("multimodal", [False, True])
def test_excluded_numeric_rows_are_never_parsed_as_features_or_targets(tmp_path, multimodal):
    rows = [{"source_record_id": f"{label}:{index}", "partition": label,
             "episode_id": label, "time": index, "value": index,
             "numeric": [index], "text": "signal", "target": index}
            for label in ("train", "validation") for index in range(6)]
    rows.extend([{"partition": label, "episode_id": label, "value": {"invalid": True},
                  "numeric": "not-an-array", "text": None, "target": {"invalid": True}}
                 for label in ("test", "truth")])
    experiment = {"definition": {"learning": {"family": "mlp"} if not multimodal else {},
                                "parameters": {"steps": 1, "hidden_size": 8,
                                               "records": rows, **({"modalities": ["numeric", "text"]} if multimodal else {"context": 2})}}}
    execute = execute_multimodal if multimodal else execute_timeseries
    execute(experiment, tmp_path, lambda row: None, lambda: False)
    state = load_research_checkpoint(tmp_path / "model.pt")[1]
    normalization = state["normalization"]["numeric"] if multimodal else state["normalization"]
    assert normalization["mean"].tolist() == pytest.approx([2.5])


@pytest.mark.parametrize("multimodal", [False, True])
def test_numeric_external_validation_binding_is_used_without_resplitting(tmp_path, multimodal):
    def rows(episode, offset):
        return [{"source_record_id": f"{episode}:{index}", "episode_id": episode, "time": index,
                 "value": offset + index, "numeric": [offset + index], "text": "signal", "target": offset + index}
                for index in range(6)]
    validation = tmp_path / "validation.json"
    validation.write_text(json.dumps({"records": rows("v", 10)}))
    experiment = {"definition": {"learning": {"family": "mlp"} if not multimodal else {},
        "parameters": {"steps": 1, "hidden_size": 8, "records": rows("t", 0),
                       **({"modalities": ["numeric", "text"]} if multimodal else {"context": 2})},
        "inputs": [{"artifact_id": "validation", "role": "validation", "visibility": "evaluation",
                    "sha256": hashlib.sha256(validation.read_bytes()).hexdigest()}]},
        "evaluation_input_paths": {"validation": str(validation)}}
    execute = execute_multimodal if multimodal else execute_timeseries
    execute(experiment, tmp_path / "output", lambda row: None, lambda: False)
    predictions = json.loads((tmp_path / "output/predictions.json").read_text())
    expected = range(6) if multimodal else range(2, 6)
    assert [row["source_record_ids"] for row in predictions["records"]] == [[f"v:{index}"] for index in expected]
    state = load_research_checkpoint(tmp_path / "output/model.pt")[1]
    normalization = state["normalization"]["numeric"] if multimodal else state["normalization"]
    assert normalization["mean"].tolist() == pytest.approx([2.5])


@pytest.mark.parametrize("parameters, message", [
    ({"temporal_split": "false"}, "boolean"),
    ({"leakage_policy": {}}, "leakage_policy"),
    ({"records": [{"value": 0}]}, "mutually exclusive"),
])
def test_forecast_partition_controls_fail_explicitly(tmp_path, parameters, message):
    with pytest.raises(ValueError, match=message):
        execute_timeseries(spec(steps=1, **parameters), tmp_path, lambda row: None, lambda: False)


def test_forecast_explicit_temporal_validation_uses_only_causal_training_history(tmp_path):
    rows = [{"source_record_id": f"r:{index}", "episode_id": "one", "time": index,
             "partition": label, "value": index if index < 8 else "excluded"}
            for index, label in enumerate(["train"] * 5 + ["validation"] * 3 + ["test", "truth"])]
    execute_timeseries(spec("mlp", values=None, records=rows, context=2, horizon=2,
                            temporal_split=True, steps=1), tmp_path, lambda row: None, lambda: False)
    state = load_research_checkpoint(tmp_path / "model.pt")[1]
    assert state["normalization"]["mean"].tolist() == pytest.approx([2.0])
    predictions = json.loads((tmp_path / "predictions.json").read_text())
    assert predictions["targets"] == [[5.0, 6.0], [6.0, 7.0]]
    assert [row["source_record_ids"] for row in predictions["records"]] == [["r:5", "r:6"], ["r:6", "r:7"]]
    first_validation = state["split"]["windows"][state["split"]["validation"][0]]
    assert first_validation == {"context_rows": [3, 4], "target_rows": [5, 6]}
