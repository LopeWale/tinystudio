"""Global routes: health, config, settings, tasks (with live logs), Hugging Face Hub, the assistant."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from tinystudio import db as db_mod
from tinystudio import vectors, worker
from tinystudio.api.deps import current_user, get_db, instance_secret, require_admin, wrap
from tinystudio.auth import Principal, issue_service_token
from tinystudio.config import config, redacted
from tinystudio.db.models import Project
from tinystudio.integrations import hub
from tinystudio.services import projects as projects_svc
from tinystudio.services import settings as settings_svc
from tinystudio.services import tasks as tasks_svc

router = APIRouter(prefix="/api", tags=["system"])


class SettingsBody(BaseModel):
    values: dict[str, str | None]


class SettingsTestBody(BaseModel):
    what: str  # helper | anthropic | openai | hub | database | redis | qdrant | inference


class HubDownloadBody(BaseModel):
    repo_id: str
    revision: str | None = None


class HubTokenBody(BaseModel):
    token: str


class HubUploadBody(BaseModel):
    project: str
    kind: str
    name: str
    repo_id: str
    private: bool = True
    merge: bool = False


class UiResultBody(BaseModel):
    id: str
    result: Any = None


class AssistantBody(BaseModel):
    provider: str = "anthropic"
    messages: list[dict[str, str]]
    allow_destructive: bool = False
    model: str | None = None
    context: dict[str, Any] | None = None
    with_ui: bool = True
    project: str | None = None


@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness plus the state of each backing store (no auth: load balancers and compose healthchecks call it)."""
    from tinystudio import __version__

    return {"ok": True, "version": __version__, "database": db_mod.ping(), "redis": worker.ping(), "qdrant": vectors.ping()}


@router.get("/config")
def show_config(_: Principal = Depends(current_user)) -> dict[str, Any]:
    cfg = config()
    from tinystudio.inference.engine import engine

    eng = engine()
    inference = {"mode": "remote" if cfg.inference_url else "in-process", "url": cfg.inference_url, "reachable": eng.ping() if hasattr(eng, "ping") else True}
    return {"database_url": redacted(cfg.database_url), "redis_url": redacted(cfg.redis_url), "qdrant_url": cfg.qdrant_url, "data_dir": str(cfg.data_dir), "host": cfg.host, "port": cfg.port, "device": cfg.device, "dtype": cfg.dtype, "inference": inference, "database_version": db_mod.database_version()}


@router.get("/settings")
def settings_show(db: Session = Depends(get_db), _: Principal = Depends(require_admin)) -> dict[str, Any]:
    return {"settings": settings_svc.describe(db), "updated": settings_svc.updated_at(db)}


@router.put("/settings")
def settings_save(body: SettingsBody, db: Session = Depends(get_db), principal: Principal = Depends(require_admin)) -> dict[str, Any]:
    from tinystudio.integrations.assistant import forget_models

    wrap(lambda: settings_svc.save(db, body.values, principal.user.id))
    forget_models()
    return {"settings": settings_svc.describe(db), "updated": settings_svc.updated_at(db)}


