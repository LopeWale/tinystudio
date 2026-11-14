"""Actual owned HTTP preserves spool logical paths through portable recovery."""
import json
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from tests.test_lab_native_workflow import native_http
from tests.test_lab_recovery_http import prepare_recovery
from tinystudio.lab import portable
from tinystudio.lab.evidence_recovery_client import RecoveryArchiveClient
from tinystudio.lab.evidence_spool import EvidenceSpool, SpoolPolicy
from tinystudio.db.models import Project
from tinystudio.lab.models import LabRun


@pytest.mark.parametrize("logical", ["evidence/channel:recordings/sensor:raw.jsonl",
                                     "evidence/" + "/".join(["channel"] * 40) + "/raw.jsonl"])
def test_actual_spool_client_server_portable_round_trip_preserves_logical_path(native_http, logical):
    fixture = native_http
    target, run, body, _ = prepare_recovery(fixture)
    entry = EvidenceSpool(SpoolPolicy(True, fixture.root / "logical-spool")).allocate(
        {"run_id": run["id"], "attempt_id": body["source_attempt_id"], "spec": run["spec"],
         "spec_digest": run["spec_digest"]}, server=fixture.url, target_id=target["id"])
    target_path = entry.execution_root / "work/evidence" / logical.removeprefix("evidence/")
    target_path.parent.mkdir(parents=True)
    source = b'{"reading":7}\npartial'
    target_path.write_bytes(source)
    snapshot = entry.snapshot()
    archive = fixture.root / "logical-export.zip"
    entry.export(archive, expected_manifest_digest=snapshot["manifest_sha256"])
    result = RecoveryArchiveClient(fixture.http, fixture.prefix).import_archive(
        archive, idempotency_key="logical-evidence-path")
    assert result["classification"] == "operator_recovered_evidence"
    exported = fixture.root / "portable-export"
    exported.mkdir()
    with fixture.factory.begin() as db:
        source_project = db.get(LabRun, run["id"]).project_id
        destination_project = db.scalar(select(Project.id).where(Project.slug == "restored-b"))
        bundle = portable.export_bundle(db, source_project, run["id"], exported)
    with ZipFile(bundle) as zip:
        manifest = json.loads(zip.read("manifest.json"))
        ref = manifest["recovered_evidence"][0]["files"][0]
        assert ref["name"] == logical
        assert zip.read("objects/" + ref["sha256"]) == source
    restored = fixture.root / "portable-restored"
    restored.mkdir()
    with fixture.factory.begin() as db:
        imported = portable.import_bundle(db, destination_project, bundle, restored)
    assert imported["execution_started"] is False
    assert imported["provenance"]["recovered_evidence"] == "historical_operator_supplied"
