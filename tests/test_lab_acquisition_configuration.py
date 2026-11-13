"""Acquisition uses authored transport choices and host-selected budgets."""
import json
from types import SimpleNamespace

import pytest

from tests.test_lab_acquisition import Clock, run, spec
from tinystudio.lab.acquisition import execute_acquisition


@pytest.mark.parametrize('port', ['COM3', '/run/operator-owned/device',
                                  'rfc2217://127.0.0.1:5111', 'socket://127.0.0.1:5112', 'loop://'])
def test_operator_serial_bindings_are_not_restricted_to_dev_paths(tmp_path, port):
    definition = spec(samples=1)
    definition['_runtime_grants']['telemetry']['endpoint']['port'] = port
    result, rows, raw, reader, calls = run(tmp_path, definition, [{'wire': b'{"value":2}\n'}])
    assert rows[0]['value'] == 2 and result['records'] == 1
    assert calls[0][0]['endpoint']['port'] == port and reader.closed


def test_explicit_large_frames_and_submillisecond_timeout_are_not_clamped(tmp_path):
    definition = spec(samples=1, timeout=.0002, max_frame_bytes=1024 ** 2)
    result, rows, raw, reader, calls = run(tmp_path, definition, [{'wire': b'{"value":-0.125}\n'}])
    assert calls[0][1:] == (.0002, 1024 ** 2)
    assert rows[0]['value'] == -.125
    assert 'read_deadline_exceeded' in rows[0]['quality_flags']
    assert result['qualification']['resource_plan']['selected']['max_frame_bytes'] == 1024 ** 2


def test_tcp_unit_identifier_255_is_preserved(tmp_path):
    definition = spec(engine='acquisition.modbus', samples=1)
    binding = definition['_runtime_grants']['telemetry']
    binding['read']['device_id'] = 255
    result, rows, raw, reader, calls = run(tmp_path, definition, [{'wire': b'captured', 'registers': [23, 24]}])
    assert rows[0]['value'] == 23
    assert calls[0][0]['read']['device_id'] == 255


@pytest.mark.parametrize('selection', [{'samples': 0}, {'duration': 0}, {'samples': 100_001, 'duration': 0}])
def test_explicit_no_observation_run_keeps_empty_evidence_without_opening_io(tmp_path, selection):
    definition = spec(**selection)
    definition['_runtime_grants']['telemetry']['limits'] = {}
    def forbidden(*args, **kwargs):
        pytest.fail('a zero-observation plan must not open a device')
    result = execute_acquisition(definition, tmp_path, lambda _: None, lambda: False,
        transport_factory=forbidden, clock=Clock())
    assert result['records'] == result['metrics']['records'] == 0
    assert (tmp_path / 'raw.jsonl').read_bytes() == b''
    assert (tmp_path / 'processed.jsonl').read_bytes() == b''
    assert result['qualification']['resource_plan']['scientific_validity_rule'] is False


def test_operator_resource_failure_precedes_io(tmp_path):
    definition = spec(samples=1, max_frame_bytes=4097)
    definition['_runtime_grants']['telemetry']['limits']['max_frame_bytes'] = 4096
    def forbidden(*args, **kwargs):
        pytest.fail('operator resource budget must be enforced before opening')
    with pytest.raises(ValueError, match='operator'):
        execute_acquisition(definition, tmp_path, lambda _: None, lambda: False,
            transport_factory=forbidden, clock=Clock())


def test_zero_timeout_mqtt_poll_processes_available_native_events():
    from tinystudio.lab.acquisition import MQTTReader
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def connect(self, *args, **kwargs): return 0
        def disconnect(self): pass
        def loop(self, timeout):
            calls.append(timeout)
            self.on_message(self, None, SimpleNamespace(topic='measurement', payload=b'{"value":7}', retain=False, qos=0))
            return 0
    binding = {'endpoint': {'host': '127.0.0.1', 'port': 1883, 'topic': 'measurement', 'tls': False}}
    library = SimpleNamespace(Client=Client, CallbackAPIVersion=SimpleNamespace(VERSION2=2))
    reader = MQTTReader(binding, .1, 128, library=library, clock=Clock())
    try:
        frame = reader.read(0)
    finally:
        reader.close()
    assert calls == [0]
    assert frame['wire'] == b'{"value":7}'
