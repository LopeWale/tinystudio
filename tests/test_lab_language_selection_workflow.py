"""Authored incompatibilities must not become queued research runs.

Uses the existing disposable PostgreSQL fixture; run with --noconftest.
"""
import pytest
from sqlalchemy import func, select

from tests.test_lab_foundation import database, setup
from tinystudio.lab.models import LabAttempt, LabDispatch, LabRun


@pytest.mark.parametrize("selection,reason", [
    ({"learning": {"architecture": {"hidden_size": 9, "heads": 2}}}, "divisible"),
    ({"learning": {"family": "gru", "architecture": {"heads": 2}}}, "architecture"),
    ({"learning": {"objective": "dpo"}}, "objective"),
    ({"parameters": {"objective": "dpo"}}, "objective"),
    ({"learning": {"update_strategy": "qlora"}}, "update_strategy"),
    ({"learning": {"representation": {"kind": "engineering"}}}, "vocabulary"),
    ({"learning": {"representation": {"kind": "byte", "custom_option": True}}}, "representation"),
    ({"retrieval": {"mode": "lexical"}}, "retrieval"),
])
def test_language_compatibility_and_admission_reject_unused_or_invalid_axes(setup, selection, reason):
    factory, project, _, client, prefix, _ = setup
    definition = {"name": "Invalid selection fixture", "engine": "learning.language",
                  "execution": {"profile": "trusted_native"}, **selection}
    checked = client.post(prefix + "/preflight", json={"definition": definition})
    assert checked.status_code == 200, checked.text
    report = checked.json()
    assert report["compatible"] is False
    assert report["execution_started"] is False
    assert any(not row["passed"] and reason in row["message"] for row in report["checks"]), report

    # Drafts remain editable and portable; the run admission boundary is authoritative.
    saved = client.post(prefix + "/definitions", json={"definition": definition})
    assert saved.status_code == 201, saved.text
    admitted = client.post(prefix + "/runs", json={
        "definition_id": saved.json()["id"], "revision": saved.json()["revision"],
        "target_id": "local", "idempotency_key": "invalid-selection",
    })
    assert admitted.status_code == 409, admitted.text
    assert reason in admitted.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(LabRun).where(LabRun.project_id == project)) == 0
        assert db.scalar(select(func.count()).select_from(LabDispatch).join(LabRun).where(LabRun.project_id == project)) == 0
        assert db.scalar(select(func.count()).select_from(LabAttempt).join(LabRun).where(LabRun.project_id == project)) == 0
