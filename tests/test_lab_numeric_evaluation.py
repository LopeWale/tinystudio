"""D comparisons: independently calculated controls and source-custody checks."""
import hashlib
import json

import pytest
import torch

from tinystudio.lab.numeric_evaluation import build_numeric_evaluation
from tinystudio.lab.timeseries import execute_multimodal, execute_timeseries, load_research_checkpoint


def numeric_spec(objective="forecasting"):
    rows = [{"source_record_id": f"{partition}:{i}", "partition": partition,
             "episode_id": partition, "time": i, "features": [i, offset + 2 * i],
             "numeric": [offset + i], "text": "signal", "target": [offset + i, offset + 10 * i]}
            for partition, offset in (("train", 0), ("validation", 100)) for i in range(6)]
    rows.extend({"source_record_id": label, "partition": label, "episode_id": label,
                 "features": "poison", "numeric": None, "target": "poison"} for label in ("test", "truth"))
    if objective == "classification":
        for index, row in enumerate(rows[:12]):
            row["target"] = [1, "1", "z", "z", "a", "a"][index % 6]
    return {"definition": {"seed": 7,
        "learning": {"family": "mlp"} if objective == "forecasting" else {"objective": objective},
        "parameters": {"steps": 2, "hidden_size": 8, "records": rows,
            **({"context": 2, "horizon": 2, "target_feature": 1} if objective == "forecasting"
               else {"modalities": ["numeric", "text"]})}}}


def run(spec, path, cancelled=lambda: False, emit=lambda row: None):
    execute = execute_timeseries if spec["definition"]["learning"].get("family") == "mlp" else execute_multimodal
    result = execute(spec, path, emit, cancelled)
    return result, json.loads((path / "numeric-evaluation.json").read_text())


def test_forecast_multistep_persistence_is_last_context_not_future(tmp_path):
    result, report = run(numeric_spec(), tmp_path)
    assert report["task"] == "forecasting" and report["output_count"] == 2
    assert report["baseline"] == {"kind": "persistence", "label": "Last context target feature repeated across horizon",
                                  "fit_case_count": 0, "fit_source_record_ids": []}
    assert report["metrics"]["baseline"] == {"mae": 3, "mse": 10}
    assert [row["baseline"]["mse"] for row in report["metrics"]["per_output"]] == [4, 16]
    assert report["records"][0]["target"] == [104, 106]
    assert report["records"][0]["baseline"] == [102, 102]
    assert report["records"][0]["context_source_record_ids"] == ["validation:0", "validation:1"]
    assert report["records"][0]["source_record_ids"] == ["validation:2", "validation:3"]
    assert json.loads(report["records"][0]["raw_target_json"]) == [104, 106]
    assert all(row["unit"] is None for row in report["metrics"]["per_output"])
    assert report["metrics"]["cases"] == 3 and report["metrics"]["scalar_values"] == 6
    assert report["model_sha256"] == hashlib.sha256((tmp_path / "model.pt").read_bytes()).hexdigest()
    assert report["corpus_sha256"] == result["metrics"]["corpus_sha256"]
    assert result["baseline_comparison"] == {key: report[key] for key in ("format", "metrics", "qualification")}
    assert [row["role"] for row in result["artifacts"]] == ["model_checkpoint", "held_out_predictions", "training_metrics", "independent_evaluation"]
    legacy = json.loads((tmp_path / "predictions.json").read_text())
    assert [row["prediction"] for row in report["records"]] == legacy["predictions"]
    assert report["qualification"]["comparison_complete"]


def test_regression_mean_per_output_is_fitted_only_on_training(tmp_path):
    _, report = run(numeric_spec("regression"), tmp_path)
    assert report["baseline"]["constant"] == [2.5, 25]
    assert report["baseline"]["fit_source_record_ids"] == [f"train:{i}" for i in range(6)]
    assert report["baseline"]["fit_case_count"] == 6
    # Validation targets are [100+i,100+10*i], independent of the fit population.
    assert report["metrics"]["baseline"]["mae"] == 100
    assert report["metrics"]["baseline"]["mse"] == pytest.approx((10000 + 35/12 + 10000 + 3500/12) / 2)
    assert report["records"][0]["context_source_record_ids"] == []
    assert json.loads(report["records"][0]["raw_target_json"]) == [100, 100]


