"""Configuration: every knob tinystudio reads from the environment, with an optional ``.env`` file.

``load_dotenv`` runs when the package is imported. It reads ``TINYSTUDIO_ENV_FILE`` if set, else ``.env`` in the
current directory and in the repository root (the parent of the ``tinystudio`` package), and never overrides a
variable that is already set. ``.env.example`` at the repository root documents every variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SECRET_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "TINYSTUDIO_LLM_API_KEY", "HF_TOKEN")


def load_dotenv(paths: list[Path] | None = None) -> list[Path]:
    """Load KEY=VALUE lines from the first .env files found; returns the files that were read."""
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


@dataclass(frozen=True)
class Settings:
    """Effective configuration, read from the environment after .env loading."""

    project: str | None = field(default_factory=lambda: os.environ.get("TINYSTUDIO_PROJECT"))
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
        out = {k: v for k, v in self.__dict__.items()}
        out["llm_api_key"] = "set" if self.llm_api_key else None
        return out


def settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------- stored settings (edited from the web interface)
STORE_FILE = "settings.json"
STORED_KEYS: dict[str, dict[str, Any]] = {
    "TINYSTUDIO_LLM_BASE_URL": {"label": "Helper LLM base URL", "group": "helper", "secret": False},
    "TINYSTUDIO_LLM_MODEL": {"label": "Helper LLM model", "group": "helper", "secret": False},
    "TINYSTUDIO_LLM_API_KEY": {"label": "Helper LLM API key", "group": "helper", "secret": True},
    "TINYSTUDIO_EMBEDDING_MODEL": {"label": "Embedding model (repositories)", "group": "rag", "secret": False},
    "ANTHROPIC_API_KEY": {"label": "Anthropic API key", "group": "anthropic", "secret": True},
    "TINYSTUDIO_ANTHROPIC_WORKSPACE_ID": {"label": "Anthropic workspace id (only for organization-level keys)", "group": "anthropic", "secret": False},
    "TINYSTUDIO_ANTHROPIC_MODEL": {"label": "Default Claude model", "group": "anthropic", "secret": False},
    "TINYSTUDIO_ASSISTANT_EFFORT": {"label": "Claude effort (low, medium, high)", "group": "anthropic", "secret": False},
    "OPENAI_API_KEY": {"label": "OpenAI API key", "group": "openai", "secret": True},
    "TINYSTUDIO_OPENAI_MODEL": {"label": "Default OpenAI model", "group": "openai", "secret": False},
    "HF_TOKEN": {"label": "Hugging Face token", "group": "hub", "secret": True},
    "TINYSTUDIO_DEVICE": {"label": "Device (auto, cuda, cuda:1, mps, cpu)", "group": "compute", "secret": False},
    "TINYSTUDIO_DTYPE": {"label": "Weights dtype (auto, fp32, bf16, fp16)", "group": "compute", "secret": False},
}


def store_path(root: Path) -> Path:
    return Path(root) / STORE_FILE


def read_store(root: Path) -> dict[str, str]:
    """Values saved from the Settings page for this project (empty when none)."""
    import json

    path = store_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    values = data.get("values", {}) if isinstance(data, dict) else {}
    return {k: str(v) for k, v in values.items() if k in STORED_KEYS and v is not None and str(v) != ""}


def write_store(root: Path, changes: dict[str, str | None]) -> dict[str, str]:
    """Merge changes into the store (None or "" removes a key) and apply them to this process. The file is owner-only."""
    import json
    import time as _time

    current = read_store(root)
    for key, value in changes.items():
        if key not in STORED_KEYS:
            raise ValueError(f"unknown setting {key}")
        if value is None or str(value).strip() == "":
            current.pop(key, None)
            os.environ.pop(key, None) if key in _APPLIED else None
        else:
            current[key] = str(value).strip()
    path = store_path(root)
    path.write_text(json.dumps({"values": current, "updated": _time.strftime("%Y-%m-%dT%H:%M:%S%z")}, indent=1), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    apply_store(root)
    return current


_APPLIED: set[str] = set()


def apply_store(root: Path) -> dict[str, str]:
    """Put the stored values into this process's environment (they win over .env and the shell) so every
    code path that reads the environment, and every task subprocess, sees the values chosen on the Settings page."""
    values = read_store(root)
    for key in list(_APPLIED):
        if key not in values:
            os.environ.pop(key, None)
            _APPLIED.discard(key)
    for key, value in values.items():
        os.environ[key] = value
        _APPLIED.add(key)
    return values


def describe(root: Path | None) -> dict[str, Any]:
    """Every configurable setting with its effective value (secrets as set/unset), where it comes from, and its label."""
    stored = read_store(root) if root else {}
    rows = []
    for key, spec in STORED_KEYS.items():
        value = os.environ.get(key)
        source = "settings" if key in stored else ("environment" if value else "default")
        rows.append({"key": key, "label": spec["label"], "group": spec["group"], "secret": spec["secret"], "set": bool(value), "value": None if spec["secret"] else value, "source": source})
    return {"settings": rows, "store": str(store_path(root)) if root else None, "server": {"host": os.environ.get("TINYSTUDIO_HOST", "127.0.0.1"), "port": os.environ.get("TINYSTUDIO_PORT", "8000")}}
