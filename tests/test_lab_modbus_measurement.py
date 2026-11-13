"""Read-only measurement binding uses owned independent Modbus wire oracles."""
from copy import deepcopy
import base64
import hashlib
import json
import socket
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from tinystudio.lab.acquisition import ModbusReader, _binding, _decode
from tinystudio.lab.engine import ExecutionCancelled


def binding(read=None, **kwargs):
    return {'binding_id': 'selected-instrument', 'engine': 'acquisition.modbus', 'read_only': True,
            'endpoint': {'host': '127.0.0.1', 'port': 1502, 'framing': 'tcp', 'allow_dns': False},
            'read': read or {'kind': 'holding_registers', 'device_id': 255, 'sunspec': {
                'scan': {'base_addresses': [1769], 'device_id': 255},
                'point': {'model_id': 64003, 'instance': 1, 'offset': 2, 'count': 2}}}, **kwargs}


def spec(grant, **parameters):
    return {'definition': {'engine': 'acquisition.modbus', 'operating_mode': 'acquisition',
                           'parameters': {'binding_id': grant['binding_id'], **parameters}},
            '_runtime_grants': {'bindings': {grant['binding_id']: grant}}}


def test_sunspec_read_binding_is_validated_without_opening_a_client():
    selected = binding()
    before = deepcopy(selected)
    assert _binding(spec(selected))['read'] == selected['read']
    assert selected == before


@pytest.mark.parametrize('change', [
    lambda read: read.update(address=0, count=1),
    lambda read: read['sunspec'].update(address=0),
    lambda read: read['sunspec']['scan'].update(base_addresses=[]),
    lambda read: read['sunspec']['scan'].update(register_kind='input_registers'),
    lambda read: read['sunspec']['scan'].update(device_id=7),
    lambda read: read['sunspec']['scan'].update(guess_standard_address=True),
    lambda read: read['sunspec']['point'].update(count=126),
    lambda read: read['sunspec']['point'].update(instance=-1),
    lambda read: read['sunspec']['point'].update(offset=False),
    lambda read: read['sunspec']['point'].update(scale_factor=-2),
    lambda read: read.update(kind='coils'),
])
def test_invalid_sunspec_authority_refused_before_client_creation(change):
    selected = binding()
    change(selected['read'])
    clients = []
    with pytest.raises(ValueError):
        ModbusReader(selected, .5, 2, library=SimpleNamespace(ModbusTcpClient=lambda *a, **k: clients.append(a)))
    assert not clients
    with pytest.raises(ValueError):
        _binding(spec(selected))


def test_authored_sunspec_point_or_endpoint_cannot_override_operator_binding():
    selected = binding()
    override = deepcopy(selected['read'])
    override['sunspec']['point']['offset'] = 0
    with pytest.raises(ValueError, match='differs'):
        _binding(spec(selected, read=override))
    with pytest.raises(ValueError, match='differs'):
        _binding(spec(selected, endpoint={**selected['endpoint'], 'port': 2502}))


def test_operator_model_budget_is_explicit_and_inherited_without_mutating_read():
    selected = binding(limits={'max_models': 3})
    before = deepcopy(selected)
    assert ModbusReader.validate_read(selected['read'], selected['limits']).max_models == 3
    assert selected == before
    for requested in (None, 4):
        selected['read']['sunspec']['scan']['max_models'] = requested
        calls = []
        with pytest.raises(ValueError, match='max_models'):
            ModbusReader(selected, .5, 0, library=SimpleNamespace(ModbusTcpClient=lambda *a, **k: calls.append(a)))
        assert not calls


