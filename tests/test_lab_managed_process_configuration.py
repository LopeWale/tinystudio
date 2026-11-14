"""Managed process plans preserve choices without launching or probing anything."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tinystudio.config import RuntimeTools
from tinystudio.lab.managed_process_configuration import (
    ManagedProcessPaths, ManagedProcessSelection, ReadinessSelection,
)


def selection(**changes):
    return {'argv': ['selected-emulator', '--timeout', '0', '', '  exact text\n', '900719925474099312345678901'],
            'cwd': 'researcher workspace/../selected', 'readiness': {'kind': 'process_running'}, **changes}


def test_authored_process_arguments_environment_and_paths_are_preserved_defensively():
    authored = selection(public_env={'UNICODE': 'μ\nvalue', 'EMPTY': ''})
    original = deepcopy(authored)
    plan = ManagedProcessSelection.from_dict(authored)
    assert plan.argv == tuple(original['argv'])
    assert str(plan.cwd) == original['cwd']
    assert dict(plan.public_env) == original['public_env']
    assert authored == original
    authored['argv'][1] = 'changed'
    authored['public_env']['UNICODE'] = 'changed'
    public = plan.as_dict()
    public['argv'][0] = 'changed'
    public['public_env']['EMPTY'] = 'changed'
    assert plan.argv == tuple(original['argv']) and dict(plan.public_env) == original['public_env']
    with pytest.raises(TypeError):
        plan.public_env['EMPTY'] = 'changed'
    with pytest.raises(FrozenInstanceError):
        plan.cwd = Path('changed')


def test_defaults_keep_raw_logs_private_and_select_no_extra_startup_or_log_ceiling():
    plan = ManagedProcessSelection.from_dict(selection())
    assert plan.startup_timeout_seconds is None and plan.max_log_bytes is None
    assert plan.shutdown_grace_seconds == 3 and plan.poll_interval_seconds == .02
    assert plan.raw_log_visibility is None and plan.export_raw_logs is False
    assert plan.as_dict()['raw_log_visibility'] is None
    assert plan.raw_log_policy == {
        'export_raw_logs': False, 'visibility': None, 'automatic_redaction': False, 'raw_rewrite': False}


@pytest.mark.parametrize('visibility', ['model', 'evaluation', 'truth'])
def test_raw_log_export_is_an_explicit_visibility_choice_without_rewriting(visibility):
    plan = ManagedProcessSelection.from_dict(selection(raw_log_visibility=visibility))
    assert plan.export_raw_logs is True
    assert plan.raw_log_policy == {
        'export_raw_logs': True, 'visibility': visibility, 'automatic_redaction': False, 'raw_rewrite': False}


@pytest.mark.parametrize('budget', [None, 0, 1, 10 ** 100])
def test_log_byte_budgets_honor_none_zero_and_unrestricted_selected_integer_size(budget):
    plan = ManagedProcessSelection.from_dict(selection(max_log_bytes=budget))
    assert plan.max_log_bytes == budget


@pytest.mark.parametrize('seconds', [None, 0, .125, 10 ** 100])
def test_startup_time_has_explicit_none_and_zero_semantics(seconds):
    plan = ManagedProcessSelection.from_dict(selection(startup_timeout_seconds=seconds))
    assert plan.startup_timeout_seconds == seconds


@pytest.mark.parametrize('seconds', [0, .125, 10 ** 100])
def test_shutdown_grace_can_be_zero_without_becoming_a_default(seconds):
    assert ManagedProcessSelection.from_dict(selection(shutdown_grace_seconds=seconds)).shutdown_grace_seconds == seconds


@pytest.mark.parametrize('encoded,expected', [
    ({'encoding': 'utf8', 'value': 'μ\x00READY\r\n'}, 'μ\x00READY\r\n'.encode()),
    ({'encoding': 'hex', 'value': '00ff0d0a'}, b'\x00\xff\r\n'),
    ({'encoding': 'base64', 'value': 'AP8NCg=='}, b'\x00\xff\r\n'),
    ({'encoding': 'base64', 'value': ''}, b''),
])
def test_log_marker_is_exact_binary_and_round_trips_without_text_normalization(encoded, expected):
    check = ReadinessSelection.from_dict({'kind': 'log_bytes', 'stream': 'stderr', 'marker': encoded})
    assert check.marker == expected and check.stream == 'stderr'
    assert ReadinessSelection.from_dict(check.as_dict()).marker == expected
    assert check.scope == 'owned_log_byte_sequence_observed'


def test_direct_construction_freezes_mutable_binary_and_command_inputs():
    marker = bytearray(b'\x00\xff')
    check = ReadinessSelection('log_bytes', stream='stdout', marker=marker)
    marker[:] = b'xx'
    assert check.marker == b'\x00\xff'
    arguments = ['researcher-check', '', '--expected', '-9']
    check = ReadinessSelection('command', argv=arguments, expected_exit_code=-9)
    arguments[0] = 'changed'
    assert check.argv == ('researcher-check', '', '--expected', '-9') and check.cwd is None
    assert check.expected_exit_code == -9 and check.scope == 'selected_command_exit_status'


@pytest.mark.parametrize('host', ['127.0.0.1', '::1', '研究.example', 'localhost'])
def test_tcp_check_describes_endpoint_reachability_without_assuming_emulator_identity(host):
    check = ReadinessSelection.from_dict({'kind': 'tcp', 'host': host, 'port': 65535})
    assert check.host == host and check.port == 65535
    assert check.scope == 'endpoint_reachability_only'
    assert ReadinessSelection.from_dict(check.as_dict()) == check


def test_selected_custom_command_has_its_own_optional_cwd_and_no_vendor_whitelist():
    check = ReadinessSelection.from_dict({'kind': 'command', 'argv': ['custom-checker', '', '--bytes', '00ff'],
        'cwd': '/operator/alias/../checks', 'expected_exit_code': 17})
    assert check.argv == ('custom-checker', '', '--bytes', '00ff')
    assert check.cwd == Path('/operator/alias/../checks') and check.expected_exit_code == 17
    assert ReadinessSelection.from_dict(check.as_dict()) == check
    plan = ManagedProcessSelection(('researcher-native-program', ''), Path('owned'), check)
    assert plan.argv == ('researcher-native-program', '')


def test_paths_keep_private_raw_streams_outside_the_automatic_export_tree():
    paths = ManagedProcessPaths(Path('selected work'))
    assert paths.evidence_directory == Path('selected work/evidence/parent-emulator')
    assert paths.private_directory == Path('selected work/parent-emulator-private')
    assert paths.lifecycle == paths.evidence_directory / 'lifecycle.jsonl'
    assert paths.readiness == paths.evidence_directory / 'readiness.jsonl'
    assert paths.manifest == paths.evidence_directory / 'manifest.json'
    for stream in ('stdout', 'stderr'):
        assert paths.private_log(stream) == paths.private_directory / (stream + '.bin')
        assert not paths.private_log(stream).is_relative_to(paths.evidence_directory)
        assert paths.exported_log(stream) == paths.evidence_directory / ('raw-' + stream + '.bin')
    for stream in ('../stdout', '/stderr', '', None):
        with pytest.raises(ValueError): paths.private_log(stream)
        with pytest.raises(ValueError): paths.exported_log(stream)


def test_process_and_readiness_construction_have_no_lookup_launch_resolution_or_probe(monkeypatch):
    import shutil
    import socket
    import subprocess
    def forbidden(*args, **kwargs):
        pytest.fail('configuration performed execution or filesystem/network discovery')
    for name in ('stat', 'resolve', 'exists', 'mkdir'):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(shutil, 'which', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    for readiness in [{'kind': 'process_running'}, {'kind': 'tcp', 'host': 'local.example', 'port': 8765},
        {'kind': 'log_bytes', 'stream': 'stdout', 'marker': {'encoding': 'hex', 'value': '00ff'}},
        {'kind': 'command', 'argv': ['uninstalled-checker', '']}]:
        assert ManagedProcessSelection.from_dict(selection(readiness=readiness)).readiness.kind == readiness['kind']
    assert ManagedProcessPaths('uncreated').manifest.name == 'manifest.json'
    assert RuntimeTools().command('renode') == 'renode'
    assert RuntimeTools().command('wokwi_cli') == 'wokwi-cli'


@pytest.mark.parametrize('changes', [
    {'argv': []}, {'argv': ''}, {'argv': ['', 'valid']}, {'argv': ['valid', None]},
    {'argv': ['valid\0']}, {'argv': ['valid', '\ud800']},
    {'cwd': ''}, {'cwd': None}, {'cwd': '/bad\0path'}, {'cwd': '\udfff'},
    {'public_env': []}, {'public_env': {'': 'bad'}}, {'public_env': {'A=B': 'bad'}},
    {'public_env': {'KEY': None}}, {'public_env': {'KEY': 'bad\0'}}, {'public_env': {'KEY': '\ud800'}},
    {'startup_timeout_seconds': False}, {'startup_timeout_seconds': -1},
    {'startup_timeout_seconds': float('nan')}, {'startup_timeout_seconds': float('inf')},
    {'startup_timeout_seconds': 10 ** 1000}, {'shutdown_grace_seconds': None}, {'shutdown_grace_seconds': True},
    {'shutdown_grace_seconds': -1}, {'shutdown_grace_seconds': float('-inf')},
    {'max_log_bytes': True}, {'max_log_bytes': -1}, {'max_log_bytes': 1.0},
    {'poll_interval_seconds': None}, {'poll_interval_seconds': True},
    {'poll_interval_seconds': float('inf')}, {'poll_interval_seconds': -1},
    {'raw_log_visibility': True}, {'raw_log_visibility': 'private'}, {'raw_log_visibility': 'public'},
    {'readiness': None}, {'unexpected': 1}, {'runtime_secrets': {'TOKEN': 'private'}},
])
def test_malformed_or_ambiguous_process_selection_is_rejected(changes):
    with pytest.raises(ValueError):
        ManagedProcessSelection.from_dict(selection(**changes))


@pytest.mark.parametrize('supplied', [None, [], {}, {'kind': 'unknown'},
    {'kind': 'process_running', 'host': None},
    {'kind': 'tcp', 'host': '', 'port': 1},
    {'kind': 'tcp', 'host': '127.0.0.1', 'port': 0},
    {'kind': 'tcp', 'host': '127.0.0.1', 'port': 65536},
    {'kind': 'tcp', 'host': '127.0.0.1', 'port': True},
    {'kind': 'tcp', 'host': '127.0.0.1', 'port': 1.0},
    {'kind': 'tcp', 'host': '127.0.0.1', 'port': 1, 'allow_dns': 1},
    {'kind': 'log_bytes', 'stream': 'stdout', 'marker': {'encoding': 'utf8', 'value': '\ud800'}},
    {'kind': 'log_bytes', 'stream': 'stdout', 'marker': {'encoding': 'base64', 'value': '!'}},
    {'kind': 'log_bytes', 'stream': 'stdout', 'marker': {'encoding': 'hex', 'value': 'g0'}},
    {'kind': 'log_bytes', 'stream': '../stdout', 'marker': {'encoding': 'hex', 'value': '00'}},
    {'kind': 'log_bytes', 'stream': 'stdout', 'marker': 'text'},
    {'kind': 'command', 'argv': []}, {'kind': 'command', 'argv': ['']},
    {'kind': 'command', 'argv': ['valid'], 'expected_exit_code': True},
    {'kind': 'command', 'argv': ['valid'], 'expected_exit_code': 0.0},
    {'kind': 'command', 'argv': ['valid'], 'cwd': ''},
    {'kind': 'command', 'argv': ['valid'], 'timeout_seconds': 4},
])
def test_readiness_kinds_have_exact_admissible_fields_and_protocol_bounds(supplied):
    with pytest.raises(ValueError):
        ReadinessSelection.from_dict(supplied)


def test_zero_poll_interval_is_an_authored_immediate_retry_choice():
    assert ManagedProcessSelection.from_dict(selection(poll_interval_seconds=0)).poll_interval_seconds == 0


def test_normalized_selection_round_trip_keeps_public_and_raw_export_policy():
    plan = ManagedProcessSelection.from_dict(selection(public_env={'KEEP': ''}, raw_log_visibility='evaluation',
        readiness={'kind': 'log_bytes', 'stream': 'stdout', 'marker': {'encoding': 'hex', 'value': '00ff'}}))
    assert ManagedProcessSelection.from_dict(plan.as_dict()) == plan
    policy = plan.raw_log_policy
    policy['visibility'] = 'changed'
    assert plan.raw_log_policy['visibility'] == 'evaluation'


def test_runtime_tools_emulator_aliases_follow_explicit_environment_selection(monkeypatch):
    monkeypatch.setenv('TINYSTUDIO_RENODE', '/researcher tools/renode-selected')
    monkeypatch.setenv('TINYSTUDIO_WOKWI_CLI', '/researcher tools/wokwi-selected')
    tools = RuntimeTools.from_environment()
    assert tools.command('renode') == '/researcher tools/renode-selected'
    assert tools.command('wokwi_cli') == '/researcher tools/wokwi-selected'
    monkeypatch.setenv('TINYSTUDIO_RENODE', '')
    monkeypatch.setenv('TINYSTUDIO_WOKWI_CLI', '')
    assert RuntimeTools.from_environment().command('renode') == 'renode'
    assert RuntimeTools.from_environment().command('wokwi_cli') == 'wokwi-cli'


@pytest.mark.parametrize('alias,filename,variable', [
    ('renode', 'renode', 'TINYSTUDIO_RENODE'), ('wokwi_cli', 'wokwi-cli', 'TINYSTUDIO_WOKWI_CLI')])
def test_emulator_resolution_uses_selected_path_and_never_falls_back_from_an_explicit_override(
        monkeypatch, tmp_path, alias, filename, variable):
    executable = tmp_path / filename
    executable.write_bytes(b'owned configuration fixture; never executed')
    executable.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path))
    monkeypatch.delenv(variable, raising=False)
    assert RuntimeTools.from_environment().resolve(alias) == str(executable)
    monkeypatch.setenv(variable, str(tmp_path / 'absent'))
    with pytest.raises(FileNotFoundError, match=variable):
        RuntimeTools.from_environment().resolve(alias)


def test_runtime_tool_emulator_fields_preserve_existing_positional_construction():
    tools = RuntimeTools('python-selected', 'cloud-selected', 'docker-selected', 'kube-selected', 'redis-selected',
                         'selected-search-path', renode='renode-selected', wokwi_cli='wokwi-selected')
    assert tools.path == 'selected-search-path' and tools.python == 'python-selected'
    assert tools.renode == 'renode-selected' and tools.wokwi_cli == 'wokwi-selected'
