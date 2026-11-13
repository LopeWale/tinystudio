"""Frames and structured payloads remain usable without fabricated scalars."""
import base64

import pytest

from tests.test_lab_acquisition import run, spec


def can_spec(**params):
    definition = spec(engine='acquisition.can', samples=1, **params)
    definition['_runtime_grants']['telemetry']['endpoint'] = {'interface': 'virtual', 'channel': 'owned-fixture'}
    definition['_runtime_grants']['telemetry']['source_clock'] = {}
    return definition


def test_can_frame_capture_retains_identity_without_inventing_a_numeric_outcome(tmp_path):
    payload = b'\xaa\xfb\x2e'
    frame = {'wire': payload, 'wire_scope': 'can_application_payload',
             'can_frame': {'arbitration_id': 321, 'is_extended_id': False, 'dlc': 3}}
    result, rows, raw, *_ = run(tmp_path, can_spec(), [frame])
    assert rows[0]['value'] is None and rows[0]['observed'] is False
    assert rows[0]['quality'] == 'frame_only'
    assert raw[0]['observed'] is True
    assert base64.b64decode(raw[0]['wire_base64']) == payload
    assert raw[0]['decoded_payload']['frame']['arbitration_id'] == 321


@pytest.mark.parametrize('decode,order,payload,offset,expected', [
    ('int16', 'big', b'\xaa\xfb\x2e', 1, -1234),
    ('float32', 'little', b'\x00\x00\x00\x3f', 0, .5),
    ('uint32', 'big', b'\x80\x00\x00\x01', 0, 2147483649),
])
def test_can_measurement_uses_only_the_authored_payload_mapping(tmp_path, decode, order, payload, offset, expected):
    frame = {'wire': payload, 'can_frame': {'arbitration_id': 321}}
    _, rows, raw, *_ = run(tmp_path, can_spec(decode=decode, byte_order=order, byte_offset=offset), [frame])
    assert rows[0]['value'] == expected and rows[0]['observed'] is True
    assert base64.b64decode(raw[0]['wire_base64']) == payload


def test_structured_serial_payload_keeps_multiple_outcomes_for_later_curation(tmp_path):
    payload = b'{"temperature":21.2,"duty":0.1,"status":"ready"}\n'
    result, rows, raw, *_ = run(tmp_path, spec(samples=1, decode='json_object'), [{'wire': payload}])
    assert rows[0]['quality'] == 'frame_only' and rows[0]['value'] is None
    assert raw[0]['decoded_payload'] == {'temperature': 21.2, 'duty': .1, 'status': 'ready'}
    assert base64.b64decode(raw[0]['wire_base64']) == payload


@pytest.mark.parametrize('payload', [b'{"value":1,"extra":NaN}\n',
    b'{"value":1,"extra":1e999}\n', b'{"value":1,"value":2}\n'])
def test_invalid_or_ambiguous_json_is_recorded_without_losing_original_bytes(tmp_path, payload):
    result, rows, raw, reader, _ = run(tmp_path, spec(samples=1), [{'wire': payload}])
    assert result['records'] == 1 and reader.closed
    assert rows[0]['quality'] == 'invalid' and rows[0]['value'] is None
    assert base64.b64decode(raw[0]['wire_base64']) == payload
