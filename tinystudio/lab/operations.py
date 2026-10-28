"""Durable evidence jobs. RQ carries IDs; PostgreSQL owns authority and results.

Callers commit admissions. Long storage operations acquire no target/run locks.
Import changes and the fenced success record share one database transaction.
"""
from __future__ import annotations

import secrets
import math
import time
import tempfile
import threading
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from tinystudio.db.models import Project
from . import portable, service as svc
from .contracts import checksum
from .models import LabArtifact, LabAttempt, LabEvent, LabOperation

LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 20


def get_operation(db, project: str, id_: str, *, lock: bool = False) -> LabOperation:
    query = select(LabOperation).where(LabOperation.id == id_, LabOperation.project_id == project)
    row = db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
    if row is None:
        raise svc.LabError("operation not found", 404)
    return row


def operation_dict(row: LabOperation) -> dict:
    return {"id": row.id, "kind": row.kind, "state": row.state,
            "created": row.created.isoformat(), "finished": row.finished.isoformat() if row.finished else None,
            "attempt": row.generation, "result": row.result, "error": row.error, "error_detail": row.error_detail}


def _snapshot(db, project: str, run_id: str) -> str:
    run = svc.get_run(db, project, run_id)
    if run.state not in svc.TERMINAL:
        raise svc.LabError("only terminal runs can be exported", 409)
    body = svc.run_dict(db, run)
    body["events"] = [{"seq": e.seq, "kind": e.kind, "payload": e.payload, "time": e.time.isoformat()}
                      for e in db.scalars(select(LabEvent).where(LabEvent.run_id == run.id).order_by(LabEvent.seq))]
    attempts = select(LabAttempt.id).where(LabAttempt.run_id == run.id)
    artifacts = {a.id: svc.artifact_dict(a) for a in db.scalars(select(LabArtifact).where(LabArtifact.attempt_id.in_(attempts)))}
    for ref in run.spec["definition"]["inputs"]:
        artifacts[ref["artifact_id"]] = svc.artifact_dict(svc.get_artifact(db, project, ref["artifact_id"]))
    body["artifacts"] = [artifacts[key] for key in sorted(artifacts)]
    return checksum(body)


def _request(db, project, kind, payload, key):
    if not isinstance(key, str) or not key.strip() or len(key) > 120:
        raise svc.LabError("idempotency key must contain 1–120 characters")
    # Short admission lock serializes duplicate keys without a failed transaction.
    if db.scalar(select(Project.id).where(Project.id == project).with_for_update()) is None:
        raise svc.LabError("project not found", 404)
    previous = db.scalar(select(LabOperation).where(LabOperation.project_id == project, LabOperation.idempotency_key == key))
    if previous:
        if previous.kind != kind or previous.payload != payload:
            raise svc.LabError("idempotency key belongs to a different operation", 409)
        return previous
    row = LabOperation(project_id=project, kind=kind, payload=payload, payload_digest=checksum(payload), idempotency_key=key)
    db.add(row)
    db.flush()
    return row


def request_export(db, project: str, run_id: str, idempotency_key: str) -> LabOperation:
    return _request(db, project, "export", {"run_id": run_id, "snapshot_digest": _snapshot(db, project, run_id)}, idempotency_key)


def request_import(db, project: str, artifact_id: str, idempotency_key: str) -> LabOperation:
    artifact = svc.get_artifact(db, project, artifact_id)
    return _request(db, project, "import", {"artifact_id": artifact.id, "sha256": artifact.sha256, "size": artifact.size}, idempotency_key)


def request_dataset_promotion(db, project, run_id, artifact_id, name, idempotency_key, *, snapshot_name=None, min_training=10):
    from .data_bridge_operations import request
    return request(db, project, run_id, artifact_id, name, idempotency_key,
                   snapshot_name=snapshot_name, min_training=min_training)


def cancel_operation(db, project: str, id_: str) -> LabOperation:
    row = get_operation(db, project, id_, lock=True)
    if row.state in {"queued", "running"}:
        row.state, row.finished = "cancelled", svc.now()
        row.error_detail = None
        row.claim_token, row.lease_until = None, None
        row.error = "Cancelled; unpublished database work is discarded. Immutable objects may remain unreferenced."
    return row


