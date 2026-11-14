"""Portable emulator selections are pure, explicit and independent of vendors."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path

import pytest

from tinystudio.config import RuntimeTools
from tinystudio.lab.hardware_emulator_selection import EmulatorProcessBinding, EmulatorSelection


def definition(**changes):
    emulator = {'binding_id': 'selected', 'assets': [{'name': 'firmware', 'artifact_id': 'input', 'destination': 'project/firmware.bin'}],
        'argv': ['', {'asset': 'firmware'}, {'workspace': 'output'}, '900719925474099312345678901'],
        'readiness': {'kind': 'log_bytes', 'stream': 'stdout', 'marker': {'encoding': 'hex', 'value': '00ff'}}, **changes}
    return {'engine': 'hardware.session', 'inputs': [{'artifact_id': 'input', 'sha256': hashlib.sha256(b'firmware').hexdigest(), 'role': 'source'}],
            'parameters': {'emulator': emulator}}


def grants(**changes):
    return {'native_execution': True, 'emulator_bindings': {'selected': {
        'executable': {'argv_prefix': ['custom-native-executable', '']}, 'public_env': {'PUBLIC': ''}, **changes}}}


def test_optional_selection_keeps_legacy_definitions_and_freezes_exact_authored_tokens():
    assert EmulatorSelection.from_definition({'engine': 'hardware.session', 'parameters': {}}) is None
    authored = definition()
    before = deepcopy(authored)
    selected = EmulatorSelection.from_definition(authored)
    assert selected.as_dict() == authored['parameters']['emulator'] | {
        'cwd': '.', 'startup_timeout_seconds': None, 'shutdown_grace_seconds': 3,
        'max_log_bytes': None, 'poll_interval_seconds': .02, 'raw_log_visibility': None, 'on_exit': 'record'}
    digest = selected.digest
    authored['parameters']['emulator']['argv'][1]['asset'] = 'changed'
    selected.readiness['marker']['value'] = 'changed'
    selected.as_dict()['assets'][0]['destination'] = 'changed'
    assert selected.as_dict()['argv'][1] == {'asset': 'firmware'}
    assert selected.readiness['marker']['value'] == '00ff' and selected.digest == digest
    assert before['parameters']['emulator']['argv'][0] == ''
    with pytest.raises(FrozenInstanceError): selected.binding_id = 'changed'


def test_input_hash_is_bound_without_repeating_it_in_authored_asset_fields():
    first = EmulatorSelection.from_definition(definition())
    authored = definition()
    authored['inputs'][0]['sha256'] = 'b' * 64
    second = EmulatorSelection.from_definition(authored)
    assert first.as_dict() == second.as_dict() and first.digest != second.digest
    assert first.assets[0].sha256 == hashlib.sha256(b'firmware').hexdigest()


def test_long_colon_and_unicode_logical_paths_have_no_filename_or_paper_cap():
    path = 'device:研究/' + 'segment/' * 50 + 'f' * 300
    selected = EmulatorSelection.from_definition(definition(assets=[{'name': 'firmware', 'artifact_id': 'input', 'destination': path}]))
    assert selected.assets[0].destination == path


@pytest.mark.parametrize('resources', [
    {'startup_timeout_seconds': None, 'max_log_bytes': None, 'poll_interval_seconds': 0},
    {'startup_timeout_seconds': 0, 'max_log_bytes': 0, 'shutdown_grace_seconds': 0},
    {'startup_timeout_seconds': 10 ** 100, 'max_log_bytes': 10 ** 1000, 'raw_log_visibility': 'truth'},
])
def test_existing_none_zero_and_large_operational_choices_are_preserved(resources):
    selected = EmulatorSelection.from_definition(definition(**resources))
    for key, value in resources.items(): assert selected.as_dict()[key] == value


def test_readiness_command_tokens_are_validated_before_any_asset_or_program_lookup(monkeypatch):
    import shutil
    import socket
    import subprocess
    def forbidden(*args, **kwargs): pytest.fail('pure selection performed I/O')
    for name in ('resolve', 'stat', 'exists', 'mkdir'):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(shutil, 'which', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    selected = EmulatorSelection.from_definition(definition(readiness={
        'kind': 'command', 'argv': ['checker', {'asset': 'firmware'}, {'workspace': '.'}, ''], 'cwd': 'checks', 'expected_exit_code': 7}))
    binding = EmulatorProcessBinding.from_grants(grants(executable={'runtime_tool': 'renode'}), 'selected')
    assert binding.argv_prefix(RuntimeTools(renode='selected-renode')) == ('selected-renode',)
    assert selected.readiness['argv'][1] == {'asset': 'firmware'}


@pytest.mark.parametrize('changes', [
    {'assets': [{'name': 'firmware', 'artifact_id': 'input', 'destination': '../escape'}]},
    {'assets': [{'name': 'firmware', 'artifact_id': 'input', 'destination': '/absolute'}]},
    {'assets': [{'name': 'firmware', 'artifact_id': 'input', 'destination': 'a//b'}]},
    {'assets': [{'name': 'firmware', 'artifact_id': 'input', 'destination': 'a/./b'}]},
    {'assets': [{'name': 'firmware', 'artifact_id': 'input', 'destination': 'a\\b'}]},
    {'assets': [{'name': 'firmware', 'artifact_id': 'missing', 'destination': 'file'}]},
    {'assets': [{'name': 'firmware', 'artifact_id': 'input', 'destination': 'file'},
                {'name': 'other', 'artifact_id': 'input', 'destination': 'file/child'}]},
    {'assets': [{'name': 'firmware', 'artifact_id': 'input', 'destination': 'file'},
                {'name': 'firmware', 'artifact_id': 'input', 'destination': 'other'}]},
    {'argv': [{'asset': 'missing'}]}, {'argv': [{'workspace': '../outside'}]},
    {'argv': [{'asset': 'firmware', 'literal': 'ignored'}]}, {'argv': [False]},
    {'cwd': '../outside'}, {'cwd': 'project/firmware.bin/subdir'},
    {'readiness': {'kind': 'command', 'argv': ['', {'asset': 'firmware'}]}},
    {'readiness': {'kind': 'command', 'argv': ['checker', {'asset': 'missing'}]}},
    {'readiness': {'kind': 'command', 'argv': ['checker'], 'cwd': '../outside'}},
    {'readiness': {'kind': 'tcp', 'host': 'localhost', 'port': 0}},
    {'startup_timeout_seconds': True}, {'max_log_bytes': False},
    {'runtime_secrets': {'environment': {'TOKEN': 'private'}}}, {'unexpected': 1},
])
def test_invalid_or_ambiguous_portable_mappings_fail_without_silent_reinterpretation(changes):
    with pytest.raises(ValueError): EmulatorSelection.from_definition(definition(**changes))


def test_operator_executable_prefix_has_no_vendor_whitelist_and_no_private_overlay():
    supplied = grants()
    binding = EmulatorProcessBinding.from_grants(supplied, 'selected')
    digest = binding.digest
    supplied['emulator_bindings']['selected']['executable']['argv_prefix'][0] = 'changed'
    assert binding.argv_prefix(RuntimeTools()) == ('custom-native-executable', '')
    assert binding.digest == digest
    with pytest.raises(TypeError): binding.public_env['PUBLIC'] = 'changed'
    assert 'runtime_secrets' not in json.dumps(binding.as_dict())
    for invalid in [grants(executable={'runtime_tool': 'unknown'}), grants(environment={'TOKEN': 'private'}),
        {'native_execution': 1, 'emulator_bindings': {}}, grants(executable={'argv_prefix': ['']}),
        grants(executable={'runtime_tool': 'renode', 'argv_prefix': ['other']})]:
        with pytest.raises(ValueError): EmulatorProcessBinding.from_grants(invalid, 'selected')
