"""RQ delivers run IDs; laboratory HTTP claims and PostgreSQL retain authority.

The operator opts into trusted native execution through a private credential
file. Jobs never contain that credential, its path, or the frozen private spec.
Cancelling/killing an RQ job does not prove descendant cleanup: normal runner
lease cancellation owns teardown; a killed worker becomes lost on lease expiry.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from rq.exceptions import DuplicateJobError
from sqlalchemy import func, select, text

from tinystudio.db import dispose_after_fork, session_factory
from tinystudio.worker import JOB_TIMEOUT
from .dispatch import delivery_queue as queue
from . import service as svc
from .contracts import checksum
from .models import LabAttempt, LabDispatch, LabRun
from .runner import Runner, validate_url

CREDENTIAL_ENV = "TINYSTUDIO_LAB_RQ_CREDENTIAL_FILE"
DEFAULT_PROFILE = {"schema_version": 1, "backends": {"rq": {"enabled": True, "profiles": ["trusted_native"]}}}
_ACTIVE = {"created", "queued", "started", "deferred", "scheduled"}
_TERMINAL = {"finished", "failed", "stopped", "canceled"}


@contextmanager
def _bounded_db():
    with session_factory()() as db, db.begin():
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("select set_config('statement_timeout','5000',true), set_config('lock_timeout','3000',true)"))
        yield db


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("operator credential contains duplicate fields")
        result[key] = value
    return result


def load_credentials(path: Path | str | None = None) -> dict:
    """Read one bounded, owner-only regular file without following a final symlink."""
    path = path if path is not None else os.environ.get(CREDENTIAL_ENV)
    if not path:
        raise ValueError(f"set {CREDENTIAL_ENV} to an operator credential file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise ValueError("operator credential must be an accessible regular nonsymlink file with mode 0600") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
            raise ValueError("operator credential must be an owned regular nonsymlink file with mode 0600")
        if info.st_size > 65536:
            raise ValueError("operator credential exceeds 64 KiB")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(65537)
        if len(data) > 65536:
            raise ValueError("operator credential exceeds 64 KiB")
        try:
            credential = json.loads(data, object_pairs_hook=_pairs)
        except (ValueError, UnicodeError):
            raise ValueError("operator credential must contain valid JSON with unique fields") from None
    finally:
        os.close(fd)
    if not isinstance(credential, dict) or set(credential)-{"server", "target_id", "token", "profile_config", "access"}:
        raise ValueError("operator credential has unsupported fields")
    for key in ("server", "target_id", "token"):
        if not isinstance(credential.get(key), str) or not credential[key].strip() or any(c in credential[key] for c in "\r\n"):
            raise ValueError("operator credential requires server, target_id and token strings")
    credential["server"] = validate_url(credential["server"])
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", credential["target_id"]):
        raise ValueError("operator target_id is invalid")
    access = credential.get("access")
    if access is not None and (not isinstance(access, dict) or set(access) != {"client_id", "client_secret", "public_url"}
            or any(not isinstance(value, str) or not value or "\r" in value or "\n" in value for value in access.values())):
        raise ValueError("operator access requires client_id, client_secret and public_url")
    if access is not None:
        validate_url(access["public_url"])
    return credential


def _require_rq(credential):
    profile = credential.get("profile_config")
    if profile is None:
        # Selecting this adapter opts into its sole supported execution profile.
        profile = json.loads(json.dumps(DEFAULT_PROFILE))
    if not isinstance(profile, dict) or profile.get("schema_version") != 1 or not isinstance(profile.get("backends"), dict):
        raise ValueError("RQ operator profile_config requires schema_version 1 and backends.rq")
    rq = profile["backends"].get("rq")
    if not isinstance(rq, dict) or rq.get("enabled") is not True or rq.get("profiles") != ["trusted_native"]:
        raise ValueError("RQ requires backends.rq enabled=true and profiles=['trusted_native']; isolation is unavailable")
    return {**credential, "profile_config": profile}


def _runner(credential):
    return Runner(credential["server"], credential["target_id"], credential["token"],
                  profile_config=credential["profile_config"], access=credential.get("access"))


def _validate_run(run):
    if checksum(run.spec) != run.spec_digest:
        raise svc.LabError("immutable RQ run specification checksum mismatch", 409)
    execution = run.spec["definition"]["execution"]
    if execution.get("backend") != "rq" or execution.get("profile") != "trusted_native":
        raise svc.LabError("RQ adapter requires frozen backend rq and profile trusted_native", 409)


def execute_run(run_id: str) -> dict:
    """Execute one exact target-owned run through the normal outbound runner."""
    credential = _require_rq(load_credentials())
    dispose_after_fork()
    with _bounded_db() as db:
        target = svc.authenticate_target(db, credential["target_id"], credential["token"])
        run = db.scalar(select(LabRun).where(LabRun.id == run_id, LabRun.target_id == target.id,
                                            LabRun.project_id == target.project_id))
        if run is None:
            raise svc.LabError("RQ run not found for operator target", 404)
        _validate_run(run)
        if run.state != "queued":
            return {"run_id": run_id, "claimed": False}
    runner = _runner(credential)
    try:
        return {"run_id": run_id, "claimed": bool(runner.run_once(run_id=run_id))}
    finally:
        runner.client.close()


def _enqueue(func, run_id, *, job_id):
    delivery = queue()
    try:
        return _enqueue_on_queue(delivery, func, run_id, job_id=job_id)
    finally:
        close = getattr(delivery.connection, "close", None)
        if close:
            close()


def _enqueue_on_queue(delivery, func, run_id, *, job_id):
    """Serialize terminal replacement and use RQ's atomic unique enqueue.

    Installed RQ 2.12 queue.py enqueue_call(unique=True) uses scripts.py's
    EXISTS/HSET/RPUSH Lua script, so competing dispatchers cannot overwrite an
    existing job. Only terminal deliveries are removed and replaced; the DB
    claim still decides whether any execution may begin.
    """
    with delivery.connection.lock("tinystudio:lab:rq-dispatch:"+job_id, timeout=15, blocking_timeout=2):
        previous = delivery.fetch_job(job_id)
        if previous is not None:
            status = previous.get_status()
            status = getattr(status, "value", status)
            if status in _ACTIVE:
                return False
            if status not in _TERMINAL:
                raise RuntimeError("RQ job has an unknown status; inspect the delivery before retrying")
            previous.delete()
        try:
            label = "Evidence operation" if getattr(func, "__module__", "").endswith(".operations") else "Laboratory run"
            delivery.enqueue_call(func, args=(run_id,), job_id=job_id, unique=True,
                                  timeout=JOB_TIMEOUT, result_ttl=3600, failure_ttl=86400,
                                  description=label+" "+run_id)
        except DuplicateJobError:
            return False
    return True


def dispatch_once(enqueue_callback=None, *, cancelled=lambda: False, timeout_seconds: float = 20) -> dict:
    """Advertise via HTTP, then dispatch committed due rows for exactly this target.

    No database truth is derived from Redis status. Missing delivery metadata
    is recreated; active delivery records are retained. A worker that dies
    before claiming leaves a queued run, and one that dies after claiming is
    recovered through the existing laboratory lease protocol.
    """
    credential = _require_rq(load_credentials())
    deadline = time.monotonic()+timeout_seconds
    def stopped():
        return cancelled() or time.monotonic() >= deadline
    if stopped():
        return {"selected": 0, "enqueued": 0, "existing": 0, "stopped": True}
    runner = _runner(credential)
    try:
        import httpx
        runner.client.timeout = httpx.Timeout(max(.1, min(10, deadline-time.monotonic())))
        runner.advertise()
    finally:
        runner.client.close()
    # Fresh sessions read committed work. No generic Task row or precommit
    # enqueue is involved, and no target/run lock survives into Redis calls.
    with _bounded_db() as db:
        target = svc.authenticate_target(db, credential["target_id"], credential["token"])
        # Idle targets must reconcile dead leased attempts even when there is
        # no other queued run to provoke a claim.
        svc.recover(db, target)
        query = select(LabRun).join(LabDispatch, LabDispatch.run_id == LabRun.id).where(
            LabRun.target_id == target.id, LabRun.project_id == target.project_id,
            LabRun.state == "queued", LabDispatch.available <= svc.now(),
            LabRun.spec["definition"]["execution"]["backend"].as_string() == "rq"
        ).order_by(LabDispatch.available).limit(100)
        pending = []
        for run in db.scalars(query):
            _validate_run(run)
            attempt_count = db.scalar(select(func.count()).select_from(LabAttempt).where(LabAttempt.run_id == run.id))
            identity = checksum({"run_id": run.id, "spec_digest": run.spec_digest, "attempt_count": attempt_count})
            pending.append((run.id, "lab-rq-"+identity))
    deliver = enqueue_callback or _enqueue
    result = {"selected": len(pending), "enqueued": 0, "existing": 0}
    for run_id, job_id in pending:
        if stopped():
            result["stopped"] = True
            break
        delivered = deliver(execute_run, run_id, job_id=job_id)
        # Injection callbacks conventionally return None after successful enqueue.
        result["existing" if delivered is False else "enqueued"] += 1
    return result
