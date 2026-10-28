"""RQ adapter boundaries; temporary SQLite metadata and fake HTTP/Redis only."""
from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tinystudio.db.models import Base, Project, User
from tinystudio.lab import rq_adapter as adapter, service as svc
from tinystudio.lab.contracts import checksum
from tinystudio.lab.models import LabAttempt, LabDefinition, LabDispatch, LabRevision, LabRun, LabTarget


@pytest.fixture
def credential(tmp_path, monkeypatch):
    path = tmp_path / "operator.json"
    path.write_text(json.dumps({"server": "http://127.0.0.1:8123", "target_id": "target", "token": "private-token"}))
    path.chmod(0o600)
    monkeypatch.setenv(adapter.CREDENTIAL_ENV, str(path))
    return path


def test_loader_explicit_and_environment_keep_secrets_local(credential):
    assert adapter.load_credentials()["target_id"] == "target"
    assert adapter.load_credentials(credential)["token"] == "private-token"
    assert "profile_config" not in adapter.load_credentials()


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o400, 0o700])
def test_loader_rejects_nonprivate_modes(credential, mode):
    credential.chmod(mode)
    with pytest.raises(ValueError, match="0600") as error:
        adapter.load_credentials()
    assert "private-token" not in str(error.value)
    assert str(credential) not in str(error.value)


def test_loader_rejects_symlink_directory_and_duplicate_json(credential, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(credential)
    with pytest.raises(ValueError, match="nonsymlink"):
        adapter.load_credentials(link)
    folder = tmp_path / "folder"
    folder.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="regular"):
        adapter.load_credentials(folder)
    credential.write_text('{"token":"one","token":"two"}')
    with pytest.raises(ValueError, match="unique"):
        adapter.load_credentials()


@pytest.mark.parametrize("value", [
    {"server": "http://remote.example", "target_id": "target", "token": "secret"},
    {"server": "http://127.0.0.1", "target_id": "../other", "token": "secret"},
    {"server": "http://127.0.0.1", "target_id": "target", "token": "secret\nheader"},
    {"server": "http://127.0.0.1", "target_id": "target", "token": "secret", "path": "/etc/passwd"},
])
def test_loader_rejects_unsafe_values_without_echoing_them(credential, value):
    credential.write_text(json.dumps(value))
    with pytest.raises(ValueError) as error:
        adapter.load_credentials()
    assert "secret" not in str(error.value)
    assert "/etc/passwd" not in str(error.value)


def test_reusable_loader_accepts_native_policy_but_rq_adapter_refuses(credential):
    data = adapter.load_credentials()
    data["profile_config"] = {"schema_version": 1, "backends": {"native": {"enabled": True}}}
    credential.write_text(json.dumps(data))
    assert adapter.load_credentials()["profile_config"] == data["profile_config"]
    with pytest.raises(ValueError, match="requires backends.rq"):
        adapter._require_rq(adapter.load_credentials())


@pytest.fixture
def setup(credential, tmp_path, monkeypatch):
    from tinystudio.storage import ArtifactStorage
    monkeypatch.setattr(svc, "storage", lambda: ArtifactStorage(root=tmp_path / "objects"))
    engine = create_engine("sqlite:///"+str(tmp_path / "metadata.db"))
    Base.metadata.create_all(engine, tables=[User.__table__, Project.__table__, LabDefinition.__table__,
        LabRevision.__table__, LabTarget.__table__, LabRun.__table__, LabDispatch.__table__, LabAttempt.__table__])
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add_all([Project(id="project", slug="project", name="Fixture"), Project(id="other", slug="other", name="Other")])
        db.add_all([LabTarget(id="target", project_id="project", name="worker", token_hash=svc.secret_hash("private-token")),
                    LabTarget(id="foreign", project_id="other", name="other")])
        db.add(LabDefinition(id="definition", project_id="project", name="Fixture"))
    monkeypatch.setattr(adapter, "session_factory", lambda: factory)
    monkeypatch.setattr(adapter, "dispose_after_fork", lambda: None)
    events = []
    class FakeRunner:
        def __init__(self, server, target_id, token, **kwargs):
            assert token == "private-token"
            assert kwargs["profile_config"]["backends"]["rq"]["profiles"] == ["trusted_native"]
            self.client = SimpleNamespace(close=lambda: events.append("close"))
        def advertise(self):
            events.append("advertise")
            return {}
        def run_once(self, *, run_id=None):
            events.append(("run", run_id))
            return True
    monkeypatch.setattr(adapter, "Runner", FakeRunner)
    yield factory, events, FakeRunner
    engine.dispose()


