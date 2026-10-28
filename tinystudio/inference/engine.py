"""The engine the inference routes drive: loads models by id, generates, streams, and logs traces.

Model ids: ``<project-slug>/<job>/<number>[@step]``, ``<project-slug>/ft-<job>-<number>``, a Hub id, or a model
directory. ``lab:<artifact-id>`` identifies a retained laboratory model within an explicit project.
Bare ``job/number`` works when a project is given (the web interface always sends one).
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
    """Resolve explicit project identities without overriding the request's scope."""
    from tinystudio.serving.retained import parse_laboratory_id

    parts = model_id.split("/")
    if len(parts) == 2 and parts[1].startswith("lab:"):
        parse_laboratory_id(parts[1])
        if project_slug != parts[0]:
            raise ProjectError("a qualified laboratory model requires its matching request project")
        return project_slug, parts[1]
    if parse_laboratory_id(model_id) is not None:
        if not project_slug:
            raise ProjectError("a laboratory model requires a project")
        return project_slug, model_id
    if (len(parts) == 3 and parts[2].partition("@")[0].isdigit()) or (len(parts) == 2 and parts[1].startswith("ft-")):
        if project_slug is not None and project_slug != parts[0]:
            raise ProjectError("model project does not match the request project")
        return parts[0], "/".join(parts[1:])
    return project_slug, model_id


class _ModelIdentity:
    """Own one cached runner and fence requests across unload, including queued loads."""

    def __init__(self, key: str, slug: str | None, local: str, scoped: bool) -> None:
        self.key = key
        self.slug = slug
        self.local = local
        self.scoped = scoped
        self.laboratory = local.startswith("lab:")
        self.operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._epoch = 0
        self._runner: ModelRunner | None = None

    def capture(self) -> int:
        with self._state_lock:
            return self._epoch

    def runner(self, epoch: int | None = None) -> ModelRunner | None:
        with self._state_lock:
            if epoch is not None and epoch != self._epoch:
                raise ProjectError("model load was cancelled by unload or a changed binding; retry the request")
            return self._runner

    def publish(self, epoch: int, runner: ModelRunner) -> None:
        with self._state_lock:
            if epoch != self._epoch:
                raise ProjectError("model load was cancelled by unload or a changed binding; retry the request")
            self._runner = runner

    def retire(self) -> None:
        with self._state_lock:
            self._epoch += 1
            runner, self._runner = self._runner, None
        # A runner may wait for active generation; never hold dictionary/state locks here.
        if runner is not None:
            runner.unload()


class LocalEngine:
    """Models loaded in this process, serialized and fenced per model identity."""

    def __init__(self) -> None:
        self._identities: dict[str, _ModelIdentity] = {}
        self.lock = threading.Lock()
        self.recent: list[dict[str, Any]] = []

    @property
    def loaded(self) -> dict[str, ModelRunner]:
        """An inspection snapshot; lifecycle mutations go through load/unload."""
        with self.lock:
            identities = list(self._identities.values())
        return {identity.key: runner for identity in identities if (runner := identity.runner()) is not None}

    def _project(self, db: Any, slug: str | None) -> Project | None:
        return projects_svc.get_project(db, slug) if slug else None

    def key(self, project_slug: str | None, model_id: str) -> str:
        slug, local = split_model_id(model_id, project_slug)
        scoped = local.startswith(("ft-", "lab:")) or local.partition("/")[2].partition("@")[0].isdigit()
        return f"{slug}/{local}" if slug and scoped else local

    def _identity(self, model_id: str, project_slug: str | None) -> _ModelIdentity:
        slug, local = split_model_id(model_id, project_slug)
        key = self.key(project_slug, model_id)
        with self.lock:
            if key not in self._identities:
                self._identities[key] = _ModelIdentity(key, slug, local, key != local)
            return self._identities[key]

    def _resolve(self, identity: _ModelIdentity) -> Any:
        with session() as db:
            resolved = models_svc.resolve(db, self._project(db, identity.slug), identity.local)
        if identity.laboratory and resolved.retained_source is None:
            raise ProjectError("laboratory model has no accepted retained source")
        return resolved

    def _validate_binding(self, identity: _ModelIdentity, resolved: Any) -> None:
        try:
            current = self._resolve(identity)
            if current.retained_source != resolved.retained_source:
                raise ProjectError("laboratory model binding changed; load the model again")
        except ProjectError:
            identity.retire()
            raise

    def load(self, model_id: str, project_slug: str | None = None) -> ModelRunner:
        identity = self._identity(model_id, project_slug)
        epoch = identity.capture()  # Capture before waiting: unload also fences queued requests.
        with identity.operation_lock:
            cached = identity.runner(epoch)
            if cached is not None:
                if identity.laboratory:
                    self._validate_binding(identity, cached.resolved)
                return identity.runner(epoch)
            resolved = self._resolve(identity)
            identity.runner(epoch)
            try:
                runner = ModelRunner(resolved)
            except OSError as cause:
                raise ProjectError(f"model {model_id!r} could not be loaded: {cause}") from cause
            try:
                if identity.laboratory:
                    self._validate_binding(identity, resolved)
                identity.publish(epoch, runner)
            except BaseException:
                runner.unload()
                raise
            return runner

    def unload(self, model_id: str, project_slug: str | None = None) -> dict[str, Any]:
        self._identity(model_id, project_slug).retire()
        return {"id": model_id, "status": "down"}

    def loaded_ids(self, project_slug: str | None = None) -> set[str]:
        """Project-local cached ids plus globally shared Hub/directory ids."""
        with self.lock:
            identities = list(self._identities.values())
        out = set()
        for identity in identities:
            if identity.scoped and identity.slug != project_slug:
                continue
            with identity.operation_lock:
                epoch = identity.capture()
                try:
                    runner = identity.runner(epoch)
                    if runner is None:
                        continue
                    if identity.laboratory:
                        self._validate_binding(identity, runner.resolved)
                    identity.runner(epoch)
                except ProjectError:
                    continue
                out.add(identity.local)
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
