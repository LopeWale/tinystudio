"""Hatch packaging gate for a complete, prebuilt React release.

This hook validates only. It never invokes Node, npm, the application or network.
Build the frontend explicitly before packaging (Docker does so in its Node stage).
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
from typing import Any


BUILD_INSTRUCTIONS = (
    'Build the React frontend first: `npm ci --prefix frontend` then '
    '`npm run build --prefix frontend`; retry the Python build afterwards. '
    'Python packaging never downloads frontend dependencies or runs npm implicitly.'
)


def _checker() -> Any:
    name = '_tinystudio_react_build_asset_checker'
    if name not in sys.modules:
        path = Path(__file__).with_name('check_react_assets.py')
        if not path.is_file():
            raise RuntimeError(f'React asset checker missing from build source: {path}. Include scripts/check_react_assets.py.')
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def validate_frontend(root: Path) -> dict[str, str]:
    """Return the digest snapshot of one complete distributable React release."""
    dist = root / 'tinystudio/ui/dist'
    try:
        report = _checker().inspect_dist(dist)
        return {name: hashlib.sha256((dist / name).read_bytes()).hexdigest() for name in report.files}
    except (ValueError, OSError) as error:
        raise RuntimeError(f'Cannot package TinyStudio: React production assets are missing or incomplete: {error}. {BUILD_INSTRUCTIONS}') from error


def validate_wheel(root: Path, artifact: Path, expected: dict[str, str]) -> None:
    """Keep one release stable throughout packaging, then compare archived bytes."""
    if validate_frontend(root) != expected:
        raise RuntimeError('React assets changed while the Python package was building. Finish the frontend build, then retry Python packaging.')
    try:
        _checker().inspect_wheel(root / 'tinystudio/ui/dist', artifact)
    except (ValueError, OSError) as error:
        raise RuntimeError(f'Wheel does not contain the verified React release: {error}. Keep tinystudio/ui/dist/** in Hatch build artifacts and rebuild.') from error


def get_build_hook():
    """Hatchling's custom plugin factory; import Hatchling only in its build env."""
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface

    class ReactFrontendBuildHook(BuildHookInterface):
        PLUGIN_NAME = 'custom'

        def initialize(self, version: str, build_data: dict[str, Any]) -> None:
            self._frontend_snapshot = validate_frontend(Path(self.root))

        def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
            if self.target_name == 'wheel' and version != 'editable' and artifact_path.endswith('.whl'):
                validate_wheel(Path(self.root), Path(artifact_path), self._frontend_snapshot)

    return ReactFrontendBuildHook
