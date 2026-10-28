"""B: registered application, real local RQ, real runner child and portable evidence.

Only uniquely owned PostgreSQL/Redis stores are used. No live service is restarted.
Run with --noconftest.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from rq import Queue, SimpleWorker

from tests.test_lab_foundation import database
from tinystudio.lab import service as svc
from tinystudio.lab import operations, rq_adapter


@pytest.fixture
def integrated(database, tmp_path, monkeypatch):
    from tinystudio import db as db_module, worker
    from tinystudio.api import app as app_module
    from tinystudio.api.deps import get_db, forget_secret
    from tinystudio.auth import ensure_instance, issue_service_token
    from tinystudio.config import reload_config
    from tinystudio.db.models import Project, User
    from tinystudio.storage import ArtifactStorage

    socket_root = tempfile.TemporaryDirectory(prefix="ts-b-redis-", dir="/private/tmp")
    socket = Path(socket_root.name) / "redis.sock"
    log = (tmp_path / "redis.log").open("wb")
    process = subprocess.Popen(["/opt/homebrew/bin/redis-server", "--port", "0", "--unixsocket", str(socket),
        "--save", "", "--appendonly", "no", "--dir", str(tmp_path)], stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic()+5
        while not socket.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("owned Redis did not initialize")
            time.sleep(.02)
        connection = Redis.from_url("unix://"+str(socket)+"?db=0")
        assert connection.ping()
        monkeypatch.setenv("TINYSTUDIO_DATABASE_URL", database.kw["bind"].url.render_as_string(hide_password=False))
        monkeypatch.setenv("TINYSTUDIO_REDIS_URL", "unix://"+str(socket)+"?db=0")
        monkeypatch.setenv("TINYSTUDIO_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("TINYSTUDIO_STORAGE_BACKEND", "local")
        for key in ("TINYSTUDIO_CF_ACCESS_REQUIRED", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD"):
            monkeypatch.setenv(key, "0" if key.endswith("REQUIRED") else "")
        reload_config(); db_module.reset(); worker.reset(); forget_secret()
        monkeypatch.setattr(svc, "storage", lambda: ArtifactStorage(root=tmp_path / "objects"))
        monkeypatch.setattr(app_module, "prepare_database", lambda *args: None)
        app = app_module.create_app(run_migrations=False, ui=False, laboratory_dispatch=False)
        def sessions():
            with database() as db:
                try:
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
        app.dependency_overrides[get_db] = sessions
        with database.begin() as db:
            user = User(email=uuid.uuid4().hex+"@b.invalid", password_hash="fixture", role="admin")
            project = Project(slug="b-"+uuid.uuid4().hex[:12], name="B workflow")
            other = Project(slug="b-"+uuid.uuid4().hex[:12], name="Other")
            db.add_all([user, project, other]); db.flush()
            token = issue_service_token(ensure_instance(db).secret_key, user.id)
            prefix, other_prefix = f"/api/p/{project.slug}/lab", f"/api/p/{other.slug}/lab"
        with TestClient(app, headers={"Authorization": "Bearer "+token}) as client:
            yield client, prefix, other_prefix, app, connection, tmp_path, database
    finally:
        db_module.reset(); worker.reset(); forget_secret()
        process.terminate(); process.wait(timeout=5); log.close(); socket_root.cleanup()
        monkeypatch.undo()
        reload_config()


def author(client, prefix, **extra):
    body = {"name": "B deterministic signal", "engine": "synthetic.signal", "seed": 42,
        "parameters": {"samples": 8, "dt": .25, "frequency": 1, "amplitude": 1, "noise": 0, "units": "V"},
        "execution": {"profile": "trusted_native", "backend": "rq", "max_seconds": 60}, **extra}
    response = client.post(prefix+"/definitions", json={"definition": body})
    assert response.status_code == 201, response.text
    return response.json(), body


def test_registered_application_rq_to_evidence_to_durable_export_and_import(integrated, monkeypatch):
    client, prefix, other_prefix, app, connection, root, factory = integrated
    from tinystudio.lab.runner import Runner
    target = client.post(prefix+"/targets", json={"name": "b-rq"}).json()
    credential = root / "credential.json"
    credential.write_text(json.dumps({"server": "http://127.0.0.1:8123", "target_id": target["id"], "token": target["token"]}))
    credential.chmod(0o600)
    monkeypatch.setenv(rq_adapter.CREDENTIAL_ENV, str(credential))
    def runner_for(credential):
        transport = TestClient(app, headers={"Authorization": "Bearer "+credential["token"]})
        return Runner(credential["server"], credential["target_id"], credential["token"], client=transport,
                      profile_config=credential["profile_config"])
    monkeypatch.setattr(rq_adapter, "_runner", runner_for)
    assert rq_adapter.dispatch_once()["selected"] == 0  # advertise before submission
    saved, body = author(client, prefix)
    checked = client.post(prefix+"/preflight", json={"definition": body, "target_id": target["id"]})
    assert checked.status_code == 200 and checked.json()["compatible"]
    submitted = client.post(prefix+"/runs", json={"definition_id": saved["id"], "revision": 1,
        "target_id": target["id"], "idempotency_key": "b-run"})
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["id"]
    assert rq_adapter.dispatch_once()["enqueued"] == 1
    assert rq_adapter.dispatch_once()["existing"] == 1
    delivery = Queue("tinystudio", connection=connection)
    SimpleWorker([delivery], connection=connection).work(burst=True)
    run = client.get(prefix+"/runs/"+run_id).json()
    assert run["state"] == "succeeded", run.get("error")
    assert run["spec"]["definition"]["execution"]["backend"] == "rq"
    assert run["result"]["execution_receipt"]["profile_manifest"]["execution_backend"] == "native"
    records = client.get(prefix+f"/runs/{run_id}/records").json()
    assert [row["value"] for row in records["records"]] == pytest.approx([0, 1, 0, -1, 0, 1, 0, -1], abs=1e-12)
    assert all(row["units"] == "V" for row in records["records"])
    events = client.get(prefix+f"/runs/{run_id}/events").json()
    after = events["events"][0]["seq"]
    resumed = client.get(prefix+f"/runs/{run_id}/events?after={after}").json()
    assert all(event["seq"] > after for event in resumed["events"])
    episodes = client.get(prefix+"/episodes", params={"run_id": run_id}).json()
    assert episodes["episodes"][0]["window_record_count"] == 8
    assert client.get(other_prefix+f"/runs/{run_id}").status_code == 404
    assert client.get(prefix+"/comparisons").json() == []
    assert client.get(prefix+"/deployments").json() == []

    created = client.post(prefix+f"/runs/{run_id}/exports", json={"idempotency_key": "b-export"})
    assert created.status_code == 202
    operation_id = created.json()["id"]
    assert client.post(prefix+f"/runs/{run_id}/exports", json={"idempotency_key": "b-export"}).json()["id"] == operation_id
    assert client.get(prefix+f"/operations/{operation_id}/download").status_code == 409
    assert client.get(other_prefix+f"/operations/{operation_id}").status_code == 404
    from tinystudio.lab.dispatch import tick
    assert tick()["operations_dispatched"] == 1
    SimpleWorker([delivery], connection=connection).work(burst=True)
    assert client.get(prefix+f"/operations/{operation_id}").json()["state"] == "succeeded"
    bundle = client.get(prefix+f"/operations/{operation_id}/download")
    assert bundle.status_code == 200 and bundle.content.startswith(b"PK")
    artifact = client.post(other_prefix+"/inputs", files={"file": ("research.zip", bundle.content, "application/zip")}).json()
    imported = client.post(other_prefix+"/bundle-imports", json={"artifact_id": artifact["id"], "idempotency_key": "b-import"})
    assert imported.status_code == 202
    assert tick()["operations_dispatched"] == 1
    SimpleWorker([delivery], connection=connection).work(burst=True)
    restored = client.get(other_prefix+"/operations/"+imported.json()["id"]).json()
    assert restored["state"] == "succeeded", restored.get("error")
    assert restored["result"]["execution_started"] is False
    assert restored["result"]["historical_manifest"]["sha256"]
    assert client.post(prefix+"/inputs/from-snapshot", json={"snapshot": "anything"}).status_code == 404


def test_schedule_is_frozen_and_never_claimed_early_or_bypassed_by_retry(integrated):
    from datetime import timedelta
    from tinystudio.lab.runner import Runner
    client, prefix, _, app, _, _, factory = integrated
    target = client.post(prefix+"/targets", json={"name": "b-schedule"}).json()
    machine = TestClient(app, headers={"Authorization": "Bearer "+target["token"]})
    runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
    runner.advertise()
    saved, _ = author(client, prefix, execution={"profile": "trusted_native", "backend": "native", "max_seconds": 60})
    when = (svc.now()+timedelta(hours=1)).isoformat()
    payload = {"definition_id": saved["id"], "revision": 1, "target_id": target["id"], "idempotency_key": "scheduled", "not_before": when}
    submitted = client.post(prefix+"/runs", json=payload)
    assert submitted.status_code == 202
    run = submitted.json()
    assert run["spec"]["schedule"]["not_before"] == when
    assert runner.run_once(run_id=run["id"]) is False
    assert client.post(prefix+f"/runs/{run['id']}/cancel").json()["state"] == "cancelled"
    assert client.post(prefix+f"/runs/{run['id']}/retry").status_code == 202
    assert runner.run_once(run_id=run["id"]) is False
    assert client.post(prefix+"/runs", json={**payload, "not_before": "2026-10-27T12:00:00"}).status_code == 422
    assert client.post(prefix+"/runs", json={**payload, "not_before": svc.now().isoformat()}).status_code == 409
    machine.close()


def test_idle_rq_target_recovers_expired_attempt_without_another_queued_run(integrated, monkeypatch):
    from datetime import timedelta
    from tinystudio.lab.models import LabAttempt
    from tinystudio.lab.runner import Runner, capabilities
    client, prefix, _, app, _, root, factory = integrated
    target = client.post(prefix+"/targets", json={"name": "b-idle-recovery"}).json()
    credential = root / "credential.json"
    credential.write_text(json.dumps({"server": "http://127.0.0.1:8123", "target_id": target["id"], "token": target["token"]}))
    credential.chmod(0o600)
    monkeypatch.setenv(rq_adapter.CREDENTIAL_ENV, str(credential))
    def runner_for(credential):
        return Runner(credential["server"], credential["target_id"], credential["token"],
            client=TestClient(app, headers={"Authorization": "Bearer "+credential["token"]}),
            profile_config=credential["profile_config"])
    monkeypatch.setattr(rq_adapter, "_runner", runner_for)
    rq_adapter.dispatch_once()
    saved, _ = author(client, prefix)
    result = client.post(prefix+"/runs", json={"definition_id": saved["id"], "revision": 1,
        "target_id": target["id"], "idempotency_key": "idle-lost"}).json()
    runner = runner_for(rq_adapter._require_rq(rq_adapter.load_credentials()))
    claimed = runner.post("/claim", {"capabilities": capabilities(runner._profile_config),
                                    "run_id": result["id"]})["attempt"]
    runner.client.close()
    with factory.begin() as db:
        attempt = db.get(LabAttempt, claimed["attempt_id"])
        attempt.lease_until = svc.now()-timedelta(seconds=1)
    assert rq_adapter.dispatch_once()["selected"] == 0
    run = client.get(prefix+"/runs/"+result["id"]).json()
    assert run["state"] == "lost"
    assert client.post(prefix+f"/runs/{run['id']}/retry").status_code == 202


def test_dispatch_process_shutdown_while_its_metadata_update_is_locked(integrated, monkeypatch):
    import asyncio
    from sqlalchemy import select
    from tinystudio.lab.dispatch import Dispatcher
    from tinystudio.lab.models import LabOperation
    client, prefix, _, _, connection, _, factory = integrated
    artifact = client.post(prefix+"/inputs", files={"file": ("pending.zip", b"fixture", "application/zip")}).json()
    operation = client.post(prefix+"/bundle-imports", json={"artifact_id": artifact["id"], "idempotency_key": "blocked-dispatch"}).json()
    locker = factory()
    locker.execute(select(LabOperation).where(LabOperation.id == operation["id"]).with_for_update()).scalar_one()
    environment = dict(os.environ)
    environment.pop(rq_adapter.CREDENTIAL_ENV, None)
    dispatcher = Dispatcher(environment=environment, grace=.1).start()
    try:
        deadline = time.monotonic()+5
        queue = Queue("tinystudio", connection=connection)
        while queue.count == 0 and time.monotonic() < deadline:
            time.sleep(.02)
        assert queue.count == 1  # delivered, then blocked updating dispatch_after
        started = time.monotonic()
        asyncio.run(dispatcher.close())
        assert time.monotonic()-started < 2
        locker.rollback(); locker.close()
        with factory() as db:
            assert db.get(LabOperation, operation["id"]).dispatch_after is None
        assert client.post(prefix+f"/operations/{operation['id']}/cancel").json()["state"] == "cancelled"
    finally:
        locker.rollback(); locker.close()
        asyncio.run(dispatcher.close())
