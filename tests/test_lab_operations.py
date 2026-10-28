"""Operation durability against an exclusively owned disposable PostgreSQL database.

Run --noconftest; never connects to shared Redis or edits an existing database.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tinystudio.config import config
from tinystudio.db.models import Project
from tinystudio.lab import operations as ops, service as svc
from tinystudio.lab.contracts import checksum
from tinystudio.lab.models import LabArtifact, LabDefinition, LabOperation
from tinystudio.storage import ArtifactStorage


@pytest.fixture(scope="module")
def database():
    admin = create_engine(config().database_url, isolation_level="AUTOCOMMIT")
    name = "tinystudio_ops_test_" + uuid.uuid4().hex[:12]
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception:
        admin.dispose()
        raise RuntimeError("operation tests require PostgreSQL CREATE DATABASE; no shared database modified") from None
    engine = create_engine(admin.url.set(database=name), connect_args={"options": "-c lock_timeout=3000 -c statement_timeout=15000"})
    try:
        from tinystudio.db.migrate import upgrade
        upgrade("head", engine.url.render_as_string(hide_password=False))
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


@pytest.fixture
def setup(database, tmp_path, monkeypatch):
    store = ArtifactStorage(root=tmp_path / "objects")
    monkeypatch.setattr(svc, "storage", lambda: store)
    with database.begin() as db:
        project = Project(slug=uuid.uuid4().hex, name="Operation fixture")
        other = Project(slug=uuid.uuid4().hex, name="Other")
        db.add_all([project, other])
        db.flush()
        project_id, other_id = project.id, other.id
        definition = svc.save_definition(db, project_id, {"name": "Evidence", "engine": "synthetic.signal",
            "parameters": {"samples": 4}, "execution": {"profile": "trusted_native"}})
        run = svc.submit(db, project_id, definition["id"], 1, "local", uuid.uuid4().hex)
        run.state = "succeeded"
        run_id = run.id
    return database, project_id, other_id, run_id, store


def admit(setup):
    factory, project, _, run, _ = setup
    with factory.begin() as db:
        return ops.request_export(db, project, run, uuid.uuid4().hex).id


def test_admission_identity_scope_and_active_rejection(setup):
    factory, project, other, run, _ = setup
    with factory.begin() as db:
        op = ops.request_export(db, project, run, "same")
        assert ops.request_export(db, project, run, "same").id == op.id
        assert op.payload_digest == checksum(op.payload)
        with pytest.raises(svc.LabError, match="operation not found"):
            ops.get_operation(db, other, op.id)
        with pytest.raises(svc.LabError, match="artifact not found"):
            ops.request_import(db, project, "missing", "import")
    with factory.begin() as db:
        svc.get_run(db, project, run).state = "running"
    with factory.begin() as db:
        with pytest.raises(svc.LabError, match="only terminal"):
            ops.request_export(db, project, run, "active")


def test_dispatch_only_committed_and_redelivery_is_harmless(setup):
    factory, project, _, run, _ = setup
    sent = []
    with factory() as db:
        row = ops.request_export(db, project, run, "dispatch")
        id_ = row.id
        assert id_ not in ops.dispatch_pending(db, sent.append)
        db.commit()
        assert id_ in ops.dispatch_pending(db, sent.append)
    result = ops.run_operation(id_, factory)
    assert result["state"] == "succeeded"
    assert ops.run_operation(id_, factory) is None
    with factory() as db:
        op = ops.get_operation(db, project, id_)
        assert op.generation == 1
        artifact = svc.get_artifact(db, project, op.result["artifact"]["id"])
        assert artifact.media_type == "application/zip"


def test_export_changed_after_admission_fails_without_output(setup):
    factory, project, _, run, _ = setup
    id_ = admit(setup)
    with factory.begin() as db:
        svc.get_run(db, project, run).error = "later recovery changed meaning"
    assert ops.run_operation(id_, factory) is None
    with factory() as db:
        row = ops.get_operation(db, project, id_)
        assert row.state == "failed" and "changed" in row.error
        assert row.result is None


def test_database_rejects_mutated_identity(setup):
    factory, _, _, _, _ = setup
    id_ = admit(setup)
    with pytest.raises(DBAPIError, match="identity is immutable"):
        with factory.begin() as db:
            db.execute(update(LabOperation).where(LabOperation.id == id_).values(payload={"run_id": "other"}))


def test_import_duplicate_delivery_creates_one_definition(setup):
    factory, project, _, _, _ = setup
    exported = ops.run_operation(admit(setup), factory)
    with factory.begin() as db:
        row = ops.request_import(db, project, exported["result"]["artifact"]["id"], "import")
        id_ = row.id
        before = db.scalar(select(func.count()).select_from(LabDefinition).where(LabDefinition.project_id == project))
    result = ops.run_operation(id_, factory)
    assert result["state"] == "succeeded"
    assert result["result"]["execution_started"] is False
    assert ops.run_operation(id_, factory) is None
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(LabDefinition).where(LabDefinition.project_id == project)) == before+1
        row = ops.get_operation(db, project, id_)
        assert row.result["definition"]["definition"]["execution"]["grants"] == {}


def test_import_failure_rolls_back_partial_database_rows(setup, monkeypatch):
    factory, project, _, _, _ = setup
    exported = ops.run_operation(admit(setup), factory)
    with factory.begin() as db:
        row = ops.request_import(db, project, exported["result"]["artifact"]["id"], "fault")
        id_ = row.id
        before = db.scalar(select(func.count()).select_from(LabDefinition).where(LabDefinition.project_id == project))
    original = ops.portable.import_bundle
    def fail_after_import(*args):
        original(*args)
        raise RuntimeError("controlled post-import failure")
    monkeypatch.setattr(ops.portable, "import_bundle", fail_after_import)
    assert ops.run_operation(id_, factory) is None
    with factory() as db:
        row = ops.get_operation(db, project, id_)
        assert row.state == "failed" and "controlled post-import failure" in row.error
        assert row.result is None
        assert db.scalar(select(func.count()).select_from(LabDefinition).where(LabDefinition.project_id == project)) == before
    monkeypatch.setattr(ops.portable, "import_bundle", original)
    with factory.begin() as db:
        ops.retry_operation(db, project, id_)
    assert ops.run_operation(id_, factory)["state"] == "succeeded"


def test_cancel_during_import_fences_all_rows(setup, monkeypatch):
    factory, project, _, _, _ = setup
    exported = ops.run_operation(admit(setup), factory)
    with factory.begin() as db:
        id_ = ops.request_import(db, project, exported["result"]["artifact"]["id"], "cancel").id
        before = db.scalar(select(func.count()).select_from(LabDefinition).where(LabDefinition.project_id == project))
    original = ops.portable.import_bundle
    def cancel_after_import(*args):
        result = original(*args)
        with factory.begin() as independent:
            ops.cancel_operation(independent, project, id_)
        return result
    monkeypatch.setattr(ops.portable, "import_bundle", cancel_after_import)
    assert ops.run_operation(id_, factory) is None
    with factory() as db:
        assert ops.get_operation(db, project, id_).state == "cancelled"
        assert db.scalar(select(func.count()).select_from(LabDefinition).where(LabDefinition.project_id == project)) == before


def test_expired_lease_requires_explicit_retry_and_rejects_old_fence(setup):
    factory, project, _, _, _ = setup
    id_ = admit(setup)
    with factory.begin() as db:
        row = ops.get_operation(db, project, id_, lock=True)
        row.state, row.claim_token, row.generation = "running", "old", 1
        row.lease_until = svc.now()-timedelta(seconds=1)
    with factory.begin() as db:
        assert ops.recover_expired(db) >= 1
        assert ops.get_operation(db, project, id_).state == "lost"
    assert ops.run_operation(id_, factory) is None
    with factory.begin() as db:
        with pytest.raises(svc.LabError, match="authority"):
            ops._Lease(factory, id_, "old", 1).finish(db, {"fake": True})
        ops.retry_operation(db, project, id_)
    assert ops.run_operation(id_, factory)["state"] == "succeeded"


def test_output_readback_corruption_is_failure(setup, monkeypatch):
    factory, project, _, _, store = setup
    id_ = admit(setup)
    upload = store.upload_file
    def corrupt(path, key, content_type=None):
        result = upload(path, key, content_type)
        store._path(key).write_bytes(b"corrupt")
        return result
    monkeypatch.setattr(store, "upload_file", corrupt)
    assert ops.run_operation(id_, factory) is None
    with factory() as db:
        row = ops.get_operation(db, project, id_)
        assert row.state == "failed" and row.result is None
        assert "checksum mismatch" in row.error
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == project)) == 0


def test_concurrent_delivery_has_one_claim_and_one_result(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    factory, project, _, _, _ = setup
    id_ = admit(setup)
    entered, release = threading.Event(), threading.Event()
    original = ops.portable.export_bundle
    def paused_export(*args):
        entered.set()
        assert release.wait(5), "test release timed out"
        return original(*args)
    monkeypatch.setattr(ops.portable, "export_bundle", paused_export)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ops.run_operation, id_, factory)
        assert entered.wait(5), "first worker did not acquire authority"
        duplicate = pool.submit(ops.run_operation, id_, factory)
        try:
            assert duplicate.result(timeout=5) is None
        finally:
            release.set()
        assert first.result(timeout=5)["state"] == "succeeded"
    with factory() as db:
        assert ops.get_operation(db, project, id_).generation == 1
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == project)) == 1


def test_worker_cannot_publish_after_lease_expiration(setup, monkeypatch):
    factory, project, _, _, _ = setup
    id_ = admit(setup)
    original = ops.portable.export_bundle
    def expire_after_export(*args):
        result = original(*args)
        with factory.begin() as independent:
            independent.execute(update(LabOperation).where(LabOperation.id == id_).values(lease_until=svc.now()-timedelta(seconds=1)))
            ops.recover_expired(independent)
        return result
    monkeypatch.setattr(ops.portable, "export_bundle", expire_after_export)
    assert ops.run_operation(id_, factory) is None
    with factory() as db:
        row = ops.get_operation(db, project, id_)
        assert row.state == "lost" and row.result is None
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == project)) == 0


def test_dispatch_stop_after_delivery_prevents_more_deliveries_and_timestamp_write(setup):
    factory, _, _, _, _ = setup
    admit(setup)
    admit(setup)
    delivered = []
    stopped = False
    def enqueue(id_):
        nonlocal stopped
        delivered.append(id_)
        stopped = True
    with factory() as db:
        sent = ops.dispatch_pending(db, enqueue, cancelled=lambda: stopped)
    assert sent == delivered
    assert len(delivered) == 1
    with factory() as db:
        assert db.get(LabOperation, delivered[0]).dispatch_after is None
        # Delivery is retained as a fact; it is not represented as cancellation.
        assert db.get(LabOperation, delivered[0]).state == "queued"


def test_dispatch_stopped_or_exhausted_before_start_delivers_nothing(setup):
    factory, _, _, _, _ = setup
    admit(setup)
    with factory() as db:
        callback = lambda id_: pytest.fail("stopped dispatch delivered a job")
        assert ops.dispatch_pending(db, callback, cancelled=lambda: True) == []
        assert ops.dispatch_pending(db, callback, timeout_seconds=0) == []


def test_dispatch_fresh_reader_and_writer_install_own_deadline_limits(setup):
    from sqlalchemy import event
    factory, _, _, _, _ = setup
    admit(setup)
    observed = []
    engine = factory.kw["bind"]
    def inspect_limits(connection, cursor, statement, parameters, context, executemany):
        normalized = statement.lower().lstrip()
        if "lab_operations" in normalized and (normalized.startswith("select") or normalized.startswith("update")):
            settings = dict(connection.exec_driver_sql("select name, setting::int from pg_settings "
                "where name in ('statement_timeout', 'lock_timeout')").all())
            observed.append((normalized.split()[0], settings))
    event.listen(engine, "before_cursor_execute", inspect_limits)
    try:
        with factory() as db:
            # Caller-local settings are intentionally much larger: the fresh
            # operation sessions must impose their own smaller deadline budget.
            db.execute(text("select set_config('statement_timeout','12000',true), set_config('lock_timeout','9000',true)"))
            ops.dispatch_pending(db, lambda id_: None, timeout_seconds=.5)
    finally:
        event.remove(engine, "before_cursor_execute", inspect_limits)
    assert {kind for kind, _ in observed} == {"select", "update"}
    for _, settings in observed:
        assert 0 < settings["statement_timeout"] <= 500
        assert 0 < settings["lock_timeout"] <= 500


def test_dispatch_held_operation_row_lock_obeys_short_deadline(setup):
    import time
    factory, _, _, _, _ = setup
    id_ = admit(setup)
    delivered = []
    # The lock is held by another connection throughout the dispatch call.
    # Without operation-local limits this fixture waits its configured 15s;
    # the required 200ms deadline must fail promptly instead.
    with factory.begin() as holder:
        holder.scalar(select(LabOperation).where(LabOperation.id == id_).with_for_update())
        started = time.monotonic()
        with factory() as db:
            with pytest.raises(DBAPIError, match="statement timeout|lock timeout"):
                ops.dispatch_pending(db, delivered.append, timeout_seconds=.2)
        elapsed = time.monotonic()-started
        assert elapsed < 1, f"operation dispatch exceeded its short database wait budget: {elapsed:.3f}s"
    assert id_ in delivered
    with factory() as db:
        row = db.get(LabOperation, id_)
        assert row.state == "queued" and row.dispatch_after is None