def test_class_majority_tie_is_lexical_and_raw_json_keeps_label_types(tmp_path):
    _, report = run(numeric_spec("classification"), tmp_path)
    assert report["output_count"] == 1 and report["metrics"]["per_output"] == []
    assert report["baseline"]["constant"] == "1"
    assert "lexicographically" in report["baseline"]["tie_break"]
    assert report["metrics"]["baseline"]["accuracy"] == pytest.approx(1 / 3)
    assert report["metrics"]["scalar_values"] == 6
    assert [row["target"] for row in report["records"][:2]] == ["1", "1"]
    assert [json.loads(row["raw_target_json"]) for row in report["records"][:2]] == [1, "1"]


@pytest.mark.parametrize("stop", [0, 1])
def test_partial_checkpoint_is_valid_evaluation_but_incomplete_comparison(tmp_path, stop):
    updates = []
    result, report = run(numeric_spec(), tmp_path, lambda: len(updates) >= stop, updates.append)
    assert result["metrics"]["steps"] == stop
    assert load_research_checkpoint(tmp_path / "model.pt")[1]["step"] == stop
    assert report["qualification"]["status"] == "cancelled"
    assert report["qualification"]["evaluation_valid"]
    assert not report["qualification"]["comparison_complete"]


def builder_args():
    return dict(task="regression", output_count=2, predictions=[[1, 2]], targets=[[2, 4]],
        cases=[{"case_id": "v:0", "source_record_ids": ["v:0"], "context_source_record_ids": [], "raw_target_json": "[2,4]"}],
        training_targets=[[0, 2], [2, 4]], training_source_ids=["t:0", "t:1"], persistence=[],
        model_sha256="a" * 64, corpus_sha256="b" * 64, completed=True, reload_agreement=True)


def test_metrics_use_unweighted_components_with_independent_known_errors():
    report = build_numeric_evaluation(**builder_args())
    assert report["metrics"]["model"] == {"mae": 1.5, "mse": 2.5}
    assert report["metrics"]["baseline"] == {"mae": 1, "mse": 1}
    assert report["metrics"]["per_output"] == [
        {"index": 0, "unit": None, "model": {"mae": 1, "mse": 1}, "baseline": {"mae": 1, "mse": 1}},
        {"index": 1, "unit": None, "model": {"mae": 2, "mse": 4}, "baseline": {"mae": 1, "mse": 1}}]


@pytest.mark.parametrize("change,message", [
    ({"predictions": []}, "case count"), ({"targets": [[2]]}, "width"),
    ({"predictions": [[float("nan"), 2]]}, "finite"),
    ({"targets": [[float("inf"), 2]]}, "finite"),
    ({"training_targets": [[1, 2]]}, "case count"),
    ({"predictions": [[1e308, 2]], "targets": [[-1e308, 4]]}, "finite"),
    ({"output_count": 0}, "output_count"),
])
def test_exact_counts_width_and_finite_metrics_are_enforced(change, message):
    args = builder_args()
    args.update(change)
    with pytest.raises(ValueError, match=message):
        build_numeric_evaluation(**args)


def test_duplicate_case_ids_reject():
    args = builder_args()
    args.update(cases=args["cases"] * 2, predictions=args["predictions"] * 2, targets=args["targets"] * 2)
    with pytest.raises(ValueError, match="unique"):
        build_numeric_evaluation(**args)


@pytest.mark.parametrize("objective", ["forecasting", "regression", "classification"])
def test_raw_inference_disagreement_fails_before_comparison_publication(tmp_path, monkeypatch, objective):
    from tinystudio.lab import timeseries
    original = timeseries.predict_from_checkpoint
    def faulty(*args):
        predictions = original(*args)
        if objective == "classification":
            predictions[0] = "injected wrong class"
        else:
            predictions[0][0] += 1
        return predictions
    monkeypatch.setattr(timeseries, "predict_from_checkpoint", faulty)
    with pytest.raises(ValueError, match="disagrees"):
        run(numeric_spec(objective), tmp_path)
    assert not (tmp_path / "numeric-evaluation.json").exists()


