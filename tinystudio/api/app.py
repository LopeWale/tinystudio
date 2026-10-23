"""The API process: migrations at start, authentication, the web interface, every route, and ``/v1``."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tinystudio.api.deps import current_user, forget_secret
from tinystudio.api.routes import accounts, artifacts, assistant_conversations, auth, data, interp, projects, serving, system, training
from tinystudio.auth import bootstrap_admin, ensure_instance
from tinystudio.auth.cloudflare import CloudflareAccessMiddleware
from tinystudio.config import config
from tinystudio.db import session
from tinystudio.inference.engine import engine
from tinystudio.inference.routes import build_v1_router

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
log = logging.getLogger("tinystudio")


def prepare_database(run_migrations: bool = True) -> None:
    """Apply migrations, make sure the instance secrets exist, create the bootstrap admin, load settings."""
    from tinystudio import worker
    from tinystudio.services import settings as settings_svc

    if run_migrations:
        from tinystudio.db.migrate import upgrade

        upgrade()
    cfg = config()
    with session() as db:
        ensure_instance(db)
        created = bootstrap_admin(db, cfg.admin_email, cfg.admin_password)
        if created is not None:
            log.warning("created the first admin account %s from TINYSTUDIO_ADMIN_EMAIL", created.email)
        settings_svc.apply(db)
        recovered = worker.recover(db)
        if recovered:
            log.warning("marked %d orphaned tasks as failed", recovered)
    forget_secret()


def create_app(run_migrations: bool = True, base_models: list[str] | None = None, auto_up: bool = True, ui: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        prepare_database(run_migrations)
        config().data_dir.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="tinystudio", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CloudflareAccessMiddleware)

    @app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
    def liveness() -> dict[str, bool]:
        return {"ok": True}
    for module in (system, auth, accounts, assistant_conversations, projects, data, training, serving, interp, artifacts):
        app.include_router(module.router)
    app.include_router(build_v1_router(engine, current_user, base_models, auto_up))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    if ui:
        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def index() -> str:
            return (UI_DIR / "index.html").read_text(encoding="utf-8")

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(UI_DIR / "favicon.svg", media_type="image/svg+xml")

        app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")
    return app
