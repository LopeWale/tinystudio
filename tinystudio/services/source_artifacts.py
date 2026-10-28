"""Retained input files and portable task references, independent of a host path."""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from tinystudio.storage import ArtifactStorage, StorageError, project_key, storage

MAX_SOURCE_BYTES = 512 * 1024**2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SourceArtifacts:
    """Own immutable source publication and project-scoped materialization."""

    def __init__(self, project_id: str, backend: ArtifactStorage | None = None) -> None:
        self.project_id = project_id
        self.backend = backend or storage()

    def retain(self, path: Path, name: str | None = None) -> dict[str, Any]:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise StorageError("source must be a regular file")
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise StorageError("source exceeds 512 MiB")
        filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", Path(name or path.name).name)
        if filename in {"", ".", ".."}:
            raise StorageError("invalid source filename")
        # Publish a stable copy, never a file the caller may still be changing.
        with tempfile.TemporaryDirectory(prefix="tinystudio-source-") as temporary:
            frozen = Path(temporary) / "source"
            shutil.copyfile(path, frozen)
            size, digest = frozen.stat().st_size, _sha256(frozen)
            if size > MAX_SOURCE_BYTES:
                raise StorageError("source exceeds 512 MiB")
            key = project_key(self.project_id, "sources", digest, filename)
            self.backend.upload_file(frozen, key)
            verified = self.backend.download_file(key, Path(temporary) / "verified")
            if verified.stat().st_size != size or _sha256(verified) != digest:
                raise StorageError("source publication checksum mismatch")
        return {"kind": "artifact", "name": filename, "storage_key": key, "sha256": digest, "size": size}

    def validate(self, reference: Any) -> dict[str, Any]:
        if not isinstance(reference, dict) or reference.get("kind") != "artifact":
            raise StorageError("source must be a retained artifact reference")
        name, digest, size = reference.get("name"), reference.get("sha256"), reference.get("size")
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or name in {".", ".."}:
            raise StorageError("invalid source filename")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise StorageError("invalid source checksum")
        if type(size) is not int or not 0 <= size <= MAX_SOURCE_BYTES:
            raise StorageError("invalid source size")
        if reference.get("storage_key") != project_key(self.project_id, "sources", digest, name):
            raise StorageError("source reference does not belong to this project")
        return dict(reference)

    def materialize(self, reference: Any, directory: Path) -> Path:
        ref = self.validate(reference)
        path = self.backend.download_file(ref["storage_key"], directory / ref["name"])
        if path.stat().st_size != ref["size"] or _sha256(path) != ref["sha256"]:
            path.unlink(missing_ok=True)
            raise StorageError("retained source checksum mismatch")
        return path


def prepare_task_sources(project_id: str | None, kind: str, args: dict[str, Any]) -> dict[str, Any]:
    """Freeze API/CLI source paths before admitting work to a different worker."""
    field = {"dataset.synthetic": "files", "repo.add": "paths"}.get(kind)
    if field is None or not args.get(field):
        return args
    if not project_id:
        raise StorageError("retained sources require a project")
    store = SourceArtifacts(project_id)
    sources = [store.validate(value) if isinstance(value, dict) else store.retain(Path(value).expanduser()) for value in args[field]]
    return {**args, field: sources}


@contextmanager
def materialized_sources(project_id: str | None, values: list[Any] | None) -> Iterator[list[Path]]:
    """Use short-lived, verified copies on the executing host and remove them afterward."""
    if not values:
        yield []
        return
    if not project_id:
        raise StorageError("retained sources require a project")
    store = SourceArtifacts(project_id)
    with tempfile.TemporaryDirectory(prefix="tinystudio-inputs-") as temporary:
        paths = []
        for index, value in enumerate(values):
            if isinstance(value, dict):
                paths.append(store.materialize(value, Path(temporary) / str(index)))
            elif store.backend.remote:
                raise StorageError("task contains a host-local source; submit it again to retain its input in R2")
            else:
                paths.append(Path(value).expanduser())
        yield paths
