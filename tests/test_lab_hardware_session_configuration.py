"""Only explicit trusted host grants select a native session binding."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from tinystudio.lab.hardware_session_configuration import HardwareSessionBinding


def grants(**entry):
    return {'native_execution': True, 'bindings': {'selected': {
        'gateway_binding': {'endpoint_id': 'owned-endpoint', 'operator_id': 'operator',
            'transport': {'kind': 'serial', 'port': 'loop://', 'baudrate': 9600},
            'command_profile': {'format': 'tinystudio.native-command-profile.v1'}},
        'state_directory': '/operator-private/session-state', **entry}}}


def test_defaults_and_public_description_do_not_expose_private_state_or_mutate_grants():
    supplied = grants()
    original = deepcopy(supplied)
    binding = HardwareSessionBinding.from_grants(supplied, 'selected')
    assert binding.binding_id == 'selected' and binding.state_directory == Path('/operator-private/session-state')
    assert binding.lease_seconds == 30 and binding.renew_interval_seconds == 5
    assert binding.max_request_bytes is None and binding.max_response_bytes is None
    public = binding.public_description()
    assert public['profile'] == binding.profile
    assert public['endpoint_id'] == 'owned-endpoint'
    assert public['qualification']['execution'] == 'trusted_native_only'
    assert public['qualification']['physical_outcome'] == 'unverified'
    assert '/operator-private/' not in json.dumps(public) and 'state_directory' not in public
    assert supplied == original
    supplied['bindings']['selected']['gateway_binding']['transport']['port'] = 'changed'
    binding.gateway_binding['transport']['port'] = 'changed'
    binding.profile['capability_claims']['changed'] = True
    assert binding.gateway_binding['transport']['port'] == 'loop://'
    assert 'changed' not in binding.profile['capability_claims']
    with pytest.raises(FrozenInstanceError):
        binding.lease_seconds = 1


@pytest.mark.parametrize('budget', [None, 0, 1, 10 ** 30])
def test_selected_budgets_are_operational_and_not_silently_capped(budget):
    binding = HardwareSessionBinding.from_grants(grants(max_request_bytes=budget, max_response_bytes=budget), 'selected')
    assert binding.max_request_bytes == binding.max_response_bytes == budget
    assert binding.public_description()['max_request_bytes'] == budget


def test_renew_interval_can_exceed_lease_for_an_authored_failure_experiment():
    binding = HardwareSessionBinding.from_grants(grants(lease_seconds=.125, renew_interval_seconds=999), 'selected')
    assert binding.lease_seconds == .125 and binding.renew_interval_seconds == 999


@pytest.mark.parametrize('evidence,expected', [({}, {'flush_records': 1, 'fsync': True}),
    ({'flush_records': 'on_close', 'fsync': False}, {'flush_records': None, 'fsync': False}),
    ({'flush_records': None}, {'flush_records': None, 'fsync': True}),
    ({'flush_records': 10 ** 30, 'fsync': False}, {'flush_records': 10 ** 30, 'fsync': False})])
def test_evidence_policy_is_selected_explicitly_and_described_without_claiming_acknowledgement(evidence, expected):
    from tinystudio.lab.evidence_io import RecordWritePolicy
    binding = HardwareSessionBinding.from_grants(grants(evidence=evidence), 'selected')
    assert isinstance(binding.evidence, RecordWritePolicy)
    assert binding.evidence.to_dict() == binding.public_description()['evidence'] == expected
    assert 'fsynced_records' not in binding.public_description()


@pytest.mark.parametrize('evidence', [None, [], {'unknown': 1}, {'flush_records': 0}, {'fsync': 1}])
def test_malformed_evidence_policy_is_rejected(evidence):
    with pytest.raises(ValueError):
        HardwareSessionBinding.from_grants(grants(evidence=evidence), 'selected')


def test_binding_selection_performs_no_filesystem_or_optional_driver_io(monkeypatch):
    import importlib
    import socket
    monkeypatch.setattr(importlib, 'import_module', lambda *args, **kwargs: pytest.fail('no driver import'))
    monkeypatch.setattr(socket, 'socket', lambda *args, **kwargs: pytest.fail('no socket open'))
    monkeypatch.setattr(Path, 'resolve', lambda *args, **kwargs: pytest.fail('no operator path resolution'))
    binding = HardwareSessionBinding.from_grants(grants(state_directory='/operator-alias/../private/state'), 'selected')
    assert str(binding.state_directory) == '/operator-alias/../private/state'


@pytest.mark.parametrize('supplied', [None, [], {}, {'native_execution': 1, 'bindings': {}},
    {'native_execution': False, 'bindings': {}}, {'native_execution': True, 'bindings': []},
    {'native_execution': True, 'bindings': {}}, {'native_execution': True, 'bindings': {'selected': None}}])
def test_missing_or_ambiguous_authority_is_rejected(supplied):
    with pytest.raises(ValueError):
        HardwareSessionBinding.from_grants(supplied, 'selected')


@pytest.mark.parametrize('entry', [{'state_directory': 'relative'}, {'state_directory': '/bad\0path'},
    {'runtime_secrets': {'password': 'must-stay-parent'}}, {'unknown': 1},
    {'lease_seconds': None}, {'lease_seconds': 0}, {'renew_interval_seconds': False},
    {'renew_interval_seconds': float('inf')}, {'max_request_bytes': -1}, {'max_response_bytes': True}])
def test_selected_entry_has_exact_fields_and_explicit_valid_resources(entry):
    with pytest.raises(ValueError):
        HardwareSessionBinding.from_grants(grants(**entry), 'selected')


def test_legacy_protocol_binding_cannot_enter_the_native_session_seam():
    supplied = grants()
    supplied['bindings']['selected']['gateway_binding'].pop('command_profile')
    with pytest.raises(ValueError):
        HardwareSessionBinding.from_grants(supplied, 'selected')
