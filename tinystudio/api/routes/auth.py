"""Sign in, sign out, first-run setup, the current account."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.auth import AuthError, Principal, authenticate_password, issue_session, SESSION_COOKIE
from tinystudio.api.deps import cookie_kwargs, current_user, get_db, instance_secret, optional_principal, wrap
from tinystudio.db.models import User
from tinystudio.config import config
from tinystudio.services import users as users_svc

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str
    password: str


class SetupBody(BaseModel):
    email: str
    password: str
    name: str = ""


class PasswordBody(BaseModel):
    current: str
    new: str


@router.get("/status")
def status(db: Session = Depends(get_db), principal: Principal | None = Depends(optional_principal)) -> dict[str, Any]:
    has_users = db.scalar(select(User.id).limit(1)) is not None
    return {"setup_required": not has_users, "authenticated": principal is not None, "user": principal.as_dict() if principal else None}


@router.post("/setup")
def setup(body: SetupBody, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Create the first admin account (only while the users table is empty)."""
    if config().cloudflare_access_team_domain or config().cloudflare_access_required:
        raise HTTPException(status_code=403, detail="create Cloudflare accounts through an existing administrator")
    if db.scalar(select(User.id).limit(1)) is not None:
        raise HTTPException(status_code=409, detail="an account already exists; sign in instead")
    user = wrap(lambda: users_svc.create_user(db, body.email, body.password, body.name, "admin"))
    response.set_cookie(SESSION_COOKIE, issue_session(instance_secret(db), user.id), **cookie_kwargs())
    return {"user": users_svc.user_dict(user)}


@router.post("/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        user = authenticate_password(db, body.email, body.password)
    except AuthError as cause:
        raise HTTPException(status_code=401, detail=str(cause)) from cause
    response.set_cookie(SESSION_COOKIE, issue_session(instance_secret(db), user.id), **cookie_kwargs())
    return {"user": users_svc.user_dict(user)}


@router.post("/logout")
def logout(response: Response, request: Request, principal: Principal | None = Depends(optional_principal)) -> dict[str, Any]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True, "logout_url": "/cdn-cgi/access/logout" if principal and principal.via == "cloudflare" else None}


@router.get("/me")
def me(principal: Principal = Depends(current_user)) -> dict[str, Any]:
    return principal.as_dict()


@router.post("/password")
def change_password(body: PasswordBody, db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    try:
        authenticate_password(db, principal.user.email, body.current)
    except AuthError as cause:
        raise HTTPException(status_code=401, detail="the current password is wrong") from cause
    wrap(lambda: users_svc.set_password(db, principal.user, body.new))
    return {"ok": True}
