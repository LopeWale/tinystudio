"""The engine the inference routes drive: loads models by id, generates, streams, and logs traces.

Model ids: ``<project-slug>/<job>/<number>[@step]``, ``<project-slug>/ft-<job>-<number>``, a Hub id, or a model
directory. Bare ``job/number`` works when a project is given (the web interface always sends one).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx

from tinystudio.config import config
from tinystudio.core.project import ProjectError, now
from tinystudio.db import session
from tinystudio.db.models import Project
from tinystudio.serving.runner import ModelRunner
from tinystudio.services import models as models_svc
from tinystudio.services import projects as projects_svc
from tinystudio.services import repositories as repositories_svc
from tinystudio.services import traces as traces_svc


def split_model_id(model_id: str, project_slug: str | None) -> tuple[str | None, str]:
    """(project slug, local id). Three segments ending in digits (or a ``ft-`` alias after a slug) name a project."""
    parts = model_id.split("/")
    if len(parts) == 3 and parts[2].partition("@")[0].isdigit():
        return parts[0], f"{parts[1]}/{parts[2]}"
    if len(parts) == 2 and parts[1].startswith("ft-"):
        return parts[0], parts[1]
    return project_slug, model_id


class LocalEngine:
    """Models loaded in this process."""

    def __init__(self) -> None:
        self.loaded: dict[str, ModelRunner] = {}
        self.lock = threading.Lock()
        self.recent: list[dict[str, Any]] = []

    def _project(self, db: Any, slug: str | None) -> Project | None:
        return projects_svc.get_project(db, slug) if slug else None

    def key(self, project_slug: str | None, model_id: str) -> str:
        slug, local = split_model_id(model_id, project_slug)
        return f"{slug}/{local}" if slug and (local.partition("/")[2].partition("@")[0].isdigit() or local.startswith("ft-")) else local

    def load(self, model_id: str, project_slug: str | None = None) -> ModelRunner:
        slug, local = split_model_id(model_id, project_slug)
        key = self.key(project_slug, model_id)
        with self.lock:
            if key in self.loaded:
                return self.loaded[key]
        with session() as db:
            resolved = models_svc.resolve(db, self._project(db, slug), local)
        try:
            runner = ModelRunner(resolved)
        except OSError as cause:
            raise ProjectError(f"model {model_id!r} could not be loaded: {cause}") from cause
        with self.lock:
            self.loaded[key] = runner
        return runner

    def unload(self, model_id: str, project_slug: str | None = None) -> dict[str, Any]:
        key = self.key(project_slug, model_id)
        with self.lock:
            runner = self.loaded.pop(key, None)
        if runner is not None:
            runner.unload()
        return {"id": model_id, "status": "down"}

    def loaded_ids(self, project_slug: str | None = None) -> set[str]:
        """Ids as the project sees them (``job/number`` within the project; full keys otherwise)."""
        out = set()
        for key in list(self.loaded):
            if project_slug and key.startswith(project_slug + "/"):
                out.add(key[len(project_slug) + 1 :])
            elif "/" not in key or not key.rpartition("/")[2].partition("@")[0].isdigit():
                out.add(key)
        return out

    def models(self, project_slug: str | None, base_models: list[str]) -> list[dict[str, Any]]:
        ids = list(base_models)
        with session() as db:
            if project_slug:
                ids += models_svc.deployable_ids(db, self._project(db, project_slug))
        loaded = self.loaded_ids(project_slug)
        for key in loaded:
            if key not in ids:
                ids.append(key)
        return [{"id": i, "object": "model", "owned_by": "tinystudio", "status": "up" if i in loaded else "down"} for i in ids]

    def trace(self, project_slug: str | None, record: dict[str, Any]) -> None:
        self.recent.append(record)
        del self.recent[:-200]
        if project_slug:
            with session() as db:
                traces_svc.append_trace(db, self._project(db, project_slug), record)

    def augment(self, project_slug: str | None, messages: list[dict[str, Any]], repositories: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not repositories:
            return messages, []
        if not project_slug:
            raise ProjectError("repositories need a project")
        with session() as db:
            return repositories_svc.augment_messages(db, self._project(db, project_slug), messages, repositories)

    def stream(self, model_id: str, messages: list[dict[str, Any]], max_tokens: int, temperature: float, top_p: float = 0.95, project_slug: str | None = None, repositories: Any = None, origin: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield {"document_chunks"}, {"delta"}, then {"done", usage, seconds}; traces the exchange."""
        runner = self.load(model_id, project_slug)
        prompt, chunks = self.augment(project_slug, messages, repositories)
        if chunks:
            yield {"document_chunks": chunks}
        started = time.perf_counter()
        pieces: list[str] = []
        for delta in runner.stream(prompt, max_tokens, temperature, top_p):
            pieces.append(delta)
            yield {"delta": delta}
        text = "".join(pieces).strip()
        prompt_tokens, completion_tokens = runner.count_tokens(prompt, text)
        seconds = round(time.perf_counter() - started, 3)
        self.trace(project_slug, {"id": f"{'play' if origin == 'playground' else 'chatcmpl'}-{uuid.uuid4().hex[:16]}", "time": now(), "model": model_id, "messages": messages, "response": text, "seconds": seconds, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "score": None, "origin": origin, "document_chunks": chunks or None})
        yield {"done": True, "seconds": seconds, "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens}, "document_chunks": chunks}

    def generate(self, model_id: str, messages: list[dict[str, Any]], max_tokens: int, temperature: float, top_p: float = 0.95, tools: list[dict[str, Any]] | None = None, response_format: dict[str, Any] | None = None, project_slug: str | None = None, repositories: Any = None) -> dict[str, Any]:
        runner = self.load(model_id, project_slug)
        prompt, chunks = self.augment(project_slug, messages, repositories)
        if response_format:
            generated = runner.generate_json(prompt, response_format, max_tokens, temperature, top_p)
        else:
            generated = runner.generate(prompt, max_tokens, temperature, top_p, tools)
        self.trace(project_slug, {"id": f"chatcmpl-{uuid.uuid4().hex[:16]}", "time": now(), "model": model_id, "messages": messages, "response": generated["content"] or json.dumps(generated.get("tool_calls")), "seconds": generated["seconds"], "prompt_tokens": generated["prompt_tokens"], "completion_tokens": generated["completion_tokens"], "score": None, "document_chunks": chunks or None})
        return {**generated, "document_chunks": chunks}


class RemoteEngine:
    """The same operations over HTTP against the ``tinystudio inference`` service (internal token)."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(600.0, connect=10.0), headers={"Authorization": f"Bearer {token}"})

    def _headers(self, project_slug: str | None) -> dict[str, str]:
        return {"X-Project": project_slug} if project_slug else {}

    def _check(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ProjectError(f"inference service: {detail}")
        return response.json()

    def load(self, model_id: str, project_slug: str | None = None) -> dict[str, Any]:
        return self._check(self.http.post(f"/v1/models/{model_id.replace('/', '~')}/up", headers=self._headers(project_slug)))

    def unload(self, model_id: str, project_slug: str | None = None) -> dict[str, Any]:
        return self._check(self.http.post(f"/v1/models/{model_id.replace('/', '~')}/down", headers=self._headers(project_slug)))

    def loaded_ids(self, project_slug: str | None = None) -> set[str]:
        return {m["id"] for m in self.models(project_slug, []) if m["status"] == "up"}

    def models(self, project_slug: str | None, base_models: list[str]) -> list[dict[str, Any]]:
        data = self._check(self.http.get("/v1/models", headers=self._headers(project_slug)))["data"]
        known = {m["id"] for m in data}
        return data + [{"id": b, "object": "model", "owned_by": "tinystudio", "status": "down"} for b in base_models if b not in known]

    def stream(self, model_id: str, messages: list[dict[str, Any]], max_tokens: int, temperature: float, top_p: float = 0.95, project_slug: str | None = None, repositories: Any = None, origin: str | None = None) -> Iterator[dict[str, Any]]:
        body = {"model": model_id, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "top_p": top_p, "stream": True, "repositories": repositories, "origin": origin}
        with self.http.stream("POST", "/internal/stream", json=body, headers=self._headers(project_slug)) as response:
            if response.status_code >= 400:
                raise ProjectError(f"inference service: {response.read().decode()[:300]}")
            for line in response.iter_lines():
                if line.startswith("data:"):
                    event = json.loads(line[5:])
                    if event.get("error"):
                        raise ProjectError(event["error"])
                    yield event

    def generate(self, model_id: str, messages: list[dict[str, Any]], max_tokens: int, temperature: float, top_p: float = 0.95, tools: list[dict[str, Any]] | None = None, response_format: dict[str, Any] | None = None, project_slug: str | None = None, repositories: Any = None) -> dict[str, Any]:
        body = {"model": model_id, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "top_p": top_p, "tools": tools, "response_format": response_format, "repositories": repositories}
        return self._check(self.http.post("/internal/generate", json=body, headers=self._headers(project_slug)))

    def ping(self) -> bool:
        try:
            return self.http.get("/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False


_engine: LocalEngine | RemoteEngine | None = None


def engine() -> LocalEngine | RemoteEngine:
    """The API's engine: remote when TINYSTUDIO_INFERENCE_URL is set, otherwise models load in this process."""
    global _engine
    if _engine is None:
        cfg = config()
        if cfg.inference_url:
            from tinystudio.auth import ensure_instance

            with session() as db:
                token = ensure_instance(db).internal_token
            _engine = RemoteEngine(cfg.inference_url, token)
        else:
            _engine = LocalEngine()
    return _engine


def set_engine(value: LocalEngine | RemoteEngine | None) -> None:
    global _engine
    _engine = value
