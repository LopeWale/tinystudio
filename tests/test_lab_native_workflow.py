"""Serial B HTTP/native wiring with exclusively owned local stores; no Docker.

SQLite here does not qualify PostgreSQL locks/migrations, RQ, a remote machine,
or deployed browser controls. Runner traffic uses real loopback sockets and the
normal HTTP client; experiment code runs in its actual native child process.
"""
from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from tinystudio.api.deps import forget_secret, get_db
from tinystudio.auth import ensure_instance, issue_service_token
from tinystudio.config import reload_config
from tinystudio.db.models import ApiKey, Base, Instance, Project, User
from tinystudio.lab import operations, service as svc
from tinystudio.lab.models import TABLES, LabArtifact, LabAttempt, LabOperation
from tinystudio.lab.runner import Runner, capabilities
from tinystudio.storage import ArtifactStorage


@pytest.fixture
def native_http(tmp_path, monkeypatch):
    from tinystudio.api.app import create_app

    engine = create_engine("sqlite:///"+str(tmp_path / "metadata.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[Instance.__table__, User.__table__,
        Project.__table__, ApiKey.__table__, *TABLES])
    factory = sessionmaker(engine, expire_on_commit=False)
    store = ArtifactStorage(root=tmp_path / "objects")
    with monkeypatch.context() as scoped:
        scoped.setenv("TINYSTUDIO_CF_ACCESS_REQUIRED", "0")
        for name in ("CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET", "TINYSTUDIO_PUBLIC_URL",
                     "TINYSTUDIO_LAB_RQ_CREDENTIAL_FILE"):
            scoped.setenv(name, "")
        scoped.setenv("TINYSTUDIO_STORAGE_BACKEND", "local")
        scoped.setenv("TINYSTUDIO_DATA_DIR", str(tmp_path / "data"))
        reload_config()
        scoped.setattr(svc, "storage", lambda: store)
        forget_secret()
        with factory.begin() as db:
            user = User(email="native-b@fixture.invalid", password_hash="not-a-login", role="admin")
            project = Project(slug="native-b", name="Native B")
            restored = Project(slug="restored-b", name="Restored B")
            db.add_all([user, project, restored]); db.flush()
            token = issue_service_token(ensure_instance(db).secret_key, user.id)
        # Use the registered application routes, without deployment preparation
        # or the Redis-backed dispatcher. Operation workers are invoked explicitly.
        app = create_app(run_migrations=False, ui=False, laboratory_dispatch=False)
        def sessions():
            with factory() as db:
                try:
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
        app.dependency_overrides[get_db] = sessions
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0)); listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        url = "http://127.0.0.1:"+str(listener.getsockname()[1])
        try:
            deadline = time.monotonic()+5
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("owned B HTTP fixture did not start")
                time.sleep(.01)
            with httpx.Client(base_url=url, headers={"Authorization": "Bearer "+token},
                              trust_env=False, timeout=30) as user_client:
                yield SimpleNamespace(http=user_client, url=url, prefix="/api/p/native-b/lab",
                    restored="/api/p/restored-b/lab", factory=factory, store=store, root=tmp_path)
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            listener.close()
            engine.dispose()
            forget_secret()
            assert not thread.is_alive(), "owned HTTP fixture failed to stop"
    reload_config()


def posted(http, path, **kwargs):
    response = http.post(path, **kwargs)
    assert response.status_code in {200, 201, 202}, response.text
    return response.json()


def enroll(native_http, name="native-worker"):
    fixture = native_http
    target = posted(fixture.http, fixture.prefix+"/targets", json={"name": name})
    runner = Runner(fixture.url, target["id"], target["token"], heartbeat_interval=.1)
    runner.advertise()
    return target, runner


def submit_signal(fixture, target, maximum=4):
    saved = posted(fixture.http, fixture.prefix+"/definitions", json={"definition": {
        "name": "Bounded upload", "engine": "synthetic.signal", "parameters": {"samples": 1},
        "execution": {"profile": "trusted_native", "max_seconds": 30, "max_output_bytes": maximum}}})
    return posted(fixture.http, fixture.prefix+"/runs", json={"definition_id": saved["id"],
        "revision": 1, "target_id": target["id"], "idempotency_key": "upload"})


