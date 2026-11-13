"""Durable study snapshots and verified links to retained observed-data analyses.

The caller owns the transaction. Retained record boundaries describe provenance;
they never establish statistical sample size or replace researcher selections.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Callable

from sqlalchemy import select

from tinystudio.db.models import Project
from tinystudio.storage import ArtifactNotFound, StorageError
from . import service as svc, studies
from .analysis_selection import ANALYSIS_FORMAT
from .contracts import checksum
from .models import LabAttempt, LabOperation, LabStudyRecord

SOURCE_FIELDS = ("study_id", "revision", "through_seq", "protocol_digest")


def request_input(db, project: str, study_id: str, idempotency_key: str, *,
                  revision: int | None = None, through_seq: int | None = None) -> LabOperation:
    """Admit once, replaying authored intent before refreshing default boundaries."""
    from .operations import _request
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 120:
        raise svc.LabError("idempotency key must contain 1–120 characters")
    intent = {"action": "capture_study", "study_id": study_id, "revision": revision, "through_seq": through_seq}
    if db.scalar(select(Project.id).where(Project.id == project).with_for_update()) is None:
        raise svc.LabError("project not found", 404)
    previous = db.scalar(select(LabOperation).where(LabOperation.project_id == project,
                                                   LabOperation.idempotency_key == idempotency_key))
    if previous is not None:
        if previous.kind != "data_bridge" or previous.payload.get("intent") != intent:
            raise svc.LabError("idempotency key belongs to a different operation", 409)
        return previous
    if not isinstance(study_id, str) or not study_id or len(study_id) > 64:
        raise svc.LabError("study_id must identify a retained study", 422)
    source = studies.tracking_manifest(db, project, study_id, revision=revision, through_seq=through_seq)
    return _request(db, project, "data_bridge", {"intent": intent, "source": source}, idempotency_key)


def _validate_source(db, project, payload):
    source = payload["source"]
    if payload.get("intent", {}).get("action") != "capture_study" or payload["intent"].get("study_id") != source.get("study_id"):
        raise svc.LabError("invalid study capture source", 409)
    retained = studies.tracking_manifest(db, project, source["study_id"],
                                        revision=source["revision"], through_seq=source["through_seq"])
    if retained != source:
        raise svc.LabError("retained study capture boundary changed", 409)
    return retained


def _sha(path, check_authority=lambda: None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            check_authority()
            digest.update(block)
    check_authority()
    return digest.hexdigest()


def execute_capture(db, project, payload, directory, check_authority: Callable = lambda: None):
    """Export the admitted historical boundary, verify storage, then publish a ref."""
    check_authority()
    source = _validate_source(db, project, payload)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = studies.export_tracking(db, project, source["study_id"], directory,
        revision=source["revision"], through_seq=source["through_seq"], check_authority=check_authority)
    check_authority()
    expected_sha, expected_size = _sha(path, check_authority), path.stat().st_size
    artifact = svc.store_artifact(db, project, path, path.name, "application/x-ndjson", "study_snapshot",
                                  visibility="evaluation")
    check_authority()
    readback = directory / "retained-snapshot"
    readback.mkdir()
    retained = svc.materialize(artifact, readback)
    if (artifact.sha256 != expected_sha or artifact.size != expected_size or retained.stat().st_size != expected_size
            or _sha(retained, check_authority) != expected_sha):
        raise svc.LabError("persisted study snapshot checksum or size mismatch", 409)
    _validate_source(db, project, payload)
    check_authority()
    return {"artifact": svc.artifact_dict(artifact), "source": deepcopy(source)}


def _snapshot_source(artifact, directory):
    """Verify complete tracking content, including the full-file retained SHA."""
    from .analysis_selection import validate_snapshot
    try:
        path = svc.materialize(artifact, Path(directory))
        source = validate_snapshot(path)
        if source.get("sha256") != artifact.sha256 or _sha(path) != artifact.sha256 or path.stat().st_size != artifact.size:
            raise svc.LabError("study snapshot artifact checksum or size mismatch", 409)
        return source
    except (ArtifactNotFound, StorageError) as error:
        raise svc.LabError("study snapshot bytes are unavailable or could not be verified", 409) from error
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        if isinstance(error, svc.LabError):
            raise
        raise svc.LabError("invalid study tracking snapshot: " + str(error), 422) from error


def save_analysis_definition(db, project, body: dict):
    """Bind project artifacts and researcher settings without accepting host paths."""
    from .analysis_selection import CUSTOM_METHOD, normalize_request
    if not isinstance(body, dict) or set(body) - {"name", "snapshot_artifact_id", "parameters", "execution",
                                                   "custom_code_artifact_id", "extra_artifact_ids"}:
        raise svc.LabError("invalid analysis definition fields", 422)
    try:
        parameters = normalize_request(body.get("parameters"))
    except (ValueError, TypeError, OverflowError) as error:
        raise svc.LabError(str(error), 422) from error
    custom = parameters["method"] == CUSTOM_METHOD
    code_id, extras = body.get("custom_code_artifact_id"), body.get("extra_artifact_ids", [])
    if (not isinstance(extras, list) or any(not isinstance(value, str) or not value for value in extras)
            or len(set(extras)) != len(extras)):
        raise svc.LabError("extra_artifact_ids must contain unique nonempty artifact ids", 422)
    if custom and (not isinstance(code_id, str) or not code_id):
        raise svc.LabError("custom.python requires a custom_code_artifact_id", 422)
    if not custom and (code_id is not None or extras):
        raise svc.LabError("analysis code and auxiliary inputs apply only to custom.python", 422)
    snapshot_id = body.get("snapshot_artifact_id")
    ids = [snapshot_id] + ([code_id] if custom else []) + extras
    if any(not isinstance(value, str) or not value or len(value) > 64 for value in ids) or len(ids) != len(set(ids)):
        raise svc.LabError("analysis requires distinct project-scoped artifact ids", 422)
    snapshot = svc.get_artifact(db, project, snapshot_id)
    with tempfile.TemporaryDirectory(prefix="ts-study-analysis-input-") as root:
        _snapshot_source(snapshot, root)
    inputs = []
    for artifact_id, role in [(snapshot_id, "study_snapshot")] + ([(code_id, "analysis_code")] if custom else []) + [
            (artifact_id, "analysis_auxiliary") for artifact_id in extras]:
        artifact = svc.get_artifact(db, project, artifact_id)
        inputs.append({"artifact_id": artifact.id, "sha256": artifact.sha256,
                       "role": role, "visibility": artifact.visibility})
    return svc.save_definition(db, project, {"name": body.get("name"), "domain": "statistics",
        "engine": "statistics.analyze", "operating_mode": "replay", "parameters": parameters,
        "execution": deepcopy(body.get("execution")), "inputs": inputs})


def _report(db, project, run, directory):
    references = [reference for reference in (run.result or {}).get("artifacts", [])
                  if reference.get("role") == "statistical_analysis"]
    if len(references) != 1:
        raise svc.LabError("analysis run must retain exactly one statistical_analysis report", 409)
    reference = references[0]
    artifact = svc.get_artifact(db, project, reference["id"])
    attempt = db.get(LabAttempt, artifact.attempt_id) if artifact.attempt_id else None
    if (artifact.role != "statistical_analysis" or attempt is None or attempt.run_id != run.id
            or artifact.sha256 != reference.get("sha256") or artifact.size != reference.get("size")):
        raise svc.LabError("retained analysis report identity does not match its run", 409)
    try:
        path = svc.materialize(artifact, directory)
        if _sha(path) != artifact.sha256 or path.stat().st_size != artifact.size:
            raise svc.LabError("analysis report checksum or size mismatch", 409)
        report = json.loads(path.read_bytes(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ArtifactNotFound, StorageError) as error:
        raise svc.LabError("analysis report bytes are unavailable or could not be verified", 409) from error
    except (ValueError, TypeError) as error:
        if isinstance(error, svc.LabError):
            raise
        raise svc.LabError("retained analysis report is not valid JSON", 409) from error
    if not isinstance(report, dict) or report.get("format") != ANALYSIS_FORMAT or not isinstance(report.get("source"), dict):
        raise svc.LabError("retained analysis report format is invalid", 409)
    return artifact, report


def _bounded_label(value):
    if not isinstance(value, str):
        return None
    return value if len(value) <= 500 else {"sha256": hashlib.sha256(value.encode()).hexdigest(), "characters": len(value)}


def record_result(db, project, study_id, run_id, author_id, idempotency_key, notes=""):
    """Append a compact analysis link at the report's historical protocol revision."""
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 120:
        raise svc.LabError("idempotency key must contain 1–120 characters", 422)
    if not isinstance(notes, str):
        raise svc.LabError("analysis result notes must be a string", 422)
    study = studies._study(db, project, study_id)
    intent = {"run_id": run_id, "notes": notes}
    previous = db.scalar(select(LabStudyRecord).where(LabStudyRecord.study_id == study_id,
                                                     LabStudyRecord.idempotency_key == idempotency_key))
    if previous is not None:
        record = studies._record(db, study, previous)
        if record["kind"] != "analysis_result" or record["author_id"] != author_id or record["data"].get("recording_intent") != intent:
            raise svc.LabError("record idempotency key was already used for different inputs", 409)
        return record
    run = svc.get_run(db, project, run_id)
    if run.state not in {"succeeded", "completed"} or run.spec.get("definition", {}).get("engine") != "statistics.analyze":
        raise svc.LabError("recording analysis requires a completed statistics.analyze run", 409)
    if checksum(run.spec) != run.spec_digest:
        raise svc.LabError("analysis run specification checksum mismatch", 409)
    with tempfile.TemporaryDirectory(prefix="ts-study-analysis-result-") as root:
        report_artifact, report = _report(db, project, run, Path(root))
        inputs = [ref for ref in run.spec["definition"]["inputs"] if ref.get("role") == "study_snapshot"]
        if len(inputs) != 1:
            raise svc.LabError("analysis run must bind exactly one study snapshot", 409)
        snapshot = svc.get_artifact(db, project, inputs[0]["artifact_id"])
        if inputs[0]["sha256"] != snapshot.sha256 or inputs[0]["visibility"] != snapshot.visibility:
            raise svc.LabError("analysis snapshot identity or visibility changed", 409)
        source = _snapshot_source(snapshot, root)
        # A self-consistent imported export can contain different observations
        # under the same study ID and protocol boundary. Only the server's
        # retained bytes establish association with this local study history.
        history = studies.export_tracking(db, project, study_id, Path(root) / "history",
            revision=source["revision"], through_seq=source["through_seq"])
        if _sha(history) != source["sha256"]:
            raise svc.LabError("analysis observations do not match retained study history", 409)
    reported = report["source"]
    if reported.get("study_id") != study_id or any(reported.get(key) != source[key] for key in (*SOURCE_FIELDS, "sha256")):
        raise svc.LabError("analysis report source does not match the requested study snapshot", 409)
    retained = studies.tracking_manifest(db, project, study_id, revision=source["revision"], through_seq=source["through_seq"])
    if any(retained[key] != source[key] for key in SOURCE_FIELDS):
        raise svc.LabError("analysis report source does not match retained study history", 409)
    from .analysis_selection import normalize_request
    if checksum(report.get("request")) != checksum(normalize_request(run.spec["definition"]["parameters"])):
        raise svc.LabError("analysis report request does not match its run", 409)
    method = report.get("method", {})
    if not isinstance(method, dict):
        raise svc.LabError("analysis report method must be an object", 409)
    counts = report.get("selection", {}).get("counts", {}) if isinstance(report.get("selection"), dict) else {}
    if not isinstance(counts, dict):
        raise svc.LabError("analysis selection counts must be an object", 409)
    summary = {"format": ANALYSIS_FORMAT, "source": {key: source[key] for key in (*SOURCE_FIELDS, "sha256")},
        "request_sha256": checksum(report["request"]), "method": {key: _bounded_label(method[key])
            for key in ("id", "name", "label", "version") if key in method},
        "counts": {key: value for key, value in counts.items() if key in {
            "source_records", "selected_records", "used_records", "group_a", "group_b"} and type(value) is int and value >= 0},
        "run_id": run.id, "run_spec_digest": run.spec_digest,
        "report": {"artifact_id": report_artifact.id, "sha256": report_artifact.sha256},
        "snapshot": {"artifact_id": snapshot.id, "sha256": snapshot.sha256}}
    return studies.append_record(db, project, study_id, {"revision": source["revision"],
        "idempotency_key": idempotency_key, "kind": "analysis_result", "unit_id": "", "condition": "",
        "data": {"recording_intent": intent, "analysis": summary}, "run_ids": [run.id],
        "artifact_ids": [report_artifact.id, snapshot.id]}, author_id)
