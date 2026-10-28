"""Transactional laboratory service. Callers commit; dispatch cannot see uncommitted runs."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from tinystudio.db.models import new_id
from tinystudio.storage import ArtifactNotFound, storage, project_key
from .contracts import ExperimentDefinition, ResolvedRunSpec, canonical, checksum
from .models import LabArtifact, LabAttempt, LabDefinition, LabDispatch, LabEvent, LabRevision, LabRun, LabTarget

LEASE_SECONDS = 60
TERMINAL = {"succeeded", "failed", "cancelled", "lost", "recovery_required"}


class LabError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def runtime_versions() -> dict[str, str]:
    result = {"python": platform.python_version(), "os": platform.system(), "machine": platform.machine()}
    for name in ("numpy", "torch", "transformers", "pyarrow", "tokenizers", "peft", "trl", "accelerate",
                 "sentence-transformers", "scipy", "pint", "simpy", "ortools", "hypothesis", "fmpy", "pybamm", "pandapower", "torchrl", "tensordict", "pyserial", "pymodbus", "paho-mqtt"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return result


def implementation_manifest() -> dict:
    package = Path(__file__).parent.parent
    files = sorted(package.rglob("*.py"))
    return {"version": 1, "files": {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}


def implementation_digest() -> str:
    return checksum(implementation_manifest())


def catalog() -> dict:
    from copy import deepcopy
    import importlib.util
    from .catalog import catalog as adapters
    from .plugins import SCHEMAS, manifest_for, qualification_matrix
    methods = [
        ("learning.language", "Tiny transformer / recurrent language model", True),
        ("retrieval.index", "Lexical, structured and episode retrieval", True),
        ("learning.pretrained", "Pretrained full / LoRA / QLoRA / DPO", True),
        ("system.cosim", "Clock-coordinated system simulation", True),
        ("data.pipeline", "Scientific data preparation and grouped partitions", True),
        ("evaluation.compare", "Independent validation and paired comparison", True),
        ("deployment.benchmark", "Measured host inference and portable package", True),
        ("learning.language_rl", "Autoregressive tool-language PPO / GRPO", True),
        ("learning.timeseries", "Time-series learning", Path(__file__).with_name("timeseries.py").exists()),
        ("learning.multimodal", "Multimodal learning", Path(__file__).with_name("timeseries.py").exists()),
        ("learning.control_rl", "Environment policy learning", Path(__file__).with_name("rl.py").exists()),
        ("learning.tool_rl", "Categorical tool-bandit policy learning", Path(__file__).with_name("rl.py").exists()),
    ]
    items = adapters()
    method_rows = []
    for id_, name, implemented in methods:
        dependencies = (["torch", "transformers", "peft", "trl"] if id_ == "learning.pretrained" else
                        ["torch"] if id_.startswith("learning.") or id_ == "deployment.benchmark" else
                        ["pyarrow"] if id_ in {"data.pipeline", "system.cosim"} else [])
        missing = [dependency for dependency in dependencies if importlib.util.find_spec(dependency) is None]
        method_rows.append({"id": id_, "name": name, "implemented": implemented,
            "available": implemented and not missing, "dependencies": dependencies,
            "qualification": "unqualified", "reason": "Implementation pending" if not implemented else
            "Optional dependencies missing: " + ", ".join(missing) if missing else None,
            "qualification_reason": "Availability is not an execution receipt; method-specific selections have additional prerequisites."})
    method_rows = [{**row, "manifest": manifest_for(row)} for row in method_rows]
    return {"schema_version": 1, "enabled_increments": ["A", "B"], "adapters": items, "methods": method_rows,
            "qualification_matrix": qualification_matrix(items+method_rows),
            "contract_schemas": deepcopy(SCHEMAS),
            "domains": sorted({item["domain"] for item in items})}


def get_definition(db: Session, project: str, id_: str, *, lock: bool = False) -> LabDefinition:
    stmt = select(LabDefinition).where(LabDefinition.id == id_, LabDefinition.project_id == project)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise LabError("definition not found", 404)
    return row


def definition_dict(db: Session, row: LabDefinition) -> dict:
    revision = db.get(LabRevision, (row.id, row.revision))
    return {"id": row.id, "name": row.name, "revision": row.revision,
            "definition": revision.definition, "created": row.created.isoformat()}


def save_definition(db: Session, project: str, body: dict, *, id_: str | None = None, expected: int | None = None) -> dict:
    try:
        definition = ExperimentDefinition.model_validate(body).model_dump()
    except ValueError as exc:
        raise LabError(str(exc), 422) from exc
    if id_:
        row = get_definition(db, project, id_, lock=True)
        if row.revision != expected:
            raise LabError("definition changed; reload its current revision before saving", 409)
        row.revision += 1
        row.name = definition["name"]
    else:
        row = LabDefinition(project_id=project, name=definition["name"], revision=1)
        db.add(row)
        db.flush()
    db.add(LabRevision(definition_id=row.id, revision=row.revision, definition=definition, digest=checksum(definition)))
    db.flush()
    return definition_dict(db, row)


def event(db: Session, run: LabRun, kind: str, payload: dict | None = None) -> None:
    payload = payload or {}
    if len(canonical(payload)) > 65536:
        raise LabError("event payload exceeds 64 KiB; publish an artifact")
    run.next_seq += 1
    db.add(LabEvent(run_id=run.id, seq=run.next_seq, kind=kind[:80], payload=payload))


def get_run(db: Session, project: str, id_: str, *, lock: bool = False) -> LabRun:
    stmt = select(LabRun).where(LabRun.id == id_, LabRun.project_id == project)
    row = db.scalar(stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt)
    if row is None:
        raise LabError("run not found", 404)
    return row


def run_dict(db: Session, row: LabRun, *, detailed: bool = True) -> dict:
    result = {"id": row.id, "definition_id": row.definition_id, "revision": row.revision,
              "target_id": row.target_id, "state": row.state, "created": row.created.isoformat(),
              "spec_digest": row.spec_digest, "result": row.result, "error": row.error,
              "cancel_requested": row.cancel_requested, "parent_run_id": row.parent_run_id}
    if detailed:
        result["spec"] = row.spec
        result["attempts"] = [{"id": a.id, "number": a.number, "state": a.state,
                               "lease_until": a.lease_until.isoformat(), "runtime": a.runtime}
                              for a in db.scalars(select(LabAttempt).where(LabAttempt.run_id == row.id).order_by(LabAttempt.number))]
    return result


def resolve_target(db: Session, project: str, id_: str) -> LabTarget:
    if id_ == "local":
        target = db.scalar(select(LabTarget).where(LabTarget.project_id == project, LabTarget.name == "local"))
        if target is None:
            # Serialise implicit local creation on the project rather than race a unique constraint.
            from tinystudio.db.models import Project
            db.scalar(select(Project).where(Project.id == project).with_for_update())
            target = db.scalar(select(LabTarget).where(LabTarget.project_id == project, LabTarget.name == "local"))
            if target is None:
                target = LabTarget(project_id=project, name="local", capabilities={})
                db.add(target)
                db.flush()
        return target
    target = db.scalar(select(LabTarget).where(LabTarget.id == id_, LabTarget.project_id == project))
    if target is None:
        raise LabError("execution target not found", 404)
    return target


def target_dict(target: LabTarget) -> dict:
    return {"id": target.id, "name": target.name, "capabilities": target.capabilities,
            "enabled": target.enabled, "available": bool(target.last_seen and aware(target.last_seen) > now()-timedelta(seconds=90)),
            "last_seen": target.last_seen.isoformat() if target.last_seen else None}


def enroll(db: Session, project: str, name: str) -> dict:
    if not name.strip() or len(name) > 200:
        raise LabError("target name must contain 1–200 characters")
    target = db.scalar(select(LabTarget).where(LabTarget.project_id == project, LabTarget.name == name).with_for_update(key_share=True))
    if target is None:
        target = LabTarget(project_id=project, name=name, capabilities={})
        db.add(target)
    elif db.scalar(select(LabAttempt.id).where(LabAttempt.target_id == target.id, LabAttempt.state == "running").limit(1)):
        raise LabError("cancel or recover active attempts before rotating this target credential", 409)
    token = "tsr-" + secrets.token_urlsafe(36)
    target.token_hash = secret_hash(token)
    db.flush()
    return {**target_dict(target), "token": token}


def authenticate_target(db: Session, id_: str, token: str) -> LabTarget:
    target = db.scalar(select(LabTarget).where(LabTarget.id == id_).with_for_update(key_share=True))
    if target is None or not target.enabled or not target.token_hash or not secrets.compare_digest(target.token_hash, secret_hash(token)):
        raise LabError("invalid execution target credential", 401)
    return target


def lock_target(db: Session, target: LabTarget) -> LabTarget:
    """All lease operations acquire target then run locks, including direct service callers."""
    target = db.scalar(select(LabTarget).where(LabTarget.id == target.id).with_for_update(key_share=True).execution_options(populate_existing=True))
    if target is None or not target.enabled:
        raise LabError("execution target is disabled", 403)
    return target


def preflight(db: Session, project: str, definition: dict, target_id: str = "local") -> dict:
    """Read-only check: no target enrollment, definition revision, run or grant is created."""
    from .preflight import execution_checks
    try:
        body = ExperimentDefinition.model_validate(definition)
    except ValueError as exc:
        raise LabError(str(exc), 422) from exc
    target = db.scalar(select(LabTarget).where(LabTarget.project_id == project,
        LabTarget.name == "local" if target_id == "local" else LabTarget.id == target_id))
    if target is None and target_id != "local":
        raise LabError("execution target not found", 404)
    capabilities = target.capabilities if target else {}
    inferred = not capabilities and target_id == "local"
    if inferred:
        from .runner import capabilities as local_capabilities
        capabilities = local_capabilities()
    checks = execution_checks(body, capabilities)
    trusted_manifest = []
    for ref in body.inputs:
        try:
            artifact = get_artifact(db, project, ref.artifact_id)
            if artifact.sha256 != ref.sha256:
                raise LabError("input digest does not match immutable artifact")
            validate_visibility(body, ref, artifact)
            trusted_manifest.append(artifact_dict(artifact))
        except LabError as exc:
            checks.append({"code": "input", "artifact_id": ref.artifact_id, "passed": False, "message": str(exc)})
        else:
            checks.append({"code": "input", "artifact_id": ref.artifact_id, "passed": True, "message": "Project-scoped immutable input matches."})
    from .leakage import selection_integrity
    integrity = selection_integrity(body.model_dump(), trusted_manifest)
    checks.extend(integrity["checks"])
    online = bool(target and target.enabled and target.last_seen and aware(target.last_seen) > now()-timedelta(seconds=90))
    if target and not target.enabled:
        checks.append({"code": "target_enabled", "passed": False, "message": "Selected execution target is disabled."})
    return {"schema_version": 1, "compatible": all(item["passed"] for item in checks), "target_online": online,
            "target_id": target.id if target else "local", "capability_source": "server_runtime_preview" if inferred else "runner_advertisement",
            "checks": checks, "integrity": integrity, "qualification": "unqualified", "execution_started": False,
            "limitations": ["Checks do not execute the engine or qualify its scientific fidelity.",
                            "Bindings, input contents and engine-specific settings are validated by the executor."]}


def validate_visibility(body: ExperimentDefinition, reference, artifact: LabArtifact) -> None:
    if artifact.visibility != "model" and reference.visibility == "model" and not any(
            override.get("kind") == "input.visibility_override" and override.get("artifact_id") == artifact.id
            and isinstance(override.get("reason"), str) and override["reason"].strip() for override in body.research_overrides):
        raise LabError("evaluation/truth evidence cannot become model input without a recorded visibility research override")


def submit(db: Session, project: str, definition_id: str, revision: int, target_id: str, key: str,
           parent_id: str | None = None, *, not_before: datetime | None = None) -> LabRun:
    if not key or len(key) > 120:
        raise LabError("idempotency_key must contain 1–120 characters")
    if not_before is not None and (not_before.tzinfo is None or not_before.utcoffset() is None):
        raise LabError("scheduled execution requires an explicit timezone")
    from datetime import timezone
    scheduled = {"not_before": not_before.astimezone(timezone.utc).isoformat()} if not_before is not None else {}
    # Project lock also closes the concurrent idempotency-key race.
    from tinystudio.db.models import Project
    db.scalar(select(Project).where(Project.id == project).with_for_update())
    existing = db.scalar(select(LabRun).where(LabRun.project_id == project, LabRun.idempotency_key == key))
    target = resolve_target(db, project, target_id)
    if existing:
        if (existing.definition_id, existing.revision, existing.target_id, existing.parent_run_id) != (definition_id, revision, target.id, parent_id):
            raise LabError("idempotency key already used for a different submission", 409)
        if existing.spec.get("schedule", {}) != scheduled:
            raise LabError("idempotency key already used for a different schedule", 409)
        return existing
    get_definition(db, project, definition_id)
    rev = db.get(LabRevision, (definition_id, revision))
    if rev is None:
        raise LabError("definition revision not found", 404)
    if checksum(rev.definition) != rev.digest:
        raise LabError("definition integrity check failed", 409)
    body = ExperimentDefinition.model_validate(rev.definition)
    compatibility, linked_job, linked_experiment = {}, None, None
    requested_link = body.parameters.get("compatibility", {})
    if not isinstance(requested_link, dict):
        raise LabError("compatibility references must be an object")
    if requested_link:
        from tinystudio.db.models import Job, Experiment, Snapshot
        if requested_link.get("job_id"):
            linked_job = db.scalar(select(Job).where(Job.id == requested_link["job_id"], Job.project_id == project))
            if linked_job is None:
                raise LabError("compatibility Job not found in this project", 404)
            compatibility = {"relationship": "source_reference", "job_id": linked_job.id,
                             "snapshot_id": linked_job.snapshot_id, "snapshot_name": linked_job.snapshot_name}
            if requested_link.get("experiment_id") or requested_link.get("experiment_number"):
                statement = select(Experiment).where(Experiment.job_id == linked_job.id)
                statement = statement.where(Experiment.id == requested_link["experiment_id"]) if requested_link.get("experiment_id") else statement.where(Experiment.number == requested_link["experiment_number"])
                linked_experiment = db.scalar(statement)
                if linked_experiment is None:
                    raise LabError("compatibility Experiment not found in this Job", 404)
                compatibility.update(experiment_id=linked_experiment.id, experiment_number=linked_experiment.number,
                                     experiment_config=linked_experiment.config, experiment_config_sha256=checksum(linked_experiment.config))
    if parent_id:
        get_run(db, project, parent_id)
    manifest = []
    for reference in body.inputs:
        artifact = get_artifact(db, project, reference.artifact_id)
        if artifact.sha256 != reference.sha256:
            raise LabError("input digest does not match immutable artifact", 409)
        validate_visibility(body, reference, artifact)
        manifest.append(artifact_dict(artifact))
    from .leakage import selection_integrity
    integrity = selection_integrity(body.model_dump(), manifest)
    if not integrity["passed"]:
        raise LabError("; ".join(item["message"] for item in integrity["checks"] if not item["passed"]), 409)
    if body.operating_mode == "physical_control":
        raise LabError("physical control requires a qualified gateway ownership adapter; no physical endpoint is bound")
    backend, profile = body.execution.backend, body.execution.profile
    profiles = target.capabilities.get("profiles_by_backend", {"native": ["trusted_native"]})
    if profile not in profiles.get(backend, []):
        raise LabError("selected target has not advertised this backend/profile; configure its runner and publish the required isolation/runtime capabilities")
    resolved_runtime = target.capabilities.get("runtime_by_backend", {}).get(backend)
    if resolved_runtime is None and backend == "native":
        resolved_runtime = target.capabilities.get("runtime") or (runtime_versions() if target.name == "local" else None)
    if not resolved_runtime:
        raise LabError("start the selected runner to resolve its runtime before submitting this experiment")
    if body.execution.grants:
        raise LabError("machine grants must be configured by the execution host operator; authored definitions cannot grant host authority")
    if (body.components or body.connections) and body.engine != "system.cosim":
        raise LabError("graph authored successfully, but execution requires a qualified graph/clock coordinator; select an executable single-engine definition")
    report = preflight(db, project, body.model_dump(), target_id)
    if not report["compatible"]:
        raise LabError("; ".join(item["message"] for item in report["checks"] if not item["passed"]), 409)
    run_id = new_id()
    spec = ResolvedRunSpec(definition=body, definition_id=definition_id, revision=revision,
                          implementation=implementation_manifest(), input_manifest=manifest,
                          runtime=resolved_runtime, research_identity=run_id,
                          compatibility=compatibility, schedule=scheduled, integrity=integrity).model_dump()
    run = LabRun(id=run_id, project_id=project, definition_id=definition_id, revision=revision, target_id=target.id,
                 idempotency_key=key, spec=spec, spec_digest=checksum(spec), state="queued", next_seq=0,
                 cancel_requested=False, parent_run_id=parent_id, job_id=linked_job.id if linked_job else None,
                 experiment_id=linked_experiment.id if linked_experiment else None)
    db.add(run)
    db.flush()
    db.add(LabDispatch(run_id=run.id, available=not_before or now()))
    event(db, run, "submitted", {"spec_digest": run.spec_digest, "target_id": target.id, "schedule": scheduled})
    db.flush()
    return run


def recover(db: Session, target: LabTarget) -> int:
    target = lock_target(db, target)
    attempts = list(db.scalars(select(LabAttempt).where(LabAttempt.target_id == target.id,
                        LabAttempt.state == "running", LabAttempt.lease_until < now())))
    count = 0
    for stale in attempts:
        run = get_run(db, target.project_id, stale.run_id, lock=True)
        db.refresh(stale)
        if stale.state != "running" or aware(stale.lease_until) >= now():
            continue
        uncertain = run.spec["definition"]["operating_mode"] in {"physical_control", "acquisition"}
        stale.state = "recovery_required" if uncertain else "lost"
        run.state = stale.state
        run.error = "runner lease expired; execution outcome is unconfirmed"
        event(db, run, "lease_expired", {"attempt_id": stale.id, "recovery_required": uncertain})
        count += 1
    return count


def claim(db: Session, target: LabTarget, capabilities: dict, *, run_id: str | None = None) -> dict | None:
    from .preflight import backend_engines
    target = lock_target(db, target)
    if len(canonical(capabilities)) > 65536:
        raise LabError("capability manifest too large")
    target.capabilities = capabilities
    target.last_seen = now()
    recover(db, target)
    statement = select(LabRun).join(LabDispatch, LabDispatch.run_id == LabRun.id).where(
        LabRun.target_id == target.id, LabRun.state == "queued", LabDispatch.available <= now())
    if run_id is not None:
        statement = statement.where(LabRun.id == run_id)
    pending = list(db.scalars(statement.order_by(LabDispatch.available)
                              .with_for_update(skip_locked=True, of=LabRun).limit(100)))
    for run in pending:
        if checksum(run.spec) != run.spec_digest:
            run.state = "failed"
            run.error = "immutable run specification checksum mismatch"
            db.execute(delete(LabDispatch).where(LabDispatch.run_id == run.id))
            event(db, run, "failed", {"error": run.error})
            continue
        reason = None
        if checksum(run.spec["implementation"]) != capabilities.get("implementation_digest"):
            reason = "runner implementation differs from submitted specification"
        elif run.spec["definition"]["engine"] not in backend_engines(capabilities, run.spec["definition"]["execution"]["backend"]):
            reason = "runner does not advertise selected engine"
        elif run.spec["definition"]["execution"]["profile"] not in capabilities.get("profiles_by_backend", {"native": ["trusted_native"]}).get(run.spec["definition"]["execution"]["backend"], []):
            reason = "runner no longer advertises the selected backend/profile"
        elif run.spec["runtime"] != capabilities.get("runtime_by_backend", {}).get(run.spec["definition"]["execution"]["backend"], capabilities.get("runtime") if run.spec["definition"]["execution"]["backend"] == "native" else None):
            reason = "runner runtime differs from submitted dependency manifest"
        if reason is None:
            from .preflight import mismatch
            reason = mismatch(run.spec["definition"], capabilities)
        if reason:
            if run.error != reason:
                run.error = reason
                event(db, run, "capability_mismatch", {"reason": reason})
            continue
        number = (db.scalar(select(func.max(LabAttempt.number)).where(LabAttempt.run_id == run.id)) or 0) + 1
        token = secrets.token_urlsafe(36)
        attempt = LabAttempt(run_id=run.id, target_id=target.id, number=number, token_hash=secret_hash(token),
                             lease_until=now()+timedelta(seconds=LEASE_SECONDS), runtime=capabilities, state="running")
        db.add(attempt)
        run.state, run.error = "running", None
        db.execute(delete(LabDispatch).where(LabDispatch.run_id == run.id))
        db.flush()
        event(db, run, "started", {"attempt_id": attempt.id, "number": number})
        return {"run_id": run.id, "attempt_id": attempt.id, "lease_token": token, "lease_seconds": LEASE_SECONDS,
                "spec": run.spec, "spec_digest": run.spec_digest}
    return None


def attempt_authority(db: Session, target: LabTarget, id_: str, token: str, *, terminal: bool = False) -> tuple[LabRun, LabAttempt]:
    lock_target(db, target)
    attempt = db.get(LabAttempt, id_)
    if attempt is None or attempt.target_id != target.id or not secrets.compare_digest(attempt.token_hash, secret_hash(token)):
        raise LabError("invalid attempt authority", 403)
    run = get_run(db, target.project_id, attempt.run_id, lock=True)
    db.refresh(attempt)
    if attempt.state == "running" and aware(attempt.lease_until) <= now():
        raise LabError("attempt lease expired; stale execution may not publish", 409)
    if not terminal and (attempt.state != "running" or run.state != "running"):
        raise LabError("attempt no longer owns this run", 409)
    return run, attempt


def heartbeat(db: Session, target: LabTarget, attempt_id: str, token: str, events: list[dict]) -> dict:
    if len(events) > 100:
        raise LabError("at most 100 events per heartbeat")
    for item in events:
        if not isinstance(item, dict) or not isinstance(item.get("kind", "progress"), str) or item.get("kind", "progress").startswith("server."):
            raise LabError("runner events cannot use the reserved server namespace")
    run, attempt = attempt_authority(db, target, attempt_id, token)
    attempt.lease_until = now()+timedelta(seconds=LEASE_SECONDS)
    target.last_seen = now()
    for item in events:
        event(db, run, item.get("kind", "progress"), item.get("payload", item))
    return {"cancel_requested": run.cancel_requested, "lease_seconds": LEASE_SECONDS}


def cancel(db: Session, project: str, id_: str) -> LabRun:
    run = get_run(db, project, id_, lock=True)
    if run.state in TERMINAL:
        return run
    run.cancel_requested = True
    if run.state == "queued":
        previous = db.scalar(select(LabAttempt.id).where(LabAttempt.run_id == run.id)
                             .order_by(LabAttempt.number.desc()).limit(1))
        run.state = "cancelled"
        db.execute(delete(LabDispatch).where(LabDispatch.run_id == run.id))
        event(db, run, "server.queued_cancelled", {"attempt_id": None, "after_attempt_id": previous})
    event(db, run, "cancel_requested")
    return run


def _canonical_result(db, project, attempt_id, result):
    if result is None:
        return None
    if not isinstance(result, dict) or len(canonical(result)) > 1024**2:
        raise LabError("result must be an object of at most 1 MiB; publish evidence as artifacts")
    refs = result.get("artifacts", [])
    if not isinstance(refs, list):
        raise LabError("result artifacts must be a list")
    canonical_artifacts, seen, names = [], set(), set()
    for item in refs:
        if not isinstance(item, dict):
            raise LabError("result artifact reference must be an object")
        artifact = get_artifact(db, project, item.get("id", ""))
        if artifact.attempt_id != attempt_id or item.get("sha256") != artifact.sha256 or artifact.role == "attempt_outcome":
            raise LabError("result references evidence outside this attempt", 409)
        if artifact.id in seen or artifact.name in names:
            raise LabError("result manifest contains duplicate artifact identities or names", 409)
        canonical_artifacts.append(artifact_dict(artifact))
        seen.add(artifact.id)
        names.add(artifact.name)
    normalized = {**result, "artifacts": canonical_artifacts} if "artifacts" in result else dict(result)
    if len(canonical(normalized)) > 1024**2:
        raise LabError("canonical result exceeds 1 MiB; publish evidence as artifacts")
    return normalized


def _verify_result(db, project, result, directory):
    for item in (result or {}).get("artifacts", []):
        materialize(get_artifact(db, project, item["id"]), directory)


def _outcome_document(run, attempt_id, state, result, error):
    return {"schema_version": 1, "kind": "tinystudio.attempt_outcome", "run_id": run.id,
            "attempt_id": attempt_id, "spec_digest": run.spec_digest, "state": state,
            "result": result, "error": error[:8192] if error else None}


def retry(db: Session, project: str, id_: str) -> LabRun:
    # Legacy outcomes may predate retained outcome artifacts. Prepare their
    # bounded archive before taking the run lock; never commit a caller's txn.
    current = get_run(db, project, id_)
    if current.state == "queued":
        return current
    if current.state not in {"failed", "lost", "cancelled"}:
        raise LabError("only failed, lost, or cancelled runs may retry; uncertain physical outcomes require recovery", 409)
    if checksum(current.spec) != current.spec_digest:
        raise LabError("immutable specification checksum mismatch", 409)
    snapshot = checksum({"state": current.state, "result": current.result, "error": current.error, "next_seq": current.next_seq})
    attempt = db.scalar(select(LabAttempt).where(LabAttempt.run_id == current.id).order_by(LabAttempt.number.desc()).limit(1))
    attempt_id = attempt.id if attempt else None
    if current.state == "cancelled":
        cancelled = db.scalar(select(LabEvent).where(LabEvent.run_id == current.id,
            LabEvent.kind == "server.queued_cancelled").order_by(LabEvent.seq.desc()).limit(1))
        completed = db.scalar(select(LabEvent).where(LabEvent.run_id == current.id,
            LabEvent.kind == "server.outcome").order_by(LabEvent.seq.desc()).limit(1))
        retried = db.scalar(select(func.max(LabEvent.seq)).where(LabEvent.run_id == current.id,
            LabEvent.kind == "retry_requested")) or 0
        # A queued cancellation owns no execution attempt, even if the previous
        # attempt also ended cancelled. Legacy ambiguous cycles stay unknown.
        queued_cycle = (cancelled is not None and cancelled.payload.get("after_attempt_id") == attempt_id
                        and (completed is None or cancelled.seq > completed.seq))
        current_execution = (completed is not None and completed.seq > retried
                             and completed.payload.get("attempt_id") == attempt_id
                             and completed.payload.get("state") == "cancelled")
        if queued_cycle or not current_execution:
            attempt_id = None
    result = _canonical_result(db, project, attempt_id, current.result)
    document = _outcome_document(current, attempt_id, current.state, result, current.error)
    with tempfile.TemporaryDirectory(prefix="ts-lab-outcome-") as root:
        path = Path(root) / "attempt-outcome.json"
        path.write_bytes(canonical(document))
        _verify_result(db, project, result, Path(root))
        verified = persist_verified(path, project, "application/json")
        run = get_run(db, project, id_, lock=True)
        db.refresh(run)
        if run.state == "queued":
            return run
        if checksum({"state": run.state, "result": run.result, "error": run.error, "next_seq": run.next_seq}) != snapshot:
            raise LabError("run outcome changed during retry preparation", 409)
        retained = next((ref for ref in outcome_references(db, run.id)
                         if ref.get("attempt_id") == attempt_id and ref.get("sha256") == verified["sha256"]), None)
        archive = get_artifact(db, project, retained["outcome_artifact_id"]) if retained else None
        if archive is None:
            archive = store_artifact(db, project, path, "attempt-outcome.json", "application/json", "attempt_outcome",
                                     attempt_id, already_stored=True, visibility="evaluation", verified=verified)
            event(db, run, "server.outcome", {"attempt_id": attempt_id, "state": run.state,
                "outcome_artifact_id": archive.id, "sha256": archive.sha256})
        run.state, run.cancel_requested, run.error, run.result = "queued", False, None, None
        not_before = run.spec.get("schedule", {}).get("not_before")
        db.add(LabDispatch(run_id=run.id, available=datetime.fromisoformat(not_before) if not_before else now()))
        event(db, run, "retry_requested", {"spec_digest": run.spec_digest})
        return run


def finish(db: Session, target: LabTarget, attempt_id: str, token: str, state: str, result: dict | None, error: str | None) -> dict:
    if state not in {"succeeded", "failed", "cancelled"}:
        raise LabError("invalid terminal state")
    payload_digest = checksum({"state": state, "result": result, "error": error})
    # Read identity without acquiring the target/run authority locks. Slow
    # storage verification must not prevent a heartbeat extending the lease.
    attempt = db.get(LabAttempt, attempt_id)
    if attempt is None or attempt.target_id != target.id or not secrets.compare_digest(attempt.token_hash, secret_hash(token)):
        raise LabError("invalid attempt authority", 403)
    run = get_run(db, target.project_id, attempt.run_id)
    if attempt.state != "running":
        # Recheck under the normal locks even for an idempotent completion.
        _, ended = attempt_authority(db, target, attempt_id, token, terminal=True)
        if ended.result_digest == payload_digest:
            return {"accepted": True, "state": ended.state}
        raise LabError("attempt already ended with a different outcome", 409)
    if aware(attempt.lease_until) <= now():
        raise LabError("attempt lease expired; stale execution may not publish", 409)
    if checksum(run.spec) != run.spec_digest:
        raise LabError("immutable specification checksum mismatch", 409)
    canonical_result = _canonical_result(db, run.project_id, attempt.id, result)
    if state == "succeeded" and (not canonical_result or not canonical_result.get("artifacts")):
        raise LabError("success requires a complete evidence manifest")
    with tempfile.TemporaryDirectory(prefix="ts-lab-outcome-") as root:
        directory = Path(root)
        _verify_result(db, run.project_id, canonical_result, directory)
        document = _outcome_document(run, attempt.id, state, canonical_result, error)
        path = directory / "attempt-outcome.json"
        path.write_bytes(canonical(document))
        verified = persist_verified(path, run.project_id, "application/json")
        # Only publication holds authority locks; repeat all mutable ownership
        # checks after verification, including cancellation and lease expiry.
        run, attempt = attempt_authority(db, target, attempt_id, token, terminal=True)
        if attempt.state != "running":
            if attempt.result_digest == payload_digest:
                return {"accepted": True, "state": attempt.state}
            raise LabError("attempt already ended with a different outcome", 409)
        if run.state != "running":
            raise LabError("attempt no longer owns this run", 409)
        if run.spec_digest != document["spec_digest"] or checksum(run.spec) != run.spec_digest:
            raise LabError("immutable specification changed during completion verification", 409)
        if state == "succeeded" and run.cancel_requested:
            raise LabError("cancellation requested; completion cannot publish success", 409)
        # Refresh/canonicalize metadata after the unlocked transfer boundary.
        final_result = _canonical_result(db, run.project_id, attempt.id, result)
        if final_result != canonical_result:
            raise LabError("artifact metadata changed during completion verification", 409)
        archive = store_artifact(db, run.project_id, path, "attempt-outcome.json", "application/json", "attempt_outcome",
                                 attempt.id, already_stored=True, visibility="evaluation", verified=verified)
        attempt.state, attempt.result_digest = state, payload_digest
        run.state, run.result, run.error = state, final_result, error[:8192] if error else None
        outcome = {"attempt_id": attempt.id, "state": state, "outcome_artifact_id": archive.id, "sha256": archive.sha256}
        if run.error:
            outcome["error"] = run.error
        event(db, run, "server.outcome", outcome)
        event(db, run, state, {"attempt_id": attempt.id, **({"error": run.error} if run.error else {})})
    return {"accepted": True, "state": state}


def outcome_references(db, run_id):
    """Only server-reserved events establish a retained outcome association."""
    return [row.payload for row in db.scalars(select(LabEvent).where(
        LabEvent.run_id == run_id, LabEvent.kind == "server.outcome").order_by(LabEvent.seq))]


def artifact_dict(row: LabArtifact) -> dict:
    return {"id": row.id, "name": row.name, "sha256": row.sha256, "size": row.size,
            "media_type": row.media_type, "role": row.role, "visibility": row.visibility}


def get_artifact(db: Session, project: str, id_: str) -> LabArtifact:
    row = db.scalar(select(LabArtifact).where(LabArtifact.id == id_, LabArtifact.project_id == project).execution_options(populate_existing=True))
    if row is None:
        raise LabError("artifact not found", 404)
    return row


def _verify_object(identity: dict, directory: Path) -> Path:
    from .engine import digest
    path = directory / identity["sha256"]
    try:
        storage().download_file(identity["storage_key"], path)
        if digest(path) != identity["sha256"]:
            raise LabError("stored artifact checksum mismatch", 409)
        if path.stat().st_size != identity["size"]:
            raise LabError("stored artifact size mismatch", 409)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def persist_verified(path: Path, project: str, media_type: str) -> dict:
    """Upload and read back exact bytes before a caller acquires publication locks."""
    from .engine import digest
    sha = digest(path)
    identity = {"sha256": sha, "size": path.stat().st_size,
                "storage_key": project_key(project, "lab", sha[:2], sha)}
    with tempfile.TemporaryDirectory(prefix="ts-lab-verify-") as root:
        try:
            _verify_object(identity, Path(root))
            return identity  # Preserve already retained CAS bytes; no rewrite.
        except (ArtifactNotFound, LabError):
            pass  # Missing or verified-corrupt object may be repaired.
        storage().upload_file(path, identity["storage_key"], media_type)
        _verify_object(identity, Path(root))
    return identity


def store_artifact(db: Session, project: str, path: Path, name: str, media_type: str, role: str = "source", attempt_id: str | None = None,
                   *, already_stored: bool = False, visibility: str = "model", verified: dict | None = None) -> LabArtifact:
    from .engine import digest
    validate_artifact_name(name)
    if visibility not in {"model", "evaluation", "truth"}:
        raise LabError("invalid artifact visibility")
    sha = digest(path)
    identity = {"sha256": sha, "size": path.stat().st_size,
                "storage_key": project_key(project, "lab", sha[:2], sha)}
    if verified is not None:
        if verified != identity:
            raise LabError("verified artifact identity differs from local bytes", 409)
    elif not already_stored:
        identity = persist_verified(path, project, media_type)
    else:
        # Compatibility callers cannot turn an upload ACK into proof.
        with tempfile.TemporaryDirectory(prefix="ts-lab-verify-") as root:
            _verify_object(identity, Path(root))
    row = LabArtifact(project_id=project, attempt_id=attempt_id, name=name, sha256=identity["sha256"], size=identity["size"],
                      media_type=media_type[:120], role=role[:64], storage_key=identity["storage_key"], visibility=visibility)
    db.add(row)
    db.flush()
    return row


def validate_artifact_name(name: str) -> str:
    """A portable logical label, never a host storage path."""
    if (not isinstance(name, str) or not name or len(name) > 255
            or PurePosixPath(name).is_absolute() or "\\" in name or ":" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise LabError("artifact name must be a safe relative POSIX path without traversal")
    return name


def materialize(row: LabArtifact, directory: Path) -> Path:
    return _verify_object({"sha256": row.sha256, "size": row.size, "storage_key": row.storage_key}, directory)


def records(db: Session, project: str, run_id: str, name: str, offset: int, limit: int) -> dict:
    run = get_run(db, project, run_id)
    if not run.result:
        return {"records": [], "next_offset": None}
    selected = next((a for a in run.result.get("artifacts", []) if a["name"] == name), None)
    if not selected:
        return {"records": [], "next_offset": None}
    if not name.endswith(".jsonl"):
        raise LabError("record inspection requires a JSONL artifact")
    row = get_artifact(db, project, selected["id"])
    with tempfile.TemporaryDirectory(prefix="ts-lab-read-") as root:
        path = materialize(row, Path(root))
        output = []
        more = False
        with path.open() as stream:
            for index, line in enumerate(iter(lambda: stream.readline(1024**2+1), "")):
                if len(line) > 1024**2:
                    raise LabError("record exceeds inspection limit; download original artifact")
                if index < offset:
                    continue
                if len(output) >= limit:
                    more = True
                    break
                output.append(json.loads(line))
        return {"records": output, "next_offset": offset+len(output) if more else None,
                "artifact": artifact_dict(row)}
