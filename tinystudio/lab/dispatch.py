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
import json
from pathlib import Path
import secrets
import tempfile
from contextlib import contextmanager

from redis import Redis
from rq import Queue
from sqlalchemy import text

from tinystudio.config import config
from tinystudio.db import session
from tinystudio.worker import QUEUE_NAME
from .operation_runtime import OperationExecutor, OperationRuntimeConfiguration, OperationRuntimePaths

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


def tick(*, cancelled=lambda: False, timeout_seconds: float = 20, configuration=None) -> dict:
    from . import operations
    from .contracts import checksum
    from .models import LabOperation
    from .rq_adapter import CREDENTIAL_ENV, _enqueue, dispatch_once

    configuration = configuration or OperationRuntimeConfiguration.from_environment()
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
            timeout_seconds=max(.1, deadline-time.monotonic()), excluded_kinds=configuration.excluded_delivery_kinds)
    runs = dispatch_once(cancelled=stopped, timeout_seconds=max(.1, deadline-time.monotonic())) if not stopped() and os.environ.get(CREDENTIAL_ENV) else {"configured": False}
    return {"operations_dispatched": len(operations_sent), "operations_recovered": recovered, "runs": runs,
            'dependency_capacity_owner': False}


class Dispatcher:
    """Own a terminable delivery process, including blocking library calls."""
    def __init__(self, *, command=None, environment=None, grace: float | None = None, configuration=None):
        self.command = command or [sys.executable, "-m", "tinystudio.lab.dispatch"]
        self.environment = environment
        self.configuration = configuration
        self.grace = grace
        self.wait_ready = command is None
        self.group = None
        self.retained = None
        self.paths = None
        self.instance = None

    def start(self):
        from .runner import ProcessGroup
        self.configuration = self.configuration or OperationRuntimeConfiguration.from_environment(self.environment)
        environment = dict(os.environ if self.environment is None else self.environment)
        environment.update(self.configuration.environment())
        directory = OperationRuntimePaths(config().data_dir).directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.retained = tempfile.TemporaryDirectory(prefix='owner-', dir=directory)
        self.paths = OperationRuntimePaths(Path(self.retained.name))
        self.instance = secrets.token_hex(16)
        environment[self.configuration.status_file_environment] = str(self.paths.status)
        environment[self.configuration.instance_environment] = self.instance
        try:
            self.group = ProcessGroup(self.command, grace=self.configuration.shutdown_grace if self.grace is None else self.grace,
                                      stdin=subprocess.DEVNULL, env=environment)
        except BaseException:
            self.retained.cleanup(); self.retained = None
            raise
        if self.wait_ready and self.configuration.mode == 'managed':
            deadline = time.monotonic()+self.configuration.startup_timeout_seconds
            try:
                while not self.status()['available']:
                    if self.group.process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('independent operation capacity did not become ready')
                    time.sleep(min(.02, max(0, deadline-time.monotonic())))
            except BaseException:
                try:
                    self.group.close()
                finally:
                    self.group = None
                    self.retained.cleanup(); self.retained = None
                raise
        return self

    def status(self):
        configuration = self.configuration or OperationRuntimeConfiguration()
        value = configuration.status()
        if configuration.mode != 'managed':
            return value
        alive = self.group is not None and self.group.process.poll() is None
        ready = False
        if alive and self.paths is not None:
            try:
                marker = json.loads(self.paths.status.read_text())
                ready = (isinstance(marker, dict) and marker.get('instance') == self.instance and marker.get('ready') is True
                         and marker.get('workers') == configuration.capacity and marker.get('failure') is None)
            except (OSError, ValueError, TypeError):
                pass
        return {**value, 'alive': alive, 'ready': ready, 'available': alive and ready}

    async def close(self):
        try:
            if self.group:
                await asyncio.to_thread(self.group.close)
                self.group = None
        finally:
            if self.retained:
                self.retained.cleanup(); self.retained = None


def run_foreground():
    """CLI controller owns the same terminable process as the API lifecycle."""
    stopped = threading.Event()
    previous = {signum: signal.signal(signum, lambda *_: stopped.set()) for signum in (signal.SIGINT, signal.SIGTERM)}
    dispatcher = Dispatcher()
    try:
        dispatcher.start()
        while not stopped.is_set():
            if dispatcher.group.process.poll() is not None:
                raise RuntimeError('laboratory dispatcher terminated')
            stopped.wait(dispatcher.configuration.poll_seconds)
    finally:
        try:
            asyncio.run(dispatcher.close())
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def main():
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    from tinystudio.db import session_factory
    configuration = OperationRuntimeConfiguration.from_environment()
    status_path = os.environ.get(configuration.status_file_environment)
    executor = OperationExecutor(session_factory(), configuration,
        status_path=Path(status_path) if status_path else None,
        instance=os.environ.get(configuration.instance_environment)).start()
    try:
        last_error = None
        while not stopped.is_set():
            executor.check()
            try:
                if configuration.delivery_enabled:
                    tick(cancelled=stopped.is_set, configuration=configuration)
                last_error = None
            except Exception as exc:
                error = type(exc).__name__
                if error != last_error:
                    log.warning("Laboratory delivery unavailable (%s); committed work remains pending", error)
                last_error = error
            executor.check()
            stopped.wait(2)
    finally:
        executor.close()


if __name__ == "__main__":
    main()
