"""Task logs remain complete across hosts; failures never publish partial heads."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_storage import MemoryS3
from tinystudio.services import task_logs
from tinystudio.services.task_logs import CHUNK_BYTES, TaskLogStorage
from tinystudio.storage import ArtifactStorage, StorageError


@pytest.fixture
def backend():
    return ArtifactStorage(client=MemoryS3(), bucket="test-only")


def test_full_and_tail_logs_cross_host_ignore_local_and_db_tail(backend, tmp_path, monkeypatch):
    store = TaskLogStorage(backend)
    writer = store.writer("task-1")
    expected = [f"line {i}: café ☀" for i in range(6000)]
    for line in expected:
        writer.write(line + "\n")
    writer.close()
    stale = tmp_path / "stale.log"
    stale.write_text("local-only stale log")
    monkeypatch.setattr(task_logs, "config", lambda: SimpleNamespace(task_log=lambda _: stale))
    # A newly constructed store has no access to writer memory or transfer files.
    second_host = TaskLogStorage(ArtifactStorage(client=backend.client, bucket="test-only"))
    assert second_host.read("task-1", fallback=["DB tail only"]) == expected
    downloads = []
    download = backend.client.download_file
    def record(bucket, key, path):
        downloads.append(key)
        download(bucket, key, path)
    monkeypatch.setattr(backend.client, "download_file", record)
    assert second_host.read("task-1", 3) == expected[-3:]
    assert len(downloads) == 2  # one head and the last bounded chunk
    assert stale.read_text() == "local-only stale log"
    assert max(len(json.loads(raw)["data"]) for key, raw in backend.client.objects.items() if "/objects/" in key) <= (CHUNK_BYTES + 2) // 3 * 4


def test_flush_failure_keeps_previous_head_and_retry_does_not_duplicate(backend, monkeypatch):
    store = TaskLogStorage(backend)
    writer = store.writer("retry")
    writer.write("first\n")
    writer.flush()
    original_head = backend.client.objects["tasks/retry/manifest.json"]
    writer.write("second\n")
    upload = backend.client.upload_file
    def fail_head(path, bucket, key, **options):
        if key.endswith("manifest.json"):
            raise OSError("controlled head publication failure")
        upload(path, bucket, key, **options)
    monkeypatch.setattr(backend.client, "upload_file", fail_head)
    with pytest.raises(StorageError, match="upload failed"):
        writer.flush()
    assert backend.client.objects["tasks/retry/manifest.json"] == original_head
    assert store.read("retry") == ["first"]
    monkeypatch.setattr(backend.client, "upload_file", upload)
    writer.flush()
    writer.close()
    assert store.read("retry") == ["first", "second"]
    resumed = TaskLogStorage(backend).writer("retry")
    resumed.write("third\n")
    resumed.close()
    assert store.read("retry") == ["first", "second", "third"]


def test_ambiguous_committed_head_retry_is_idempotent(backend, monkeypatch):
    writer = TaskLogStorage(backend).writer("ambiguous")
    writer.write("once\n")
    upload = backend.client.upload_file
    def lost_response(path, bucket, key, **options):
        upload(path, bucket, key, **options)
        if key.endswith("manifest.json"):
            raise OSError("lost success response")
    monkeypatch.setattr(backend.client, "upload_file", lost_response)
    with pytest.raises(StorageError):
        writer.flush()
    monkeypatch.setattr(backend.client, "upload_file", upload)
    writer.flush()
    assert TaskLogStorage(backend).read("ambiguous") == ["once"]
    assert len([key for key in backend.client.objects if "/objects/" in key]) == 1


def test_missing_remote_never_falls_back_and_corruption_is_explicit(backend, tmp_path, monkeypatch):
    local = tmp_path / "legacy.log"
    local.write_text("legacy local secret\n")
    monkeypatch.setattr(task_logs, "config", lambda: SimpleNamespace(task_log=lambda _: local))
    store = TaskLogStorage(backend)
    assert store.read("missing", fallback=["database-tail"]) == []
    with pytest.raises(StorageError, match="publication failures"):
        store.read("missing", missing_ok=False)
    writer = store.writer("corrupt")
    writer.write("durable\n")
    writer.close()
    key = next(key for key in backend.client.objects if "/objects/" in key)
    original = backend.client.objects.pop(key)
    with pytest.raises(StorageError, match="not found"):
        store.read("corrupt")
    backend.client.objects[key] = original + b"tampered"
    with pytest.raises(StorageError, match="checksum"):
        store.read("corrupt")


def test_publish_cost_is_linear_even_with_many_flushes(backend):
    writer = TaskLogStorage(backend).writer("linear")
    for _ in range(80):
        writer.write("x" * 100 + "\n")
        writer.flush()
    assert len(backend.client.uploads) == 160
    manifests = [raw for key, raw in backend.client.objects.items() if key.endswith("manifest.json")]
    assert len(manifests[0]) < 160
    chunks = [len(raw) for key, raw in backend.client.objects.items() if "/objects/" in key]
    assert max(chunks) < 300  # no growing list of historical chunks



def test_manifest_rejects_external_keys_and_invalid_shape(backend):
    backend.client.objects["tasks/invalid/manifest.json"] = b'{"version":1,"head":"../../other","bytes":1,"chunks":1}'
    with pytest.raises(StorageError, match="manifest"):
        TaskLogStorage(backend).read("invalid")
    for task_id in ("../other", "a/b", "bad\\id", ""):
        with pytest.raises(StorageError, match="ID"):
            TaskLogStorage(backend).writer(task_id)


def test_route_full_log_and_missing_remote_are_actionable(backend, monkeypatch):
    from tinystudio.api.routes import system
    task = SimpleNamespace(id="route", state="succeeded", log_tail=["tail only"])
    monkeypatch.setattr(system.tasks_svc, "get_task", lambda db, task_id: task)
    monkeypatch.setattr(system, "TaskLogStorage", lambda: TaskLogStorage(backend))
    with pytest.raises(HTTPException, match="publication failures"):
        system.task_log("route", tail=0, db=None, _=None)
    writer = TaskLogStorage(backend).writer("route")
    writer.write("first\nsecond\nthird\n")
    writer.close()
    assert system.task_log("route", tail=0, db=None, _=None)["lines"] == ["first", "second", "third"]
    assert system.task_log("route", tail=1, db=None, _=None)["lines"] == ["third"]
    with pytest.raises(HTTPException, match="nonnegative"):
        system.task_log("route", tail=-1, db=None, _=None)


def test_local_writer_and_legacy_tail_compatibility(tmp_path, monkeypatch):
    path = tmp_path / "tasks" / "local.log"
    monkeypatch.setattr(task_logs, "config", lambda: SimpleNamespace(task_log=lambda _: path))
    store = TaskLogStorage(ArtifactStorage(root=tmp_path / "artifacts"))
    assert store.read("local", fallback=["legacy tail"]) == ["legacy tail"]
    writer = store.writer("local")
    writer.write("first\n")
    writer.close()
    assert path.read_text() == "first\n"
    assert store.read("local") == ["first"]


@pytest.fixture
def task_db(monkeypatch):
    """Own a disposable schema in the test DB; never rerun shared migrations."""
    from contextlib import contextmanager
    from uuid import uuid4
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from tests.conftest import _create_test_database, _test_database_url
    from tinystudio import worker
    from tinystudio.db.models import Base
    problem = _create_test_database()
    if problem:
        pytest.skip(f"test PostgreSQL unavailable: {problem}")
    schema = "task_logs_" + uuid4().hex
    engine = create_engine(_test_database_url())
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(scoped)
        factory = sessionmaker(scoped, expire_on_commit=False)
        @contextmanager
        def session():
            with factory() as db:
                try:
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
        monkeypatch.setattr(worker, "session", session)
        monkeypatch.setattr(worker, "dispose_after_fork", lambda: None)
        with factory() as db:
            yield db, session
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.parametrize("failure", ["none", "construct", "handler", "finalize"])
def test_worker_finalization_and_duplicate_delivery(task_db, backend, monkeypatch, failure):
    from tinystudio import worker
    from tinystudio.services import settings, tasks
    from tinystudio.worker.handlers import HANDLERS
    db, session = task_db
    task = tasks.create_task(db, "log.test", {})
    db.commit()
    monkeypatch.setattr(settings, "apply", lambda db: None)
    monkeypatch.setattr(worker, "publish", lambda *args: None)
    monkeypatch.setattr(worker, "TaskLogStorage", lambda: TaskLogStorage(backend))
    handler_calls = []
    def handler(context):
        handler_calls.append(True)
        # Another delivery while this task is running cannot open a second writer.
        assert worker.run_task(task.id) is None
        context.progress("handler complete")
        with session() as check:
            assert tasks.get_task(check, task.id).state == "running"
        if failure == "handler":
            raise ValueError("controlled handler failure")
        if failure == "finalize":
            backend.client.fail_upload = True
        return {"done": True}
    monkeypatch.setitem(HANDLERS, "log.test", handler)
    if failure == "construct":
        backend.client.fail_download = True
        # Existing head forces a remote download during writer construction.
        writer = TaskLogStorage(backend).writer(task.id)
        writer.write("previous attempt\n")
        writer.close()
    worker.run_task(task.id)
    db.expire_all()
    row = tasks.get_task(db, task.id)
    if failure == "none":
        assert row.state == "succeeded"
        assert "handler complete" in TaskLogStorage(backend).read(task.id)
        assert TaskLogStorage(backend).read(task.id)[-1].endswith("succeeded")
        assert handler_calls == [True]
        assert worker.run_task(task.id) is None
        assert handler_calls == [True]
    else:
        assert row.state == "failed" and row.finished
        if failure == "handler":
            assert "controlled handler failure" in row.error
            assert any("controlled handler failure" in line for line in TaskLogStorage(backend).read(task.id))
        else:
            assert "artifact" in row.error
        assert handler_calls == ([] if failure == "construct" else [True])


def test_overlapping_deliveries_are_serialized_before_opening_log(task_db, backend, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from tinystudio import worker
    from tinystudio.services import settings, tasks
    from tinystudio.worker.handlers import HANDLERS
    db, session = task_db
    task = tasks.create_task(db, "log.race", {})
    db.commit()
    task_id = task.id
    entered_claim, release_claim, entered_second = Event(), Event(), Event()
    original = tasks.mark_running
    def pause_claim(db, task_id, worker_name):
        entered_claim.set()
        assert release_claim.wait(5), "test did not release claim"
        return original(db, task_id, worker_name)
    monkeypatch.setattr(tasks, "mark_running", pause_claim)
    monkeypatch.setattr(settings, "apply", lambda db: None)
    monkeypatch.setattr(worker, "publish", lambda *args: None)
    monkeypatch.setattr(worker, "TaskLogStorage", lambda: TaskLogStorage(backend))
    calls = []
    def handler(context):
        calls.append(True)
        context.progress("one execution")
        return {"done": True}
    monkeypatch.setitem(HANDLERS, "log.race", handler)
    def second():
        entered_second.set()
        return worker.run_task(task_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker.run_task, task_id)
        assert entered_claim.wait(5)
        other = pool.submit(second)
        assert entered_second.wait(5)
        # Both deliveries overlap while the first owns a PostgreSQL row lock.
        release_claim.set()
        assert first.result(timeout=10) == {"done": True}
        assert other.result(timeout=10) is None
    assert calls == [True]
    assert TaskLogStorage(backend).read(task_id).count("one execution") == 1
    with session() as check:
        assert tasks.get_task(check, task_id).state == "succeeded"


def test_chunk_upload_failure_cannot_advance_head_and_resume_checks_history(backend):
    store = TaskLogStorage(backend)
    writer = store.writer("history")
    writer.write("old\n")
    writer.flush()
    first_manifest = backend.client.objects["tasks/history/manifest.json"]
    writer.write("new\n")
    backend.client.fail_upload = True
    with pytest.raises(StorageError, match="upload failed"):
        writer.flush()
    assert backend.client.objects["tasks/history/manifest.json"] == first_manifest
    backend.client.fail_upload = False
    writer.flush()
    writer.close()
    assert store.read("history") == ["old", "new"]
    oldest_key = "tasks/history/objects/" + json.loads(first_manifest)["head"]
    del backend.client.objects[oldest_key]
    # Tail may still read its selected suffix, but a new attempt must not claim
    # a complete durable log while retaining missing historical chunks.
    with pytest.raises(StorageError, match="not found"):
        store.writer("history")
