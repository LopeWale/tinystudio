"""Recovery bundles retain bytes and historical claims without live authority."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from sqlalchemy import func, select

from tests.test_lab_recovered_evidence import store, uploaded, attach, recovery
from tinystudio.lab import portable, service as svc
from tinystudio.lab.contracts import ExperimentDefinition, ResolvedRunSpec, canonical, checksum
from tinystudio.lab.models import LabArtifact, LabAttempt, LabEvent, LabRun, LabTarget


@pytest.fixture
def retained(store):
    with store.factory.begin() as db:
        run = db.get(LabRun, store.run)
        definition = ExperimentDefinition(name="Recovered experiment", engine="acquisition.modbus",
                                          operating_mode="acquisition", execution={"profile": "trusted_native"})
        store.spec = ResolvedRunSpec(definition=definition, definition_id=run.definition_id, revision=1,
                                     implementation=store.spec["implementation"], input_manifest=[],
                                     runtime=store.spec["runtime"], research_identity=store.run).model_dump()
        run.spec = deepcopy(store.spec)
        run.spec_digest = checksum(run.spec)
    return store


def attached(store, *, state="failed", attribution=None, logical=None):
    with store.factory.begin() as db:
        run, attempt = db.get(LabRun, store.run), db.get(LabAttempt, store.attempt)
        run.state = attempt.state = state
        body, manifest, file = uploaded(db, store, attempt_id=attribution,
                                       manifest_change=(lambda value: value["files"][0].update(path=logical)) if logical else None)
        if logical:
            body["files"][0]["name"] = logical
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        result = attach(db, store, body, preview)
        return result, manifest.id, file.id


def exported(store, label="export"):
    directory = store.root / label
    directory.mkdir()
    with store.factory.begin() as db:
        path = portable.export_bundle(db, store.project, store.run, directory)
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    return path, manifest


def imported(store, path, label="import"):
    directory = store.root / label
    directory.mkdir()
    with store.factory.begin() as db:
        before = {model: db.scalar(select(func.count()).select_from(model)) for model in (LabRun, LabAttempt, LabEvent)}
        result = portable.import_bundle(db, store.other, path, directory)
        assert result["execution_started"] is False
        assert all(db.scalar(select(func.count()).select_from(model)) == count for model, count in before.items())
        original = svc.get_artifact(db, store.other, result["historical_manifest"]["id"])
        document = json.loads(svc.materialize(original, directory).read_bytes())
        return result, document


@pytest.mark.parametrize("state", ["failed", "recovery_required"])
def test_attached_recovery_exports_all_references_and_imports_as_history(retained, state):
    result, manifest_id, file_id = attached(retained, state=state)
    path, manifest = exported(retained)
    receipt_id = result["receipt"]["id"]
    assert set(row["id"] for row in manifest["artifacts"]) == {receipt_id, manifest_id, file_id}
    association = manifest["recovered_evidence"][0]
    assert association["receipt_artifact_id"] == receipt_id and association["manifest_artifact_id"] == manifest_id
    assert association["source_attempt_id"] == retained.attempt
    assert association["files"][0]["artifact_id"] == file_id
    assert all(manifest["artifact_origins"][id_] == {"kind": "recovered_evidence", "attempt_id": None}
               for id_ in (receipt_id, manifest_id, file_id))
    with ZipFile(path) as archive:
        file = next(row for row in manifest["artifacts"] if row["id"] == file_id)
        assert archive.read("objects/" + file["sha256"]) == b'{"reading": 7}\nnot-a-json-row'
    restored, history = imported(retained, path)
    assert history == manifest
    assert restored["provenance"]["recovered_evidence"] == "historical_operator_supplied"
    with retained.factory.begin() as db:
        assert db.get(LabRun, retained.run).state == state
        assert all(svc.get_artifact(db, retained.other, id_).attempt_id is None for id_ in restored["artifact_mapping"].values())


def test_existing_attempt_output_origin_is_preserved_when_recovered_again(retained):
    _, _, file_id = attached(retained, attribution=retained.attempt)
    path, manifest = exported(retained)
    assert manifest["artifact_origins"][file_id] == {"kind": "attempt_output", "attempt_id": retained.attempt}
    imported(retained, path)


def test_existing_submitted_input_origin_is_preserved_when_recovered(retained):
    data = b'{"reading": 7}\nnot-a-json-row'
    with retained.factory.begin() as db:
        path = retained.root / "input.jsonl"
        path.write_bytes(data)
        row = svc.store_artifact(db, retained.project, path, "input.jsonl", "application/x-ndjson")
        input_id = row.id
        run = db.get(LabRun, retained.run)
        spec = deepcopy(run.spec)
        spec["definition"]["inputs"] = [{"artifact_id": row.id, "sha256": row.sha256, "role": "source", "visibility": "model"}]
        spec["input_manifest"] = [svc.artifact_dict(row)]
        run.spec, run.spec_digest, retained.spec = spec, checksum(spec), spec
        run.state = db.get(LabAttempt, retained.attempt).state = "failed"
        body, _, _ = uploaded(db, retained)
        body["files"][0].update(artifact_id=row.id, sha256=row.sha256, size=row.size)
        preview = recovery().preview_recovered_evidence(db, retained.project, retained.run, body)
        attach(db, retained, body, preview)
    bundle, manifest = exported(retained)
    assert manifest["artifact_origins"][input_id] == {"kind": "input", "attempt_id": None}
    imported(retained, bundle)


@pytest.mark.parametrize("part", ["receipt_role", "receipt_visibility", "receipt_attempt", "file_metadata", "receipt_bytes", "manifest_bytes", "file_bytes", "source_target"])
def test_export_rechecks_committed_recovery_metadata_and_cas_bytes(retained, part):
    attached_result, manifest_id, file_id = attached(retained)
    with retained.factory.begin() as db:
        receipt = svc.get_artifact(db, retained.project, attached_result["receipt"]["id"])
        if part == "receipt_role":
            receipt.role = "ordinary"
        elif part == "receipt_visibility":
            receipt.visibility = "model"
        elif part == "receipt_attempt":
            receipt.attempt_id = retained.attempt
        elif part == "file_metadata":
            svc.get_artifact(db, retained.project, file_id).name = "changed.jsonl"
        elif part == "source_target":
            target = LabTarget(project_id=retained.project, name="another-target")
            db.add(target)
            db.flush()
            db.get(LabAttempt, retained.attempt).target_id = target.id
        else:
            row = receipt if part == "receipt_bytes" else svc.get_artifact(db, retained.project, manifest_id if part == "manifest_bytes" else file_id)
            (retained.storage.root / row.storage_key).write_bytes(b"corrupted committed bytes")
    with pytest.raises(Exception, match="receipt|recovery|recovered|checksum|size|metadata|target"):
        exported(retained)


def rewrite(path, destination, edit, object_edit=None):
    with ZipFile(path) as archive:
        members = {item.filename: archive.read(item) for item in archive.infolist()}
    manifest = json.loads(members["manifest.json"])
    edit(manifest)
    if object_edit:
        object_edit(manifest, members)
    members["manifest.json"] = canonical(manifest)
    members["manifest.sha256"] = checksum(manifest).encode()
    with ZipFile(destination, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return destination


@pytest.mark.parametrize("mutation", ["missing_field", "missing_association", "orphan_association", "missing_event",
    "event_receipt_hash", "unknown_file", "extra_file", "missing_file", "wrong_source_attempt", "wrong_origin",
    "orphan_recovered_artifact", "extra_object", "unhashable_file_ref", "unhashable_attempt_ref", "colliding_event_seq"])
def test_import_rejects_missing_or_phantom_recovery_associations_before_storing(retained, mutation):
    attached(retained)
    path, _ = exported(retained)
    def edit(manifest):
        association = manifest["recovered_evidence"][0]
        event = next(row for row in manifest["run"]["events"] if row["kind"] == "server.evidence_recovered")
        if mutation == "missing_field":
            manifest.pop("recovered_evidence")
        elif mutation == "missing_association":
            manifest["recovered_evidence"] = []
        elif mutation == "orphan_association":
            manifest["recovered_evidence"].append(dict(association, event_seq=999))
        elif mutation == "missing_event":
            manifest["run"]["events"].remove(event)
        elif mutation == "event_receipt_hash":
            event["payload"]["receipt"]["sha256"] = "0" * 64
        elif mutation == "unknown_file":
            association["files"][0]["artifact_id"] = "unknown"
        elif mutation == "extra_file":
            association["files"].append(dict(association["files"][0], name="evidence/extra"))
        elif mutation == "missing_file":
            association["files"] = []
        elif mutation == "wrong_source_attempt":
            association["source_attempt_id"] = retained.foreign
        elif mutation == "wrong_origin":
            manifest["artifact_origins"][association["receipt_artifact_id"]] = {"kind": "attempt_output", "attempt_id": retained.attempt}
        elif mutation == "orphan_recovered_artifact":
            orphan = dict(manifest["artifacts"][0], id="orphan-artifact")
            manifest["artifacts"].append(orphan)
            manifest["artifact_origins"][orphan["id"]] = {"kind": "recovered_evidence", "attempt_id": None}
        elif mutation == "unhashable_file_ref":
            association["files"][0]["artifact_id"] = []
        elif mutation == "unhashable_attempt_ref":
            association["source_attempt_id"] = []
        elif mutation == "colliding_event_seq":
            manifest["run"]["events"].append({"seq": event["seq"], "kind": "progress", "payload": {}, "time": event["time"]})
    def object_edit(manifest, members):
        if mutation == "extra_object":
            members["objects/" + "0" * 64] = b"unreferenced"
    changed = rewrite(path, retained.root / "bad.zip", edit, object_edit)
    folder = retained.root / "bad-import"
    folder.mkdir()
    with retained.factory.begin() as db:
        before = db.scalar(select(func.count()).select_from(LabArtifact))
        with pytest.raises(svc.LabError, match="recovery|recovered|receipt|association|origin|object|metadata"):
            portable.import_bundle(db, retained.other, changed, folder)
        assert db.scalar(select(func.count()).select_from(LabArtifact)) == before


@pytest.mark.parametrize("mutation", ["metadata", "source", "producer", "preview_digest", "byte_facts"])
def test_import_checks_receipt_contents_even_when_bundle_hashes_are_self_consistent(retained, mutation):
    attached(retained)
    path, _ = exported(retained)
    def objects(manifest, members):
        association = manifest["recovered_evidence"][0]
        receipt = next(item for item in manifest["artifacts"] if item["id"] == association["receipt_artifact_id"])
        document = json.loads(members.pop("objects/" + receipt["sha256"]))
        if mutation == "metadata":
            document["files"][0]["artifact"]["role"] = "fabricated"
        elif mutation == "source":
            document["source"]["spec_digest"] = "0" * 64
        elif mutation == "producer":
            document["producer_manifest"]["origin"]["run_id"] = "another-run"
        elif mutation == "preview_digest":
            document["digest"] = "0" * 64
        else:
            document["files"][0]["verified_bytes"]["jsonl"]["newline_count"] = 999
        data = canonical(document) + b"\n"
        sha = hashlib.sha256(data).hexdigest()
        receipt.update(sha256=sha, size=len(data))
        association["receipt_sha256"] = sha
        event = next(row for row in manifest["run"]["events"] if row["kind"] == "server.evidence_recovered")
        event["payload"]["receipt"] = deepcopy(receipt)
        members["objects/" + sha] = data
    changed = rewrite(path, retained.root / "forged-receipt.zip", lambda _: None, objects)
    with pytest.raises(svc.LabError, match="receipt|recovery|recovered|metadata|source|digest|producer|byte"):
        imported(retained, changed)


def test_original_attempt_outcomes_and_legacy_bundles_remain_compatible(retained):
    with retained.factory.begin() as db:
        db.get(LabAttempt, retained.attempt).token_hash = svc.secret_hash("portable-lease")
        svc.finish(db, db.get(LabTarget, retained.target), retained.attempt, "portable-lease", "failed", None, "original failure")
    original, manifest = exported(retained)
    assert "recovered_evidence" not in manifest and len(manifest["attempt_outcomes"]) == 1
    imported(retained, original)
    legacy = rewrite(original, retained.root / "legacy.zip", lambda value: (value.pop("artifact_origins"), value.pop("attempt_outcomes")))
    result, _ = imported(retained, legacy, label="legacy-import")
    assert result["provenance"]["attempt_attribution"] == "unknown_legacy_bundle"
    attached(retained)
    combined, manifest = exported(retained, label="combined-export")
    assert len(manifest["attempt_outcomes"]) == len(manifest["recovered_evidence"]) == 1
    imported(retained, combined, label="combined-import")


def test_multiple_recovery_events_keep_each_complete_association_with_shared_cas(retained):
    attached(retained)
    with retained.factory.begin() as db:
        body, _, _ = uploaded(db, retained)
        preview = recovery().preview_recovered_evidence(db, retained.project, retained.run, body)
        attach(db, retained, body, preview, idempotency_key="recover-two")
    path, manifest = exported(retained)
    assert len(manifest["recovered_evidence"]) == 2
    assert len({item["event_seq"] for item in manifest["recovered_evidence"]}) == 2
    assert len(manifest["artifacts"]) == 6
    imported(retained, path)


@pytest.mark.parametrize("logical", ["evidence/channel:recordings/sensor:raw.jsonl",
                                     "evidence/" + "/".join(["channel"] * 40) + "/raw.jsonl"])
def test_recovery_bundle_keeps_logical_paths_without_artifact_label_limits(retained, logical):
    attached(retained, logical=logical)
    path, manifest = exported(retained)
    assert manifest["recovered_evidence"][0]["files"][0]["name"] == logical
    _, history = imported(retained, path)
    assert history["recovered_evidence"][0]["files"][0]["name"] == logical
