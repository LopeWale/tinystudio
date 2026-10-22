"""Interpretability tests on a tiny random Llama model (no download: the SmolLM2 tokenizer is read from the local Hub cache)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tinystudio.core import data

from tinystudio.interp import neurons as interp
from tinystudio.core.project import Project, ProjectError, write_json
from tinystudio.serving.runner import resolve_model
from tinystudio.training.train import TrainConfig, _checkpoint_args

os.environ.setdefault("HF_HUB_OFFLINE", "1")
TOKENIZER = "HuggingFaceTB/SmolLM2-135M-Instruct"


def _tokenizer_cached() -> bool:
    try:
        from transformers import AutoTokenizer

        AutoTokenizer.from_pretrained(TOKENIZER)
        return True
    except Exception:  # noqa: BLE001
        return False


needs_tokenizer = pytest.mark.skipif(not _tokenizer_cached(), reason="SmolLM2 tokenizer not in the local Hub cache")


def row(user: str, answer: str, labels: list[str]):
    return data.validate_datapoint({"messages": [{"role": "system", "content": "Reply with JSON."}, {"role": "user", "content": user}, {"role": "assistant", "content": answer}], "labels": labels})


def tiny_model_dir(tmp_path: Path) -> Path:
    """A random 2-layer SwiGLU Llama saved as a model directory, with the real SmolLM2 tokenizer."""
    import torch
    from transformers import AutoTokenizer, LlamaConfig, LlamaForCausalLM

    tok = AutoTokenizer.from_pretrained(TOKENIZER)
    cfg = LlamaConfig(vocab_size=len(tok), hidden_size=32, intermediate_size=48, num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=256, hidden_act="silu", tie_word_embeddings=True)
    torch.manual_seed(len(str(tmp_path)))
    model = LlamaForCausalLM(cfg)
    tmp_path.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "tiny-model"
    model.save_pretrained(out)
    tok.save_pretrained(out)
    return out


def make_project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "p", "p")
    rows = [row(f"turn the {d} off in the {r}", json.dumps({"action": "turn_off", "device": d, "room": r}), [d, "turn_off"]) for d in ("lights", "fan", "heater") for r in ("kitchen", "garage", "bedroom", "office")]
    rows += [row(f"set the {d} to {v}", json.dumps({"action": "set", "device": d, "value": v}), [d, "set"]) for d in ("thermostat", "speaker") for v in (18, 21, 40, 60)]
    data.save_dataset(project, "cmds", rows)
    return project


def test_shared_prefix_and_regime_helpers():
    import torch

    ids = torch.tensor([[1, 2, 3, 4, 0], [1, 2, 9, 4, 5], [1, 2, 3, 7, 8]])
    am = torch.tensor([[1, 1, 1, 1, 0], [1, 1, 1, 1, 1], [1, 1, 1, 1, 1]])
    assert interp.shared_prefix_len(ids, am) == 2
    assert interp.shared_prefix_len(ids[:1], am[:1]) == 0
    same = torch.tensor([[1, 2, 3], [1, 2, 3]])
    assert interp.shared_prefix_len(same, torch.ones_like(same)) == 0  # everything shared -> nothing excluded
    mask = interp.content_mask(am, 2)
    assert mask[0].tolist() == [False, False, True, True, False]
    assert interp.REGIMES == {3: "+/+", 2: "+/-", 1: "-/+", 0: "-/-"}
    assert interp.default_capture_name("job/3@40", "cmds") == "job-3-step40--cmds"


@needs_tokenizer
def test_capture_neuron_view_and_table(tmp_path: Path):
    project = make_project(tmp_path)
    model_dir = tiny_model_dir(tmp_path)
    meta = interp.capture(project, interp.CaptureSpec(model_id=str(model_dir), dataset="cmds", layers=[1], topk=6, batch_size=5), progress=lambda *_: None)
    assert meta["layers"] == [1] and meta["neurons_per_layer"] == 48 and meta["rows"] == 20 and meta["prefix_len"] > 0
    assert (interp.capture_dir(project, meta["name"]) / "layer-1.npz").exists()
    table = interp.neuron_table(project, meta["name"], 1, sort="echo_share", limit=5)
    assert table["total"] == 48 and len(table["rows"]) == 5 and all(0 <= r["echo_share"] <= 1 for r in table["rows"])
    with pytest.raises(ProjectError, match="sort must be"):
        interp.neuron_table(project, meta["name"], 1, sort="nope")
    view = interp.neuron_view(project, meta["name"], 1, 0, limit=6)
    assert len(view["positives"]) == 6 and len(view["negatives"]) == 6
    first = view["positives"][0]
    assert first["value"] >= view["positives"][-1]["value"] and first["position"] >= meta["prefix_len"]
    assert first["regime"] in interp.REGIMES.values() and first["token"] and isinstance(first["labels"], list)
    assert sum(view["regimes"].values()) == 6
    # label shares are per distinct datapoint: every top datapoint carries 'turn_off' or 'set', so the top label can reach 100%
    assert all(0 < share <= 1 for _, share in view["label_share"]) and view["label_share"][0][1] >= 1 / 6
    assert view["stats"]["max"] == pytest.approx(first["value"], rel=1e-4)
    assert 0 <= view["stats"]["density"] <= 1 and (view["stats"]["open_max"] is None or view["stats"]["open_max"] <= view["stats"]["max"] + 1e-6)
    assert interp.label_shares([{"datapoint": 1, "labels": ["a", "b"]}, {"datapoint": 1, "labels": ["a", "b"]}, {"datapoint": 2, "labels": ["a"]}, {"datapoint": 3, "labels": []}]) == [["a", 0.667], ["b", 0.333], ["(unlabelled)", 0.333]]
    # explanation prompt is built offline and names the bracketed token convention
    prompt = interp.explain_prompt(view)
    assert "[" in prompt and "verb-like" in prompt and "STRONGEST NEGATIVE" in prompt
    with pytest.raises(ProjectError, match="has no layer 0"):
        interp.neuron_view(project, meta["name"], 0, 0)
    with pytest.raises(ProjectError, match="already exists"):
        interp.capture(project, interp.CaptureSpec(model_id=str(model_dir), dataset="cmds", layers=[1], name=meta["name"]), progress=lambda *_: None)
    assert interp.list_captures(project)[0]["explained"] == 0
    interp.delete_capture(project, meta["name"])
    assert interp.list_captures(project) == []


@needs_tokenizer
def test_score_texts_and_diff_against_itself(tmp_path: Path):
    project = make_project(tmp_path)
    model_dir = tiny_model_dir(tmp_path)
    meta = interp.capture(project, interp.CaptureSpec(model_id=str(model_dir), dataset="cmds", layers=[0, 1], topk=4), progress=lambda *_: None)
    loaded = interp.load_model(project, str(model_dir), device="cpu")
    tokens = interp._tokens(project, meta["name"])
    assert len(tokens["prefix_ids"]) == meta["prefix_len"]
    corpus_row = {"user": "turn the lights off in the kitchen", "assistant": json.dumps({"action": "turn_off", "device": "lights", "room": "kitchen"})}
    scored = interp.score_texts(loaded, 1, [0, 5], [corpus_row, "hello"], "Reply with JSON.", prefix_ids=tokens["prefix_ids"])
    assert len(scored) == 2 and set(scored[0]["neurons"]) == {0, 5} and "max" in scored[0]["neurons"][0] and scored[0]["neurons"][0]["token"]
    assert scored[0]["text"].startswith("turn the lights off") and "⟶" in scored[0]["text"] and scored[1]["text"] == "hello"
    assert isinstance(scored[0]["neurons"][0]["gate_open"], bool)
    # a corpus exchange scores at most the corpus maximum of that neuron (same template, same prefix exclusion)
    view0 = interp.neuron_view(project, meta["name"], 1, 0)
    assert scored[0]["neurons"][0]["max"] <= view0["stats"]["max"] + 1e-4
    if view0["stats"]["open_max"] is not None:
        assert (scored[0]["neurons"][0]["open_max"] or -1e9) <= view0["stats"]["open_max"] + 1e-4
    # comparing a model with itself: correlation 1, no change
    d = interp.diff(project, interp.DiffSpec(experiment=str(model_dir), base=str(model_dir), dataset="cmds", layers=[1], batch_size=7), progress=lambda *_: None)
    assert d["mode"] == "two_models" and d["layer_summary"]["1"]["median_r"] == pytest.approx(1.0, abs=1e-5) and d["layer_summary"]["1"]["below_0_9"] == 0
    # a different model must show up as changed (guards against comparing a pass with itself)
    other = tiny_model_dir(tmp_path / "other")
    d2 = interp.diff(project, interp.DiffSpec(experiment=str(other), base=str(model_dir), dataset="cmds", layers=[1], name="other-vs-tiny"), progress=lambda *_: None)
    assert d2["layer_summary"]["1"]["median_r"] < 0.99 and d2["layer_summary"]["1"]["rel_change_median"] > 0.05
    interp.delete_diff(project, "other-vs-tiny")
    layer = interp.diff_layer(project, d["name"], 1, limit=3)
    assert len(layer["rows"]) == 3 and all(abs(r["max_delta"]) < 1e-4 and r["change"] < 1e-3 for r in layer["rows"])
    assert interp.diff_layer(project, d["name"], 1, limit=2, sort="rel_change")["sort"] == "rel_change"
    with pytest.raises(ProjectError, match="sort must be"):
        interp.diff_layer(project, d["name"], 1, sort="nope")
    assert d["layer_summary"]["1"]["constant"] == 0
    neuron = interp.diff_neuron(project, d["name"], 1, 0)
    assert neuron["datapoints"] == [] and neuron["r"] == pytest.approx(1.0, abs=1e-5) and neuron["change"] < 1e-3
    interp.delete_diff(project, d["name"])


@needs_tokenizer
def test_checkpoint_references_and_drift(tmp_path: Path):
    project = make_project(tmp_path)
    model_dir = tiny_model_dir(tmp_path)
    data.autosplit(project, "cmds", 80)
    data.create_snapshot(project, "cmds", "s1", min_training=5)
    from tinystudio.training.train import create_job, create_pending_experiment, experiment_dir

    create_job(project, "j", "s1")
    n = create_pending_experiment(project, "j", TrainConfig(base_model=str(model_dir), checkpoint_every_steps=2))
    exp = experiment_dir(project, "j", n)
    # fake two full-model checkpoints and a final model directory
    import shutil

    for step in (2, 4):
        shutil.copytree(model_dir, exp / "checkpoints" / f"checkpoint-{step}")
        with (exp / "checkpoints.jsonl").open("a") as handle:
            handle.write(json.dumps({"step": step, "epoch": step / 4, "loss": 1.0 / step, "path": str(exp / "checkpoints" / f"checkpoint-{step}")}) + "\n")
    shutil.copytree(model_dir, exp / "model")
    write_json(exp / "status.json", {"state": "succeeded"})
    checkpoints = interp.experiment_checkpoints(project, "j", n)
    assert [c["step"] for c in checkpoints] == [2, 4] and checkpoints[0]["loss"] == 0.5 and checkpoints[1]["model_id"] == f"j/{n}@4"
    assert resolve_model(project, f"j/{n}@2").kind == "full" and resolve_model(project, f"j/{n}@2").model_dir == exp / "checkpoints" / "checkpoint-2"
    with pytest.raises(ProjectError, match="no checkpoint at step 9"):
        resolve_model(project, f"j/{n}@9")
    metas = interp.capture_checkpoints(project, "j", n, "cmds", None, [1], topk=5, progress=lambda *_: None)
    assert [m["model_id"] for m in metas] == [f"j/{n}@2", f"j/{n}@4", f"j/{n}"]
    d = interp.drift(project, "j", n, "cmds", 1, 3)
    assert [s["step"] for s in d["steps"]] == [2, 4, None] and d["steps"][-1]["final"] is True
    assert d["steps"][0]["jaccard_prev"] is None and d["steps"][1]["jaccard_prev"] == 1.0 and d["steps"][2]["jaccard_final"] == 1.0  # identical weights
    assert d["steps"][1]["offshoot_candidates"] == [] and d["steps"][1]["new_labels"] == [] and d["list"] in ("gate_open", "positives")
    assert d["steps"][0]["datapoints"] >= 1 and set(d["core_labels"]) == set(d["first_seen"]) and all(st == 2 for st in d["first_seen"].values())
    with pytest.raises(ProjectError, match="no captures"):
        interp.drift(project, "j", n, "other", 1, 3)


def test_checkpoint_training_arguments():
    assert _checkpoint_args(TrainConfig(base_model="x")) == {"save_strategy": "no"}
    args = _checkpoint_args(TrainConfig(base_model="x", checkpoint_every_steps=10, keep_checkpoints=3))
    assert args == {"save_strategy": "steps", "save_steps": 10, "save_total_limit": 3, "save_only_model": True}
    assert _checkpoint_args(TrainConfig(base_model="x", checkpoint_every_steps=5))["save_total_limit"] is None
