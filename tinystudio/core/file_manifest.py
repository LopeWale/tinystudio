"""Content identities for retained regular-file directory trees."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def directory_manifest(path: str | Path) -> dict:
    """Hash regular files by relative name, rejecting links and empty bundles."""
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        raise ValueError("a server-resolved regular model directory is required")
    files = {}
    for source in sorted(path.rglob("*")):
        if source.is_symlink():
            raise ValueError("model artifacts must not contain symbolic links")
        if source.is_file():
            files[source.relative_to(path).as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
    if not files:
        raise ValueError("model directory is empty")
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"sha256": digest, "files": files}

