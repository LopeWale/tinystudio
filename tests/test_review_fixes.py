"""Regressions for the findings of the 2026-10-21 code review."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinystudio.core import data
from tinystudio.core.documents import chunk_text
from tinystudio.evaluation.evaluate import _passed
from tinystudio.core.project import Project, write_json
from tinystudio.serving.runner import resolve_model
from tinystudio.training.train import claim_experiment_dir, create_job


def row(user: str, answer: str, **extra):
    return {"messages": [{"role": "user", "content": user}, {"role": "assistant", "content": answer}], **extra}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    p = Project.create(tmp_path / "p", "p")
    (p.dataset_dir("d")).mkdir()
    data.save_dataset(p, "d", [data.validate_datapoint(row(f"q{i}", f"a{i}")) for i in range(12)])
    data.autosplit(p, "d", 90)
    data.create_snapshot(p, "d", "s1")
    return p


def test_judge_pass_field_is_not_truthy_string():
    assert _passed(True) and _passed("true") and _passed("PASS") and _passed(1)
    assert not _passed("false") and not _passed("fail") and not _passed(None) and not _passed(0) and not _passed("")


def test_chunks_are_bounded_and_non_empty():
    for text, max_chars in (("x" * 1000 + "\n\n" + "y" * 500, 1000), ("\n\n".join("p" * 900 for _ in range(3)), 1000), ("a" * 5000, 1000)):
        chunks = chunk_text(text, max_chars=max_chars, overlap=100)
        assert chunks and all(chunks), chunks
        assert all(len(c) <= max_chars for c in chunks), [len(c) for c in chunks]


def test_add_datapoints_bucket_override_applies(project: Project):
    data.add_datapoints(project, "d", [row("x", "y")], "validation")
    data.add_datapoints(project, "d", [row("x2", "y2", bucket="training")])
    rows = data.load_dataset(project, "d")
    assert rows[-2]["bucket"] == "validation" and rows[-1]["bucket"] == "training"
    with pytest.raises(data.DatapointError, match="bucket"):
        data.add_datapoints(project, "d", [row("x", "y")], "test")


def test_experiment_numbering_skips_deleted_and_existing_dirs(project: Project):
    create_job(project, "j", "s1")
    for n in (1, 2):
        number, exp_dir = claim_experiment_dir(project, "j")
        assert number == n
        write_json(exp_dir / "status.json", {"state": "succeeded"})
        write_json(exp_dir / "config.json", {})
    import shutil

    shutil.rmtree(project.job_dir("j") / "experiments" / "1")
    assert claim_experiment_dir(project, "j")[0] == 3
    (project.job_dir("j") / "experiments" / "4").mkdir()  # stray directory without status.json
    assert claim_experiment_dir(project, "j")[0] == 5


def test_resolve_model_prefers_directories_and_rejects_traversal(project: Project, tmp_path: Path):
    model_dir = tmp_path / "merged"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    assert resolve_model(project, str(model_dir)).kind == "full"
    assert resolve_model(project, "job/../../x").kind == "base"
    assert resolve_model(project, "HuggingFaceTB/SmolLM2-135M-Instruct").kind == "base"


def test_tree_ignores_partial_dirs_and_keeps_orphan_snapshots(project: Project):
    (project.root / "snapshots" / "half-written").mkdir()
    (project.root / "jobs" / "empty").mkdir()
    tree = project.tree()
    assert [s["name"] for s in tree["datasets"][0]["snapshots"]] == ["s1"]
    import shutil

    shutil.rmtree(project.dataset_dir("d"))
    tree = project.tree()
    assert tree["datasets"][0]["missing"] is True and tree["datasets"][0]["snapshots"][0]["name"] == "s1"


def test_eval_failed_state_can_be_rerun(project: Project):
    from tinystudio.evaluation import evaluate

    eval_dir = project.eval_dir("e")
    write_json(eval_dir / "eval.json", {"name": "e", "state": "failed"})
    evaluate.create_metric(project, "m", "code", check="exact_match")
    # no models to load: an empty model list must still replace the failed record rather than refuse the name
    summary = evaluate.run_eval(project, "e", "s1", [], ["m"], None, progress=lambda _: None)
    assert summary["state"] == "succeeded" and summary["leaderboard"] == []
    with pytest.raises(Exception, match="already exists"):
        evaluate.run_eval(project, "e", "s1", [], ["m"], None, progress=lambda _: None)
    assert json.loads((eval_dir / "eval.json").read_text())["state"] == "succeeded"
