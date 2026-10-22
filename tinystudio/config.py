"""Deployment configuration: where the stores are and where artifacts live.

These values describe one deployment and come from the environment (or a ``.env`` file next to the
package or in the working directory). Everything a team member changes day to day lives in the
Settings page instead and is stored in the database (see :mod:`tinystudio.services.settings`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_DATABASE_URL = "postgresql+psycopg://tinystudio:tinystudio@127.0.0.1:5432/tinystudio"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    """One deployment's infrastructure settings."""

    database_url: str
    redis_url: str
    qdrant_url: str
    qdrant_api_key: str | None
    data_dir: Path
    host: str
    port: int
    inference_url: str | None
    inference_port: int
    admin_email: str | None
    admin_password: str | None
    device: str
    dtype: str
    secure_cookies: bool
    session_days: int

    @property
    def inference_in_process(self) -> bool:
        """No separate inference service configured: the API loads models itself (single-machine use)."""
        return not self.inference_url

    def project_dir(self, project_id: str) -> Path:
        return self.data_dir / "projects" / project_id

    def task_log(self, task_id: str) -> Path:
        return self.data_dir / "tasks" / f"{task_id}.log"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


def _load() -> Config:
    data_dir = Path(_env("TINYSTUDIO_DATA_DIR") or Path.home() / ".tinystudio" / "data").expanduser().resolve()
    return Config(
        database_url=_env("TINYSTUDIO_DATABASE_URL", DEFAULT_DATABASE_URL),
        redis_url=_env("TINYSTUDIO_REDIS_URL", DEFAULT_REDIS_URL),
        qdrant_url=_env("TINYSTUDIO_QDRANT_URL", DEFAULT_QDRANT_URL),
        qdrant_api_key=_env("TINYSTUDIO_QDRANT_API_KEY") or None,
        data_dir=data_dir,
        host=_env("TINYSTUDIO_HOST", "127.0.0.1"),
        port=int(_env("TINYSTUDIO_PORT", "8000")),
        inference_url=_env("TINYSTUDIO_INFERENCE_URL").rstrip("/") or None,
        inference_port=int(_env("TINYSTUDIO_INFERENCE_PORT", "8001")),
        admin_email=_env("TINYSTUDIO_ADMIN_EMAIL") or None,
        admin_password=os.environ.get("TINYSTUDIO_ADMIN_PASSWORD") or None,
        device=_env("TINYSTUDIO_DEVICE", "auto") or "auto",
        dtype=_env("TINYSTUDIO_DTYPE", "auto") or "auto",
        secure_cookies=_env("TINYSTUDIO_SECURE_COOKIES", "0").lower() in ("1", "true", "yes"),
        session_days=int(_env("TINYSTUDIO_SESSION_DAYS", "14")),
    )


@lru_cache(maxsize=1)
def config() -> Config:
    """The deployment configuration, read once per process."""
    return _load()


def reload_config() -> Config:
    """Re-read the environment (tests and the CLI's --database-url style overrides)."""
    config.cache_clear()
    return config()


def redacted(url: str) -> str:
    """A connection URL with its password hidden, for logs and the config command."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.password:
        netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url
