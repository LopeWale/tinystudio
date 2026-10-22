"""Evaluation runs: rows in ``evaluations`` and ``eval_results``; the scoring loop is in :mod:`tinystudio.evaluation.evaluate`."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from tinystudio.core.llm import LLMClient
from tinystudio.core.project import ProjectError, check_name, iso
from tinystudio.db import session
from tinystudio.db.models import EvalResult, Evaluation, Project
from tinystudio.evaluation.evaluate import evaluate_rows
from tinystudio.services import metrics as metrics_svc
from tinystudio.services import snapshots as snapshots_svc


def eval_dict(evaluation: Evaluation) -> dict[str, Any]:
    return {"id": evaluation.id, "name": evaluation.name, "snapshot": evaluation.snapshot_name, "models": evaluation.models, "metrics": evaluation.metrics, "datapoints": evaluation.datapoints, "state": evaluation.state, "created": iso(evaluation.created), "finished": iso(evaluation.finished), "error": evaluation.error, "leaderboard": evaluation.leaderboard, "task_id": evaluation.task_id}


def result_dict(r: EvalResult) -> dict[str, Any]:
    return {"datapoint": r.datapoint, "model": r.model, "metric": r.metric, "score": r.score, "reason": r.reason, "prediction": r.prediction, "reference": r.reference, "user": r.user, "seconds": r.seconds, **(r.detail or {})}


def get_eval(db: Session, project: Project, name: str) -> Evaluation:
    evaluation = db.scalar(select(Evaluation).where(Evaluation.project_id == project.id, Evaluation.name == name))
    if evaluation is None:
        raise ProjectError(f"evaluation {name!r} does not exist")
    return evaluation


def list_evals(db: Session, project: Project) -> list[dict[str, Any]]:
    return [eval_dict(e) for e in db.scalars(select(Evaluation).where(Evaluation.project_id == project.id).order_by(Evaluation.created))]


def eval_rows(db: Session, evaluation: Evaluation) -> list[dict[str, Any]]:
    return [result_dict(r) for r in db.scalars(select(EvalResult).where(EvalResult.evaluation_id == evaluation.id).order_by(EvalResult.id))]


def delete_eval(db: Session, project: Project, name: str) -> None:
    db.delete(get_eval(db, project, name))
    db.flush()


def prepare_eval(db: Session, project: Project, name: str, snapshot: str, model_ids: list[str], metric_names: list[str], limit: int | None = None, task_id: str | None = None) -> Evaluation:
    """Validate and create the evaluation row in state ``running`` (the worker fills it)."""
    check_name(name, "evaluation")
    if not model_ids or not metric_names:
        raise ProjectError("pick at least one model and one metric")
    snap = snapshots_svc.get_snapshot(db, project, snapshot)
    if snap.validation_count == 0:
        raise ProjectError(f"snapshot {snapshot!r} has no validation datapoints")
    for metric_name in metric_names:
        metrics_svc.get_metric(db, project, metric_name)
    previous = db.scalar(select(Evaluation).where(Evaluation.project_id == project.id, Evaluation.name == name))
    if previous is not None:
        if previous.state == "succeeded":
            raise ProjectError(f"evaluation {name!r} already exists; pick a new name")
        db.delete(previous)
        db.flush()
    rows_count = min(snap.validation_count, limit) if limit else snap.validation_count
    evaluation = Evaluation(project_id=project.id, name=name, snapshot_name=snapshot, models=model_ids, metrics=metric_names, datapoints=rows_count, state="running", task_id=task_id)
    db.add(evaluation)
    db.flush()
    return evaluation


def run_eval(evaluation_id: str, client: LLMClient | None, limit: int | None = None, max_new_tokens: int = 256, progress: Any = print) -> dict[str, Any]:
    """Execute a prepared evaluation. Opens its own short transactions so a long run never holds a connection."""
    from tinystudio.services.models import load_runner_factory

    with session() as db:
        evaluation = db.get(Evaluation, evaluation_id)
        if evaluation is None:
            raise ProjectError("evaluation vanished before it ran")
        project = db.get(Project, evaluation.project_id)
        assert project is not None
        _, val_rows = snapshots_svc.load_snapshot(db, project, evaluation.snapshot_name)
        rows = val_rows[:limit] if limit else val_rows
        metrics = [metrics_svc.judge_spec(metrics_svc.get_metric(db, project, m)) for m in evaluation.metrics]
        model_ids = list(evaluation.models)
        project_id = project.id
    load_runner = load_runner_factory(project_id)
    buffer: list[dict[str, Any]] = []

    def flush() -> None:
        if not buffer:
            return
        with session() as db:
            db.execute(insert(EvalResult), [{"evaluation_id": evaluation_id, **{k: v for k, v in r.items()}} for r in buffer])
        buffer.clear()

    def on_result(record: dict[str, Any]) -> None:
        buffer.append(record)
        if len(buffer) >= 50:
            flush()

    try:
        leaderboard = evaluate_rows(rows, model_ids, metrics, load_runner, client, max_new_tokens, on_result, progress)
        flush()
    except BaseException as cause:
        flush()
        with session() as db:
            row = db.get(Evaluation, evaluation_id)
            if row is not None:
                row.state, row.error, row.finished = "failed", f"{type(cause).__name__}: {cause}", datetime.now(timezone.utc)
        raise
    with session() as db:
        row = db.get(Evaluation, evaluation_id)
        assert row is not None
        row.state, row.leaderboard, row.finished = "succeeded", leaderboard, datetime.now(timezone.utc)
        return eval_dict(row)
