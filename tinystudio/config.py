"""Deployment configuration: where the stores are and where artifacts live.

These values describe one deployment and come from the environment (or a ``.env`` file next to the
package or in the working directory). Everything a team member changes day to day lives in the
Settings page instead and is stored in the database (see :mod:`tinystudio.services.settings`).
"""

from __future__ import annotations

import os
import hashlib
import tempfile
import shutil
import sys
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

DEFAULT_DATABASE_URL = "postgresql+psycopg://tinystudio:tinystudio@127.0.0.1:5432/tinystudio"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"
DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class NativeProcessSettings:
    """Host-side lifecycle timing, separate from an experiment's wall-time budget."""

    poll_seconds: float = .02
    startup_seconds: float = 30

    def __post_init__(self):
        if any(not math.isfinite(value) or value <= 0 for value in (self.poll_seconds, self.startup_seconds)):
            raise ValueError("native process timing values must be finite and positive")

    @classmethod
    def from_environment(cls) -> NativeProcessSettings:
        defaults = cls()
        return cls(float(_env("TINYSTUDIO_PROCESS_POLL_SECONDS", str(defaults.poll_seconds))),
                   float(_env("TINYSTUDIO_PROCESS_STARTUP_SECONDS", str(defaults.startup_seconds))))


@dataclass(frozen=True)
class RuntimePaths:
    """The existing host-local layout, rooted in a deployment's data/cache directory."""

    root: Path

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def frontend_releases(self) -> Path:
        return self.root / "frontend-releases"

    @property
    def uploads(self) -> Path:
        return self.root / "uploads"

    def project(self, project_id: str) -> Path:
        return self.root / "projects" / project_id

    def task_log(self, task_id: str) -> Path:
        return self.root / "tasks" / f"{task_id}.log"

    def snapshot(self, project_id: str, snapshot_id: str) -> Path:
        return self.project(project_id) / "snapshots" / snapshot_id

    def interpretability(self, project_id: str) -> Path:
        return self.project(project_id) / "interp"

    def interpretability_locks(self, project_id: str) -> Path:
        return self.interpretability(project_id) / ".locks"


@dataclass(frozen=True)
class RuntimeTools:
    """Executable selection; explicit overrides win over discovery on the selected PATH."""

    python: str = field(default_factory=lambda: sys.executable)
    container_python: str = field(default="python", kw_only=True)
    cloudflared: str = "cloudflared"
    docker: str = "docker"
    kubectl: str = "kubectl"
    redis_server: str = "redis-server"
    path: str = field(default_factory=lambda: os.environ.get("PATH", os.defpath))
    names: ClassVar[tuple[str, ...]] = ("python", "cloudflared", "docker", "kubectl", "redis_server")

    @classmethod
    def from_environment(cls) -> RuntimeTools:
        defaults = cls()
        return cls(**{name: _env(f"TINYSTUDIO_{name.upper()}") or getattr(defaults, name)
                      for name in cls.names},
                   container_python=_env("TINYSTUDIO_CONTAINER_PYTHON") or defaults.container_python,
                   path=defaults.path)

    def command(self, name: str) -> str:
        """Freeze a command in a plan without requiring the backend on the authoring host."""
        if name not in self.names:
            raise ValueError(f"unknown runtime executable: {name}")
        command = os.path.expanduser(getattr(self, name))
        if os.path.dirname(command):
            command = str(Path(command).absolute())
        return command

    def resolve(self, name: str) -> str:
        selected = shutil.which(self.command(name), path=self.path)
        if not selected:
            raise FileNotFoundError(f"Set TINYSTUDIO_{name.upper()} to an executable or make {name} available on PATH")
        return selected


@dataclass(frozen=True)
class LaunchdConfig:
    """Paths and commands for the macOS login-service adapter."""

    root: Path
    env_file: Path
    agents_directory: Path
    logs_directory: Path
    tools: RuntimeTools
    label_prefix: str = "dev.joatlabs.tinystudio"

    @classmethod
    def from_environment(cls, root: Path) -> LaunchdConfig:
        home = Path.home()
        return cls(root=root.absolute(),
                   env_file=Path(_env("TINYSTUDIO_ENV_FILE") or root / ".env").expanduser().absolute(),
                   agents_directory=Path(_env("TINYSTUDIO_LAUNCHD_DIR") or home / "Library" / "LaunchAgents").expanduser().absolute(),
                   logs_directory=Path(_env("TINYSTUDIO_SERVICE_LOG_DIR") or home / "Library" / "Logs" / "TinyStudio").expanduser().absolute(),
                   tools=RuntimeTools.from_environment())


