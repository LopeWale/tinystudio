"""Standalone authenticated local gateway; never imports server DB/Redis configuration.

Provision owner-only files (0600): the endpoint binding consumed by CommandGateway
and a token descriptor {endpoint_id, operator_id, token_sha256, permissions:[...]},
where permissions explicitly name state/journal/acquire/heartbeat/commands/stop/
reconcile. The journal directory must be owner-only (0700). No grants/tokens are
created here. Bearer secrets remain with the operator; only SHA-256 is loaded.
Every command body includes endpoint_id; stop/reconcile derive operator identity
from the token descriptor, never from an authored research definition.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .gateway import CommandGateway, GatewayError, _binding, _parameters

PERMISSIONS = {"state", "journal", "acquire", "heartbeat", "commands", "stop", "reconcile"}
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Identity = Annotated[str, Field(strict=True, min_length=1, max_length=128)]


def private_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("gateway configuration must be an existing regular file")
    info = path.stat()
    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
        raise ValueError("gateway configuration must be owned by the operator with mode 0600")
    if info.st_size > 65536:
        raise ValueError("gateway configuration exceeds 64 KiB")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("gateway configuration must be a JSON object")
    return value


class EndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint_id: Identity


class AcquireRequest(EndpointRequest):
    owner: Identity
    ttl: Annotated[Finite, Field(gt=0, le=3600)] = 30


class HeartbeatRequest(AcquireRequest):
    epoch: Annotated[int, Field(strict=True, ge=1)]


class ParameterValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Finite
    unit: Annotated[str, Field(strict=True, max_length=128)]


class CommandRequest(EndpointRequest):
    command_id: Identity
    owner: Identity
    epoch: Annotated[int, Field(strict=True, ge=1)]
    parameters: dict[str, ParameterValue] = Field(min_length=1, max_length=32)
    expires_at: Annotated[Finite, Field(gt=0)]


class StopRequest(EndpointRequest):
    request_id: Identity


def create_gateway_app(binding_file, journal_root, token_file, transport_factory=None):
    binding = _binding(private_json(binding_file))
    token = private_json(token_file)
    if set(token) != {"endpoint_id", "operator_id", "token_sha256", "permissions"}:
        raise ValueError("token descriptor requires explicit endpoint/operator/hash/permissions")
    if token["endpoint_id"] != binding["endpoint_id"] or token["operator_id"] != binding["operator_id"]:
        raise ValueError("token scope must match exactly the operator-provisioned endpoint")
    if not isinstance(token["token_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", token["token_sha256"]):
        raise ValueError("token descriptor requires a SHA-256 secret hash")
    permissions = token["permissions"]
    if not isinstance(permissions, list) or not permissions or any(not isinstance(item, str) or item not in PERMISSIONS for item in permissions):
        raise ValueError("token permissions must be an explicit nonempty allowlist")
    root = Path(journal_root).resolve()
    if not root.is_dir() or stat.S_IMODE(root.stat().st_mode) != 0o700 or root.stat().st_uid != os.geteuid():
        raise ValueError("journal root must be an existing operator-owned mode 0700 directory")
    endpoint_id = binding["endpoint_id"]

    @asynccontextmanager
    async def lifespan(app):
        transport = transport_factory(binding) if transport_factory is not None else None
        gateway = CommandGateway(root / "gateway.sqlite3", binding, transport)
        app.state.gateway = gateway
        try:
            yield
        finally:
            gateway.close()
            app.state.gateway = None

    app = FastAPI(title="TinyStudio local endpoint gateway", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    def guard(permission):
        def authorize(authorization: Annotated[str | None, Header()] = None,
                      endpoint_id_query: Annotated[str | None, Query(alias="endpoint_id", max_length=128)] = None):
            scheme, separator, supplied = (authorization or "").partition(" ")
            if not separator or scheme.lower() != "bearer":
                supplied = ""
            hashed = hashlib.sha256(supplied.encode()).hexdigest() if len(supplied) <= 4096 else ""
            if not supplied or not hmac.compare_digest(hashed, token["token_sha256"]):
                raise HTTPException(401, "invalid endpoint bearer token", headers={"WWW-Authenticate": "Bearer"})
            if permission not in permissions:
                raise HTTPException(403, "endpoint token does not grant this operation")
            if endpoint_id_query is not None and endpoint_id_query != endpoint_id:
                raise HTTPException(403, "token is scoped to a different endpoint")
        return Depends(authorize)

    def scope(body):
        if body.endpoint_id != endpoint_id:
            raise HTTPException(403, "token is scoped to a different endpoint")

    @app.exception_handler(GatewayError)
    async def gateway_error(request, exc):
        try:
            requires_recovery = app.state.gateway.snapshot()["state"] == "recovery_required"
        except GatewayError:
            requires_recovery = True
        return JSONResponse(status_code=409, content={"detail": str(exc), "retry_allowed": False,
                                                     "recovery_requires_operator": requires_recovery})

    @app.exception_handler(TimeoutError)
    @app.exception_handler(ConnectionError)
    async def device_unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": "device reconciliation query unavailable",
                                                     "retry_allowed": False, "recovery_requires_operator": True})

    @app.get("/state", dependencies=[guard("state")])
    def state():
        return app.state.gateway.snapshot()

    @app.get("/journal", dependencies=[guard("journal")])
    def journal(offset: Annotated[int, Query(ge=0)] = 0,
                limit: Annotated[int, Query(ge=1, le=1000)] = 100):
        rows = app.state.gateway.journal()
        return {"endpoint_id": endpoint_id, "items": rows[offset:offset+limit],
                "next_offset": offset + limit if offset + limit < len(rows) else None}

    @app.post("/acquire", dependencies=[guard("acquire")])
    def acquire(body: AcquireRequest):
        scope(body)
        return app.state.gateway.acquire(body.owner, ttl=body.ttl)

    @app.post("/heartbeat", dependencies=[guard("heartbeat")])
    def heartbeat(body: HeartbeatRequest):
        scope(body)
        expires_at = app.state.gateway.heartbeat(body.owner, body.epoch, ttl=body.ttl)
        return {"endpoint_id": endpoint_id, "epoch": body.epoch, "expires_at": expires_at}

    @app.post("/commands", dependencies=[guard("commands")])
    def command(body: CommandRequest):
        scope(body)
        parameters = {name: value.model_dump() for name, value in body.parameters.items()}
        try:
            _parameters(parameters, binding["limits"])
        except GatewayError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Reject expiry before admitting a journal entry. Gateway repeats its own
        # trusted clock/lease checks at the actual transport-write boundary.
        import time
        if body.expires_at <= time.time():
            raise HTTPException(410, "command has expired")
        return app.state.gateway.dispatch(body.command_id, owner=body.owner, epoch=body.epoch,
                                          parameters=parameters, expires_at=body.expires_at)

    @app.post("/stop", dependencies=[guard("stop")])
    def stop(body: StopRequest):
        scope(body)
        return app.state.gateway.local_stop(token["operator_id"], request_id=body.request_id)

    @app.post("/reconcile", dependencies=[guard("reconcile")])
    def reconcile(body: EndpointRequest):
        scope(body)
        return app.state.gateway.reconcile(token["operator_id"])

    return app
