"""Configuration consumers agree; no stores, credentials, or external services."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

configuration = importlib.import_module("tinystudio.config")


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYSTUDIO_SKIP_DOTENV", "1")
    monkeypatch.setenv("TINYSTUDIO_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TINYSTUDIO_DATA_DIR", str(tmp_path / "data"))
    configuration.config.cache_clear()
    yield
    configuration.config.cache_clear()


def test_display_uses_the_same_normalized_deployment_snapshot(monkeypatch):
    from tinystudio.settings import settings

    monkeypatch.setenv("TINYSTUDIO_DATABASE_URL", "  postgresql+psycopg://user:secret@db.invalid/studio  ")
    monkeypatch.setenv("TINYSTUDIO_REDIS_URL", "  redis://cache.invalid:6379/3  ")
    monkeypatch.setenv("TINYSTUDIO_INFERENCE_URL", "  https://inference.invalid///  ")
    monkeypatch.setenv("TINYSTUDIO_HOST", "  localhost  ")
    monkeypatch.setenv("TINYSTUDIO_DEVICE", "  cpu  ")
    deployed = configuration.config()
    displayed = settings()
    assert displayed.database_url == deployed.database_url == "postgresql+psycopg://user:secret@db.invalid/studio"
    assert displayed.redis_url == deployed.redis_url == "redis://cache.invalid:6379/3"
    assert displayed.inference_url == deployed.inference_url == "https://inference.invalid"
    assert displayed.host == deployed.host == "localhost"
    assert displayed.device == deployed.device == "cpu"
    assert "secret" not in str(displayed.as_dict())


def test_deployment_reload_does_not_freeze_editable_preferences(monkeypatch):
    from tinystudio.core.llm import LLMClient
    from tinystudio.integrations.assistant import anthropic_default
    from tinystudio.settings import settings

    monkeypatch.setenv("TINYSTUDIO_PORT", "8765")
    configuration.reload_config()
    monkeypatch.setenv("TINYSTUDIO_PORT", "8766")
    monkeypatch.setenv("TINYSTUDIO_LLM_BASE_URL", "https://helper.invalid/v1/")
    monkeypatch.setenv("TINYSTUDIO_ANTHROPIC_MODEL", "research-model-first")
    assert settings().port == 8765
    assert LLMClient.from_env().base_url == "https://helper.invalid/v1"
    assert anthropic_default() == "research-model-first"
    monkeypatch.setenv("TINYSTUDIO_LLM_BASE_URL", "https://replacement.invalid/v1/")
    monkeypatch.setenv("TINYSTUDIO_ANTHROPIC_MODEL", "research-model-next")
    assert settings().llm_base_url == LLMClient.from_env().base_url == "https://replacement.invalid/v1"
    assert anthropic_default() == "research-model-next"
    assert configuration.reload_config().port == settings().port == 8766


def test_client_default_follows_configured_api_port_and_preserves_explicit_origin(monkeypatch):
    monkeypatch.setenv("TINYSTUDIO_PORT", "8765")
    monkeypatch.delenv("TINYSTUDIO_URL", raising=False)
    from tinystudio import config as module

    assert hasattr(module, "ClientConfig"), "CLI and MCP need one shared endpoint configuration"
    assert module.ClientConfig.from_environment().url == "http://127.0.0.1:8765"
    monkeypatch.setenv("TINYSTUDIO_URL", "https://studio.example.invalid/base/")
    assert module.ClientConfig.from_environment().url == "https://studio.example.invalid/base"


def test_lab_http_client_uses_the_shared_endpoint_without_sending_a_request(monkeypatch):
    from tinystudio.lab.cli import client

    monkeypatch.setenv("TINYSTUDIO_PORT", "8765")
    monkeypatch.setenv("TINYSTUDIO_API_KEY", "fixture-api-key")
    monkeypatch.delenv("TINYSTUDIO_URL", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    with client() as http:
        assert str(http.base_url) == "http://127.0.0.1:8765"
        assert http.headers["Authorization"] == "Bearer fixture-api-key"


def test_explicit_mcp_origin_does_not_load_unrelated_server_deployment(monkeypatch):
    from tinystudio.integrations import mcp_server

    monkeypatch.setenv("TINYSTUDIO_STORAGE_BACKEND", "not-a-server-configuration")
    monkeypatch.delenv("TINYSTUDIO_URL", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    started = []
    class Server:
        def __init__(self, studio):
            self.studio = studio
        def run(self, *, transport):
            try:
                started.append((self.studio.base_url, self.studio.project, transport))
            finally:
                self.studio.http.close()
    monkeypatch.setattr(mcp_server, "build_server", Server)
    mcp_server.main("https://remote-studio.invalid", "research", "fixture-api-key")
    assert started == [("https://remote-studio.invalid", "research", "stdio")]


def test_runtime_tools_resolve_from_selected_path_and_respect_explicit_override(monkeypatch, tmp_path):
    assert hasattr(configuration, "RuntimeTools"), "runtime commands need a shared portable resolver"
    directory = tmp_path / "tools"
    directory.mkdir()
    executable = directory / "cloudflared"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(directory))
    monkeypatch.delenv("TINYSTUDIO_CLOUDFLARED", raising=False)
    assert Path(configuration.RuntimeTools.from_environment().resolve("cloudflared")) == executable
    monkeypatch.setenv("TINYSTUDIO_CLOUDFLARED", str(directory / "absent"))
    with pytest.raises(FileNotFoundError, match="TINYSTUDIO_CLOUDFLARED"):
        configuration.RuntimeTools.from_environment().resolve("cloudflared")


def test_path_layout_preserves_local_and_r2_cache_locations(monkeypatch, tmp_path):
    assert hasattr(configuration, "RuntimePaths"), "all runtime paths need one importable layout"
    cfg = configuration.config()
    assert cfg.paths.artifacts == tmp_path / "data/artifacts"
    assert cfg.paths.frontend_releases == tmp_path / "data/frontend-releases"
    assert cfg.project_dir("p") == tmp_path / "data/projects/p"
    assert cfg.paths.snapshot("p", "s") == tmp_path / "data/projects/p/snapshots/s"
    assert cfg.task_log("t") == tmp_path / "data/tasks/t.log"
    monkeypatch.setenv("TINYSTUDIO_STORAGE_BACKEND", "r2")
    monkeypatch.setenv("TINYSTUDIO_CACHE_DIR", str(tmp_path / "disposable"))
    monkeypatch.setenv("R2_BUCKET_NAME", "research")
    remote = configuration.reload_config()
    assert remote.paths.artifacts == tmp_path / "disposable/artifacts"
    assert remote.artifact_location == "r2://research"


def test_explicit_relative_executable_does_not_select_a_different_path_command(monkeypatch, tmp_path):
    selected = tmp_path / "python"
    selected.write_text("#!/bin/sh\nexit 0\n")
    selected.chmod(0o700)
    other = tmp_path / "other"
    other.mkdir()
    (other / "python").symlink_to(selected)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TINYSTUDIO_PYTHON", "./python")
    monkeypatch.setenv("PATH", str(other))
    assert configuration.RuntimeTools.from_environment().resolve("python") == str(selected)
