"""Content-addressed serving tests: temp directories, no app/env/DB imports.

Run with --noconftest to avoid the unrelated database fixture bootstrap.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

MODULE = Path(__file__).resolve().parents[1] / "tinystudio/frontend_release.py"
spec = importlib.util.spec_from_file_location("isolated_frontend_release", MODULE)
release = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = release
spec.loader.exec_module(release)


def build(root: Path, version="11111111") -> Path:
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / ".vite").mkdir(exist_ok=True)
    entry, detail, css = (f"assets/{kind}-{version}.{extension}" for kind, extension in [("index", "js"), ("detail", "js"), ("style", "css")])
    (root / "index.html").write_text(f'<div id="root"></div><script type="module" src="/ui/{entry}"></script><link rel="stylesheet" href="/ui/{css}">')
    (root / entry).write_text(f'const createRoot=()=>42;createRoot();import("./detail-{version}.js");')
    (root / detail).write_text(f'export const fixture="version-{version}";')
    (root / css).write_text('body{color:inherit}')
    (root / ".vite/manifest.json").write_text(json.dumps({"index.html": {"file": entry, "isEntry": True, "css": [css], "dynamicImports": ["detail.tsx"]}, "detail.tsx": {"file": detail, "isDynamicEntry": True}}))
    return root


def client_for(current, prior=()):
    return TestClient(Starlette(routes=[Mount("/ui", app=release.FrontendReleaseFiles(current, prior))]))


def test_snapshot_is_independent_from_rebuild_and_reuses_exact_cached_bytes(tmp_path):
    source = build(tmp_path / "dist")
    data = tmp_path / "data"
    first = release.snapshot_frontend(source, data)
    marker_time = (first.directory / release.MARKER).stat().st_mtime_ns
    assert first.directory.parent == data / "frontend-releases"
    assert first.directory.name == first.digest
    assert len(first.digest) == 64
    assert release.snapshot_frontend(source, data) == first
    assert (first.directory / release.MARKER).stat().st_mtime_ns == marker_time
    client = client_for(first)
    entry_before = client.get("/ui/assets/index-11111111.js")
    index_before = client.get("/ui/index.html")
    shutil.rmtree(source)
    assert client.get("/ui/index.html").content == index_before.content
    assert client.get("/ui/assets/index-11111111.js").content == entry_before.content
    assert entry_before.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert index_before.headers["cache-control"] == "no-cache"
    assert client.get("/ui/.release.json").status_code == 404


def test_restart_retains_old_split_assets_but_never_old_index_or_manifest(tmp_path):
    source = build(tmp_path / "dist")
    data = tmp_path / "data"
    old = release.snapshot_frontend(source, data)
    old_index = (old.directory / "index.html").read_bytes()
    shutil.rmtree(source)
    build(source, "22222222")
    current = release.snapshot_frontend(source, data)
    cached = release.retained_frontends(data / "frontend-releases")
    assert {item.digest for item in cached} == {old.digest, current.digest}
    client = client_for(current, cached)
    assert client.get("/ui/assets/detail-11111111.js").text == 'export const fixture="version-11111111";'
    assert client.get("/ui/assets/detail-22222222.js").text == 'export const fixture="version-22222222";'
    assert client.get("/ui/index.html").content != old_index
    assert "22222222" in client.get("/ui/.vite/manifest.json").text
    assert old.directory.is_dir(), "Snapshots are retained; this helper never prunes prior releases"
    (old.directory / "foreign.txt").write_text("do not expose via fallback")
    assert client.get("/ui/foreign.txt").status_code == 404


@pytest.mark.parametrize("missing", ["index.html", ".vite/manifest.json", "assets/detail-11111111.js", "assets/style-11111111.css"])
def test_incomplete_build_is_not_published(tmp_path, missing):
    source = build(tmp_path / "dist")
    (source / missing).unlink()
    with pytest.raises(release.FrontendReleaseError, match="missing|Missing"):
        release.snapshot_frontend(source, tmp_path / "data")
    assert not (tmp_path / "data/frontend-releases").exists()


@pytest.mark.parametrize("kind", ["root", "file", "directory"])
def test_any_source_symlink_is_rejected_even_inside_tree(tmp_path, kind):
    source = build(tmp_path / "dist")
    if kind == "root":
        alias = tmp_path / "linked-dist"
        alias.symlink_to(source, target_is_directory=True)
        source = alias
    elif kind == "file":
        (source / "assets/alias.js").symlink_to(source / "assets/detail-11111111.js")
    else:
        (source / "linked-assets").symlink_to(source / "assets", target_is_directory=True)
    with pytest.raises(release.FrontendReleaseError, match="symlink"):
        release.snapshot_frontend(source, tmp_path / "data")


def test_invalid_manifest_and_traversing_runtime_dependency_are_rejected(tmp_path):
    source = build(tmp_path / "dist")
    manifest = source / ".vite/manifest.json"
    manifest.write_text('{"bad":')
    with pytest.raises(release.FrontendReleaseError, match="Invalid Vite manifest"):
        release.snapshot_frontend(source, tmp_path / "data")
    build(source)
    (source / "assets/index-11111111.js").write_text('const createRoot=()=>{};import("../private.js")')
    with pytest.raises(release.FrontendReleaseError, match="Invalid frontend asset path"):
        release.snapshot_frontend(source, tmp_path / "data")


def test_source_change_during_copy_never_publishes_and_preserves_foreign_staging(tmp_path, monkeypatch):
    source = build(tmp_path / "dist")
    data = tmp_path / "data"
    cache = data / "frontend-releases"
    cache.mkdir(parents=True)
    foreign = cache / ".staging-foreign"
    foreign.mkdir()
    (foreign / "owned-by-someone-else").write_text("preserve")
    original = release._write_staging
    changed = False
    def changing_copy(directory, files):
        nonlocal changed
        original(directory, files)
        if not changed:
            changed = True
            (source / "assets/detail-11111111.js").write_text('export const changed=true;')
    monkeypatch.setattr(release, "_write_staging", changing_copy)
    with pytest.raises(release.FrontendReleaseError, match="changed while copying"):
        release.snapshot_frontend(source, data)
    assert [path.name for path in cache.iterdir() if path.is_dir()] == [".staging-foreign"]
    assert (foreign / "owned-by-someone-else").read_text() == "preserve"


def test_corrupt_or_symlinked_cache_is_rejected_without_repair(tmp_path):
    source = build(tmp_path / "dist")
    data = tmp_path / "data"
    cached = release.snapshot_frontend(source, data)
    asset = cached.directory / "assets/detail-11111111.js"
    asset.write_text("corrupted")
    with pytest.raises(release.FrontendReleaseError, match="integrity mismatch"):
        release.snapshot_frontend(source, data)
    assert asset.read_text() == "corrupted", "Never rewrite or repair an existing release"
    assert release.retained_frontends(data / "frontend-releases") == ()
    asset.unlink()
    asset.symlink_to(source / "assets/detail-11111111.js")
    with pytest.raises(release.FrontendReleaseError, match="symlink"):
        release.snapshot_frontend(source, data)


def test_cache_root_symlink_and_same_hashed_url_with_different_bytes_are_rejected(tmp_path):
    source = build(tmp_path / "dist")
    data = tmp_path / "data"
    old = release.snapshot_frontend(source, data)
    (source / "assets/detail-11111111.js").write_text('export const changed=true;')
    with pytest.raises(release.FrontendReleaseError, match="without changing its URL"):
        release.snapshot_frontend(source, data)
    assert old.directory.is_dir()
    linked_data = tmp_path / "linked-data"
    linked_data.mkdir()
    (linked_data / "frontend-releases").symlink_to(data / "frontend-releases", target_is_directory=True)
    with pytest.raises(release.FrontendReleaseError, match="must not be a symlink"):
        release.snapshot_frontend(source, linked_data)


def test_unbuilt_frontend_returns_503_without_reading_mutable_source():
    client = client_for(None)
    assert client.get("/ui/index.html").status_code == 503
    assert client.get("/ui/assets/arbitrary-12345678.js").status_code == 503


def test_prior_hashed_assets_remain_available_even_when_restart_has_no_complete_current_build(tmp_path):
    source = build(tmp_path / "dist")
    data = tmp_path / "data"
    old = release.snapshot_frontend(source, data)
    client = client_for(None, release.retained_frontends(data / "frontend-releases"))
    assert client.get("/ui/index.html").status_code == 503
    assert client.get("/ui/.vite/manifest.json").status_code == 503
    response = client.get("/ui/assets/detail-11111111.js")
    assert response.status_code == 200
    assert response.content == (old.directory / "assets/detail-11111111.js").read_bytes()


@pytest.mark.parametrize("kind", ["file", "incomplete-directory", "symlink"])
def test_existing_foreign_destination_is_never_replaced_or_cleaned(tmp_path, kind):
    source = build(tmp_path / "dist")
    data = tmp_path / "data"
    files, _ = release._capture(source)
    digest = release._digest(release._hashes(files))
    cache = data / "frontend-releases"
    cache.mkdir(parents=True)
    destination = cache / digest
    if kind == "file":
        destination.write_text("foreign")
    elif kind == "incomplete-directory":
        destination.mkdir()
        (destination / "foreign.txt").write_text("foreign")
    else:
        destination.symlink_to(source, target_is_directory=True)
    with pytest.raises(release.FrontendReleaseError, match="missing|symlink"):
        release.snapshot_frontend(source, data)
    if kind == "file":
        assert destination.read_text() == "foreign"
    elif kind == "incomplete-directory":
        assert (destination / "foreign.txt").read_text() == "foreign"
    else:
        assert destination.is_symlink()


def test_concurrent_startups_publish_only_one_complete_snapshot(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    source = build(tmp_path / "dist")
    data = tmp_path / "data"
    with ThreadPoolExecutor(max_workers=3) as executor:
        snapshots = list(executor.map(lambda _: release.snapshot_frontend(source, data), range(3)))
    assert snapshots[0] == snapshots[1] == snapshots[2]
    directories = [path for path in (data / "frontend-releases").iterdir() if path.is_dir()]
    assert directories == [snapshots[0].directory]
    assert release._verify_cached(snapshots[0].directory, snapshots[0].digest) == snapshots[0]


@pytest.mark.parametrize("text", [
    "const diagnostic=(e='react')=>`Please import '@xyflow/${e}/dist/style.css' or base.css.`;",
    '''const diagnostic="Please import 'some-package' to continue";''',
    '''const diagnostic="Please import './missing-12345678.js' to continue";''',
    '''/* import "./missing-12345678.js"; */''',
    '''// import "https://outside.invalid/module.js"\n/* export * from "outside-package" */''',
    '''const diagnostic=`outer ${`inner import 'outside-package'`}`;''',
    '''const pattern=/import\\s+"outside-package"/;''',
    '''const object={import(value){return value}};object.import("outside-package");''',
    '''const object={export:"outside-package"};object?.import("outside-package");const url=import.meta.url;''',
    '''if (ready) /import "outside-package"/.test(text);''',
    '''async function example() {} /import "outside-package"/.test(text);''',
    '''const diagnostic=`outer ${/import "outside-package"/.test(text)}`;''',
])
def test_snapshot_checks_executable_imports_not_diagnostic_prose(tmp_path, text):
    source = build(tmp_path / "dist")
    with (source / "assets/index-11111111.js").open("a") as output:
        output.write("\n"+text)
    assert release.snapshot_frontend(source, tmp_path / "data").directory.is_dir()


@pytest.mark.parametrize("text", [
    '''import "outside-package";''',
    '''export * from "https://outside.invalid/module.js";''',
    '''import/*gap*/("outside-package");''',
    '''const diagnostic=`outer ${import("outside-package")}`;''',
    '''const diagnostic=`outer ${`inner ${import("outside-package")}`}`;''',
    '''import(`outside-package`);''',
    '''import(`https://${host}/module.js`);''',
    r'''import("\x68ttps://outside.invalid/module.js");''',
    '''import("../private.js");''',
    '''export/*gap*/{value as other}/*gap*/from/*gap*/"outside-package";''',
    '''import value from "outside-package" with {type:"json"};''',
    '''const value=call() / import("outside-package") / 2;''',
    '''const value={} / import("outside-package") / 2;''',
    '''const value=function(){} / import("outside-package") / 2;''',
    '''const value=class {} / import("outside-package") / 2;''',
    '''const value=!{} / import("outside-package") / 2;''',
    '''const value=object.return / import("outside-package") / 2;''',
    '''const value=object.if(ready) / import("outside-package") / 2;''',
    '''const value=1 % {} / import("outside-package") / 2;''',
    '''export{value as in}from"outside-package";''',
    '''import{in as value}from"outside-package";''',
    '''import(("https://outside.invalid/module.js"));''',
    '''import((/*comment*/("../private.js")));''',
    '''import(((`https://${host}/module.js`)));''',
    '''import((("https://outside.invalid/")+name));''',
])
def test_snapshot_rejects_executable_external_and_escaping_imports(tmp_path, text):
    source = build(tmp_path / "dist")
    with (source / "assets/index-11111111.js").open("a") as output:
        output.write("\n"+text)
    with pytest.raises(release.FrontendReleaseError):
        release.snapshot_frontend(source, tmp_path / "data")


def test_parenthesized_existing_module_still_validates(tmp_path):
    source = build(tmp_path / "dist")
    with (source / "assets/index-11111111.js").open("a") as output:
        output.write('\nimport((/*comment*/(`./detail-11111111.js`)));')
    assert release.snapshot_frontend(source, tmp_path / "data").directory.is_dir()