def retry_operation(db, project: str, id_: str) -> LabOperation:
    row = get_operation(db, project, id_, lock=True)
    if row.state not in {"failed", "lost", "cancelled"}:
        raise svc.LabError("only failed, lost, or cancelled operations may retry", 409)
    row.state, row.finished, row.error, row.result = "queued", None, None, None
    row.error_detail = None
    row.claim_token, row.lease_until, row.dispatch_after = None, None, None
    return row


def recover_expired(db) -> int:
    result = db.execute(update(LabOperation).where(LabOperation.state == "running", LabOperation.lease_until <= svc.now())
                        .values(state="lost", claim_token=None, lease_until=None, finished=svc.now(),
                                error="Worker lease expired; unpublished database work cannot commit. Explicit retry is required."))
    return result.rowcount


def _dispatch_active(deadline, cancelled):
    return not cancelled() and time.monotonic() < deadline


def _dispatch_limits(db, deadline, cancelled):
    """Each independent transaction owns limits; caller-local SETs do not carry."""
    if not _dispatch_active(deadline, cancelled):
        return False
    if db.get_bind().dialect.name == "postgresql":
        milliseconds = int((deadline - time.monotonic()) * 1000)
        if milliseconds < 1:
            return False
        db.execute(text("select set_config('statement_timeout', :statement, true), "
                        "set_config('lock_timeout', :lock, true)"),
                   {"statement": str(min(5000, milliseconds)), "lock": str(min(3000, milliseconds))})
    return _dispatch_active(deadline, cancelled)


def dispatch_pending(db, enqueue_callback, *, cancelled=lambda: False, timeout_seconds=20) -> list[str]:
    """Deliver committed IDs with bounded DB waits and cooperative stop/deadline.

    Returns IDs whose callbacks completed, including deliveries made before a
    stop that prevented their dispatch timestamp write. This cannot withdraw
    delivered jobs or interrupt an arbitrary callback: the delivery adapter must
    bound its own I/O. A lost response/timestamp may redeliver; claim fencing
    handles that ambiguity. Caller-uncommitted admissions remain invisible.
    """
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise ValueError("dispatch timeout_seconds must be finite and nonnegative")
    deadline = time.monotonic() + timeout_seconds
    if not _dispatch_active(deadline, cancelled):
        return []
    bind = db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    factory = lambda: Session(engine, expire_on_commit=False)
    with factory() as reader:
        if not _dispatch_limits(reader, deadline, cancelled):
            return []
        ids = list(reader.scalars(select(LabOperation.id).where(LabOperation.state == "queued",
            (LabOperation.dispatch_after.is_(None)) | (LabOperation.dispatch_after <= svc.now())).limit(100)))
    sent = []
    for id_ in ids:
        if not _dispatch_active(deadline, cancelled):
            break
        enqueue_callback(id_)
        sent.append(id_)
        if not _dispatch_active(deadline, cancelled):
            break
        with factory() as writer, writer.begin():
            if not _dispatch_limits(writer, deadline, cancelled):
                break
            writer.execute(update(LabOperation).where(LabOperation.id == id_, LabOperation.state == "queued")
                           .values(dispatch_after=svc.now() + timedelta(seconds=30)))
    return sent


