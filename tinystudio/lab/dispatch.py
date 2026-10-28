"""Bounded delivery of committed laboratory work to the existing RQ workers."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

from redis import Redis
from rq import Queue
from sqlalchemy import text

from tinystudio.config import config
from tinystudio.db import session
from tinystudio.worker import QUEUE_NAME

log = logging.getLogger("tinystudio.lab")


def delivery_queue():
    return Queue(QUEUE_NAME, connection=Redis.from_url(config().redis_url,
        socket_connect_timeout=3, socket_timeout=3, health_check_interval=30))


@contextmanager
def _bounded_session():
    with session() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("select set_config('statement_timeout','5000',true), set_config('lock_timeout','3000',true)"))
        yield db


def tick(*, cancelled=lambda: False, timeout_seconds: float = 20) -> dict:
    from . import operations
    from .contracts import checksum
    from .models import LabOperation
    from .rq_adapter import CREDENTIAL_ENV, _enqueue, dispatch_once

    deadline = time.monotonic()+timeout_seconds
    def stopped():
        return cancelled() or time.monotonic() >= deadline
    if stopped():
        return {"stopped": True}
    with _bounded_session() as db:
        recovered = operations.recover_expired(db)
    def deliver(id_):
        with _bounded_session() as reader:
            row = reader.get(LabOperation, id_)
            if row is None:
                return
            generation = row.generation
        identity = checksum({"operation_id": id_, "generation": generation})
        _enqueue(operations.run_operation, id_, job_id="lab-operation-"+identity)
    with _bounded_session() as db:
        operations_sent = operations.dispatch_pending(db, deliver, cancelled=stopped,
            timeout_seconds=max(.1, deadline-time.monotonic()))
    runs = dispatch_once(cancelled=stopped, timeout_seconds=max(.1, deadline-time.monotonic())) if not stopped() and os.environ.get(CREDENTIAL_ENV) else {"configured": False}
    return {"operations_dispatched": len(operations_sent), "operations_recovered": recovered, "runs": runs}


class Dispatcher:
    """Own a terminable delivery process, including blocking library calls."""
    def __init__(self, *, command=None, environment=None, grace: float = 3):
        self.command = command or [sys.executable, "-m", "tinystudio.lab.dispatch"]
        self.environment = environment
        self.grace = grace
        self.group = None

    def start(self):
        from .runner import ProcessGroup
        self.group = ProcessGroup(self.command, grace=self.grace, stdin=subprocess.DEVNULL,
                                  env=self.environment)
        return self

    async def close(self):
        if self.group:
            await asyncio.to_thread(self.group.close)
            self.group = None


def main():
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    last_error = None
    while not stopped.is_set():
        try:
            tick(cancelled=stopped.is_set)
            last_error = None
        except Exception as exc:
            error = type(exc).__name__
            if error != last_error:
                log.warning("Laboratory delivery unavailable (%s); committed work remains pending", error)
            last_error = error
        stopped.wait(2)


if __name__ == "__main__":
    main()
