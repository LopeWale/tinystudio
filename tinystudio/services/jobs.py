"""Fine-tuning jobs and experiments: rows in PostgreSQL, weights under the data directory."""

from __future__ import annotations

import shutil
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tinystudio.config import config
from tinystudio.storage import project_key, storage
from tinystudio.core.project import ProjectError, check_name, iso
from tinystudio.db import session
from tinystudio.db.models import Analysis, Experiment, ExperimentLog, Job, Project
from tinystudio.services import snapshots as snapshots_svc
from tinystudio.training.train import TrainConfig, alias_for, checkpoints_on_disk, export_merged, parse_experiment_ref, train_experiment


def job_dict(job: Job) -> dict[str, Any]:
    return {"id": job.id, "name": job.name, "snapshot": job.snapshot_name, "goal": job.goal, "reasoning": job.reasoning, "created": iso(job.created), "experiments": [e.number for e in job.experiments]}


def exp_path(exp: Experiment) -> Path:
    return config().data_dir / exp.artifact_dir


def ensure_experiment_artifacts(exp: Experiment, step: str = "") -> Path:
    """Restore published weights into this worker's local cache when absent."""
    path = exp_path(exp)
    status = exp.status or {}
    if step:
        target = path / "checkpoints" / f"checkpoint-{step}"
        record = next((c for c in status.get("artifact_checkpoints", []) if str(c["step"]) == step), None)
        if record:
            storage().hydrate_directory(record["prefix"], target)
        elif status.get("artifact_prefix"):
            storage().hydrate_directory(status["artifact_prefix"], path)
    elif status.get("artifact_prefix"):
        # The manifest verifies every required file even after a partial cache loss.
        storage().hydrate_directory(status["artifact_prefix"], path)
    return path


def experiment_checkpoint_rows(exp: Experiment) -> list[dict[str, Any]]:
    local = {c["step"]: c for c in checkpoints_on_disk(exp_path(exp))}
    for item in (exp.status or {}).get("artifact_checkpoints", []):
        local.setdefault(item["step"], {k: v for k, v in item.items() if k != "prefix"})
    return sorted(local.values(), key=lambda c: c["step"])


def experiment_dict(exp: Experiment, job_name: str | None = None) -> dict[str, Any]:
    name = job_name or exp.job.name
    return {"id": exp.id, "number": exp.number, "alias": exp.alias, "job": name, "state": exp.state, "created": iso(exp.created), "updated": iso(exp.updated), "task_id": exp.task_id, **{k: v for k, v in (exp.status or {}).items() if k not in ("state", "number", "job")}, "config": exp.config, "checkpoints": [c["step"] for c in experiment_checkpoint_rows(exp)], "model_id": f"{name}/{exp.number}"}


def get_job(db: Session, project: Project, name: str) -> Job:
    job = db.scalar(select(Job).where(Job.project_id == project.id, Job.name == name))
    if job is None:
        raise ProjectError(f"job {name!r} does not exist")
    return job


def list_jobs(db: Session, project: Project) -> list[dict[str, Any]]:
    return [{"job": job_dict(job), "experiments": [experiment_dict(e, job.name) for e in job.experiments]} for job in db.scalars(select(Job).where(Job.project_id == project.id).order_by(Job.created))]


def create_job(db: Session, project: Project, name: str, snapshot: str, goal: str = "", reasoning: bool = False) -> dict[str, Any]:
    check_name(name, "job")
    snap = snapshots_svc.get_snapshot(db, project, snapshot)
    if db.scalar(select(Job.id).where(Job.project_id == project.id, Job.name == name)) is not None:
        raise ProjectError(f"job {name!r} already exists; add an experiment to it")
    job = Job(project_id=project.id, name=name, snapshot_id=snap.id, snapshot_name=snap.name, goal=goal or project.goal, reasoning=reasoning)
    db.add(job)
    db.flush()
    return job_dict(job)


def delete_job(db: Session, project: Project, name: str) -> None:
    job = get_job(db, project, name)
    if any(e.state == "running" for e in job.experiments):
        raise ProjectError(f"job {name!r} has a running experiment; stop its task first")
    dirs = [exp_path(e) for e in job.experiments]
    merged_dirs = [directory for exp in job.experiments for directory in _merged_cache_paths(exp)]
    backend = storage()
    for exp in job.experiments:
        if backend.remote or (exp.status or {}).get("artifact_prefix") or (exp.status or {}).get("artifact_checkpoints"):
            backend.delete_prefix(project_key(project.id, "models", exp.id))
    db.delete(job)
    db.flush()
    for d in [*dirs, *merged_dirs]:
        shutil.rmtree(d, ignore_errors=True)


def get_experiment(db: Session, project: Project, job_name: str, number: int) -> Experiment:
    job = get_job(db, project, job_name)
    exp = db.scalar(select(Experiment).where(Experiment.job_id == job.id, Experiment.number == number))
    if exp is None:
        raise ProjectError(f"experiment {job_name}/{number} does not exist")
    return exp


