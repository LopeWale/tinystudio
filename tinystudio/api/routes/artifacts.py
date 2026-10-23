"""Authorize direct private R2 uploads; validate staged JSONL before importing rows."""
from __future__ import annotations

import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from tinystudio.api.deps import get_db, project_dep, wrap
from tinystudio.db.models import Project
from tinystudio.services import datasets
from tinystudio.storage import project_key, storage

router = APIRouter(prefix="/api/p/{slug}", tags=["artifacts"])
MAX_UPLOAD = 512 * 1024 * 1024


class UploadBody(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(default="application/octet-stream", max_length=150)
    size: int = Field(gt=0, le=MAX_UPLOAD)


class ImportBody(BaseModel):
    name: str
    key: str


def upload_key(project: Project, key: str) -> str:
    prefix = project_key(project.id, "uploads") + "/"
    if not key.startswith(prefix) or not re.fullmatch(r"[a-f0-9]{32}/[a-zA-Z0-9_.-]+", key[len(prefix):]):
        raise HTTPException(400, "invalid project upload key")
    return key


@router.get("/artifacts/config")
def artifact_config(project: Project = Depends(project_dep)) -> dict[str, bool]:
    return {"direct_upload": storage().remote}


@router.post("/artifacts/uploads")
def authorize_upload(body: UploadBody, project: Project = Depends(project_dep)) -> dict[str, Any]:
    store = storage()
    if not store.remote:
        raise HTTPException(409, "direct uploads require R2")
    filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", Path(body.filename).name)
    key = project_key(project.id, "uploads", uuid.uuid4().hex, filename)
    url = store.presign(key, method="put_object", expires=900, content_type=body.content_type, content_length=body.size)
    return {"key": key, "url": url, "headers": {"Content-Type": body.content_type}, "expires_in": 900}


@router.post("/datasets/from-object")
def import_upload(body: ImportBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    store = storage()
    if not store.remote:
        raise HTTPException(409, "object imports require R2")
    key = upload_key(project, body.key)
    if not store.exists(key):
        raise HTTPException(400, "upload is incomplete or expired")
    if store.size(key) > MAX_UPLOAD:
        raise HTTPException(413, "upload exceeds 512 MiB")
    with tempfile.TemporaryDirectory(prefix="tinystudio-import-") as directory:
        target = Path(directory) / "upload.jsonl"
        store.download_file(key, target)
        if target.stat().st_size > MAX_UPLOAD:
            raise HTTPException(413, "upload exceeds 512 MiB")
        # Rows are validated and copied into the DB; a reusable staging URL cannot
        # subsequently mutate an imported dataset or an immutable snapshot.
        result = wrap(lambda: datasets.create_from_jsonl(db, project, body.name, target, Path(key).name))
    store.delete(key)
    return result
