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
from copy import deepcopy
import hashlib
import hmac
import inspect
import os
from pathlib import Path
import re
import stat
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .gateway import CommandGateway, GatewayError, GatewayExpired, _binding, _parameters,transport_for
from .gateway_configuration import (GatewayApiPaths, GatewayStoragePaths, GatewayFilePolicy,
    GatewayRequestPolicy,GatewayRequestTooLarge,decode_gateway_json)

PERMISSIONS = {"state", "journal", "acquire", "heartbeat", "commands", "stop", "reconcile"}
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Identity = Annotated[str, Field(strict=True, min_length=1)]
PATHS=GatewayApiPaths()


class GatewayJsonEnvelope:
    """Buffer bounded JSON intent before any effect, preserving ASGI delivery.

    Native device bytes remain encoded strings inside this envelope. They are
    never parsed as JSON, so malformed device-protocol experiments remain valid.
    """
    def __init__(self,app,*,policy):
        self.app,self.policy=app,policy

    async def __call__(self,scope,receive,send):
        path=scope.get('path','');root=scope.get('root_path','')
        if root and (path==root or path.startswith(root+'/')):path=path[len(root):] or '/'
        if scope['type']!='http' or scope.get('method')!='POST' or path not in PATHS.mutation_routes:
            return await self.app(scope,receive,send)
        messages=[];body=bytearray()
        try:
            while True:
                event=await receive()
                if event['type']=='http.disconnect':return
                if event['type']!='http.request':raise ValueError('invalid gateway request stream')
                chunk=event.get('body',b'')
                self.policy.check_size(len(body)+len(chunk))
                body.extend(chunk);messages.append(event)
                if not event.get('more_body',False):break
            decode_gateway_json(body)
        except (ValueError,RecursionError) as error:
            response=JSONResponse(status_code=413 if isinstance(error,GatewayRequestTooLarge) else 422,
                content={'detail':str(error)})
            return await response(scope,receive,send)
        iterator=iter(messages)
        async def replay():
            try:return next(iterator)
            except StopIteration:return await receive()
        return await self.app(scope,replay,send)


def private_json(path,*,policy=None):
    return (policy or GatewayFilePolicy.from_environment()).read(path)


class EndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint_id: Identity


class AcquireRequest(EndpointRequest):
    owner: Identity
    ttl: Annotated[Finite, Field(gt=0)] = 30


class HeartbeatRequest(AcquireRequest):
    epoch: Annotated[int, Field(strict=True, ge=1)]


class ParameterValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Finite
    unit: str = Field(strict=True)


class CommandRequest(EndpointRequest):
    command_id: Identity
    owner: Identity
    epoch: Annotated[int, Field(strict=True, ge=1)]
    parameters: dict[str, ParameterValue] | None = Field(default=None,min_length=1)
    payload: dict | None = None
    expires_at: Annotated[Finite, Field(gt=0)]

    @model_validator(mode='after')
    def selected_command_form(self):
        if (self.parameters is None)==(self.payload is None):
            raise ValueError('provide exactly one of typed parameters or native payload')
        return self


class StopRequest(EndpointRequest):
    request_id: Identity


class ReconcileRequest(EndpointRequest):
    evidence: dict | None = None