@dataclass(frozen=True)
class Preferences:
    """Defaults editable through saved team settings; read afresh after each apply/save."""

    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_model: str = ""
    llm_api_key: str | None = field(default=None, repr=False)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    anthropic_model: str = "claude-opus-5-5"
    anthropic_workspace_id: str = ""
    assistant_effort: str = "medium"
    openai_model: str = ""
    anthropic_key_set: bool = False
    openai_key_set: bool = False
    hf_token_set: bool = False
    mps_fallback: str = "1"

    @classmethod
    def from_environment(cls) -> Preferences:
        defaults = cls()
        names = ("llm_base_url", "llm_model", "llm_api_key", "embedding_model", "anthropic_model",
                 "anthropic_workspace_id", "assistant_effort", "openai_model")
        values = {name: os.environ.get(f"TINYSTUDIO_{name.upper()}", getattr(defaults, name)) for name in names}
        values["llm_base_url"] = values["llm_base_url"].rstrip("/")
        values["embedding_model"] = values["embedding_model"] or defaults.embedding_model
        return cls(**values,
                   anthropic_key_set=bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
                   openai_key_set=bool(os.environ.get("OPENAI_API_KEY")),
                   hf_token_set=bool(os.environ.get("HF_TOKEN")),
                   mps_fallback=os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK", defaults.mps_fallback))


@dataclass(frozen=True)
class ClientConfig:
    """CLI/MCP connection settings, independent of origin-bound Access credentials."""

    url: str
    project: str | None = None
    api_key: str | None = field(default=None, repr=False)

    @classmethod
    def from_environment(cls, *, url: str | None = None) -> ClientConfig:
        return cls(url=(url or _env("TINYSTUDIO_URL")).rstrip("/") or config().local_url,
                   project=os.environ.get("TINYSTUDIO_PROJECT") or None,
                   api_key=os.environ.get("TINYSTUDIO_API_KEY") or None)


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
    inference_host: str
    inference_port: int
    admin_email: str | None
    admin_password: str | None
    device: str
    dtype: str
    secure_cookies: bool
    session_days: int
    storage_backend: str = "local"
    r2_endpoint_url: str = ""
    r2_bucket: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_region: str = "auto"
    cloudflare_access_team_domain: str | None = None
    cloudflare_access_audience: str | None = None
    cloudflare_access_required: bool = False
    public_url: str | None = None
    sync_row_limit: int = 100_000  # dataset-wide operations above this many rows run as worker tasks, not inside the request
    self_url: str | None = None

    @property
    def paths(self) -> RuntimePaths:
        return RuntimePaths(self.data_dir)

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def cache_dir(self) -> Path:
        """Disposable runtime files; remote artifacts are authoritative in R2 mode."""
        return self.data_dir

    @property
    def artifact_location(self) -> str:
        return f"r2://{self.r2_bucket}" if self.storage_backend == "r2" else str(self.paths.artifacts)

    @property
    def inference_in_process(self) -> bool:
        """No separate inference service configured: the API loads models itself (single-machine use)."""
        return not self.inference_url

    def project_dir(self, project_id: str) -> Path:
        return self.paths.project(project_id)

    def task_log(self, task_id: str) -> Path:
        return self.paths.task_log(task_id)

    @property
    def uploads_dir(self) -> Path:
        return self.paths.uploads


def _load() -> Config:
    backend = _env("TINYSTUDIO_STORAGE_BACKEND", "local")
    if backend not in {"local", "r2"}:
        raise ValueError("TINYSTUDIO_STORAGE_BACKEND must be local or r2")
    if backend == "r2":
        namespace = hashlib.sha256(f"{_env('R2_ENDPOINT_URL')}/{_env('R2_BUCKET_NAME')}".encode()).hexdigest()[:20]
        # No durable state is rooted in a user's home directory in R2 mode.
        # This stable, host-local workspace can be discarded and reconstructed.
        user_namespace = os.getuid() if hasattr(os, "getuid") else "user"
        scratch = Path(tempfile.gettempdir()) / f"tinystudio-{user_namespace}" / namespace
        data_dir = Path(_env("TINYSTUDIO_CACHE_DIR") or scratch).expanduser().resolve()
    else:
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
        inference_host=_env("TINYSTUDIO_INFERENCE_HOST", "0.0.0.0"),
        inference_port=int(_env("TINYSTUDIO_INFERENCE_PORT", "8001")),
        admin_email=_env("TINYSTUDIO_ADMIN_EMAIL") or None,
        admin_password=os.environ.get("TINYSTUDIO_ADMIN_PASSWORD") or None,
        device=_env("TINYSTUDIO_DEVICE", "auto") or "auto",
        dtype=_env("TINYSTUDIO_DTYPE", "auto") or "auto",
        secure_cookies=_env("TINYSTUDIO_SECURE_COOKIES", "0").lower() in ("1", "true", "yes"),
        session_days=int(_env("TINYSTUDIO_SESSION_DAYS", "14")),
        storage_backend=backend,
        r2_endpoint_url=_env("R2_ENDPOINT_URL"),
        r2_bucket=_env("R2_BUCKET_NAME"),
        r2_access_key_id=_env("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=_env("R2_SECRET_ACCESS_KEY"),
        r2_region=_env("R2_REGION", "auto"),
        cloudflare_access_team_domain=_env("CF_ACCESS_TEAM_DOMAIN") or None,
        cloudflare_access_audience=_env("CF_ACCESS_AUD") or None,
        cloudflare_access_required=_env("TINYSTUDIO_CF_ACCESS_REQUIRED", "0").lower() in ("1", "true", "yes"),
        public_url=_env("TINYSTUDIO_PUBLIC_URL").rstrip("/") or None,
        self_url=_env("TINYSTUDIO_SELF_URL").rstrip("/") or None,
        sync_row_limit=int(_env("TINYSTUDIO_SYNC_ROW_LIMIT", "100000")),
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
