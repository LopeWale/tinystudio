"""Project-scoped routes: models (up/down), the playground, stats, the catalog, repositories."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio import worker
from tinystudio.api.deps import current_user, get_db, project_dep, wrap
from tinystudio.auth import Principal
from tinystudio.config import config
from tinystudio.db.models import Dataset, Evaluation, Experiment, Job, Metric, Project, Snapshot
from tinystudio.inference.engine import engine
from tinystudio.services import models as models_svc
from tinystudio.services import repositories as repositories_svc
from tinystudio.services import traces as traces_svc
from tinystudio.training.recommend import catalog_as_dicts, catalog_entry

router = APIRouter(prefix="/api/p/{slug}", tags=["serving"])


class PlaygroundBody(BaseModel):
    model: str
    messages: list[dict[str, str]]
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(512, ge=1, le=8192)
    repositories: Any = None


class RepositoryBody(BaseModel):
    name: str
    description: str = ""
    embedding_model: str | None = None


class RepositoryQueryBody(BaseModel):
    text: str
    limit: int = Field(5, ge=1, le=50)
    similarity_threshold: float = Field(0.0, ge=-1.0, le=1.0)


@router.get("/models")
def models(project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    try:
        loaded = engine().loaded_ids(project.slug)
    except Exception as cause:  # noqa: BLE001 - the inference service may be down; the list still renders
        loaded = set()
        rows = models_svc.model_rows(db, project, loaded)
        for row in rows:
            row["status"] = "unknown"
        return rows
    return models_svc.model_rows(db, project, loaded)


@router.post("/models/{model_slug}/up")
def model_up(model_slug: str, project: Project = Depends(project_dep)) -> dict[str, Any]:
    model_id = model_slug.replace("~", "/")
    wrap(lambda: engine().load(model_id, project.slug))
    return {"id": model_id, "status": "up"}


@router.post("/models/{model_slug}/down")
def model_down(model_slug: str, project: Project = Depends(project_dep)) -> dict[str, Any]:
    return wrap(lambda: engine().unload(model_slug.replace("~", "/"), project.slug))


@router.post("/playground/chat")
def playground_chat(body: PlaygroundBody, project: Project = Depends(project_dep)) -> StreamingResponse:
    slug = project.slug

    def events() -> Iterator[str]:
        try:
            for event in engine().stream(body.model, body.messages, body.max_tokens, body.temperature, 0.95, slug, body.repositories, "playground"):
                if "document_chunks" in event and not event.get("done"):
                    yield f"data: {json.dumps({'document_chunks': event['document_chunks']})}\n\n"
                elif "delta" in event:
                    yield f"data: {json.dumps({'delta': event['delta']})}\n\n"
                elif event.get("done"):
                    yield f"data: {json.dumps({'done': True, 'seconds': event.get('seconds')})}\n\n"
        except Exception as cause:  # noqa: BLE001 - surfaced to the page instead of a dropped stream
            yield f"data: {json.dumps({'error': f'{type(cause).__name__}: {cause}'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


@router.get("/stats")
def stats(project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    from sqlalchemy import func

    experiments = list(db.scalars(select(Experiment).join(Job).where(Job.project_id == project.id)))
    datapoints = sum(int(v) for counts in db.scalars(select(Dataset.counts).where(Dataset.project_id == project.id)) for v in (counts or {}).values())  # maintained counters, no row scan
    try:
        loaded = sorted(engine().loaded_ids(project.slug))
    except Exception:  # noqa: BLE001
        loaded = []
    return {
        "datasets": int(db.scalar(select(func.count()).select_from(Dataset).where(Dataset.project_id == project.id)) or 0),
        "datapoints": datapoints,
        "snapshots": int(db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.project_id == project.id)) or 0),
        "jobs": int(db.scalar(select(func.count()).select_from(Job).where(Job.project_id == project.id)) or 0),
        "experiments": dict(Counter(e.state for e in experiments)),
        "training_seconds": round(sum(float((e.status or {}).get("seconds") or 0) for e in experiments), 1),
        "evaluations": int(db.scalar(select(func.count()).select_from(Evaluation).where(Evaluation.project_id == project.id)) or 0),
        "metrics": int(db.scalar(select(func.count()).select_from(Metric).where(Metric.project_id == project.id)) or 0),
        **traces_svc.stats(db, project),
        "models_loaded": loaded,
    }


@router.get("/catalog")
def catalog(project: Project = Depends(project_dep)) -> list[dict[str, Any]]:
    return catalog_as_dicts()


@router.get("/catalog/{model_id:path}")
def catalog_item(model_id: str, project: Project = Depends(project_dep)) -> dict[str, Any]:
    entry = catalog_entry(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="not in the catalog")
    return {"id": entry.id, "params_b": entry.params_b, "family": entry.family, "license": entry.license, "notes": entry.notes}


# ---------------------------------------------------------------- repositories
@router.get("/repositories")
def repositories_list(project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return repositories_svc.list_repositories(db, project)


@router.post("/repositories")
def repositories_create(body: RepositoryBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: repositories_svc.create_repository(db, project, body.name, body.description, body.embedding_model))


@router.get("/repositories/{name}")
def repository_meta(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return repositories_svc.repository_dict(wrap(lambda: repositories_svc.get_repository(db, project, name)))


@router.get("/repositories/{name}/chunks")
def repository_chunks(name: str, document: str | None = None, limit: int = 50, offset: str | None = None, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: repositories_svc.chunks(db, project, name, document, max(1, min(limit, 500)), offset))


@router.delete("/repositories/{name}")
def repository_delete(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    wrap(lambda: repositories_svc.delete_repository(db, project, name))
    return {"deleted": name}


@router.post("/repositories/{name}/documents")
async def repository_add_files(name: str, request: Request, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    """Multipart upload of documents (files[]) plus optional urls and paths (one per line); indexing runs as a task."""
    repo = wrap(lambda: repositories_svc.get_repository(db, project, name))
    form = await request.form()
    uploads = config().uploads_dir
    uploads.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for item in form.getlist("files"):
        if hasattr(item, "filename") and hasattr(item, "read") and getattr(item, "filename", None):
            target = uploads / f"{uuid.uuid4().hex[:8]}-{Path(item.filename).name}"
            target.write_bytes(await item.read())
            paths.append(str(target))
    paths += [p.strip() for p in str(form.get("paths") or "").splitlines() if p.strip()]
    urls = [u.strip() for u in str(form.get("urls") or "").splitlines() if u.strip()]
    if not paths and not urls:
        raise HTTPException(400, "upload at least one file or give a URL")
    return wrap(lambda: worker.enqueue(db, "repo.add", {"repository_id": repo.id, "repository": name, "paths": paths, "urls": urls}, project, principal.user))


@router.delete("/repositories/{name}/documents/{document:path}")
def repository_remove_document(name: str, document: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: repositories_svc.remove_document(db, project, name, document))


@router.post("/repositories/{name}/query")
def repository_query(name: str, body: RepositoryQueryBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return wrap(lambda: repositories_svc.query(db, project, name, body.text, body.limit, body.similarity_threshold))
