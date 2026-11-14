"""Private, persistent execution folders with explicit public evidence snapshots.

Only work/evidence bytes enter the public inventory. A snapshot is operator-host
evidence, not proof of execution success, physical stop, or source attestation.
Quotas prevent new allocations; they never make existing evidence unrecoverable.
Retention is recorded policy metadata, with no automatic pruning or execution.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import urlsplit, urlunsplit
import uuid
import zipfile

from .contracts import checksum


FORMAT = "tinystudio.evidence-spool.v1"
_KEY = re.compile(r"attempt-[0-9a-f]{64}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_CHUNK = 1024 * 1024
_PRIVATE_KEYS = {"token", "lease_token", "target_token", "password", "secrets", "grants",
                 "_runtime_secrets", "_runtime_grants"}


def _encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def _pairs(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("duplicate evidence manifest field")
        value[name] = item
    return value


def _nonfinite(value):
    raise ValueError("nonfinite evidence manifest value")


def _relative(name):
    if (not isinstance(name, str) or not name or "\\" in name or PurePosixPath(name).is_absolute()
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or any(ord(char) < 32 for char in name)):
        raise ValueError("unsafe evidence relative path")
    return name.split("/")


def _absolute(value):
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError) as exc:
        raise ValueError("spool root requires an absolute path") from exc
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("spool root requires an absolute path without traversal")
    return path


def _operator_path(value):
    """Canonicalize operator-selected ancestors once, rejecting the final link.

    This permits platform aliases in selected root/export parents. Internal
    evidence paths never use this helper: their descriptor walk rejects links.
    """
    path = _absolute(value)
    if path.is_symlink():
        raise ValueError("operator-selected final path cannot be a symlink")
    return path.parent.resolve() / path.name


@contextmanager
def _directory(path, *, create=False):
    """Walk descriptors without following a symlink in any path component."""
    path = _absolute(path)
    descriptor = os.open(path.anchor, _DIRECTORY)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
                child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError("spool path contains a symlink or non-directory") from exc
                raise
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _private_directory(path, *, create=False):
    with _directory(path, create=create) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("spool directories must be operator-owned with mode 0700")


@contextmanager
def _regular(base, name, *, flags=os.O_RDONLY):
    parts = _relative(name)
    with _directory(base.joinpath(*parts[:-1])) as parent:
        try:
            descriptor = os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError("evidence file cannot be a symlink") from exc
            raise
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("evidence must be a regular file without hard links")
            yield descriptor
        finally:
            os.close(descriptor)


@contextmanager
def _locked(base, name):
    with _regular(base, name, flags=os.O_RDWR | os.O_CREAT) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("spool lock must be operator-owned with mode 0600")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_nlink)


def _files(base):
    """Enumerate evidence only, rejecting links and non-regular entries."""
    result = []
    def walk(descriptor, prefix):
        for name in sorted(os.listdir(descriptor)):
            relative = "/".join([*prefix, name])
            _relative(relative)
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                try:
                    child = os.open(name, _DIRECTORY, dir_fd=descriptor)
                except OSError as exc:
                    raise ValueError("evidence directory changed or became a symlink") from exc
                try:
                    walk(child, [*prefix, name])
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                result.append(relative)
            else:
                raise ValueError("evidence contains a symlink, hard link, or non-regular file")
    try:
        with _directory(base) as descriptor:
            walk(descriptor, [])
    except FileNotFoundError:
        # An attempt may fail before creating its evidence folder.
        if base.exists() or base.is_symlink():
            raise ValueError("evidence changed during enumeration")
    return sorted(result)


def _inventory_file(base, name, *, sink=None):
    with _regular(base, name) as descriptor:
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        size = newline_count = tail_bytes = 0
        last = b""
        while block := os.read(descriptor, _CHUNK):
            digest.update(block)
            size += len(block)
            newline_count += block.count(b"\n")
            last_break = block.rfind(b"\n")
            tail_bytes = len(block) - last_break - 1 if last_break >= 0 else tail_bytes + len(block)
            last = block[-1:]
            if sink is not None:
                sink.write(block)
        if _identity(before) != _identity(os.fstat(descriptor)) or size != before.st_size:
            raise ValueError("evidence changed while hashing or exporting")
    item = {"path": "evidence/" + name, "size": size, "sha256": digest.hexdigest()}
    if Path(name).suffix.lower() in {".jsonl", ".ndjson"}:
        item["jsonl"] = {"newline_count": newline_count, "tail_bytes": tail_bytes,
                         "ends_with_newline": last == b"\n", "semantic_validation": "not_performed"}
    return item


def _server_origin(server):
    if not isinstance(server, str) or any(ord(char) < 32 for char in server):
        raise ValueError("spool origin requires a credential-free server origin")
    parsed = urlsplit(server)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid server origin port") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("spool origin requires scheme, host and optional port without credentials")
    host = "[" + parsed.hostname + "]" if ":" in parsed.hostname else parsed.hostname
    return urlunsplit((parsed.scheme, host + (":" + str(port) if port is not None else ""), "", "", ""))


def _key(origin):
    return "attempt-" + checksum({name: origin[name] for name in ("server", "target_id", "attempt_id")})


def _validate_manifest(value):
    """Keep the public v1 surface closed to accidentally retained private data."""
    def require(condition):
        if not condition:
            raise ValueError("invalid public evidence manifest shape or inventory")
    require(isinstance(value, dict) and set(value) == {"format", "origin", "classification", "outcome",
            "cleanup", "publication", "policy", "quota", "files"})
    require(value["format"] == FORMAT and value["classification"] == "operator_host_evidence")
    origin = value["origin"]
    require(isinstance(origin, dict) and set(origin) == {"server", "target_id", "run_id", "attempt_id",
            "spec_digest", "engine", "implementation_digest", "runtime"})
    require(all(isinstance(origin[name], str) and origin[name] for name in
                ("server", "target_id", "run_id", "attempt_id", "engine")))
    require(_server_origin(origin["server"]) == origin["server"])
    require(all(isinstance(origin[name], str) and _HASH.fullmatch(origin[name])
                for name in ("spec_digest", "implementation_digest")))
    require(isinstance(origin["runtime"], dict) and all(isinstance(k, str) and isinstance(v, str)
            and k.lower() not in _PRIVATE_KEYS for k, v in origin["runtime"].items()))
    require(value["outcome"] == {"verification": "unverified", "physical_stop": "unverified"})
    cleanup = value["cleanup"]
    require(isinstance(cleanup, dict) and set(cleanup) == {"status", "private_context_removed"}
            and cleanup["status"] in {"unknown", "confirmed"} and type(cleanup["private_context_removed"]) is bool)
    publication = value["publication"]
    require(isinstance(publication, dict))
    if publication.get("status") == "unknown":
        require(publication == {"status": "unknown"})
    else:
        require(set(publication) == {"status", "server_state", "ack"}
                and publication["status"] == "server_acknowledged"
                and publication["server_state"] in {"succeeded", "failed", "cancelled"})
        require(publication["ack"] == {"accepted": True, "state": publication["server_state"],
                "run_id": origin["run_id"], "attempt_id": origin["attempt_id"]})
    policy = value["policy"]
    require(isinstance(policy, dict) and set(policy) == {"retention"})
    retention = policy["retention"]
    require(retention == "keep" or type(retention) in (int, float) and math.isfinite(retention) and retention >= 0)
    quota = value["quota"]
    require(isinstance(quota, dict) and set(quota) == {"max_bytes", "used_bytes", "exceeded"})
    require(quota["max_bytes"] is None or type(quota["max_bytes"]) is int and quota["max_bytes"] >= 0)
    require(type(quota["used_bytes"]) is int and quota["used_bytes"] >= 0 and type(quota["exceeded"]) is bool)
    require(isinstance(value["files"], list))
    names = []
    for item in value["files"]:
        require(isinstance(item, dict) and set(item) in ({"path", "size", "sha256"}, {"path", "size", "sha256", "jsonl"}))
        parts = _relative(item["path"])
        require(parts[0] == "evidence" and len(parts) >= 2 and type(item["size"]) is int and item["size"] >= 0)
        require(isinstance(item["sha256"], str) and _HASH.fullmatch(item["sha256"]))
        names.append(item["path"])
        if "jsonl" in item:
            facts = item["jsonl"]
            require(isinstance(facts, dict) and set(facts) == {"newline_count", "tail_bytes", "ends_with_newline", "semantic_validation"})
            require(type(facts["newline_count"]) is int and 0 <= facts["newline_count"] <= item["size"]
                    and type(facts["tail_bytes"]) is int and 0 <= facts["tail_bytes"] <= item["size"]
                    and type(facts["ends_with_newline"]) is bool and facts["semantic_validation"] == "not_performed")
    require(names == sorted(set(names)))


@dataclass(frozen=True)
class SpoolPolicy:
    """Operator retention metadata and a logical-file-byte admission budget.

    max_bytes counts logical sizes throughout the spool at new allocation. It is
    not a filesystem quota and does not interrupt writes or block recovery of an
    oversized existing attempt. Execution output budgets remain runner policy.
    """
    enabled: bool
    root: Path
    max_bytes: int | None = None
    retention: str | int | float = "keep"

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("spool enabled must be boolean")
        object.__setattr__(self, "root", _operator_path(self.root))
        if self.max_bytes is not None and (type(self.max_bytes) is not int or self.max_bytes < 0):
            raise ValueError("spool max_bytes must be a nonnegative integer or null")
        if self.retention != "keep" and (type(self.retention) not in (int, float)
                or not math.isfinite(self.retention) or self.retention < 0):
            raise ValueError("spool retention must be keep or finite nonnegative seconds")

    @classmethod
    def from_profile(cls, profile, engine, default_root):
        if profile is None:
            profile = {}
        if not isinstance(profile, dict) or not isinstance(engine, str):
            raise ValueError("spool policy requires an operator profile and engine")
        value = profile.get("evidence_spool", {})
        if not isinstance(value, dict) or set(value) - {"enabled", "root", "max_bytes", "retention"}:
            raise ValueError("unsupported evidence_spool policy fields")
        return cls(enabled=value.get("enabled", engine.startswith(("acquisition.", "hardware."))),
                   root=value.get("root", default_root), max_bytes=value.get("max_bytes"),
                   retention=value.get("retention", "keep"))


class EvidenceSpool:
    def __init__(self, policy: SpoolPolicy):
        if not isinstance(policy, SpoolPolicy):
            raise ValueError("evidence spool requires a validated policy")
        self.policy = policy
        if policy.enabled:
            _private_directory(policy.root, create=True)

    def _usage(self):
        total = 0
        def walk(descriptor):
            nonlocal total
            for name in os.listdir(descriptor):
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, _DIRECTORY, dir_fd=descriptor)
                    try:
                        walk(child)
                    finally:
                        os.close(child)
                else:
                    # Private links are measured without following them. Only
                    # the explicit evidence inventory requires link-free files.
                    total += info.st_size
        with _directory(self.policy.root) as descriptor:
            walk(descriptor)
        return total

    def allocate(self, attempt, *, server, target_id):
        if not self.policy.enabled:
            raise ValueError("evidence spooling is disabled")
        if not isinstance(attempt, dict) or not isinstance(attempt.get("spec"), dict):
            raise ValueError("spool allocation requires an immutable attempt")
        spec = attempt["spec"]
        if attempt.get("spec_digest") != checksum(spec):
            raise ValueError("attempt specification digest mismatch")
        origin = {"server": _server_origin(server), "target_id": target_id,
                  "run_id": attempt.get("run_id", spec.get("research_identity")),
                  "attempt_id": attempt.get("attempt_id"), "spec_digest": attempt["spec_digest"],
                  "engine": spec.get("definition", {}).get("engine"),
                  "implementation_digest": checksum(spec.get("implementation")),
                  "runtime": deepcopy(spec.get("runtime"))}
        if any(not isinstance(origin[name], str) or not origin[name] for name in
               ("target_id", "run_id", "attempt_id", "engine")):
            raise ValueError("spool origin requires run, attempt, target and engine identities")
        if not isinstance(origin["runtime"], dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                        for k, v in origin["runtime"].items()):
            raise ValueError("spool runtime is declared string-version metadata")
        if any(name.lower() in _PRIVATE_KEYS for name in origin["runtime"]):
            raise ValueError("private fields cannot enter public runtime metadata")
        manifest = {"format": FORMAT, "origin": origin, "classification": "operator_host_evidence",
                    "outcome": {"verification": "unverified", "physical_stop": "unverified"},
                    "cleanup": {"status": "unknown", "private_context_removed": False},
                    "publication": {"status": "unknown"}, "policy": {"retention": self.policy.retention},
                    "quota": {}, "files": []}
        key = _key(origin)
        with _locked(self.policy.root, ".allocation.lock"):
            with _directory(self.policy.root) as descriptor:
                try:
                    os.stat(key, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise FileExistsError("attempt already has persistent evidence; allocation cannot overwrite it")
                used = self._usage()
                for _ in range(8):
                    manifest["quota"] = {"max_bytes": self.policy.max_bytes, "used_bytes": used + len(_encoded(manifest)),
                                         "exceeded": False}
                if self.policy.max_bytes is not None and manifest["quota"]["used_bytes"] > self.policy.max_bytes:
                    raise ValueError("evidence spool allocation exceeds its configured disk budget")
                os.mkdir(key, mode=0o700, dir_fd=descriptor)
                os.fsync(descriptor)
            entry = SpoolAttempt(self, key, origin=origin, create=True)
            entry._write_manifest(manifest)
        return entry

    def open_attempt(self, key):
        entry = SpoolAttempt(self, key)
        entry._read_manifest()
        return entry

    def list_attempts(self):
        if not self.policy.root.exists():
            return []
        rows = []
        with _directory(self.policy.root) as descriptor:
            for key in sorted(os.listdir(descriptor)):
                if _KEY.fullmatch(key):
                    try:
                        entry = self.open_attempt(key)
                        manifest, encoded = entry._read_manifest()
                    except Exception as error:
                        # Allocation or storage can fail before a manifest is
                        # committed. Keep the entry visible without concealing
                        # other recoverable attempts or granting replay rights.
                        rows.append({"key": key, "inspection": {"status": "unreadable",
                            "error_type": type(error).__name__, "error": str(error)}})
                    else:
                        rows.append({"key": key, "manifest": manifest,
                                     "manifest_sha256": hashlib.sha256(encoded).hexdigest()})
        return rows


class SpoolAttempt:
    def __init__(self, spool: EvidenceSpool, key: str, *, origin=None, create=False):
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError("invalid spool attempt key")
        self.spool, self.key = spool, key
        self.path = spool.policy.root / key
        self.execution_root = self.path / "private"
        self.manifest_path = self.path / "manifest.json"
        self._origin = deepcopy(origin)
        _private_directory(self.path)
        _private_directory(self.execution_root, create=create)

    @property
    def _evidence(self):
        return self.execution_root / "work/evidence"

    def _read_manifest(self):
        with _regular(self.path, "manifest.json") as descriptor:
            before = os.fstat(descriptor)
            if before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600:
                raise ValueError("spool manifest must be operator-owned with mode 0600")
            chunks = []
            while block := os.read(descriptor, _CHUNK):
                chunks.append(block)
            if _identity(before) != _identity(os.fstat(descriptor)):
                raise ValueError("spool manifest changed during inspection")
        encoded = b"".join(chunks)
        manifest = json.loads(encoded, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        _validate_manifest(manifest)
        if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT
                or not isinstance(manifest.get("origin"), dict) or _key(manifest["origin"]) != self.key
                or self._origin is not None and manifest["origin"] != self._origin):
            raise ValueError("spool manifest origin or format mismatch")
        return manifest, encoded

    def _write_manifest(self, manifest):
        encoded = _encoded(manifest)
        temporary = ".manifest-" + uuid.uuid4().hex + ".tmp"
        with _directory(self.path) as descriptor:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, "manifest.json", src_dir_fd=descriptor, dst_dir_fd=descriptor)
                os.fsync(descriptor)
            finally:
                try:
                    os.unlink(temporary, dir_fd=descriptor)
                except FileNotFoundError:
                    pass

    def _quota(self, manifest):
        with _regular(self.path, "manifest.json") as descriptor:
            old_size = os.fstat(descriptor).st_size
        used = self.spool._usage() - old_size
        for _ in range(8):
            size = used + len(_encoded(manifest))
            maximum = self.spool.policy.max_bytes
            manifest["quota"] = {"max_bytes": maximum, "used_bytes": size,
                                 "exceeded": maximum is not None and size > maximum}

    def mark_cleanup(self, confirmed):
        if type(confirmed) is not bool:
            raise ValueError("cleanup confirmation must be explicit boolean")
        with _locked(self.path, ".manifest.lock"):
            manifest, _ = self._read_manifest()
            removed = False
            if confirmed:
                with _directory(self.execution_root) as descriptor:
                    try:
                        info = os.stat("context.json", dir_fd=descriptor, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise ValueError("private context must be a regular file, never a symlink or hard link")
                        os.unlink("context.json", dir_fd=descriptor)
                        os.fsync(descriptor)
                        removed = True
            manifest["cleanup"] = {"status": "confirmed" if confirmed else "unknown",
                                   "private_context_removed": removed or manifest["cleanup"]["private_context_removed"]}
            self._quota(manifest)
            self._write_manifest(manifest)

    def record_server_ack(self, summary):
        if (not isinstance(summary, dict) or summary.get("accepted") is not True
                or summary.get("state") not in {"succeeded", "failed", "cancelled"}):
            raise ValueError("server acknowledgement requires an explicitly accepted terminal state")
        with _locked(self.path, ".manifest.lock"):
            manifest, _ = self._read_manifest()
            origin = manifest["origin"]
            if any(name in summary and summary[name] != origin[name] for name in ("run_id", "attempt_id")):
                raise ValueError("server acknowledgement belongs to a different attempt")
            ack = {"accepted": True, "state": summary["state"],
                   "run_id": origin["run_id"], "attempt_id": origin["attempt_id"]}
            if manifest["publication"]["status"] == "server_acknowledged" and manifest["publication"]["ack"] != ack:
                raise ValueError("server acknowledgement cannot replace a different terminal receipt")
            manifest["publication"] = {"status": "server_acknowledged", "server_state": summary["state"], "ack": ack}
            self._quota(manifest)
            self._write_manifest(manifest)

    def snapshot(self):
        with _locked(self.path, ".manifest.lock"):
            manifest, _ = self._read_manifest()
            names = _files(self._evidence)
            inventory = [_inventory_file(self._evidence, name) for name in names]
            if names != _files(self._evidence):
                raise ValueError("evidence inventory changed during snapshot")
            manifest["files"] = inventory
            self._quota(manifest)
            self._write_manifest(manifest)
            encoded = _encoded(manifest)
            return {"manifest": manifest, "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
                    "files": deepcopy(inventory)}

    def inspect(self):
        """Read the last atomic manifest without changing its inventory."""
        manifest,encoded=self._read_manifest()
        return {'manifest':deepcopy(manifest),'manifest_sha256':hashlib.sha256(encoded).hexdigest(),
                'files':deepcopy(manifest['files'])}

    def export(self, destination, *, expected_manifest_digest):
        """Stream an exclusive ZIP; preserve tails and reject changed source bytes."""
        if not isinstance(expected_manifest_digest, str) or not _HASH.fullmatch(expected_manifest_digest):
            raise ValueError("export requires the expected manifest digest")
        destination = _operator_path(destination)
        if destination.is_relative_to(self.path):
            raise ValueError("export destination cannot be inside its evidence attempt")
        with _locked(self.path, ".manifest.lock"):
            manifest, encoded = self._read_manifest()
            if hashlib.sha256(encoded).hexdigest() != expected_manifest_digest:
                raise ValueError("spool manifest changed before export")
            inventory = manifest["files"]
            names = []
            for item in inventory:
                parts = _relative(item["path"])
                if parts[0] != "evidence" or len(parts) < 2:
                    raise ValueError("manifest inventory escapes evidence")
                names.append("/".join(parts[1:]))
            if names != _files(self._evidence):
                raise ValueError("evidence inventory changed since snapshot")
            with _directory(destination.parent) as parent:
                descriptor = os.open(destination.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
                identity = os.fstat(descriptor)
                try:
                    with os.fdopen(descriptor, "w+b") as stream:
                        with zipfile.ZipFile(stream, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                            archive.writestr(self._zip_info("manifest.json"), encoded)
                            for name, item in zip(names, inventory, strict=True):
                                with archive.open(self._zip_info(item["path"]), "w", force_zip64=True) as target:
                                    observed = _inventory_file(self._evidence, name, sink=target)
                                if observed != item:
                                    raise ValueError("evidence checksum or inventory changed during export")
                        if names != _files(self._evidence) or any(
                                _inventory_file(self._evidence, name) != item for name, item in zip(names, inventory, strict=True)):
                            raise ValueError("evidence changed before export completed")
                        stream.flush()
                        os.fsync(stream.fileno())
                        before_hash = os.fstat(stream.fileno())
                        stream.seek(0)
                        digest = hashlib.sha256()
                        while block := stream.read(_CHUNK):
                            digest.update(block)
                        size = stream.tell()
                        if _identity(before_hash) != _identity(os.fstat(stream.fileno())):
                            raise ValueError("export bytes changed during checksum calculation")
                        if hashlib.sha256(self._read_manifest()[1]).hexdigest() != expected_manifest_digest:
                            raise ValueError("spool manifest changed while exporting")
                        stored = os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
                        if (stored.st_dev, stored.st_ino) != (identity.st_dev, identity.st_ino):
                            raise ValueError("export destination changed during publication")
                    os.fsync(parent)
                except BaseException:
                    try:
                        stored = os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
                        if (stored.st_dev, stored.st_ino) == (identity.st_dev, identity.st_ino):
                            os.unlink(destination.name, dir_fd=parent)
                            os.fsync(parent)
                    except FileNotFoundError:
                        pass
                    raise
        return {"path": str(destination), "size": size, "sha256": digest.hexdigest(),
                "manifest_sha256": expected_manifest_digest}

    @staticmethod
    def _zip_info(name):
        _relative(name)
        info = zipfile.ZipInfo(name)
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        return info
