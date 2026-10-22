"""Background work: Redis is the queue and the progress channel, RQ runs jobs, PostgreSQL keeps the durable task row.

- ``enqueue`` creates a ``tasks`` row and an RQ job whose id is the task id.
- ``run_task`` is the RQ job function: it executes the handler for the task's kind and records the outcome.
- Progress lines go to a log file under the data directory, to the Redis channel ``task:<id>`` (the API relays it as
  server-sent events), and, in batches, to the row's ``log_tail``.
- ``stop`` cancels a queued job or kills the running work horse, then marks the task and its experiment stopped.
"""

from __future__ import annotations

import platform
import sys
import threading
import time
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from redis import Redis
from rq import Queue
from rq.command import send_stop_job_command
from rq.job import Job as RqJob
from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.config import config
from tinystudio.core.project import ProjectError
from tinystudio.db import dispose_after_fork, session
from tinystudio.db.models import Experiment, Project, Task, User
from tinystudio.services import tasks as tasks_svc

QUEUE_NAME = "tinystudio"
JOB_TIMEOUT = int(timedelta(days=7).total_seconds())
RESULT_TTL = int(timedelta(days=1).total_seconds())
FAILURE_TTL = int(timedelta(days=7).total_seconds())
_lock = threading.Lock()
_redis: Redis | None = None


def redis_conn() -> Redis:
    global _redis
    with _lock:
        if _redis is None:
            _redis = Redis.from_url(config().redis_url, decode_responses=False, health_check_interval=30)
        return _redis


def reset() -> None:
    global _redis
    with _lock:
        _redis = None


def ping() -> bool:
    try:
        return bool(redis_conn().ping())
    except Exception:  # noqa: BLE001
        return False


def queue() -> Queue:
    return Queue(QUEUE_NAME, connection=redis_conn())


def channel(task_id: str) -> str:
    return f"tinystudio:task:{task_id}"


def enqueue(db: Session, kind: str, args: dict[str, Any], project: Project | None = None, user: User | None = None) -> dict[str, Any]:
    """Create the task row and queue it. The row is committed by the caller's transaction; the RQ job is created now."""
    from tinystudio.worker.handlers import HANDLERS

    if kind not in HANDLERS:
        raise ProjectError(f"unknown task kind {kind!r}")
    task = tasks_svc.create_task(db, kind, args, project, user)
    try:
        job = queue().enqueue(run_task, task.id, job_id=task.id, job_timeout=JOB_TIMEOUT, result_ttl=RESULT_TTL, failure_ttl=FAILURE_TTL, description=f"{kind} {task.id}")
    except Exception as cause:  # noqa: BLE001 - Redis down: the task must not stay queued forever
        task.state, task.error = "failed", f"could not reach the queue: {cause}"
        raise ProjectError(f"the task queue (Redis at {config().redis_url}) is not reachable: {cause}") from cause
    task.rq_job_id = job.id
    return tasks_svc.task_dict(task)


def stop(db: Session, task_id: str) -> dict[str, Any]:
    task = tasks_svc.get_task(db, task_id)
    if task.state not in ("queued", "running"):
        return tasks_svc.task_dict(task)
    conn = redis_conn()
    try:
        job = RqJob.fetch(task.rq_job_id or task.id, connection=conn)
    except Exception:  # noqa: BLE001 - job already gone
        job = None
    if task.state == "queued" and job is not None:
        job.cancel()
    elif job is not None:
        try:
            send_stop_job_command(conn, job.id)
        except Exception:  # noqa: BLE001 - not running anywhere any more
            pass
    task.state, task.error = "stopped", "stopped by request"
    from datetime import datetime, timezone

    task.finished = datetime.now(timezone.utc)
    for exp in db.scalars(select(Experiment).where(Experiment.task_id == task_id, Experiment.state == "running")):
        exp.state = "failed"
        exp.status = {**(exp.status or {}), "state": "failed", "error": "stopped"}
    publish(task_id, "[stopped by request]")
    return tasks_svc.task_dict(task)