@pytest.mark.parametrize('maximum', [None, 0, 2])
def test_owned_tcp_resolves_arbitrary_base_repeated_model_instance_and_reads_point(maximum):
    pytest.importorskip('pymodbus.client')
    from test_lab_device_discovery import mapping, owned_register_server

    selected = binding()
    before = deepcopy(selected)
    words = mapping(1769, [(64003, [41]), (777, [90, 91, 92]), (64003, [11, 12, 0x1234, 0x5678, 15])])
    with owned_register_server(words) as (port, requests):
        selected['endpoint']['port'] = port
        reader = ModbusReader(selected, .5, maximum)
        try:
            assert reader.source_binding['resolved_read'] == {
                'kind': 'holding_registers', 'address': 1783, 'count': 2, 'device_id': 255}
            source = deepcopy(reader.source_binding)
            before_measurements = len(requests)
            first = reader.read(.5)
            second = reader.read(.5)
            assert first['registers'] == second['registers'] == [0x1234, 0x5678]
            assert _decode(first, {'decode': 'uint32'}, 'acquisition.modbus')[0] == 0x12345678
            assert source == reader.source_binding, 'discovery and resolution occur once at open'
            assert len(requests) == before_measurements + 2
            assert requests[-2:] == [(255, 3, 1783, 2), (255, 3, 1783, 2)]
            assert len(first['trace_packets']) == 2
            for trace in [*source['discovery_wire'], *first['trace_packets']]:
                captured = base64.b64decode(trace['prefix_base64'])
                assert trace['captured_bytes'] == len(captured)
                assert maximum is None or len(captured) <= maximum
                assert trace['truncated'] is (len(captured) < trace['size'])
                assert len(trace['sha256']) == 64 and trace['wall_time'] > 0 and trace['monotonic'] > 0
            if maximum == 0:
                assert first['wire'] == first['request_wire'] == b''
                assert first['evidence_truncated'] is True and not first.get('truncated', False)
                assert first['trace_capture']['observed_buffer_bytes']['rx'] > 0
        finally:
            reader.close()
    before['endpoint']['port'] = port
    assert selected == before
    assert source['selection'] == 'sunspec' and source['discovery']['complete'] is True
    assert source['resolution']['instance'] == 1 and source['resolution']['model_id'] == 64003
    assert source['requested_read'] == selected['read']
    json.dumps(source, allow_nan=False)


@pytest.mark.parametrize('failure', ['ambiguous', 'not_found', 'offset', 'partial'])
def test_owned_tcp_resolution_errors_retain_discovery_and_close_connection(failure):
    pytest.importorskip('pymodbus.client')
    from test_lab_device_discovery import mapping, owned_register_server

    selected = binding()
    selected['read']['sunspec']['point'] = {'model_id': 64003, 'instance': 0, 'offset': 0, 'count': 1}
    words = mapping(1769, [(64003, [11, 12])])
    match = {'ambiguous': 'ambiguous', 'not_found': 'not discovered', 'offset': 'payload', 'partial': 'complete'}[failure]
    if failure == 'ambiguous':
        selected['read']['sunspec']['scan']['base_addresses'].append(3001)
        words.update(mapping(3001, [(64003, [88])]))
    elif failure == 'not_found':
        selected['read']['sunspec']['point']['model_id'] = 65000
    elif failure == 'offset':
        selected['read']['sunspec']['point']['offset'] = 2
    else:
        selected['read']['sunspec']['scan']['max_models'] = 0
    with owned_register_server(words) as (port, requests):
        selected['endpoint']['port'] = port
        with pytest.raises(ValueError, match=match) as caught:
            ModbusReader(selected, .5, 1)
    source = caught.value.source_binding
    assert source['resolved_read'] is None and source['resolution'] is None
    assert source['discovery'] and source['discovery_wire']
    assert all(unit == 255 and function == 3 for unit, function, _, _ in requests)
    json.dumps(source, allow_nan=False)


@pytest.mark.parametrize('maximum', [None, 0, 3])
def test_direct_read_stays_compatible_and_capture_limits_do_not_change_protocol(maximum):
    calls = []

    class Client:
        def __init__(self, host, **kwargs):
            self.trace = kwargs['trace_packet']

        def connect(self):
            return True

        def read_input_registers(self, *, address, count, device_id):
            calls.append((address, count, device_id))
            tx, rx = b'exact request bytes', b'exact response bytes'
            assert self.trace(True, tx) is tx and self.trace(False, rx) is rx
            return SimpleNamespace(registers=[0xbe00, 0], isError=lambda: False)

        def close(self):
            calls.append('closed')

    selected = binding({'kind': 'input_registers', 'address': 2222, 'count': 2, 'device_id': 255})
    reader = ModbusReader(selected, .5, maximum, library=SimpleNamespace(ModbusTcpClient=Client))
    try:
        frame = reader.read(.5)
        assert _decode(frame, {'decode': 'float32'}, 'acquisition.modbus')[0] == -.125
        assert frame['wire'] == frame['request_wire'] == b''
        assert frame['trace_packets'][1]['sha256'] == hashlib.sha256(b'exact response bytes').hexdigest()
        assert frame['trace_packets'][1]['size'] == len(b'exact response bytes')
        assert frame['wire_scope'] == 'modbus_tcp_sdk_trace_callback_buffers'
        assert reader.source_binding['selection'] == 'direct' and reader.source_binding['discovery'] is None
    finally:
        reader.close()
    assert calls == [(2222, 2, 255), 'closed']