@router.post("/settings/test")
def settings_test(body: SettingsTestBody, db: Session = Depends(get_db), _: Principal = Depends(require_admin)) -> dict[str, Any]:
    from tinystudio.core.llm import LLMClient, LLMError

    settings_svc.apply(db)
    try:
        if body.what == "helper":
            client = LLMClient.from_env()
            model = client.resolve_model()
            reply = client.chat([{"role": "user", "content": "Reply with the single word OK."}], max_tokens=8)
            return {"ok": True, "detail": f"{client.base_url} answered with model {model}: {reply.strip()[:60]!r}"}
        if body.what in ("anthropic", "openai"):
            from tinystudio.integrations.assistant import forget_models, list_models

            forget_models()
            info = list_models()[body.what]
            if not info["available"]:
                return {"ok": False, "detail": "no credentials saved"}
            if info["error"]:
                return {"ok": False, "detail": info["error"]}
            return {"ok": True, "detail": f"{len(info['models'])} models listed; default {info['default']}"}
        if body.what == "hub":
            token = os.environ.get("HF_TOKEN")
            if not token:
                return {"ok": False, "detail": "no token saved"}
            from huggingface_hub import HfApi

            who = HfApi().whoami(token=token)
            return {"ok": True, "detail": f"token belongs to {who.get('name')} ({who.get('type')})"}
        if body.what == "database":
            return {"ok": db_mod.ping(), "detail": f"PostgreSQL {db_mod.database_version() or 'unreachable'} at {redacted(config().database_url)}"}
        if body.what == "redis":
            return {"ok": worker.ping(), "detail": f"Redis at {redacted(config().redis_url)}; {len(worker.alive_job_ids()) if worker.ping() else 0} queued or running jobs"}
        if body.what == "qdrant":
            ok = vectors.ping()
            return {"ok": ok, "detail": f"Qdrant at {config().qdrant_url}: {len(vectors.client().get_collections().collections) if ok else 'unreachable'} collections"}
        if body.what == "inference":
            from tinystudio.inference.engine import engine

            eng = engine()
            if hasattr(eng, "ping"):
                return {"ok": eng.ping(), "detail": f"inference service at {config().inference_url}"}
            return {"ok": True, "detail": "models load inside the API process (no TINYSTUDIO_INFERENCE_URL)"}
    except LLMError as cause:
        return {"ok": False, "detail": str(cause)}
    except Exception as cause:  # noqa: BLE001 - a connection test must report, not crash
        return {"ok": False, "detail": f"{type(cause).__name__}: {cause}"}
    raise HTTPException(status_code=400, detail="what must be helper, anthropic, openai, hub, database, redis, qdrant, or inference")


# ---------------------------------------------------------------- tasks
@router.get("/tasks")
def list_tasks(project: str | None = None, limit: int = 50, db: Session = Depends(get_db), _: Principal = Depends(current_user)) -> list[dict[str, Any]]:
    row = projects_svc.get_project(db, project) if project else None
    return tasks_svc.list_tasks(db, row, max(1, min(limit, 500)))


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_user)) -> dict[str, Any]:
    return tasks_svc.task_dict(wrap(lambda: tasks_svc.get_task(db, task_id)))


@router.get("/tasks/{task_id}/log")
def task_log(task_id: str, tail: int = 500, db: Session = Depends(get_db), _: Principal = Depends(current_user)) -> dict[str, Any]:
    task = wrap(lambda: tasks_svc.get_task(db, task_id))
    path = config().task_log(task.id)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else list(task.log_tail or [])
    return {"id": task.id, "state": task.state, "lines": lines[-tail:]}


@router.get("/tasks/{task_id}/events")
def task_events(task_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_user)) -> StreamingResponse:
    """Live log lines as server-sent events; ends when the task finishes."""
    task = wrap(lambda: tasks_svc.get_task(db, task_id))
    tail = list(task.log_tail or [])
    state = task.state

    def events() -> Iterator[str]:
        for line in tail:
            yield f"data: {json.dumps({'line': line})}\n\n"
        if state not in ("queued", "running"):
            yield f"data: {json.dumps({'state': state, 'done': True})}\n\n"
            return
        for line in worker.subscribe(task_id, timeout=15.0):
            if line is None:
                with db_mod.session() as s:
                    current = tasks_svc.get_task(s, task_id)
                    if current.state not in ("queued", "running"):
                        yield f"data: {json.dumps({'state': current.state, 'done': True})}\n\n"
                        return
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps({'line': line})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str, db: Session = Depends(get_db), _: Principal = Depends(current_user)) -> dict[str, Any]:
    return wrap(lambda: worker.stop(db, task_id))


# ---------------------------------------------------------------- hugging face hub
@router.get("/hub/search")
def hub_search(q: str = "", limit: int = 15, max_params_b: float | None = 2.0, author: str | None = None, task: str = "text-generation", _: Principal = Depends(current_user)) -> list[dict[str, Any]]:
    return wrap(lambda: hub.search(q, limit, max_params_b, author, task))


@router.get("/hub/models")
def hub_models(_: Principal = Depends(current_user)) -> dict[str, Any]:
    return {"models": hub.cached_models(), "free_disk_gb": hub.free_disk_gb(config().data_dir if config().data_dir.exists() else None), "token": hub.token_status()}


@router.get("/hub/models/{repo_id:path}/info")
def hub_model_info(repo_id: str, _: Principal = Depends(current_user)) -> dict[str, Any]:
    return wrap(lambda: hub.model(repo_id))


