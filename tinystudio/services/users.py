"""Accounts and API keys."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.auth import AuthError, hash_password, new_api_key
from tinystudio.core.project import ProjectError, iso
from tinystudio.db.models import ApiKey, User

ROLES = ("admin", "member")


def user_dict(user: User) -> dict[str, Any]:
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role, "disabled": user.disabled, "created": iso(user.created), "last_login": iso(user.last_login)}


def list_users(db: Session) -> list[dict[str, Any]]:
    return [user_dict(u) for u in db.scalars(select(User).order_by(User.created))]


def get_user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ProjectError("user not found")
    return user


def create_user(db: Session, email: str, password: str, name: str = "", role: str = "member") -> User:
    email = email.strip().lower()
    if "@" not in email:
        raise ProjectError("email must contain @")
    if role not in ROLES:
        raise ProjectError(f"role must be one of {ROLES}")
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise ProjectError(f"a user with email {email} already exists")
    try:
        password_hash = hash_password(password)
    except AuthError as cause:
        raise ProjectError(str(cause)) from cause
    user = User(email=email, name=name.strip() or email.split("@")[0], password_hash=password_hash, role=role)
    db.add(user)
    db.flush()
    return user


def set_password(db: Session, user: User, password: str) -> None:
    try:
        user.password_hash = hash_password(password)
    except AuthError as cause:
        raise ProjectError(str(cause)) from cause


def update_user(db: Session, user: User, name: str | None = None, role: str | None = None, disabled: bool | None = None, actor: User | None = None) -> User:
    if role is not None:
        if role not in ROLES:
            raise ProjectError(f"role must be one of {ROLES}")
        if actor is not None and actor.id == user.id and role != "admin" and user.role == "admin":
            raise ProjectError("you cannot remove your own admin role")
        user.role = role
    if name is not None:
        user.name = name.strip()
    if disabled is not None:
        if actor is not None and actor.id == user.id and disabled:
            raise ProjectError("you cannot disable your own account")
        user.disabled = disabled
    return user


def delete_user(db: Session, user: User, actor: User | None = None) -> None:
    if actor is not None and actor.id == user.id:
        raise ProjectError("you cannot delete your own account")
    db.delete(user)
    db.flush()


# ---------------------------------------------------------------- API keys
def key_dict(key: ApiKey) -> dict[str, Any]:
    return {"id": key.id, "name": key.name, "prefix": key.prefix, "created": iso(key.created), "last_used": iso(key.last_used), "user_id": key.user_id}


def list_keys(db: Session, user: User | None = None) -> list[dict[str, Any]]:
    query = select(ApiKey).order_by(ApiKey.created)
    if user is not None:
        query = query.where(ApiKey.user_id == user.id)
    return [key_dict(k) for k in db.scalars(query)]


def create_key(db: Session, user: User, name: str) -> tuple[dict[str, Any], str]:
    """Return (record, plaintext). The plaintext is never stored."""
    if not name.strip():
        raise ProjectError("the key needs a name")
    plaintext, prefix, digest = new_api_key()
    key = ApiKey(user_id=user.id, name=name.strip(), prefix=prefix, sha256=digest)
    db.add(key)
    db.flush()
    return key_dict(key), plaintext


def revoke_key(db: Session, key_id: str, user: User | None = None) -> None:
    key = db.get(ApiKey, key_id)
    if key is None or (user is not None and key.user_id != user.id and user.role != "admin"):
        raise ProjectError(f"key {key_id!r} not found")
    db.delete(key)
    db.flush()
