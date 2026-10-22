"""Accounts, sessions, API keys, and the FastAPI dependencies that enforce them.

- Passwords: argon2id (argon2-cffi defaults), rehashed when parameters change.
- Sessions: a signed, timestamped cookie (itsdangerous) carrying the user id; HttpOnly, SameSite=Lax.
- API keys: ``ts-`` + 32 random bytes; only the SHA-256 digest is stored. Keys work on ``/api`` and ``/v1``.
- Service tokens: short-lived signed tokens the API mints for its own helpers (the in-app assistant calls the
  API over HTTP as the user who asked).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.db.models import ApiKey, Instance, User

SESSION_COOKIE = "tinystudio_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "tinystudio"
KEY_PREFIX = "ts-"
SERVICE_TTL_SECONDS = 3600

_hasher = PasswordHasher()


class AuthError(RuntimeError):
    """Authentication or authorization failed; ``status`` is the HTTP code to answer with."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- passwords and keys
def hash_password(password: str) -> str:
    if len(password) < 8:
        raise AuthError("passwords need at least 8 characters", 400)
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_api_key() -> tuple[str, str, str]:
    """(plaintext, prefix, sha256). The plaintext is shown once and never stored."""
    plaintext = KEY_PREFIX + secrets.token_urlsafe(32)
    return plaintext, plaintext[:8], hashlib.sha256(plaintext.encode()).hexdigest()


def key_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- instance secrets
def ensure_instance(db: Session) -> Instance:
    """The single ``instance`` row holds the cookie-signing secret and the internal service token."""
    row = db.get(Instance, 1)
    if row is None:
        row = Instance(id=1, secret_key=secrets.token_urlsafe(48), internal_token=secrets.token_urlsafe(48))
        db.add(row)
        db.flush()
    return row


def serializer(secret: str, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=salt)


def issue_session(secret: str, user_id: str) -> str:
    return serializer(secret, "session").dumps({"u": user_id})


def read_session(secret: str, token: str, max_age: int) -> str | None:
    try:
        return str(serializer(secret, "session").loads(token, max_age=max_age)["u"])
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        return None


def issue_service_token(secret: str, user_id: str) -> str:
    return "tss-" + serializer(secret, "service").dumps({"u": user_id})


def read_service_token(secret: str, token: str) -> str | None:
    if not token.startswith("tss-"):
        return None
    try:
        return str(serializer(secret, "service").loads(token[4:], max_age=SERVICE_TTL_SECONDS)["u"])
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        return None


# ---------------------------------------------------------------- principals
@dataclass
class Principal:
    user: User
    via: str  # session | api_key | service
    api_key: ApiKey | None = None

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.user.id, "email": self.user.email, "name": self.user.name, "role": self.user.role, "via": self.via}


def authenticate_password(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or user.disabled or not verify_password(user.password_hash, password):
        raise AuthError("email or password is wrong")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login = datetime.now(timezone.utc)
    return user


def principal_from_bearer(db: Session, secret: str, token: str) -> Principal | None:
    """An API key or a service token presented as ``Authorization: Bearer``."""
    user_id = read_service_token(secret, token)
    if user_id:
        user = db.get(User, user_id)
        return Principal(user, "service") if user and not user.disabled else None
    if token.startswith(KEY_PREFIX):
        key = db.scalar(select(ApiKey).where(ApiKey.sha256 == key_digest(token)))
        if key is None:
            return None
        user = db.get(User, key.user_id)
        if user is None or user.disabled:
            return None
        key.last_used = datetime.now(timezone.utc)
        return Principal(user, "api_key", key)
    return None


def principal_from_cookie(db: Session, secret: str, cookie: str | None, max_age: int) -> Principal | None:
    if not cookie:
        return None
    user_id = read_session(secret, cookie, max_age)
    if not user_id:
        return None
    user = db.get(User, user_id)
    return Principal(user, "session") if user and not user.disabled else None


def bootstrap_admin(db: Session, email: str | None, password: str | None) -> User | None:
    """Create the first admin from the environment when the users table is empty (container first start)."""
    if db.scalar(select(User.id).limit(1)) is not None:
        return None
    if not email or not password:
        return None
    user = User(email=email.strip().lower(), name=email.split("@")[0], password_hash=hash_password(password), role="admin")
    db.add(user)
    db.flush()
    return user
