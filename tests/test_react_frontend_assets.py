"""React release checks. Run with --noconftest: no application, env-file or DB imports."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_react_assets", ROOT / "scripts/check_react_assets.py")
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)


@pytest.fixture
def built(tmp_path):
    """Independent tiny Vite-shaped graph with static, dynamic, CSS and font edges."""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / ".vite").mkdir()
    (root / "index.html").write_text('<div id="root"></div><script type="module" src="/ui/assets/index-12345678.js"></script><link rel="stylesheet" href="/ui/assets/style-12345678.css">')
    (root / "assets/index-12345678.js").write_text('import{createRoot}from"./vendor-12345678.js";createRoot(document.getElementById("root"));import("./detail-12345678.js");')
    (root / "assets/vendor-12345678.js").write_text('export function createRoot(node){return node}')
    (root / "assets/detail-12345678.js").write_text('export const detail=42;')
    (root / "assets/style-12345678.css").write_text('@font-face{src:url("./font-12345678.woff2")}body{background:url(data:image/svg+xml;base64,AAAA)}')
    (root / "assets/font-12345678.woff2").write_bytes(b"independent fixture font bytes")
    manifest = {
        "index.html": {"file": "assets/index-12345678.js", "isEntry": True, "css": ["assets/style-12345678.css"], "imports": ["_vendor.js"], "dynamicImports": ["detail.tsx"]},
        "_vendor.js": {"file": "assets/vendor-12345678.js"},
        "detail.tsx": {"file": "assets/detail-12345678.js", "isDynamicEntry": True},
    }
    (root / ".vite/manifest.json").write_text(json.dumps(manifest))
    return root


def wheel_from(dist: Path, target: Path, *, omit: str = "", replace: str = "") -> Path:
    with ZipFile(target, "w") as archive:
        for path in dist.rglob("*"):
            if path.is_file():
                relative = path.relative_to(dist).as_posix()
                if relative != omit:
                    archive.writestr("tinystudio/ui/dist/" + relative, b"wrong bytes" if relative == replace else path.read_bytes())
    return target


def test_independent_asset_graph_resolves_static_dynamic_css_and_font(built):
    report = checker.inspect_dist(built)
    assert report.entries == ("assets/index-12345678.js",)
    assert set(report.referenced) == {"assets/index-12345678.js", "assets/vendor-12345678.js", "assets/detail-12345678.js", "assets/style-12345678.css", "assets/font-12345678.woff2"}
    assert len(report.files) == 7


@pytest.mark.parametrize("missing", ["assets/vendor-12345678.js", "assets/detail-12345678.js", "assets/style-12345678.css", "assets/font-12345678.woff2"])
def test_missing_recursive_asset_is_a_release_failure(built, missing):
    (built / missing).unlink()
    with pytest.raises(checker.AssetError, match="Missing asset"):
        checker.inspect_dist(built)


def test_manifest_dependency_key_must_resolve(built):
    path = built / ".vite/manifest.json"
    manifest = json.loads(path.read_text())
    del manifest["detail.tsx"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(checker.AssetError, match="Missing manifest dynamicImports"):
        checker.inspect_dist(built)


@pytest.mark.parametrize("reference", ["../outside.js", "/ui/%2e%2e/outside.js", "/api/studio.js", "https://esm.sh/react@19", "react"])
def test_traversal_remote_or_unbundled_module_is_rejected(built, reference):
    (built / "assets/index-12345678.js").write_text(f'import "{reference}";')
    with pytest.raises(checker.AssetError, match="Traversal|escapes|External|Unbundled"):
        checker.inspect_dist(built)


def test_asset_symlink_cannot_read_outside_dist(built, tmp_path):
    outside = tmp_path / "outside.js"
    outside.write_text("createRoot()")
    asset = built / "assets/vendor-12345678.js"
    asset.unlink()
    asset.symlink_to(outside)
    with pytest.raises(checker.AssetError, match="symlink escapes"):
        checker.inspect_dist(built)


def test_unreferenced_symlink_directory_still_cannot_escape_static_root(built, tmp_path):
    outside = tmp_path / "external"
    outside.mkdir()
    (built / "exposed").symlink_to(outside, target_is_directory=True)
    with pytest.raises(checker.AssetError, match="symlink escapes"):
        checker.inspect_dist(built)


def test_entry_without_react_runtime_is_rejected(built):
    for relative in ("assets/index-12345678.js", "assets/vendor-12345678.js"):
        path = built / relative
        path.write_text(path.read_text().replace("createRoot", "nativeRenderer"))
    with pytest.raises(checker.AssetError, match="React createRoot runtime"):
        checker.inspect_dist(built)


@pytest.mark.parametrize("name", [".env", ".env.production", "credentials.json", "assets/private.key", "assets/index.js.map"])
def test_prohibited_secret_file_classes_and_source_maps_are_excluded(built, name):
    (built / name).write_text("safe fixture marker; never a real secret")
    with pytest.raises(checker.AssetError, match="Prohibited"):
        checker.inspect_dist(built)


def test_inline_sourcemap_reference_is_prohibited(built):
    with (built / "assets/index-12345678.js").open("a") as output:
        output.write("\n//# sourceMappingURL=data:application/json;base64,e30=")
    with pytest.raises(checker.AssetError, match="source map"):
        checker.inspect_dist(built)


def test_development_html_and_unmanifested_or_non_react_entries_fail(built):
    index = built / "index.html"
    index.write_text('<div id="root"></div><script type="module" src="/src/main.tsx"></script>')
    with pytest.raises(checker.AssetError, match="development source"):
        checker.inspect_dist(built)
    index.write_text('<div id="root"></div><script type="module" src="/ui/assets/detail-12345678.js"></script>')
    with pytest.raises(checker.AssetError, match="does not match"):
        checker.inspect_dist(built)


def test_unresolved_environment_access_is_not_a_browser_contract(built):
    with (built / "assets/index-12345678.js").open("a") as output:
        output.write("\nconst forbidden=import.meta.env;")
    with pytest.raises(checker.AssetError, match="environment credential access"):
        checker.inspect_dist(built)


def test_orphaned_runtime_file_is_not_silently_packaged(built):
    (built / "assets/orphan-12345678.js").write_text("createRoot()")
    with pytest.raises(checker.AssetError, match="absent from manifest"):
        checker.inspect_dist(built)


def test_wheel_has_every_exact_verified_byte_including_vite_manifest(built, tmp_path):
    wheel = wheel_from(built, tmp_path / "fixture.whl")
    assert checker.inspect_wheel(built, wheel) == checker.inspect_dist(built)


@pytest.mark.parametrize("change", ["omit", "replace"])
def test_incomplete_or_stale_wheel_fails_even_when_local_build_passes(built, tmp_path, change):
    checker.inspect_dist(built)
    wheel = wheel_from(built, tmp_path / "fixture.whl", **{change: "assets/detail-12345678.js"})
    with pytest.raises(checker.AssetError, match="asset set differs|bytes differ"):
        checker.inspect_wheel(built, wheel)


def test_production_react_asset_graph_is_complete():
    report = checker.inspect_dist(ROOT / "tinystudio/ui/dist")
    assert report.entries
    assert any(name.endswith(".css") for name in report.files), "The verified React release must include emitted CSS"


def test_production_wheel_matches_verified_react_asset_graph():
    wheel = os.environ.get("TINYSTUDIO_REACT_WHEEL")
    if not wheel:
        pytest.skip("Set TINYSTUDIO_REACT_WHEEL to the freshly built wheel for packaged-byte validation")
    checker.inspect_wheel(ROOT / "tinystudio/ui/dist", Path(wheel))


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
def test_executable_import_check_ignores_javascript_diagnostic_text(built, text):
    with (built / "assets/index-12345678.js").open("a") as output:
        output.write("\n"+text)
    checker.inspect_dist(built)


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
def test_executable_external_or_escaping_imports_remain_rejected(built, text):
    with (built / "assets/index-12345678.js").open("a") as output:
        output.write("\n"+text)
    with pytest.raises(checker.AssetError):
        checker.inspect_dist(built)


def test_parenthesized_existing_module_still_validates(built):
    source = built
    with (source / "assets/index-12345678.js").open("a") as output:
        output.write('\nimport((/*comment*/(`./detail-12345678.js`)));')
    checker.inspect_dist(source)
