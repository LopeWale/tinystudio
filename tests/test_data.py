from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinystudio import data
from tinystudio.project import Project, ProjectError


def row(user: str, answer: str, **extra):
    return {"messages": [{"role": "user", "content": user}, {"role": "assistant", "content": answer}], **extra}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.create(tmp_path / "p", "p", "goal")


def write_jsonl(path: Path, rows) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_validate_datapoint_rejects_bad_shapes():
    with pytest.raises(data.DatapointError, match="messages"):
        data.validate_datapoint({"messages": []})
    with pytest.raises(data.DatapointError, match="assistant"):
        data.validate_datapoint({"messages": [{"role": "user", "content": "hi"}]})
    with pytest.raises(data.DatapointError, match="system message is only allowed first"):
        data.validate_datapoint({"messages": [{"role": "user", "content": "hi"}, {"role": "system", "content": "x"}, {"role": "assistant", "content": "y"}]})
    with pytest.raises(data.DatapointError, match="bucket"):
        data.validate_datapoint(row("a", "b", bucket="test"))


def test_validate_datapoint_defaults_and_strips_unknown_keys():
    clean = data.validate_datapoint(row("a", "b", extra="ignored", source="synthetic:x"))
    assert clean == {"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}], "bucket": "uncategorized", "labels": [], "source": "synthetic:x"}


def test_read_jsonl_reports_line_numbers(tmp_path: Path):
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(row("a", "b")) + "\n{not json\n", encoding="utf-8")
    with pytest.raises(data.DatapointError, match=r"bad\.jsonl:2: invalid JSON"):
        list(data.read_jsonl(path))


def test_dataset_split_snapshot_roundtrip(project: Project, tmp_path: Path):
    rows = [row(f"q{i}", f"a{i}") for i in range(20)]
    source = write_jsonl(tmp_path / "in.jsonl", rows)
    meta = data.create_dataset_from_jsonl(project, "d", source)
    assert meta["datapoints"] == 20 and meta["buckets"]["uncategorized"] == 20

    with pytest.raises(ProjectError, match="at least 10 training"):
        data.create_snapshot(project, "d", "s0")

    meta = data.autosplit(project, "d", training_pct=80, seed=1)
    assert meta["buckets"] == {"uncategorized": 0, "training": 16, "validation": 4}

    # a second split must not move anything: manual/previous buckets survive
    assert data.autosplit(project, "d", training_pct=50)["buckets"] == meta["buckets"]

    snap = data.create_snapshot(project, "d", "s1")
    assert (snap["training_count"], snap["validation_count"]) == (16, 4)
    train, val = data.load_snapshot(project, "s1")
    assert len(train) == 16 and len(val) == 4
    with pytest.raises(ProjectError, match="immutable"):
        data.create_snapshot(project, "d", "s1")


def test_autosplit_keeps_synthetic_out_of_validation(project: Project, tmp_path: Path):
    rows = [row(f"q{i}", f"a{i}", source="synthetic:doc") for i in range(10)] + [row(f"h{i}", f"b{i}") for i in range(10)]
    data.create_dataset_from_jsonl(project, "d", write_jsonl(tmp_path / "in.jsonl", rows))
    data.autosplit(project, "d", training_pct=50, allow_synthetic_in_validation=False)
    val = [r for r in data.load_dataset(project, "d") if r["bucket"] == "validation"]
    assert len(val) == 10 and all(not r.get("source", "").startswith("synthetic") for r in val)


def test_project_tree_lists_snapshots_under_datasets(project: Project, tmp_path: Path):
    data.create_dataset_from_jsonl(project, "d", write_jsonl(tmp_path / "in.jsonl", [row(f"q{i}", f"a{i}") for i in range(12)]))
    data.autosplit(project, "d", 90)
    data.create_snapshot(project, "d", "s1")
    tree = project.tree()
    assert tree["datasets"][0]["name"] == "d"
    assert tree["datasets"][0]["snapshots"][0]["name"] == "s1"


def test_names_are_validated(project: Project):
    with pytest.raises(ProjectError, match="name"):
        project.dataset_dir("../escape")