def test_loader_rejects_nonfinite_logits_before_classification_argmax(tmp_path):
    from tinystudio.lab.timeseries import predict_from_checkpoint
    spec = numeric_spec("classification")
    run(spec, tmp_path)
    path = tmp_path / "model.pt"
    state = torch.load(path, weights_only=True)
    state["model"]["head.bias"][0] = float("nan")
    torch.save(state, path)
    with pytest.raises(ValueError, match="non-finite"):
        predict_from_checkpoint(path, spec["definition"]["parameters"]["records"][:1])


@pytest.mark.parametrize("case_change,message", [
    ({"source_record_ids": []}, "exactly one target source"),
    ({"source_record_ids": ["v:0", "v:1"]}, "exactly one target source"),
    ({"source_record_ids": "v:0"}, "array"),
    ({"source_record_ids": [7]}, "source ID strings"),
    ({"source_record_ids": [""]}, "source ID strings"),
    ({"source_record_ids": ["  "]}, "source ID strings"),
    ({"context_source_record_ids": None}, "array"),
    ({"context_source_record_ids": [""]}, "source ID strings"),
    ({"context_source_record_ids": ["v:history"]}, "empty context"),
    ({"raw_target_json": 3}, "valid JSON text"),
    ({"raw_target_json": "unquoted text"}, "valid JSON text"),
    ({"raw_target_json": "NaN"}, "valid JSON text"),
    ({"raw_target_json": "[Infinity]"}, "valid JSON text"),
])
def test_case_provenance_and_original_json_schema_are_enforced(case_change, message):
    args = builder_args()
    args["cases"][0].update(case_change)
    with pytest.raises(ValueError, match=message):
        build_numeric_evaluation(**args)


@pytest.mark.parametrize("change,message", [
    ({"model_sha256": "a" * 63}, "model_sha256"),
    ({"model_sha256": "x" * 64}, "model_sha256"),
    ({"corpus_sha256": None}, "corpus_sha256"),
    ({"completed": 1}, "boolean"),
    ({"reload_agreement": "true"}, "boolean"),
    ({"training_source_ids": ["t:0", ""]}, "source ID strings"),
    ({"training_source_ids": "t:0"}, "array"),
])
def test_digest_qualification_and_training_source_contract(change, message):
    args = builder_args()
    args.update(change)
    with pytest.raises(ValueError, match=message):
        build_numeric_evaluation(**args)


@pytest.mark.parametrize("sources,context", [(["v:0"], ["history"]), (["v:0", "v:1"], [])])
def test_forecast_provenance_requires_horizon_target_sources_and_context(sources, context):
    args = builder_args()
    args.update(task="forecasting", persistence=[[1, 1]], training_targets=[], training_source_ids=[])
    args["cases"][0].update(source_record_ids=sources, context_source_record_ids=context)
    with pytest.raises(ValueError, match="forecast provenance"):
        build_numeric_evaluation(**args)


@pytest.mark.parametrize("objective", ["forecasting", "regression", "classification"])
def test_original_empty_source_id_fails_explicitly_without_replacement(tmp_path, objective):
    spec = numeric_spec(objective)
    spec["definition"]["parameters"]["records"][8]["source_record_id"] = ""
    with pytest.raises(ValueError, match="nonempty source ID strings"):
        run(spec, tmp_path)
    assert not (tmp_path / "numeric-evaluation.json").exists()


def test_repeated_source_ids_across_episodes_are_not_globally_rejected():
    args = builder_args()
    args["training_source_ids"] = ["reused", "reused"]
    args["cases"] = [{**args["cases"][0], "case_id": f"v:{index}", "source_record_ids": ["reused"], "episode_id": index}
                     for index in range(2)]
    args["predictions"] *= 2
    args["targets"] *= 2
    report = build_numeric_evaluation(**args)
    assert report["metrics"]["cases"] == 2
    assert report["baseline"]["fit_source_record_ids"] == ["reused", "reused"]
