"""Settings: .env parsing, the server-side store the Settings page edits, and how it reaches the environment."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tinystudio import settings as cfg
from tinystudio.core.project import Project


def test_load_dotenv_parses_quotes_comments_and_does_not_override(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nexport TS_TEST_A=plain   # inline comment\nTS_TEST_B=\"quoted # not a comment\"\nTS_TEST_C='single'\nTS_TEST_D=\n\nTS_TEST_E=already\n")
    monkeypatch.setenv("TS_TEST_E", "shell wins")
    for k in ("TS_TEST_A", "TS_TEST_B", "TS_TEST_C", "TS_TEST_D"):
        monkeypatch.delenv(k, raising=False)
    assert cfg.load_dotenv([env, env]) == [env]  # the same file is read once
    assert os.environ["TS_TEST_A"] == "plain" and os.environ["TS_TEST_B"] == "quoted # not a comment" and os.environ["TS_TEST_C"] == "single" and os.environ["TS_TEST_D"] == "" and os.environ["TS_TEST_E"] == "shell wins"
    for k in ("TS_TEST_A", "TS_TEST_B", "TS_TEST_C", "TS_TEST_D"):
        monkeypatch.delenv(k, raising=False)


def test_store_round_trip_applies_and_masks(tmp_path: Path, monkeypatch):
    root = tmp_path / "p"
    Project.create(root, "p")
    for k in ("TINYSTUDIO_LLM_MODEL", "OPENAI_API_KEY", "TINYSTUDIO_DEVICE"):
        monkeypatch.delenv(k, raising=False)
    assert cfg.read_store(root) == {}
    saved = cfg.write_store(root, {"TINYSTUDIO_LLM_MODEL": "qwen3", "OPENAI_API_KEY": "sk-test", "TINYSTUDIO_DEVICE": " cpu "})
    assert saved == {"TINYSTUDIO_LLM_MODEL": "qwen3", "OPENAI_API_KEY": "sk-test", "TINYSTUDIO_DEVICE": "cpu"}
    assert os.environ["TINYSTUDIO_LLM_MODEL"] == "qwen3" and os.environ["OPENAI_API_KEY"] == "sk-test"
    assert oct(cfg.store_path(root).stat().st_mode & 0o777) == "0o600"
    assert json.loads(cfg.store_path(root).read_text())["values"]["OPENAI_API_KEY"] == "sk-test"
    shown = {row["key"]: row for row in cfg.describe(root)["settings"]}
    assert shown["OPENAI_API_KEY"]["set"] is True and shown["OPENAI_API_KEY"]["value"] is None and shown["OPENAI_API_KEY"]["source"] == "settings"
    assert shown["TINYSTUDIO_LLM_MODEL"]["value"] == "qwen3"
    # clearing removes the key from the store and from the environment
    cfg.write_store(root, {"OPENAI_API_KEY": None, "TINYSTUDIO_DEVICE": ""})
    assert "OPENAI_API_KEY" not in cfg.read_store(root) and "OPENAI_API_KEY" not in os.environ and "TINYSTUDIO_DEVICE" not in os.environ
    with pytest.raises(ValueError, match="unknown setting"):
        cfg.write_store(root, {"NOT_A_SETTING": "x"})
    # opening the project applies the store to the environment of that process
    monkeypatch.delenv("TINYSTUDIO_LLM_MODEL", raising=False)
    Project.open(root)
    assert os.environ["TINYSTUDIO_LLM_MODEL"] == "qwen3"
    cfg.write_store(root, {"TINYSTUDIO_LLM_MODEL": None})


def test_device_override_validation(monkeypatch):
    from tinystudio.serving.runner import pick_device

    monkeypatch.setenv("TINYSTUDIO_DEVICE", "cpu")
    assert pick_device() == "cpu"
    monkeypatch.setenv("TINYSTUDIO_DEVICE", "auto")
    assert pick_device() in ("cuda", "mps", "cpu")
    monkeypatch.setenv("TINYSTUDIO_DEVICE", "not-a-device")
    with pytest.warns(UserWarning, match="not a valid device"):
        assert pick_device() in ("cuda", "mps", "cpu")


def test_workspace_id_is_an_anthropic_setting():
    from tinystudio.settings import STORED_KEYS, Settings

    assert STORED_KEYS["TINYSTUDIO_ANTHROPIC_WORKSPACE_ID"] == {"label": "Anthropic workspace id (only for organization-level keys)", "group": "anthropic", "secret": False}
    assert "anthropic_workspace_id" in Settings().as_dict()
