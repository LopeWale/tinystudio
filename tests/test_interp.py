"""Interpretability on a tiny random Llama: captures, neuron views, comparisons, checkpoints, drift (store interface)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.conftest import needs_tokenizer, tiny_llama_dir
from tinystudio.core import data
from tinystudio.core.project import ProjectError
from tinystudio.interp import neurons as interp


def row(user: str, answer: str, labels: list[str]):
    return data.validate_datapoint({"messages": [{"role": "system", "content": "Reply with JSON."}, {"role": "user", "content": user}, {"role": "assistant", "content": answer}], "labels": labels})


def command_rows() -> list[dict]:
    rows = [row(f"turn the {d} off in the {r}", json.dumps({"action": "turn_off", "device": d, "room": r}), [d, "turn_off"]) for d in ("lights", "fan", "heater") for r in ("kitchen", "garage", "bedroom", "office")]
    rows += [row(f"set the {d} to {v}", json.dumps({"action": "set", "device": d, "value": v}), [d, "set"]) for d in ("thermostat", "speaker") for v in (18, 21, 40, 60)]
    return rows


def test_shared_prefix_and_regime_helpers():
    import torch

    ids = torch.tensor([[1, 2, 3, 4, 0], [1, 2, 9, 4, 5], [1, 2, 3, 7, 8]])
    am = torch.tensor([[1, 1, 1, 1, 0], [1, 1, 1, 1, 1], [1, 1, 1, 1, 1]])
    assert interp.shared_prefix_len(ids, am) == 2 and interp.shared_prefix_len(ids[:1], am[:1]) == 0
    same = torch.tensor([[1, 2, 3], [1, 2, 3]])
    assert interp.shared_prefix_len(same, torch.ones_like(same)) == 0
    assert interp.content_mask(am, 2)[0].tolist() == [False, False, True, True, False]
    assert interp.REGIMES == {3: "+/+", 2: "+/-", 1: "-/+", 0: "-/-"}
    assert interp.default_capture_name("job/3@40", "cmds") == "job-3-step40--cmds"


@needs_tokenizer
def test_capture_neuron_view_and_table(tmp_path: Path):
    store = interp.LocalStore(tmp_path / "interp", command_rows())
    model_dir = tiny_llama_dir(tmp_path)
    meta = interp.capture(store, interp.CaptureSpec(model_id=str(model_dir), dataset="cmds", layers=[1], topk=6, batch_size=5), progress=lambda *_: None)
    assert meta["layers"] == [1] and meta["neurons_per_layer"] == 48 and meta["rows"] == 20 and meta["prefix_len"] > 0
    assert (store.captures_dir / meta["name"] / "layer-1.npz").exists()
    table = interp.neuron_table(store, meta["name"], 1, sort="echo_share", limit=5)
    assert table["total"] == 48 and len(table["rows"]) == 5 and all(0 <= r["echo_share"] <= 1 for r in table["rows"])
    with pytest.raises(ProjectError, match="sort must be"):
        interp.neuron_table(store, meta["name"], 1, sort="nope")
    view = interp.neuron_view(store, meta["name"], 1, 0, limit=6)
    assert len(view["positives"]) == 6 and len(view["negatives"]) == 6
    first = view["positives"][0]
    assert first["value"] >= view["positives"][-1]["value"] and first["position"] >= meta["prefix_len"] and first["regime"] in interp.REGIMES.values()
    assert sum(view["regimes"].values()) == 6 and all(0 < share <= 1 for _, share in view["label_share"])
    assert view["stats"]["max"] == pytest.approx(first["value"], rel=1e-4)
    assert interp.label_shares([{"datapoint": 1, "labels": ["a", "b"]}, {"datapoint": 1, "labels": ["a", "b"]}, {"datapoint": 2, "labels": ["a"]}, {"datapoint": 3, "labels": []}]) == [["a", 0.667], ["b", 0.333], ["(unlabelled)", 0.333]]
    assert "STRONGEST NEGATIVE" in interp.explain_prompt(view)
    with pytest.raises(ProjectError, match="has no layer 0"):
        interp.neuron_view(store, meta["name"], 0, 0)
    with pytest.raises(ProjectError, match="already exists"):
        interp.capture(store, interp.CaptureSpec(model_id=str(model_dir), dataset="cmds", layers=[1], name=meta["name"]), progress=lambda *_: None)
    assert interp.list_captures(store)[0]["explained"] == 0
    interp.delete_capture(store, meta["name"])
    assert interp.list_captures(store) == []


@needs_tokenizer
def test_score_texts_and_diff_against_itself(tmp_path: Path):
    store = interp.LocalStore(tmp_path / "interp", command_rows())
    model_dir = tiny_llama_dir(tmp_path)
    meta = interp.capture(store, interp.CaptureSpec(model_id=str(model_dir), dataset="cmds", layers=[0, 1], topk=4), progress=lambda *_: None)
    loaded = interp.load_model(store, str(model_dir), device="cpu")
    tokens = interp._tokens(store, meta["name"])
    scored = interp.score_texts(loaded, 1, [0, 5], [{"user": "turn the lights off in the kitchen", "assistant": json.dumps({"action": "turn_off", "device": "lights", "room": "kitchen"})}, "hello"], "Reply with JSON.", prefix_ids=tokens["prefix_ids"])
    assert len(scored) == 2 and set(scored[0]["neurons"]) == {0, 5} and scored[1]["text"] == "hello"
    view0 = interp.neuron_view(store, meta["name"], 1, 0)
    assert scored[0]["neurons"][0]["max"] <= view0["stats"]["max"] + 1e-4
    d = interp.diff(store, interp.DiffSpec(experiment=str(model_dir), base=str(model_dir), dataset="cmds", layers=[1], batch_size=7), progress=lambda *_: None)
    assert d["mode"] == "two_models" and d["layer_summary"]["1"]["median_r"] == pytest.approx(1.0, abs=1e-5) and d["layer_summary"]["1"]["below_0_9"] == 0
    other = tiny_llama_dir(tmp_path / "other", seed=7)
    d2 = interp.diff(store, interp.DiffSpec(experiment=str(other), base=str(model_dir), dataset="cmds", layers=[1], name="other-vs-tiny"), progress=lambda *_: None)
    assert d2["layer_summary"]["1"]["median_r"] < 0.99 and d2["layer_summary"]["1"]["rel_change_median"] > 0.05
    interp.delete_diff(store, "other-vs-tiny")
    layer = interp.diff_layer(store, d["name"], 1, limit=3)
    assert len(layer["rows"]) == 3 and all(abs(r["max_delta"]) < 1e-4 and r["change"] < 1e-3 for r in layer["rows"])
    assert interp.diff_neuron(store, d["name"], 1, 0)["r"] == pytest.approx(1.0, abs=1e-5)
    interp.delete_diff(store, d["name"])


@needs_tokenizer
def test_checkpoint_captures_and_drift_through_the_project_store(db, project, tmp_path: Path):
    from tinystudio.services import datasets as datasets_svc
    from tinystudio.services import jobs as jobs_svc
    from tinystudio.services import snapshots as snapshots_svc
    from tinystudio.services.interp import ProjectStore, overview
    from tinystudio.services.models import resolve
    from tinystudio.training.train import TrainConfig

    model_dir = tiny_llama_dir(tmp_path)
    datasets_svc.create_dataset(db, project, "cmds", command_rows())
    datasets_svc.autosplit(db, project, "cmds", 80)
    snapshots_svc.create_snapshot(db, project, "cmds", "s1", min_training=5)
    jobs_svc.create_job(db, project, "j", "s1")
    exp = jobs_svc.add_experiment(db, project, "j", TrainConfig(base_model=str(model_dir), checkpoint_every_steps=2))
    path = jobs_svc.exp_path(exp)
    for step in (2, 4):
        shutil.copytree(model_dir, path / "checkpoints" / f"checkpoint-{step}")
        with (path / "checkpoints.jsonl").open("a") as handle:
            handle.write(json.dumps({"step": step, "epoch": step / 4, "loss": 1.0 / step}) + "\n")
    shutil.copytree(model_dir, path / "model")
    exp.state, exp.status = "succeeded", {"state": "succeeded"}
    db.commit()
    store = ProjectStore(project.id)
    checkpoints = store.experiment_checkpoints("j", exp.number)
    assert [c["step"] for c in checkpoints] == [2, 4] and checkpoints[0]["loss"] == 0.5 and checkpoints[1]["model_id"] == f"j/{exp.number}@4"
    assert resolve(db, project, f"j/{exp.number}@2").model_dir == path / "checkpoints" / "checkpoint-2"
    assert len(store.rows_for("cmds", None)) == 20 and len(store.rows_for(None, "s1")) == 20
    metas = interp.capture_checkpoints(store, "j", exp.number, "cmds", None, [1], topk=5, progress=lambda *_: None)
    assert [m["model_id"] for m in metas] == [f"j/{exp.number}@2", f"j/{exp.number}@4", f"j/{exp.number}"]
    db.expire_all()
    assert len(overview(db, project)["captures"]) == 3
    d = interp.drift(store, "j", exp.number, "cmds", 1, 3)
    assert [s["step"] for s in d["steps"]] == [2, 4, None] and d["steps"][-1]["final"] is True and d["steps"][1]["jaccard_prev"] == 1.0
    with pytest.raises(ProjectError, match="no captures"):
        interp.drift(store, "j", exp.number, "other", 1, 3)
    interp.delete_capture(store, metas[0]["name"])
    db.expire_all()
    assert len(overview(db, project)["captures"]) == 2
