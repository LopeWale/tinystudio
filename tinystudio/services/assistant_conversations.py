"""Private per-user, per-project assistant snapshots with optimistic concurrency."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from tinystudio.core.project import iso
from tinystudio.db.models import AssistantConversation

MAX_SNAPSHOT_BYTES = 1024 * 1024


class ConversationConflict(Exception):
    """The caller must reload before replacing a newer snapshot."""


class ConversationTooLarge(ValueError):
    pass


class ToolDisplay(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(default="", max_length=300)
    name: str = Field(max_length=300)
    input: Any = None
    kind: Literal["ui", "tool"] = "tool"
    output: Any = None


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["user", "assistant"]
    content: str
    calls: list[ToolDisplay] = Field(default_factory=list, max_length=128)


class ConversationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: StrictInt = Field(ge=0, le=2**63 - 2)
    history: list[ConversationMessage] = Field(max_length=1000)
    provider: str = Field(default="", max_length=80)
    model: str = Field(default="", max_length=300)

    def snapshot(self) -> dict[str, Any]:
        # Omit absent tool output so a pending display call retains its meaning.
        payload = self.model_dump(exclude={"revision"}, exclude_unset=True)
        payload.setdefault("provider", "")
        payload.setdefault("model", "")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, UnicodeError, RecursionError) as cause:
            raise ValueError("conversation must contain valid JSON text") from cause
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            raise ConversationTooLarge("conversation exceeds the 1 MiB storage limit; clear or shorten it before saving")
        return payload


def _snapshot(row: Any) -> dict[str, Any]:
    return {"revision": row["revision"], "history": row["history"], "provider": row["provider"], "model": row["model"], "updated": iso(row["updated"])}


def load(db: Session, project_id: str, user_id: str) -> dict[str, Any]:
    table = AssistantConversation.__table__
    row = db.execute(select(table).where(table.c.project_id == project_id, table.c.user_id == user_id)).mappings().first()
    return _snapshot(row) if row else {"revision": 0, "history": [], "provider": "", "model": "", "updated": None}


def save(db: Session, project_id: str, user_id: str, body: ConversationWrite) -> dict[str, Any]:
    """Atomically replace a matching revision, including an empty (cleared) snapshot.

    An absent row is revision zero. The composite primary key arbitrates racing
    first writes; later writes compare revisions inside UPDATE, not in Python.
    The caller's transaction owns commit/rollback, as for other services.
    """
    payload = body.snapshot()
    table = AssistantConversation.__table__
    values = {**payload, "revision": body.revision + 1, "updated": func.now()}
    if body.revision == 0:
        statement = insert(table).values(project_id=project_id, user_id=user_id, **values).on_conflict_do_nothing(index_elements=[table.c.project_id, table.c.user_id])
    else:
        statement = update(table).where(table.c.project_id == project_id, table.c.user_id == user_id, table.c.revision == body.revision).values(**values)
    try:
        row = db.execute(statement.returning(table)).mappings().first()
    except DataError as cause:
        # JSON permits text (for example NUL) that PostgreSQL JSONB rejects.
        # The request dependency rolls this transaction back on the error.
        raise ValueError("conversation contains text or numbers PostgreSQL cannot store") from cause
    if row is None:
        raise ConversationConflict("conversation changed in another window; reload before saving")
    return _snapshot(row)
