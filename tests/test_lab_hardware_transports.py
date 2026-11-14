"""Native protocol checks use owned localhost servers and virtual buses only."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import hashlib
import socket
from threading import Event, Thread
from types import SimpleNamespace
import uuid

import pytest

from tinystudio.lab.hardware_transports import (
    CANReader, ModbusEndpoint, SerialEndpoint, read_registers,
)


@pytest.mark.parametrize('port', ['COM3', '/tmp/operator-selected-serial', 'relative-device'])
def test_serial_native_names_are_preserved_and_opened_once(port):
    calls = []
    class Serial:
        def __init__(self, **kwargs):
            calls.append(('construct', kwargs))
        def open(self): calls.append(('open', self.port))
        def close(self): calls.append(('close',))
    endpoint = SerialEndpoint.from_dict({'port': port, 'baudrate': 9600})
    opened = endpoint.open(.25, library=SimpleNamespace(Serial=Serial))
    assert opened.port == port
    assert calls == [
        ('construct', {'port': None, 'baudrate': 9600, 'timeout': .25,
                       'write_timeout': 0, 'xonxoff': False, 'rtscts': False, 'dsrdtr': False}),
        ('open', port),
    ]
    with pytest.raises(FrozenInstanceError): endpoint.port = 'other'


@pytest.mark.parametrize('port', ['rfc2217://127.0.0.1:2217', 'socket://[::1]:2000', 'loop://'])
def test_serial_urls_select_url_factory_without_guessing_a_local_device(port):
    calls = []
    class Port:
        def open(self): calls.append(('open',))
        def close(self): calls.append(('close',))
    def factory(url, **kwargs):
        calls.append(('factory', url, kwargs))
        return Port()
    SerialEndpoint.from_dict({'port': port, 'baudrate': 115200}).open(
        .2, library=SimpleNamespace(serial_for_url=factory))
    assert calls[0][1] == port
    assert calls[0][2]['do_not_open'] is True
    assert calls[0][2]['write_timeout'] is (None if port.startswith('rfc2217:') else 0)
    assert calls[1:] == [('open',)]


@pytest.mark.parametrize('value', [
    {'port': 'rfc2217://localhost:2217', 'baudrate': 9600},
    {'port': 'socket://127.0.0.1', 'baudrate': 9600},
    {'port': 'socket://127.0.0.1:0', 'baudrate': 9600},
    {'port': 'socket://user@127.0.0.1:42', 'baudrate': 9600},
    {'port': 'loop://remote', 'baudrate': 9600},
    {'port': 'spy:///tmp/device', 'baudrate': 9600},
    {'port': '', 'baudrate': 9600},
    {'port': 'COM3', 'baudrate': True},
    {'port': 'rfc2217://127.0.0.1:2217', 'baudrate': 9600, 'write_timeout': 0},
])
def test_invalid_serial_configuration_fails_before_open(value):
    with pytest.raises(ValueError): SerialEndpoint.from_dict(value)


def test_dns_is_an_explicit_operator_option_and_url_is_preserved():
    serial = SerialEndpoint.from_dict({'port': 'rfc2217://selected.example:2217', 'baudrate': 9600, 'allow_dns': True})
    modbus = ModbusEndpoint.from_dict({'host': 'selected.example', 'port': 1502, 'allow_dns': True})
    assert serial.port == 'rfc2217://selected.example:2217'
    assert modbus.host == 'selected.example'


def test_serial_open_failure_closes_the_created_object():
    calls = []
    class Serial:
        def __init__(self, **kwargs): pass
        def open(self): raise OSError('owned endpoint unavailable')
        def close(self): calls.append('close')
    with pytest.raises(OSError, match='owned endpoint unavailable'):
        SerialEndpoint.from_dict({'port': 'COM3', 'baudrate': 9600}).open(.1, library=SimpleNamespace(Serial=Serial))
    assert calls == ['close']


def test_actual_pyserial_loop_url_keeps_received_bytes():
    serial = pytest.importorskip('serial')
    endpoint = SerialEndpoint.from_dict({'port': 'loop://', 'baudrate': 115200, 'write_timeout': 1})
    port = endpoint.open(.25, library=serial)
    try:
        # The write belongs to this explicit local emulator, not acquisition.
        assert port.write(b'-0.125\r\n') == 8
        assert port.read_until(b'\n', 128) == b'-0.125\r\n'
    finally: port.close()


def test_modbus_creation_is_unopened_and_packet_trace_is_observational():
    calls = []
    class Client:
        def __init__(self, host, **kwargs): calls.append((host, kwargs))
        def connect(self): pytest.fail('endpoint creation must leave the client unopened')
    endpoint = ModbusEndpoint.from_dict({'host': '127.0.0.1', 'port': 1502})
    observed = []
    endpoint.create_client(.2, trace_packet=lambda sending, wire: observed.append((sending, wire)) or b'CHANGED',
                           library=SimpleNamespace(ModbusTcpClient=Client))
    host, kwargs = calls[0]
    assert host == '127.0.0.1' and kwargs['port'] == 1502 and kwargs['retries'] == 0
    packet = b'original protocol bytes'
    assert kwargs['trace_packet'](True, packet) is packet
    assert observed == [(True, packet)]


def test_selected_modbus_framing_fails_if_library_cannot_express_it():
    library = SimpleNamespace(ModbusTcpClient=lambda *args, **kwargs: pytest.fail('missing framer must fail first'))
    with pytest.raises(ValueError, match='RTU'):
        ModbusEndpoint.from_dict({'host': '127.0.0.1', 'port': 1502, 'framing': 'rtu_over_tcp'}).create_client(.1, library=library)


@pytest.mark.parametrize('endpoint', [
    {'host': 'localhost', 'port': 502}, {'host': '127.0.0.1', 'port': True},
    {'host': '127.0.0.1', 'port': 65536}, {'host': '127.0.0.1', 'port': 502, 'framing': 'guessed'},
])
def test_invalid_modbus_configuration_fails_before_client_creation(endpoint):
    with pytest.raises(ValueError): ModbusEndpoint.from_dict(endpoint)


@pytest.mark.parametrize('key', ['device_id', 'slave'])
def test_register_reads_use_public_selected_device_parameter(key):
    calls = []
    if key == 'device_id':
        def method(*, address, count, device_id): calls.append((address, count, device_id)); return 'response'
    else:
        def method(*, address, count, slave): calls.append((address, count, slave)); return 'response'
    client = SimpleNamespace(read_input_registers=method)
    assert read_registers(client, kind='input_registers', address=65535, count=1, device_id=247) == 'response'
    assert calls == [(65535, 1, 247)]


@pytest.mark.parametrize('change', [
    {'kind': 'coils'}, {'address': -1}, {'address': 65536}, {'address': 65535, 'count': 2},
    {'count': 0}, {'count': 126}, {'device_id': -1}, {'device_id': 256}, {'count': True},
])
def test_register_reads_enforce_protocol_bounds_before_sending(change):
    args = {'kind': 'holding_registers', 'address': 0, 'count': 1, 'device_id': 1, **change}
    client = SimpleNamespace(read_holding_registers=lambda **kwargs: pytest.fail('invalid read must not send'))
    with pytest.raises(ValueError): read_registers(client, **args)


def test_register_api_without_public_device_parameter_fails_visibly():
    with pytest.raises(ValueError, match='device_id/slave'):
        read_registers(SimpleNamespace(read_holding_registers=lambda address, count: None),
                       kind='holding_registers', address=0, count=1, device_id=1)


def _crc16(data):
    # Independent bitwise Modbus CRC oracle; the production client owns framing.
    result = 0xffff
    for byte in data:
        result ^= byte
        for _ in range(8): result = (result >> 1) ^ (0xa001 if result & 1 else 0)
    return result.to_bytes(2, 'little')


def _recv_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk: raise EOFError('local protocol client disconnected')
        data.extend(chunk)
    return bytes(data)


@contextmanager
def _modbus_server(framing):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(3)
    requests = []; errors = []
    def serve():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(3)
                if framing == 'rtu_over_tcp':
                    request = _recv_exact(connection, 8)
                    assert request[-2:] == _crc16(request[:-2])
                    response = b'\x01\x03\x04\x00\x7b\xff\xff'
                    response += _crc16(response)
                else:
                    header = _recv_exact(connection, 6)
                    request = header + _recv_exact(connection, int.from_bytes(header[4:6], 'big'))
                    response = header[:4] + b'\x00\x07' + request[6:7] + b'\x03\x04\x00\x7b\xff\xff'
                requests.append(request)
                connection.sendall(response)
        except BaseException as exc: errors.append(exc)
    thread = Thread(target=serve, daemon=True); thread.start()
    failed = False
    try: yield listener.getsockname()[1], requests
    except BaseException:
        failed = True
        raise
    finally:
        thread.join(4); listener.close()
        assert not thread.is_alive(), 'owned Modbus server did not terminate'
        if errors and not failed: raise errors[0]


@pytest.mark.parametrize(('framing', 'device_id'), [('tcp', 1), ('tcp', 255), ('tcp', 0), ('rtu_over_tcp', 1)])
def test_actual_pymodbus_packets_match_independent_tcp_and_rtu_servers(framing, device_id):
    client_library = pytest.importorskip('pymodbus.client')
    sent = []; received = []
    def trace(sending, packet): (sent if sending else received).append(bytes(packet))
    with _modbus_server(framing) as (port, requests):
        client = ModbusEndpoint.from_dict({'host': '127.0.0.1', 'port': port, 'framing': framing}).create_client(
            .5, trace_packet=trace, library=client_library)
        try:
            assert client.connect()
            response = read_registers(client, kind='holding_registers', address=0x20, count=2, device_id=device_id)
            assert not response.isError() and response.registers == [123, 65535]
        finally: client.close()
    request = requests[0]
    if framing == 'rtu_over_tcp':
        assert request == bytes.fromhex('01 03 00 20 00 02 C5 C1')
        assert b''.join(received)[-2:] == _crc16(b''.join(received)[:-2])
    else:
        assert request[2:6] == bytes.fromhex('00 00 00 06')
        assert request[6] == device_id
        assert request[7:] == bytes.fromhex('03 00 20 00 02')
    assert b''.join(sent) == request


@pytest.mark.parametrize('maximum', [None, 2, 0])
def test_actual_python_can_virtual_bus_keeps_frame_metadata_and_explicit_truncation(maximum):
    can = pytest.importorskip('can')
    channel = 'tinystudio-owned-' + uuid.uuid4().hex
    binding = {'endpoint': {'interface': 'virtual', 'channel': channel}}
    peer = can.Bus(interface='virtual', channel=channel, ignore_config=True)
    reader = None
    try:
        reader = CANReader(binding, .1, maximum, library=can)
        peer.send(can.Message(arbitration_id=0x18ff50e5, is_extended_id=True,
                              is_fd=True, bitrate_switch=True, data=b'\x01\x02\x03\x04'))
        frame = reader.read(.25)
        payload = b'\x01\x02\x03\x04'
        captured = payload if maximum is None else payload[:maximum]
        assert frame['wire'] == captured
        assert frame['wire_scope'] == 'can_application_payload'
        assert frame['truncated'] is (maximum is not None)
        assert frame['can_frame']['arbitration_id'] == 0x18ff50e5
        assert frame['can_frame']['is_extended_id'] is True
        assert frame['can_frame']['is_fd'] is True and frame['can_frame']['bitrate_switch'] is True
        assert frame['can_frame']['dlc'] == 4 and frame['can_frame']['channel'] == channel
        assert frame['can_frame']['data_hex'] == captured.hex()
        assert frame['original_wire_bytes'] == len(payload)
        assert frame['wire_sha256'] == hashlib.sha256(payload).hexdigest()
        assert reader.library_metadata == {'name': 'python-can', 'version': can.__version__}
        assert reader.read(.001) is None
    finally:
        if reader is not None: reader.close()
        peer.shutdown()


def test_can_uses_only_explicit_operator_configuration_and_receives_without_send():
    calls = []
    class Bus:
        def __init__(self, **kwargs): calls.append(('construct', kwargs))
        def recv(self, timeout): calls.append(('recv', timeout)); return None
        def shutdown(self): calls.append(('shutdown',))
        def send(self, *args, **kwargs): pytest.fail('CAN acquisition must not send')
    reader = CANReader({'endpoint': {'interface': 'operator-plugin', 'channel': 3,
                                    'options': {'bitrate': 333333, 'receive_own_messages': False}}},
                       .2, None, library=SimpleNamespace(Bus=Bus, __version__='fixture'))
    assert reader.read(.1) is None
    reader.close()
    assert calls == [('construct', {'interface': 'operator-plugin', 'channel': 3, 'ignore_config': True,
                                    'bitrate': 333333, 'receive_own_messages': False}),
                     ('recv', .1), ('shutdown',)]


@contextmanager
def _rfc2217_loop_server(serial):
    """Own both the RFC2217 bridge and its local pySerial loop endpoint."""
    rfc2217 = pytest.importorskip('serial.rfc2217')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(3)
    stop = Event(); errors = []
    def serve():
        try:
            connection, _ = listener.accept()
            with connection, serial.serial_for_url('loop://', timeout=0) as emulator:
                connection.settimeout(.01)
                manager = rfc2217.PortManager(emulator, SimpleNamespace(write=connection.sendall))
                while not stop.is_set():
                    try: incoming = connection.recv(4096)
                    except socket.timeout: incoming = None
                    if incoming == b'': break
                    if incoming:
                        decoded = b''.join(manager.filter(incoming))
                        if decoded: emulator.write(decoded)
                    outgoing = emulator.read(emulator.in_waiting)
                    if outgoing: connection.sendall(b''.join(manager.escape(outgoing)))
                    manager.check_modem_lines()
        except BaseException as exc:
            if not stop.is_set(): errors.append(exc)
    thread = Thread(target=serve, daemon=True); thread.start()
    failed = False
    try: yield listener.getsockname()[1]
    except BaseException:
        failed = True
        raise
    finally:
        stop.set(); thread.join(4); listener.close()
        assert not thread.is_alive(), 'owned RFC2217 server did not terminate'
        if errors and not failed: raise errors[0]


def test_actual_pyserial_rfc2217_negotiates_with_owned_loop_bridge():
    serial = pytest.importorskip('serial')
    with _rfc2217_loop_server(serial) as port_number:
        url = f'rfc2217://127.0.0.1:{port_number}'
        port = SerialEndpoint.from_dict({'port': url, 'baudrate': 115200}).open(.5, library=serial)
        try:
            assert port.port == url and port.write_timeout is None
            # Explicit emulator stimulus also verifies RFC2217 Telnet escaping.
            payload = b'-0.125\xff\r\n'
            assert port.write(payload) == len(payload)
            assert port.read_until(b'\n', 128) == payload
        finally: port.close()


@pytest.mark.parametrize('timeout', [0, .000001])
def test_nonblocking_and_submillisecond_timeouts_are_preserved(timeout):
    serial_kwargs = []; modbus_kwargs = []; can_timeouts = []
    class Port:
        def __init__(self, **kwargs): serial_kwargs.append(kwargs)
        def open(self): pass
        def close(self): pass
    class Bus:
        def __init__(self, **kwargs): pass
        def recv(self, timeout): can_timeouts.append(timeout)
        def shutdown(self): pass
    SerialEndpoint.from_dict({'port': 'COM3', 'baudrate': 9600}).open(timeout, library=SimpleNamespace(Serial=Port)).close()
    ModbusEndpoint.from_dict({'host': '127.0.0.1', 'port': 502}).create_client(
        timeout, library=SimpleNamespace(ModbusTcpClient=lambda host, **kwargs: modbus_kwargs.append(kwargs)))
    reader = CANReader({'endpoint': {'interface': 'fixture', 'channel': 0}}, timeout, None,
                       library=SimpleNamespace(Bus=Bus))
    try: assert reader.read(timeout) is None
    finally: reader.close()
    assert serial_kwargs[0]['timeout'] == modbus_kwargs[0]['timeout'] == can_timeouts[0] == timeout


def test_actual_pyserial_socket_receives_from_owned_local_server():
    serial = pytest.importorskip('serial')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(3)
    opened = Event(); errors = []
    def serve():
        try:
            connection, _ = listener.accept()
            with connection:
                if not opened.wait(3): raise TimeoutError('local serial client did not open')
                connection.sendall(b'-0.125\r\n')
                # Keep the owned stream open until the client has read its bytes.
                connection.settimeout(3)
                assert connection.recv(1) == b''
        except BaseException as exc: errors.append(exc)
    thread = Thread(target=serve, daemon=True); thread.start()
    url = f'socket://127.0.0.1:{listener.getsockname()[1]}'
    try:
        port = SerialEndpoint.from_dict({'port': url, 'baudrate': 9600}).open(.25, library=serial)
        try:
            opened.set()
            assert port.port == url
            assert port.read_until(b'\n', 128) == b'-0.125\r\n'
        finally: port.close()
    finally:
        thread.join(4); listener.close()
        assert not thread.is_alive(), 'owned serial socket server did not terminate'
    if errors: raise errors[0]


def test_serial_options_are_frozen_and_explicit_overrides_are_used():
    native_options = {'rtscts': True, 'write_timeout': None, 'parity': 'E', 'bytesize': 7}
    endpoint = SerialEndpoint.from_dict({'port': 'COM3', 'baudrate': 19200, 'options': native_options})
    native_options['parity'] = 'N'
    calls = []
    class Port:
        def __init__(self, **kwargs): calls.append(kwargs)
        def open(self): pass
        def close(self): pass
    endpoint.open(.1, library=SimpleNamespace(Serial=Port)).close()
    assert calls[0]['rtscts'] is True and calls[0]['write_timeout'] is None
    assert calls[0]['bytesize'] == 7 and calls[0]['parity'] == 'E'


def test_selected_modbus_trace_feature_is_never_silently_dropped():
    class ClientWithoutTrace:
        def __init__(self, host, *, port, timeout, retries): pass
    with pytest.raises(TypeError, match='trace_packet'):
        ModbusEndpoint.from_dict({'host': '127.0.0.1', 'port': 502}).create_client(
            .1, trace_packet=lambda sending, packet: packet,
            library=SimpleNamespace(ModbusTcpClient=ClientWithoutTrace))


@pytest.mark.parametrize('timeout', [-1, float('nan'), float('inf'), True])
def test_invalid_timeouts_fail_before_native_open(timeout):
    with pytest.raises(ValueError, match='timeout'):
        SerialEndpoint.from_dict({'port': 'COM3', 'baudrate': 9600}).open(
            timeout, library=SimpleNamespace(Serial=lambda **kwargs: pytest.fail('invalid timeout must not open')))


@pytest.mark.parametrize('endpoint', [
    {'interface': 'virtual'},
    {'interface': 'virtual', 'channel': 'owned', 'ignore_config': False},
    {'interface': 'virtual', 'channel': 'owned', 'options': {'interface': 'other'}},
])
def test_can_requires_explicit_channel_and_rejects_ambient_configuration_overrides(endpoint):
    with pytest.raises(ValueError):
        CANReader({'endpoint': endpoint}, .1, None,
                  library=SimpleNamespace(Bus=lambda **kwargs: pytest.fail('invalid endpoint must not open')))
