"""Distribution gate checks without importing TinyStudio, dotenv, or a database."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tomllib
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tinystudio_frontend_build_hook', ROOT / 'scripts/build_frontend_hook.py')
hook = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hook
spec.loader.exec_module(hook)


@pytest.fixture
def project(tmp_path):
    dist = tmp_path / 'tinystudio/ui/dist'
    (dist / 'assets').mkdir(parents=True)
    (dist / '.vite').mkdir()
    (dist / 'index.html').write_text('<div id="root"></div><script type="module" src="/ui/assets/index-12345678.js"></script><link rel="stylesheet" href="/ui/assets/style-12345678.css">')
    (dist / 'assets/index-12345678.js').write_text('const root=document.getElementById("root");createRoot(root);')
    (dist / 'assets/style-12345678.css').write_text('body{color:inherit}')
    (dist / '.vite/manifest.json').write_text(json.dumps({'index.html': {'file': 'assets/index-12345678.js', 'css': ['assets/style-12345678.css'], 'isEntry': True}}))
    return tmp_path


def make_wheel(project, target, *, omit='', changed=''):
    dist = project / 'tinystudio/ui/dist'
    with ZipFile(target, 'w') as wheel:
        for path in dist.rglob('*'):
            if path.is_file():
                relative = path.relative_to(dist).as_posix()
                if relative != omit:
                    wheel.writestr('tinystudio/ui/dist/' + relative, b'wrong archived release' if relative == changed else path.read_bytes())
    return target


def test_missing_build_fails_with_explicit_manual_build_instructions(tmp_path):
    with pytest.raises(RuntimeError, match='npm ci --prefix frontend.*npm run build --prefix frontend'):
        hook.validate_frontend(tmp_path)


@pytest.mark.parametrize('relative', ['.vite/manifest.json', 'assets/index-12345678.js', 'assets/style-12345678.css'])
def test_incomplete_release_fails_before_packaging(project, relative):
    (project / 'tinystudio/ui/dist' / relative).unlink()
    with pytest.raises(RuntimeError, match='Cannot package TinyStudio.*missing or incomplete'):
        hook.validate_frontend(project)


def test_stale_html_reference_fails_even_when_new_assets_exist(project):
    dist = project / 'tinystudio/ui/dist'
    (dist / 'assets/index-12345678.js').rename(dist / 'assets/index-new12345.js')
    with pytest.raises(RuntimeError, match='Missing asset referenced by index.html'):
        hook.validate_frontend(project)


def test_valid_dist_has_stable_digest_snapshot_and_no_subprocess(project, monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError('Build hook must not invoke npm or any subprocess')
    monkeypatch.setattr(subprocess, 'run', prohibited)
    monkeypatch.setattr(subprocess, 'Popen', prohibited)
    snapshot = hook.validate_frontend(project)
    assert set(snapshot) == {'index.html', '.vite/manifest.json', 'assets/index-12345678.js', 'assets/style-12345678.css'}
    assert all(len(digest) == 64 for digest in snapshot.values())
    assert hook.validate_frontend(project) == snapshot


def test_wheel_exactly_matches_the_release_validated_at_initialize(project, tmp_path):
    snapshot = hook.validate_frontend(project)
    wheel = make_wheel(project, tmp_path / 'valid.whl')
    hook.validate_wheel(project, wheel, snapshot)


def test_changed_release_during_packaging_is_rejected(project, tmp_path):
    snapshot = hook.validate_frontend(project)
    wheel = make_wheel(project, tmp_path / 'old.whl')
    (project / 'tinystudio/ui/dist/assets/style-12345678.css').write_text('body{color:purple}')
    with pytest.raises(RuntimeError, match='changed while.*building'):
        hook.validate_wheel(project, wheel, snapshot)


@pytest.mark.parametrize('change', ['omit', 'changed'])
def test_missing_or_stale_wheel_asset_is_a_packaging_failure(project, tmp_path, change):
    snapshot = hook.validate_frontend(project)
    wheel = make_wheel(project, tmp_path / 'incomplete.whl', **{change:'assets/style-12345678.css'})
    with pytest.raises(RuntimeError, match='Wheel does not contain the verified React release'):
        hook.validate_wheel(project, wheel, snapshot)


def test_hatch_config_registers_actual_custom_hook_and_includes_ignored_dist():
    config = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    build = config['tool']['hatch']['build']
    assert build['hooks']['custom']['path'] == 'scripts/build_frontend_hook.py'
    assert 'tinystudio/ui/dist/**' in build['artifacts']
    assert build['targets']['wheel']['packages'] == ['tinystudio']


def test_docker_builds_frontend_before_installing_python_package():
    docker = (ROOT / 'Dockerfile').read_text()
    assert 'FROM node:24-bookworm-slim AS frontend-build' in docker
    assert docker.index('COPY frontend/package.json frontend/package-lock.json ./') < docker.index('RUN npm ci --no-audit --no-fund') < docker.index('COPY frontend/ ./') < docker.index('RUN npm run build')
    assert docker.index('COPY scripts/build_frontend_hook.py scripts/check_react_assets.py ./scripts/') < docker.index('RUN pip install ".[assistant]"')
    assert docker.index('COPY --from=frontend-build /build/tinystudio/ui/dist ./tinystudio/ui/dist') < docker.index('RUN pip install ".[assistant]"') < docker.index('RUN python scripts/check_react_assets.py')
    # Existing deployment role and CPU/CUDA Torch selection remain intact.
    assert 'ARG TORCH_INDEX_URL=' in docker
    assert 'RUN pip install --index-url ${TORCH_INDEX_URL} torch' in docker
    assert 'USER studio' in docker
    assert 'CMD ["tinystudio", "serve"]' in docker


def test_docker_context_omits_host_dependencies_secrets_and_companion_state():
    patterns = set((ROOT / '.dockerignore').read_text().splitlines())
    assert {'**/.env', '**/.env.*', '**/node_modules', '**/.superpowers', '.superpowers', 'tinystudio/ui/dist'} <= patterns
    assert not any(pattern.startswith('!.env') or pattern.startswith('!**/.env') for pattern in patterns)


def test_parser_versions_match_runtime_and_isolated_build_dependencies():
    config = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    expected = {'tree-sitter==0.25.2', 'tree-sitter-javascript==0.25.0'}
    assert expected <= set(config['project']['dependencies'])
    assert expected <= set(config['build-system']['requires'])
