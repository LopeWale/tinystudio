"""Local server: the Studio-style web interface, its JSON API, and an OpenAI-compatible endpoint.

OpenAI-compatible routes:

- ``GET  /v1/models``                 every deployable id, with ``status`` up | down
- ``POST /v1/models/{id}/up``         load into memory (id uses ``~`` instead of ``/``, e.g. ``support-bot~1``; aliases ``ft-<job>-<n>`` work too)
- ``POST /v1/models/{id}/down``       unload
- ``POST /v1/chat/completions``       standard chat body; ``stream: true`` sends SSE chunks
- ``GET  /v1/traces``                 the last N traces logged by this server

When the project holds at least one API key (web interface → API Keys), the
``/v1`` routes require ``Authorization: Bearer <key>``. The web interface
talks to ``/api`` and is never gated.

Every completion is appended to ``<project>/traces.jsonl`` with its model id,
latency, and token counts.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tinystudio.serving.keys import verify_key
from tinystudio.core.project import Project, ProjectError, now
from tinystudio.serving.runner import ModelRunner, resolve_model
from tinystudio.core.traces import append_trace
from tinystudio.training.train import list_experiments

UI_DIR = Path(__file__).resolve().parents[1] / "ui"


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float = 0.0
    top_p: float = 0.95
    max_tokens: int = Field(default=256, ge=1, le=4096)
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None  # "auto" | "none" | {"type": "function", ...}; "required" is not enforceable on these models
    response_format: dict[str, Any] | None = None  # {"type": "json_object"} or {"type": "json_schema", "json_schema": {"schema": {...}}}
    repositories: Any = None  # RAG: {"ids": ["docs"], "limit": 3, "similarity_threshold": 0.3} (Prem's shape) or ["docs"]; retrieved chunks are returned in tinystudio.document_chunks


def _slug_to_id(slug: str) -> str:
    return slug.replace("~", "/")


def deployable_ids(project: Project | None, extra: list[str]) -> list[str]:
    ids = list(extra)
    if project is not None:
        for job in project.list_names("jobs"):
            for experiment in list_experiments(project, job):
                if experiment.get("state") == "succeeded":
                    ids.append(f"{job}/{experiment['number']}")
    return ids


def create_app(project: Project | None, base_models: list[str], auto_up: bool = True, trace_limit: int = 200, ui: bool = True) -> FastAPI:
    app = FastAPI(title="tinystudio", version="0.1.0")
    loaded: dict[str, ModelRunner] = {}
    recent: list[dict[str, Any]] = []

    def require_key(request: Request) -> None:
        if project is None:
            return
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else None
        if not verify_key(project, token):
            raise HTTPException(status_code=401, detail="a valid API key is required: Authorization: Bearer <key> (create one in the web interface under API Keys)")

    def load(model_id: str) -> ModelRunner:
        if model_id in loaded:
            return loaded[model_id]
        try:
            runner = ModelRunner(resolve_model(project, model_id))
        except ProjectError as cause:
            raise HTTPException(status_code=404, detail=str(cause)) from cause
        except OSError as cause:
            raise HTTPException(status_code=404, detail=f"model {model_id!r} could not be loaded: {cause}") from cause
        loaded[model_id] = runner
        return runner

    def unload(model_id: str) -> dict[str, Any]:
        runner = loaded.pop(model_id, None)
        if runner is not None:
            runner.unload()
        return {"id": model_id, "status": "down"}

    def trace(record: dict[str, Any]) -> None:
        recent.append(record)
        del recent[:-trace_limit]
        if project is not None:
            append_trace(project, record)

    @app.get("/v1/models")
    def models(request: Request) -> dict[str, Any]:
        require_key(request)
        ids = deployable_ids(project, base_models)
        for model_id in loaded:
            if model_id not in ids:
                ids.append(model_id)
        return {
            "object": "list",
            "data": [
                {"id": model_id, "object": "model", "owned_by": "tinystudio", "status": "up" if model_id in loaded else "down"}
                for model_id in ids
            ],
        }

    @app.post("/v1/models/{slug}/up")
    def up(slug: str, request: Request) -> dict[str, Any]:
        require_key(request)
        model_id = _slug_to_id(slug)
        load(model_id)
        return {"id": model_id, "status": "up"}

    @app.post("/v1/models/{slug}/down")
    def down(slug: str, request: Request) -> dict[str, Any]:
        require_key(request)
        return unload(_slug_to_id(slug))

    @app.get("/v1/traces")
    def traces(request: Request, limit: int = 50) -> dict[str, Any]:
        require_key(request)
        return {"data": recent[-limit:]}

    @app.post("/v1/chat/completions")
    def chat(body: ChatRequest, request: Request) -> Any:
        require_key(request)
        if body.model not in loaded and not auto_up:
            raise HTTPException(status_code=409, detail=f"model {body.model!r} is down; POST /v1/models/{body.model.replace('/', '~')}/up first")
        runner = load(body.model)
        messages = [m.model_dump() for m in body.messages]
        document_chunks: list[dict[str, Any]] = []
        if body.repositories:
            if project is None:
                raise HTTPException(status_code=400, detail="repositories need a project (start the server inside one)")
            from tinystudio.rag.repositories import augment_messages

            try:
                messages, document_chunks = augment_messages(project, messages, body.repositories)
            except ProjectError as cause:
                raise HTTPException(status_code=400, detail=str(cause)) from cause
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
        created = int(time.time())
        tools = body.tools if body.tools and body.tool_choice != "none" else None
        json_mode = body.response_format if body.response_format and body.response_format.get("type") in ("json_object", "json_schema") else None

        if body.stream and not tools and not json_mode:
            def events() -> Iterator[str]:
                pieces: list[str] = []
                started = time.perf_counter()
                try:
                    for delta in runner.stream(messages, body.max_tokens, body.temperature, body.top_p):
                        pieces.append(delta)
                        chunk = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": body.model, "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]}
                        yield f"data: {json.dumps(chunk)}\n\n"
                except Exception as cause:  # noqa: BLE001 - report instead of dropping the stream
                    yield f"data: {json.dumps({'error': {'message': f'{type(cause).__name__}: {cause}'}})}\n\n"
                    return
                response_text = "".join(pieces).strip()
                prompt_tokens, completion_tokens = runner.count_tokens(messages, response_text)
                final = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": body.model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens}}
                if document_chunks:
                    final["tinystudio"] = {"document_chunks": document_chunks}
                yield f"data: {json.dumps(final)}\n\ndata: [DONE]\n\n"
                trace({"id": completion_id, "time": now(), "model": body.model, "messages": messages, "response": response_text, "seconds": round(time.perf_counter() - started, 3), "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "score": None})

            return StreamingResponse(events(), media_type="text/event-stream")

        if json_mode:
            generated = runner.generate_json(messages, json_mode, body.max_tokens, body.temperature, body.top_p)
        else:
            generated = runner.generate(messages, body.max_tokens, body.temperature, body.top_p, tools)
        message: dict[str, Any] = {"role": "assistant", "content": generated["content"] or None}
        if generated.get("tool_calls"):
            message["tool_calls"] = generated["tool_calls"]
        trace({"id": completion_id, "time": now(), "model": body.model, "messages": messages, "response": generated["content"] or json.dumps(generated.get("tool_calls")), "seconds": generated["seconds"], "prompt_tokens": generated["prompt_tokens"], "completion_tokens": generated["completion_tokens"], "score": None})
        response = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": body.model,
            "choices": [{"index": 0, "message": message, "finish_reason": generated["finish_reason"]}],
            "usage": {"prompt_tokens": generated["prompt_tokens"], "completion_tokens": generated["completion_tokens"], "total_tokens": generated["prompt_tokens"] + generated["completion_tokens"]},
        }
        if json_mode:
            response["tinystudio"] = {"json_valid": generated.get("json_valid", False), "attempts": generated.get("attempts", 1)}
        if document_chunks:
            response.setdefault("tinystudio", {})["document_chunks"] = document_chunks
        if body.stream:  # tools or JSON mode: emit the finished message as one chunk so streaming clients still work
            def one_chunk() -> Iterator[str]:
                chunk = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": body.model, "choices": [{"index": 0, "delta": message, "finish_reason": generated["finish_reason"]}], "usage": response["usage"]}
                yield f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"

            return StreamingResponse(one_chunk(), media_type="text/event-stream")
        return response

    if project is not None and ui:
        from tinystudio.api.studio import build_studio_router
        from tinystudio.api.tasks import TaskRunner, build_router

        tasks = TaskRunner(project)
        app.include_router(build_router(project, loaded, load, unload, tasks))
        app.include_router(build_studio_router(project, loaded, load, unload, tasks.start, trace))
        app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(UI_DIR / "favicon.svg", media_type="image/svg+xml")

    return app
