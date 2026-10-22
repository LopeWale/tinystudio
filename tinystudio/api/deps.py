"""FastAPI dependencies: database sessions, the current user, admin checks, project resolution, error mapping."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from tinystudio.auth import CSRF_HEADER, CSRF_VALUE, SESSION_COOKIE, Principal, ensure_instance, principal_from_bearer, principal_from_cookie
from tinystudio.config import config
from tinystudio.core.data import DatapointError
from tinystudio.core.project import ProjectError
from tinystudio.db import session_factory
from tinystudio.db.models import Project
from tinystudio.services import projects as projects_svc

_secret: dict[str, str] = {}


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def instance_secret(db: Session) -> str:
    if "key" not in _secret:
        _secret["key"] = ensure_instance(db).secret_key
    return _secret["key"]


def forget_secret() -> None:
    _secret.clear()


def optional_principal(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return principal_from_bearer(db, instance_secret(db), header[7:].strip())
    principal = principal_from_cookie(db, instance_secret(db), request.cookies.get(SESSION_COOKIE), config().session_days * 86400)
    if principal is not None and request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER, "").lower() != CSRF_VALUE:
        raise HTTPException(status_code=403, detail=f"state-changing requests from the browser need the header {CSRF_HEADER}: {CSRF_VALUE}")
    return principal


def current_user(principal: Principal | None = Depends(optional_principal)) -> Principal:
    if principal is None:
        raise HTTPException(status_code=401, detail="sign in (session cookie) or send an API key: Authorization: Bearer ts-...")
    return principal


def require_admin(principal: Principal = Depends(current_user)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="this needs an admin account")
    return principal


def project_dep(slug: str, db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> Project:
    try:
        return projects_svc.get_project(db, slug)
    except ProjectError as cause:
        raise HTTPException(status_code=404, detail=str(cause)) from cause


def wrap(action: Callable[[], Any]) -> Any:
    """Expected failures become 400s with their reason; everything else propagates."""
    try:
        return action()
    except (ProjectError, DatapointError, ValueError) as cause:
        raise HTTPException(status_code=400, detail=str(cause)) from cause


def cookie_kwargs() -> dict[str, Any]:
    cfg = config()
    return {"httponly": True, "samesite": "lax", "secure": cfg.secure_cookies, "max_age": cfg.session_days * 86400, "path": "/"}
