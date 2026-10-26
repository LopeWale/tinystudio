"""Task rows: the durable record of background work (the queue itself is Redis, see :mod:`tinystudio.worker`)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from tinystudio.core.project import ProjectError, iso
from tinystudio.db.models import Project, Task, User

TAIL_LINES = 40


def task_dict(task: Task, *, include_result: bool = True) -> dict[str, Any]:
    return {"id": task.id, "kind": task.kind, "args": task.args, "state": task.state, "created": iso(task.created), "started": iso(task.started), "finished": iso(task.finished), "error": task.error, "result": task.result if include_result else None, "project_id": task.project_id, "log_tail": task.log_tail or [], "worker": task.worker}


def create_task(db: Session, kind: str, args: dict[str, Any], project: Project | None = None, user: User | None = None) -> Task:
    task = Task(kind=kind, args=args, project_id=project.id if project else None, user_id=user.id if user else None, state="queued", log_tail=[])
    db.add(task)
    db.flush()
    return task


def get_task(db: Session, task_id: str) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise ProjectError(f"task {task_id!r} not found")
    return task


def list_tasks(db: Session, project: Project | None = None, limit: int = 50, states: tuple[str, ...] | None = None, *, include_result: bool = True) -> list[dict[str, Any]]:
    query = select(Task).order_by(Task.created.desc()).limit(limit)
    if not include_result:
        query = query.options(defer(Task.result, raiseload=True))
    if project is not None:
        query = query.where(Task.project_id == project.id)
    if states:
        query = query.where(Task.state.in_(states))
    return [task_dict(t, include_result=include_result) for t in db.scalars(query)]


def mark_running(db: Session, task_id: str, worker: str) -> Task:
    task = get_task(db, task_id)
    task.state, task.started, task.worker = "running", datetime.now(timezone.utc), worker
    return task


def mark_finished(db: Session, task_id: str, state: str, error: str | None = None, result: dict[str, Any] | None = None) -> Task:
    task = get_task(db, task_id)
    if task.state in ("succeeded", "failed", "stopped") and state == "failed" and task.state != "failed":
        return task  # a stop already recorded wins over the failure the kill produced
    task.state, task.finished, task.error, task.result = state, datetime.now(timezone.utc), error, result
    return task


def set_tail(db: Session, task_id: str, lines: list[str]) -> None:
    task = db.get(Task, task_id)
    if task is not None:
        task.log_tail = lines[-TAIL_LINES:]


def recover_orphans(db: Session, alive_job_ids: set[str]) -> int:
    """Mark tasks whose RQ job vanished (worker crash) as failed. Returns the count."""
    n = 0
    for task in db.scalars(select(Task).where(Task.state.in_(("queued", "running")))):
        if task.rq_job_id and task.rq_job_id not in alive_job_ids:
            task.state, task.finished, task.error = "failed", datetime.now(timezone.utc), "the worker running this task disappeared"
            n += 1
    return n
