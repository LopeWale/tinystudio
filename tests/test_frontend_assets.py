"""Check the served native module graph, including paths browsers resolve recursively."""
import importlib.util
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from fastapi.testclient import TestClient

from tinystudio.api.app import UI_DIR, create_app

IMPORT = re.compile(r'''(?m)^\s*(?:import|export)\b[^\n]*?\bfrom\s*["']([^"']+)["']''')


def test_every_browser_module_is_served_with_javascript_mime():
    client = TestClient(create_app(run_migrations=False, auto_up=False))
    index = client.get("/")
    assert index.status_code == 200
    entry = re.search(r'<script type="module" src="([^"]+)"', index.text).group(1)
    visited, active = set(), set()

    def visit(url):
        assert url not in active, f"cyclic dependency: {url}"
        if url in visited:
            return
        active.add(url)
        response = client.get(url)
        assert response.status_code == 200, url
        assert "javascript" in response.headers["content-type"], url
        for specifier in IMPORT.findall(response.text):
            assert specifier.startswith("."), (url, specifier)
            visit(urljoin(url, specifier))
        active.remove(url)
        visited.add(url)

    visit(entry)
    paths = {urlsplit(url).path.removeprefix("/ui/") for url in visited}
    assert paths == {path.relative_to(UI_DIR).as_posix() for path in UI_DIR.rglob("*.js")}
    assert {"core/api.js", "core/state.js", "core/router.js", "routes.js", "application.js", "assistant/view.js", "pages/datasets.js"} <= paths


def test_release_versioning_is_stable_and_changes_for_dependency_updates(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/version_frontend.py"
    spec = importlib.util.spec_from_file_location("frontend_versioning", path)
    versioning = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(versioning)
    (tmp_path / "studio.js").write_text('import { render } from "./page.js";\nrender();\n')
    (tmp_path / "page.js").write_text('export const render = () => "first";\n')
    (tmp_path / "index.html").write_text('<script type="module" src="/ui/studio.js"></script>')
    first = versioning.version_frontend(tmp_path)
    assert f'page.js?v={first}' in (tmp_path / "studio.js").read_text()
    assert versioning.version_frontend(tmp_path) == first
    (tmp_path / "page.js").write_text('export const render = () => "changed";\n')
    second = versioning.version_frontend(tmp_path)
    assert second != first
    assert f'page.js?v={second}' in (tmp_path / "studio.js").read_text()
    assert f'studio.js?v={second}' in (tmp_path / "index.html").read_text()