@router.post("/hub/download")
def hub_download(body: HubDownloadBody, db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    return wrap(lambda: worker.enqueue(db, "hub.download", body.model_dump(), None, principal.user))


@router.delete("/hub/models/{repo_id:path}")
def hub_delete(repo_id: str, _: Principal = Depends(require_admin)) -> dict[str, Any]:
    return wrap(lambda: hub.delete_cached(repo_id))


@router.get("/hub/token")
def hub_token(_: Principal = Depends(current_user)) -> dict[str, Any]:
    return hub.token_status()


@router.post("/hub/token")
def hub_set_token(body: HubTokenBody, db: Session = Depends(get_db), principal: Principal = Depends(require_admin)) -> dict[str, Any]:
    token = body.token.strip()
    if not token.startswith("hf_"):
        raise HTTPException(status_code=400, detail="Hugging Face tokens start with hf_")
    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    try:
        who = HfApi().whoami(token=token)
    except HfHubHTTPError as cause:
        raise HTTPException(status_code=400, detail=f"the token was rejected: {cause}") from cause
    settings_svc.save(db, {"HF_TOKEN": token}, principal.user.id)
    return {"configured": True, "user": who.get("name"), "type": who.get("type")}


@router.post("/hub/upload")
def hub_upload(body: HubUploadBody, db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    if body.kind not in ("experiment", "dataset", "snapshot"):
        raise HTTPException(status_code=400, detail="kind must be experiment, dataset, or snapshot")
    project = wrap(lambda: projects_svc.get_project(db, body.project))
    return wrap(lambda: worker.enqueue(db, "hub.upload", body.model_dump(exclude={"project"}), project, principal.user))


# ---------------------------------------------------------------- assistant
@router.get("/assistant/status")
def assistant_status(request: Request, project: str | None = None, _: Principal = Depends(current_user)) -> dict[str, Any]:
    import sys
    from pathlib import Path

    from tinystudio.integrations.assistant import status as assistant_status_fn
    from tinystudio.integrations.tools import Studio, tool_specs

    base = str(request.base_url).rstrip("/")
    return {**assistant_status_fn(), "mcp": {"command": f"{Path(sys.executable).parent / 'tinystudio'} mcp --url {base}" + (f" --project {project}" if project else "") + "   (with TINYSTUDIO_API_KEY=ts-... in the environment)", "tools": len(tool_specs(Studio("http://127.0.0.1", None, None)))}}


@router.get("/assistant/models")
def assistant_models(_: Principal = Depends(current_user)) -> dict[str, Any]:
    from tinystudio.integrations.assistant import list_models

    return list_models()


@router.post("/assistant/ui-result")
def assistant_ui_result(body: UiResultBody, _: Principal = Depends(current_user)) -> dict[str, Any]:
    from tinystudio.integrations.assistant import UiBridge

    if not UiBridge.deliver(body.id, body.result):
        raise HTTPException(status_code=404, detail="no pending interface request with that id (it may have timed out)")
    return {"delivered": body.id}


@router.post("/assistant/chat")
def assistant_chat(body: AssistantBody, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> StreamingResponse:
    from tinystudio.integrations.assistant import stream_events
    from tinystudio.integrations.tools import Studio

    if not body.messages or body.messages[-1].get("role") != "user":
        raise HTTPException(status_code=400, detail="the last message must come from the user")
    if body.provider not in ("anthropic", "openai"):
        raise HTTPException(status_code=400, detail="provider must be anthropic or openai")
    if body.project:
        wrap(lambda: projects_svc.get_project(db, body.project))
    settings_svc.apply(db)
    # the assistant calls this API back as the signed-in user, over the loopback address, for one hour at most
    self_url = os.environ.get("TINYSTUDIO_SELF_URL") or f"http://127.0.0.1:{config().port}"
    studio = Studio(self_url, body.project, issue_service_token(instance_secret(db), principal.user.id),
                    cf_access_client_id="", cf_access_client_secret="",
                    access_assertion=getattr(request.state, "cloudflare_assertion", None))

    def events() -> Iterator[str]:
        for event in stream_events(body.provider, studio, body.messages, body.allow_destructive, body.model, body.context, body.with_ui):
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})