def add_run(factory, id_, *, target="target", project="project", backend="rq", profile="trusted_native", state="queued", delay=0):
    spec = {"definition": {"execution": {"backend": backend, "profile": profile}}}
    with factory.begin() as db:
        db.add(LabRun(id=id_, project_id=project, definition_id="definition", revision=1, target_id=target,
                      idempotency_key=id_, spec=spec, spec_digest=checksum(spec), state=state))
        db.add(LabDispatch(run_id=id_, available=svc.now()+timedelta(seconds=delay)))


def test_failed_attempt_diagnostic_survives_retry_and_duplicate_completion(setup):
    from sqlalchemy import delete, select
    from tinystudio.lab.models import LabEvent

    factory, _, _ = setup
    LabEvent.__table__.create(factory.kw["bind"])
    from tinystudio.lab.models import LabArtifact
    LabArtifact.__table__.create(factory.kw["bind"])
    add_run(factory, "failed-history", state="running")
    diagnostic = "Measured -0.125 V: controlled failure. " + "detail "*1500
    with factory.begin() as db:
        db.execute(delete(LabDispatch).where(LabDispatch.run_id == "failed-history"))
        db.add(LabAttempt(id="attempt-history", run_id="failed-history", target_id="target", number=1,
            token_hash=svc.secret_hash("lease-history"), lease_until=svc.now()+timedelta(seconds=60),
            state="running", runtime={}))
    with factory.begin() as db:
        target = db.get(LabTarget, "target")
        for _ in range(2):
            assert svc.finish(db, target, "attempt-history", "lease-history", "failed", None, diagnostic) == {
                "accepted": True, "state": "failed"}
    with factory.begin() as db:
        digest = db.get(LabRun, "failed-history").spec_digest
        retried = svc.retry(db, "project", "failed-history")
        assert retried.state == "queued" and retried.error is None
        assert retried.spec_digest == digest
    with factory() as db:
        history = list(db.scalars(select(LabEvent).where(LabEvent.run_id == "failed-history").order_by(LabEvent.seq)))
        assert [row.kind for row in history] == ["server.outcome", "failed", "retry_requested"]
        assert history[1].payload == {"attempt_id": "attempt-history", "error": diagnostic[:8192]}
        retained = svc.get_artifact(db, "project", history[0].payload["outcome_artifact_id"])
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            archived = json.loads(svc.materialize(retained, Path(directory)).read_bytes())
        assert archived["error"] == diagnostic[:8192]
        assert archived["state"] == "failed" and archived["attempt_id"] == "attempt-history"
        assert db.get(LabAttempt, "attempt-history").state == "failed"


def test_dispatch_advertises_and_selects_exact_committed_target_due_rq_only(setup):
    factory, events, _ = setup
    add_run(factory, "due")
    add_run(factory, "native", backend="native")
    add_run(factory, "foreign", target="foreign", project="other")
    add_run(factory, "later", delay=100)
    add_run(factory, "done", state="succeeded")
    deliveries = []
    def enqueue(function, run_id, *, job_id):
        assert events == ["advertise", "close"]
        deliveries.append((function, run_id, job_id))
    assert adapter.dispatch_once(enqueue) == {"selected": 1, "enqueued": 1, "existing": 0}
    assert deliveries[0][0] is adapter.execute_run
    assert deliveries[0][1] == "due"
    assert deliveries[0][2].startswith("lab-rq-")
    assert "private-token" not in str(deliveries)
    with factory() as db:
        assert db.get(LabRun, "due").state == "queued"
    assert adapter.dispatch_once(lambda *a, **k: False) == {"selected": 1, "enqueued": 0, "existing": 1}


def test_stable_delivery_identity_changes_only_with_attempt_generation(setup):
    factory, _, _ = setup
    add_run(factory, "retry")
    ids = []
    enqueue = lambda func, run_id, *, job_id: ids.append(job_id)
    adapter.dispatch_once(enqueue)
    adapter.dispatch_once(enqueue)
    assert ids[0] == ids[1]
    with factory.begin() as db:
        db.add(LabAttempt(id="attempt", run_id="retry", target_id="target", number=1, token_hash="hash",
                          lease_until=svc.now(), state="lost", runtime={}))
    adapter.dispatch_once(enqueue)
    assert ids[2] != ids[0]