class _Lease:
    """A single attempt's authority, refreshed only by matching token/generation."""
    def __init__(self, factory, operation_id, token, generation):
        self.factory, self.id, self.token, self.generation = factory, operation_id, token, generation
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)

    def predicate(self):
        return (LabOperation.id == self.id, LabOperation.state == "running", LabOperation.claim_token == self.token,
                LabOperation.generation == self.generation, LabOperation.lease_until > svc.now())

    def _heartbeat(self):
        while not self.stop.wait(HEARTBEAT_SECONDS):
            try:
                with self.factory() as db, db.begin():
                    result = db.execute(update(LabOperation).where(*self.predicate()).values(lease_until=svc.now()+timedelta(seconds=LEASE_SECONDS)))
                    if result.rowcount != 1:
                        return
            except Exception:
                # No guessed authority after a failed heartbeat: final publication
                # still checks the persisted lease and may reject this attempt.
                return

    def check(self):
        """Cooperative boundary check; final publication still requires finish."""
        with self.factory() as db:
            if db.scalar(select(LabOperation.id).where(*self.predicate())) is None:
                raise svc.LabError("operation authority expired or was cancelled", 409)

    def finish(self, db, result):
        row = db.scalar(select(LabOperation).where(*self.predicate()).with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise svc.LabError("operation authority expired or was cancelled", 409)
        row.state, row.result, row.finished = "succeeded", result, svc.now()
        row.claim_token, row.lease_until, row.error = None, None, None
        return operation_dict(row)


def run_operation(operation_id: str, session_factory=None) -> dict | None:
    if session_factory is None:
        from tinystudio.db import session_factory as default_factory
        session_factory = default_factory()
    token = secrets.token_hex(32)
    with session_factory() as db, db.begin():
        # Atomic compare-and-swap prevents duplicate delivery from starting work.
        result = db.execute(update(LabOperation).where(LabOperation.id == operation_id, LabOperation.state == "queued")
                            .values(state="running", claim_token=token, generation=LabOperation.generation+1,
                                    lease_until=svc.now()+timedelta(seconds=LEASE_SECONDS), dispatch_after=None))
        if result.rowcount != 1:
            return None
        row = db.get(LabOperation, operation_id)
        project, kind, payload, generation = row.project_id, row.kind, row.payload, row.generation
        digest = row.payload_digest
    lease = _Lease(session_factory, operation_id, token, generation)
    lease.thread.start()
    try:
        if checksum(payload) != digest:
            raise svc.LabError("operation payload checksum mismatch", 409)
        with tempfile.TemporaryDirectory(prefix="ts-lab-operation-") as root:
            directory = Path(root)
            with session_factory() as db, db.begin():
                if kind == "export":
                    if _snapshot(db, project, payload["run_id"]) != payload["snapshot_digest"]:
                        raise svc.LabError("run changed since export admission", 409)
                    bundle = portable.export_bundle(db, project, payload["run_id"], directory)
                    # Read a fresh database view after the potentially long export.
                    db.expire_all()
                    if _snapshot(db, project, payload["run_id"]) != payload["snapshot_digest"]:
                        raise svc.LabError("run changed during export", 409)
                    artifact = svc.store_artifact(db, project, bundle, "research.zip", "application/zip", "bundle", visibility="evaluation")
                    result = {"artifact": svc.artifact_dict(artifact)}
                elif kind == "import":
                    artifact = svc.get_artifact(db, project, payload["artifact_id"])
                    if artifact.sha256 != payload["sha256"] or artifact.size != payload["size"]:
                        raise svc.LabError("bundle artifact changed", 409)
                    bundle = svc.materialize(artifact, directory)
                    if bundle.stat().st_size != artifact.size:
                        raise svc.LabError("bundle artifact size mismatch", 409)
                    result = portable.import_bundle(db, project, bundle, directory)
                elif kind == "data_bridge":
                    if payload.get("intent", {}).get("action") == "capture_repository":
                        from .repository_capture import execute_capture
                        result = execute_capture(db, project, payload, directory, check_authority=lease.check)
                    else:
                        from .data_bridge_operations import execute
                        result = execute(db, project, payload, directory)
                else:
                    raise svc.LabError("unsupported operation kind")
                # Read back persisted bytes before publishing references. This also
                # detects an acknowledged but corrupted remote storage write.
                verify = directory / "verify"
                verify.mkdir()
                ids = ([result["artifact"]["id"]] if kind == "export" else
                       list(result["artifact_mapping"].values()) + [result["historical_manifest"]["id"]] if kind == "import" else [])
                for artifact_id in ids:
                    stored = svc.get_artifact(db, project, artifact_id)
                    if svc.materialize(stored, verify).stat().st_size != stored.size:
                        raise svc.LabError("persisted output size mismatch", 409)
                return lease.finish(db, result)
    except Exception as exc:
        # Import rows roll back before recording failure in an independent txn.
        with session_factory() as db, db.begin():
            db.execute(update(LabOperation).where(*lease.predicate()).values(state="failed", finished=svc.now(),
                error=f"{type(exc).__name__}: {str(exc)[:1800]}", claim_token=None, lease_until=None,
                error_detail={"status": getattr(exc, "status", 500), "code": "limit_exceeded" if getattr(exc, "status", None) == 413 else "operation_failed"}))
        return None
    finally:
        lease.stop.set()
        lease.thread.join(timeout=HEARTBEAT_SECONDS+1)
