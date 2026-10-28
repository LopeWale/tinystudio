#!/usr/bin/env python3
"""Validate the packaged React asset graph without importing the app or reading env files.

Usage: python scripts/check_react_assets.py [--dist tinystudio/ui/dist] [--wheel FILE]
This verifies references and prohibited file classes, not arbitrary secret detection.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

# Load only the stdlib reference reader: importing tinystudio would read dotenv.
_reference_spec = importlib.util.spec_from_file_location("_frontend_reference_reader", Path(__file__).resolve().parents[1] / "tinystudio/frontend_references.py")
_reference_reader = importlib.util.module_from_spec(_reference_spec)
_reference_spec.loader.exec_module(_reference_reader)
reference_literals = _reference_reader.reference_literals


class AssetError(ValueError):
    """A built or packaged frontend violates its distributable-asset contract."""


@dataclass(frozen=True)
class AssetReport:
    files: tuple[str, ...]
    referenced: tuple[str, ...]
    entries: tuple[str, ...]


class _Index(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
        self.modules: list[str] = []
        self.root = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        item = dict(attrs)
        self.root |= item.get("id") == "root"
        if tag == "script":
            if item.get("src"):
                self.references.append(item["src"])
                if item.get("type") == "module":
                    self.modules.append(item["src"])
            else:
                raise AssetError("Production index must not contain inline executable scripts")
        elif tag == "link" and item.get("href") and item.get("rel") in {
            "stylesheet", "modulepreload", "preload", "icon", "apple-touch-icon",
        }:
            self.references.append(item["href"])
        elif tag in {"img", "source", "video", "audio"} and item.get("src"):
            self.references.append(item["src"])


# Vite's manifest is authoritative for split/dynamic chunks. JavaScript values
# come from the lexical reader; CSS URLs retain their own reference grammar.
STATIC_ASSET = re.compile(r'''(?:/ui/|\./|\.\./|assets/)[^"'<>\n]+?\.(?:js|mjs|css|woff2?|ttf|otf|png|jpe?g|gif|svg|webp|avif|ico|wasm)(?:\?[^"']*)?''')
CSS_URL = re.compile(r'''url\(\s*(?:["']([^"']+)["']|([^\s)]+))\s*\)''')
CSS_IMPORT = re.compile(r'''@import\s+["']([^"']+)["']''')
HASHED_JS = re.compile(r"^assets/[^/]+-[A-Za-z0-9_-]{6,}\.(?:m?js)$")
FORBIDDEN_NAMES = {".npmrc", ".pypirc", ".netrc", "credentials", "credentials.json", "service-account.json", "id_rsa", "id_ed25519", "token", "token.txt"}
FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db", ".map"}


def _allowed_filename(relative: str) -> None:
    parts = PurePosixPath(relative).parts
    for part in parts:
        if part.lower().startswith(".env") or part.lower() in FORBIDDEN_NAMES:
            raise AssetError(f"Prohibited credential filename in frontend: {relative}")
    if PurePosixPath(relative).suffix.lower() in FORBIDDEN_SUFFIXES:
        raise AssetError(f"Prohibited credential/database/source-map file: {relative}")


def _reference(reference: str, owner: str, *, module: bool = False) -> str | None:
    if not reference or reference.startswith("#"):
        return None
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme == "data" and not module:
            return None
        raise AssetError(f"External runtime dependency in {owner}: {reference}")
    path = unquote(parsed.path)
    if "\\" in path or "\x00" in path:
        raise AssetError(f"Invalid asset path in {owner}: {reference}")
    if path.startswith("/ui/"):
        relative = path.removeprefix("/ui/")
    elif path.startswith("/"):
        raise AssetError(f"Asset escapes /ui/ serving base in {owner}: {reference}")
    elif path.startswith("assets/"):
        relative = path
    else:
        if module and not path.startswith("."):
            raise AssetError(f"Unbundled module import in {owner}: {reference}")
        relative = (PurePosixPath(owner).parent / path).as_posix()
    # Parent traversal is forbidden even when normalizing it would land in dist;
    # Vite's configured emitted graph has no need for upward references.
    if ".." in PurePosixPath(relative).parts or relative.startswith("/"):
        raise AssetError(f"Traversal in asset reference from {owner}: {reference}")
    _allowed_filename(relative)
    return relative


def inspect_dist(dist: Path) -> AssetReport:
    if dist.is_symlink():
        raise AssetError("React dist directory must not be a symlink")
    dist = dist.resolve()
    if not dist.is_dir():
        raise AssetError(f"React production build missing: {dist}; run npm --prefix frontend run build")
    files: dict[str, Path] = {}
    for path in dist.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(dist):
            raise AssetError(f"Asset symlink escapes dist: {path.relative_to(dist)}")
        if not path.is_file():
            continue
        relative = path.relative_to(dist).as_posix()
        _allowed_filename(relative)
        if not path.resolve().is_relative_to(dist):
            raise AssetError(f"Asset symlink escapes dist: {relative}")
        files[relative] = path
    for required in ("index.html", ".vite/manifest.json"):
        if required not in files:
            raise AssetError(f"React production build missing {required}")
    index_text = files["index.html"].read_text(encoding="utf-8")
    if "/src/" in index_text or "@vite/client" in index_text:
        raise AssetError("Production index still points to Vite development source")
    index = _Index()
    index.feed(index_text)
    if not index.root or not index.modules:
        raise AssetError("Production index requires the React root and a module entry")
    referenced: set[str] = set()

    def require(reference: str, owner: str, *, module: bool = False) -> str | None:
        relative = _reference(reference, owner, module=module)
        if relative is not None:
            if relative not in files:
                raise AssetError(f"Missing asset referenced by {owner}: {relative}")
            referenced.add(relative)
        return relative

    entries = tuple(require(url, "index.html") for url in index.modules)
    if any(not entry or not HASHED_JS.fullmatch(entry) for entry in entries):
        raise AssetError("Production entry must be a hashed assets/*.js Vite bundle")
    for reference in index.references:
        require(reference, "index.html")
    try:
        manifest = json.loads(files[".vite/manifest.json"].read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise AssetError(f"Invalid Vite manifest: {error}") from error
    if not isinstance(manifest, dict) or not manifest:
        raise AssetError("Vite manifest must contain entry records")
    manifest_entries = set()
    for key, record in manifest.items():
        if not isinstance(record, dict) or not isinstance(record.get("file"), str):
            raise AssetError(f"Invalid Vite manifest record: {key}")
        emitted = require(record["file"], ".vite-manifest-root")
        if record.get("isEntry"):
            manifest_entries.add(emitted)
        for field in ("css", "assets"):
            for asset in record.get(field, []):
                if not isinstance(asset, str):
                    raise AssetError(f"Invalid {field} entry in manifest record {key}")
                require(asset, ".vite-manifest-root")
        for field in ("imports", "dynamicImports"):
            for dependency in record.get(field, []):
                if dependency not in manifest:
                    raise AssetError(f"Missing manifest {field} record {dependency} from {key}")
    if not set(entries).issubset(manifest_entries):
        raise AssetError("HTML module entry does not match Vite manifest entry")
    for relative, path in files.items():
        if path.suffix not in {".js", ".mjs", ".css"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "sourceMappingURL=" in text:
            raise AssetError(f"Production source map reference is prohibited: {relative}")
        if "import.meta.env" in text or re.search(r"process\.env\.(?:OPENAI_API_KEY|ANTHROPIC_API_KEY|HF_TOKEN|R2_SECRET_ACCESS_KEY)", text):
            raise AssetError(f"Unresolved environment credential access in bundle: {relative}")
        if path.suffix in {".js", ".mjs"}:
            try:
                imports, literals = reference_literals(text)
            except ValueError as error:
                raise AssetError(f"Invalid executable module reference in {relative}: {error}") from error
            for reference in imports:
                require(reference, relative, module=True)
            for reference in literals:
                if STATIC_ASSET.fullmatch(reference):
                    require(reference, relative)
        else:
            for quoted, bare in CSS_URL.findall(text):
                require(quoted or bare, relative)
            for reference in CSS_IMPORT.findall(text):
                require(reference, relative)
    orphaned = {name for name in files if name.endswith((".js", ".mjs", ".css"))} - referenced
    if orphaned:
        raise AssetError(f"Emitted modules/styles are absent from manifest and references: {sorted(orphaned)}")
    if not any("createRoot" in files[entry].read_text(encoding="utf-8") for entry in entries):
        # Split vendor runtimes may hold createRoot rather than the entry itself.
        if not any("createRoot" in path.read_text(encoding="utf-8") for name, path in files.items() if name in referenced and name.endswith((".js", ".mjs"))):
            raise AssetError("React createRoot runtime is absent from the production graph")
    return AssetReport(tuple(sorted(files)), tuple(sorted(referenced)), tuple(sorted(entries)))


def inspect_wheel(dist: Path, wheel: Path) -> AssetReport:
    report = inspect_dist(dist)
    prefix = "tinystudio/ui/dist/"
    with ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise AssetError("Wheel contains duplicate archive entries")
        packaged = {name.removeprefix(prefix) for name in names if name.startswith(prefix) and not name.endswith("/")}
        if packaged != set(report.files):
            raise AssetError(f"Wheel React asset set differs: missing={sorted(set(report.files)-packaged)}, extra={sorted(packaged-set(report.files))}")
        for relative in report.files:
            if archive.read(prefix + relative) != (dist / relative).read_bytes():
                raise AssetError(f"Wheel asset bytes differ from verified build: {relative}")
        for name in names:
            if name.startswith(prefix):
                _allowed_filename(name)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path(__file__).resolve().parents[1] / "tinystudio/ui/dist")
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    try:
        report = inspect_wheel(args.dist, args.wheel) if args.wheel else inspect_dist(args.dist)
    except (AssetError, OSError) as error:
        parser.exit(1, f"React asset verification failed: {error}\n")
    print(f"Verified {len(report.files)} React build files, {len(report.referenced)} references, {len(report.entries)} entry bundles" + (f"; wheel bytes match {args.wheel.name}" if args.wheel else ""))


if __name__ == "__main__":
    main()
