"""Outbound runner with frozen source, scoped transport and owned process groups."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

from .contracts import checksum
from .engine import digest
from .execution_outcome import (CANCELLED_EVIDENCE_EXIT, FAILED_EVIDENCE_EXIT,
                                cancelled_evidence_outcome, failed_evidence_outcome)
from .service import catalog, implementation_digest, runtime_versions
from .shared_work_configuration import SharedWorkConfiguration

TRANSFER_LIMIT = 512 * 1024**2
CLAIM_RETRY_INITIAL_SECONDS = 1.0
CLAIM_RETRY_MAX_SECONDS = 30.0
log = logging.getLogger("tinystudio.lab")


class ClaimUnavailable(RuntimeError):
    """A retryable failure before the client receives any execution authority."""
    def __init__(self, status: int | None = None):
        self.status = status
        super().__init__("Laboratory claim temporarily unavailable")


def _backend_configs(profile_config: dict | None) -> dict:
    if profile_config is None:
        return {}
    if not isinstance(profile_config, dict) or profile_config.get("schema_version") != 1 or not isinstance(profile_config.get("backends", {}), dict):
        raise ValueError("operator profile_config requires schema_version 1 and a backends mapping")
    return profile_config.get("backends", {})


def _backend_runtime(backend: str, config: dict) -> dict:
    if backend in {"native", "rq"}:
        return runtime_versions()
    evidence = config.get("runtime_evidence", {})
    runtime = evidence.get("runtime")
    identity = {"image": config.get("image"), "runtime": runtime}
    if (not isinstance(runtime, dict) or runtime.get("os") != "Linux" or not runtime.get("python") or not runtime.get("machine") or any(not isinstance(key,str) or not isinstance(value,str) for key,value in runtime.items())
            or evidence.get("image") != config.get("image") or evidence.get("sha256") != checksum(identity)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:+-]*@sha256:[0-9a-f]{64}", str(config.get("image", "")))):
        raise ValueError("container runtime requires image-bound operator runtime evidence; host-runtime fallback is prohibited")
    return json.loads(json.dumps(runtime))


def capabilities(profile_config: dict | None = None, *, work_root: Path | None = None) -> dict:
    info, runtime = catalog(), runtime_versions()
    rows = info["adapters"] + info["methods"]
    engines = [row["id"] for row in rows if row["implemented"] and row["available"]]
    result = {"schema_version": 1, "implementation_digest": implementation_digest(), "runtime": runtime,
              "profiles": ["trusted_native"], "backends": ["native"], "engines": engines,
              "runtime_by_backend": {"native": runtime}, "profiles_by_backend": {"native": ["trusted_native"]},
              "engines_by_backend": {"native": list(engines)}, "backend_qualification": {"native": "trusted_native_not_isolated"}}
    result["custom_analysis_python"] = (profile_config or {}).get("engine_grants", {}).get("statistics.analyze", {}).get("native_execution") is True
    from .hardware_bindings import HardwareBindingRegistry
    discovery_grants = (profile_config or {}).get('engine_grants', {}).get('hardware.discover', {})
    result['hardware_discovery_bindings'] = HardwareBindingRegistry.from_grants(discovery_grants, 'discovery').discovery_capabilities()
    from .hardware_session_configuration import HardwareSessionBinding
    session_grants=(profile_config or {}).get('engine_grants',{}).get('hardware.session',{})
    if not isinstance(session_grants,dict):raise ValueError('hardware session grants must be an object')
    result['hardware_session_native_execution']=session_grants.get('native_execution') is True
    session_bindings=session_grants.get('bindings',{})
    if not isinstance(session_bindings,dict):raise ValueError('hardware session bindings must be an object')
    result['hardware_session_bindings']=[HardwareSessionBinding.from_grants(session_grants,key).public_description()
        for key in session_bindings] if result['hardware_session_native_execution'] else []
    from .spool_configuration import SpoolConfiguration
    result['evidence_spool_by_backend'] = {
        backend:SpoolConfiguration.resolve(profile_config,'hardware.discover',backend,work_root=work_root).advertised()
        for backend in {'native',*_backend_configs(profile_config)}}
    for backend, config in _backend_configs(profile_config).items():
        if backend == "rq" and isinstance(config, dict) and config.get("enabled") is True and config.get("profiles") == ["trusted_native"]:
            result["backends"].append("rq")
            result["profiles_by_backend"]["rq"] = ["trusted_native"]
            result["runtime_by_backend"]["rq"] = runtime
            result["engines_by_backend"]["rq"] = list(engines)
            result["backend_qualification"]["rq"] = "rq_delivery_trusted_native_execution"
            continue
        if backend not in {"native", "container", "kubernetes"} or not isinstance(config, dict) or config.get("enabled") is not True or "isolated" not in config.get("profiles", []):
            continue
        if backend == "native":
            if platform.system() != "Darwin" or shutil.which("sandbox-exec") is None or not config.get("runtime_read_paths"):
                continue
            for path in config["runtime_read_paths"]:
                if not Path(path).is_absolute() or not Path(path).exists():
                    raise ValueError("native isolated runtime read roots must be explicit existing absolute paths")
        selected_runtime = _backend_runtime(backend, config)
        if backend == "kubernetes" and (not config.get("shared_work_root") or not config.get("termination_probe") or config.get("grants", {}).get("network_policy_enforced") is not True):
            continue
        if backend not in result["backends"]:
            result["backends"].append(backend)
        if "isolated" not in result["profiles"]:
            result["profiles"].append("isolated")
        result["profiles_by_backend"].setdefault(backend, []).append("isolated")
        result["runtime_by_backend"][backend] = selected_runtime
        if backend in {"container", "kubernetes"}:
            result.setdefault("resource_limits_by_backend", {})[backend] = {
                "cpu": config.get("limits", {}).get("cpu", 1),
                "memory_bytes": config.get("limits", {}).get("memory_bytes", 512*1024**2),
                "pids": config.get("limits", {}).get("pids", 64)}
        if backend == "kubernetes":
            result["kubernetes_pids_limit"] = config.get("grants", {}).get("kubernetes_pids_limit")
        selected_engines = config.get("engines", engines)
        if not isinstance(selected_engines, list) or any(engine not in {row["id"] for row in rows if row["implemented"]} for engine in selected_engines):
            raise ValueError("configured backend engines must name implemented adapters")
        result["engines_by_backend"][backend] = sorted(set(result["engines_by_backend"].get(backend, []) + selected_engines))
        result["engines"] = sorted(set(result["engines"] + selected_engines))
        result["backend_qualification"][backend] = "configured; actual child identity and profile enforcement receipts required"
    for backend,available in result['engines_by_backend'].items():
        if backend!='native' or os.name!='posix':
            result['engines_by_backend'][backend]=[engine for engine in available if engine!='hardware.session']
    return result


def validate_url(server: str) -> str:
    parsed = urlsplit(server)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("runner server must be an origin without credentials, path, query or fragment")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("remote runners require authenticated HTTPS; plain HTTP is allowed only on loopback")
    return server.rstrip("/")


class ImplementationSnapshot:
    """An attempt-owned, hash-bound package; no paths from a manifest escape it."""
    def __init__(self, source: Path, destination: Path, manifest: dict):
        self.source, self.root = source.resolve(), destination
        self.manifest = json.loads(json.dumps(manifest))

    @staticmethod
    def _relative(name: str) -> PurePosixPath:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", name):
            raise ValueError("unsafe implementation manifest path")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in {".", ".."} for part in name.split("/")) or path.suffix != ".py":
            raise ValueError("unsafe implementation manifest path")
        return path

    def create(self, guard=lambda: None):
        files = self.manifest.get("files")
        if self.manifest.get("version") != 1 or not isinstance(files, dict) or not files:
            raise ValueError("unsupported or empty implementation manifest")
        if not {"__init__.py", "settings.py", "lab/__init__.py", "lab/execution.py", "lab/contracts.py", "lab/service.py"}.issubset(files):
            raise ValueError("implementation manifest does not cover the execution bootstrap")
        self.root.mkdir(parents=True, exist_ok=False)
        for name, expected in files.items():
            guard()
            relative = self._relative(name)
            source = self.source.joinpath(*relative.parts)
            if not re.fullmatch(r"[0-9a-f]{64}", str(expected)) or source.is_symlink() or not source.resolve().is_relative_to(self.source):
                raise ValueError("invalid implementation source path or hash")
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("implementation source changed after claim")
            target = self.root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(content)
            target.chmod(0o444)
        for folder in sorted((path for path in self.root.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
            folder.chmod(0o555)
        self.root.chmod(0o555)
        self.verify()
        return self

    def verify(self):
        files = self.manifest["files"]
        observed = set()
        for path in self.root.rglob("*"):
            if path.is_symlink():
                raise ValueError("implementation snapshot contains a symlink")
            if path.is_file():
                name = path.relative_to(self.root).as_posix()
                if name not in files or digest(path) != files[name]:
                    raise ValueError("implementation snapshot checksum mismatch")
                observed.add(name)
        if observed != set(files):
            raise ValueError("implementation snapshot is incomplete")


class ProcessGroup:
    """Own exactly the new session created for this attempt, including descendants."""
    def __init__(self, command: list[str], *, grace: float = 3, **kwargs):
        if os.name != "posix":
            raise ValueError("native execution requires POSIX process-group lifecycle support")
        self._supervisor = None
        from tinystudio.config import NativeProcessSettings

        self._process_settings = NativeProcessSettings.from_environment()
        if sys.platform == "linux":
            from .native_process import NativeSupervisor

            self._supervisor = NativeSupervisor(command, grace=grace, settings=self._process_settings, **kwargs)
            self.process = self._supervisor
        else:
            self.process = subprocess.Popen(command, start_new_session=True, **kwargs)
        self.pgid, self.grace = self.process.pid, grace
        self.closed = False

    def _exists(self):
        try:
            os.killpg(self.pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Never interpret EPERM as absence. Allow teardown to settle, but
            # require an affirmative ESRCH before cleanup can be confirmed.
            return True

    def _signal(self, sig):
        try:
            os.killpg(self.pgid, sig)
        except (ProcessLookupError, PermissionError):
            # A denied signal is not success; subsequent existence checks must
            # still prove the owned group is gone within the cleanup deadline.
            pass

    def close(self):
        if self.closed:
            return
        failure = None
        try:
            self._close_group()
        except BaseException as error:
            failure = error
        finally:
            if self._supervisor is not None:
                try:
                    self._supervisor.close()
                except BaseException as error:
                    if failure is None:
                        failure = error
                    else:
                        failure.add_note(f"Supervisor cleanup also reported: {error}")
        if failure is not None:
            raise failure
        self.closed = True

    def _close_group(self):
        status_error = None
        # Do this even when poll() has reaped the original leader.
        if self._exists():
            self._signal(signal.SIGTERM)
        deadline = time.monotonic() + self.grace
        while self._exists() and time.monotonic() < deadline:
            try:
                self.process.poll()
            except Exception as error:
                # Losing status must not prevent escalation of an owned
                # workload that ignores SIGTERM.
                status_error = error
            time.sleep(self._process_settings.poll_seconds)
        if self._exists():
            self._signal(signal.SIGKILL)
        try:
            self.process.wait(timeout=max(self.grace, .1))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("cannot confirm execution group leader cleanup") from exc
        except Exception as error:
            status_error = error
        deadline = time.monotonic() + max(self.grace, .1)
        while self._exists() and time.monotonic() < deadline:
            time.sleep(self._process_settings.poll_seconds)
        if self._exists():
            raise RuntimeError("cannot confirm execution descendant cleanup")
        if status_error is not None:
            raise RuntimeError("cannot confirm execution exit status") from status_error


class AttemptCancelled(Exception):
    pass


class LeaseHeartbeat:
    """Lease renewal/cancellation outlives individual downloads, execution and uploads."""
    def __init__(self, runner, prefix: str, lease: str, root: Path, *, interval: float, deadline: float,authority=None):
        self.runner, self.prefix, self.lease, self.root = runner, prefix, lease, root
        self.interval, self.deadline = interval, deadline
        self.authority=authority
        self.stop_event = threading.Event()
        self.cancelled = threading.Event()
        self.error: Exception | None = None
        self.connectivity_lost = False
        self.offset = 0
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, name="tinystudio-lab-lease", daemon=True)

    def start(self):
        self.renew()
        self.thread.start()
        return self

    def _events(self):
        path = self.root / "events.jsonl"
        if not path.exists():
            return [], self.offset
        if path.is_symlink():
            raise ValueError("executor events cannot be a symlink")
        events, offset = [], self.offset
        with path.open("rb") as stream:
            stream.seek(offset)
            for _ in range(100):
                line = stream.readline(65537)
                if len(line) > 65536:
                    raise ValueError("executor event exceeds 64 KiB")
                if not line or not line.endswith(b"\n"):
                    break
                events.append(json.loads(line))
                offset = stream.tell()
        return events, offset

    def renew(self):
        with self.lock:
            if self.error is not None:
                raise self.error
            try:
                events, offset = self._events()
                sent_at=time.monotonic()
                try:
                    status = self.runner.post(self.prefix + "/heartbeat", {"events": events}, self.lease)
                except Exception:
                    self.connectivity_lost = True
                    raise
                self.offset = offset
                if status.get("cancel_requested"):
                    if self.authority is not None:self.authority.revoke('server_requested_cancellation')
                    self.cancelled.set()
                    (self.root / "cancel").touch()
                elif self.authority is not None and not self.authority.snapshot()['revoked']:
                    from .attempt_control import AttemptAuthorityLost
                    try:self.authority.renew(status.get('lease_seconds'),sent_at=sent_at)
                    except AttemptAuthorityLost:
                        # Device admission remains latched off. A still-authorized
                        # Lab heartbeat can retain data after the supervisor ends.
                        pass
            except Exception as exc:
                if self.authority is not None:self.authority.revoke('lab_heartbeat_failed')
                self.error = exc
                (self.root / "cancel").touch()
                raise

    def _loop(self):
        while not self.stop_event.wait(self.interval):
            try:
                self.renew()
            except Exception:
                return

    def check(self, *, ignore_cancel=False):
        if self.error:
            if self.connectivity_lost:
                raise RuntimeError("runner lost lease connectivity") from self.error
            raise self.error
        if self.cancelled.is_set() and not ignore_cancel:
            raise AttemptCancelled("server requested cancellation")
        if time.monotonic() > self.deadline:
            if self.authority is not None:self.authority.revoke('selected_execution_deadline_exceeded')
            raise TimeoutError("experiment exceeded selected wall-time budget")

    def stop(self):
        if self.authority is not None:self.authority.revoke('lab_heartbeat_stopped')
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=35)
        if self.thread.is_alive():
            raise RuntimeError("lease heartbeat did not stop within its transport timeout")


class GuardedUpload:
    """Make multipart reads cancellation-aware and enforce their exact size bound."""
    def __init__(self, stream, heartbeat: LeaseHeartbeat, maximum: int, *, audit_only=False):
        self.stream, self.heartbeat, self.maximum = stream, heartbeat, maximum
        self.audit_only = audit_only
        self.read_bytes = 0

    def read(self, size=-1):
        self.heartbeat.check(ignore_cancel=self.audit_only)
        block = self.stream.read(size)
        self.read_bytes += len(block)
        if self.read_bytes > self.maximum:
            raise ValueError("output changed or exceeded its transfer bound")
        return block

    def seek(self, *args):
        return self.stream.seek(*args)

    def tell(self):
        return self.stream.tell()

    def fileno(self):
        return self.stream.fileno()


class Runner:
    def __init__(self, server: str, target_id: str, token: str, *, client=None, work_root: Path | None = None,
                 source_root: Path | None = None, heartbeat_interval: float = 5, process_grace: float = 3,
                 profile_config: dict | None = None, access: dict | None = None):
        if client is None:
            from tinystudio.integrations.tools import Studio
            access = access or {}
            self.client = Studio(validate_url(server), token=token,
                cf_access_client_id=access.get("client_id"), cf_access_client_secret=access.get("client_secret"),
                public_url=access.get("public_url")).http
            self.client.timeout = httpx.Timeout(30)
        else:
            self.client = client
        self.prefix = f"/api/lab/runner/{target_id}"
        self.server = server
        self.target_id = target_id
        self.work_root = work_root
        self.source_root = source_root or Path(__file__).resolve().parents[1]
        self.heartbeat_interval, self.process_grace = heartbeat_interval, process_grace
        self._profile_config = json.loads(json.dumps(profile_config)) if profile_config is not None else None
        _backend_configs(self._profile_config)

    @staticmethod
    def _python_command(backend: str, config: dict) -> str:
        if backend in {"native", "rq"}:
            return sys.executable
        from tinystudio.config import RuntimeTools
        selected = config["python"] if "python" in config else RuntimeTools.from_environment().container_python
        if not isinstance(selected, str) or not selected.strip() or "\x00" in selected:
            raise ValueError("selected execution profile Python interpreter must be a nonempty command")
        return selected

    def post(self, path: str, body: dict, lease: str | None = None):
        response = self.client.post(self.prefix + path, json=body, headers={"X-Lab-Lease": lease} if lease else {})
        response.raise_for_status()
        return response.json()

    def _advertised_capabilities(self):
        if self.work_root is None:
            return capabilities(self._profile_config)
        return capabilities(self._profile_config,work_root=self.work_root)

    def advertise(self) -> dict:
        return self.post("/advertise", {"capabilities": self._advertised_capabilities()})

    def run_once(self, *, run_id: str | None = None) -> bool:
        body = {"capabilities": self._advertised_capabilities()}
        if run_id is not None:
            body["run_id"] = run_id
        try:
            claimed = self.post("/claim", body)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, httpx.ProxyError) as error:
            raise ClaimUnavailable() from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in {408, 429, 500, 502, 503, 504}:
                raise
            raise ClaimUnavailable(error.response.status_code) from error
        attempt = claimed["attempt"]
        if not attempt:
            return False
        backend = attempt["spec"]["definition"]["execution"]["backend"]
        selected = _backend_configs(self._profile_config).get(backend, {})
        directory = selected.get("shared_work_root") if backend == "kubernetes" else self.work_root
        if backend == "kubernetes" and not directory:
            raise ValueError("Kubernetes execution needs an operator-provisioned shared_work_root")
        from .evidence_spool import EvidenceSpool
        from .spool_configuration import SpoolConfiguration
        try:
            selected_spool = SpoolConfiguration.resolve(self._profile_config,attempt["spec"]["definition"]["engine"],backend,work_root=self.work_root)
            selected_spool.require_ready()
            spool_policy = selected_spool.policy
            retained = EvidenceSpool(spool_policy).allocate(attempt, server=self.server, target_id=self.target_id) if spool_policy.enabled else None
        except FileExistsError:
            # Existing evidence may belong to an already active execution of
            # this authority. Never overwrite it or end that attempt here.
            raise
        except Exception as cause:
            try:
                self.post(f"/attempts/{attempt['attempt_id']}/finish",{
                    "state":"failed","result":None,
                    "error":f"Host evidence admission failed before local execution: {type(cause).__name__}: {cause}"[:8192]},attempt['lease_token'])
            except Exception as finish_error:
                cause.add_note(f"Pre-execution failure acknowledgement is unconfirmed: {type(finish_error).__name__}: {finish_error}")
            raise
        if spool_policy.enabled:
            try:
                self.execute_attempt(attempt, retained.execution_root, evidence_recorder=retained)
            except BaseException as cause:
                try:
                    retained.snapshot()
                except Exception as snapshot_error:
                    cause.add_note(f"Local evidence inventory also failed: {type(snapshot_error).__name__}: {snapshot_error}")
                raise
            retained.snapshot()
            return True
        with tempfile.TemporaryDirectory(prefix="tinystudio-lab-", dir=directory) as root:
            self.execute_attempt(attempt, Path(root))
        return True

    def _download(self, spec, root, prefix, lease, heartbeat):
        paths, total = {}, 0
        (root / "inputs").mkdir()
        metadata = {row["id"]: row for row in spec.get("input_manifest", [])}
        for ref in spec["definition"]["inputs"]:
            heartbeat.check()
            identifier = ref["artifact_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier) or identifier in paths:
                raise ValueError("invalid or duplicate input artifact identifier")
            suffix = Path(metadata.get(identifier, {}).get("name", "input.bin")).suffix
            if not re.fullmatch(r"\.[A-Za-z0-9_-]{1,20}", suffix):
                suffix = ".bin"
            target = root / "inputs" / (identifier + suffix)
            with self.client.stream("GET", self.prefix + prefix + "/inputs/" + identifier, headers={"X-Lab-Lease": lease}) as response:
                response.raise_for_status()
                with target.open("xb") as output:
                    received = 0
                    for block in response.iter_bytes():
                        heartbeat.check()
                        received += len(block)
                        total += len(block)
                        if total > TRANSFER_LIMIT:
                            raise ValueError("inputs exceed runner's 512 MiB aggregate transfer limit")
                        output.write(block)
            heartbeat.check()
            if digest(target) != ref["sha256"]:
                raise ValueError("downloaded input checksum mismatch")
            if identifier in metadata and received != metadata[identifier]["size"]:
                raise ValueError("downloaded input size differs from frozen input manifest")
            target.chmod(0o444)
            paths[identifier] = str(target)
        return paths

    @staticmethod
    def _environment(root: Path, snapshot: ImplementationSnapshot):
        environment = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT"}}
        # HOME and temporary files belong to this attempt; no .env, user token,
        # cloud credentials, PYTHONPATH or Python startup hooks are inherited.
        home = root / "work" / "home"
        home.mkdir()
        environment.update({"HOME": str(home), "TMPDIR": str(home), "TINYSTUDIO_SKIP_DOTENV": "1",
                            "PYTHONPATH": str(snapshot.root.parent), "PYTHONDONTWRITEBYTECODE": "1",
                            "PYTHONNOUSERSITE": "1"})
        return environment

    @staticmethod
    def _output_size(root: Path):
        paths = list((root / "work").rglob("*"))
        if any(path.is_symlink() for path in paths):
            raise ValueError("executor output contains a symlink")
        return sum(path.stat().st_size for path in paths if path.is_file())

    def _selection(self, spec):
        policy = spec["definition"]["execution"]
        backend, profile = policy["backend"], policy["profile"]
        if (backend, profile) == ("native", "trusted_native"):
            if policy.get("requirements"):
                raise ValueError("trusted native execution cannot enforce hard resource requirements")
            return {}, runtime_versions()
        config = _backend_configs(self._profile_config).get(backend, {})
        if (backend, profile) == ("rq", "trusted_native"):
            if config.get("enabled") is not True or config.get("profiles") != ["trusted_native"]:
                raise ValueError("RQ trusted native execution must be explicitly enabled by the operator")
            if policy.get("requirements"):
                raise ValueError("RQ trusted native execution cannot enforce hard resource requirements")
            return config, runtime_versions()
        if profile != "isolated" or config.get("enabled") is not True or profile not in config.get("profiles", []):
            raise ValueError("selected execution profile/backend is not enabled by operator configuration")
        advertised = capabilities(self._profile_config)
        if profile not in advertised["profiles_by_backend"].get(backend, []):
            raise ValueError("selected execution backend prerequisites remain unqualified")
        return config, _backend_runtime(backend, config)

    def _profile(self, spec, attempt, root, snapshot, config, command):
        from .profiles import ProfileRequest, RunnerGrants, Limits, build_profile
        grants = config.get("grants", {})
        trusted = RunnerGrants(network_endpoints=tuple(tuple(item) for item in grants.get("network_endpoints", [])),
                               docker_network=grants.get("docker_network"), read_secrets=tuple(Path(value) for value in grants.get("read_secrets", [])),
                               devices=tuple(Path(value) for value in grants.get("devices", [])), kubernetes_secrets=tuple(grants.get("kubernetes_secrets", [])),
                               network_policy_enforced=grants.get("network_policy_enforced", False), kubernetes_pids_limit=grants.get("kubernetes_pids_limit"))
        policy = spec["definition"]["execution"]
        from .contracts import ExecutionProfile
        from .preflight import resource_selection
        resources = resource_selection(ExecutionProfile.model_validate(policy), config.get("limits", {}),
                                       kubernetes_pids_limit=trusted.kubernetes_pids_limit)
        request = ProfileRequest(backend=policy["backend"],attempt_id=attempt["attempt_id"],attempt_root=root,
                                 source_root=snapshot.root,inputs_root=root/"inputs",output_root=root/"work",command=tuple(command),
                                 expected_os=spec["runtime"]["os"], limits=Limits(cpu=resources.get("cpu",1),memory_bytes=resources.get("memory_bytes",512*1024**2),pids=resources.get("pids",64),wall_seconds=policy["max_seconds"]),
                                 runtime_read_paths=tuple(Path(value) for value in config.get("runtime_read_paths", [])) + ((root/"spec.json", root/"context.json") if policy["backend"] == "native" else ()),
                                 image=config.get("image"),namespace=config.get("namespace","tinystudio"),kube_context=config.get("kube_context"),
                                 kubeconfig=Path(config["kubeconfig"]) if config.get("kubeconfig") else None,storage_claim=config.get("storage_claim"),
                                 require_hard_resources=config.get("require_hard_resources",False) or "pids" in policy.get("requirements", {}),
                                 shared_work_root=SharedWorkConfiguration.from_backend(config).root if policy["backend"]=="kubernetes" else None)
        return build_profile(request,trusted)

    @staticmethod
    def _termination_probe(config):
        argv = config.get("termination_probe")
        if not isinstance(argv,list) or not argv or any(not isinstance(value,str) or not value for value in argv):
            return None
        def probe(job_uid):
            result = subprocess.run([*argv,job_uid],capture_output=True,text=True,timeout=30,check=False,
                                    env={key:value for key,value in os.environ.items() if key in {"PATH","LANG","LC_ALL"}})
            if result.returncode != 0:
                return False
            evidence = json.loads(result.stdout)
            return evidence.get("job_uid") == job_uid and evidence.get("terminated") is True
        return probe

    def execute_attempt(self, attempt: dict, root: Path, *, evidence_recorder=None):
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ValueError("attempt root must be an existing absolute non-symlink directory")
        # macOS temporary-directory ancestors can be aliases (/var -> /private/var).
        # Bind command arguments, inputs and mounts to one path before publication.
        root = root.resolve(strict=True)
        # Serialize immediately: all execution-local bindings live in context.json.
        frozen = json.dumps(attempt["spec"], allow_nan=False)
        spec = json.loads(frozen)
        if checksum(spec) != attempt["spec_digest"]:
            raise ValueError("server specification checksum mismatch")
        prefix, lease = f"/attempts/{attempt['attempt_id']}", attempt["lease_token"]
        policy = spec["definition"]["execution"]
        work = root / "work"
        work.mkdir()
        evidence = work / "evidence"
        engine=spec['definition']['engine']
        deadline=time.monotonic()+policy['max_seconds']
        authority=None
        if engine=='hardware.session':
            from .attempt_control import AttemptControl,AttemptIdentity
            authority=AttemptControl(AttemptIdentity(self.server,attempt['run_id'],attempt['attempt_id'],
                self.target_id,attempt['spec_digest']),deadline=deadline)
        heartbeat = LeaseHeartbeat(self, prefix, lease, work, interval=self.heartbeat_interval,
                                   deadline=deadline,authority=authority)
        group = None
        session_bridge=None;parent_artifacts=[];parent_outcome=None
        state, result, error = "failed", None, None
        verified_audit_result = False
        cleanup_confirmed = True
        recorder_error = None
        try:
            heartbeat.start()
            heartbeat.check()
            snapshot = ImplementationSnapshot(self.source_root, root / "runtime" / "tinystudio", spec["implementation"]).create(heartbeat.check)
            config, selected_runtime = self._selection(spec)
            if selected_runtime != spec["runtime"]:
                raise ValueError("runner runtime changed after claim")
            if policy["backend"] == "kubernetes":
                SharedWorkConfiguration.from_backend(config).relative_path(root)
            paths = self._download(spec, root, prefix, lease, heartbeat)
            (root / "spec.json").write_text(frozen)
            (root / "spec.json").chmod(0o444)
            engine = spec["definition"]["engine"]
            operator = self._profile_config or {}
            context = {"spec_digest": attempt["spec_digest"], "source_root": str(snapshot.root.resolve()), "input_paths": paths,
                       "_runtime_grants": operator.get("engine_grants", {}).get(engine, {}),
                       "_runtime_secrets": operator.get("engine_secrets", {}).get(engine, {})}
            if engine=='hardware.session':
                from .hardware_session_engine import validate_selection
                from .hardware_session_configuration import HardwareSessionBinding
                from .hardware_session_bridge import HardwareSessionBridge,SessionBridgePaths
                selected=validate_selection(spec['definition'])
                binding=HardwareSessionBinding.from_grants(operator.get('engine_grants',{}).get(engine,{}),selected.binding_id)
                runtime_secrets=binding.select_runtime_secrets(operator.get('engine_secrets',{}).get(engine,{}))
                session_bridge=HardwareSessionBridge(binding,authority,SessionBridgePaths.evidence_in_work(work),runtime_secrets=runtime_secrets)
                session_bridge.start()
                context['_runtime_grants']={'native_execution':True}
                context['_runtime_secrets']={}
                context['hardware_session']=session_bridge.descriptor
            command = [self._python_command(policy["backend"], config), "-m", "tinystudio.lab.execution",str(root/"spec.json"),str(evidence),str(root/"context.json")]
            # Build against an existing context path, then bind its public manifest.
            (root / "context.json").write_text("{}")
            plan = self._profile(spec,attempt,root,snapshot,config,command) if policy["profile"] == "isolated" else None
            context["profile_manifest"] = plan.manifest() if plan else {"format":"tinystudio.execution-profile.v1","backend":"native","profile":"trusted_native","qualification":"trusted_native_not_isolated"}
            context["profile_manifest"]["profile"] = policy["profile"]
            if evidence_recorder is not None:
                context["profile_manifest"]["evidence_retention"] = {
                    "mechanism":"persistent_host_spool","classification":"operator_host_evidence",
                    "spool_key":evidence_recorder.key,"retention":evidence_recorder.spool.policy.retention,
                    "qualification":"Selected retention mechanism; no filesystem or physical outcome guarantee."}
            if policy["backend"] == "rq":
                context["profile_manifest"].update(backend="rq", execution_backend="native")
            (root / "context.json").write_text(json.dumps(context, allow_nan=False))
            (root / "context.json").chmod(0o444)
            with (work / "stdout.log").open("wb") as stdout, (work / "stderr.log").open("wb") as stderr:
                if plan is None:
                    cleanup_confirmed = False
                    group = ProcessGroup(command, grace=self.process_grace, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=self._environment(root, snapshot), cwd=work)
                    poll = group.process.poll
                else:
                    from .profiles import ProfileExecution
                    cleanup_confirmed = False
                    group = ProfileExecution(plan,termination_probe=self._termination_probe(config))
                    group.start(stdout=stdout,stderr=stderr)
                    poll = group.poll
                returncode = poll()
                cancellation_deadline = None
                while returncode is None:
                    try:
                        heartbeat.check()
                    except AttemptCancelled:
                        # The cancel file is already visible to the child. Give
                        # cooperative engines a bounded opportunity to write an
                        # explicit audit outcome, while lease/time checks remain
                        # active. Uncooperative children follow normal teardown.
                        heartbeat.check(ignore_cancel=True)
                        if cancellation_deadline is None:
                            cancellation_deadline = time.monotonic() + max(0, self.process_grace)
                        if time.monotonic() >= cancellation_deadline:
                            raise
                    if self._output_size(root) > policy["max_output_bytes"]:
                        raise ValueError("experiment exceeded selected output budget")
                    pause = .05 if cancellation_deadline is None else min(.05, max(0, cancellation_deadline - time.monotonic()))
                    time.sleep(pause)
                    returncode = poll()
                if plan is not None and policy["backend"] != "native":
                    captured_out,captured_err = group.logs()
                    stdout.write(captured_out.encode());stderr.write(captured_err.encode())
                    stdout.flush();stderr.flush()
                if authority is not None:authority.revoke('supervisor_process_exited')
                group.close()  # Descendants must stop before reading/hashing any final output.
                cleanup_confirmed = True
                audit_only = returncode in {CANCELLED_EVIDENCE_EXIT, FAILED_EVIDENCE_EXIT}
                if session_bridge is not None:
                    parent_outcome=session_bridge.close()
                    cleanup_confirmed=session_bridge.cleanup_confirmed
                    parent_artifacts=session_bridge.retain_artifacts(evidence,
                        visibility='evaluation' if audit_only else selected.artifact_visibility)
                expected_outcome = (cancelled_evidence_outcome() if returncode == CANCELLED_EVIDENCE_EXIT
                                    else failed_evidence_outcome() if returncode == FAILED_EVIDENCE_EXIT else None)
                def publication_guard():
                    heartbeat.check(ignore_cancel=audit_only)
                publication_guard()
                if returncode not in {0, CANCELLED_EVIDENCE_EXIT, FAILED_EVIDENCE_EXIT}:
                    with (work / "stderr.log").open("rb") as stream:
                        stream.seek(max(0, stream.seek(0, 2) - 8192))
                        error = stream.read().decode(errors="replace")
                else:
                    if self._output_size(root) > policy["max_output_bytes"]:
                        raise ValueError("experiment exceeded selected output budget")
                    snapshot.verify()
                    for ref in spec["definition"]["inputs"]:
                        if digest(Path(paths[ref["artifact_id"]])) != ref["sha256"]:
                            raise ValueError("executor changed a frozen input artifact")
                    if checksum(json.loads((root / "spec.json").read_text())) != attempt["spec_digest"] or checksum(json.loads((root / "context.json").read_text())) != checksum(context):
                        raise ValueError("execution changed its frozen specification or context")
                    if (work / "result.json").stat().st_size > 16 * 1024**2:
                        raise ValueError("executor result exceeds 16 MiB")
                    result = json.loads((work / "result.json").read_text())
                    if not isinstance(result, dict):
                        raise ValueError("executor result must be an object")
                    receipt = json.loads((work / "runtime-receipt.json").read_text())
                    expected_receipt = {"format": "tinystudio.execution-receipt.v1", "spec_digest": attempt["spec_digest"],
                                        "implementation_digest": checksum(spec["implementation"]), "runtime": spec["runtime"],
                                        "source_root": str(snapshot.root.resolve()), "context_digest": checksum(context), "profile_manifest": context["profile_manifest"]}
                    if audit_only:
                        if result.get("execution_outcome") != expected_outcome:
                            raise ValueError("executor lacks its explicit audit outcome")
                        if returncode == FAILED_EVIDENCE_EXIT and not isinstance(result.get("execution_error"), str):
                            raise ValueError("failed audit outcome requires an explicit error")
                        expected_receipt.update(outcome=expected_outcome, result_digest=checksum(result))
                    elif "execution_outcome" in result:
                        raise ValueError("successful executor cannot publish an audit outcome")
                    if receipt != expected_receipt or _backend_runtime(policy["backend"], config) != spec["runtime"]:
                        raise ValueError("executor runtime identity receipt mismatch")
                    if session_bridge is not None:
                        result.setdefault('artifacts',[]).extend(parent_artifacts)
                        result['parent_session']=parent_outcome
                    artifacts, total = [], 0
                    for item in result.get("artifacts", []):
                        publication_guard()
                        path = Path(item["path"])
                        if path.is_symlink() or not path.resolve().is_relative_to(evidence.resolve()) or not path.is_file():
                            raise ValueError("executor artifact escapes its output directory")
                        size = path.stat().st_size
                        total += size
                        if total > min(policy["max_output_bytes"], TRANSFER_LIMIT):
                            raise ValueError("outputs exceed bounded artifact transfer budget")
                        if digest(path) != item["sha256"]:
                            raise ValueError("executor artifact checksum mismatch")
                        with path.open("rb") as stream:
                            response = self.client.post(self.prefix + prefix + "/artifacts", params={"role": item.get("role", "evidence"), "sha256": item["sha256"], "visibility": item.get("visibility", "model")},
                                files={"file": (item["name"], GuardedUpload(stream, heartbeat, size, audit_only=audit_only), item.get("media_type", "application/octet-stream"))}, headers={"X-Lab-Lease": lease})
                        response.raise_for_status()
                        publication_guard()
                        if digest(path) != item["sha256"]:
                            raise ValueError("executor artifact changed during publication")
                        artifacts.append(response.json())
                    heartbeat.renew()
                    publication_guard()
                    result["artifacts"] = artifacts
                    result["execution_receipt"] = receipt
                    state = expected_outcome["state"] if audit_only else "succeeded"
                    error = result.get("execution_error") if returncode == FAILED_EVIDENCE_EXIT else None
                    verified_audit_result = audit_only
        except AttemptCancelled:
            if authority is not None:authority.revoke('attempt_cancelled')
            state, result, error = "cancelled", None, None
        except Exception as cause:
            if authority is not None:authority.revoke('attempt_failed')
            error = f"{type(cause).__name__}: {cause}"[:8192]
        finally:
            if authority is not None:authority.revoke('attempt_cleanup')
            if group is not None:
                try:
                    group.close()
                    cleanup_confirmed = True
                except Exception as cause:
                    cleanup_confirmed = False
                    state, result, error = "failed", None, f"process cleanup unconfirmed: {cause}"[:8192]
            if session_bridge is not None:
                try:session_bridge.close()
                except Exception as cause:
                    state,result,error='failed',None,'parent session cleanup/evidence unconfirmed: '+type(cause).__name__
                cleanup_confirmed=cleanup_confirmed and session_bridge.cleanup_confirmed
            if evidence_recorder is not None:
                try:
                    evidence_recorder.mark_cleanup(cleanup_confirmed)
                except Exception as cause:
                    recorder_error = cause
        try:
            if heartbeat.connectivity_lost:
                raise RuntimeError("runner lost lease connectivity; local execution stopped, server must recover the attempt") from heartbeat.error
            if heartbeat.cancelled.is_set() and state == "succeeded":
                state, result = "cancelled", None
            elif heartbeat.cancelled.is_set() and state == "failed" and verified_audit_result and result is not None:
                state = "cancelled"
            retained = result if state == "succeeded" or (state in {"cancelled", "failed"} and verified_audit_result) else None
            acknowledgement = self.post(prefix + "/finish", {"state": state, "result": retained, "error": error}, lease)
            if evidence_recorder is not None:
                if not isinstance(acknowledgement, dict) or acknowledgement.get("accepted") is not True or acknowledgement.get("state") != state:
                    raise RuntimeError("completion acknowledgement is unconfirmed; local evidence remains retained")
                evidence_recorder.record_server_ack({"accepted": True, "state": state})
        finally:
            heartbeat.stop()
        if recorder_error is not None:
            raise RuntimeError("execution ended but local evidence metadata update failed; inspect the retained spool") from recorder_error


def run(server: str, target_id: str, token: str, *, once: bool = False, poll_seconds: float = 2,
        profile_config: dict | None = None, access: dict | None = None):
    runner = Runner(server, target_id, token, profile_config=profile_config, access=access)
    retry_delay = CLAIM_RETRY_INITIAL_SECONDS
    try:
        while True:
            try:
                worked = runner.run_once()
            except ClaimUnavailable as error:
                if once:
                    raise error.__cause__ from None
                # Never log transport URLs, headers, body text or credentials.
                log.warning("Laboratory claim unavailable (%s); reconnecting in %.1f seconds",
                            f"HTTP {error.status}" if error.status is not None else "transport", retry_delay)
                time.sleep(retry_delay)
                retry_delay = min(CLAIM_RETRY_MAX_SECONDS, retry_delay * 2)
                continue
            retry_delay = CLAIM_RETRY_INITIAL_SECONDS
            if once:
                return worked
            if not worked:
                time.sleep(poll_seconds)
    finally:
        runner.client.close()
