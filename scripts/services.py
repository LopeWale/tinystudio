#!/usr/bin/env python3
"""Install per-user launchd services; credentials remain in the project's private .env."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).absolute().parents[1]
LABEL_PREFIX = "dev.joatlabs.tinystudio"
COMPONENTS = ("tunnel", "api", "worker")


def run(component: str) -> None:
    # Load values without importing the application or putting secrets in plist/argv.
    sys.path.insert(0, str(ROOT))
    from tinystudio.settings import load_dotenv
    load_dotenv([ROOT / ".env"])
    if component == "tunnel":
        binary = shutil.which("cloudflared", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
        token = os.environ.get("TUNNEL_TOKEN")
        if not binary or not token:
            raise SystemExit("cloudflared and TUNNEL_TOKEN are required")
        env = {k: os.environ[k] for k in ("HOME", "PATH", "TMPDIR") if k in os.environ}
        env["TUNNEL_TOKEN"] = token
        os.execve(binary, [binary, "tunnel", "--no-autoupdate", "run"], env)
    # Server processes never need the connector token or outbound Access credential.
    for name in ("TUNNEL_TOKEN", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET", "TINYSTUDIO_API_KEY"):
        os.environ[name] = ""
    command = "serve" if component == "api" else "worker"
    os.execv(sys.executable, [sys.executable, "-m", "tinystudio.cli", command])


def manage(action: str, components: list[str]) -> None:
    domain = f"gui/{os.getuid()}"
    directory = Path.home() / "Library/LaunchAgents"
    logs = Path.home() / "Library/Logs/TinyStudio"
    for component in components:
        label = f"{LABEL_PREFIX}.{component}"
        path = directory / f"{label}.plist"
        if action == "install":
            if (ROOT / ".env").stat().st_mode & 0o077:
                raise SystemExit("Set .env permissions to 0600 before installing services")
            directory.mkdir(parents=True, exist_ok=True)
            logs.mkdir(parents=True, exist_ok=True, mode=0o700)
            spec = {
                "Label": label,
                "ProgramArguments": [str(ROOT / ".venv/bin/python"), str(Path(__file__).absolute()), "run", component],
                "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
                "ThrottleInterval": 10, "Umask": 0o077,
                "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "TINYSTUDIO_ENV_FILE": str(ROOT / ".env")},
                "StandardOutPath": str(logs / f"{component}.log"),
                "StandardErrorPath": str(logs / f"{component}.log"),
            }
            if path.exists():
                existing = plistlib.loads(path.read_bytes())
                if existing.get("ProgramArguments") != spec["ProgramArguments"]:
                    raise SystemExit(f"Refusing to replace another checkout's service: {label}")
            subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
            path.write_bytes(plistlib.dumps(spec))
            path.chmod(0o600)
            subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
            print(f"Installed {label}; starts at login and restarts after exit")
        elif action == "uninstall":
            if path.exists() and plistlib.loads(path.read_bytes()).get("WorkingDirectory") != str(ROOT):
                raise SystemExit(f"Refusing to remove another checkout's service: {label}")
            subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
            path.unlink(missing_ok=True)
            print(f"Removed {label}")
        else:
            result = subprocess.run(["launchctl", "print", f"{domain}/{label}"], text=True, capture_output=True)
            fields = [s.strip() for s in result.stdout.splitlines() if any(s.strip().startswith(k) for k in ("state =", "pid =", "last exit code ="))]
            print(label + ": " + (", ".join(fields) if result.returncode == 0 else "not loaded"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall", "status", "run"))
    parser.add_argument("components", nargs="+", choices=COMPONENTS)
    args = parser.parse_args()
    if args.action == "run":
        if len(args.components) != 1:
            parser.error("run takes one component")
        run(args.components[0])
    else:
        manage(args.action, args.components)
