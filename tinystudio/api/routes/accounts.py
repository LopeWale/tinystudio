"""Users (admin) and API keys (per user)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from tinystudio.api.deps import current_user, get_db, require_admin, wrap
from tinystudio.auth import Principal
from tinystudio.services import users as users_svc

router = APIRouter(prefix="/api", tags=["accounts"])


class NewUserBody(BaseModel):
    email: str
    password: str
    name: str = ""
    role: str = "member"


class UserPatchBody(BaseModel):
    name: str | None = None
    role: str | None = None
    disabled: bool | None = None


class SetPasswordBody(BaseModel):
    password: str


class KeyBody(BaseModel):
    name: str


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: Principal = Depends(require_admin)) -> list[dict[str, Any]]:
    return users_svc.list_users(db)


@router.post("/users")
def create_user(body: NewUserBody, db: Session = Depends(get_db), _: Principal = Depends(require_admin)) -> dict[str, Any]:
    return users_svc.user_dict(wrap(lambda: users_svc.create_user(db, body.email, body.password, body.name, body.role)))


@router.patch("/users/{user_id}")
def patch_user(user_id: str, body: UserPatchBody, db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    user = wrap(lambda: users_svc.get_user(db, user_id))
    if not principal.is_admin:
        if user.id != principal.user.id or body.role is not None or body.disabled is not None:
            raise HTTPException(status_code=403, detail="only admins change roles or other accounts")
    return users_svc.user_dict(wrap(lambda: users_svc.update_user(db, user, body.name, body.role, body.disabled, principal.user)))


@router.post("/users/{user_id}/password")
def set_password(user_id: str, body: SetPasswordBody, db: Session = Depends(get_db), _: Principal = Depends(require_admin)) -> dict[str, Any]:
    user = wrap(lambda: users_svc.get_user(db, user_id))
    wrap(lambda: users_svc.set_password(db, user, body.password))
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: str, db: Session = Depends(get_db), principal: Principal = Depends(require_admin)) -> dict[str, Any]:
    user = wrap(lambda: users_svc.get_user(db, user_id))
    wrap(lambda: users_svc.delete_user(db, user, principal.user))
    return {"deleted": user_id}


@router.get("/keys")
def list_keys(all: bool = False, db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> list[dict[str, Any]]:
    return users_svc.list_keys(db, None if (all and principal.is_admin) else principal.user)


@router.post("/keys")
def create_key(body: KeyBody, db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    record, plaintext = wrap(lambda: users_svc.create_key(db, principal.user, body.name))
    return {**record, "key": plaintext}


@router.delete("/keys/{key_id}")
def revoke_key(key_id: str, db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    wrap(lambda: users_svc.revoke_key(db, key_id, principal.user))
    return {"revoked": key_id}