def create_gateway_app(binding_file, journal_root, token_file, transport_factory=None,*,runtime_secrets_file=None,file_policy=None,request_policy=None):
    policy=file_policy or GatewayFilePolicy.from_environment()
    http_policy=request_policy or GatewayRequestPolicy.from_environment()
    binding = _binding(private_json(binding_file,policy=policy))
    token = private_json(token_file,policy=policy)
    runtime_secrets=private_json(runtime_secrets_file,policy=policy) if runtime_secrets_file is not None else None
    if runtime_secrets is not None and transport_factory is not None:
        try:inspect.signature(transport_factory).bind(binding,runtime_secrets=runtime_secrets)
        except (TypeError,ValueError) as error:
            raise ValueError('a transport_factory used with a credential file must explicitly accept runtime_secrets') from error
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
        if transport_factory is None:
            transport=transport_for(binding,runtime_secrets=runtime_secrets)
        else:
            selected={'runtime_secrets':deepcopy(runtime_secrets)} if runtime_secrets is not None else {}
            transport=transport_factory(deepcopy(binding),**selected)
        gateway = CommandGateway(GatewayStoragePaths.in_directory(root).journal, binding, transport)
        app.state.gateway = gateway
        try:
            yield
        finally:
            gateway.close()
            app.state.gateway = None

    app = FastAPI(title="TinyStudio local endpoint gateway", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GatewayJsonEnvelope,policy=http_policy)

    def described_profile(profile):
        return {**profile,'gateway_http':{'max_body_bytes':http_policy.max_bytes,'json_in_memory':True,
                    'envelope_validation':'unique_keys_and_finite_json'},
                'configuration_budget':{'max_file_bytes':policy.max_bytes}}

    def guard(permission):
        def authorize(authorization: Annotated[str | None, Header()] = None,
                      endpoint_id_query: Annotated[str | None, Query(alias="endpoint_id")] = None):
            scheme, separator, supplied = (authorization or "").partition(" ")
            if not separator or scheme.lower() != "bearer":
                supplied = ""
            hashed = hashlib.sha256(supplied.encode()).hexdigest()
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
        result={"detail": str(exc), "retry_allowed": False,"recovery_requires_operator": requires_recovery}
        if getattr(exc,'delivery_receipt',None) is not None:result['delivery_receipt']=exc.delivery_receipt
        return JSONResponse(status_code=410 if isinstance(exc,GatewayExpired) else 409,content=result)

    @app.exception_handler(TimeoutError)
    @app.exception_handler(ConnectionError)
    async def device_unavailable(request, exc):
        return JSONResponse(status_code=503, content={"detail": "device reconciliation query unavailable",
                                                     "retry_allowed": False, "recovery_requires_operator": True})

    @app.get(PATHS.route('state'), dependencies=[guard("state")])
    def state():
        result=app.state.gateway.snapshot()
        result['command_profile']=described_profile(result['command_profile'])
        return result

    @app.get(PATHS.command_profile,dependencies=[guard('state')])
    def command_profile():
        return described_profile(app.state.gateway.snapshot()['command_profile'])

    @app.get(PATHS.route('journal'), dependencies=[guard("journal")])
    def journal(offset: Annotated[int, Query(ge=0)] = 0,
                limit: Annotated[int, Query(ge=1, le=1000)] = 100):
        rows = app.state.gateway.journal()
        return {"endpoint_id": endpoint_id, "items": rows[offset:offset+limit],
                "next_offset": offset + limit if offset + limit < len(rows) else None}

    @app.post(PATHS.route('acquire'), dependencies=[guard("acquire")])
    def acquire(body: AcquireRequest):
        scope(body)
        return app.state.gateway.acquire(body.owner, ttl=body.ttl)

    @app.post(PATHS.route('heartbeat'), dependencies=[guard("heartbeat")])
    def heartbeat(body: HeartbeatRequest):
        scope(body)
        expires_at = app.state.gateway.heartbeat(body.owner, body.epoch, ttl=body.ttl)
        return {"endpoint_id": endpoint_id, "epoch": body.epoch, "expires_at": expires_at}

    @app.post(PATHS.route('commands'), dependencies=[guard("commands")])
    def command(body: CommandRequest):
        scope(body)
        parameters = {name: value.model_dump() for name, value in body.parameters.items()} if body.parameters is not None else None
        try:
            if 'command_profile' in binding:
                from .command_profiles import NativeCommandProfile
                if parameters is not None:raise GatewayError('selected native profile requires payload')
                NativeCommandProfile.from_binding(binding).validate_payload(body.payload)
            else:
                if body.payload is not None:raise GatewayError('selected legacy profile requires typed parameters')
                _parameters(parameters, binding["limits"])
        except (GatewayError,ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        # Core returns a matching historical receipt before checking authority.
        # New intents still require current ownership and unexpired dispatch.
        return app.state.gateway.dispatch(body.command_id, owner=body.owner, epoch=body.epoch,
                                          parameters=parameters,payload=body.payload, expires_at=body.expires_at)

    @app.post(PATHS.route('stop'), dependencies=[guard("stop")])
    def stop(body: StopRequest):
        scope(body)
        return app.state.gateway.local_stop(token["operator_id"], request_id=body.request_id)

    @app.post(PATHS.route('reconcile'), dependencies=[guard("reconcile")])
    def reconcile(body: ReconcileRequest):
        scope(body)
        return app.state.gateway.reconcile(token["operator_id"],evidence=body.evidence)

    return app
