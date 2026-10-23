"""Snapshots: immutable copies of a dataset's training and validation buckets."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from tinystudio.config import config
from tinystudio.storage import project_key, storage
from tinystudio.core.project import ProjectError, check_name, iso
from tinystudio.db.models import Project, Snapshot, SnapshotRow
from tinystudio.services import datasets as datasets_svc


def snapshot_dict(snapshot: Snapshot) -> dict[str, Any]:
    return {"id": snapshot.id, "name": snapshot.name, "dataset": snapshot.dataset_name, "training_count": snapshot.training_count, "validation_count": snapshot.validation_count, "created": iso(snapshot.created)}


def get_snapshot(db: Session, project: Project, name: str) -> Snapshot:
    snapshot = db.scalar(select(Snapshot).where(Snapshot.project_id == project.id, Snapshot.name == name))
    if snapshot is None:
        raise ProjectError(f"snapshot {name!r} does not exist")
    return snapshot


def list_snapshots(db: Session, project: Project) -> list[dict[str, Any]]:
    return [snapshot_dict(s) for s in db.scalars(select(Snapshot).where(Snapshot.project_id == project.id).order_by(Snapshot.created))]


def create_snapshot(db: Session, project: Project, dataset_name: str, name: str, min_training: int = 10) -> dict[str, Any]:
    """Freeze the current training and validation buckets."""
    check_name(name, "snapshot")
    dataset = datasets_svc.get_dataset(db, project, dataset_name)
    if db.scalar(select(Snapshot.id).where(Snapshot.project_id == project.id, Snapshot.name == name)) is not None:
        raise ProjectError(f"snapshot {name!r} already exists; snapshots are immutable, pick a new name")
    train = datasets_svc.rows(db, dataset, bucket="training")
    val = datasets_svc.rows(db, dataset, bucket="validation")
    if len(train) < min_training:
        raise ProjectError(f"snapshot needs at least {min_training} training datapoints; dataset {dataset_name!r} has {len(train)} (split the dataset first)")
    snapshot = Snapshot(project_id=project.id, name=name, dataset_id=dataset.id, dataset_name=dataset.name, training_count=len(train), validation_count=len(val))
    db.add(snapshot)
    db.flush()
    payload = [{"snapshot_id": snapshot.id, "split": "train", "position": i, "row": _strip(r)} for i, r in enumerate(train)]
    payload += [{"snapshot_id": snapshot.id, "split": "val", "position": i, "row": _strip(r)} for i, r in enumerate(val)]
    for offset in range(0, len(payload), 2000):
        db.execute(insert(SnapshotRow), payload[offset : offset + 2000])
    backend = storage()
    if backend.remote:
        prefix = project_key(project.id, "snapshots", snapshot.id)
        with tempfile.TemporaryDirectory(prefix="tinystudio-snapshot-") as tmp:
            directory = Path(tmp)
            import pyarrow as pa
            import pyarrow.parquet as pq

            for split, rows in (("train", train), ("val", val)):
                for offset in range(0, len(rows), 10000):
                    values = [json.dumps(_strip(row), ensure_ascii=False) for row in rows[offset:offset + 10000]]
                    pq.write_table(pa.table({"row_json": pa.array(values, type=pa.string())}), directory / f"{split}-{offset // 10000:05d}.parquet", compression="zstd")
            backend.publish_directory(directory, prefix)
        snapshot.storage_prefix = prefix
        db.flush()
    return snapshot_dict(snapshot)


def _strip(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "id"}


def snapshot_rows(db: Session, snapshot: Snapshot) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prefix = getattr(snapshot, "storage_prefix", None)
    if prefix:
        import pyarrow.parquet as pq

        directory = config().project_dir(snapshot.project_id) / "snapshots" / snapshot.id
        storage().hydrate_directory(prefix, directory)
        def read(split: str) -> list[dict[str, Any]]:
            return [json.loads(value) for path in sorted(directory.glob(f"{split}-*.parquet")) for value in pq.read_table(path)["row_json"].to_pylist()]
        train, val = read("train"), read("val")
        if len(train) != snapshot.training_count or len(val) != snapshot.validation_count:
            raise ProjectError("snapshot artifact row counts do not match the frozen snapshot")
        return train, val
    train = [r.row for r in db.scalars(select(SnapshotRow).where(SnapshotRow.snapshot_id == snapshot.id, SnapshotRow.split == "train").order_by(SnapshotRow.position))]
    val = [r.row for r in db.scalars(select(SnapshotRow).where(SnapshotRow.snapshot_id == snapshot.id, SnapshotRow.split == "val").order_by(SnapshotRow.position))]
    return train, val


def load_snapshot(db: Session, project: Project, name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return snapshot_rows(db, get_snapshot(db, project, name))


def delete_snapshot(db: Session, project: Project, name: str) -> None:
    snapshot = get_snapshot(db, project, name)
    if getattr(snapshot, "storage_prefix", None):
        storage().delete_directory(snapshot.storage_prefix)
    db.delete(snapshot)
    db.flush()
