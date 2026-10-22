"""OpenAI-compatible routes over an engine, plus the internal streaming routes the API uses against the service."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from tinystudio.core.project import ProjectError
from tinystudio.inference.engine import LocalEngine, RemoteEngine


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float = 0.0
    top_p: float = 0.95
    max_tokens: int = Field(default=256, ge=1, le=8192)
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    repositories: Any = None
    origin: str | None = None


def _project_slug(request: Request) -> str | None:
    return request.headers.get("x-project") or request.query_params.get("project") or None


def build_v1_router(get_engine: Callable[[], LocalEngine | RemoteEngine], auth: Any, base_models: list[str] | None = None, auto_up: bool = True) -> APIRouter:
    """``/v1`` routes. ``auth`` is a FastAPI dependency (API keys and sessions on the API; the internal token on the service)."""
    router = APIRouter(dependencies=[Depends(auth)])
    bases = list(base_models or [])

    def wrap(action: Callable[[], Any]) -> Any:
        try:
            return action()
        except ProjectError as cause:
            raise HTTPException(status_code=404 if "does not exist" in str(cause) or "could not be loaded" in str(cause) else 400, detail=str(cause)) from cause

    @router.get("/v1/models")
    def models(request: Request) -> dict[str, Any]:
        return {"object": "list", "data": wrap(lambda: get_engine().models(_project_slug(request), bases))}

    @router.post("/v1/models/{slug}/up")
    def up(slug: str, request: Request) -> dict[str, Any]:
        model_id = slug.replace("~", "/")
        wrap(lambda: get_engine().load(model_id, _project_slug(request)))
        return {"id": model_id, "status": "up"}

    @router.post("/v1/models/{slug}/down")
    def down(slug: str, request: Request) -> dict[str, Any]:
        return wrap(lambda: get_engine().unload(slug.replace("~", "/"), _project_slug(request)))

    @router.post("/v1/chat/completions")
    def chat(body: ChatRequest, request: Request) -> Any:
        eng = get_engine()
        project = _project_slug(request)
        if not auto_up and body.model not in eng.loaded_ids(project):
            raise HTTPException(status_code=409, detail=f"model {body.model!r} is down; POST /v1/models/{body.model.replace('/', '~')}/up first")
        messages = [m.model_dump() for m in body.messages]
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
        created = int(time.time())
        tools = body.tools if body.tools and body.tool_choice != "none" else None
        json_mode = body.response_format if body.response_format and body.response_format.get("type") in ("json_object", "json_schema") else None
        if body.stream and not tools and not json_mode:
            def events() -> Iterator[str]:
                try:
                    for event in eng.stream(body.model, messages, body.max_tokens, body.temperature, body.top_p, project, body.repositories):
                        if "delta" in event:
                            yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': body.model, 'choices': [{'index': 0, 'delta': {'content': event['delta']}, 'finish_reason': None}]})}\n\n"
                        elif event.get("done"):
                            final: dict[str, Any] = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": body.model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": event["usage"]}
                            if event.get("document_chunks"):
                                final["tinystudio"] = {"document_chunks": event["document_chunks"]}
                            yield f"data: {json.dumps(final)}\n\ndata: [DONE]\n\n"
                except Exception as cause:  # noqa: BLE001 - report instead of dropping the stream
                    yield f"data: {json.dumps({'error': {'message': f'{type(cause).__name__}: {cause}'}})}\n\n"

            return StreamingResponse(events(), media_type="text/event-stream")
        generated = wrap(lambda: eng.generate(body.model, messages, body.max_tokens, body.temperature, body.top_p, tools, json_mode, project, body.repositories))
        message: dict[str, Any] = {"role": "assistant", "content": generated["content"] or None}
        if generated.get("tool_calls"):
            message["tool_calls"] = generated["tool_calls"]
        response: dict[str, Any] = {"id": completion_id, "object": "chat.completion", "created": created, "model": body.model, "choices": [{"index": 0, "message": message, "finish_reason": generated["finish_reason"]}], "usage": {"prompt_tokens": generated["prompt_tokens"], "completion_tokens": generated["completion_tokens"], "total_tokens": generated["prompt_tokens"] + generated["completion_tokens"]}}
        if json_mode:
            response["tinystudio"] = {"json_valid": generated.get("json_valid", False), "attempts": generated.get("attempts", 1)}
        if generated.get("document_chunks"):
            response.setdefault("tinystudio", {})["document_chunks"] = generated["document_chunks"]
        if body.stream:
            def one_chunk() -> Iterator[str]:
                yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': body.model, 'choices': [{'index': 0, 'delta': message, 'finish_reason': generated['finish_reason']}], 'usage': response['usage']})}\n\ndata: [DONE]\n\n"

            return StreamingResponse(one_chunk(), media_type="text/event-stream")
        return response

    return router


def build_internal_router(get_engine: Callable[[], LocalEngine], auth: Any) -> APIRouter:
    """Routes the API uses when the engine is remote: the raw event stream and the raw generate result."""
    router = APIRouter(dependencies=[Depends(auth)])

    @router.post("/internal/stream")
    def stream(body: ChatRequest, request: Request) -> StreamingResponse:
        def events() -> Iterator[str]:
            try:
                for event in get_engine().stream(body.model, [m.model_dump() for m in body.messages], body.max_tokens, body.temperature, body.top_p, _project_slug(request), body.repositories, body.origin):
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as cause:  # noqa: BLE001
                yield f"data: {json.dumps({'error': f'{type(cause).__name__}: {cause}'})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @router.post("/internal/generate")
    def generate(body: ChatRequest, request: Request) -> dict[str, Any]:
        try:
            return get_engine().generate(body.model, [m.model_dump() for m in body.messages], body.max_tokens, body.temperature, body.top_p, body.tools, body.response_format, _project_slug(request), body.repositories)
        except ProjectError as cause:
            raise HTTPException(status_code=400, detail=str(cause)) from cause

    return router