def find_experiment(db: Session, project: Project, reference: str) -> Experiment:
    """'job/number' or 'ft-job-number'."""
    job_name, number = parse_experiment_ref(reference.partition("@")[0])
    return get_experiment(db, project, job_name, number)


def add_experiment(db: Session, project: Project, job_name: str, cfg: TrainConfig) -> Experiment:
    """Register an experiment without training it (Studio's 'configure, then Start Experiments')."""
    job = get_job(db, project, job_name)
    if cfg.refinetune_from:
        parent = find_experiment(db, project, cfg.refinetune_from)
        if parent.state != "succeeded":
            raise ProjectError(f"refinetune_from: experiment {cfg.refinetune_from!r} has not succeeded")
    number = int(db.scalar(select(func.coalesce(func.max(Experiment.number), 0)).where(Experiment.job_id == job.id))) + 1
    exp = Experiment(job_id=job.id, number=number, alias=alias_for(job.name, number), config=cfg.as_dict(), state="pending", status={"created": iso(datetime.now(timezone.utc)), "snapshot": job.snapshot_name}, artifact_dir=f"projects/{project.id}/experiments/{job.id}/{number}")
    db.add(exp)
    db.flush()
    exp_path(exp).mkdir(parents=True, exist_ok=True)
    return exp


def delete_experiment(db: Session, project: Project, job_name: str, number: int) -> None:
    exp = get_experiment(db, project, job_name, number)
    if exp.state == "running":
        raise ProjectError(f"experiment {job_name}/{number} is running; stop its task first")
    path = exp_path(exp)
    merged_dirs = _merged_cache_paths(exp)
    backend = storage()
    if backend.remote or (exp.status or {}).get("artifact_prefix") or (exp.status or {}).get("artifact_checkpoints"):
        backend.delete_prefix(project_key(project.id, "models", exp.id))
    db.delete(exp)
    db.flush()
    shutil.rmtree(path, ignore_errors=True)
    for directory in merged_dirs:
        shutil.rmtree(directory, ignore_errors=True)


def loss_curve(db: Session, exp: Experiment) -> list[dict[str, Any]]:
    return [row.payload for row in db.scalars(select(ExperimentLog).where(ExperimentLog.experiment_id == exp.id, ExperimentLog.kind == "loss").order_by(ExperimentLog.step, ExperimentLog.id))]


def checkpoints(db: Session, project: Project, job_name: str, number: int) -> list[dict[str, Any]]:
    exp = get_experiment(db, project, job_name, number)
    return [{**c, "model_id": f"{job_name}/{number}@{c['step']}"} for c in experiment_checkpoint_rows(exp)]


def pending_numbers(db: Session, project: Project, job_name: str) -> list[int]:
    job = get_job(db, project, job_name)
    return [e.number for e in job.experiments if e.state == "pending"]


def _merged_cache_paths(exp: Experiment) -> tuple[Path, ...]:
    path = exp_path(exp)
    # Include the previous number-keyed layout so deleting experiments made by
    # an earlier deployment cannot leave a cache that a reused number could trust.
    return path / "merged", path.parent / f"{exp.id}-merged", path.parent / f"{path.name}-merged"


def parent_weights(db: Session, project: Project, reference: str) -> Path:
    """Weights of a finished experiment to continue from: its full model, or its adapter merged once into the base (cached)."""
    parent = find_experiment(db, project, reference)
    from tinystudio.training.quantization import is_retained_quantized, has_quantized_marker
    if is_retained_quantized(parent.config, parent.status or {}):
        raise ProjectError("Quantized policy refinement through a merged float base is not qualified; use its retained laboratory bundle")
    if parent.state != "succeeded":
        raise ProjectError(f"refinetune_from: experiment {reference!r} has not succeeded")
    path = ensure_experiment_artifacts(parent)
    if has_quantized_marker(path):
        raise ProjectError("Quantized policy refinement through a merged float base is not qualified; use its retained laboratory bundle")
    if (path / "model").exists():
        return path / "model"
    merged = path.parent / f"{parent.id}-merged" if (parent.status or {}).get("artifact_prefix") else path / "merged"
    if not (merged / "config.json").exists():
        ancestor = parent.config.get("refinetune_from")
        source = str(parent_weights(db, project, ancestor)) if ancestor else (parent.status or {}).get("weights_source")
        export_merged(path, merged, parent.config["base_model"], source)
    return merged


def _set_state(experiment_id: str, state: str, **extra: Any) -> dict[str, Any]:
    with session() as db:
        exp = db.get(Experiment, experiment_id)
        assert exp is not None
        exp.state = state
        exp.status = {**(exp.status or {}), "state": state, "updated": iso(datetime.now(timezone.utc)), **extra}
        return {"number": exp.number, "alias": exp.alias, **exp.status}


