"""Interpretability: the store that binds :mod:`tinystudio.interp.neurons` to a project's rows, models, and artifact directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.config import config
from tinystudio.core.project import iso
from tinystudio.db import session
from tinystudio.db.models import InterpCapture, Project
from tinystudio.interp import neurons
from tinystudio.interp.neurons import InterpError


class ProjectStore:
    """InterpStore for one project; every database read opens its own short session (captures run for a long time)."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        root = config().project_dir(project_id) / "interp"
        self.captures_dir = root / "captures"
        self.diffs_dir = root / "diffs"

    def _project(self, db: Session) -> Project:
        project = db.get(Project, self.project_id)
        if project is None:
            raise InterpError("project does not exist")
        return project

    def rows_for(self, dataset: str | None, snapshot: str | None) -> list[dict[str, Any]]:
        from tinystudio.services import datasets as datasets_svc
        from tinystudio.services import snapshots as snapshots_svc

        with session() as db:
            project = self._project(db)
            if dataset:
                return [{k: v for k, v in r.items() if k != "id"} for r in datasets_svc.rows(db, datasets_svc.get_dataset(db, project, dataset))]
            train, val = snapshots_svc.load_snapshot(db, project, snapshot or "")
            return train + val

    def resolve_model(self, model_id: str) -> Any:
        from tinystudio.services.models import resolve

        with session() as db:
            return resolve(db, self._project(db), model_id)

    def experiment_checkpoints(self, job: str, number: int) -> list[dict[str, Any]]:
        from tinystudio.services.jobs import checkpoints

        with session() as db:
            return checkpoints(db, self._project(db), job, number)

    def experiment_succeeded(self, job: str, number: int) -> bool:
        from tinystudio.services.jobs import get_experiment

        with session() as db:
            return get_experiment(db, self._project(db), job, number).state == "succeeded"

    def register(self, kind: str, name: str, meta: dict[str, Any]) -> None:
        with session() as db:
            row = db.scalar(select(InterpCapture).where(InterpCapture.project_id == self.project_id, InterpCapture.kind == kind, InterpCapture.name == name))
            directory = (self.captures_dir if kind == "capture" else self.diffs_dir) / name
            relative = str(directory.relative_to(config().data_dir))
            if row is None:
                db.add(InterpCapture(project_id=self.project_id, kind=kind, name=name, model_id=str(meta.get("model_id") or meta.get("experiment") or ""), source=str(meta.get("source") or ""), meta=meta, artifact_dir=relative))
            else:
                row.meta, row.model_id, row.source, row.artifact_dir = meta, str(meta.get("model_id") or meta.get("experiment") or ""), str(meta.get("source") or ""), relative

    def unregister(self, kind: str, name: str) -> None:
        with session() as db:
            row = db.scalar(select(InterpCapture).where(InterpCapture.project_id == self.project_id, InterpCapture.kind == kind, InterpCapture.name == name))
            if row is not None:
                db.delete(row)


def store_for(project: Project) -> ProjectStore:
    return ProjectStore(project.id)


def registered(db: Session, project: Project, kind: str) -> list[dict[str, Any]]:
    return [{**row.meta, "registered": iso(row.created)} for row in db.scalars(select(InterpCapture).where(InterpCapture.project_id == project.id, InterpCapture.kind == kind).order_by(InterpCapture.created))]


def overview(db: Session, project: Project) -> dict[str, Any]:
    store = store_for(project)
    return {"captures": neurons.list_captures(store), "comparisons": neurons.list_diffs(store)}


def artifact_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0
