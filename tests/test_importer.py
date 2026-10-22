"""Importing the pre-database file layout."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import row
from tinystudio.core.project import write_json
from tinystudio.services import datasets as datasets_svc
from tinystudio.services import evals as evals_svc
from tinystudio.services import jobs as jobs_svc
from tinystudio.services import projects as projects_svc
from tinystudio.services import traces as traces_svc
from tinystudio.services.importer import import_directory


def build_legacy_project(root: Path) -> None:
    write_json(root / "tinystudio.json", {"name": "legacy", "goal": "old goal", "created": "2026-01-01T00:00:00+00:00"})
    rows = [row(f"q{i}", f"a{i}", labels=["x"]) for i in range(12)]
    for i, r in enumerate(rows):
        r["bucket"] = "validation" if i < 2 else "training"
    (root / "datasets" / "d").mkdir(parents=True)
    (root / "datasets" / "d" / "datapoints.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    write_json(root / "datasets" / "d" / "meta.json", {"name": "d", "labels": [{"name": "x", "description": "ex"}], "origin": {"kind": "jsonl", "path": "/tmp/d.jsonl"}})
    (root / "snapshots" / "s1").mkdir(parents=True)
    (root / "snapshots" / "s1" / "train.jsonl").write_text("\n".join(json.dumps(r) for r in rows[2:]) + "\n")
    (root / "snapshots" / "s1" / "val.jsonl").write_text("\n".join(json.dumps(r) for r in rows[:2]) + "\n")
    write_json(root / "snapshots" / "s1" / "meta.json", {"name": "s1", "dataset": "d", "training_count": 10, "validation_count": 2, "created": "2026-01-02T00:00:00+00:00"})
    exp = root / "jobs" / "j" / "experiments" / "1"
    exp.mkdir(parents=True)
    write_json(root / "jobs" / "j" / "job.json", {"name": "j", "snapshot": "s1", "goal": "g", "reasoning": False, "created": "2026-01-03T00:00:00+00:00", "experiments": [1]})
    write_json(exp / "config.json", {"base_model": "HuggingFaceTB/SmolLM2-135M-Instruct", "training_type": "lora"})
    write_json(exp / "status.json", {"state": "succeeded", "train_loss": 0.5, "seconds": 12, "created": "2026-01-03T00:00:00+00:00"})
    (exp / "loss.jsonl").write_text(json.dumps({"step": 1, "loss": 1.0}) + "\n" + json.dumps({"step": 2, "loss": 0.5}) + "\n")
    (exp / "adapter").mkdir()
    (exp / "adapter" / "adapter_config.json").write_text("{}")
    write_json(root / "metrics" / "custom.json", {"name": "custom", "kind": "code", "check": "exact_match", "description": "", "created": "2026-01-01T00:00:00+00:00"})
    (root / "evals" / "e1").mkdir(parents=True)
    write_json(root / "evals" / "e1" / "eval.json", {"name": "e1", "snapshot": "s1", "models": ["j/1"], "metrics": ["custom"], "datapoints": 2, "state": "succeeded", "created": "2026-01-04T00:00:00+00:00", "leaderboard": [{"model": "j/1", "scores": {"custom": 1.0}, "overall": 1.0}]})
    (root / "evals" / "e1" / "results.jsonl").write_text(json.dumps({"datapoint": 0, "model": "j/1", "metric": "custom", "score": 1.0, "reason": "", "prediction": "a0", "reference": "a0", "user": "q0", "seconds": 0.1}) + "\n")
    (root / "traces.jsonl").write_text(json.dumps({"id": "t1", "time": "2026-01-05T00:00:00+00:00", "model": "j/1", "messages": [{"role": "user", "content": "q"}], "response": "a", "seconds": 0.2, "score": 0.9}) + "\n")
    (root / "autopilot").mkdir()
    (root / "autopilot" / "loop.jsonl").write_text(json.dumps({"cycle": 1, "name": "loop", "dataset": "d", "winner": "j/1", "finished": "2026-01-06T00:00:00+00:00"}) + "\n")
    write_json(root / ".api-keys.json", [{"id": "x", "sha256": "y"}])


def test_import_legacy_project(db, tmp_path: Path):
    root = tmp_path / "legacy"
    build_legacy_project(root)
    summary = import_directory(root, link=False, progress=lambda *_: None)
    assert summary["project"] == "legacy" and summary["datasets"] == 1 and summary["datapoints"] == 12 and summary["snapshots"] == 1 and summary["jobs"] == 1 and summary["experiments"] == 1
    assert summary["metrics"] == 1 and summary["evaluations"] == 1 and summary["traces"] == 1 and summary["autopilot_cycles"] == 1 and summary["skipped"]
    project = projects_svc.get_project(db, "legacy")
    assert project.goal == "old goal"
    meta = datasets_svc.meta(db, datasets_svc.get_dataset(db, project, "d"))
    assert meta["datapoints"] == 12 and meta["buckets"]["validation"] == 2 and meta["labels"][0]["name"] == "x"
    job = jobs_svc.list_jobs(db, project)[0]
    exp = job["experiments"][0]
    assert exp["state"] == "succeeded" and exp["train_loss"] == 0.5 and exp["alias"] == "ft-j-1"
    assert (jobs_svc.exp_path(jobs_svc.get_experiment(db, project, "j", 1)) / "adapter" / "adapter_config.json").exists()
    assert len(jobs_svc.loss_curve(db, jobs_svc.get_experiment(db, project, "j", 1))) == 2
    evaluation = evals_svc.get_eval(db, project, "e1")
    assert evaluation.leaderboard[0]["overall"] == 1.0 and len(evals_svc.eval_rows(db, evaluation)) == 1
    assert traces_svc.list_traces(db, project)[0]["score"] == 0.9
    from tinystudio.services.autopilot import list_histories

    assert list_histories(db, project)[0]["winner"] == "j/1"
