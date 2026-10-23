"""Datasets and datapoints in PostgreSQL.

Storage and retrieval rules (see the phase-two research report for the sources):

- ``datapoints`` is hash-partitioned by ``dataset_id``; every query filters on it, so one partition is read.
- Writes go through ``COPY`` (psycopg 3), not multi-row INSERT.
- Each dataset row carries its per-bucket ``counts``; reads never count rows. Every mutation locks the dataset
  row first, which serializes appends (positions stay unique) and keeps the counts exact.
- Paging uses a keyset on ``position`` (``after``); ``offset`` remains for numbered pages in the interface.
- Whole-dataset reads stream in batches (``iter_rows``) instead of loading every row at once.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from tinystudio.core.data import BUCKETS, DatapointError, assign_split, read_jsonl, validate_datapoint
from tinystudio.core.project import ProjectError, check_name, iso
from tinystudio.db.models import CleaningRun, Datapoint, Dataset, Project, Snapshot

ROW_FIELDS = ("messages", "bucket", "labels", "source", "rating", "rejected")
BATCH_ROWS = 5000
COPY_SQL = "COPY datapoints (dataset_id, position, messages, bucket, labels, source, rating, rejected) FROM STDIN"


def row_dict(dp: Datapoint) -> dict[str, Any]:
    row: dict[str, Any] = {"id": dp.id, "messages": dp.messages, "bucket": dp.bucket, "labels": dp.labels or []}
    if dp.source is not None:
        row["source"] = dp.source
    if dp.rating is not None:
        row["rating"] = dp.rating
    if dp.rejected is not None:
        row["rejected"] = dp.rejected
    return row


def _copy_rows(db: Session, dataset_id: str, rows: list[dict[str, Any]], start: int) -> None:
    """Bulk-load validated rows with COPY on the session's own connection (same transaction)."""
    if not rows:
        return
    from psycopg.types.json import Jsonb

    db.flush()  # the dataset row must exist before its datapoints reference it
    raw = db.connection().connection.driver_connection
    with raw.cursor() as cursor, cursor.copy(COPY_SQL) as copy:
        for i, r in enumerate(rows):
            copy.write_row((dataset_id, start + i, Jsonb(r["messages"]), r.get("bucket", "uncategorized"), Jsonb(r.get("labels", [])), r.get("source"), r.get("rating"), r.get("rejected")))


def _lock(db: Session, dataset: Dataset, shared: bool = False) -> None:
    """Row-lock the dataset for the rest of the transaction: writers exclusive, snapshot readers shared."""
    db.execute(select(Dataset.id).where(Dataset.id == dataset.id).with_for_update(read=shared))


def _recount(db: Session, dataset: Dataset) -> dict[str, int]:
    out = {bucket: 0 for bucket in BUCKETS}
    for bucket, n in db.execute(select(Datapoint.bucket, func.count()).where(Datapoint.dataset_id == dataset.id).group_by(Datapoint.bucket)).all():
        out[bucket] = int(n)
    dataset.counts = out
    return out


def _bump(dataset: Dataset, rows: list[dict[str, Any]]) -> None:
    current = {bucket: int((dataset.counts or {}).get(bucket, 0)) for bucket in BUCKETS}
    for r in rows:
        current[r.get("bucket", "uncategorized")] += 1
    dataset.counts = current


def _touch(db: Session, dataset: Dataset) -> None:
    dataset.updated = func.now()
    db.flush()
    db.refresh(dataset)


def get_dataset(db: Session, project: Project, name: str) -> Dataset:
    dataset = db.scalar(select(Dataset).where(Dataset.project_id == project.id, Dataset.name == name))
    if dataset is None:
        raise ProjectError(f"dataset {name!r} does not exist")
    return dataset


def counts(db: Session, dataset: Dataset) -> dict[str, int]:
    """Datapoints per bucket from the maintained counters (no row scan)."""
    return {bucket: int((dataset.counts or {}).get(bucket, 0)) for bucket in BUCKETS}


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
    dataset = Dataset(project_id=project.id, name=name, origin=origin, labels=[], counts={})
    db.add(dataset)
    db.flush()
    _copy_rows(db, dataset.id, clean, 0)
    _bump(dataset, clean)
    _touch(db, dataset)
    return meta(db, dataset)


def create_from_jsonl(db: Session, project: Project, name: str, source: Path, origin_path: str | None = None) -> dict[str, Any]:
    rows = list(read_jsonl(source))
    if not rows:
        raise ProjectError(f"{source} holds no datapoints")
    return create_dataset(db, project, name, rows, {"kind": "jsonl", "path": origin_path or str(source)})


def count(db: Session, dataset: Dataset) -> int:
    return sum(counts(db, dataset).values())