@pytest.mark.parametrize('maximum', [None, 0, 3])
def test_callback_receipts_preserve_each_buffer_hash_with_aggregate_bounded_prefixes(maximum):
    packets = [('tx', b'ab'), ('tx', b'cdef'), ('rx', b'uv'), ('rx', b'wxyz')]

    class Client:
        def __init__(self, host, **kwargs):
            self.trace = kwargs['trace_packet']

        def connect(self):
            return True

        def read_holding_registers(self, *, address, count, device_id):
            for direction, data in packets:
                assert self.trace(direction == 'tx', data) is data
            return SimpleNamespace(registers=[9], isError=lambda: False)

        def close(self):
            pass

    selected = binding({'kind': 'holding_registers', 'address': 1, 'count': 1, 'device_id': 255})
    reader = ModbusReader(selected, .5, maximum, library=SimpleNamespace(ModbusTcpClient=Client))
    try:
        frame = reader.read(.5)
        assert frame['wire'] == frame['request_wire'] == b''
        assert 'wire_sha256' not in frame and 'original_wire_bytes' not in frame
        captured = {'rx': b'', 'tx': b''}
        for actual, (direction, data) in zip(frame['trace_packets'], packets, strict=True):
            assert actual['direction'] == direction and actual['size'] == len(data)
            assert actual['sha256'] == hashlib.sha256(data).hexdigest()
            remaining = len(data) if maximum is None else max(0, maximum - len(captured[direction]))
            prefix = base64.b64decode(actual['prefix_base64'])
            assert prefix == data[:remaining]
            captured[direction] += prefix
            assert actual['capture_scope'] == 'pymodbus_trace_callback_buffer'
        assert maximum is None or all(len(prefix) <= maximum for prefix in captured.values())
    finally:
        reader.close()


def test_connection_failure_closes_client_and_attaches_json_safe_source_binding():
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            raise ConnectionError('owned peer failed')

        def close(self):
            calls.append('closed')

    with pytest.raises(ConnectionError, match='owned peer') as caught:
        ModbusReader(binding(), .5, 0, library=SimpleNamespace(ModbusTcpClient=Client))
    assert calls == ['closed']
    assert caught.value.source_binding['resolved_read'] is None
    assert caught.value.source_binding['discovery'] is None
    json.dumps(caught.value.source_binding, allow_nan=False)


def test_actual_rtu_over_tcp_reports_its_framing_scope():
    pytest.importorskip('pymodbus.client')
    from test_lab_hardware_transports import _modbus_server

    selected = binding({'kind': 'holding_registers', 'address': 0x20, 'count': 2, 'device_id': 1})
    selected['endpoint']['framing'] = 'rtu_over_tcp'
    with _modbus_server('rtu_over_tcp') as (port, requests):
        selected['endpoint']['port'] = port
        reader = ModbusReader(selected, .5, 0)
        try:
            frame = reader.read(.5)
            assert frame['registers'] == [123, 65535]
            assert frame['wire_scope'] == 'modbus_rtu_over_tcp_sdk_trace_callback_buffers'
            assert reader.source_binding['wire_scope'] == 'modbus_rtu_over_tcp_sdk_trace_callback_buffers'
        finally:
            reader.close()
    assert len(requests) == 1


class ControlledClock:
    def __init__(self):
        self.now = 0

    def time(self):
        return 1000 + self.now

    def monotonic(self):
        return self.now


@pytest.mark.parametrize('stop', ['deadline', 'cancelled'])
def test_scan_checks_total_budget_between_individually_short_reads_and_retains_partial_receipt(stop):
    from test_lab_device_discovery import mapping

    clock = ControlledClock()
    calls = []
    closed = []
    words = mapping(1769, [(64003, [1]), (64003, [2]), (64003, [3]), (64003, [4])])

    class Client:
        def __init__(self, host, **kwargs):
            self.trace = kwargs['trace_packet']

        def connect(self):
            return True

        def read_holding_registers(self, *, address, count, device_id):
            calls.append((address, count))
            clock.now += .04
            self.trace(True, b'request-buffer')
            self.trace(False, b'decoder-buffer')
            return SimpleNamespace(registers=[words[index] for index in range(address, address + count)],
                                   isError=lambda: False)

        def close(self):
            closed.append(True)

    cancellation = lambda: stop == 'cancelled' and len(calls) >= 2
    deadline = .09 if stop == 'deadline' else None
    expected = TimeoutError if stop == 'deadline' else ExecutionCancelled
    with pytest.raises(expected) as caught:
        ModbusReader(binding(), .5, 3, library=SimpleNamespace(ModbusTcpClient=Client), clock=clock,
                     cancelled=cancellation, deadline=deadline)
    assert closed == [True] and len(calls) == (3 if stop == 'deadline' else 2)
    source = caught.value.source_binding
    report = source['discovery']
    assert report['complete'] is False and source['resolved_read'] is None
    assert report['status'] == ('deadline_exceeded' if stop == 'deadline' else 'cancelled')
    assert caught.value.partial_report == report and report['registers_read'] == 2 * len(calls)
    assert source['execution_budget']['deadline_monotonic'] == deadline
    assert source['discovery_wire']
    assert sum(receipt['captured_bytes'] for receipt in source['discovery_wire'] if receipt['direction'] == 'rx') == 3
    json.dumps(source, allow_nan=False)


