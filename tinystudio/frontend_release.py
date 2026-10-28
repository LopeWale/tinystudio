"""Verified, content-addressed frontend snapshots; no build tool or DB dependency.

A process serves one snapshot. Cached releases are retained without pruning so
older tabs may request their hashed assets after a restart. This is filesystem
isolation from subsequent builds, not protection from an administrator editing
the release cache. Cached bytes are verified on every application construction.
"""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from urllib.parse import unquote, urlsplit

from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles

# Load only the stdlib reference reader: importing tinystudio would read dotenv.
_reference_spec = importlib.util.spec_from_file_location("_frontend_reference_reader", Path(__file__).with_name("frontend_references.py"))
_reference_reader = importlib.util.module_from_spec(_reference_spec)
_reference_spec.loader.exec_module(_reference_reader)
module_references = _reference_reader.module_references

log = logging.getLogger("tinystudio")
MARKER = ".release.json"
DIGEST = re.compile(r"[a-f0-9]{64}")
HASHED_ASSET = re.compile(r"assets/[^/]+-[A-Za-z0-9_-]{6,}\.(?:js|mjs|css|woff2?|ttf|otf|png|jpe?g|gif|svg|webp|avif|ico|wasm)")
CSS_URL = re.compile(r'''url\(\s*(?:["']([^"']+)["']|([^\s)]+))\s*\)''')
CSS_IMPORT = re.compile(r'''@import\s+["']([^"']+)["']''')


class FrontendReleaseError(ValueError):
    """A source or cached release cannot be safely published/served."""


@dataclass(frozen=True)
class FrontendRelease:
    directory: Path
    digest: str
    files: tuple[tuple[str, str], ...]