def _query(dataset: Dataset, bucket: str | None, after: int | None) -> Any:
    query = select(Datapoint).where(Datapoint.dataset_id == dataset.id).order_by(Datapoint.position)
    if bucket:
        query = query.where(Datapoint.bucket == bucket)
    if after is not None:
        query = query.where(Datapoint.position > after)
    return query


def page(db: Session, dataset: Dataset, limit: int = 25, after: int | None = None, offset: int = 0, bucket: str | None = None) -> tuple[list[dict[str, Any]], int | None]:
    """One page of rows and the cursor for the next one (None at the end). ``after`` wins over ``offset``."""
    query = _query(dataset, bucket, after)
    if offset and after is None:
        query = query.offset(offset)
    points = list(db.scalars(query.limit(limit + 1)))
    more = len(points) > limit
    points = points[:limit]
    return [row_dict(dp) for dp in points], (points[-1].position if more and points else None)


def iter_rows(db: Session, dataset: Dataset, bucket: str | None = None, batch: int = BATCH_ROWS) -> Iterator[dict[str, Any]]:
    """Every row in position order, read in keyset batches so memory stays bounded."""
    after: int | None = None
    while True:
        points = list(db.scalars(_query(dataset, bucket, after).limit(batch)))
        for dp in points:
            yield row_dict(dp)
        if len(points) < batch:
            return
        after = points[-1].position


def rows(db: Session, dataset: Dataset, offset: int = 0, limit: int | None = None, bucket: str | None = None) -> list[dict[str, Any]]:
    if limit is None and not offset:
        return list(iter_rows(db, dataset, bucket))
    query = _query(dataset, bucket, None)
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
    _lock(db, dataset)
    db.refresh(dataset)  # counters as of the lock, not as of an earlier read
    start = int(db.scalar(select(func.coalesce(func.max(Datapoint.position), -1)).where(Datapoint.dataset_id == dataset.id))) + 1
    _copy_rows(db, dataset.id, clean, start)
    _bump(dataset, clean)
    _touch(db, dataset)
    return meta(db, dataset)


def replace_rows(db: Session, dataset: Dataset, new_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Overwrite every datapoint (cleaning, auto-labelling). One transaction, so readers see old or new, never half."""
    clean = [validate_datapoint(r, f"record {i + 1}") for i, r in enumerate(new_rows)]
    _lock(db, dataset)
    db.execute(delete(Datapoint).where(Datapoint.dataset_id == dataset.id))
    _copy_rows(db, dataset.id, clean, 0)
    dataset.counts = {}
    _bump(dataset, clean)
    _touch(db, dataset)
    return meta(db, dataset)


def delete_rows(db: Session, dataset: Dataset, ids: list[int]) -> dict[str, Any]:
    if ids:
        _lock(db, dataset)
        db.execute(delete(Datapoint).where(Datapoint.dataset_id == dataset.id, Datapoint.id.in_(ids)))
        _recount(db, dataset)
        _touch(db, dataset)
    return meta(db, dataset)


def autosplit(db: Session, project: Project, name: str, training_pct: int = 80, seed: int = 0, allow_synthetic_in_validation: bool = False) -> dict[str, Any]:
    dataset = get_dataset(db, project, name)
    _lock(db, dataset)
    # the split needs only id, bucket and source; skipping messages avoids de-TOASTing every row
    current = [{"id": i, "bucket": b, "source": s or ""} for i, b, s in db.execute(select(Datapoint.id, Datapoint.bucket, Datapoint.source).where(Datapoint.dataset_id == dataset.id).order_by(Datapoint.position)).all()]
    moves = assign_split(current, training_pct, seed, allow_synthetic_in_validation)
    if moves:
        # one set-based UPDATE joined to unnested arrays, instead of one statement per row
        ids = [current[index]["id"] for index in moves]
        buckets = list(moves.values())
        db.execute(
            text("UPDATE datapoints AS d SET bucket = m.bucket FROM unnest(CAST(:ids AS bigint[]), CAST(:buckets AS text[])) AS m(id, bucket) WHERE d.dataset_id = :dataset AND d.id = m.id"),
            {"ids": ids, "buckets": buckets, "dataset": dataset.id},
        )
        _recount(db, dataset)
        _touch(db, dataset)
    return meta(db, dataset)


def set_bucket(db: Session, dataset: Dataset, ids: list[int], bucket: str) -> dict[str, Any]:
    if bucket not in BUCKETS:
        raise DatapointError(f"bucket must be one of {BUCKETS}")
    if ids:
        _lock(db, dataset)
        db.execute(update(Datapoint).where(Datapoint.dataset_id == dataset.id, Datapoint.id.in_(ids)).values(bucket=bucket))
        _recount(db, dataset)
        _touch(db, dataset)
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
    _lock(db, dataset)
    current = list(iter_rows(db, dataset))
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

    return quality_profile(list(iter_rows(db, dataset)))
