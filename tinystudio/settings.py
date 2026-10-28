"""``.env`` loading and the effective-configuration view.

``load_dotenv`` runs when the package is imported. It reads ``TINYSTUDIO_ENV_FILE`` if set, else ``.env`` in the
current directory and in the repository root, and never overrides a variable that is already set. Deployment
values (stores, data directory, ports) are read by :mod:`tinystudio.config`; team settings (credentials, helper LLM,
defaults) live in the database and are edited on the Settings page (:mod:`tinystudio.services.settings`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SECRET_KEYS = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET", "TUNNEL_TOKEN", "TINYSTUDIO_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "TINYSTUDIO_LLM_API_KEY", "HF_TOKEN", "TINYSTUDIO_ADMIN_PASSWORD", "TINYSTUDIO_QDRANT_API_KEY")


def load_dotenv(paths: list[Path] | None = None) -> list[Path]:
    """Load KEY=VALUE lines from the first .env files found; returns the files that were read."""
    if os.environ.get("TINYSTUDIO_SKIP_DOTENV") == "1":
        return []
    candidates = paths or []
    if not candidates:
        explicit = os.environ.get("TINYSTUDIO_ENV_FILE")
        if explicit:
            candidates.append(Path(explicit).expanduser())
        candidates.append(Path.cwd() / ".env")
        candidates.append(Path(__file__).resolve().parents[1] / ".env")
    loaded: list[Path] = []
    for path in candidates:
        if not path.is_file() or any(path.resolve() == seen.resolve() for seen in loaded):
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().removeprefix("export ").strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            else:
                value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()  # inline comments
            if key and key not in os.environ:
                os.environ[key] = value
        loaded.append(path)
    return loaded


def _deployment() -> Any:
    from tinystudio.config import config

    return config()


@dataclass(frozen=True)
class Settings:
    """Effective configuration, read from the environment after .env loading."""

    project: str | None = field(default_factory=lambda: os.environ.get("TINYSTUDIO_PROJECT"))
    database_url: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_DATABASE_URL", "postgresql+psycopg://tinystudio:tinystudio@127.0.0.1:5432/tinystudio"))
    redis_url: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_REDIS_URL", "redis://127.0.0.1:6379/0"))
    qdrant_url: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_QDRANT_URL", "http://127.0.0.1:6333"))
    data_dir: str = field(default_factory=lambda: str(_deployment().data_dir))
    storage_backend: str = field(default_factory=lambda: _deployment().storage_backend)
    artifact_location: str = field(default_factory=lambda: _deployment().artifact_location)
    inference_url: str | None = field(default_factory=lambda: os.environ.get("TINYSTUDIO_INFERENCE_URL") or None)
    host: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("TINYSTUDIO_PORT", "8000")))
    device: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_DEVICE", "auto"))
    dtype: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_DTYPE", "auto"))
    llm_base_url: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_LLM_BASE_URL", "http://127.0.0.1:8080/v1"))
    llm_model: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_LLM_MODEL", ""))
    llm_api_key: str | None = field(default_factory=lambda: os.environ.get("TINYSTUDIO_LLM_API_KEY"))
    embedding_model: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    anthropic_model: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_ANTHROPIC_MODEL", "claude-opus-5-5"))
    anthropic_workspace_id: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_ANTHROPIC_WORKSPACE_ID", ""))
    assistant_effort: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_ASSISTANT_EFFORT", "medium"))
    openai_model: str = field(default_factory=lambda: os.environ.get("TINYSTUDIO_OPENAI_MODEL", ""))
    anthropic_key_set: bool = field(default_factory=lambda: bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")))
    openai_key_set: bool = field(default_factory=lambda: bool(os.environ.get("OPENAI_API_KEY")))
    hf_token_set: bool = field(default_factory=lambda: bool(os.environ.get("HF_TOKEN")))
    mps_fallback: str = field(default_factory=lambda: os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK", "1"))

    def as_dict(self) -> dict[str, Any]:
        """Settings for display; secrets are reported only as set/unset."""
        from tinystudio.config import redacted

        out = {k: v for k, v in self.__dict__.items()}
        out["llm_api_key"] = "set" if self.llm_api_key else None
        out["database_url"] = redacted(self.database_url)
        out["redis_url"] = redacted(self.redis_url)
        return out


def settings() -> Settings:
    return Settings()
