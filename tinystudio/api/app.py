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
from tinystudio.api.routes import accounts, artifacts, assistant_attachments, assistant_conversations, auth, data, interp, projects, serving, system, training
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


def create_app(run_migrations: bool = True, base_models: list[str] | None = None, auto_up: bool = True,
               ui: bool = True, ui_directory: Path | None = None, laboratory_dispatch: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        prepare_database(run_migrations)
        config().data_dir.mkdir(parents=True, exist_ok=True)
        from tinystudio.lab.dispatch import Dispatcher
        dispatcher = Dispatcher().start() if laboratory_dispatch else None
        app.state.laboratory_dispatch = dispatcher
        try:
            yield
        finally:
            if dispatcher:
                await dispatcher.close()

    app = FastAPI(title="tinystudio", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CloudflareAccessMiddleware)

    @app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
    def liveness() -> dict[str, bool]:
        return {"ok": True}
    for module in (system, auth, accounts, assistant_attachments, assistant_conversations, projects, data, training, serving, interp, artifacts):
        app.include_router(module.router)
    from tinystudio.lab.api import install as install_laboratory

    install_laboratory(app)
    app.include_router(build_v1_router(engine, current_user, base_models, auto_up))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    if ui:
        # Production captures verified bytes in a content-addressed cache during
        # app construction. Later builds cannot change this process's release.
        # Explicit ui_directory overrides retain the native/test serving path.
        if ui_directory is not None:
            frontend = ui_directory
            ui_files = StaticFiles(directory=str(frontend), check_dir=False)
        else:
            from tinystudio.frontend_release import FrontendReleaseError, FrontendReleaseFiles, retained_frontends, snapshot_frontend

            try:
                release = snapshot_frontend(UI_DIR / "dist", config().data_dir)
            except (FrontendReleaseError, OSError) as error:
                log.error("Frontend release unavailable: %s", error)
                release = None
            frontend = release.directory if release else None
            retained = retained_frontends(config().data_dir / "frontend-releases")
            ui_files = FrontendReleaseFiles(release, retained)

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def index() -> HTMLResponse:
            source = frontend / "index.html" if frontend else None
            if source is None or not source.is_file():
                return HTMLResponse(
                    '<!doctype html><title>TinyStudio frontend build required</title>'
                    '<h1>Frontend build required</h1><p>Run <code>npm ci --prefix frontend</code> '
                    'and <code>npm run build --prefix frontend</code>, then restart TinyStudio.</p>',
                    status_code=503,
                )
            return HTMLResponse(source.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})

        @app.get("/favicon.ico", include_in_schema=False, response_model=None)
        def favicon() -> FileResponse | HTMLResponse:
            source = frontend / "favicon.svg" if frontend else None
            if source is None or not source.is_file():
                return HTMLResponse("", status_code=404)
            return FileResponse(source, media_type="image/svg+xml")

        app.mount("/ui", ui_files, name="ui")
    return app
