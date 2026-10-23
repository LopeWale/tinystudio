"""Interpretability: the store that binds :mod:`tinystudio.interp.neurons` to a project's rows, models, and artifact directory."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from filelock import FileLock
from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.config import config
from tinystudio.storage import ArtifactNotFound, StorageError, project_key, storage
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

    def _directory(self, kind: str, name: str) -> Path:
        if kind not in {"capture", "diff"}:
            raise InterpError("invalid interpretability artifact kind")
        project_key(self.project_id, "captures", kind, name)
        return (self.captures_dir if kind == "capture" else self.diffs_dir) / name

    def _artifact_lock(self, kind: str, name: str) -> FileLock:
        self._directory(kind, name)
        directory = config().project_dir(self.project_id) / "interp" / ".locks"
        directory.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(f"{kind}/{name}".encode()).hexdigest()
        # Lock files live outside hydrated/deleted artifact directories and must
        # retain their inode while another process could still be waiting on it.
        return FileLock(directory / f"{identity}.lock")

    def artifact_directory(self, kind: str, name: str) -> Path:
        with self._artifact_lock(kind, name):
            return self._hydrate_artifact(kind, name)

    def _hydrate_artifact(self, kind: str, name: str) -> Path:
        directory = self._directory(kind, name)
        with session() as db:
            row = db.scalar(select(InterpCapture).where(InterpCapture.project_id == self.project_id, InterpCapture.kind == kind, InterpCapture.name == name))
            prefix = (row.meta or {}).get("storage_prefix") if row else None
        if prefix:
            try:
                storage().hydrate_directory(prefix, directory, refresh=True)
            except StorageError as cause:
                raise InterpError(f"{kind} {name!r} is unavailable: {cause}") from cause
        return directory

    def list_artifacts(self, kind: str) -> list[dict[str, Any]]:
        with session() as db:
            records = [(row.name, dict(row.meta)) for row in db.scalars(select(InterpCapture).where(InterpCapture.project_id == self.project_id, InterpCapture.kind == kind).order_by(InterpCapture.created))]
        out = []
        for name, meta in records:
            try:
                directory = self.artifact_directory(kind, name)
            except InterpError as cause:
                if isinstance(cause.__cause__, ArtifactNotFound):
                    out.append({**meta, "available": False, "error": "artifacts expired or deleted"})
                    continue
                raise
            if (directory / "meta.json").is_file():
                if kind == "capture":
                    meta.update(explained=len(neurons.latest_records(directory / "explanations.jsonl")), tested=len(neurons.latest_records(directory / "tests.jsonl")))
                out.append(meta)
        return out

    def delete_artifact(self, kind: str, name: str) -> None:
        with self._artifact_lock(kind, name):
            self._delete_artifact(kind, name)

    def _delete_artifact(self, kind: str, name: str) -> None:
        directory = self._directory(kind, name)
        with session() as db:
            row = db.scalar(select(InterpCapture).where(InterpCapture.project_id == self.project_id, InterpCapture.kind == kind, InterpCapture.name == name))
            if row is None and not (directory / "meta.json").is_file():
                raise InterpError(f"{kind} {name!r} does not exist")
            prefix = (row.meta or {}).get("storage_prefix") if row else None
            backend = storage()
            if prefix:
                backend.delete_directory(prefix)
            elif row is None and backend.remote:
                # A failed publication can leave scratch artifacts before DB registration.
                backend.delete_directory(project_key(self.project_id, "captures", kind, name))
            if row is not None:
                db.delete(row)
        shutil.rmtree(directory, ignore_errors=True)

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

    def append_record(self, name: str, filename: str, record: dict[str, Any], meta: dict[str, Any]) -> None:
        """Serialize refresh, sidecar mutation and publication across local workers."""
        if filename not in {"explanations.jsonl", "tests.jsonl"}:
            raise InterpError("invalid capture sidecar")
        with self._artifact_lock("capture", name):
            directory = self._hydrate_artifact("capture", name)
            if not (directory / "meta.json").is_file():
                raise InterpError(f"capture {name!r} does not exist")
            neurons._append_jsonl(directory / filename, record)
            self._register("capture", name, meta)

    def register(self, kind: str, name: str, meta: dict[str, Any]) -> None:
        with self._artifact_lock(kind, name):
            self._register(kind, name, meta)

    def _register(self, kind: str, name: str, meta: dict[str, Any]) -> None:
        directory = self._directory(kind, name)
        backend = storage()
        if backend.remote:
            prefix = project_key(self.project_id, "captures", kind, name)
            backend.publish_directory(directory, prefix)
            meta = {**meta, "storage_prefix": prefix}
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