def publish(task_id: str, line: str) -> None:
    try:
        redis_conn().publish(channel(task_id), line.encode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - progress is best effort
        pass


def subscribe(task_id: str, timeout: float = 20.0) -> Iterator[str | None]:
    """Yield log lines as they are published; None every ``timeout`` seconds of silence (for keepalives)."""
    pubsub = redis_conn().pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(channel(task_id))
    try:
        while True:
            message = pubsub.get_message(timeout=timeout)
            if message is None:
                yield None
            elif message.get("type") == "message":
                data = message["data"]
                yield data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
    finally:
        pubsub.close()


class TaskLog:
    """Progress sink for one task: file, Redis channel, and the row's tail (flushed every two seconds)."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.path = config().task_log(task_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a", encoding="utf-8")
        self.tail: list[str] = []
        self.dirty = False
        self.last_flush = time.monotonic()

    def __call__(self, line: Any) -> None:
        text = str(line).rstrip()
        if not text or "redirects.py" in text:
            return
        self.handle.write(text + "\n")
        self.handle.flush()
        publish(self.task_id, text)
        self.tail = (self.tail + [text])[-tasks_svc.TAIL_LINES :]
        self.dirty = True
        if time.monotonic() - self.last_flush > 2:
            self.flush()

    def flush(self) -> None:
        if self.dirty:
            with session() as db:
                tasks_svc.set_tail(db, self.task_id, self.tail)
            self.dirty, self.last_flush = False, time.monotonic()

    def close(self) -> None:
        self.flush()
        self.handle.close()


def run_task(task_id: str) -> dict[str, Any] | None:
    """The RQ job: executed in the worker's work horse."""
    from tinystudio.services import settings as settings_svc
    from tinystudio.worker.handlers import HANDLERS, Context

    dispose_after_fork()
    log = TaskLog(task_id)
    worker_name = f"{platform.node()}:{sys.argv[0].rsplit('/', 1)[-1]}"
    with session() as db:
        task = tasks_svc.mark_running(db, task_id, worker_name)
        kind, args, project_id, user_id = task.kind, dict(task.args), task.project_id, task.user_id
        settings_svc.apply(db)
    log(f"[task {task_id}] {kind} started on {worker_name}")
    try:
        result = HANDLERS[kind](Context(task_id, project_id, user_id, args, log))
        log(f"[task {task_id}] succeeded")
        with session() as db:
            tasks_svc.mark_finished(db, task_id, "succeeded", result=_jsonable(result))
        return result
    except KeyboardInterrupt:
        with session() as db:
            tasks_svc.mark_finished(db, task_id, "stopped", error="interrupted")
        raise
    except Exception as cause:  # noqa: BLE001 - the row records every failure
        import traceback

        log(f"[task {task_id}] failed: {type(cause).__name__}: {cause}")
        log(traceback.format_exc()[-3000:])
        with session() as db:
            tasks_svc.mark_finished(db, task_id, "failed", error=f"{type(cause).__name__}: {cause}")
        return None
    finally:
        log.close()


def _jsonable(value: Any) -> dict[str, Any] | None:
    import json

    if value is None:
        return None
    try:
        text = json.dumps(value, default=str)
    except TypeError:
        return {"repr": repr(value)[:2000]}
    if len(text) > 200_000:
        return {"truncated": True, "preview": text[:2000]}
    loaded = json.loads(text)
    return loaded if isinstance(loaded, dict) else {"value": loaded}


def run_inline(task_id: str) -> dict[str, Any] | None:
    """Execute a task in this process (the CLI on a machine without a worker)."""
    return run_task(task_id)


def work(queues: list[str] | None = None, burst: bool = False) -> None:
    """Run a worker process. macOS uses the spawn-based worker because fork is unsafe with Objective-C frameworks."""
    from rq import Worker

    names = queues or [QUEUE_NAME]
    conn = redis_conn()
    if platform.system() == "Darwin":
        from rq.worker import SpawnWorker

        worker: Worker = SpawnWorker([Queue(n, connection=conn) for n in names], connection=conn)
    else:
        worker = Worker([Queue(n, connection=conn) for n in names], connection=conn)
    worker.work(burst=burst, with_scheduler=False)


def alive_job_ids() -> set[str]:
    """Ids of RQ jobs that still exist (queued, started, scheduled, deferred)."""
    q = queue()
    ids: set[str] = set(q.job_ids)
    for registry in (q.started_job_registry, q.deferred_job_registry, q.scheduled_job_registry):
        ids.update(registry.get_job_ids())
    return ids


def recover(db: Session) -> int:
    """Mark tasks whose RQ job vanished as failed (worker crash)."""
    try:
        alive = alive_job_ids()
    except Exception:  # noqa: BLE001 - Redis down: nothing to recover
        return 0
    return tasks_svc.recover_orphans(db, alive)