def run_experiment(experiment_id: str, progress: Any = print, task_id: str | None = None) -> dict[str, Any]:
    """Train one experiment. Uses short transactions so hours of training never hold a database connection."""
    with session() as db:
        exp = db.get(Experiment, experiment_id)
        if exp is None:
            raise ProjectError("experiment does not exist")
        if exp.state == "running":
            raise ProjectError(f"experiment {exp.alias} is already running")
        project = db.get(Project, exp.job.project_id)
        assert project is not None
        cfg = TrainConfig.from_dict(exp.config)
        train_rows, val_rows = snapshots_svc.load_snapshot(db, project, exp.job.snapshot_name)
        weights = parent_weights(db, project, cfg.refinetune_from) if cfg.refinetune_from else None
        if task_id:
            exp.task_id = task_id
        out_dir = exp_path(exp)
        artifact_prefix = project_key(project.id, "models", exp.id)
    started = time.perf_counter()
    _set_state(experiment_id, "running", started_at=iso(datetime.now(timezone.utc)), weights_source=str(weights) if weights else cfg.base_model,
        **({"quantization_format": cfg.as_dict()["quantization_format"]} if cfg.update_strategy == "qlora" else {}))

    def on_log(kind: str, row: dict[str, Any]) -> None:
        checkpoint = None
        if kind == "checkpoint" and backend.remote:
            step = int(row["step"])
            directory = out_dir / "checkpoints" / f"checkpoint-{step}"
            prefix = artifact_prefix + f"/checkpoints/{step}"
            manifest = backend.publish_directory(directory, prefix)
            checkpoint = {**row, "step": step, "prefix": prefix, "bytes": sum(f["size"] for f in manifest["files"])}
        with session() as db:
            db.add(ExperimentLog(experiment_id=experiment_id, kind=kind, step=int(row.get("step", 0)), payload=row))
            if checkpoint:
                current = db.get(Experiment, experiment_id)
                assert current is not None
                entries = [*((current.status or {}).get("artifact_checkpoints", [])), checkpoint]
                if cfg.keep_checkpoints > 0:
                    for old in entries[:-cfg.keep_checkpoints]:
                        backend.delete_directory(old["prefix"])
                    entries = entries[-cfg.keep_checkpoints:]
                current.status = {**(current.status or {}), "artifact_checkpoints": entries}

    try:
        backend = storage()
        result = train_experiment(out_dir, cfg, train_rows, val_rows, progress, weights, on_log)
        if backend.remote:
            backend.publish_directory(out_dir, artifact_prefix + "/final")
            result = {**result, "artifact_prefix": artifact_prefix + "/final"}
        return _set_state(experiment_id, "succeeded", seconds=round(time.perf_counter() - started, 1), **result)
    except KeyboardInterrupt:
        return _set_state(experiment_id, "failed", error="interrupted", seconds=round(time.perf_counter() - started, 1))
    except Exception as cause:  # noqa: BLE001 - the row must record any failure reason
        return _set_state(experiment_id, "failed", error=f"{type(cause).__name__}: {cause}", traceback=traceback.format_exc()[-4000:], seconds=round(time.perf_counter() - started, 1))


def run_pending(job_id: str, progress: Any = print, task_id: str | None = None) -> list[dict[str, Any]]:
    """Train every pending experiment of a job in order (one at a time; there is one GPU)."""
    with session() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise ProjectError("job does not exist")
        pending = [(e.id, e.number, e.config["base_model"], e.config["training_type"]) for e in job.experiments if e.state == "pending"]
        name = job.name
    results = []
    for exp_id, number, base_model, kind in pending:
        progress(f"=== experiment {name}/{number} ({base_model}, {kind})")
        results.append(run_experiment(exp_id, progress, task_id))
    return results


def mark_stopped(experiment_ids: list[str]) -> None:
    """A task was stopped: running experiments it owned become failed."""
    for exp_id in experiment_ids:
        _set_state(exp_id, "failed", error="stopped")


def analysis_dict(row: Analysis | None) -> dict[str, Any] | None:
    return None if row is None else {**row.payload, "created": iso(row.created)}


def get_analysis(db: Session, job: Job) -> dict[str, Any] | None:
    return analysis_dict(db.get(Analysis, job.id))


def analyze_job(db: Session, project: Project, job_name: str, client: Any, sample: int = 8) -> dict[str, Any]:
    from tinystudio.evaluation.analysis import analyze

    job = get_job(db, project, job_name)
    train_rows, _ = snapshots_svc.load_snapshot(db, project, job.snapshot_name)
    payload = analyze(train_rows, job.goal or project.goal, client, sample)
    payload.update({"job": job.name, "snapshot": job.snapshot_name})
    row = db.get(Analysis, job.id)
    if row is None:
        row = Analysis(job_id=job.id, payload=payload)
        db.add(row)
    else:
        row.payload = payload
    db.flush()
    return analysis_dict(row) or payload
