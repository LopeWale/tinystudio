"""Datasets and datapoints in PostgreSQL."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session

from tinystudio.core.data import BUCKETS, DatapointError, assign_split, read_jsonl, validate_datapoint
from tinystudio.core.project import ProjectError, check_name, iso
from tinystudio.db.models import CleaningRun, Datapoint, Dataset, Project, Snapshot

ROW_FIELDS = ("messages", "bucket", "labels", "source", "rating", "rejected")


def row_dict(dp: Datapoint) -> dict[str, Any]:
    row: dict[str, Any] = {"id": dp.id, "messages": dp.messages, "bucket": dp.bucket, "labels": dp.labels or []}
    if dp.source is not None:
        row["source"] = dp.source
    if dp.rating is not None:
        row["rating"] = dp.rating
    if dp.rejected is not None:
        row["rejected"] = dp.rejected
    return row


def _insert_rows(db: Session, dataset_id: str, rows: list[dict[str, Any]], start: int) -> None:
    if not rows:
        return
    payload = [{"dataset_id": dataset_id, "position": start + i, "messages": r["messages"], "bucket": r.get("bucket", "uncategorized"), "labels": r.get("labels", []), "source": r.get("source"), "rating": r.get("rating"), "rejected": r.get("rejected")} for i, r in enumerate(rows)]
    for offset in range(0, len(payload), 2000):
        db.execute(insert(Datapoint), payload[offset : offset + 2000])


def get_dataset(db: Session, project: Project, name: str) -> Dataset:
    dataset = db.scalar(select(Dataset).where(Dataset.project_id == project.id, Dataset.name == name))
    if dataset is None:
        raise ProjectError(f"dataset {name!r} does not exist")
    return dataset


def counts(db: Session, dataset: Dataset) -> dict[str, int]:
    rows = db.execute(select(Datapoint.bucket, func.count()).where(Datapoint.dataset_id == dataset.id).group_by(Datapoint.bucket)).all()
    out = {bucket: 0 for bucket in BUCKETS}
    for bucket, n in rows:
        out[bucket] = int(n)
    return out


def meta(db: Session, dataset: Dataset) -> dict[str, Any]:
    buckets = counts(db, dataset)
    snapshot_count = db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.dataset_id == dataset.id)) or 0
    return {"id": dataset.id, "name": dataset.name, "datapoints": sum(buckets.values()), "buckets": buckets, "labels": dataset.labels or [], "origin": dataset.origin, "created": iso(dataset.created), "updated": iso(dataset.updated), "snapshot_count": int(snapshot_count)}


def list_datasets(db: Session, project: Project) -> list[dict[str, Any]]:
    return [meta(db, d) for d in db.scalars(select(Dataset).where(Dataset.project_id == project.id).order_by(Dataset.created))]


def create_dataset(db: Session, project: Project, name: str, rows: list[dict[str, Any]], origin: dict[str, Any] | None = None) -> dict[str, Any]:
    check_name(name, "dataset")
    if db.scalar(select(Dataset.id).where(Dataset.project_id == project.id, Dataset.name == name)) is not None:
        raise ProjectError(f"dataset {name!r} already exists")
    clean = [validate_datapoint(r, f"record {i + 1}") for i, r in enumerate(rows)]
    dataset = Dataset(project_id=project.id, name=name, origin=origin, labels=[])
    db.add(dataset)
    db.flush()
    _insert_rows(db, dataset.id, clean, 0)
    return meta(db, dataset)


def create_from_jsonl(db: Session, project: Project, name: str, source: Path, origin_path: str | None = None) -> dict[str, Any]:
    rows = list(read_jsonl(source))
    if not rows:
        raise ProjectError(f"{source} holds no datapoints")
    return create_dataset(db, project, name, rows, {"kind": "jsonl", "path": origin_path or str(source)})


def count(db: Session, dataset: Dataset) -> int:
    return int(db.scalar(select(func.count()).select_from(Datapoint).where(Datapoint.dataset_id == dataset.id)) or 0)


def rows(db: Session, dataset: Dataset, offset: int = 0, limit: int | None = None, bucket: str | None = None) -> list[dict[str, Any]]:
    query = select(Datapoint).where(Datapoint.dataset_id == dataset.id).order_by(Datapoint.position, Datapoint.id)
    if bucket:
        query = query.where(Datapoint.bucket == bucket)
    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return [row_dict(dp) for dp in db.scalars(query)]


def add_rows(db: Session, project: Project, name: str, new_rows: list[dict[str, Any]], bucket: str | None = None) -> dict[str, Any]:
    """Append records. With ``bucket`` every record is forced into that bucket; otherwise each record's own bucket is kept."""
    if bucket is not None and bucket not in BUCKETS:
        raise DatapointError(f"bucket must be one of {BUCKETS}")
    dataset = get_dataset(db, project, name)
    clean = []
    for i, row in enumerate(new_rows):
        value = validate_datapoint(row, f"record {i + 1}")
        if bucket is not None:
            value["bucket"] = bucket
        clean.append(value)
    start = int(db.scalar(select(func.coalesce(func.max(Datapoint.position), -1)).where(Datapoint.dataset_id == dataset.id))) + 1
    _insert_rows(db, dataset.id, clean, start)
    dataset.updated = func.now()
    return meta(db, dataset)


