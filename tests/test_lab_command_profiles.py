"""Native command contracts validate explicit author choices without device I/O."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest

from tinystudio.lab.command_profiles import (
    COMPLETED_NATIVE_STATUSES, CommandProfileError, NativeCommandProfile,
    decode_bytes, validate_read, validate_transport,
)


def binding(kind='serial', **overrides):
    transports = {
        'serial': {'kind': 'serial', 'port': 'loop://', 'baudrate': 8_000_001},
        'tcp': {'kind': 'tcp', 'host': '127.0.0.1', 'port': 5001},
        'modbus': {'kind': 'modbus', 'host': '127.0.0.1', 'port': 1502, 'device_id': 255},
        'mqtt': {'kind': 'mqtt', 'host': '127.0.0.1', 'port': 1883, 'topic': 'selected/control'},
        'can': {'kind': 'can', 'interface': 'virtual', 'channel': 'owned-fixture'},
        'custom': {'kind': 'custom', 'entrypoint': 'operator.plugin:Adapter', 'identity': {'device': 'chosen'},
                   'adapter_config': {'units': 'selected', 'calibration': [0, -3.75, 10 ** 30]}},
    }
    value = {'endpoint_id': 'endpoint-one', 'operator_id': 'operator-one', 'transport': transports[kind],
             'command_profile': {'format': 'tinystudio.native-command-profile.v1'}}
    value.update(overrides)
    return value


def data(value, encoding='utf8'):
    return {'encoding': encoding, 'value': value}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def request_and_receipt(profile, **changes):
    request = {'protocol': 'tinystudio.native-command.v1', 'endpoint_id': 'endpoint-one', 'command_id': 'command-one',
               'epoch': 7, 'owner': 'run-one', 'expires_at': 123.5, 'kind': 'command',
               'profile_digest': profile.digest, 'payload': {'operation': 'serial.write', 'data': data('STOP\r\n')}}
    receipt = {'format': 'tinystudio.command-delivery.v1', 'endpoint_id': 'endpoint-one', 'command_id': 'command-one',
               'epoch': 7, 'profile_digest': profile.digest, 'status': 'delivered_unverified',
               'device_application_confirmed': False, 'physical_outcome': 'unverified'}
    receipt.update(changes)
    return request, receipt


@pytest.mark.parametrize('kind', ['serial', 'tcp', 'modbus', 'mqtt', 'can', 'custom'])
def test_creation_never_opens_or_imports_an_optional_native_driver(kind, monkeypatch):
    import importlib
    import socket
    monkeypatch.setattr(importlib, 'import_module', lambda *a, **kw: pytest.fail('profile must not import a driver'))
    monkeypatch.setattr(socket, 'socket', lambda *a, **kw: pytest.fail('profile must not open an endpoint'))
    selected = binding(kind)
    profile = NativeCommandProfile.from_binding(selected)
    assert profile.binding['transport']['kind'] == kind
    assert profile.binding['send_timeout'] == 2
    assert profile.binding['max_lease_seconds'] is None
    assert profile.binding['command_profile']['max_request_bytes'] is None
    assert profile.binding['command_profile']['max_response_bytes'] is None
    assert profile.local_stop_payload is None


def test_profile_is_frozen_defensive_and_digest_binds_public_profile_and_transport():
    original = binding()
    profile = NativeCommandProfile.from_binding(original)
    normalized = profile.binding
    expected = hashlib.sha256(canonical({key: normalized[key] for key in ('command_profile', 'transport')})).hexdigest()
    assert profile.digest == expected
    original['transport']['port'] = 'changed'
    normalized['transport']['port'] = 'changed'
    assert profile.binding['transport']['port'] == 'loop://'
    with pytest.raises(FrozenInstanceError):
        profile.digest = 'changed'
    changed = binding()
    changed['transport']['baudrate'] += 1
    assert NativeCommandProfile.from_binding(changed).digest != profile.digest


@pytest.mark.parametrize('encoded,expected', [(data('é\r\n\x00'), 'é\r\n\x00'.encode()),
    (data('00 ff 0A', 'hex'), b'\x00\xff\x0a'), (data('AP8K', 'base64'), b'\x00\xff\x0a'), (data(''), b'')])
def test_explicit_encodings_preserve_exact_bytes(encoded, expected):
    assert decode_bytes(encoded) == expected
    assert NativeCommandProfile.decode_bytes(encoded) == expected


@pytest.mark.parametrize('encoded', [{'value': 'x'}, data('abc', 'hex'), data('%%%', 'base64'),
                                  data('x', 'unknown'), data(10), {'encoding': 'hex', 'value': '00', 'suffix': '\n'}])
def test_invalid_encodings_are_rejected(encoded):
    with pytest.raises(CommandProfileError): decode_bytes(encoded)


def test_raw_query_requires_bound_read_framing_and_never_adds_a_delimiter():
    selected = binding()
    profile = NativeCommandProfile.from_binding(selected)
    command = {'operation': 'serial.query', 'data': data('MEAS?')}
    with pytest.raises(CommandProfileError, match='read'):
        profile.validate_payload(command)
    selected['transport']['read'] = {'kind': 'delimiter', 'delimiter': data('0d0a', 'hex'), 'max_bytes': None}
    profile = NativeCommandProfile.from_binding(selected)
    assert profile.validate_payload(command) == command
    assert decode_bytes(profile.validate_payload(command)['data']) == b'MEAS?'
    command['data']['value'] = 'changed'
    assert profile.binding['transport']['read']['delimiter'] == data('0d0a', 'hex')


@pytest.mark.parametrize('kind', ['serial', 'tcp'])
@pytest.mark.parametrize('read', [None, {'kind': 'fixed_bytes', 'count': 1},
    {'kind': 'delimiter', 'delimiter': {'encoding': 'hex', 'value': '00'}}])
@pytest.mark.parametrize('selection,without_read,with_read', [
    (None, ['write'], ['write', 'query']),
    ([], [], []),
    (['query'], [], ['query']),
    (['write'], ['write'], ['write']),
    (['query', 'write'], ['write'], ['query', 'write']),
])
def test_descriptor_schema_matches_configured_operation_admission(kind, read, selection, without_read, with_read):
    jsonschema = pytest.importorskip('jsonschema')
    selected = binding(kind)
    selected['transport']['read'] = deepcopy(read)
    selected['command_profile']['allowed_operations'] = (
        None if selection is None else [kind + '.' + suffix for suffix in selection])
    profile = NativeCommandProfile.from_binding(selected)
    frozen_binding, digest = profile.binding, profile.digest
    description = profile.describe()
    validator = jsonschema.Draft202012Validator(description['payload_schema'])
    expected = [kind + '.' + suffix for suffix in (without_read if read is None else with_read)]
    for operation in (kind + '.write', kind + '.query'):
        command = {'operation': operation, 'data': data('READ?')}
        assert validator.is_valid(command) is (operation in expected)
        if operation in expected:
            assert profile.validate_payload(command) == command
        else:
            with pytest.raises(CommandProfileError):
                profile.validate_payload(command)
    assert description['implemented_operations'] == [kind + '.write', kind + '.query']
    assert description['selected_operations'] == selected['command_profile']['allowed_operations']
    assert description['effective_operations'] == description['allowed_operations'] == expected
    if not expected:
        assert description['payload_schema'] == {'not': {}}
    if read is None and (selection is None or 'query' in selection):
        assert description['unavailable_operations'][kind + '.query'] == {
            'reason': 'missing_configuration', 'required_fields': ['transport.read']}
    else:
        assert description['unavailable_operations'] == {}
    assert profile.binding == frozen_binding and profile.digest == digest


@pytest.mark.parametrize('operations', [None, [], ['opaque_program']])
def test_custom_descriptor_preserves_opaque_operator_operation_selection(operations):
    jsonschema = pytest.importorskip('jsonschema')
    selected = binding('custom')
    selected['command_profile']['allowed_operations'] = operations
    profile = NativeCommandProfile.from_binding(selected)
    description = profile.describe()
    validator = jsonschema.Draft202012Validator(description['payload_schema'])
    assert description['implemented_operations'] is None
    assert description['selected_operations'] == operations
    assert description['effective_operations'] == description['allowed_operations'] == operations
    assert description['unavailable_operations'] == {}
    for operation in ('opaque_program', 'another_operator_operation'):
        command = {'operation': operation, 'arbitrary_native_options': [True, '9007199254740993', -3.5]}
        allowed = operations is None or operation in operations
        assert validator.is_valid(command) is allowed
        if allowed:
            assert profile.validate_payload(command) == command
        else:
            with pytest.raises(CommandProfileError):
                profile.validate_payload(command)
    assert profile.binding['command_profile']['allowed_operations'] == operations


def test_descriptor_operation_disclosures_are_defensive_and_keep_claims_unverified():
    selected = binding()
    selected['command_profile']['capability_claims'] = {'device_fencing': True}
    profile = NativeCommandProfile.from_binding(selected)
    frozen_binding, digest = profile.binding, profile.digest
    description = profile.describe()
    description['implemented_operations'].clear()
    description['effective_operations'].clear()
    description['selected_operations'] = ['serial.query']
    description['unavailable_operations']['serial.query']['required_fields'].clear()
    description['capability_claims']['device_fencing'] = False
    fresh = profile.describe()
    assert fresh['implemented_operations'] == ['serial.write', 'serial.query']
    assert fresh['effective_operations'] == ['serial.write']
    assert fresh['selected_operations'] is None
    assert fresh['unavailable_operations']['serial.query']['required_fields'] == ['transport.read']
    assert fresh['capability_claims'] == {'device_fencing': True}
    assert fresh['qualification']['device_capabilities'] == 'operator_claims_only'
    assert profile.binding == frozen_binding and profile.digest == digest


@pytest.mark.parametrize('read', [{'kind': 'fixed_bytes', 'count': 0}, {'kind': 'fixed_bytes', 'count': 4_000_000_000},
                                {'kind': 'delimiter', 'delimiter': data('AA', 'hex'), 'max_bytes': 0}])
def test_read_settings_keep_author_selected_operational_sizes(read):
    actual = validate_read(read)
    assert all(actual[key] == value for key, value in read.items())


@pytest.mark.parametrize('read', [{'kind': 'line'}, {'kind': 'delimiter', 'delimiter': data('')},
                                {'kind': 'fixed_bytes', 'count': True}, {'kind': 'fixed_bytes', 'count': -1}])
def test_ambiguous_or_invalid_read_framing_is_rejected(read):
    with pytest.raises(CommandProfileError): validate_read(read)


@pytest.mark.parametrize('seconds', [0.00001, 31, 100_000_000])
def test_positive_finite_timeouts_are_selected_without_legacy_clamps(seconds):
    profile = NativeCommandProfile.from_binding(binding(send_timeout=seconds, max_lease_seconds=seconds * 2))
    assert profile.binding['send_timeout'] == seconds
    assert profile.binding['max_lease_seconds'] == seconds * 2


@pytest.mark.parametrize('seconds', [0, -1, True, float('nan'), float('inf')])
def test_invalid_timeout_is_rejected(seconds):
    with pytest.raises(CommandProfileError): NativeCommandProfile.from_binding(binding(send_timeout=seconds))


def test_operator_stop_bypasses_only_the_command_operation_allowlist():
    selected = binding(local_stop={'operation': 'serial.write', 'data': data('STOP\n')})
    selected['transport']['read'] = {'kind': 'fixed_bytes', 'count': 1}
    selected['command_profile']['allowed_operations'] = ['serial.query']
    profile = NativeCommandProfile.from_binding(selected)
    stop = profile.local_stop_payload
    with pytest.raises(CommandProfileError, match='allow'):
        profile.validate_payload(stop)
    assert profile.validate_payload(stop, operator_action=True) == stop
    stop['data']['value'] = 'changed'
    assert profile.local_stop_payload['data']['value'] == 'STOP\n'
    assert profile.binding['command_profile']['allowed_operations'] == ['serial.query']


def test_selected_request_budget_counts_decoded_bytes_without_changing_the_payload():
    selected = binding()
    selected['command_profile']['max_request_bytes'] = 2
    profile = NativeCommandProfile.from_binding(selected)
    payload = {'operation': 'serial.write', 'data': data('AP8=', 'base64')}
    assert profile.validate_payload(payload) == payload
    with pytest.raises(CommandProfileError, match='budget'):
        profile.validate_payload({'operation': 'serial.write', 'data': data('éé')})


@pytest.mark.parametrize('operation,payload', [
    ('write_register', {'address': 65535, 'value': 65535}),
    ('write_registers', {'address': 65000, 'values': [0, 65535] * 61 + [123]}),
    ('write_coil', {'address': 65535, 'value': False}),
    ('write_coils', {'address': 60000, 'values': [False, True] * 984}),
    ('read_holding_registers', {'address': 0, 'count': 125}),
    ('read_input_registers', {'address': 0, 'count': 125}),
    ('read_coils', {'address': 0, 'count': 2000}),
    ('read_discrete_inputs', {'address': 0, 'count': 2000}),
])
def test_standard_modbus_operations_preserve_protocol_widths_and_full_adus(operation, payload):
    profile = NativeCommandProfile.from_binding(binding('modbus'))
    command = {'operation': 'modbus.' + operation, **payload}
    assert profile.validate_payload(command) == command
    assert profile.binding['transport']['device_id'] == 255


@pytest.mark.parametrize('payload', [
    {'operation': 'modbus.write_register', 'address': 0, 'value': 65536},
    {'operation': 'modbus.write_register', 'address': 0, 'value': True},
    {'operation': 'modbus.write_coil', 'address': 0, 'value': 1},
    {'operation': 'modbus.write_registers', 'address': 0, 'values': [0] * 124},
    {'operation': 'modbus.write_coils', 'address': 0, 'values': [True] * 1969},
    {'operation': 'modbus.read_holding_registers', 'address': 0, 'count': 126},
    {'operation': 'modbus.read_coils', 'address': 0, 'count': 2001},
    {'operation': 'modbus.read_input_registers', 'address': 65535, 'count': 2},
    {'operation': 'modbus.write_registers', 'address': 0, 'values': []},
])
def test_modbus_protocol_limits_fail_visibly_without_chunking_or_scaling(payload):
    with pytest.raises(CommandProfileError): NativeCommandProfile.from_binding(binding('modbus')).validate_payload(payload)


@pytest.mark.parametrize('unit', [0, 247, 255])
def test_modbus_unit_identifier_is_exactly_operator_selected(unit):
    selected = binding('modbus')
    selected['transport'].update(device_id=unit, framing='rtu_over_tcp')
    assert NativeCommandProfile.from_binding(selected).binding['transport']['device_id'] == unit


def test_mqtt_requires_explicit_qos_retain_and_preserves_selected_topic_and_payload():
    profile = NativeCommandProfile.from_binding(binding('mqtt'))
    command = {'operation': 'mqtt.publish', 'data': data('00ff00', 'hex'), 'qos': 2, 'retain': True}
    assert profile.validate_payload(command) == command
    assert profile.binding['transport']['topic'] == 'selected/control'
    for key in ('qos', 'retain'):
        incomplete = deepcopy(command)
        incomplete.pop(key)
        with pytest.raises(CommandProfileError): profile.validate_payload(incomplete)


@pytest.mark.parametrize('payload', [
    {'arbitration_id': 2047, 'data': data('ff' * 8, 'hex'), 'is_extended_id': False, 'is_fd': False},
    {'arbitration_id': 536870911, 'data': data('00' * 64, 'hex'), 'is_extended_id': True, 'is_fd': True,
     'bitrate_switch': True, 'error_state_indicator': False, 'is_error_frame': False},
    {'arbitration_id': 7, 'data': data(''), 'is_extended_id': False, 'is_fd': False, 'is_remote_frame': True, 'dlc': 8},
])
def test_native_can_frames_preserve_selected_sdk_flags_and_protocol_lengths(payload):
    command = {'operation': 'can.send', **payload}
    assert NativeCommandProfile.from_binding(binding('can')).validate_payload(command) == command


@pytest.mark.parametrize('change', [{'arbitration_id': 2048}, {'data': data('ff' * 9, 'hex')},
    {'is_fd': True, 'data': data('ff' * 65, 'hex')}, {'bitrate_switch': True},
    {'is_remote_frame': True, 'data': data('01', 'hex')}])
def test_invalid_can_protocol_frames_are_rejected(change):
    payload = {'operation': 'can.send', 'arbitration_id': 1, 'data': data(''), 'is_extended_id': False, 'is_fd': False, **change}
    with pytest.raises(CommandProfileError): NativeCommandProfile.from_binding(binding('can')).validate_payload(payload)


def test_custom_adapter_keeps_arbitrary_finite_operator_json_and_explicit_identity():
    profile = NativeCommandProfile.from_binding(binding('custom'))
    payload = {'operation': 'operator_selected_program', 'experiment': {'negative': -999999999999,
               'units': 'kHz', 'samples': [None, True, 0.00000000000003, 'original\x00bytes']}}
    assert profile.validate_payload(payload) == payload
    assert profile.binding['transport']['adapter_config'] == binding('custom')['transport']['adapter_config']
    payload['experiment']['samples'].append(10)
    assert len(profile.validate_payload({'operation': 'other', 'payload': []})['payload']) == 0


@pytest.mark.parametrize('payload', [{'operation': 'x', 'x': float('nan')}, {'operation': 'x', 1: 'key'},
    {'operation': 'x', 'x': b'bytes'}, {'operation': 'x', 'x': (1, 2)}, {'x': 1}])
def test_custom_payload_must_be_an_exact_finite_json_object(payload):
    with pytest.raises(CommandProfileError): NativeCommandProfile.from_binding(binding('custom')).validate_payload(payload)


@pytest.mark.parametrize('status', sorted(COMPLETED_NATIVE_STATUSES - {'application_reported'}))
def test_completed_receipts_keep_delivery_scope_without_physical_confirmation(status):
    profile = NativeCommandProfile.from_binding(binding())
    request, receipt = request_and_receipt(profile, status=status)
    assert profile.validate_receipt(request, receipt) == receipt
    receipt['status'] = 'changed'
    assert profile.validate_receipt(request, request_and_receipt(profile, status=status)[1])['status'] == status


@pytest.mark.parametrize('change', [{'status': 'unknown'}, {'status': 'confirmed'}, {'epoch': True},
    {'epoch': 8}, {'command_id': 'other'}, {'endpoint_id': 'other'}, {'profile_digest': '0' * 64},
    {'device_application_confirmed': True}, {'physical_outcome': 'stopped'}, {'device_application_confirmed': 0}])
def test_mismatched_uncertain_or_overstated_receipts_cannot_complete_a_command(change):
    profile = NativeCommandProfile.from_binding(binding())
    request, receipt = request_and_receipt(profile, **change)
    with pytest.raises(CommandProfileError): profile.validate_receipt(request, receipt)


def test_custom_application_confirmation_is_an_adapter_claim_only():
    profile = NativeCommandProfile.from_binding(binding('custom'))
    request, receipt = request_and_receipt(profile, status='application_reported', device_application_confirmed=True,
                                         application_confirmation_scope='adapter_claim')
    assert profile.validate_receipt(request, receipt) == receipt
    receipt.pop('application_confirmation_scope')
    with pytest.raises(CommandProfileError): profile.validate_receipt(request, receipt)


@pytest.mark.parametrize('kind', ['serial', 'tcp', 'modbus', 'mqtt', 'can'])
def test_builtin_receipts_cannot_interpret_device_bytes_as_an_application_report(kind):
    profile = NativeCommandProfile.from_binding(binding(kind))
    request, receipt = request_and_receipt(profile, status='application_reported',
        device_application_confirmed=False, application_confirmation_scope='adapter_claim')
    with pytest.raises(CommandProfileError, match='custom'):
        profile.validate_receipt(request, receipt)


@pytest.mark.parametrize('confirmed', [False, True])
def test_custom_application_report_always_requires_adapter_claim_scope(confirmed):
    profile = NativeCommandProfile.from_binding(binding('custom'))
    request, receipt = request_and_receipt(profile, status='application_reported',
        device_application_confirmed=confirmed)
    for scope in (None, 'device_verified'):
        receipt['application_confirmation_scope'] = scope
        with pytest.raises(CommandProfileError, match='adapter claim'):
            profile.validate_receipt(request, receipt)
    receipt['application_confirmation_scope'] = 'adapter_claim'
    assert profile.validate_receipt(request, receipt) == receipt


@pytest.mark.parametrize('kind', ['serial', 'tcp', 'modbus', 'mqtt', 'can', 'custom'])
def test_description_advertises_only_completed_statuses_for_the_selected_transport(kind):
    profile = NativeCommandProfile.from_binding(binding(kind))
    expected = COMPLETED_NATIVE_STATUSES if kind == 'custom' else COMPLETED_NATIVE_STATUSES - {'application_reported'}
    assert profile.describe()['completed_statuses'] == sorted(expected)


def test_public_command_format_matches_the_native_request_envelope():
    from tinystudio.lab import command_profiles
    profile = NativeCommandProfile.from_binding(binding())
    assert getattr(command_profiles, 'COMMAND_FORMAT', None) == request_and_receipt(profile)[0]['protocol']


def test_description_is_public_typed_and_defensive_without_promising_device_capabilities():
    selected = binding('mqtt')
    selected['command_profile']['capability_claims'] = {'persistent_fencing': True, 'physical_stop': 'operator-declared'}
    profile = NativeCommandProfile.from_binding(selected)
    description = profile.describe()
    assert description['profile_digest'] == profile.digest
    assert description['transport_kind'] == 'mqtt'
    assert description['allowed_operations'] == ['mqtt.publish']
    assert description['payload_schema']['oneOf'][0]['properties']['qos']['enum'] == [0, 1, 2]
    assert description['capability_claims'] == selected['command_profile']['capability_claims']
    assert description['qualification']['device_capabilities'] == 'operator_claims_only'
    assert description['qualification']['physical_outcome'] == 'unverified'
    assert 'host' not in description and 'topic' not in description
    description['capability_claims']['physical_stop'] = 'changed'
    assert profile.describe()['capability_claims']['physical_stop'] == 'operator-declared'


def test_native_limits_cannot_be_silently_ignored_as_legacy_typed_parameter_enforcement():
    selected = binding(limits={'voltage': {'minimum': 0, 'maximum': 1, 'unit': 'V'}})
    with pytest.raises(CommandProfileError, match='limits'): NativeCommandProfile.from_binding(selected)
    for empty in (None, {}):
        assert 'limits' not in NativeCommandProfile.from_binding(binding(limits=empty)).binding


@pytest.mark.parametrize('change', [{'command_profile': {'format': 'protocol-1'}}, {'transport': {'kind': 'fixture'}},
    {'endpoint_id': ''}, {'send_timeout': None}, {'max_lease_seconds': False}, {'unknown': 1}])
def test_invalid_native_binding_fails_without_guessing_or_falling_back(change):
    with pytest.raises(CommandProfileError): NativeCommandProfile.from_binding(binding(**change))


@pytest.mark.parametrize('kind', ['serial', 'custom'])
def test_empty_operation_allowlist_describes_a_valid_reject_all_schema(kind):
    selected = binding(kind)
    selected['command_profile']['allowed_operations'] = []
    profile = NativeCommandProfile.from_binding(selected)
    assert profile.describe()['payload_schema'] == {'not': {}}
    with pytest.raises(CommandProfileError):
        profile.validate_payload({'operation': 'anything'})


def test_custom_payload_schema_describes_the_operator_selected_operation_allowlist():
    selected = binding('custom')
    selected['command_profile']['allowed_operations'] = ['selected_program', 'selected_stop']
    profile = NativeCommandProfile.from_binding(selected)
    assert profile.describe()['payload_schema']['properties']['operation']['enum'] == ['selected_program', 'selected_stop']


def test_public_helpers_report_consistent_validation_errors_for_invalid_json():
    with pytest.raises(CommandProfileError): validate_transport({'kind': []})
    with pytest.raises(CommandProfileError):
        NativeCommandProfile.from_binding(binding()).validate_payload({'operation': 'serial.write', 'data': data('\ud800')})


def test_can_remote_and_error_frames_are_mutually_exclusive_native_sdk_flags():
    with pytest.raises(CommandProfileError):
        NativeCommandProfile.from_binding(binding('can')).validate_payload({'operation': 'can.send',
            'arbitration_id': 1, 'data': data(''), 'is_extended_id': False, 'is_fd': False,
            'is_remote_frame': True, 'is_error_frame': True})


@pytest.mark.parametrize('framing,maximum', [('tcp', 17), ('rtu_over_tcp', 13)])
def test_selected_modbus_request_budget_counts_the_exact_native_adu(framing, maximum):
    selected = binding('modbus')
    selected['transport']['framing'] = framing
    selected['command_profile']['max_request_bytes'] = maximum
    payload = {'operation': 'modbus.write_registers', 'address': 12, 'values': [1, 65535]}
    assert NativeCommandProfile.from_binding(selected).validate_payload(payload) == payload
    selected['command_profile']['max_request_bytes'] = maximum - 1
    with pytest.raises(CommandProfileError, match='budget'):
        NativeCommandProfile.from_binding(selected).validate_payload(payload)


def test_native_identity_lengths_are_unbounded_unless_an_operator_selects_a_budget():
    selected = binding(endpoint_id='endpoint-' + 'x' * 10_000, operator_id='operator-' + 'y' * 10_000)
    profile = NativeCommandProfile.from_binding(selected)
    assert profile.binding['endpoint_id'] == selected['endpoint_id']
    assert profile.binding['operator_id'] == selected['operator_id']
    assert profile.binding['command_profile']['max_identity_chars'] is None
    assert profile.describe()['max_identity_chars'] is None


def test_selected_identity_character_budget_is_enforced_advertised_and_digest_bound():
    selected = binding(endpoint_id='endpoint', operator_id='operator')
    selected['command_profile']['max_identity_chars'] = 8
    profile = NativeCommandProfile.from_binding(selected)
    assert profile.describe()['max_identity_chars'] == 8
    assert profile.binding['command_profile']['max_identity_chars'] == 8
    larger = deepcopy(selected)
    larger['command_profile']['max_identity_chars'] = 9
    assert NativeCommandProfile.from_binding(larger).digest != profile.digest
    for identity in ('endpoint_id', 'operator_id'):
        longer = deepcopy(selected)
        longer[identity] = 'x' * 9
        with pytest.raises(CommandProfileError, match='identity.*budget'):
            NativeCommandProfile.from_binding(longer)


@pytest.mark.parametrize('maximum', [0, -1, True, 1.5, '128'])
def test_identity_budget_must_be_a_selected_positive_integer(maximum):
    selected = binding()
    selected['command_profile']['max_identity_chars'] = maximum
    with pytest.raises(CommandProfileError): NativeCommandProfile.from_binding(selected)


def test_ownership_key_is_an_explicit_namespace_claim_without_an_automatic_default():
    assert 'ownership_key' not in NativeCommandProfile.from_binding(binding()).binding
    selected = binding(ownership_key='operator-shared-device-alias')
    profile = NativeCommandProfile.from_binding(selected)
    assert profile.binding['ownership_key'] == selected['ownership_key']
    assert profile.describe()['ownership_key'] == selected['ownership_key']
    assert profile.describe()['qualification']['ownership_namespace'] == 'operator_claim_only_not_single_writer_proof'
    for invalid in ('', None, 1):
        with pytest.raises(CommandProfileError): NativeCommandProfile.from_binding(binding(ownership_key=invalid))
