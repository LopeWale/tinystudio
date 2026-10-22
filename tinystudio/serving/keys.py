"""Local API keys for the OpenAI-compatible endpoint.

Studio issues bearer keys per project. Here keys live in
``<project>/.api-keys.json`` as SHA-256 digests; the plaintext is shown once
at creation. While at least one key exists, ``/v1/*`` requires
``Authorization: Bearer <key>``; the web interface uses its own
``/api/playground`` route and is unaffected.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from tinystudio.core.project import Project, ProjectError, now, read_json, write_json

PREFIX = "ts-"


def _path(project: Project):
    return project.root / ".api-keys.json"


def list_keys(project: Project) -> list[dict[str, Any]]:
    path = _path(project)
    return read_json(path) if path.exists() else []


def create_key(project: Project, name: str) -> tuple[dict[str, Any], str]:
    """Return (record, plaintext). The plaintext is never stored."""
    if not name.strip():
        raise ProjectError("the key needs a name")
    plaintext = PREFIX + secrets.token_urlsafe(32)
    record = {"id": secrets.token_hex(4), "name": name.strip(), "prefix": plaintext[:8], "sha256": hashlib.sha256(plaintext.encode()).hexdigest(), "created": now(), "last_used": None}
    keys = list_keys(project)
    keys.append(record)
    write_json(_path(project), keys)
    return record, plaintext


def revoke_key(project: Project, key_id: str) -> None:
    keys = list_keys(project)
    remaining = [k for k in keys if k["id"] != key_id]
    if len(remaining) == len(keys):
        raise ProjectError(f"key {key_id!r} not found")
    write_json(_path(project), remaining)


def verify_key(project: Project, token: str | None) -> bool:
    keys = list_keys(project)
    if not keys:
        return True  # no keys defined: the endpoint stays open on localhost
    if not token:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    for key in keys:
        if secrets.compare_digest(key["sha256"], digest):
            key["last_used"] = now()
            write_json(_path(project), keys)
            return True
    return False