def test_execute_only_exact_scoped_rq_run_and_closes_owned_client(setup):
    factory, events, _ = setup
    add_run(factory, "specific")
    add_run(factory, "foreign", target="foreign", project="other")
    add_run(factory, "native", backend="native")
    assert adapter.execute_run("specific") == {"run_id": "specific", "claimed": True}
    assert events == [("run", "specific"), "close"]
    with pytest.raises(svc.LabError, match="not found"):
        adapter.execute_run("foreign")
    with pytest.raises(svc.LabError, match="requires frozen backend rq"):
        adapter.execute_run("native")


def test_duplicate_completed_delivery_does_not_contact_runner(setup):
    factory, events, _ = setup
    add_run(factory, "done", state="succeeded")
    assert adapter.execute_run("done") == {"run_id": "done", "claimed": False}
    assert events == []


def test_advertisement_failure_closes_client_and_enqueues_nothing(setup, monkeypatch):
    factory, events, runner = setup
    add_run(factory, "due")
    def failed(self):
        raise RuntimeError("HTTP unavailable")
    monkeypatch.setattr(runner, "advertise", failed)
    with pytest.raises(RuntimeError, match="HTTP unavailable"):
        adapter.dispatch_once(lambda *a, **k: pytest.fail("must not enqueue before advertisement"))
    assert events == ["close"]


def test_execution_transport_failure_closes_client(setup, monkeypatch):
    factory, events, runner = setup
    add_run(factory, "due")
    def failed(self, **kwargs):
        raise RuntimeError("HTTP unavailable")
    monkeypatch.setattr(runner, "run_once", failed)
    with pytest.raises(RuntimeError, match="HTTP unavailable"):
        adapter.execute_run("due")
    assert events == ["close"]


def test_rq_active_delivery_retained_terminal_replaced_and_missing_recreated(monkeypatch):
    calls = []
    class FakeQueue:
        status = "started"
        previous = True
        connection = SimpleNamespace(lock=lambda *a, **k: nullcontext())
        def fetch_job(self, id_):
            if self.previous:
                return SimpleNamespace(get_status=lambda: self.status, delete=lambda: calls.append("delete"))
        def enqueue_call(self, func, **kwargs):
            calls.append(kwargs)
    queue = FakeQueue()
    monkeypatch.setattr(adapter, "queue", lambda: queue)
    assert adapter._enqueue(adapter.execute_run, "run", job_id="stable") is False
    assert calls == []
    queue.status = "failed"
    assert adapter._enqueue(adapter.execute_run, "run", job_id="stable") is True
    assert calls[0] == "delete"
    assert calls[1]["args"] == ("run",)
    assert calls[1]["unique"] is True and calls[1]["job_id"] == "stable"
    queue.previous = False
    assert adapter._enqueue(adapter.execute_run, "run", job_id="missing") is True
    assert calls[-1]["job_id"] == "missing"


def test_unique_enqueue_race_leaves_authority_to_postgres(monkeypatch):
    def duplicate(*args, **kwargs):
        raise adapter.DuplicateJobError("already exists")
    queue = SimpleNamespace(connection=SimpleNamespace(lock=lambda *a, **k: nullcontext()),
                            fetch_job=lambda id_: None, enqueue_call=duplicate)
    monkeypatch.setattr(adapter, "queue", lambda: queue)
    assert adapter._enqueue(adapter.execute_run, "run", job_id="same") is False


def test_dispatch_does_not_see_uncommitted_run(setup):
    factory, _, _ = setup
    spec = {"definition": {"execution": {"backend": "rq", "profile": "trusted_native"}}}
    with factory() as pending:
        pending.add(LabRun(id="pending", project_id="project", definition_id="definition", revision=1,
                           target_id="target", idempotency_key="pending", spec=spec,
                           spec_digest=checksum(spec), state="queued"))
        pending.add(LabDispatch(run_id="pending", available=svc.now()))
        pending.flush()
        assert adapter.dispatch_once(lambda *a, **k: pytest.fail("uncommitted run was delivered"))["selected"] == 0
        pending.rollback()


def test_corrupted_frozen_spec_never_delivers_or_executes(setup):
    factory, events, _ = setup
    add_run(factory, "corrupt")
    with factory.begin() as db:
        db.get(LabRun, "corrupt").spec_digest = "0"*64
    with pytest.raises(svc.LabError, match="checksum"):
        adapter.dispatch_once(lambda *a, **k: pytest.fail("corrupt spec was delivered"))
    with pytest.raises(svc.LabError, match="checksum"):
        adapter.execute_run("corrupt")
    assert events == ["advertise", "close"]
