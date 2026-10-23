"""Snapshots: immutable copies of a dataset's training and validation buckets.

New snapshots are Parquet shards in the artifact store (local directory or R2) with a checksum manifest; the
``snapshots`` row keeps the counts and the manifest prefix. Snapshots made before this layout keep their rows in
``snapshot_rows`` and are still readable.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.config import config
from tinystudio.storage import project_key, storage
from tinystudio.core.project import ProjectError, check_name, iso
from tinystudio.db.models import Project, Snapshot, SnapshotRow  # SnapshotRow: legacy snapshots frozen as SQL rows
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


SHARD_ROWS = 10_000


def _write_split(directory: Path, split: str, rows: Iterable[dict[str, Any]]) -> int:
    """Write rows as zstd Parquet shards of SHARD_ROWS JSON strings; returns the row count."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    count, shard, buffer = 0, 0, []

    def flush() -> None:
        nonlocal shard
        pq.write_table(pa.table({"row_json": pa.array(buffer, type=pa.string())}), directory / f"{split}-{shard:05d}.parquet", compression="zstd")
        shard += 1
        buffer.clear()

    for row in rows:
        buffer.append(json.dumps(_strip(row), ensure_ascii=False))
        count += 1
        if len(buffer) >= SHARD_ROWS:
            flush()
    if buffer:
        flush()
    return count


def freeze(project_id: str, snapshot_id: str, splits: dict[str, Iterable[dict[str, Any]]]) -> tuple[str, dict[str, int]]:
    """Publish a snapshot's rows as Parquet shards through the configured artifact backend (local or R2)."""
    prefix = project_key(project_id, "snapshots", snapshot_id)
    with tempfile.TemporaryDirectory(prefix="tinystudio-snapshot-") as tmp:
        directory = Path(tmp)
        written = {split: _write_split(directory, split, rows) for split, rows in splits.items()}
        storage().publish_directory(directory, prefix)
    return prefix, written


def create_snapshot(db: Session, project: Project, dataset_name: str, name: str, min_training: int = 10) -> dict[str, Any]:
    """Freeze the current training and validation buckets into Parquet; PostgreSQL keeps only the manifest pointer."""
    check_name(name, "snapshot")
    dataset = datasets_svc.get_dataset(db, project, dataset_name)
    if db.scalar(select(Snapshot.id).where(Snapshot.project_id == project.id, Snapshot.name == name)) is not None:
        raise ProjectError(f"snapshot {name!r} already exists; snapshots are immutable, pick a new name")
    datasets_svc._lock(db, dataset, shared=True)  # no writer can change the buckets while they are frozen
    buckets = datasets_svc.counts(db, dataset)
    if buckets["training"] < min_training:
        raise ProjectError(f"snapshot needs at least {min_training} training datapoints; dataset {dataset_name!r} has {buckets['training']} (split the dataset first)")
    snapshot = Snapshot(project_id=project.id, name=name, dataset_id=dataset.id, dataset_name=dataset.name, training_count=buckets["training"], validation_count=buckets["validation"])
    db.add(snapshot)
    db.flush()
    prefix, written = freeze(project.id, snapshot.id, {"train": datasets_svc.iter_rows(db, dataset, bucket="training"), "val": datasets_svc.iter_rows(db, dataset, bucket="validation")})
    if (written["train"], written["val"]) != (snapshot.training_count, snapshot.validation_count):
        storage().delete_directory(prefix)
        raise ProjectError("the dataset changed while the snapshot was being written; try again")
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
