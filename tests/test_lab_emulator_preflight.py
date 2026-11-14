"""Configured emulator availability is pure and distinct from runtime proof."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

from tinystudio.config import RuntimeTools
from tinystudio.lab.contracts import ExperimentDefinition
from tinystudio.lab import hardware_emulator_selection as selection
from tinystudio.lab.preflight import execution_checks, mismatch


def grants():
    return {'native_execution': True, 'emulator_bindings': {
        'selected-emulator': {
            'executable': {'argv_prefix': ['/private/operator/secret-program', '', '--private-argument']},
            'public_env': {'PUBLIC_CHOICE': 'PUBLIC_SENTINEL'}},
        'alias': {'executable': {'runtime_tool': 'renode'}}}}


def definition(emulator=True):
    parameters = {'binding_id': 'selected-gateway',
        'supervisor': {'kind': 'recorded', 'artifact_id': 'supervisor'}}
    if emulator is not False:
        parameters['emulator'] = (None if emulator is None else {
            'binding_id': 'selected-emulator',
            'assets': [{'name': 'firmware', 'artifact_id': 'firmware', 'destination': 'project/firmware.bin'}],
            'argv': [{'asset': 'firmware'}, {'workspace': 'output'}],
            'readiness': {'kind': 'process_running'}})
    return ExperimentDefinition.model_validate({
        'name': 'Configured emulator session', 'engine': 'hardware.session',
        'operating_mode': 'simulation',
        'execution': {'backend': 'native', 'profile': 'trusted_native'},
        'inputs': [{'artifact_id': 'supervisor', 'sha256': 'a' * 64, 'role': 'source'},
                   {'artifact_id': 'firmware', 'sha256': 'b' * 64, 'role': 'source'}],
        'parameters': parameters})


def capabilities(**changes):
    return {'engines': ['hardware.session'], 'runtime': {'python': 'fixture'},
        'profiles_by_backend': {'native': ['trusted_native']},
        'hardware_session_native_execution': True,
        'hardware_session_bindings': [{'binding_id': 'selected-gateway'}], **changes}


def checks(body=None, caps=None):
    return {row['code']: row for row in execution_checks(
        definition() if body is None else body,
        capabilities() if caps is None else caps)}


def test_selected_emulator_requires_its_exact_host_binding_before_submission():
    result = checks()
    assert result['hardware_session_selection']['passed'] is True
    assert result['hardware_session_binding']['passed'] is True
    assert result['hardware_session_emulator_binding']['passed'] is False
    assert mismatch(definition().model_dump(), capabilities()) is not None


@pytest.mark.parametrize('bindings', [None, {}, 'selected-emulator', [], [None], ['selected-emulator'],
    [{'binding_id': 'neighbor'}], {'selected-emulator': {'binding_id': 'selected-emulator'}},
    [{'binding_id': 'selected-gateway'}]])
def test_another_or_malformed_emulator_advertisement_cannot_satisfy_selection(bindings):
    result = checks(caps=capabilities(hardware_session_emulator_bindings=bindings))
    assert result['hardware_session_emulator_binding']['passed'] is False


@pytest.mark.parametrize('qualification', [None, {}, {'physical_outcome': 'unverified'},
    {'executable': 'not_checked', 'readiness': 'not_checked', 'firmware': 'not_checked'}])
def test_exact_configured_binding_is_admissible_without_installation_or_fidelity_claims(qualification):
    caps = capabilities(hardware_session_emulator_bindings=[{
        'binding_id': 'selected-emulator', 'qualification': qualification}])
    assert all(row['passed'] for row in checks(caps=caps).values())
    assert mismatch(definition().model_dump(), caps) is None


@pytest.mark.parametrize('emulator', [False, None])
def test_absent_or_null_emulator_preserves_legacy_session_admission(emulator):
    result = checks(definition(emulator), capabilities(hardware_session_emulator_bindings='unused'))
    assert 'hardware_session_emulator_binding' not in result
    assert all(row['passed'] for row in result.values())


def test_emulator_binding_does_not_replace_gateway_or_trusted_native_authority():
    caps = capabilities(hardware_session_emulator_bindings=[{'binding_id': 'selected-emulator'}],
        hardware_session_native_execution=False, hardware_session_bindings=[])
    result = checks(caps=caps)
    assert result['hardware_session_emulator_binding']['passed'] is True
    assert result['hardware_session_native_execution']['passed'] is False
    assert result['hardware_session_binding']['passed'] is False


def test_malformed_selection_is_reported_before_emulator_binding_lookup():
    body = definition().model_dump()
    body['parameters']['emulator']['argv'] = [{'asset': 'missing'}]
    result = checks(ExperimentDefinition.model_validate(body))
    assert result['hardware_session_selection']['passed'] is False
    assert 'hardware_session_emulator_binding' not in result


def test_capabilities_expose_only_configured_identity_digest_and_unverified_scope():
    authored = grants()
    before = deepcopy(authored)
    rows = selection.emulator_binding_capabilities(authored)
    assert authored == before
    assert [row['binding_id'] for row in rows] == ['alias', 'selected-emulator']
    for row in rows:
        assert set(row) == {'binding_id', 'binding_sha256', 'qualification'}
        assert row['binding_sha256'] == selection.EmulatorProcessBinding.from_grants(authored, row['binding_id']).digest
        assert row['qualification'] == {
            'availability': 'configured_only', 'executable': 'not_checked',
            'readiness': 'not_checked', 'firmware': 'not_checked',
            'model_fidelity': 'unverified', 'physical_outcome': 'unverified'}
    encoded = json.dumps(rows)
    assert not any(value in encoded for value in [
        '/private/', 'secret-program', '--private-argument', 'PUBLIC_SENTINEL', 'argv_prefix', 'public_env'])
    rows[0]['qualification']['executable'] = 'caller mutation'
    assert selection.emulator_binding_capabilities(authored)[0]['qualification']['executable'] == 'not_checked'


def test_configured_digest_changes_with_selected_public_binding_not_private_overlay():
    authored = grants()
    before = selection.emulator_binding_capabilities(authored)
    binding = selection.EmulatorProcessBinding.from_grants(authored, 'selected-emulator')
    assert binding.select_runtime_secrets({'emulator_bindings': {
        'selected-emulator': {'environment': {'TOKEN': 'PRIVATE_SENTINEL'}}}}) == {
            'environment': {'TOKEN': 'PRIVATE_SENTINEL'}}
    assert selection.emulator_binding_capabilities(authored) == before
    authored['emulator_bindings']['selected-emulator']['executable']['argv_prefix'][1] = 'new argument'
    after = selection.emulator_binding_capabilities(authored)
    assert after[0] == before[0]
    assert after[1]['binding_sha256'] != before[1]['binding_sha256']


@pytest.mark.parametrize('native', [None, False, 0, 1, 'true', {}, []])
def test_disabled_native_grant_advertises_no_emulator_binding(native):
    authored = grants()
    authored['native_execution'] = native
    assert selection.emulator_binding_capabilities(authored) == []


def test_no_configured_emulator_bindings_is_compatible_with_existing_session_grants():
    assert selection.emulator_binding_capabilities({}) == []
    assert selection.emulator_binding_capabilities({'native_execution': True, 'bindings': {}}) == []


@pytest.mark.parametrize('authored', [None, [], {'emulator_bindings': None},
    {'native_execution': True, 'emulator_bindings': []},
    {'native_execution': True, 'emulator_bindings': {1: {'executable': {'argv_prefix': ['program']}}}},
    {'native_execution': True, 'emulator_bindings': {'chosen': {'executable': {'argv_prefix': ['']}}}},
    {'native_execution': True, 'emulator_bindings': {'chosen': {'executable': {'argv_prefix': ['program']},
        'environment': {'TOKEN': 'PRIVATE_SENTINEL'}}}}])
def test_malformed_config_is_not_silently_advertised(authored):
    with pytest.raises(ValueError): selection.emulator_binding_capabilities(authored)


def test_public_declaration_and_preflight_perform_no_lookup_launch_or_probe(monkeypatch):
    body, authored = definition(), grants()
    def forbidden(*args, **kwargs): pytest.fail('capability/preflight performed I/O')
    with monkeypatch.context() as patch:
        for name in ('resolve', 'stat', 'exists', 'open'):
            patch.setattr(Path, name, forbidden)
        patch.setattr(shutil, 'which', forbidden)
        patch.setattr(socket, 'socket', forbidden)
        patch.setattr(socket, 'getaddrinfo', forbidden)
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(RuntimeTools, 'from_environment', forbidden)
        caps = capabilities(hardware_session_emulator_bindings=selection.emulator_binding_capabilities(authored))
        result = checks(body, caps)
    assert all(row['passed'] for row in result.values())
