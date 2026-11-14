"""Owned capacity for operations that a running Native/RQ publisher awaits.

Constructors/imports perform no I/O. The dispatcher process owns the bounded
threads; its existing process-group lifecycle can terminate blocked SDK calls.
External capacity is an operator declaration, never an inferred worker count.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from typing import ClassVar


@dataclass(frozen=True)
class OperationRuntimeConfiguration:
    mode: str = 'managed'
    capacity: int = 1
    poll_seconds: float = .25
    shutdown_grace: float = 3
    startup_timeout_seconds: float = 10
    delivery_enabled: bool = True
    dependency_kinds: ClassVar[tuple[str, ...]] = ('artifact_verify', 'attempt_finalize')
    prefix: ClassVar[str] = 'TINYSTUDIO_LAB_OPERATION_'
    status_file_environment: ClassVar[str] = prefix+'STATUS_FILE'
    instance_environment: ClassVar[str] = prefix+'INSTANCE'

    def __post_init__(self):
        if self.mode not in {'managed', 'external', 'disabled'}:
            raise ValueError('operation runtime mode must be managed, external, or disabled')
        if type(self.capacity) is not int or self.capacity < 0:
            raise ValueError('operation runtime capacity must be a nonnegative integer')
        if type(self.delivery_enabled) is not bool:
            raise ValueError('operation runtime delivery_enabled must be boolean')
        for value in (self.poll_seconds, self.shutdown_grace, self.startup_timeout_seconds):
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise ValueError('operation runtime timing must be finite and nonnegative')
        if self.capacity == 0 or self.mode == 'disabled':
            object.__setattr__(self, 'mode', 'disabled')
            object.__setattr__(self, 'capacity', 0)

    @classmethod
    def from_environment(cls, environment=None):
        defaults = cls()
        environment = os.environ if environment is None else environment
        def selected(name, fallback):
            return environment.get(cls.prefix+name, str(fallback)).strip()
        delivery = selected('DELIVERY_ENABLED', '1').lower()
        if delivery not in {'0', '1', 'true', 'false'}:
            raise ValueError('operation delivery selection must be boolean')
        return cls(mode=selected('MODE', defaults.mode), capacity=int(selected('CAPACITY', defaults.capacity)),
                   poll_seconds=float(selected('POLL_SECONDS', defaults.poll_seconds)),
                   shutdown_grace=float(selected('SHUTDOWN_GRACE', defaults.shutdown_grace)),
                   startup_timeout_seconds=float(selected('STARTUP_TIMEOUT_SECONDS', defaults.startup_timeout_seconds)),
                   delivery_enabled=delivery in {'1', 'true'})

    def environment(self):
        return {self.prefix+name: str(value) for name, value in (
            ('MODE', self.mode), ('CAPACITY', self.capacity), ('POLL_SECONDS', self.poll_seconds),
            ('SHUTDOWN_GRACE', self.shutdown_grace), ('STARTUP_TIMEOUT_SECONDS', self.startup_timeout_seconds),
            ('DELIVERY_ENABLED', int(self.delivery_enabled)))}

    @property
    def excluded_delivery_kinds(self):
        return self.dependency_kinds if self.mode in {'managed', 'external'} else ()

    def status(self):
        external = self.mode == 'external'
        return {'mode': self.mode, 'configured': self.mode != 'disabled', 'capacity': self.capacity,
                'alive': None if external else False, 'ready': None if external else False,
                'available': external, 'qualification': 'operator_declared_unqualified' if external else 'unqualified'}


@dataclass(frozen=True)
class OperationRuntimePaths:
    root: Path

    @property
    def directory(self):
        return Path(self.root)/'lab-operation-runtime'

    @property
    def status(self):
        return Path(self.root)/'capacity.json'


def register_operation_runtime(app, provider):
    """Explicitly register a managed owner or operator-declared external capacity."""
    if not callable(getattr(provider, 'status', None)):
        raise TypeError('operation runtime registration requires a status provider')
    app.state.lab_operation_runtime = provider
    return provider


def operation_runtime_status(app):
    provider = getattr(app.state, 'lab_operation_runtime', None)
    if provider is None:
        provider = getattr(app.state, 'laboratory_dispatch', None)
    if provider is None:
        return {**OperationRuntimeConfiguration(mode='disabled').status(), 'mode': 'missing'}
    return dict(provider.status())


def require_operation_runtime(app):
    from .service import LabError
    value = operation_runtime_status(app)
    if not value['available']:
        raise LabError('independent artifact operation capacity is unavailable; retained work remains inspectable', 503)
    return value


def require_pending_operation_runtime(app, operation):
    if operation.state in {'queued', 'running'}:
        require_operation_runtime(app)


class OperationExecutor:
    """Bounded DB-direct dependency workers inside the owned dispatcher process."""
    def __init__(self, factory, configuration, *, status_path=None, instance=None):
        self.factory, self.configuration = factory, configuration
        self.status_path, self.instance = status_path, instance
        self.stopped = threading.Event()
        self.threads = []
        self.failure = None
        self.ready = False
        self.status_lock = threading.Lock()

    def _publish_status(self):
        if self.status_path is None:
            return
        with self.status_lock:
            # Snapshot under the same lock as replacement: a startup writer
            # must never overwrite a later slot-failure marker with stale ready.
            value = {'instance': self.instance, 'pid': os.getpid(), 'ready': self.ready and self.failure is None,
                     'workers': len(self.threads), 'failure': type(self.failure).__name__ if self.failure else None}
            descriptor, name = tempfile.mkstemp(prefix='.capacity-', dir=self.status_path.parent)
            try:
                with os.fdopen(descriptor, 'w') as stream:
                    json.dump(value, stream)
                    stream.flush(); os.fsync(stream.fileno())
                os.replace(name, self.status_path)
            finally:
                Path(name).unlink(missing_ok=True)

    def start(self):
        if self.configuration.mode == 'managed':
            # Check the supplied database/schema before reporting owned startup.
            from sqlalchemy import select
            from .models import LabOperation
            with self.factory() as db:
                db.execute(select(LabOperation.id).limit(0))
            for index in range(self.configuration.capacity):
                thread = threading.Thread(target=self._work, name='lab-operation-'+str(index), daemon=True)
                self.threads.append(thread)
                thread.start()
        self.ready = True
        self._publish_status()
        return self

    def _work(self):
        try:
            from sqlalchemy import select
            from .models import LabOperation
            from .operations import run_operation
            while not self.stopped.is_set():
                with self.factory() as db:
                    identities = list(db.scalars(select(LabOperation.id).where(
                        LabOperation.state == 'queued', LabOperation.kind.in_(self.configuration.dependency_kinds))
                        .limit(self.configuration.capacity)))
                # Reader closes before the normal worker claims its own token/
                # generation and runs its existing lease/publication fences.
                for identity in identities:
                    if self.stopped.is_set():
                        break
                    run_operation(identity, session_factory=self.factory)
                self.stopped.wait(self.configuration.poll_seconds)
        except BaseException as failure:
            self.failure = failure
            self.stopped.set()
            self._publish_status()

    def check(self):
        if self.failure is not None:
            raise RuntimeError('independent operation worker terminated') from None
        if self.configuration.mode == 'managed' and any(not thread.is_alive() for thread in self.threads):
            raise RuntimeError('independent operation worker terminated')

    def close(self):
        self.ready = False
        self.stopped.set()
        self._publish_status()
        import time
        deadline = time.monotonic()+self.configuration.shutdown_grace
        for thread in self.threads:
            thread.join(max(0, deadline-time.monotonic()))