def test_real_http_native_replay_preserves_frozen_inputs_and_recovers_export(native_http, monkeypatch):
    fixture = native_http
    original = b'{"time":0,"value":-1.25,"units":"V"}\n{"time":1,"value":0,"units":"V"}\n{"time":2,"value":2.5,"units":"V"}\n'
    uploaded = posted(fixture.http, fixture.prefix+"/inputs",
        files={"file": ("signal.jsonl", original, "application/jsonl")})
    target, runner = enroll(fixture)
    try:
        definition = {"name": "Frozen replay", "engine": "recorded.replay",
            "inputs": [{"artifact_id": uploaded["id"], "sha256": uploaded["sha256"], "role": "source"}],
            "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 30}}
        saved = posted(fixture.http, fixture.prefix+"/definitions", json={"definition": definition})
        checked = posted(fixture.http, fixture.prefix+"/preflight",
            json={"definition": definition, "target_id": target["id"]})
        assert checked["compatible"]
        submitted = posted(fixture.http, fixture.prefix+"/runs", json={"definition_id": saved["id"],
            "revision": 1, "target_id": target["id"], "idempotency_key": "native-replay"})
        # Later edits cannot change what the already-submitted child receives.
        replacement = posted(fixture.http, fixture.prefix+"/inputs",
            files={"file": ("later.jsonl", b'{"time":0,"value":999,"units":"V"}\n', "application/jsonl")})
        edited = {**definition, "inputs": [{"artifact_id": replacement["id"], "sha256": replacement["sha256"], "role": "source"}]}
        response = fixture.http.put(fixture.prefix+"/definitions/"+saved["id"],
            json={"expected_revision": 1, "definition": edited})
        assert response.status_code == 200, response.text
        assert runner.client.get(fixture.prefix+"/definitions").status_code == 401
        assert runner.run_once(run_id=submitted["id"])
    finally:
        runner.client.close()
    run_path = fixture.prefix+"/runs/"+submitted["id"]
    run = fixture.http.get(run_path).json()
    assert run["state"] == "succeeded", run.get("error")
    assert run["spec_digest"] == submitted["spec_digest"]
    assert run["spec"]["definition"]["inputs"][0]["artifact_id"] == uploaded["id"]
    observed = fixture.http.get(run_path+"/records", params={"artifact": "raw.jsonl"}).json()["records"]
    assert [row["value"] for row in observed] == [-1.25, 0, 2.5]
    assert [row["units"] for row in observed] == ["V"]*3
    history = fixture.http.get(run_path+"/events").json()
    assert history["events"][-1]["kind"] == "succeeded"
    resumed = fixture.http.get(run_path+"/events", params={"after": history["next_after"]}).json()
    assert resumed["events"] == [] and resumed["has_more"] is False
    for artifact in run["result"]["artifacts"]:
        response = fixture.http.get(fixture.prefix+"/artifacts/"+artifact["id"])
        assert response.status_code == 200
        assert len(response.content) == artifact["size"]
        assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
    operation = posted(fixture.http, run_path+"/exports", json={"idempotency_key": "disconnected-export"})
    # Explicit worker invocation proves durable service behavior; it is not RQ
    # delivery qualification. A different HTTP client reconnects by operation ID.
    assert operations.run_operation(operation["id"], fixture.factory)["state"] == "succeeded"
    from tinystudio.lab import cli
    monkeypatch.setenv("TINYSTUDIO_URL", fixture.url)
    monkeypatch.setenv("TINYSTUDIO_API_KEY", fixture.http.headers["authorization"][7:])
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "")
    output = fixture.root / "recovered.zip"
    cli.operation(operation["id"], "native-b", output=output)
    assert output.is_file() and not output.with_name(output.name+".partial").exists()
    with fixture.factory() as db:
        assert db.scalar(select(func.count()).select_from(LabOperation).where(LabOperation.kind == "export")) == 1
    transferred = posted(fixture.http, fixture.restored+"/inputs",
        files={"file": ("research.zip", output.read_bytes(), "application/zip")})
    imported = posted(fixture.http, fixture.restored+"/bundle-imports",
        json={"artifact_id": transferred["id"], "idempotency_key": "restore"})
    restored = operations.run_operation(imported["id"], fixture.factory)
    assert restored["state"] == "succeeded"
    result = fixture.http.get(fixture.restored+"/operations/"+imported["id"]).json()["result"]
    assert result["execution_started"] is False
    restored_input = result["artifact_mapping"][uploaded["id"]]
    assert fixture.http.get(fixture.restored+"/artifacts/"+restored_input).content == original
    assert fixture.http.get(fixture.restored+"/runs").json() == []


def test_identical_upload_redelivery_works_after_exhausting_budget(native_http):
    fixture = native_http
    target, runner = enroll(fixture)
    try:
        submitted = submit_signal(fixture, target)
        attempt = runner.post("/claim", {"capabilities": capabilities(), "run_id": submitted["id"]})["attempt"]
        suffix = runner.prefix+"/attempts/"+attempt["attempt_id"]+"/artifacts"
        body = b"-1.2"
        kwargs = {"params": {"sha256": hashlib.sha256(body).hexdigest()},
            "headers": {"X-Lab-Lease": attempt["lease_token"]},
            "files": {"file": ("measurement.txt", body, "text/plain")}}
        first = posted(runner.client, suffix, **kwargs)
        second = posted(runner.client, suffix, **kwargs)
        assert first == second and first["size"] == 4
        with fixture.factory() as db:
            assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.attempt_id == attempt["attempt_id"])) == 1
            assert db.get(LabAttempt, attempt["attempt_id"]).state == "running"
        kwargs["files"] = {"file": ("different.txt", b"x", "text/plain")}
        kwargs["params"] = {"sha256": hashlib.sha256(b"x").hexdigest()}
        assert runner.client.post(suffix, **kwargs).status_code == 413
    finally:
        runner.client.close()
