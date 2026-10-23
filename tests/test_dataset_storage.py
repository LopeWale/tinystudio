"""Datapoint storage: hash partitions, COPY ingest, maintained counters, keyset paging, streamed reads, Parquet snapshots."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from tests.conftest import row
from tinystudio.core.quality import CleanSpec
from tinystudio.db.models import Datapoint, Snapshot
from tinystudio.services import datasets as datasets_svc
from tinystudio.services import snapshots as snapshots_svc


def many(n: int, offset: int = 0) -> list[dict]:
    return [row(f"q{i}", json.dumps({"i": i}), labels=["x"] if i % 2 else [], rating=float(i % 10), rejected=("bad" if i % 3 == 0 else None) or "worse") for i in range(offset, offset + n)]


def test_datapoints_table_is_hash_partitioned(db):
    kind = db.execute(text("select relkind from pg_class where relname = 'datapoints'")).scalar()
    strategy = db.execute(text("select partstrat from pg_partitioned_table where partrelid = 'datapoints'::regclass")).scalar()
    children = db.execute(text("select count(*) from pg_inherits where inhparent = 'datapoints'::regclass")).scalar()
    pk = db.execute(text("select pg_get_constraintdef(oid) from pg_constraint where conrelid = 'datapoints'::regclass and contype = 'p'")).scalar()
    unique = db.execute(text("select indexdef from pg_indexes where tablename = 'datapoints' and indexname = 'ix_datapoints_dataset_position'")).scalar()
    assert (kind, strategy, children) == ("p", "h", 16)
    assert pk == "PRIMARY KEY (dataset_id, id)" and "UNIQUE" in unique


def test_copy_ingest_round_trips_every_field_and_keeps_counters(db, project):
    rows = many(1200)
    meta = datasets_svc.create_dataset(db, project, "big", rows)
    assert meta["datapoints"] == 1200 and meta["buckets"] == {"uncategorized": 1200, "training": 0, "validation": 0}
    ds = datasets_svc.get_dataset(db, project, "big")
    stored = datasets_svc.rows(db, ds)
    assert [r["messages"] for r in stored] == [r["messages"] for r in rows]
    assert stored[1]["labels"] == ["x"] and stored[3]["rating"] == 3.0 and stored[0]["rejected"] == "bad"
    # the counters agree with the rows after every kind of write
    datasets_svc.add_rows(db, project, "big", many(50, 1200), bucket="validation")
    datasets_svc.autosplit(db, project, "big", 80, seed=1)
    first_ids = [r["id"] for r in datasets_svc.rows(db, ds, limit=7)]
    datasets_svc.set_bucket(db, ds, first_ids[:3], "validation")
    datasets_svc.delete_rows(db, ds, first_ids[3:])
    datasets_svc.apply_cleaning(db, project, "big", CleanSpec(dedupe_user=True))
    actual = dict(db.execute(text("select bucket, count(*) from datapoints where dataset_id = :d group by bucket"), {"d": ds.id}).all())
    assert {k: v for k, v in datasets_svc.counts(db, ds).items() if v} == actual
    assert datasets_svc.count(db, ds) == sum(actual.values()) == 1246


def test_positions_stay_unique_and_ordered_across_appends_and_rewrites(db, project):
    datasets_svc.create_dataset(db, project, "d", many(10))
    ds = datasets_svc.get_dataset(db, project, "d")
    datasets_svc.add_rows(db, project, "d", many(5, 10))
    positions = [p for (p,) in db.execute(text("select position from datapoints where dataset_id = :d order by position"), {"d": ds.id}).all()]
    assert positions == list(range(15))
    datasets_svc.delete_rows(db, ds, [datasets_svc.rows(db, ds, limit=1)[0]["id"]])
    datasets_svc.add_rows(db, project, "d", many(1, 99))
    positions = [p for (p,) in db.execute(text("select position from datapoints where dataset_id = :d order by position"), {"d": ds.id}).all()]
    assert positions == list(range(1, 16)) and len(set(positions)) == 15
    datasets_svc.replace_rows(db, ds, datasets_svc.rows(db, ds)[::-1])
    positions = [p for (p,) in db.execute(text("select position from datapoints where dataset_id = :d order by position"), {"d": ds.id}).all()]
    assert positions == list(range(15))


def test_keyset_pages_cover_every_row_once_and_match_offset_pages(db, project):
    datasets_svc.create_dataset(db, project, "d", many(103))
    ds = datasets_svc.get_dataset(db, project, "d")
    seen, after = [], None
    while True:
        rows, after = datasets_svc.page(db, ds, limit=25, after=after)
        seen += [r["id"] for r in rows]
        if after is None:
            break
    assert len(seen) == 103 == len(set(seen))
    assert [r["id"] for r in datasets_svc.page(db, ds, limit=25, offset=50)[0]] == seen[50:75]
    assert [r["id"] for r in datasets_svc.iter_rows(db, ds, batch=10)] == seen
    datasets_svc.autosplit(db, project, "d", 70, seed=2)
    training = [r for r in datasets_svc.iter_rows(db, ds, bucket="training", batch=7)]
    assert len(training) == datasets_svc.counts(db, ds)["training"] and all(r["bucket"] == "training" for r in training)


def test_queries_prune_to_one_partition(db, project):
    datasets_svc.create_dataset(db, project, "d", many(20))
    ds = datasets_svc.get_dataset(db, project, "d")
    plan = "\n".join(r[0] for r in db.execute(text("explain select * from datapoints where dataset_id = :d and position > 3 order by position limit 5"), {"d": ds.id}).all())
    import re

    assert len(set(re.findall(r" on (datapoints_p\d+)", plan))) == 1, plan


def test_snapshots_are_parquet_in_the_artifact_store_without_sql_rows(db, project, infra):
    datasets_svc.create_dataset(db, project, "d", many(30))
    datasets_svc.autosplit(db, project, "d", 80, seed=3)
    snap = snapshots_svc.create_snapshot(db, project, "d", "s1")
    row_ = db.get(Snapshot, snap["id"])
    assert row_.storage_prefix == f"snapshots/{project.id}/{row_.id}"
    assert db.execute(text("select count(*) from snapshot_rows where snapshot_id = :s"), {"s": row_.id}).scalar() == 0
    published = infra["data_dir"] / "artifacts" / row_.storage_prefix / "manifest.json"
    assert published.exists() and {f["path"].split("-")[0] for f in json.loads(published.read_text())["files"]} == {"train", "val"}
    train, val = snapshots_svc.load_snapshot(db, project, "s1")
    assert (len(train), len(val)) == (snap["training_count"], snap["validation_count"]) and "id" not in train[0]
    # the frozen copy does not follow later edits
    datasets_svc.add_rows(db, project, "d", many(5, 100), bucket="training")
    assert len(snapshots_svc.load_snapshot(db, project, "s1")[0]) == snap["training_count"]
    snapshots_svc.delete_snapshot(db, project, "s1")
    assert not published.exists()


def test_snapshot_shards_split_at_the_row_limit(db, project, monkeypatch, infra):
    monkeypatch.setattr(snapshots_svc, "SHARD_ROWS", 7)
    datasets_svc.create_dataset(db, project, "d", many(40))
    datasets_svc.autosplit(db, project, "d", 75, seed=4)
    snap = snapshots_svc.create_snapshot(db, project, "d", "s1")
    manifest = json.loads((infra["data_dir"] / "artifacts" / db.get(Snapshot, snap["id"]).storage_prefix / "manifest.json").read_text())
    train_shards = [f for f in manifest["files"] if f["path"].startswith("train-")]
    assert len(train_shards) == -(-snap["training_count"] // 7)
    assert len(snapshots_svc.load_snapshot(db, project, "s1")[0]) == snap["training_count"]


def test_composite_key_bulk_update_touches_only_its_dataset(db, project):
    datasets_svc.create_dataset(db, project, "a", many(12))
    datasets_svc.create_dataset(db, project, "b", many(12))
    datasets_svc.autosplit(db, project, "a", 50, seed=5)
    b = datasets_svc.get_dataset(db, project, "b")
    assert datasets_svc.counts(db, b)["uncategorized"] == 12
    assert db.execute(text("select count(*) from datapoints where dataset_id = :d and bucket <> 'uncategorized'"), {"d": b.id}).scalar() == 0


def test_large_datasets_are_processed_by_a_worker(db, project, redis_available, monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from rq import Queue, SimpleWorker

    from tinystudio import worker
    from tinystudio.api.app import create_app
    from tinystudio.api.routes import data as data_routes
    from tinystudio.services import tasks as tasks_svc
    from tinystudio.services import users as users_svc

    user = users_svc.create_user(db, "owner@example.com", "password-123", role="admin")
    _, key = users_svc.create_key(db, user, "k")
    datasets_svc.create_dataset(db, project, "big", many(30))
    db.commit()
    real = data_routes.config()
    monkeypatch.setattr(data_routes, "config", lambda: SimpleNamespace(**{**real.__dict__, "sync_row_limit": 10}))
    with TestClient(create_app(run_migrations=False), headers={"Authorization": f"Bearer {key}"}) as client:
        slug = project.slug
        split = client.post(f"/api/p/{slug}/datasets/big/split", json={"train_pct": 80})
        assert split.status_code == 202 and split.json()["queued"] is True and split.json()["task"]["kind"] == "dataset.split"
        SimpleWorker([Queue(worker.QUEUE_NAME, connection=worker.redis_conn())], connection=worker.redis_conn()).work(burst=True)
        snap = client.post(f"/api/p/{slug}/snapshots", json={"dataset": "big", "name": "s1"})
        clean = client.post(f"/api/p/{slug}/datasets/big/clean", json={"dedupe_user": True})
        assert snap.status_code == 202 and clean.status_code == 202
        SimpleWorker([Queue(worker.QUEUE_NAME, connection=worker.redis_conn())], connection=worker.redis_conn()).work(burst=True)
        db.expire_all()
        states = {t["kind"]: t["state"] for t in tasks_svc.list_tasks(db, project)}
        assert states == {"dataset.split": "succeeded", "snapshot.create": "succeeded", "dataset.clean": "succeeded"}, states
        assert client.get(f"/api/p/{slug}/snapshots").json()[0]["training_count"] == 24
        # small datasets still answer inline
        datasets_svc.create_dataset(db, project, "small", many(5))
        db.commit()
        assert client.post(f"/api/p/{slug}/datasets/small/split", json={"train_pct": 80}).json()["buckets"]["uncategorized"] == 0
