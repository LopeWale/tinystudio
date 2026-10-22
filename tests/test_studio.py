"""Tests for the Studio-replica additions: aliases, pending experiments, labels, metric editing, keys, curation."""

from __future__ import annotations

from pathlib import Path

import pytest

from tinystudio.core import data

from tinystudio.evaluation import evaluate

from tinystudio.serving import keys

from tinystudio.core import synth
from tinystudio.core.project import Project
from tinystudio.training.train import TrainConfig, alias_for, create_job, create_pending_experiment, delete_experiment, list_experiments, parse_alias


def row(user: str, answer: str, **extra):
    return {"messages": [{"role": "user", "content": user}, {"role": "assistant", "content": answer}], **extra}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    p = Project.create(tmp_path / "p", "p", "goal")
    data.save_dataset(p, "d", [data.validate_datapoint(row(f"q{i}", f"a{i}")) for i in range(12)])
    data.autosplit(p, "d", 90)
    data.create_snapshot(p, "d", "s1")
    return p


def test_aliases_roundtrip():
    assert alias_for("my-job", 3) == "ft-my-job-3"
    assert parse_alias("ft-my-job-3") == ("my-job", 3)
    assert parse_alias("my-job/3") is None and parse_alias("ft-x") is None


def test_pending_experiments_and_job_lifecycle(project: Project):
    create_job(project, "j", "s1", reasoning=True)
    n1 = create_pending_experiment(project, "j", TrainConfig(base_model="HuggingFaceTB/SmolLM2-135M-Instruct"))
    n2 = create_pending_experiment(project, "j", TrainConfig(base_model="Qwen/Qwen2.5-0.5B-Instruct", training_type="full"))
    exps = list_experiments(project, "j")
    assert [e["number"] for e in exps] == [n1, n2] and all(e["state"] == "pending" for e in exps)
    assert exps[0]["alias"] == "ft-j-1" and exps[1]["config"]["lora_targets"] == ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
    delete_experiment(project, "j", n1)
    assert [e["number"] for e in list_experiments(project, "j")] == [n2]
    with pytest.raises(Exception, match="does not exist"):
        delete_experiment(project, "j", 99)


def test_labels_and_rating_survive_validation(project: Project):
    meta = data.set_labels(project, "d", [{"name": "good", "description": "fine"}, {"name": "good", "description": "dup"}, {"name": "", "description": "x"}])
    assert meta["labels"] == [{"name": "good", "description": "fine"}]
    clean = data.validate_datapoint(row("a", "b", rating=7))
    assert clean["rating"] == 7.0
    assert "rating" not in data.validate_datapoint(row("a", "b", rating="high"))


def test_metric_update_delete_and_builtins(project: Project):
    evaluate.ensure_builtin_metrics(project)
    assert set(project.list_names("metrics")) >= {"Conciseness", "Correctness", "Hallucination"}
    updated = evaluate.update_metric(project, "Conciseness", should=["Be short"], should_not=None, description="desc")
    assert updated["rules"]["should"] == ["Be short"] and len(updated["rules"]["should_not"]) == 4 and updated["description"] == "desc"
    with pytest.raises(Exception, match="at least one rule"):
        evaluate.update_metric(project, "Conciseness", should=[], should_not=[])
    evaluate.delete_metric(project, "Conciseness")
    assert "Conciseness" not in project.list_names("metrics")
    evaluate.ensure_builtin_metrics(project)  # idempotent: recreates only what is missing
    assert "Conciseness" in project.list_names("metrics")


def test_api_keys_gate_only_when_defined(project: Project):
    assert keys.verify_key(project, None) is True
    record, plaintext = keys.create_key(project, "app")
    assert plaintext.startswith("ts-") and record["prefix"] == plaintext[:8] and "sha256" in record
    assert keys.verify_key(project, plaintext) is True
    assert keys.verify_key(project, "ts-wrong") is False and keys.verify_key(project, None) is False
    keys.revoke_key(project, record["id"])
    assert keys.verify_key(project, None) is True


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat_json(self, messages, **_):
        self.calls += 1
        return self.replies.pop(0) if self.replies else {"pairs": []}


def test_synthesize_and_curate_with_fake_client():
    source = synth.Source("notes.txt", "The capital of France is Paris. " * 40, "file")
    spec = synth.SynthSpec(pairs_per_source=2, curate_threshold=7, per_call=2)
    client = FakeClient([
        {"pairs": [{"question": "Capital of France?", "answer": "Paris."}, {"question": "Junk?", "answer": "Junk."}]},
        {"ratings": [{"index": 0, "rating": 9}, {"index": 1, "rating": 3}]},
    ])
    rows = synth.synthesize(client, [source], spec, progress=lambda _: None)
    assert len(rows) == 1 and rows[0]["rating"] == 9 and rows[0]["source"] == "synthetic:notes.txt"
    assert rows[0]["messages"][-1]["content"] == "Paris."


def test_synthesize_stops_when_helper_is_useless():
    source = synth.Source("notes.txt", "text " * 100, "file")
    client = FakeClient([])  # every call returns no pairs
    with pytest.raises(synth.LLMError, match="no usable pairs"):
        synth.synthesize(client, [source], synth.SynthSpec(pairs_per_source=3), progress=lambda _: None)
    assert client.calls >= 3


def test_auto_label_assigns_known_labels_only():
    rows = [data.validate_datapoint(row("lights on", "{}")), data.validate_datapoint(row("set heat", "{}"))]
    client = FakeClient([{"labels": [{"index": 0, "label": "lighting"}, {"index": 1, "label": "bogus"}]}])
    out = synth.auto_label(client, rows, [{"name": "lighting", "description": ""}, {"name": "climate", "description": ""}], progress=lambda _: None)
    assert out[0]["labels"] == ["lighting"] and out[1]["labels"] == []
