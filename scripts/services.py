#!/usr/bin/env python3
"""Install per-user launchd services; credentials remain in the project's private .env."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).absolute().parents[1]
COMPONENTS = ("tunnel", "api", "worker")


def configuration():
    # Load values without importing the application or putting secrets in plist/argv.
    sys.path.insert(0, str(ROOT))
    from tinystudio.settings import load_dotenv
    from tinystudio.config import LaunchdConfig

    selected = LaunchdConfig.from_environment(ROOT)
    load_dotenv([selected.env_file])
    return LaunchdConfig.from_environment(ROOT)


def run(component: str) -> None:
    selected = configuration()
    if component == "tunnel":
        try:
            binary = selected.tools.resolve("cloudflared")
        except FileNotFoundError as cause:
            raise SystemExit(str(cause)) from None
        token = os.environ.get("TUNNEL_TOKEN")
        if not token:
            raise SystemExit("TUNNEL_TOKEN is required")
        env = {k: os.environ[k] for k in ("HOME", "PATH", "TMPDIR") if k in os.environ}
        env["TUNNEL_TOKEN"] = token
        os.execve(binary, [binary, "tunnel", "--no-autoupdate", "run"], env)
    # Server processes never need the connector token or outbound Access credential.
    for name in ("TUNNEL_TOKEN", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET", "TINYSTUDIO_API_KEY"):
        os.environ[name] = ""
    command = "serve" if component == "api" else "worker"
    interpreter = selected.tools.resolve("python")
    os.execv(interpreter, [interpreter, "-m", "tinystudio.cli", command])


def manage(action: str, components: list[str]) -> None:
    if sys.platform != "darwin":
        raise SystemExit("launchd service management requires macOS")
    selected = configuration()
    domain = f"gui/{os.getuid()}"
    directory = selected.agents_directory
    logs = selected.logs_directory
    for component in components:
        label = f"{selected.label_prefix}.{component}"
        path = directory / f"{label}.plist"
        if action == "install":
            if selected.env_file.stat().st_mode & 0o077:
                raise SystemExit("Set the selected environment file permissions to 0600 before installing services")
            directory.mkdir(parents=True, exist_ok=True)
            logs.mkdir(parents=True, exist_ok=True, mode=0o700)
            spec = {
                "Label": label,
                "ProgramArguments": [selected.tools.resolve("python"), str(Path(__file__).absolute()), "run", component],
                "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
                "ThrottleInterval": 10, "Umask": 0o077,
                "EnvironmentVariables": {"PATH": selected.tools.path, "TINYSTUDIO_ENV_FILE": str(selected.env_file)},
                "StandardOutPath": str(logs / f"{component}.log"),
                "StandardErrorPath": str(logs / f"{component}.log"),
            }
            if component == "tunnel":
                spec["EnvironmentVariables"]["TINYSTUDIO_CLOUDFLARED"] = selected.tools.resolve("cloudflared")
            if path.exists():
                existing = plistlib.loads(path.read_bytes())
                arguments = existing.get("ProgramArguments")
                if (not isinstance(arguments, list) or len(arguments) != len(spec["ProgramArguments"])
                        or arguments[1:] != spec["ProgramArguments"][1:]
                        or existing.get("WorkingDirectory") != spec["WorkingDirectory"]):
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