def replace_rows(db: Session, dataset: Dataset, new_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Overwrite every datapoint (cleaning, auto-labelling). One transaction, so readers see old or new, never half."""
    clean = [validate_datapoint(r, f"record {i + 1}") for i, r in enumerate(new_rows)]
    db.execute(delete(Datapoint).where(Datapoint.dataset_id == dataset.id))
    _insert_rows(db, dataset.id, clean, 0)
    dataset.updated = func.now()
    return meta(db, dataset)


def delete_rows(db: Session, dataset: Dataset, ids: list[int]) -> dict[str, Any]:
    if ids:
        db.execute(delete(Datapoint).where(Datapoint.dataset_id == dataset.id, Datapoint.id.in_(ids)))
        dataset.updated = func.now()
    return meta(db, dataset)


def autosplit(db: Session, project: Project, name: str, training_pct: int = 80, seed: int = 0, allow_synthetic_in_validation: bool = False) -> dict[str, Any]:
    dataset = get_dataset(db, project, name)
    current = rows(db, dataset)
    moves = assign_split(current, training_pct, seed, allow_synthetic_in_validation)
    if moves:
        db.execute(update(Datapoint), [{"id": current[index]["id"], "bucket": bucket} for index, bucket in moves.items()])
        dataset.updated = func.now()
    return meta(db, dataset)


def set_bucket(db: Session, dataset: Dataset, ids: list[int], bucket: str) -> dict[str, Any]:
    if bucket not in BUCKETS:
        raise DatapointError(f"bucket must be one of {BUCKETS}")
    if ids:
        db.execute(update(Datapoint).where(Datapoint.dataset_id == dataset.id, Datapoint.id.in_(ids)).values(bucket=bucket))
    return meta(db, dataset)


def set_labels(db: Session, project: Project, name: str, labels: list[dict[str, str]]) -> dict[str, Any]:
    """Define the label vocabulary (name + description), as Studio's create-labels does."""
    dataset = get_dataset(db, project, name)
    clean, seen = [], set()
    for label in labels:
        label_name = str(label.get("name", "")).strip()
        if not label_name or label_name in seen:
            continue
        seen.add(label_name)
        clean.append({"name": label_name, "description": str(label.get("description", "")).strip()})
    dataset.labels = clean
    return meta(db, dataset)


def delete_dataset(db: Session, project: Project, name: str) -> None:
    dataset = get_dataset(db, project, name)
    db.delete(dataset)
    db.flush()


def apply_cleaning(db: Session, project: Project, name: str, spec: Any) -> dict[str, Any]:
    from tinystudio.core.quality import clean, redact_pii

    dataset = get_dataset(db, project, name)
    current = rows(db, dataset)
    redacted = 0
    if spec.redact_pii:
        current, redacted = redact_pii(current)
    kept, removed = clean(current, spec)
    result = replace_rows(db, dataset, kept)
    run = CleaningRun(dataset_id=dataset.id, before=len(current), after=len(kept), removed=removed, redacted_rows=redacted, spec=spec.__dict__)
    db.add(run)
    db.flush()
    return {"time": iso(run.time) or "", "before": run.before, "after": run.after, "removed": removed, "redacted_rows": redacted, "spec": spec.__dict__, "meta": result}


def cleaning_history(db: Session, dataset: Dataset) -> list[dict[str, Any]]:
    return [{"time": iso(r.time), "before": r.before, "after": r.after, "removed": r.removed, "redacted_rows": r.redacted_rows, "spec": r.spec} for r in db.scalars(select(CleaningRun).where(CleaningRun.dataset_id == dataset.id).order_by(CleaningRun.time))]


def profile(db: Session, dataset: Dataset) -> dict[str, Any]:
    from tinystudio.core.quality import profile as quality_profile

    return quality_profile(rows(db, dataset))
