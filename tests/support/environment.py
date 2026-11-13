"""Explicitly selected qualification infrastructure and uniquely owned resources.

These helpers never select production stores or silently replace server/container
qualification with local emulation. Importing the module starts no resources.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from types import MappingProxyType
from typing import ClassVar, Mapping
from urllib.parse import quote, urlsplit
import uuid

from qdrant_client import QdrantClient, models
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from tinystudio.config import RuntimeTools


@dataclass(frozen=True)
class TestEnvironment:
    """Test prerequisites, read on demand rather than at collection time."""

    __test__: ClassVar[bool] = False

    tools: RuntimeTools
    temporary_parent: Path
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    container_profile: Path | None = None

    @classmethod
    def from_environment(cls):
        def selected(name):
            return os.environ.get(name, "").strip() or None
        temporary = selected("TINYSTUDIO_TEST_TEMP_DIR") or tempfile.gettempdir()
        profile = selected("TINYSTUDIO_TEST_CONTAINER_PROFILE")
        return cls(RuntimeTools.from_environment(), Path(temporary).expanduser().resolve(),
                   selected("TINYSTUDIO_TEST_QDRANT_URL"), selected("TINYSTUDIO_TEST_QDRANT_API_KEY"),
                   Path(profile).expanduser().resolve() if profile else None)

    def owned_redis(self, work_dir: Path):
        return OwnedRedis(self, work_dir)

    def qdrant_collection(self, *, prefix="ts_capture_test_", dimension=2):
        return OwnedQdrantCollection(self, prefix=prefix, dimension=dimension)

    def require_container(self):
        if self.container_profile is None:
            raise ValueError("set TINYSTUDIO_TEST_CONTAINER_PROFILE to a JSON file with an immutable image and its exact runtime map")
        try:
            profile = json.loads(self.container_profile.read_text())
        except (OSError, ValueError) as error:
            raise ValueError("cannot load TINYSTUDIO_TEST_CONTAINER_PROFILE: " + str(error)) from error
        if not isinstance(profile, dict) or set(profile) - {"image", "runtime", "python"}:
            raise ValueError("container profile must be an object without unknown fields (image, runtime, python)")
        return ContainerQualification(self.tools, profile.get("image"), profile.get("runtime"),
                                      profile.get("python", "python"))


class OwnedRedis:
    """Own one Redis child and Unix socket; never connect to a shared Redis URL."""

    def __init__(self, environment: TestEnvironment, work_dir: Path):
        self.environment, self.work_dir = environment, Path(work_dir)

    def __enter__(self):
        executable = self.environment.tools.resolve("redis_server")
        with ExitStack() as resources:
            directory = resources.enter_context(tempfile.TemporaryDirectory(
                prefix="ts-redis-", dir=self.environment.temporary_parent))
            self.socket = Path(directory) / "redis.sock"
            self.url = "unix://" + quote(str(self.socket), safe="/") + "?db=0"
            self.log_path = self.work_dir / "redis.log"
            log = resources.enter_context(self.log_path.open("wb"))
            self.process = subprocess.Popen([executable, "--port", "0", "--unixsocket", str(self.socket),
                "--save", "", "--appendonly", "no", "--dir", str(self.work_dir)],
                stdout=log, stderr=subprocess.STDOUT)
            resources.callback(self._stop)
            self.connection = Redis.from_url(self.url, socket_connect_timeout=.2, socket_timeout=1)
            resources.callback(self.connection.close)
            deadline = time.monotonic() + 5
            while True:
                if self.process.poll() is not None:
                    raise RuntimeError("owned Redis exited before readiness; inspect " + str(self.log_path))
                try:
                    if self.connection.ping():
                        break
                except (RedisConnectionError, RedisTimeoutError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("owned Redis did not initialize; inspect " + str(self.log_path)
                                       + "; TINYSTUDIO_TEST_TEMP_DIR can select a shorter Unix socket directory")
                time.sleep(.02)
            self._resources = resources.pop_all()
        return self

    def _stop(self):
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def __exit__(self, *error):
        return self._resources.__exit__(*error)


class OwnedQdrantCollection:
    """Own a UUID collection on an explicitly selected real Qdrant server."""

    def __init__(self, environment: TestEnvironment, *, prefix: str, dimension: int):
        self.environment, self.dimension = environment, dimension
        self.name = prefix + uuid.uuid4().hex

    def __enter__(self):
        url = self.environment.qdrant_url
        if not url:
            raise ValueError("set TINYSTUDIO_TEST_QDRANT_URL to an explicitly selected test Qdrant server")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("TINYSTUDIO_TEST_QDRANT_URL must select an HTTP(S) Qdrant server; local mode does not qualify server behavior")
        with ExitStack() as resources:
            self.client = QdrantClient(url=url, api_key=self.environment.qdrant_api_key, timeout=10)
            resources.callback(self.client.close)
            # Register before sending: a timeout can leave a successfully created
            # collection whose response was lost. Reconcile only this UUID name.
            resources.callback(self._delete)
            self.client.create_collection(self.name, vectors_config=models.VectorParams(
                size=self.dimension, distance=models.Distance.COSINE))
            self._resources = resources.pop_all()
        return self

    def _delete(self):
        if self.client.collection_exists(self.name):
            self.client.delete_collection(self.name)

    def __exit__(self, *error):
        return self._resources.__exit__(*error)


@dataclass(frozen=True)
class ContainerQualification:
    """Selected immutable local/shared-filesystem image and recorded runtime.

