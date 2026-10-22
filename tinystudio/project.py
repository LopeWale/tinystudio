"""Project layout and manifest.

A project is a directory. Everything is a file you can inspect, diff, and
delete: no database, no account, no network unless you point a step at an
LLM endpoint you run yourself.

    <project>/
      tinystudio.json                 manifest: name, goal, created
      datasets/<name>/datapoints.jsonl  one {"messages":[...], "bucket": ...} per line
      datasets/<name>/meta.json
      snapshots/<name>/train.jsonl    frozen split
      snapshots/<name>/val.jsonl
      snapshots/<name>/meta.json
      jobs/<name>/job.json            fine-tuning job: snapshot + experiments
      jobs/<name>/experiments/<n>/    config.json, loss.jsonl, status.json, adapter/ or model/
      metrics/<name>.json             rubric metric: should / should-not rules
      evals/<name>/eval.json          evaluation run: snapshot, models, metrics
      evals/<name>/results.jsonl      one row per (datapoint, model, metric)
      traces.jsonl                    scored inferences, promotable into a dataset
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST = "tinystudio.json"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")  # Studio-style names such as "Strict JSON" are allowed
# A directory only counts as an entity once its marker file exists, so interrupted writes and stray folders are ignored.
MARKERS = {"datasets": "meta.json", "snapshots": "meta.json", "jobs": "job.json", "evals": "eval.json"}


class ProjectError(RuntimeError):
    """A project-level precondition failed; the message says which one."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_name(name: str, kind: str) -> str:
    if not SAFE_NAME.match(name):
        raise ProjectError(
            f"{kind} name {name!r} must be 1-64 characters of letters, digits, spaces, '.', '_' or '-' and start with a letter or digit"
        )
    return name


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    tmp.replace(path)


@dataclass(frozen=True)
class Project:
    root: Path

    @classmethod
    def create(cls, root: Path, name: str, goal: str = "") -> "Project":
        root = root.resolve()
        if (root / MANIFEST).exists():
            raise ProjectError(f"{root} already holds a tinystudio project")
        root.mkdir(parents=True, exist_ok=True)
        for sub in ("datasets", "snapshots", "jobs", "metrics", "evals"):
            (root / sub).mkdir(exist_ok=True)
        write_json(
            root / MANIFEST,
            {"name": check_name(name, "project"), "goal": goal, "created": now()},
        )
        return cls(root)

    @classmethod
    def open(cls, root: Path) -> "Project":
        root = root.resolve()
        if not (root / MANIFEST).exists():
            raise ProjectError(
                f"{root} is not a tinystudio project (missing {MANIFEST}); run `tinystudio init` first"
            )
        return cls(root)

    @property
    def manifest(self) -> dict[str, Any]:
        return read_json(self.root / MANIFEST)

    def dataset_dir(self, name: str) -> Path:
        return self.root / "datasets" / check_name(name, "dataset")

    def snapshot_dir(self, name: str) -> Path:
        return self.root / "snapshots" / check_name(name, "snapshot")

    def job_dir(self, name: str) -> Path:
        return self.root / "jobs" / check_name(name, "job")

    def metric_path(self, name: str) -> Path:
        return self.root / "metrics" / f"{check_name(name, 'metric')}.json"

    def eval_dir(self, name: str) -> Path:
        return self.root / "evals" / check_name(name, "evaluation")

    @property
    def traces_path(self) -> Path:
        return self.root / "traces.jsonl"

    def list_names(self, kind: str) -> list[str]:
        base = self.root / kind
        if not base.exists():
            return []
        if kind == "metrics":
            return sorted(p.stem for p in base.glob("*.json"))
        marker = MARKERS.get(kind)
        return sorted(p.name for p in base.iterdir() if p.is_dir() and (marker is None or (p / marker).exists()))

    def tree(self) -> dict[str, Any]:
        """Project tree in the spirit of Studio's GET /projects/{id}: datasets → snapshots → jobs → experiments."""
        tree: dict[str, Any] = {"project": self.manifest, "datasets": []}
        jobs = [read_json(self.job_dir(job) / "job.json") for job in self.list_names("jobs")]
        snapshots_by_dataset: dict[str, list[dict[str, Any]]] = {}
        for snap in self.list_names("snapshots"):
            meta = read_json(self.snapshot_dir(snap) / "meta.json")
            snapshots_by_dataset.setdefault(meta["dataset"], []).append(
                {"name": snap, **meta, "jobs": [job for job in jobs if job.get("snapshot") == snap]}
            )
        known = self.list_names("datasets")
        for name in known:
            meta = read_json(self.dataset_dir(name) / "meta.json")
            tree["datasets"].append(
                {"name": name, **meta, "snapshots": snapshots_by_dataset.get(name, [])}
            )
        for name, snapshots in snapshots_by_dataset.items():
            if name not in known:  # dataset directory deleted; its snapshots still train
                tree["datasets"].append(
                    {
                        "name": name,
                        "missing": True,
                        "datapoints": 0,
                        "buckets": {"uncategorized": 0, "training": 0, "validation": 0},
                        "snapshots": snapshots,
                    }
                )
        return tree
