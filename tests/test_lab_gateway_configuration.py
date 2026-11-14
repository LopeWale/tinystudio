"""Private gateway JSON is exact data with an explicit optional byte budget."""
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tinystudio.lab.gateway_configuration import GatewayApiPaths, GatewayFilePolicy, GatewayStoragePaths


def private_file(path, content):
    path.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
    path.chmod(0o600)
    return path


def test_private_json_larger_than_64_kib_has_no_default_ceiling(tmp_path):
    original = {'operator_notes': 'x' * (70 * 1024), 'integer': 9007199254740993}
    path = private_file(tmp_path / 'binding.json', json.dumps(original))
    assert path.stat().st_size > 65536
    assert GatewayFilePolicy().read(path) == original


@pytest.mark.parametrize('margin', [0, 1])
def test_exact_and_larger_selected_budget_allow_the_exact_private_bytes(tmp_path, margin):
    wire = b'{"operator_notes":"owned exact bytes"}\n'
    path = private_file(tmp_path / 'binding.json', wire)
    assert GatewayFilePolicy(max_bytes=len(wire) + margin).read(path) == {'operator_notes': 'owned exact bytes'}


@pytest.mark.parametrize('budget', [0, 1])
def test_explicit_zero_or_exhausted_byte_budget_is_not_treated_as_unbounded(tmp_path, budget):
    path = private_file(tmp_path / 'binding.json', '{}')
    with pytest.raises(ValueError, match='byte budget'):
        GatewayFilePolicy(max_bytes=budget).read(path)


@pytest.mark.parametrize('value', [True, -1, 1.5, '65536'])
def test_invalid_selected_policy_is_rejected(value):
    with pytest.raises(ValueError, match='budget'):
        GatewayFilePolicy(max_bytes=value)


def test_budget_selection_and_shared_paths_are_importable_immutable_and_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv('TINYSTUDIO_GATEWAY_CONFIG_BYTES', raising=False)
    assert GatewayFilePolicy.from_environment().max_bytes is None
    monkeypatch.setenv('TINYSTUDIO_GATEWAY_CONFIG_BYTES', '0')
    assert GatewayFilePolicy.from_environment().max_bytes == 0
    policy = GatewayFilePolicy(max_bytes=10 ** 30)
    with pytest.raises(FrozenInstanceError):
        policy.max_bytes = 1
    paths = GatewayStoragePaths.in_directory(tmp_path)
    assert paths.journal == tmp_path / 'gateway.sqlite3'
    assert paths.journal_lock == tmp_path / 'gateway.sqlite3.lock'
    assert paths.endpoint_lock({'port': 1502, 'host': '127.0.0.1'}) == paths.endpoint_lock({'host': '127.0.0.1', 'port': 1502})
    assert paths.endpoint_lock({'port': 1503}) != paths.endpoint_lock({'port': 1502})
    assert GatewayApiPaths().command_profile == '/command-profile'
    assert GatewayApiPaths(base='/owned').route('commands') == '/owned/commands'
    with pytest.raises(ValueError, match='unknown'):
        GatewayApiPaths().route('../commands')


@pytest.mark.parametrize('mode', [0o400, 0o640, 0o644, 0o660, 0o700])
def test_configuration_requires_exact_private_owner_mode(tmp_path, mode):
    path = private_file(tmp_path / 'binding.json', '{}')
    path.chmod(mode)
    with pytest.raises(ValueError, match='0600'):
        GatewayFilePolicy().read(path)


def test_symlinks_and_hardlink_aliases_are_rejected(tmp_path):
    path = private_file(tmp_path / 'binding.json', '{}')
    symbolic = tmp_path / 'symbolic.json'
    symbolic.symlink_to(path)
    with pytest.raises(ValueError, match='symlink'):
        GatewayFilePolicy().read(symbolic)
    hard = tmp_path / 'hard.json'
    os.link(path, hard)
    for selected in (path, hard):
        with pytest.raises(ValueError, match='operator-owned|regular file'):
            GatewayFilePolicy().read(selected)


def test_foreign_owner_is_rejected_before_reading_contents(tmp_path, monkeypatch):
    path = private_file(tmp_path / 'binding.json', '{}')
    monkeypatch.setattr(os, 'geteuid', lambda: path.stat().st_uid + 1)
    with pytest.raises(ValueError, match='operator-owned'):
        GatewayFilePolicy().read(path)


@pytest.mark.parametrize('wire', [
    '{"port":1,"port":2}', '{"transport":{"port":1,"port":2}}',
    '{"value":NaN}', '{"value":Infinity}', '{"value":-Infinity}', '{"value":1e999}',
    '[]', 'true', 'null', '1', '"text"',
])
def test_configuration_rejects_ambiguous_nonfinite_or_non_object_json(tmp_path, wire):
    path = private_file(tmp_path / 'binding.json', wire)
    with pytest.raises(ValueError):
        GatewayFilePolicy().read(path)


def test_literal_payload_strings_are_not_parsed_or_normalized_as_configuration_json(tmp_path):
    selected = {'wire': '{"duplicate":1,"duplicate":2,"reading":NaN,"exponent":1e999}\r\n'}
    path = private_file(tmp_path / 'binding.json', json.dumps(selected))
    assert GatewayFilePolicy().read(path) == selected


def test_owned_fifo_is_rejected_without_waiting_for_a_peer(tmp_path):
    path = tmp_path / 'configuration.fifo'
    os.mkfifo(path, 0o600)
    program = '''import sys
from tinystudio.lab.gateway_configuration import GatewayFilePolicy
try:GatewayFilePolicy().read(sys.argv[1])
except ValueError:raise SystemExit(0)
raise SystemExit(3)
'''
    process = subprocess.Popen([sys.executable, '-c', program, str(path)],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        try:
            _, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pytest.fail('regular-file validation blocked while opening an owned FIFO with no peer')
        assert process.returncode == 0, stderr.decode()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=2)