class _Index(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references: list[str] = []
        self.modules: list[str] = []
        self.root = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.root |= attrs.get("id") == "root"
        if tag == "script":
            if not attrs.get("src"):
                raise FrontendReleaseError("Frontend index contains an inline script")
            self.references.append(attrs["src"])
            if attrs.get("type") == "module":
                self.modules.append(attrs["src"])
        elif tag == "link" and attrs.get("href") and attrs.get("rel") in {"stylesheet", "modulepreload", "preload", "icon"}:
            self.references.append(attrs["href"])


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise FrontendReleaseError(f"Invalid frontend asset path: {value}")
    if any(part.lower().startswith(".env") or part.lower() in {".npmrc", ".netrc", "credentials", "credentials.json", "service-account.json", "id_rsa", "id_ed25519"} for part in path.parts) or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db", ".map"}:
        raise FrontendReleaseError(f"Prohibited frontend asset filename: {value}")
    return path.as_posix()


def _capture(root: Path, *, cached: bool = False) -> tuple[dict[str, bytes], dict[str, tuple[int, int, int, int]]]:
    if root.is_symlink() or not root.is_dir():
        raise FrontendReleaseError(f"Frontend directory missing or symlinked: {root}")
    files, stamps = {}, {}
    for directory, subdirs, names in os.walk(root, followlinks=False):
        for name in subdirs:
            if (Path(directory) / name).is_symlink():
                raise FrontendReleaseError(f"Frontend contains a symlink: {name}")
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise FrontendReleaseError(f"Frontend contains a symlink or nonregular file: {relative}")
            if cached and relative == MARKER:
                continue
            if relative == MARKER:
                raise FrontendReleaseError("Source frontend contains a reserved release marker")
            _safe_relative(relative)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                data = stream.read()
                after = os.fstat(stream.fileno())
            final = path.lstat()
            stamp = lambda item: (item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
            if stamp(before) != stamp(after) or stamp(after) != stamp(final) or not stat.S_ISREG(final.st_mode):
                raise FrontendReleaseError(f"Frontend changed during capture: {relative}")
            files[relative], stamps[relative] = data, stamp(final)
    return files, stamps


def _hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}


def _digest(hashes: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _json(data: bytes, label: str):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise FrontendReleaseError(f"Duplicate JSON field in {label}: {key}")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as error:
        raise FrontendReleaseError(f"Invalid {label}: {error}") from error


def _verify_graph(files: dict[str, bytes]) -> None:
    if "index.html" not in files or ".vite/manifest.json" not in files:
        raise FrontendReleaseError("Frontend build missing index.html or Vite manifest")
    def reference(value: str, owner: str, module: bool = False):
        parsed = urlsplit(value)
        if parsed.scheme == "data" and not module:
            return None
        if parsed.scheme or parsed.netloc:
            raise FrontendReleaseError(f"External runtime asset in {owner}: {value}")
        path = unquote(parsed.path)
        if not path or path.startswith("#"):
            return None
        if path.startswith("/ui/"):
            relative = path[4:]
        elif path.startswith("/"):
            raise FrontendReleaseError(f"Asset escapes /ui/: {value}")
        elif path.startswith("assets/"):
            relative = path
        else:
            if module and not path.startswith("."):
                raise FrontendReleaseError(f"Unbundled module in {owner}: {value}")
            relative = (PurePosixPath(owner).parent / path).as_posix()
        relative = _safe_relative(relative)
        if relative not in files:
            raise FrontendReleaseError(f"Missing frontend asset referenced by {owner}: {relative}")
        return relative
    try:
        index = _Index()
        index.feed(files["index.html"].decode("utf-8"))
        if not index.root or not index.modules:
            raise FrontendReleaseError("Frontend index requires a React root and module entry")
        if b"/src/" in files["index.html"] or b"@vite/client" in files["index.html"]:
            raise FrontendReleaseError("Frontend still references development source")
        entries = {reference(value, "index.html") for value in index.modules}
        if not all(HASHED_ASSET.fullmatch(entry or "") and entry.endswith((".js", ".mjs")) for entry in entries):
            raise FrontendReleaseError("Frontend entry must use hashed JavaScript assets")
        for value in index.references:
            reference(value, "index.html")
        manifest = _json(files[".vite/manifest.json"], "Vite manifest")
        if not isinstance(manifest, dict) or not manifest:
            raise FrontendReleaseError("Frontend manifest has no records")
        manifest_entries = set()
        for key, record in manifest.items():
            if not isinstance(record, dict) or not isinstance(record.get("file"), str):
                raise FrontendReleaseError(f"Invalid manifest record: {key}")
            emitted = reference(record["file"], ".manifest-root")
            if record.get("isEntry"):
                manifest_entries.add(emitted)
            for field in ("css", "assets"):
                if not isinstance(record.get(field, []), list):
                    raise FrontendReleaseError(f"Invalid manifest {field}: {key}")
                for item in record.get(field, []):
                    if not isinstance(item, str):
                        raise FrontendReleaseError(f"Invalid manifest asset: {key}")
                    reference(item, ".manifest-root")
            for field in ("imports", "dynamicImports"):
                for dependency in record.get(field, []):
                    if dependency not in manifest:
                        raise FrontendReleaseError(f"Missing manifest dependency: {dependency}")
        if not entries <= manifest_entries:
            raise FrontendReleaseError("Frontend HTML and manifest entries disagree")
        for name, data in files.items():
            if name.endswith((".js", ".mjs", ".css")):
                text = data.decode("utf-8")
                if "sourceMappingURL=" in text or "import.meta.env" in text:
                    raise FrontendReleaseError(f"Unpackaged development content in frontend: {name}")
                if name.endswith(".css"):
                    for quoted, bare in CSS_URL.findall(text):
                        reference(quoted or bare, name)
                    for value in CSS_IMPORT.findall(text):
                        reference(value, name)
                else:
                    try:
                        imports = module_references(text)
                    except ValueError as error:
                        raise FrontendReleaseError(f"Invalid executable module reference in {name}: {error}") from error
                    for value in imports:
                        reference(value, name, module=True)
        if not any(b"createRoot" in data for name, data in files.items() if name.endswith((".js", ".mjs"))):
            raise FrontendReleaseError("Frontend React runtime missing")
    except (UnicodeError, TypeError, KeyError) as error:
        raise FrontendReleaseError(f"Corrupt frontend graph: {error}") from error


def _verify_cached(directory: Path, digest: str) -> FrontendRelease:
    if directory.is_symlink() or not directory.is_dir():
        raise FrontendReleaseError(f"Cached frontend is missing or symlinked: {digest}")
    marker = directory / MARKER
    if marker.is_symlink() or not marker.is_file():
        raise FrontendReleaseError(f"Cached frontend marker missing or symlinked: {digest}")
    record = _json(marker.read_bytes(), "frontend release marker")
    files, _ = _capture(directory, cached=True)
    hashes = _hashes(files)
    if not isinstance(record, dict) or record.get("digest") != digest or record.get("files") != hashes or _digest(hashes) != digest:
        raise FrontendReleaseError(f"Cached frontend integrity mismatch: {digest}")
    _verify_graph(files)
    return FrontendRelease(directory, digest, tuple(hashes.items()))


def _write_staging(directory: Path, files: dict[str, bytes]) -> None:
    for relative, data in files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def snapshot_frontend(source: Path, data_directory: Path) -> FrontendRelease:
    """Capture a complete build or fail without changing a published release.

    Call once per application construction, before accepting UI requests. A
    rebuild requires a new application/process; this process keeps its snapshot.
    No existing directory is deleted or repaired, including corrupt releases.
    """
    source = source.absolute()
    files, stamps = _capture(source)
    _verify_graph(files)
    hashes = _hashes(files)
    digest = _digest(hashes)
    cache = data_directory / "frontend-releases"
    if cache.is_symlink():
        raise FrontendReleaseError("Frontend release cache must not be a symlink")
    cache.mkdir(parents=True, exist_ok=True)
    lock_path = cache / ".publish.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        destination = cache / digest
        if destination.exists() or destination.is_symlink():
            release = _verify_cached(destination, digest)
            final, final_stamps = _capture(source)
            if final != files or final_stamps != stamps:
                raise FrontendReleaseError("Frontend changed while reusing its cached release")
            return release
        # A hashed URL must always identify the same bytes, including old tabs.
        for previous in retained_frontends(cache):
            for name, old_hash in previous.files:
                if HASHED_ASSET.fullmatch(name) and name in hashes and hashes[name] != old_hash:
                    raise FrontendReleaseError(f"Hashed frontend asset changed bytes without changing its URL: {name}")
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=cache))
        try:
            _write_staging(staging, files)
            marker = json.dumps({"digest": digest, "files": hashes}, sort_keys=True).encode()
            _write_staging(staging, {MARKER: marker})
            release = _verify_cached(staging, digest)
            final, final_stamps = _capture(source)
            if final != files or final_stamps != stamps:
                raise FrontendReleaseError("Frontend changed while copying; release not published")
            # The publication lock serializes cooperating application startups.
            # Never replace an existing directory, including an incomplete one.
            if destination.exists() or destination.is_symlink():
                return _verify_cached(destination, digest)
            os.rename(staging, destination)
            return FrontendRelease(destination, release.digest, release.files)
        finally:
            if staging.exists():
                shutil.rmtree(staging)  # This invocation's unique staging only.


