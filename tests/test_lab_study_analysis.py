"""Study evidence bridges in a newly owned UUID PostgreSQL database.

Run with --noconftest. Synthetic retained reports exercise association checks;
they are not scientific engine execution receipts.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import func, select

from tests.test_lab_foundation import database
from tests.test_lab_studies import protocol, record
from tinystudio.db.models import Project, User
from tinystudio.lab import operations as ops, service as svc, studies, study_analysis as analysis
from tinystudio.lab.analysis_selection import validate_snapshot
from tinystudio.lab.contracts import canonical, checksum
from tinystudio.lab.models import LabArtifact, LabAttempt, LabOperation, LabRun, LabStudyRecord, LabStudyRevision
from tinystudio.storage import ArtifactStorage


@pytest.fixture
def evidence(database, tmp_path, monkeypatch):
    store = ArtifactStorage(root=tmp_path / "objects")
    monkeypatch.setattr(svc, "storage", lambda: store)
    with database.begin() as db:
        user = User(email=uuid.uuid4().hex + "@analysis.invalid", password_hash="unused")
        project = Project(slug=uuid.uuid4().hex, name="Study analysis")
        other = Project(slug=uuid.uuid4().hex, name="Other project")
        db.add_all([user, project, other]); db.flush()
        study = studies.save_study(db, project.id, protocol(), user.id, key="study")
        for index in range(7):
            studies.append_record(db, project.id, study["id"], record(key=str(index), unit_id="device-" + str(index),
                condition="baseline" if index < 3 else "intervention", data={"temperature": index + .25}), user.id)
        fixture = SimpleNamespace(factory=database, project=project.id, other=other.id, author=user.id,
                                  study=study["id"], root=tmp_path, store=store)
    return fixture


def parameters(**patch):
    return {"method": "means.welch", "confidence": .95, "alternative": "two-sided",
        "selection": {"mode": "all"}, "outcome_path": "/data/temperature", "condition_path": "/condition",
        "unit_path": "/unit_id", "measurement_unit": "degC", "groups": ["baseline", "intervention"],
        "repeated": "error", "missing": "error", **patch}


def admit(fixture, key="capture", **boundary):
    with fixture.factory.begin() as db:
        return analysis.request_input(db, fixture.project, fixture.study, key, **boundary)


def capture(fixture, **boundary):
    operation = admit(fixture, **boundary)
    with fixture.factory.begin() as db:
        return analysis.execute_capture(db, fixture.project, operation.payload, fixture.root / uuid.uuid4().hex)


def definition_body(artifact_id, **patch):
    return {"name": "Retained observations", "snapshot_artifact_id": artifact_id, "parameters": parameters(),
            "execution": {"profile": "trusted_native", "max_seconds": 37}, **patch}


def retained_report(fixture, *, source_patch=None, report_patch=None, state="succeeded", engine="statistics.analyze", altered_observations=False):
    """Build server-retained metadata for association tests, without claiming execution."""
    output = capture(fixture)
    with fixture.factory.begin() as db:
        if altered_observations:
            artifact = svc.get_artifact(db, fixture.project, output["artifact"]["id"])
            path = svc.materialize(artifact, fixture.root / "altered-source")
            documents = [json.loads(line) for line in path.read_bytes().splitlines()]
            observation = next(row["record"] for row in documents if row["kind"] == "record")
            observation["data"]["temperature"] = -999
            observation["digest"] = checksum({key: value for key, value in observation.items() if key != "digest"})
            prefix = b"".join(canonical(row)+b"\n" for row in documents[:-1])
            documents[-1]["sha256"] = hashlib.sha256(prefix).hexdigest()
            path.write_bytes(prefix+canonical(documents[-1])+b"\n")
            # Valid portable input, deliberately different from the study's history.
            assert validate_snapshot(path)["through_seq"] == 7
            replacement = svc.store_artifact(db, fixture.project, path, "altered.jsonl", "application/x-ndjson", "input", visibility="evaluation")
            output["artifact"] = svc.artifact_dict(replacement)
        saved = analysis.save_analysis_definition(db, fixture.project, definition_body(output["artifact"]["id"]))
        definition = deepcopy(saved["definition"])
        definition["engine"] = engine
        target = svc.resolve_target(db, fixture.project, "local")
        spec = {"definition": definition}
        run = LabRun(project_id=fixture.project, definition_id=saved["id"], revision=1, target_id=target.id,
            idempotency_key=uuid.uuid4().hex, spec=spec, spec_digest=checksum(spec), state=state)
        db.add(run); db.flush()
        attempt = LabAttempt(run_id=run.id, target_id=target.id, number=1, token_hash="not-a-credential",
            lease_until=svc.now()+timedelta(minutes=1), state="succeeded", runtime={"fixture": "association-only"})
        db.add(attempt); db.flush()
        source = {**output["source"], "sha256": output["artifact"]["sha256"], **(source_patch or {})}
        report = {"format": analysis.ANALYSIS_FORMAT, "source": source, "request": definition["parameters"],
            "method": {"id": "means.welch", "version": "1", "label": "Welch"},
            "selection": {"counts": {"source_records": 7, "selected_records": 7, "used_records": 7,
                                      "group_a": 3, "group_b": 4}},
            "result": {"custom_output": {"large": "x"*100_000}},
            "qualification": {"status": "association_fixture"}, **(report_patch or {})}
        path = fixture.root / (uuid.uuid4().hex + ".json")
        path.write_bytes(canonical(report))
        artifact = svc.store_artifact(db, fixture.project, path, "statistical-analysis.json", "application/json",
            "statistical_analysis", attempt_id=attempt.id, visibility="evaluation")
        run.result = {"artifacts": [svc.artifact_dict(artifact)]}
        return SimpleNamespace(run=run.id, artifact=artifact.id, snapshot=output["artifact"]["id"],
                               source=source, report=report, attempt=attempt.id)


def test_result_cannot_associate_rehashed_altered_observations_with_retained_study(evidence):
    f = evidence
    retained = retained_report(f, altered_observations=True)
    with f.factory.begin() as db:
        with pytest.raises(svc.LabError, match="retained study history|observations"):
            analysis.record_result(db, f.project, f.study, retained.run, f.author, "altered-result")
        assert studies.get_study(db, f.project, f.study)["last_sequence"] == 7


def test_frozen_capture_replays_default_boundary_after_append_and_protocol_revision(evidence):
    f = evidence
    original = admit(f)
    with f.factory.begin() as db:
        revision = studies.save_study(db, f.project, protocol(name="Later protocol"), f.author, id_=f.study, expected=1)
        studies.append_record(db, f.project, f.study, record(revision=2, key="later"), f.author)
        replayed = analysis.request_input(db, f.project, f.study, "capture")
        assert replayed.id == original.id and replayed.payload == original.payload
        assert original.payload["source"]["revision"] == 1 and original.payload["source"]["through_seq"] == 7
        result = analysis.execute_capture(db, f.project, original.payload, f.root / "frozen")
        artifact = svc.get_artifact(db, f.project, result["artifact"]["id"])
        path = svc.materialize(artifact, f.root)
        source = validate_snapshot(path)
        assert source["revision"] == 1 and source["through_seq"] == 7 and source["protocol_digest"] != revision["digest"]
        assert source["sha256"] == artifact.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert artifact.role == "study_snapshot" and artifact.visibility == "evaluation"
        assert result["source"] == original.payload["source"]
        assert len([line for line in path.read_text().splitlines() if json.loads(line)["kind"] == "record"]) == 7


def test_capture_intent_collision_scope_and_invalid_boundaries(evidence):
    f = evidence
    original = admit(f)
    with f.factory.begin() as db:
        with pytest.raises(svc.LabError, match="different operation"):
            analysis.request_input(db, f.project, f.study, "capture", revision=1, through_seq=7)
        with pytest.raises(svc.LabError, match="study not found"):
            analysis.request_input(db, f.other, f.study, "capture")
        with pytest.raises(svc.LabError, match="study not found"):
            analysis.execute_capture(db, f.other, original.payload, f.root / "denied")
        for boundary in ({"revision": True}, {"through_seq": -1}, {"through_seq": 8}):
            with pytest.raises(svc.LabError, match="boundary"):
                analysis.request_input(db, f.project, f.study, uuid.uuid4().hex, **boundary)


@pytest.mark.parametrize("damage", ["protocol", "record"])
def test_capture_rejects_corrupted_retained_history(evidence, damage):
    f = evidence
    payload = admit(f).payload
    with f.factory.begin() as db:
        if damage == "protocol":
            row = db.get(LabStudyRevision, (f.study, 1))
            row.snapshot = {**row.snapshot, "author_id": "changed"}
        else:
            row = db.scalar(select(LabStudyRecord).where(LabStudyRecord.study_id == f.study))
            row.record = {**row.record, "data": {"tampered": True}}
    with pytest.raises(svc.LabError, match="checksum"):
        with f.factory.begin() as db:
            analysis.execute_capture(db, f.project, payload, f.root / "corrupt")
    with f.factory() as db:
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == f.project)) == 0


def test_capture_checks_persisted_full_bytes_even_if_materializer_returns_wrong_same_size(evidence, monkeypatch):
    f = evidence
    payload = admit(f).payload
    materialize = svc.materialize
    def corrupt(artifact, directory):
        path = materialize(artifact, directory)
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        return path
    monkeypatch.setattr(svc, "materialize", corrupt)
    with pytest.raises(svc.LabError, match="persisted study snapshot"):
        with f.factory.begin() as db:
            analysis.execute_capture(db, f.project, payload, f.root / "corrupt-readback")
    with f.factory() as db:
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == f.project)) == 0


def test_capture_cancellation_checks_during_record_serialization_and_rolls_back(evidence):
    f = evidence
    payload, checks = admit(f).payload, []
    def authority():
        checks.append(True)
        if len(checks) == 6:
            raise svc.LabError("cancelled test authority", 409)
    with pytest.raises(svc.LabError, match="cancelled"):
        with f.factory.begin() as db:
            analysis.execute_capture(db, f.project, payload, f.root / "cancelled", authority)
    assert len(checks) == 6
    assert not list((f.root / "cancelled").glob("*.jsonl"))


def test_operation_retry_preserves_admitted_history_after_readback_failure(evidence, monkeypatch):
    f = evidence
    operation = admit(f)
    materialize = svc.materialize
    def unavailable(*args):
        raise RuntimeError("controlled persisted readback failure")
    monkeypatch.setattr(svc, "materialize", unavailable)
    assert ops.run_operation(operation.id, f.factory) is None
    with f.factory.begin() as db:
        failed = ops.get_operation(db, f.project, operation.id)
        assert failed.state == "failed" and "readback failure" in failed.error
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == f.project)) == 0
        studies.save_study(db, f.project, protocol(name="Appended later"), f.author, id_=f.study, expected=1)
        studies.append_record(db, f.project, f.study, record(revision=2, key="later"), f.author)
        ops.retry_operation(db, f.project, operation.id)
    monkeypatch.setattr(svc, "materialize", materialize)
    output = ops.run_operation(operation.id, f.factory)
    assert output["state"] == "succeeded" and output["result"]["source"]["revision"] == 1
    assert output["result"]["source"]["through_seq"] == 7


@pytest.mark.parametrize("boundary", ["storage", "readback"])
def test_operation_cancellation_cannot_publish_snapshot_and_can_retry(evidence, monkeypatch, boundary):
    f = evidence
    operation = admit(f)
    method = "store_artifact" if boundary == "storage" else "materialize"
    original = getattr(svc, method)
    cancelled = []
    def cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        if not cancelled:
            with f.factory.begin() as db:
                ops.cancel_operation(db, f.project, operation.id)
            cancelled.append(True)
        return result
    monkeypatch.setattr(svc, method, cancel)
    assert ops.run_operation(operation.id, f.factory) is None
    with f.factory.begin() as db:
        row = ops.get_operation(db, f.project, operation.id)
        assert row.state == "cancelled" and row.result is None
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == f.project)) == 0
        ops.retry_operation(db, f.project, operation.id)
    monkeypatch.setattr(svc, method, original)
    assert ops.run_operation(operation.id, f.factory)["state"] == "succeeded"


@pytest.mark.parametrize("visibility", ["model", "evaluation", "truth"])
def test_definition_binds_valid_uploaded_snapshot_actual_sha_and_visibility(evidence, visibility):
    f = evidence
    with f.factory.begin() as db:
        path = studies.export_tracking(db, f.project, f.study, f.root / "uploaded")
        artifact = svc.store_artifact(db, f.project, path, "uploaded.jsonl", "application/x-ndjson", "input", visibility=visibility)
        body = definition_body(artifact.id)
        before = deepcopy(body)
        saved = analysis.save_analysis_definition(db, f.project, body)
        assert body == before
        definition = saved["definition"]
        assert definition["inputs"] == [{"artifact_id": artifact.id, "sha256": artifact.sha256,
            "role": "study_snapshot", "visibility": visibility}]
        assert definition["engine"] == "statistics.analyze" and definition["operating_mode"] == "replay"
        assert definition["execution"]["max_seconds"] == 37 and definition["execution"]["grants"] == {}
        assert definition["parameters"]["selection"]["exclude_ids"] == []
        with pytest.raises(svc.LabError, match="artifact not found"):
            analysis.save_analysis_definition(db, f.other, body)


def test_custom_definition_binds_code_and_auxiliary_without_visibility_or_host_grants(evidence):
    f = evidence
    snapshot = capture(f)["artifact"]
    custom = {"method": "custom.python", "selection": {"mode": "all"},
              "custom_method": {"name": "Researcher supplied", "version": "0.1"}, "options": {"grouping": "authored"}}
    with f.factory.begin() as db:
        path = f.root / "custom.py"; path.write_text("print('fixture')")
        code = svc.store_artifact(db, f.project, path, "method.py", "text/x-python", "input", visibility="evaluation")
        extra = svc.store_artifact(db, f.project, path, "support.txt", "text/plain", "truth", visibility="truth")
        body = definition_body(snapshot["id"], parameters=custom, custom_code_artifact_id=code.id,
                               extra_artifact_ids=[extra.id])
        saved = analysis.save_analysis_definition(db, f.project, body)
        assert [(ref["role"], ref["visibility"]) for ref in saved["definition"]["inputs"]] == [
            ("study_snapshot", "evaluation"), ("analysis_code", "evaluation"), ("analysis_auxiliary", "truth")]
        assert saved["definition"]["execution"]["grants"] == {}
        with pytest.raises(svc.LabError, match="only to custom"):
            analysis.save_analysis_definition(db, f.project, {**body, "parameters": parameters()})
        with pytest.raises(svc.LabError, match="requires a custom_code"):
            analysis.save_analysis_definition(db, f.project, definition_body(snapshot["id"], parameters=custom))
        with pytest.raises(svc.LabError, match="distinct"):
            analysis.save_analysis_definition(db, f.project, {**body, "extra_artifact_ids": [snapshot["id"]]})
        with pytest.raises(svc.LabError, match="scoped artifact"):
            analysis.save_analysis_definition(db, f.project, {**body, "execution": {"profile": "trusted_native", "grants": {"input_paths": ["/host"]}}})


def test_definition_rejects_invalid_snapshot_even_when_artifact_role_claims_snapshot(evidence):
    f = evidence
    with f.factory.begin() as db:
        path = f.root / "bad.jsonl"; path.write_text('{"kind":"manifest"}\n')
        artifact = svc.store_artifact(db, f.project, path, path.name, "application/x-ndjson", "study_snapshot")
        with pytest.raises(svc.LabError, match="invalid study tracking snapshot"):
            analysis.save_analysis_definition(db, f.project, definition_body(artifact.id))


def test_completed_result_link_uses_historical_revision_and_small_frozen_provenance(evidence):
    f = evidence
    retained = retained_report(f)
    with f.factory.begin() as db:
        studies.save_study(db, f.project, protocol(name="Current r2"), f.author, id_=f.study, expected=1)
        studies.append_record(db, f.project, f.study, record(revision=2, key="new-observation"), f.author)
        linked = analysis.record_result(db, f.project, f.study, retained.run, f.author, "result", notes="Authored assessment")
        assert linked["revision"] == 1 and linked["protocol_digest"] == retained.source["protocol_digest"]
        assert linked["kind"] == "analysis_result" and linked["supersedes"] is None and linked["unit_id"] == ""
        assert linked["data"]["analysis"]["source"]["through_seq"] == 7
        assert linked["data"]["analysis"]["counts"]["source_records"] == 7
        assert "custom_output" not in json.dumps(linked["data"]) and len(canonical(linked["data"])) < 4000
        assert {ref["id"] for ref in linked["artifact_references"]} == {retained.artifact, retained.snapshot}
        assert linked["run_references"][0]["id"] == retained.run
        assert linked["run_references"][0]["state_at_link"] == "succeeded"
        svc.get_run(db, f.project, retained.run).state = "failed"
        replayed = analysis.record_result(db, f.project, f.study, retained.run, f.author, "result", notes="Authored assessment")
        assert replayed == linked
        with pytest.raises(svc.LabError, match="different inputs"):
            analysis.record_result(db, f.project, f.study, retained.run, f.author, "result", notes="Changed")
        assert studies.get_study(db, f.project, f.study)["last_sequence"] == 9


@pytest.mark.parametrize("source_patch", [{"study_id": "another-study"}, {"revision": 2}, {"through_seq": 6},
                                         {"protocol_digest": "0"*64}, {"sha256": "0"*64}])
def test_result_rejects_report_source_mismatch(evidence, source_patch):
    f = evidence
    retained = retained_report(f, source_patch=source_patch)
    with f.factory.begin() as db:
        with pytest.raises(svc.LabError, match="source does not match"):
            analysis.record_result(db, f.project, f.study, retained.run, f.author, "result")
        assert studies.get_study(db, f.project, f.study)["last_sequence"] == 7


@pytest.mark.parametrize("state,engine", [("failed", "statistics.analyze"), ("queued", "statistics.analyze"),
                                         ("succeeded", "statistics.plan")])
def test_result_requires_completed_analysis_and_project_scope(evidence, state, engine):
    f = evidence
    retained = retained_report(f, state=state, engine=engine)
    with f.factory.begin() as db:
        with pytest.raises(svc.LabError, match="completed statistics.analyze"):
            analysis.record_result(db, f.project, f.study, retained.run, f.author, "result")
        with pytest.raises(svc.LabError, match="study not found"):
            analysis.record_result(db, f.other, f.study, retained.run, f.author, "result")


@pytest.mark.parametrize("damage", ["report_bytes", "report_origin", "report_reference", "snapshot_visibility", "request"])
def test_result_rejects_corruption_identity_or_request_changes(evidence, damage):
    f = evidence
    retained = retained_report(f, report_patch={"request": parameters(confidence=.8)} if damage == "request" else None)
    with f.factory.begin() as db:
        artifact = svc.get_artifact(db, f.project, retained.artifact)
        if damage == "report_bytes":
            path = f.store.root / artifact.storage_key
            path.write_bytes(b"corrupt")
        elif damage == "report_origin":
            artifact.attempt_id = None
        elif damage == "report_reference":
            run = svc.get_run(db, f.project, retained.run)
            run.result = {"artifacts": [{**run.result["artifacts"][0], "sha256": "0"*64}]}
        elif damage == "snapshot_visibility":
            svc.get_artifact(db, f.project, retained.snapshot).visibility = "model"
    with f.factory.begin() as db:
        with pytest.raises(svc.LabError):
            analysis.record_result(db, f.project, f.study, retained.run, f.author, "result")
        assert studies.get_study(db, f.project, f.study)["last_sequence"] == 7
