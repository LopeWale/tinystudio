"""Exercise generated launchd specs and exec boundaries without managing host services."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess
import sys

import pytest


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("service_launcher", Path(__file__).parents[1] / "scripts/services.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path / "checkout")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TINYSTUDIO_SKIP_DOTENV", "1")
    monkeypatch.setenv("PATH", str(tmp_path / "tools"))
    monkeypatch.delenv("TINYSTUDIO_PYTHON", raising=False)
    env_file = tmp_path / "selected.env"
    env_file.write_text("TUNNEL_TOKEN=fixture-private-token\n")
    env_file.chmod(0o600)
    monkeypatch.setenv("TINYSTUDIO_ENV_FILE", str(env_file))
    monkeypatch.setenv("TINYSTUDIO_LAUNCHD_DIR", str(tmp_path / "agents"))
    monkeypatch.setenv("TINYSTUDIO_SERVICE_LOG_DIR", str(tmp_path / "logs"))
    calls = []
    def execute(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", execute)
    return module, calls


def test_install_uses_selected_environment_current_interpreter_and_path(launcher, tmp_path):
    module, calls = launcher
    module.manage("install", ["api"])
    files = list((tmp_path / "agents").glob("*.plist"))
    assert len(files) == 1
    saved = plistlib.loads(files[0].read_bytes())
    assert saved["ProgramArguments"][0] == sys.executable
    assert saved["EnvironmentVariables"] == {"PATH": str(tmp_path / "tools"), "TINYSTUDIO_ENV_FILE": str(tmp_path / "selected.env")}
    assert saved["WorkingDirectory"] == str(tmp_path / "checkout")
    assert saved["StandardOutPath"] == str(tmp_path / "logs/api.log")
    assert files[0].stat().st_mode & 0o777 == 0o600
    assert b"fixture-private-token" not in files[0].read_bytes()
    assert calls[-1][1] == "bootstrap"


def test_install_keeps_permission_and_checkout_ownership_checks(launcher, tmp_path):
    module, calls = launcher
    (tmp_path / "selected.env").chmod(0o644)
    with pytest.raises(SystemExit, match="0600"):
        module.manage("install", ["api"])
    assert not calls
    (tmp_path / "selected.env").chmod(0o600)
    module.manage("install", ["api"])
    before = len(calls)
    monkey_checkout = tmp_path / "other-checkout"
    module.ROOT = monkey_checkout
    with pytest.raises(SystemExit, match="another checkout"):
        module.manage("install", ["api"])
    with pytest.raises(SystemExit, match="another checkout"):
        module.manage("uninstall", ["api"])
    assert len(calls) == before


def test_reinstall_same_checkout_accepts_an_updated_interpreter(launcher, monkeypatch, tmp_path):
    module, _ = launcher
    module.manage("install", ["api"])
    interpreter = tmp_path / "selected-python"
    interpreter.symlink_to(sys.executable)
    monkeypatch.setenv("TINYSTUDIO_PYTHON", str(interpreter))
    module.manage("install", ["api"])
    saved = plistlib.loads(next((tmp_path / "agents").glob("*.plist")).read_bytes())
    assert saved["ProgramArguments"][0] == str(interpreter)


def test_tunnel_resolves_selected_connector_and_scopes_credentials(launcher, monkeypatch, tmp_path):
    module, _ = launcher
    connector = tmp_path / "connector"
    connector.write_text("#!/bin/sh\nexit 0\n")
    connector.chmod(0o700)
    monkeypatch.setenv("TINYSTUDIO_CLOUDFLARED", str(connector))
    monkeypatch.setenv("TUNNEL_TOKEN", "fixture-connector-token")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "fixture-r2-secret")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "fixture-access-secret")
    executed = []
    def execute(binary, argv, environment):
        executed.append((binary, argv, environment))
        raise SystemExit("exec boundary")
    monkeypatch.setattr(os, "execve", execute)
    with pytest.raises(SystemExit, match="exec boundary"):
        module.run("tunnel")
    binary, argv, environment = executed[0]
    assert binary == str(connector)
    assert argv == [str(connector), "tunnel", "--no-autoupdate", "run"]
    assert environment["TUNNEL_TOKEN"] == "fixture-connector-token"
    assert "R2_SECRET_ACCESS_KEY" not in environment
    assert "CF_ACCESS_CLIENT_SECRET" not in environment


@pytest.mark.parametrize("component,command", [("api", "serve"), ("worker", "worker")])
def test_application_process_does_not_inherit_connector_or_client_keys(launcher, monkeypatch, component, command):
    module, _ = launcher
    names = ("TUNNEL_TOKEN", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET", "TINYSTUDIO_API_KEY")
    for name in names:
        monkeypatch.setenv(name, "fixture-secret")
    executed = []
    def execute(binary, argv):
        executed.append((binary, argv, {name: os.environ.get(name) for name in names}))
        raise SystemExit("exec boundary")
    monkeypatch.setattr(os, "execv", execute)
    with pytest.raises(SystemExit, match="exec boundary"):
        module.run(component)
    binary, argv, environment = executed[0]
    assert binary == sys.executable
    assert argv[-1] == command
    assert not any(environment.values())


def test_direct_run_honors_the_selected_python_interpreter(launcher, monkeypatch, tmp_path):
    module, _ = launcher
    interpreter = tmp_path / "selected-python"
    interpreter.symlink_to(sys.executable)
    monkeypatch.setenv("TINYSTUDIO_PYTHON", str(interpreter))
    executed = []
    def execute(binary, argv):
        executed.append((binary, argv))
        raise SystemExit("exec boundary")
    monkeypatch.setattr(os, "execv", execute)
    with pytest.raises(SystemExit, match="exec boundary"):
        module.run("worker")
    assert executed[0][0] == str(interpreter)
    assert executed[0][1] == [str(interpreter), "-m", "tinystudio.cli", "worker"]
