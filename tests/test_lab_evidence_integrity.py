"""Evidence integrity and retention using temporary SQLite and controlled storage.

No PostgreSQL, Redis, Docker, executor or service is started by these checks.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import sessionmaker

from tinystudio.db.models import Base, Project, User
from tinystudio.lab import portable, service as svc
from tinystudio.lab.contracts import ExperimentDefinition, checksum, canonical
from tinystudio.lab.models import LabArtifact, LabAttempt, LabDefinition, LabDispatch, LabEvent, LabRevision, LabRun, LabTarget
from tinystudio.storage import ArtifactStorage, ArtifactNotFound


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///"+str(tmp_path / "metadata.sqlite"))
    Base.metadata.create_all(engine, tables=[User.__table__, Project.__table__, LabDefinition.__table__, LabRevision.__table__,
        LabTarget.__table__, LabRun.__table__, LabAttempt.__table__, LabEvent.__table__, LabArtifact.__table__, LabDispatch.__table__])
    factory = sessionmaker(engine, expire_on_commit=False)
    store = ArtifactStorage(root=tmp_path / "objects")
    monkeypatch.setattr(svc, "storage", lambda: store)
    with factory.begin() as db:
        db.add_all([Project(id="p", slug="project", name="Project"), Project(id="q", slug="other", name="Other")])
        target = LabTarget(id="target", project_id="p", name="Target", enabled=True)
        db.add(target)
        source = tmp_path / "source.jsonl"
        source.write_bytes(b'{"original":1}\n')
        artifact = svc.store_artifact(db, "p", source, "source.jsonl", "application/jsonl")
        definition = ExperimentDefinition(name="Original", engine="synthetic.signal", inputs=[{"artifact_id": artifact.id,
            "sha256": artifact.sha256}], execution={"profile": "trusted_native"}).model_dump()
        saved = svc.save_definition(db, "p", definition)
        spec = {"schema_version": 1, "definition": definition, "definition_id": saved["id"], "revision": 1,
            "implementation": {"version": 1, "files": {"fixture.py": "a"*64}}, "input_manifest": [svc.artifact_dict(artifact)],
            "runtime": {"python": "fixture"}, "research_identity": "fixture"}
        run = LabRun(id="run", project_id="p", definition_id=saved["id"], revision=1, target_id="target",
            idempotency_key="fixture", spec=spec, spec_digest=checksum(spec), state="running")
        attempt = LabAttempt(id="attempt", run_id="run", target_id="target", number=1, token_hash=svc.secret_hash("lease"),
            lease_until=svc.now()+timedelta(seconds=60), state="running", runtime={})
        db.add_all([run, attempt])
        db.flush()
        source_id = artifact.id
    yield factory, store, tmp_path, source_id
    engine.dispose()


def output(db, root, *, name="partial.jsonl", attempt="attempt", project="p", content=b'{"partial":1}\n'):
    path = root / (name.replace("/", "-")+".upload")
    path.write_bytes(content)
    return svc.store_artifact(db, project, path, name, "application/jsonl", "evidence", attempt)


def test_acknowledged_corrupt_upload_never_publishes_artifact(fixture, monkeypatch):
    factory, store, root, _ = fixture
    upload = store.upload_file
    def corrupt(path, key, media_type=None):
        upload(path, key, media_type)
        store._path(key).write_bytes(b"corrupted ACK")
        return key
    monkeypatch.setattr(store, "upload_file", corrupt)
    with factory.begin() as db:
        before = db.scalar(select(func.count()).select_from(LabArtifact))
        with pytest.raises(svc.LabError, match="checksum mismatch"):
            output(db, root)
        assert db.scalar(select(func.count()).select_from(LabArtifact)) == before


@pytest.mark.parametrize("fault", ["corrupt", "deleted"])
def test_completion_rechecks_persisted_outputs_before_success(fixture, fault):
    factory, store, root, _ = fixture
    with factory.begin() as db:
        row = output(db, root)
        result = {"artifacts": [svc.artifact_dict(row)]}
        path = store._path(row.storage_key)
        if fault == "deleted":
            path.unlink()
        else:
            path.write_bytes(b"bad")
        with pytest.raises((svc.LabError, ArtifactNotFound)):
            svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", "succeeded", result, None)
        assert db.get(LabRun, "run").state == "running"
        assert db.get(LabAttempt, "attempt").result_digest is None


def test_materialize_rejects_size_mismatch_and_removes_local_partial(fixture):
    factory, _, root, source_id = fixture
    download = root / "read"
    download.mkdir()
    with factory.begin() as db:
        row = svc.get_artifact(db, "p", source_id)
        row.size += 1
        with pytest.raises(svc.LabError, match="size"):
            svc.materialize(row, download)
        assert not (download / row.sha256).exists()


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_partial_completion_canonicalizes_all_states_and_duplicate_is_idempotent(fixture, monkeypatch, state):
    factory, store, root, _ = fixture
    with factory.begin() as db:
        row = output(db, root)
        request = {"metric": 2, "artifacts": [{"id": row.id, "sha256": row.sha256,
            "name": "forged-name", "size": -1, "media_type": "forged"}]}
        assert svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", state, request, "diagnostic")["accepted"]
        assert db.get(LabRun, "run").result["artifacts"] == [svc.artifact_dict(row)]
        refs = svc.outcome_references(db, "run")
        assert len(refs) == 1
        retained = svc.get_artifact(db, "p", refs[0]["outcome_artifact_id"])
        document = json.loads(svc.materialize(retained, root).read_bytes())
        assert document["result"] == db.get(LabRun, "run").result
        assert document["error"] == "diagnostic" and document["attempt_id"] == "attempt"
        monkeypatch.setattr(store, "download_file", lambda *a: pytest.fail("duplicate completion retransferred evidence"))
        assert svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", state, request, "diagnostic") == {"accepted": True, "state": state}


@pytest.mark.parametrize("state", ["failed", "cancelled"])
@pytest.mark.parametrize("fault", ["duplicate", "other_attempt", "foreign"])
def test_partial_completion_rejects_duplicate_or_foreign_evidence(fixture, state, fault):
    factory, _, root, _ = fixture
    with factory.begin() as db:
        if fault == "other_attempt":
            db.add(LabAttempt(id="other-attempt", run_id="run", target_id="target", number=2,
                token_hash="hash", lease_until=svc.now(), state="failed", runtime={}))
        row = output(db, root, project="q" if fault == "foreign" else "p",
                     attempt="other-attempt" if fault == "other_attempt" else "attempt")
        refs = [svc.artifact_dict(row)] * (2 if fault == "duplicate" else 1)
        with pytest.raises(svc.LabError, match="duplicate|outside this attempt|artifact not found"):
            svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", state, {"artifacts": refs}, "diagnostic")
        assert db.get(LabRun, "run").state == "running"


def test_slow_storage_verification_precedes_authority_lock_and_stale_recheck(fixture, monkeypatch):
    factory, store, root, _ = fixture
    with factory.begin() as db:
        row = output(db, root)
        original_download = store.download_file
        original_authority = svc.attempt_authority
        clock = [svc.now()]
        verified = []
        def download(*args):
            assert not verified or verified[-1] != "locked"
            verified.append("download")
            clock[0] += timedelta(seconds=61)
            return original_download(*args)
        def authority(*args, **kwargs):
            assert "download" in verified
            verified.append("locked")
            return original_authority(*args, **kwargs)
        monkeypatch.setattr(store, "download_file", download)
        monkeypatch.setattr(svc, "attempt_authority", authority)
        monkeypatch.setattr(svc, "now", lambda: clock[0])
        with pytest.raises(svc.LabError, match="lease expired"):
            svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", "succeeded", {"artifacts": [svc.artifact_dict(row)]}, None)
        assert verified[-1] == "locked"
        assert db.get(LabRun, "run").state == "running"
        assert svc.outcome_references(db, "run") == []


def test_runner_cannot_forge_server_outcome_events(fixture):
    factory, _, _, _ = fixture
    with factory.begin() as db:
        lease = db.get(LabAttempt, "attempt").lease_until
        sequence = db.get(LabRun, "run").next_seq
        with pytest.raises(svc.LabError, match="reserved server"):
            svc.heartbeat(db, db.get(LabTarget, "target"), "attempt", "lease", [{"kind": "server.outcome", "payload": {"outcome_artifact_id": "forged"}}])
        assert db.get(LabAttempt, "attempt").lease_until == lease
        assert db.get(LabRun, "run").next_seq == sequence
        assert svc.outcome_references(db, "run") == []


def test_original_input_failed_partial_retry_bundle_import_retains_attribution(fixture):
    factory, _, root, source_id = fixture
    with factory.begin() as db:
        run = db.get(LabRun, "run")
        spec_digest = run.spec_digest
        (root / "source.jsonl").write_bytes(b'{"edited":2}\n')
        edited = svc.store_artifact(db, "p", root / "source.jsonl", "edited.jsonl", "application/jsonl")
        draft = dict(run.spec["definition"])
        draft["inputs"] = [{"artifact_id": edited.id, "sha256": edited.sha256}]
        svc.save_definition(db, "p", draft, id_=run.definition_id, expected=1)
        partial = output(db, root)
        partial_id = partial.id
        svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", "failed",
                   {"records": 1, "artifacts": [svc.artifact_dict(partial)]}, "first attempt diagnostic")
    with factory.begin() as db:
        retried = svc.retry(db, "p", "run")
        assert retried.result is None and retried.error is None and retried.spec_digest == spec_digest
        # This retention test supplies the post-claim boundary directly; it does
        # not claim to exercise the scheduler, lease claim or executor.
        db.execute(delete(LabDispatch).where(LabDispatch.run_id == "run"))
        retried.state = "running"
        db.add(LabAttempt(id="attempt-two", run_id="run", target_id="target", number=2, token_hash=svc.secret_hash("second"),
            lease_until=svc.now()+timedelta(seconds=60), state="running", runtime={}))
        second = output(db, root, name="second.jsonl", attempt="attempt-two", content=b'{"second":1}\n')
        svc.finish(db, db.get(LabTarget, "target"), "attempt-two", "second", "failed", {"artifacts": [svc.artifact_dict(second)]}, "second diagnostic")
    exported = root / "exported"
    exported.mkdir()
    with factory.begin() as db:
        bundle = portable.export_bundle(db, "p", "run", exported)
    with ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["run"]["spec_digest"] == spec_digest
        assert manifest["run"]["spec"]["definition"]["inputs"][0]["artifact_id"] == source_id
        assert manifest["artifact_origins"][partial_id] == {"kind": "attempt_output", "attempt_id": "attempt"}
        first = next(item for item in manifest["attempt_outcomes"] if item["attempt_id"] == "attempt")
        document = json.loads(archive.read("objects/"+first["sha256"]))
        assert document["error"] == "first attempt diagnostic"
        assert document["result"]["records"] == 1
        assert document["result"]["artifacts"][0]["id"] == partial_id
        source = next(item for item in manifest["artifacts"] if item["id"] == source_id)
        assert archive.read("objects/"+source["sha256"]) == b'{"original":1}\n'
    imported = root / "imported"
    imported.mkdir()
    with factory.begin() as db:
        before = db.scalar(select(func.count()).select_from(LabRun))
        result = portable.import_bundle(db, "q", bundle, imported)
        assert result["execution_started"] is False and result["provenance"]["attempt_attribution"] == "recorded"
        assert db.scalar(select(func.count()).select_from(LabRun)) == before
        provenance = svc.get_artifact(db, "q", result["historical_manifest"]["id"])
        assert json.loads(svc.materialize(provenance, imported).read_bytes()) == manifest
        restored = svc.get_artifact(db, "q", result["artifact_mapping"][source_id])
        assert svc.materialize(restored, imported).read_bytes() == b'{"original":1}\n'


def _rewrite(bundle, destination, edit):
    with ZipFile(bundle) as source:
        manifest = json.loads(source.read("manifest.json"))
        edit(manifest)
        with ZipFile(destination, "w") as output:
            for name in source.namelist():
                if name not in {"manifest.json", "manifest.sha256"}:
                    output.writestr(name, source.read(name))
            output.writestr("manifest.json", canonical(manifest))
            output.writestr("manifest.sha256", checksum(manifest))
    return destination


def _bundle(fixture):
    factory, _, root, _ = fixture
    folder = root / "export"
    folder.mkdir()
    with factory.begin() as db:
        row = output(db, root)
        svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", "failed", {"artifacts": [svc.artifact_dict(row)]}, "diagnostic")
        return portable.export_bundle(db, "p", "run", folder)


def test_duplicate_hash_export_validates_every_recorded_size(fixture):
    factory, _, root, _ = fixture
    with factory.begin() as db:
        first = output(db, root)
        second = output(db, root, name="same-bytes.jsonl")
        assert first.sha256 == second.sha256
        second.size += 1
        db.get(LabRun, "run").state = "failed"
        db.get(LabAttempt, "attempt").state = "failed"
        folder = root / "export"
        folder.mkdir()
        with pytest.raises(svc.LabError, match="size"):
            portable.export_bundle(db, "p", "run", folder)


@pytest.mark.parametrize("tamper", ["origin", "outcome", "duplicate_size"])
def test_import_rejects_inconsistent_attribution_or_shared_hash_metadata(fixture, tamper):
    factory, _, root, source_id = fixture
    bundle = _bundle(fixture)
    def edit(manifest):
        if tamper == "origin":
            manifest["artifact_origins"][source_id]["attempt_id"] = "forged"
        elif tamper == "outcome":
            manifest["attempt_outcomes"][0]["attempt_id"] = "forged"
        else:
            item = dict(manifest["artifacts"][0], id="duplicate-size", size=99999)
            manifest["artifacts"].append(item)
            manifest["artifact_origins"]["duplicate-size"] = {"kind": "attempt_output", "attempt_id": "attempt"}
    forged = _rewrite(bundle, root / "forged.zip", edit)
    folder = root / "import"
    folder.mkdir()
    with factory.begin() as db:
        with pytest.raises(svc.LabError, match="origin|association|size"):
            portable.import_bundle(db, "q", forged, folder)
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == "q")) == 0


def test_legacy_schema_one_accepts_evidence_with_explicit_unknown_attribution(fixture):
    factory, _, root, _ = fixture
    bundle = _bundle(fixture)
    legacy = _rewrite(bundle, root / "legacy.zip", lambda manifest: (manifest.pop("artifact_origins"), manifest.pop("attempt_outcomes")))
    folder = root / "legacy-import"
    folder.mkdir()
    with factory.begin() as db:
        result = portable.import_bundle(db, "q", legacy, folder)
        assert result["provenance"]["attempt_attribution"] == "unknown_legacy_bundle"
        assert result["execution_started"] is False


def test_identical_upload_preserves_existing_valid_cas_bytes(fixture, monkeypatch):
    factory, store, root, _ = fixture
    with factory.begin() as db:
        original = output(db, root)
        def corrupt_if_called(*args):
            pytest.fail("valid retained content must not be overwritten")
        monkeypatch.setattr(store, "upload_file", corrupt_if_called)
        alias = output(db, root, name="alias.jsonl")
        assert alias.sha256 == original.sha256
        assert svc.materialize(original, root).read_bytes() == b'{"partial":1}\n'


@pytest.mark.parametrize("digest_bytes", [b"short", b"x"*65])
def test_import_bounds_digest_member_before_read(fixture, digest_bytes):
    factory, _, root, _ = fixture
    bundle = _bundle(fixture)
    malformed = root / "digest.zip"
    with ZipFile(bundle) as original, ZipFile(malformed, "w") as target:
        for name in original.namelist():
            target.writestr(name, digest_bytes if name == "manifest.sha256" else original.read(name))
    folder = root / "digest-import"
    folder.mkdir()
    with factory.begin() as db:
        with pytest.raises(svc.LabError, match="exactly 64 bytes"):
            portable.import_bundle(db, "q", malformed, folder)
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == "q")) == 0


def test_import_bounds_artifact_metadata_count(fixture):
    factory, _, root, _ = fixture
    bundle = _bundle(fixture)
    malformed = _rewrite(bundle, root / "count.zip", lambda manifest: manifest.update(artifacts=[{}]*9999))
    folder = root / "count-import"
    folder.mkdir()
    with factory.begin() as db:
        with pytest.raises(svc.LabError, match="artifact count"):
            portable.import_bundle(db, "q", malformed, folder)


def test_cancel_committed_during_download_is_refreshed_before_success(fixture, monkeypatch):
    factory, store, root, _ = fixture
    with factory.begin() as db:
        row = output(db, root)
        result = {"artifacts": [svc.artifact_dict(row)]}
    download = store.download_file
    cancelled = []
    def cancel_during_verification(*args):
        if not cancelled:
            with factory.begin() as independent:
                svc.cancel(independent, "p", "run")
            cancelled.append(True)
        return download(*args)
    monkeypatch.setattr(store, "download_file", cancel_during_verification)
    with factory.begin() as db:
        stale = svc.get_run(db, "p", "run")
        assert stale.cancel_requested is False
        with pytest.raises(svc.LabError, match="cancellation requested"):
            svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", "succeeded", result, None)
        assert stale.cancel_requested is True
        assert stale.state == "running" and stale.result is None
        assert svc.outcome_references(db, "run") == []
    with factory() as db:
        assert db.get(LabRun, "run").cancel_requested is True
        assert db.get(LabAttempt, "attempt").state == "running"


def test_retained_outcome_bound_accounts_for_unicode_diagnostic_bytes(fixture):
    factory, _, root, _ = fixture
    diagnostic = "測"*8192
    with factory.begin() as db:
        row = output(db, root)
        result = {"notes": "x"*(1024**2-1000), "artifacts": [svc.artifact_dict(row)]}
        assert len(canonical(result)) <= 1024**2
        svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", "failed", result, diagnostic)
        folder = root / "unicode-export"
        folder.mkdir()
        bundle = portable.export_bundle(db, "p", "run", folder)
    with ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        association = manifest["attempt_outcomes"][0]
        retained = json.loads(archive.read("objects/"+association["sha256"]))
        assert retained["error"] == diagnostic
        assert retained["result"] == result


@pytest.mark.parametrize("prior_state", ["failed", "cancelled"])
def test_queued_cancellation_between_retries_has_no_previous_attempt_attribution(fixture, prior_state):
    factory, _, root, _ = fixture
    with factory.begin() as db:
        partial = output(db, root)
        svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", prior_state,
                   {"artifacts": [svc.artifact_dict(partial)]}, "first execution diagnostic")
    with factory.begin() as db:
        svc.retry(db, "p", "run")
        assert db.get(LabAttempt, "attempt").state == prior_state
    with factory.begin() as db:
        assert svc.cancel(db, "p", "run").state == "cancelled"
    with factory.begin() as db:
        svc.retry(db, "p", "run")
        cancelled = [ref for ref in svc.outcome_references(db, "run") if ref["state"] == "cancelled"]
        assert len(cancelled) == (2 if prior_state == "cancelled" else 1)
        assert cancelled[-1]["attempt_id"] is None
        assert db.get(LabAttempt, "attempt").state == prior_state
        assert db.scalar(select(func.count()).select_from(LabAttempt)) == 1
        db.execute(delete(LabDispatch).where(LabDispatch.run_id == "run"))
        db.get(LabRun, "run").state = "running"
        db.add(LabAttempt(id="new-attempt", run_id="run", target_id="target", number=2,
            token_hash=svc.secret_hash("new-lease"), lease_until=svc.now()+timedelta(seconds=60), state="running", runtime={}))
        svc.finish(db, db.get(LabTarget, "target"), "new-attempt", "new-lease", "failed", None, "new execution")
    folder = root / "cancelled-cycle-export"
    folder.mkdir()
    with factory.begin() as db:
        bundle = portable.export_bundle(db, "p", "run", folder)
    with ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        cancellation = next(item for item in manifest["attempt_outcomes"] if item["state"] == "cancelled" and item["attempt_id"] is None)
        assert cancellation["attempt_id"] is None
        assert manifest["artifact_origins"][cancellation["artifact_id"]] == {"kind": "attempt_outcome", "attempt_id": None}
        assert next(item for item in manifest["run"]["attempts"] if item["id"] == "attempt")["state"] == prior_state
    restored = root / "cancelled-cycle-import"
    restored.mkdir()
    with factory.begin() as db:
        imported = portable.import_bundle(db, "q", bundle, restored)
        assert imported["execution_started"] is False
        assert imported["provenance"]["attempt_attribution"] == "recorded"


def test_canonical_result_expansion_cannot_exceed_retained_result_limit(fixture):
    factory, _, _, source_id = fixture
    with factory.begin() as db:
        source = svc.get_artifact(db, "p", source_id)
        artifacts = []
        for index in range(200):
            row = LabArtifact(project_id="p", attempt_id="attempt", name=f"out-{index:03d}-"+"n"*240,
                sha256=source.sha256, size=source.size, storage_key=source.storage_key,
                media_type="application/"+"m"*100, role="evidence-"+"r"*55, visibility="model")
            db.add(row)
            artifacts.append(row)
        db.flush()
        incoming = {"notes": "x"*(1024**2-40000),
                    "artifacts": [{"id": row.id, "sha256": row.sha256} for row in artifacts]}
        expanded = {**incoming, "artifacts": [svc.artifact_dict(row) for row in artifacts]}
        assert len(canonical(incoming)) < 1024**2 < len(canonical(expanded))
        with pytest.raises(svc.LabError, match="canonical result exceeds 1 MiB"):
            svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", "failed", incoming, "bounded completion")
        assert db.get(LabRun, "run").state == "running"
        assert svc.outcome_references(db, "run") == []


def test_retry_rejects_new_attempt_aba_with_identical_terminal_payload(fixture, monkeypatch):
    factory, store, _, _ = fixture
    diagnostic = "identical measured failure"
    with factory.begin() as db:
        svc.finish(db, db.get(LabTarget, "target"), "attempt", "lease", "failed", None, diagnostic)
    download = store.download_file
    intervened = []
    def complete_next_attempt_during_archive_readback(*args):
        if not intervened:
            intervened.append(True)
            with factory.begin() as independent:
                svc.retry(independent, "p", "run")
                independent.execute(delete(LabDispatch).where(LabDispatch.run_id == "run"))
                independent.get(LabRun, "run").state = "running"
                independent.add(LabAttempt(id="intervening-attempt", run_id="run", target_id="target", number=2,
                    token_hash=svc.secret_hash("intervening-lease"), lease_until=svc.now()+timedelta(seconds=60),
                    state="running", runtime={}))
                independent.flush()
                svc.finish(independent, independent.get(LabTarget, "target"), "intervening-attempt", "intervening-lease",
                           "failed", None, diagnostic)
        return download(*args)
    monkeypatch.setattr(store, "download_file", complete_next_attempt_during_archive_readback)
    with factory.begin() as db:
        with pytest.raises(svc.LabError, match="outcome changed during retry preparation"):
            svc.retry(db, "p", "run")
    assert intervened == [True]
    with factory() as db:
        assert db.get(LabRun, "run").state == "failed"
        assert db.get(LabRun, "run").error == diagnostic
        assert db.get(LabAttempt, "intervening-attempt").state == "failed"
        assert any(ref["attempt_id"] == "intervening-attempt" for ref in svc.outcome_references(db, "run"))
        assert db.get(LabDispatch, "run") is None