def retained_frontends(cache: Path) -> tuple[FrontendRelease, ...]:
    """Return only complete, integrity-checked releases; skip foreign/staging paths."""
    if not cache.is_dir() or cache.is_symlink():
        return ()
    releases = []
    for directory in sorted(cache.iterdir()):
        if not DIGEST.fullmatch(directory.name):
            continue
        try:
            releases.append(_verify_cached(directory, directory.name))
        except (FrontendReleaseError, OSError) as error:
            log.warning("Ignoring invalid retained frontend %s: %s", directory.name, error)
    return tuple(releases)


class FrontendReleaseFiles(StaticFiles):
    """Current release, with fallback only for prior content-hashed asset URLs."""
    def __init__(self, current: FrontendRelease | None, retained: tuple[FrontendRelease, ...] = ()):
        super().__init__(directory=str(current.directory) if current else None, check_dir=False)
        self.current = current
        self.prior = tuple(StaticFiles(directory=str(release.directory)) for release in retained if current is None or release.digest != current.digest)

    async def get_response(self, path: str, scope):
        if self.current is None:
            if HASHED_ASSET.fullmatch(path):
                for previous in self.prior:
                    try:
                        response = await previous.get_response(path, scope)
                        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                        return response
                    except HTTPException as error:
                        if error.status_code != 404:
                            raise
            return PlainTextResponse("Frontend build required; restart after building.", status_code=503)
        if path == MARKER or path.startswith(".release"):
            raise HTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or not HASHED_ASSET.fullmatch(path):
                raise
            for previous in self.prior:
                try:
                    response = await previous.get_response(path, scope)
                    break
                except HTTPException as prior_error:
                    if prior_error.status_code != 404:
                        raise
            else:
                raise error
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if HASHED_ASSET.fullmatch(path) else "no-cache"
        return response
