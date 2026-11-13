"""Image interpreter selection remains an operator choice, not a host path guess."""
import sys

import pytest

from tinystudio.config import RuntimeTools
from tinystudio.lab.runner import Runner


def test_image_python_default_and_environment_live_in_importable_settings(monkeypatch):
    monkeypatch.delenv('TINYSTUDIO_CONTAINER_PYTHON', raising=False)
    assert RuntimeTools.from_environment().container_python == 'python'
    monkeypatch.setenv('TINYSTUDIO_CONTAINER_PYTHON', '../image-venv/bin/python')
    assert RuntimeTools.from_environment().container_python == '../image-venv/bin/python'


@pytest.mark.parametrize('backend', ['container', 'kubernetes'])
def test_image_specific_override_precedes_shared_default_without_host_resolution(monkeypatch, backend):
    monkeypatch.setenv('TINYSTUDIO_CONTAINER_PYTHON', 'python-from-image-path')
    assert Runner._python_command(backend, {}) == 'python-from-image-path'
    assert Runner._python_command(backend, {'python': '/image-only/env/bin/python'}) == '/image-only/env/bin/python'


@pytest.mark.parametrize('backend', ['native', 'rq'])
def test_native_execution_keeps_the_advertised_running_interpreter(monkeypatch, backend):
    monkeypatch.setenv('TINYSTUDIO_CONTAINER_PYTHON', 'another-runtime')
    assert Runner._python_command(backend, {'python': 'image-only'}) == sys.executable


@pytest.mark.parametrize('selection', [None, '', '  ', ['python'], 0, 'bad\x00path'])
def test_invalid_operator_interpreter_does_not_trigger_a_silent_fallback(selection):
    with pytest.raises(ValueError, match='Python|interpreter'):
        Runner._python_command('container', {'python': selection})
