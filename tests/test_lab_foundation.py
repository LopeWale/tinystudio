"""Real PostgreSQL and HTTP runner checks in a newly owned disposable database.

Run with --noconftest: the application's shared test database is never touched.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from tinystudio.auth import ensure_instance, issue_service_token
from tinystudio.api.deps import forget_secret, get_db
from tinystudio.config import config
from tinystudio.db.models import Base, Dataset, Experiment, Job, Project, Snapshot, User
from tinystudio.lab import service as svc
from tinystudio.lab.api import install
from tinystudio.lab.contracts import DatasetManifest, ExperimentDefinition, checksum, numeric_equal
from tinystudio.lab.models import TABLES, LabAttempt, LabDispatch, LabEvent, LabRun
from tinystudio.lab.runner import Runner, capabilities, validate_url
from tinystudio.storage import ArtifactStorage


@pytest.fixture(scope="module")
def database():
    # Fresh UUID ownership: never truncate/drop a pre-existing or configured database.
    admin = create_engine(config().database_url, isolation_level="AUTOCOMMIT")
    name = "tinystudio_lab_test_" + uuid.uuid4().hex[:12]
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception:
        admin.dispose()
        raise RuntimeError("lab integration tests need PostgreSQL CREATE DATABASE access; no shared database was modified") from None
    engine = create_engine(admin.url.set(database=name), connect_args={"options": "-c lock_timeout=3000 -c statement_timeout=15000"})
    try:
        from tinystudio.db.migrate import downgrade, upgrade
        from sqlalchemy import inspect
        upgrade("e8a0b4c32d16", engine.url.render_as_string(hide_password=False))
        legacy_tables = inspect(engine).get_table_names()
        def legacy_schema():
            inspector = inspect(engine)
            return {name: [(col["name"], str(col["type"]), col["nullable"], col["default"])
                           for col in inspector.get_columns(name)]
                    for name in legacy_tables}
        before = legacy_schema()
        with engine.begin() as conn:
            conn.execute(Project.__table__.insert().values(id="migration-sentinel", slug="existing-before-lab", name="Preserved"))
        # Exercise the application's discovered migration chain, rather than
        # installing candidate schema SQL through a test-only shortcut.
        upgrade("head", engine.url.render_as_string(hide_password=False))
        with engine.begin() as conn:
            assert conn.execute(select(Project.name).where(Project.id == "migration-sentinel")).scalar() == "Preserved"
            assert conn.execute(text("select version_num from alembic_version")).scalar() == "h1d3e7f65a49"
        assert legacy_schema() == before
        assert {table.name for table in TABLES} <= set(inspect(engine).get_table_names())
        downgrade("e8a0b4c32d16", engine.url.render_as_string(hide_password=False))
        assert not ({table.name for table in TABLES} & set(inspect(engine).get_table_names()))
        assert legacy_schema() == before
        with engine.connect() as conn:
            assert conn.execute(select(Project.name).where(Project.id == "migration-sentinel")).scalar() == "Preserved"
        upgrade("head", engine.url.render_as_string(hide_password=False))
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            # Own UUID database only. In-process RQ workers may retain idle
            # driver connections after exercising their fork-safety cleanup.
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def setup(database, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "storage", lambda: ArtifactStorage(root=tmp_path / "objects"))
    forget_secret()
    with database.begin() as db:
        user = User(email=f"{uuid.uuid4().hex}@lab.invalid", password_hash="not-used", role="admin")
        project = Project(slug="lab-"+uuid.uuid4().hex[:10], name="Lab qualification")
        other = Project(slug="lab-"+uuid.uuid4().hex[:10], name="Other")
        db.add_all([user, project, other])
        db.flush()
        token = issue_service_token(ensure_instance(db).secret_key, user.id)
        project_id, slug, other_id = project.id, project.slug, other.id
    app = FastAPI()
    install(app, include_data_bridges=True)
    def sessions():
        with database() as db:
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
    app.dependency_overrides[get_db] = sessions
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        yield database, project_id, other_id, client, f"/api/p/{slug}/lab", app


def definition(**extra):
    return {"name": "Fixture", "engine": "synthetic.signal", "parameters": {"samples": 12, "dt": .25},
            "execution": {"profile": "trusted_native"}, **extra}


def queued(db, project, **extra):
    d = svc.save_definition(db, project, definition(**extra))
    return svc.submit(db, project, d["id"], d["revision"], "local", uuid.uuid4().hex)


def test_snapshot_idempotency_and_revision_conflict(setup):
    factory, project, _, _, _, _ = setup
    with factory.begin() as db:
        original = queued(db, project)
        spec_hash, run_id, definition_id = original.spec_digest, original.id, original.definition_id
        updated = svc.save_definition(db, project, definition(seed=99), id_=definition_id, expected=1)
        assert updated["revision"] == 2
        again = svc.submit(db, project, definition_id, 1, "local", original.idempotency_key)
        assert again.id == run_id
        assert again.spec["definition"]["seed"] == 42
        assert again.spec_digest == spec_hash == checksum(again.spec)
        with pytest.raises(svc.LabError, match="different submission"):
            svc.submit(db, project, definition_id, 2, "local", original.idempotency_key)
        with pytest.raises(svc.LabError, match="changed"):
            svc.save_definition(db, project, definition(), id_=definition_id, expected=1)


def test_existing_training_link_is_scoped_frozen_and_does_not_rewrite_legacy_state(setup):
    from tinystudio.db.models import ExperimentLog

    factory, project, other, _, _, _ = setup
    original_config = {"base_model": "local-fixture", "training_type": "lora", "lr": 0.000125}
    original_status = {"steps": 7, "train_loss": 1.25}
    with factory.begin() as db:
        snapshot = Snapshot(project_id=project, name="legacy-link", dataset_name="legacy-data",
                            training_count=10, validation_count=2)
        db.add(snapshot)
        db.flush()
        job = Job(project_id=project, name="legacy-job", snapshot_id=snapshot.id, snapshot_name=snapshot.name)
        db.add(job)
        db.flush()
        experiment = Experiment(job_id=job.id, number=1, alias="Existing", config=original_config,
                                state="succeeded", status=original_status, artifact_dir="legacy-job/1")
        db.add(experiment)
        db.flush()
        db.add(ExperimentLog(experiment_id=experiment.id, kind="loss", step=7,
                             payload={"train_loss": 1.25, "validation_loss": 1.5}))
        linked = queued(db, project, parameters={"compatibility": {"job_id": job.id, "experiment_number": 1}})
        run_id, job_id, experiment_id = linked.id, job.id, experiment.id
        assert (linked.job_id, linked.experiment_id) == (job_id, experiment_id)
        assert linked.spec["compatibility"]["experiment_config_sha256"] == checksum(original_config)
        assert experiment.state == "succeeded" and experiment.status == original_status
        assert snapshot.training_count == 10 and snapshot.validation_count == 2
    with factory.begin() as db:
        experiment = db.get(Experiment, experiment_id)
        experiment.config = {**original_config, "lr": .5}
        frozen = svc.get_run(db, project, run_id)
        assert frozen.spec["compatibility"]["experiment_config"] == original_config
        log = db.scalar(select(ExperimentLog).where(ExperimentLog.experiment_id == experiment_id))
        assert log.payload == {"train_loss": 1.25, "validation_loss": 1.5}
        with pytest.raises(svc.LabError, match="not found in this project"):
            queued(db, other, parameters={"compatibility": {"job_id": job_id}})


@pytest.mark.parametrize("reference", [
    {"experiment_id": "orphan"}, {"job_id": ""}, {"job_id": "existing", "experiment_number": 0},
    {"job_id": "existing", "experiment_id": "one", "experiment_number": 1},
    {"job_id": "existing", "change_state": "running"},
])
def test_invalid_legacy_references_are_rejected_before_submission(reference):
    with pytest.raises(ValueError):
        ExperimentDefinition.model_validate(definition(parameters={"compatibility": reference}))


def test_dispatch_is_transactional_and_lease_fences_stale_runner(setup):
    factory, project, _, _, _, _ = setup
    with factory.begin() as db:
        enrolled = svc.enroll(db, project, "local")
    first = factory()
    try:
        run = queued(first, project)
        with factory.begin() as second:
            target = svc.authenticate_target(second, enrolled["id"], enrolled["token"])
            assert svc.claim(second, target, capabilities()) is None
        first.commit()
        run_id = run.id
    finally:
        first.close()
    with factory.begin() as db:
        target = svc.authenticate_target(db, enrolled["id"], enrolled["token"])
        claimed = svc.claim(db, target, capabilities())
        assert claimed["run_id"] == run_id
        assert svc.claim(db, target, capabilities()) is None
        attempt = db.get(LabAttempt, claimed["attempt_id"])
        attempt.lease_until = svc.now()-timedelta(seconds=1)
    with factory.begin() as db:
        target = svc.authenticate_target(db, enrolled["id"], enrolled["token"])
        with pytest.raises(svc.LabError, match="expired"):
            svc.heartbeat(db, target, claimed["attempt_id"], claimed["lease_token"], [])
        assert svc.recover(db, target) == 1
        run = svc.retry(db, project, run_id)
        assert run.spec_digest == claimed["spec_digest"]
        second = svc.claim(db, target, capabilities())
        assert second["attempt_id"] != claimed["attempt_id"]
        with pytest.raises(svc.LabError, match="already ended"):
            svc.finish(db, target, claimed["attempt_id"], claimed["lease_token"], "succeeded", {"artifacts": []}, None)


def test_project_and_runner_scope(setup):
    factory, project, other, client, prefix, app = setup
    with factory.begin() as db:
        run = queued(db, project)
        with pytest.raises(svc.LabError, match="not found"):
            svc.get_run(db, other, run.id)
    response = client.post(prefix+"/targets", json={"name": "local"})
    assert response.status_code == 201, response.text
    target = response.json()
    with TestClient(app, headers={"Authorization": "Bearer "+target["token"]}) as machine:
        assert machine.get(prefix+"/definitions").status_code == 401
        assert machine.post("/api/lab/runner/unknown/claim", json={"capabilities": capabilities()}).status_code == 401
        claimed = machine.post(f"/api/lab/runner/{target['id']}/claim", json={"capabilities": capabilities()}).json()["attempt"]
        response = machine.get(f"/api/lab/runner/{target['id']}/attempts/{claimed['attempt_id']}/inputs/missing",
                               headers={"X-Lab-Lease": claimed["lease_token"]})
        assert response.status_code == 403


def test_actual_http_subprocess_evidence_and_bundle_restore(setup, tmp_path):
    factory, project, other, client, prefix, app = setup
    target = client.post(prefix+"/targets", json={"name": "local"}).json()
    created = client.post(prefix+"/definitions", json={"definition": definition(pipeline=[{"kind": "offset", "parameters": {"offset": -2}}])})
    assert created.status_code == 201, created.text
    submitted = client.post(prefix+"/runs", json={"definition_id": created.json()["id"], "revision": 1, "idempotency_key": "roundtrip"})
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["id"]
    with TestClient(app, headers={"Authorization": "Bearer "+target["token"]}) as machine:
        runner = Runner("http://localhost", target["id"], target["token"], client=machine)
        assert runner.run_once()
    finished = client.get(prefix+f"/runs/{run_id}").json()
    assert finished["state"] == "succeeded", finished.get("error")
    rows = client.get(prefix+f"/runs/{run_id}/records").json()["records"]
    raw = client.get(prefix+f"/runs/{run_id}/records?artifact=raw.jsonl").json()["records"]
    assert len(rows) == len(raw) == 12
    assert rows[0]["value"] == raw[0]["value"]-2
    events = client.get(prefix+f"/runs/{run_id}/events").json()
    assert [e["seq"] for e in events["events"]] == list(range(1, events["next_after"]+1))
    assert client.get(prefix+f"/runs/{run_id}/events?after={events['next_after']}").json()["events"] == []
    bundle = client.get(prefix+f"/runs/{run_id}/export")
    assert bundle.status_code == 200, bundle.text[:300] if bundle.status_code != 200 else ""
    saved = tmp_path / "bundle.zip"
    saved.write_bytes(bundle.content)
    from tinystudio.lab.portable import import_bundle
    with factory.begin() as db:
        restored = import_bundle(db, other, saved, tmp_path)
        assert restored["execution_started"] is False
        assert restored["definition"]["definition"]["parameters"]["samples"] == 12
        assert len(restored["artifact_mapping"]) >= 4


def test_input_hashes_partial_upload_and_cancel_are_truthful(setup, tmp_path):
    factory, project, _, client, prefix, app = setup
    item = client.post(prefix+"/inputs", files={"file": ("source.jsonl", b'{"time":0,"value":-0.125}\n', "application/jsonl")}).json()
    target = client.post(prefix+"/targets", json={"name": "local"}).json()
    with factory.begin() as db:
        run = queued(db, project, engine="recorded.replay", inputs=[{"artifact_id": item["id"], "sha256": item["sha256"]}])
        run_id = run.id
    with TestClient(app, headers={"Authorization": "Bearer "+target["token"]}) as machine:
        claimed = machine.post(f"/api/lab/runner/{target['id']}/claim", json={"capabilities": capabilities()}).json()["attempt"]
        path = f"/api/lab/runner/{target['id']}/attempts/{claimed['attempt_id']}"
        headers = {"X-Lab-Lease": claimed["lease_token"]}
        upload = machine.post(path+"/artifacts?sha256="+"0"*64, files={"file": ("wrong.json", b"wrong")}, headers=headers)
        assert upload.status_code == 409
        assert client.get(prefix+f"/runs/{run_id}").json()["result"] is None
        client.post(prefix+f"/runs/{run_id}/cancel")
        heartbeat = machine.post(path+"/heartbeat", json={}, headers=headers)
        assert heartbeat.json()["cancel_requested"] is True
        assert client.get(prefix+f"/runs/{run_id}").json()["state"] == "running"
        done = {"state": "cancelled"}
        assert machine.post(path+"/finish", json=done, headers=headers).status_code == 200
        assert machine.post(path+"/finish", json=done, headers=headers).status_code == 200
        assert client.get(prefix+f"/runs/{run_id}").json()["state"] == "cancelled"


def test_contracts_preserve_numeric_and_partition_meaning():
    assert numeric_equal(-.125, -.125)
    assert not numeric_equal(-.125, .125)
    assert not numeric_equal(1.2, 12)
    assert not numeric_equal(float("nan"), 0)
    with pytest.raises(ValueError, match="partition leakage"):
        DatasetManifest(artifacts=[], partitions={"train": ["episode1"], "test": ["episode1"]})
    with pytest.raises(ValueError, match="scoped artifact"):
        ExperimentDefinition(**definition(parameters={"resume_from": "/private/model"}))
    with pytest.raises(ValueError):
        ExperimentDefinition(**definition(parameters={"amplitude": float("nan")}))
    with pytest.raises(ValueError, match="HTTPS"):
        validate_url("http://remote.example")


def test_omitted_native_selection_cannot_dispatch(setup):
    _, _, _, client, prefix, _ = setup
    omitted = definition()
    del omitted["execution"]
    assert client.post(prefix+"/definitions", json={"definition": omitted}).status_code == 422
    omitted["execution"] = {}
    assert client.post(prefix+"/definitions", json={"definition": omitted}).status_code == 422


def test_concurrent_claim_and_renew_recovery_lock_order(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    factory, project, _, _, _, _ = setup
    with factory.begin() as db:
        target = svc.enroll(db, project, "local")
        run = queued(db, project)
        run_id = run.id
    barrier = Barrier(2)
    def claim():
        with factory.begin() as db:
            db.execute(text("SET LOCAL lock_timeout = '2s'"))
            machine = svc.resolve_target(db, project, target["id"])
            barrier.wait(timeout=2)
            return svc.claim(db, machine, capabilities())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        results = [f.result(timeout=5) for f in futures]
    assert sum(item is not None for item in results) == 1
    claimed = next(item for item in results if item)
    barrier = Barrier(2)
    def operation(heartbeat):
        with factory.begin() as db:
            db.execute(text("SET LOCAL lock_timeout = '2s'"))
            machine = svc.resolve_target(db, project, target["id"])
            barrier.wait(timeout=2)
            if heartbeat:
                return svc.heartbeat(db, machine, claimed["attempt_id"], claimed["lease_token"], [])
            return svc.recover(db, machine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(operation, action) for action in (True, False)]
        results = [f.result(timeout=5) for f in futures]
    assert results[1] == 0
    with factory.begin() as db:
        assert svc.get_run(db, project, run_id).state == "running"


def test_completion_rebuilds_metadata_and_rejects_duplicates(setup, tmp_path):
    factory, project, _, _, _, _ = setup
    with factory.begin() as db:
        target = svc.enroll(db, project, "local")
        run = queued(db, project)
        machine = svc.resolve_target(db, project, target["id"])
        claimed = svc.claim(db, machine, capabilities())
        source = tmp_path / "source.jsonl"
        source.write_text('{"time":0,"value":1}\n')
        artifact = svc.store_artifact(db, project, source, "real.jsonl", "application/jsonl", "raw", claimed["attempt_id"])
        wrong = {**svc.artifact_dict(artifact), "name": "fabricated.jsonl", "size": 0, "role": "forged"}
        with pytest.raises(svc.LabError, match="duplicate"):
            svc.finish(db, machine, claimed["attempt_id"], claimed["lease_token"], "succeeded", {"artifacts": [wrong, wrong]}, None)
        svc.finish(db, machine, claimed["attempt_id"], claimed["lease_token"], "succeeded", {"artifacts": [wrong]}, None)
        assert run.result["artifacts"] == [svc.artifact_dict(artifact)]


def test_import_rejects_self_consistent_wrong_references(setup, tmp_path):
    from zipfile import ZipFile
    from tinystudio.lab.portable import export_bundle, import_bundle
    from tinystudio.lab.contracts import canonical
    factory, project, other, _, _, _ = setup
    with factory.begin() as db:
        source = tmp_path / "source"
        source.write_bytes(b'{"time":0,"value":1}\n')
        artifact = svc.store_artifact(db, project, source, "source.jsonl", "application/jsonl")
        run = queued(db, project, engine="recorded.replay", inputs=[{"artifact_id": artifact.id, "sha256": artifact.sha256}])
        # Export requires terminal evidence. Cancel this unexecuted fixture so
        # the assertions below reach their intended malformed-import boundary.
        assert svc.cancel(db, project, run.id).state == "cancelled"
        bundle = export_bundle(db, project, run.id, tmp_path)
        with ZipFile(bundle) as archive:
            members = {i.filename: archive.read(i) for i in archive.infolist()}
        original = json.loads(members["manifest.json"])
        for variant in ("duplicate", "wrong_reference"):
            manifest = json.loads(json.dumps(original))
            if variant == "duplicate":
                manifest["artifacts"].append(dict(manifest["artifacts"][0]))
            else:
                manifest["run"]["spec"]["definition"]["inputs"][0]["sha256"] = "0"*64
                manifest["run"]["spec_digest"] = checksum(manifest["run"]["spec"])
            changed = tmp_path / (variant+".zip")
            with ZipFile(changed, "w") as archive:
                for name, payload in members.items():
                    if name == "manifest.json":
                        payload = canonical(manifest)
                    elif name == "manifest.sha256":
                        payload = checksum(manifest).encode()
                    archive.writestr(name, payload)
            with pytest.raises(svc.LabError, match="duplicate|reference differs"):
                import_bundle(db, other, changed, tmp_path)


def test_nested_compiler_evidence_publishes_and_roundtrips(setup, tmp_path):
    import hashlib
    from tinystudio.lab.portable import import_bundle

    factory, project, other, client, prefix, app = setup
    target = client.post(prefix+"/targets", json={"name": "local"}).json()
    with factory.begin() as db:
        run_id = queued(db, project).id
    with TestClient(app, headers={"Authorization": "Bearer "+target["token"]}) as machine:
        claimed = machine.post(f"/api/lab/runner/{target['id']}/claim", json={"capabilities": capabilities()}).json()["attempt"]
        url = f"/api/lab/runner/{target['id']}/attempts/{claimed['attempt_id']}"
        headers = {"X-Lab-Lease": claimed["lease_token"]}
        data = b"compiler output retained verbatim\n"
        sha = hashlib.sha256(data).hexdigest()
        for name in ("../escape.log", "/absolute.log", "stage/../escape.log", "stage//bad.log"):
            rejected = machine.post(url+"/artifacts", params={"sha256": sha}, headers=headers,
                                    files={"file": (name, data, "text/plain")})
            assert rejected.status_code == 400, rejected.text
        response = machine.post(url+"/artifacts", params={"sha256": sha}, headers=headers,
                                files={"file": ("stage-0/stdout.log", data, "text/plain")})
        assert response.status_code == 201, response.text
        artifact = response.json()
        assert artifact["name"] == "stage-0/stdout.log"
        assert machine.post(url+"/finish", json={"state": "succeeded", "result": {"artifacts": [artifact]}}, headers=headers).status_code == 200
    downloaded = client.get(prefix+"/artifacts/"+artifact["id"])
    assert downloaded.content == data
    assert 'filename="stdout.log"' in downloaded.headers["content-disposition"]
    exported = client.get(prefix+f"/runs/{run_id}/export")
    assert exported.status_code == 200
    bundle = tmp_path / "compiler.zip"
    bundle.write_bytes(exported.content)
    with factory.begin() as db:
        restored = import_bundle(db, other, bundle, tmp_path)
        retained = svc.get_artifact(db, other, restored["artifact_mapping"][artifact["id"]])
        assert retained.name == "stage-0/stdout.log"
        assert retained.sha256 == sha


def test_preflight_is_read_only_and_submission_and_claim_obey_backend_scope(setup):
    from sqlalchemy import func
    from tinystudio.lab.models import LabDefinition, LabTarget

    factory, project, other, client, prefix, _ = setup
    with factory.begin() as db:
        before = tuple(db.scalar(select(func.count()).select_from(model).where(model.project_id == project))
                       for model in (LabDefinition, LabRun, LabTarget))
    report = client.post(prefix+"/preflight", json={"definition": definition()})
    assert report.status_code == 200, report.text
    assert report.json()["compatible"] is True
    assert report.json()["target_online"] is False
    assert report.json()["execution_started"] is False
    with factory.begin() as db:
        after = tuple(db.scalar(select(func.count()).select_from(model).where(model.project_id == project))
                      for model in (LabDefinition, LabRun, LabTarget))
        assert before == after
        foreign = svc.enroll(db, other, "remote")
    assert client.post(prefix+"/preflight", json={"definition": definition(), "target_id": foreign["id"]}).status_code == 404
    with factory.begin() as db:
        enrolled = svc.enroll(db, project, "local")
        run_id = queued(db, project).id
    caps = capabilities()
    del caps["engines_by_backend"]["native"]
    caps["engines_by_backend"]["container"] = ["synthetic.signal"]
    with factory.begin() as db:
        machine = svc.authenticate_target(db, enrolled["id"], enrolled["token"])
        assert svc.claim(db, machine, caps) is None
        assert svc.get_run(db, project, run_id).state == "queued"
    report = client.post(prefix+"/preflight", json={"definition": definition()}).json()
    assert report["compatible"] is False
    assert any(c["code"] == "engine" and not c["passed"] for c in report["checks"])
    with factory.begin() as db:
        new = svc.save_definition(db, project, definition())
        with pytest.raises(svc.LabError, match="advertise"):
            svc.submit(db, project, new["id"], 1, "local", "incompatible-engine")


def test_snapshot_bridge_http_uses_scoped_existing_evidence(setup):
    from tinystudio.db.models import SnapshotRow

    factory, project, other, client, prefix, _ = setup
    with factory.begin() as db:
        snapshot = Snapshot(project_id=project, name="bridge-source", dataset_name="source", training_count=1, validation_count=1)
        foreign = Snapshot(project_id=other, name="foreign-only", dataset_name="source", training_count=1, validation_count=0)
        db.add_all([snapshot, foreign]); db.flush()
        snapshot_id, foreign_id = snapshot.id, foreign.id
        for split, text_ in (("train", "training evidence"), ("val", "held-out evidence")):
            db.add(SnapshotRow(snapshot_id=snapshot.id, split=split, position=0,
                row={"messages": [{"role": "user", "content": text_}], "custom_provenance": "retained"}))
    exported = client.post(prefix+"/inputs/from-snapshot", json={"snapshot": snapshot_id})
    assert exported.status_code == 201, exported.text
    result = exported.json()
    assert result["partition_counts"] == {"train": 1, "validation": 1}
    assert result["final_test_available"] is False
    content = client.get(prefix+"/artifacts/"+result["id"])
    rows = [json.loads(line) for line in content.text.splitlines()]
    assert [r["partition"] for r in rows] == ["train", "validation"]
    assert rows[0]["source_record_id"] == f"snapshot:{snapshot_id}:train:0"
    assert rows[1]["custom_provenance"] == "retained"
    denied = client.post(prefix+"/inputs/from-snapshot", json={"snapshot": foreign_id})
    assert denied.status_code == 404, denied.text


def test_changed_operator_resource_allowance_prevents_attempt_claim(setup):
    import copy
    factory, project, _, _, _, _ = setup
    caps = {"implementation_digest": svc.implementation_digest(), "engines_by_backend": {"container": ["synthetic.signal"]},
            "profiles_by_backend": {"container": ["isolated"]}, "runtime_by_backend": {"container": {"os": "Linux", "python": "3.12"}},
            "resource_limits_by_backend": {"container": {"cpu": 2, "memory_bytes": 512*1024**2, "pids": 64}}}
    with factory.begin() as db:
        enrolled = svc.enroll(db, project, "container-qualification")
        target = svc.authenticate_target(db, enrolled["id"], enrolled["token"])
        target.capabilities = caps
        saved = svc.save_definition(db, project, definition(execution={"backend": "container", "profile": "isolated", "requirements": {"cpu": 1}}))
        submitted = svc.submit(db, project, saved["id"], 1, target.id, "resource-change")
        run_id = submitted.id
    narrowed = copy.deepcopy(caps)
    narrowed["resource_limits_by_backend"]["container"]["cpu"] = .5
    with factory.begin() as db:
        target = svc.authenticate_target(db, enrolled["id"], enrolled["token"])
        assert svc.claim(db, target, narrowed) is None
        run = svc.get_run(db, project, run_id)
        assert run.state == "queued"
        assert "allowance" in run.error
        assert db.scalar(select(LabAttempt.id).where(LabAttempt.run_id == run_id)) is None