Bind mounts refer to the Docker engine host. Selecting a remote context does
not stage client-side files or qualify a remote execution environment.
"""

    tools: RuntimeTools
    image: str
    runtime: Mapping[str, str]
    python_command: str = "python"

    required_runtime_keys: ClassVar[frozenset[str]] = frozenset({
        "os", "machine", "python", "torch", "transformers", "peft", "trl", "bitsandbytes",
        "accelerate", "tokenizers", "safetensors", "datasets"})
    owner_label: ClassVar[str] = "tinystudio.test.owner"

    runtime_script = '''
import importlib.metadata as metadata, json, platform
runtime = {"os": platform.system(), "machine": platform.machine(), "python": platform.python_version()}
for name in ("numpy", "torch", "transformers", "pyarrow", "tokenizers", "peft", "trl", "accelerate", "bitsandbytes", "safetensors", "datasets", "sentence-transformers", "scipy", "pint", "simpy", "ortools", "hypothesis", "fmpy", "pybamm", "pandapower", "torchrl", "tensordict", "pyserial", "pymodbus", "paho-mqtt"):
    try: runtime[name] = metadata.version(name)
    except metadata.PackageNotFoundError: pass
print(json.dumps(runtime))
'''

    def __post_init__(self):
        if not isinstance(self.image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[0-9a-f]{64}", self.image):
            raise ValueError("container qualification requires an immutable image@sha256 digest")
        if (not isinstance(self.runtime, dict) or not self.required_runtime_keys <= self.runtime.keys()
                or any(not isinstance(key, str) or not isinstance(value, str) or not value
                       for key, value in self.runtime.items())):
            raise ValueError("container runtime must record os, machine, python and all QLoRA qualification package versions")
        if not isinstance(self.python_command, str) or not self.python_command or "\0" in self.python_command:
            raise ValueError("container python must be an explicit executable name or path")
        object.__setattr__(self, "runtime", MappingProxyType(dict(self.runtime)))

    def validate_runtime(self, measured):
        if measured != dict(self.runtime):
            raise ValueError("measured container runtime differs from the exact recorded qualification runtime: "
                             + json.dumps({"expected": dict(self.runtime), "measured": measured}, sort_keys=True))

    def measure_runtime(self):
        executable = self.tools.resolve("docker")
        identity = subprocess.run([executable, "image", "inspect", self.image, "--format", "{{json .RepoDigests}}"],
                                  capture_output=True, text=True, check=True)
        if self.image not in json.loads(identity.stdout):
            raise ValueError("local image must retain the selected immutable repository digest")
        measured = json.loads(self.python(self.runtime_script))
        self.validate_runtime(measured)
        return measured

    def python(self, code, *, mounts=(), environment=None, timeout=180):
        executable = self.tools.resolve("docker")
        owner = uuid.uuid4().hex
        name = "tinystudio-d-proof-" + owner
        command = [executable, "run", "--rm", "--name", name, "--pull=never", "--network", "none",
                   "--label", self.owner_label + "=" + owner,
                   "--read-only", "--workdir", "/", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
                   "--cpus", "1", "--memory", "2g", "--pids-limit", "128",
                   "--user", f"{max(1, os.getuid())}:{max(1, os.getgid())}"]
        for source, target, writable in mounts:
            command += ["--mount", f"type=bind,src={source},dst={target}" + ("" if writable else ",readonly")]
        for key, value in {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                           "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", **(environment or {})}.items():
            command += ["--env", f"{key}={value}"]
        try:
            result = subprocess.run([*command, self.image, self.python_command, "-c", code],
                                    capture_output=True, text=True, timeout=timeout, check=False)
        finally:
            # A client can exit unsuccessfully, or be interrupted, while its
            # container remains. Never remove by a possibly reused name.
            self._cleanup_container(executable, name, owner)
        if result.returncode != 0:
            raise AssertionError(result.stdout + "\n" + result.stderr)
        return result.stdout

    def _inspect_container(self, executable, selector, owner, *, identity=None):
        result = subprocess.run([executable, "inspect", selector], capture_output=True,
                                text=True, timeout=30, check=False)
        if result.returncode != 0:
            # A generic error or absence of a different resource cannot prove
            # that the exact identity we inspected is absent.
            absence = r"\bno such (?:object|container):?\s+['\"]?" + re.escape(selector) + r"['\"]?(?:\s|$)"
            if re.search(absence, result.stderr, re.IGNORECASE):
                return None
            raise RuntimeError("owned container absence could not be confirmed: " + result.stderr[-2048:])
        try:
            records = json.loads(result.stdout)
            if not isinstance(records, list) or len(records) != 1:
                raise ValueError("ambiguous inspection")
            record = records[0]
            identifier = record["Id"]
            config = record["Config"]
            if (not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier)
                    or (identity is not None and identifier != identity)
                    or config.get("Image") != self.image or config.get("Labels", {}).get(self.owner_label) != owner):
                raise ValueError("identity, image or owner mismatch")
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise RuntimeError("container ownership could not be verified; refusing cleanup") from error
        return identifier

    def _cleanup_container(self, executable, name, owner):
        identity = self._inspect_container(executable, name, owner)
        if identity is None:
            return
        removed = subprocess.run([executable, "rm", "--force", identity], capture_output=True,
                                 text=True, timeout=30, check=False)
        if self._inspect_container(executable, identity, owner, identity=identity) is not None:
            raise RuntimeError("owned container cleanup could not be confirmed: " + removed.stderr[-2048:])
