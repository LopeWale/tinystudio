"""Shared primitives: the expected-failure type, names, timestamps, and small JSON helpers for artifacts.

Projects, datasets, jobs, and the rest are rows in PostgreSQL (see :mod:`tinystudio.db.models`); weights,
checkpoints, and activation captures are directories under the data directory.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")  # Studio-style names such as "Strict JSON" are allowed
SLUG_CHARS = re.compile(r"[^a-z0-9]+")


class ProjectError(RuntimeError):
    """A precondition failed; the message says which one. Adapters turn it into a 400."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_name(name: str, kind: str) -> str:
    if not isinstance(name, str) or not SAFE_NAME.match(name):
        raise ProjectError(f"{kind} name {name!r} must be 1-64 characters of letters, digits, spaces, '.', '_' or '-' and start with a letter or digit")
    return name


def slugify(name: str) -> str:
    slug = SLUG_CHARS.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise ProjectError(f"{name!r} has no letters or digits to make a slug from")
    return slug[:80]


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    tmp.replace(path)


def iso(value: Any) -> str | None:
    """A datetime (or None) as an ISO-8601 string for API payloads."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(value)
