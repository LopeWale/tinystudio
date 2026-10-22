"""The standalone inference service: ``tinystudio inference``. Trusts the deployment's internal token."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from tinystudio.auth import ensure_instance
from tinystudio.db import session
from tinystudio.inference.engine import LocalEngine
from tinystudio.inference.routes import build_internal_router, build_v1_router


def create_inference_app(base_models: list[str] | None = None) -> FastAPI:
    app = FastAPI(title="tinystudio inference", version="0.1.0")
    engine = LocalEngine()
    state: dict[str, str] = {}

    def token() -> str:
        if "token" not in state:
            with session() as db:
                state["token"] = ensure_instance(db).internal_token
        return state["token"]

    def auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        presented = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not presented or not secrets.compare_digest(presented, token()):
            raise HTTPException(status_code=401, detail="the inference service only answers the API (internal token)")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "loaded": sorted(engine.loaded)}

    app.include_router(build_v1_router(lambda: engine, auth, base_models))
    app.include_router(build_internal_router(lambda: engine, auth))
    app.state.engine = engine
    return app
