"""Global settings: one set per deployment, edited by admins on the Settings page, stored in the ``settings`` table.

Readers that predate the database (the helper-LLM client, the assistant, the Hub module) read ``os.environ``,
so :func:`apply` copies the stored values into the process environment; the API does that at start and after
every save, the worker before every task. Environment variables and ``.env`` seed values that are not stored.
"""

from __future__ import annotations

import os
from datetime import timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.db.models import Setting

SPEC: dict[str, dict[str, Any]] = {
    "TINYSTUDIO_LLM_BASE_URL": {"label": "Helper LLM base URL", "group": "helper", "secret": False},
    "TINYSTUDIO_LLM_MODEL": {"label": "Helper LLM model", "group": "helper", "secret": False},
    "TINYSTUDIO_LLM_API_KEY": {"label": "Helper LLM API key", "group": "helper", "secret": True},
    "TINYSTUDIO_EMBEDDING_MODEL": {"label": "Default embedding model for new repositories", "group": "rag", "secret": False},
    "ANTHROPIC_API_KEY": {"label": "Anthropic API key", "group": "anthropic", "secret": True},
    "TINYSTUDIO_ANTHROPIC_WORKSPACE_ID": {"label": "Anthropic workspace id (only for organization-level keys)", "group": "anthropic", "secret": False},
    "TINYSTUDIO_ANTHROPIC_MODEL": {"label": "Default Claude model", "group": "anthropic", "secret": False},
    "TINYSTUDIO_ASSISTANT_EFFORT": {"label": "Claude effort (low, medium, high)", "group": "anthropic", "secret": False},
    "OPENAI_API_KEY": {"label": "OpenAI API key", "group": "openai", "secret": True},
    "TINYSTUDIO_OPENAI_MODEL": {"label": "Default OpenAI model", "group": "openai", "secret": False},
    "HF_TOKEN": {"label": "Hugging Face token", "group": "hub", "secret": True},
}
SECRET_KEYS = tuple(k for k, s in SPEC.items() if s["secret"])
_APPLIED: set[str] = set()
_ENV_SEED: dict[str, str] = {}  # values the environment held before the first apply(); restored when a key is cleared


def stored(db: Session) -> dict[str, str]:
    """Values saved from the Settings page."""
    return {row.key: row.value for row in db.scalars(select(Setting)) if row.key in SPEC}


def get(db: Session, key: str) -> str | None:
    """A setting: the stored value, else the environment, else None."""
    row = db.get(Setting, key)
    if row is not None and row.value != "":
        return row.value
    return os.environ.get(key) or None


def apply(db: Session) -> dict[str, str]:
    """Copy stored values into os.environ (stored wins). Keys cleared since the last apply fall back to their seed."""
    values = stored(db)
    for key in SPEC:
        if key not in _ENV_SEED and key in os.environ and key not in _APPLIED:
            _ENV_SEED[key] = os.environ[key]
    for key in _APPLIED - set(values):
        if key in _ENV_SEED:
            os.environ[key] = _ENV_SEED[key]
        else:
            os.environ.pop(key, None)
    _APPLIED.clear()
    for key, value in values.items():
        os.environ[key] = value
        _APPLIED.add(key)
    return values


def save(db: Session, changes: dict[str, str | None], user_id: str | None = None) -> dict[str, str]:
    """Merge changes (None or "" removes a key), then apply to this process."""
    for key, value in changes.items():
        if key not in SPEC:
            raise ValueError(f"unknown setting {key}")
        row = db.get(Setting, key)
        if value is None or str(value).strip() == "":
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(Setting(key=key, value=str(value).strip(), updated_by=user_id))
        else:
            row.value = str(value).strip()
            row.updated_by = user_id
    db.flush()
    return apply(db)


def describe(db: Session) -> list[dict[str, Any]]:
    """Rows for the Settings page: secrets are reported as set or unset, never returned."""
    values = stored(db)
    rows = []
    for key, spec in SPEC.items():
        if key in values:
            source, value = "settings", values[key]
        elif key in _ENV_SEED or (key in os.environ and key not in _APPLIED):
            source, value = "environment", _ENV_SEED.get(key, os.environ.get(key, ""))
        else:
            source, value = "default", None
        rows.append({"key": key, "label": spec["label"], "group": spec["group"], "secret": spec["secret"], "set": value is not None and value != "", "value": None if spec["secret"] else value, "source": source})
    return rows


def updated_at(db: Session) -> str | None:
    latest = max((row.updated for row in db.scalars(select(Setting))), default=None)
    return latest.astimezone(timezone.utc).isoformat(timespec="seconds") if latest else None
