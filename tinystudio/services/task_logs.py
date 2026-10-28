"""Durable task logs: local files or immutable R2 chunks with a published head.

Each chunk points backwards, so publishing is linear in new log bytes (including
metadata), and tail reads need only the newest chunks. A single RQ execution owns
one task's writer. Temporary transfer files are never authoritative in R2 mode.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from tinystudio.config import config
from tinystudio.storage import ArtifactNotFound, ArtifactStorage, StorageError, storage

CHUNK_BYTES = 64 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _prefix(task_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
        raise StorageError("invalid task log ID")
    return f"tasks/{task_id}"


def _json(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _digest(value: Any) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


class TaskLogStorage:
    """Own transfers and validation, without depending on a worker's workspace."""

    def __init__(self, backend: ArtifactStorage | None = None) -> None:
        self._backend = backend if backend is not None else storage()

    @property
    def backend(self) -> ArtifactStorage:
        return self._backend

    def _get(self, key: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="tinystudio-task-log-") as tmp:
            return self.backend.download_file(key, Path(tmp) / "object").read_bytes()

    def _put(self, key: str, data: bytes) -> None:
        with tempfile.TemporaryDirectory(prefix="tinystudio-task-log-") as tmp:
            path = Path(tmp) / "object"
            path.write_bytes(data)
            self.backend.upload_file(path, key, "application/json")

    def manifest(self, task_id: str) -> dict[str, Any]:
        try:
            value = json.loads(self._get(_prefix(task_id) + "/manifest.json"))
            if (value["version"] != 1
                    or type(value["bytes"]) is not int or value["bytes"] < 0
                    or type(value["chunks"]) is not int or not 0 <= value["chunks"] <= value["bytes"]
                    or (value["chunks"] == 0 and (value["head"] is not None or value["bytes"] != 0))
                    or (value["chunks"] > 0 and not _digest(value["head"]))):
                raise ValueError("invalid head")
            return value
        except (ValueError, KeyError, TypeError) as cause:
            raise StorageError("invalid task log manifest") from cause

    def _chunk(self, prefix: str, head: str, remaining: int, count: int) -> tuple[bytes, str | None]:
        raw = self._get(prefix + "/objects/" + head)
        if hashlib.sha256(raw).hexdigest() != head:
            raise StorageError("task log chunk checksum mismatch")
        try:
            value = json.loads(raw)
            payload = base64.b64decode(value["data"], validate=True)
            previous = value["previous"]
            if (not 0 < len(payload) <= CHUNK_BYTES or type(value["bytes"]) is not int
                    or type(value["chunks"]) is not int or value["bytes"] != remaining
                    or value["chunks"] != count or len(payload) > remaining
                    or (count == 1 and (previous is not None or len(payload) != remaining))
                    or (count > 1 and (not _digest(previous) or len(payload) >= remaining))):
                raise ValueError("invalid chunk linkage")
            return payload, previous
        except (ValueError, KeyError, TypeError) as cause:
            raise StorageError("invalid task log chunk") from cause

    def read(self, task_id: str, tail: int = 0, *, fallback: list[str] | None = None, missing_ok: bool = True) -> list[str]:
        """Read full logs with tail=0, or the last positive number of lines.

        Legacy DB-tail fallback is local-only. Missing R2 logs yield an empty log;
        a missing chunk referenced by a published head is an explicit error.
        """
        prefix = _prefix(task_id)
        if tail < 0:
            raise StorageError("task log tail must be nonnegative (zero means full log)")
        if not self.backend.remote:
            path = config().task_log(task_id)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else list(fallback or [])
            return lines[-tail:] if tail else lines
        try:
            manifest = self.manifest(task_id)
        except ArtifactNotFound as cause:
            if not missing_ok:
                raise StorageError("durable task log is missing; inspect log publication failures or rerun the task") from cause
            return []
        head, remaining, count = manifest["head"], manifest["bytes"], manifest["chunks"]
        parts: list[bytes] = []
        newlines = 0
        while head is not None:
            payload, head = self._chunk(prefix, head, remaining, count)
            parts.append(payload)
            remaining -= len(payload)
            count -= 1
            newlines += payload.count(b"\n")
            # One extra newline ensures the oldest partial line is excluded.
            if tail and newlines > tail:
                break
        lines = b"".join(reversed(parts)).decode("utf-8", "replace").splitlines()
        return lines[-tail:] if tail else lines

    def writer(self, task_id: str) -> TaskLogWriter:
        return TaskLogWriter(self, task_id)


class TaskLogWriter:
    """Append state with bounded pending bytes; failed flushes retain pending data."""

    def __init__(self, store: TaskLogStorage, task_id: str) -> None:
        self._store = store
        self._prefix = _prefix(task_id)
        self._pending = bytearray()
        self._closed = False
        self._head: str | None = None
        self._size = self._count = 0
        self._handle = None
        if store.backend.remote:
            try:
                manifest = store.manifest(task_id)
            except ArtifactNotFound:
                pass
            else:
                self._head, self._size, self._count = manifest["head"], manifest["bytes"], manifest["chunks"]
                # A requeued attempt must not extend a broken historical log.
                # Validate in bounded memory; ordinary first starts have no chain.
                head, remaining, count = self._head, self._size, self._count
                while head is not None:
                    payload, head = store._chunk(self._prefix, head, remaining, count)
                    remaining -= len(payload)
                    count -= 1
        else:
            path = config().task_log(task_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = path.open("a", encoding="utf-8")

    def write(self, text: str) -> None:
        if self._closed:
            raise StorageError("task log writer is closed")
        if self._handle is not None:
            self._handle.write(text)
            self._handle.flush()
            return
        data = text.encode("utf-8", "replace")
        offset = 0
        while offset < len(data):
            take = min(CHUNK_BYTES - len(self._pending), len(data) - offset)
            self._pending.extend(data[offset:offset + take])
            offset += take
            if len(self._pending) == CHUNK_BYTES:
                self.flush()

    def _upload_chunk(self, payload: bytes) -> tuple[str, int, int]:
        size, count = self._size + len(payload), self._count + 1
        raw = _json({"previous": self._head, "bytes": size, "chunks": count,
                     "data": base64.b64encode(payload).decode("ascii")})
        head = hashlib.sha256(raw).hexdigest()
        self._store._put(self._prefix + "/objects/" + head, raw)
        return head, size, count

    def flush(self) -> None:
        if self._closed:
            return
        if self._handle is not None:
            self._handle.flush()
        elif self._pending:
            head, size, count = self._upload_chunk(bytes(self._pending))
            # Keep state unchanged until the visibility commit succeeds. Retrying
            # an ambiguous upload writes the exact same chunk and head again.
            self._store._put(self._prefix + "/manifest.json", _json({"version": 1, "head": head, "bytes": size, "chunks": count}))
            self._head, self._size, self._count = head, size, count
            self._pending.clear()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.flush()
        finally:
            if self._handle is not None:
                self._handle.close()
            self._closed = True