def test_owned_tcp_cancellation_stops_scan_between_actual_reads_and_closes():
    pytest.importorskip('pymodbus.client')
    from test_lab_device_discovery import mapping, owned_register_server

    selected = binding()
    with owned_register_server(mapping(1769, [(64003, [1]), (64003, [2])])) as (port, requests):
        selected['endpoint']['port'] = port
        with pytest.raises(ExecutionCancelled) as caught:
            ModbusReader(selected, .5, 0, cancelled=lambda: len(requests) >= 2)
    assert len(requests) == 2 and caught.value.source_binding['discovery']['status'] == 'cancelled'


def test_expired_deadline_refuses_native_client_creation():
    calls = []
    with pytest.raises(TimeoutError) as caught:
        ModbusReader(binding(), .5, 0, deadline=0, clock=ControlledClock(),
                     library=SimpleNamespace(ModbusTcpClient=lambda *a, **k: calls.append(a)))
    assert not calls and caught.value.source_binding['discovery'] is None


def test_cancellation_before_open_refuses_native_client_creation_and_attaches_receipt():
    calls = []
    with pytest.raises(ExecutionCancelled) as caught:
        ModbusReader(binding(), .5, 0, cancelled=lambda: True,
                     library=SimpleNamespace(ModbusTcpClient=lambda *a, **k: calls.append(a)))
    assert not calls and caught.value.source_binding['discovery'] is None


@pytest.mark.parametrize('stop',['deadline','cancelled'])
def test_measurement_checks_budget_again_after_open(stop):
    clock=ControlledClock();cancelled=[False];reads=[]
    class Client:
        def __init__(self,*a,**kw):pass
        def connect(self):return True
        def close(self):pass
        def read_holding_registers(self,*,address,count,device_id):
            reads.append(address)
            return SimpleNamespace(registers=[9],isError=lambda:False)
    selected=binding({'kind':'holding_registers','device_id':255,'address':1,'count':1})
    reader=ModbusReader(selected,.5,0,clock=clock,deadline=1,cancelled=lambda:cancelled[0],
                        library=SimpleNamespace(ModbusTcpClient=Client))
    if stop=='deadline':clock.now=2
    else:cancelled[0]=True
    try:
        with pytest.raises(TimeoutError if stop=='deadline' else ExecutionCancelled):reader.read(0)
    finally:reader.close()
    assert reads==[]


def test_actual_fragmented_response_retains_decoder_snapshots_without_fabricating_wire():
    pytest.importorskip('pymodbus.client')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(3)
    failures = []
    snapshot_seen = Event()
    response = bytes.fromhex('000100000005ff03020009')

    def serve():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(3)
                request = bytearray()
                while len(request) < 12:
                    request.extend(connection.recv(12 - len(request)))
                assert request[6:] == bytes.fromhex('ff0300010001')
                connection.sendall(response[:4])
                assert snapshot_seen.wait(2), 'first SDK decoder snapshot did not arrive'
                connection.sendall(response[4:])
        except BaseException as exc:
            failures.append(exc)

    # Signal from a receive receipt only; transmit callbacks also timestamp.
    from tinystudio.lab.modbus_trace import RawModbusTrace
    original = RawModbusTrace.observe

    def observed(trace, sending, buffer):
        result = original(trace, sending, buffer)
        if not sending:
            snapshot_seen.set()
        return result

    from unittest.mock import patch
    selected = binding({'kind': 'holding_registers', 'address': 1, 'count': 1, 'device_id': 255})
    selected['endpoint']['port'] = listener.getsockname()[1]
    thread = Thread(target=serve, daemon=True)
    thread.start()
    try:
        with patch.object(RawModbusTrace, 'observe', observed):
            reader = ModbusReader(selected, 2, None)
            try:
                frame = reader.read(2)
            finally:
                reader.close()
    finally:
        listener.close()
        thread.join(3)
    assert not failures and not thread.is_alive()
    assert frame['registers'] == [9] and frame['wire'] == b''
    received = [receipt for receipt in frame['trace_packets'] if receipt['direction'] == 'rx']
    assert [receipt['size'] for receipt in received] == [4, 11]
    assert [base64.b64decode(receipt['prefix_base64']) for receipt in received] == [response[:4], response]
    assert received[-1]['sha256'] == hashlib.sha256(response).hexdigest()
    assert all(receipt['capture_scope'] == 'pymodbus_trace_callback_buffer' for receipt in received)
    assert frame['trace_capture']['transport_wire_reconstruction'] is False
