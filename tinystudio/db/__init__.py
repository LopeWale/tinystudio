"""PostgreSQL access: one engine per process, short-lived sessions, fork safety for workers."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tinystudio.config import config

_lock = threading.Lock()
_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def engine() -> Engine:
    """The process-wide engine (created on first use)."""
    global _engine, _factory
    with _lock:
        if _engine is None:
            _engine = create_engine(config().database_url, pool_pre_ping=True, pool_size=5, max_overflow=10, future=True)
            _factory = sessionmaker(_engine, expire_on_commit=False)
        return _engine


def session_factory() -> sessionmaker[Session]:
    engine()
    assert _factory is not None
    return _factory


@contextmanager
def session() -> Iterator[Session]:
    """A transaction: commits on success, rolls back on any exception."""
    db = session_factory()()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def dispose_after_fork() -> None:
    """Call in a forked child before its first query: pooled connections must not be shared with the parent."""
    if _engine is not None:
        _engine.dispose(close=False)


def reset() -> None:
    """Forget the engine (tests that change the database URL)."""
    global _engine, _factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine, _factory = None, None


def ping() -> bool:
    """True when the database answers."""
    try:
        with engine().connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:  # noqa: BLE001 - a health probe reports, it does not raise
        return False


def database_version() -> str | None:
    try:
        with engine().connect() as conn:
            return str(conn.execute(text("show server_version")).scalar())
    except Exception:  # noqa: BLE001
        return None


__all__ = ["Any", "database_version", "dispose_after_fork", "engine", "ping", "reset", "session", "session_factory"]
