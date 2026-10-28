"""One assistant worker owns each private conversation until it finishes.

PostgreSQL session locks survive transaction commit and are released by backend
session death. The connection is dedicated and never returned locked to the pool.
A durable revision claim additionally prevents replay after a lost lease/process.
"""
from __future__ import annotations

import hashlib
import threading
from typing import Any

from sqlalchemy import text

from tinystudio import db


class TurnInProgress(ValueError):
    pass


class ConversationLease:
    def __init__(self, connection: Any, key: int):
        self._connection = connection
        self._key = key
        self._mutex = threading.Lock()

    @classmethod
    def acquire(cls, project_id: str, user_id: str) -> ConversationLease:
        key = int.from_bytes(hashlib.sha256(f'tinystudio:assistant:{project_id}:{user_id}'.encode()).digest()[:8], 'big', signed=True)
        connection = db.engine().connect()
        try:
            connection.execution_options(isolation_level='AUTOCOMMIT')
            if not connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': key}):
                raise TurnInProgress('an assistant turn is already running for this conversation; wait for it to finish')
        except BaseException:
            connection.close()
            raise
        return cls(connection, key)

    def close(self) -> None:
        with self._mutex:
            connection, self._connection = self._connection, None
            if connection is None:
                return
            try:
                connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': self._key})
            except BaseException:
                # Never return an ambiguously locked physical session to the pool.
                connection.invalidate()
            finally:
                connection.close()


def claim_revision(project_id: str, user_id: str, snapshot: dict[str, Any], provider: str, model: str | None) -> dict[str, Any]:
    from tinystudio.services import assistant_conversations as conversations

    body = conversations.ConversationWrite(revision=snapshot['revision'], history=snapshot['history'],
        provider=provider, model=model or snapshot['model'])
    with db.session() as session:
        return conversations.save(session, project_id, user_id, body)
