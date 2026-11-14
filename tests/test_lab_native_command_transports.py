"""Native command boundaries use only owned local emulators and virtual buses."""
from contextlib import contextmanager
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import ssl
import subprocess
import time
from threading import Thread
from types import SimpleNamespace
import uuid

import pytest

from tinystudio.lab.command_profiles import NativeCommandProfile
from tinystudio.lab.native_command_transports import (
    NativeCommandTransport, NativeCommandTransportError, native_transport_for,
)


def binding(kind="tcp", **transport):
    defaults = {"tcp": {"host": "127.0.0.1", "port": 15000},
                "serial": {"port": "loop://", "baudrate": 115200, "options": {"write_timeout": 1}},
                "modbus": {"host": "127.0.0.1", "port": 15000, "device_id": 255},
                "mqtt": {"host": "127.0.0.1", "port": 15000, "topic": "owned/exact", "tls": False},
                "can": {"interface": "virtual", "channel": "fixture"}}
    return {"endpoint_id": "owned-device", "operator_id": "local-operator",
            "transport": {"kind": kind, **defaults.get(kind, {}), **transport},
            "command_profile": {"format": "tinystudio.native-command-profile.v1"}}


def request(selected, operation, **payload):
    profile = NativeCommandProfile.from_binding(selected)
    return {"protocol": "tinystudio.native-command.v1", "endpoint_id": selected["endpoint_id"],
            "command_id": "owned-command", "epoch": 3, "owner": "run:attempt",
            "expires_at": time.time() + 30, "kind": "command", "profile_digest": profile.digest,
            "payload": {"operation": operation, **payload}}


def data(value):
    return {"encoding": "utf8", "value": value}


@contextmanager
def tcp_peer(expected, response=b"", *, respond=True):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(2)
    received, errors = [], []
    def serve():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(2)
                actual = bytearray()
                while len(actual) < len(expected):
                    block = connection.recv(len(expected) - len(actual))
                    if not block:
                        break
                    actual.extend(block)
                received.append(bytes(actual))
                if respond:
                    connection.sendall(response)
        except BaseException as error:
            errors.append(error)
    thread = Thread(target=serve, daemon=True); thread.start()
    try:
        yield listener.getsockname()[1], received
    finally:
        thread.join(3); listener.close()
        assert not thread.is_alive(), "owned TCP peer did not stop"
        assert not errors, errors


def test_construction_performs_no_network_or_native_device_io(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("construction must not connect"))
    for kind in ("tcp", "serial", "modbus", "mqtt", "can"):
        assert native_transport_for(binding(kind)) is not None


def test_actual_tcp_query_preserves_precision_strings_and_explicit_framing():
    sent = b"SET 900719925474099312345678901234567890\r\n"
    response = b"RESULT 900719925474099312345678901234567891\r\n"
    with tcp_peer(sent, response) as (port, observed):
        selected = binding(port=port, read={"kind": "delimiter", "delimiter": {"encoding": "hex", "value": "0d0a"}})
        command = request(selected, "tcp.query", data=data(sent.decode()))
        checks = []
        result = native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False,
                                                         authorize=lambda: checks.append(True) or deepcopy(command))
    assert observed == [sent] and checks
    assert result["status"] == "response_received" and result["device_application_confirmed"] is False
    assert result["physical_outcome"] == "unverified"
    assert base64.b64decode(result["evidence"]["response"]["base64"]) == response
    assert result["cleanup"]["confirmed"] is True


def test_actual_pyserial_loop_query_uses_native_url_and_exact_bytes():
    selected = binding("serial", read={"kind": "fixed_bytes", "count": 5})
    command = request(selected, "serial.query", data={"encoding": "hex", "value": "00ff0a0d31"})
    result = native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    assert base64.b64decode(result["evidence"]["response"]["base64"]) == b"\x00\xff\n\r1"
    assert result["status"] == "response_received"


class FakeSocket:
    def __init__(self, *, partial=False, close_error=False):
        self.writes = []; self.partial = partial; self.close_error = close_error
    def settimeout(self, value):
        self.timeout = value
    def send(self, value):
        if self.partial and self.writes:
            raise OSError("primary write failure")
        count = 2 if self.partial else len(value)
        self.writes.append(bytes(value[:count])); return count
    def close(self):
        if self.close_error:
            raise OSError("secondary close failure")


def test_each_partial_tcp_write_requires_fresh_exact_authorization_and_keeps_partial_evidence(monkeypatch):
    peer = FakeSocket(partial=True, close_error=True)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: peer)
    selected = binding(); command = request(selected, "tcp.write", data=data("abcdef"))
    checks = []
    with pytest.raises(NativeCommandTransportError, match="primary write failure") as caught:
        native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False,
            authorize=lambda: checks.append(len(peer.writes)) or command)
    assert checks == [0, 1] and peer.writes == [b"ab"]
    evidence = caught.value.delivery_evidence
    assert evidence["status"] == "unknown"
    assert base64.b64decode(evidence["evidence"]["written"]["base64"]) == b"ab"
    assert "secondary close failure" in evidence["cleanup"]["error"]["message"]


@pytest.mark.parametrize("change", [lambda r: r.update(epoch=4), lambda r: r["payload"].update(data=data("other"))])
def test_changed_authorized_request_never_reaches_a_write(monkeypatch, change):
    peer = FakeSocket(); monkeypatch.setattr(socket, "create_connection", lambda *a, **k: peer)
    selected = binding(); command = request(selected, "tcp.write", data=data("exact"))
    altered = deepcopy(command); change(altered)
    with pytest.raises(NativeCommandTransportError):
        native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: altered)
    assert peer.writes == []


def test_cancellation_after_connection_setup_never_writes(monkeypatch):
    peer = FakeSocket(); cancelled = []
    def connect(*args, **kwargs):
        cancelled.append(True); return peer
    monkeypatch.setattr(socket, "create_connection", connect)
    selected = binding(); command = request(selected, "tcp.write", data=data("exact"))
    with pytest.raises(NativeCommandTransportError):
        native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: bool(cancelled), authorize=lambda: command)
    assert peer.writes == []


def test_query_budget_exhaustion_retains_bytes_without_reporting_complete():
    with tcp_peer(b"query", b"abcdef") as (port, _):
        selected = binding(port=port, read={"kind": "delimiter", "delimiter": data("\n"), "max_bytes": 4})
        command = request(selected, "tcp.query", data=data("query"))
        with pytest.raises(NativeCommandTransportError) as caught:
            native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    receipt = caught.value.delivery_evidence
    assert receipt["status"] == "unknown" and receipt["evidence"]["response"]["size"] == 4


def test_large_raw_response_has_no_implicit_64k_cap():
    response = b"x" * 131073
    with tcp_peer(b"query", response) as (port, _):
        selected = binding(port=port, read={"kind": "fixed_bytes", "count": len(response)})
        command = request(selected, "tcp.query", data=data("query"))
        receipt = native_transport_for(selected).exchange(command, timeout=2, cancelled=lambda: False, authorize=lambda: command)
    assert receipt["evidence"]["response"]["sha256"] == hashlib.sha256(response).hexdigest()
    assert receipt["evidence"]["response"]["size"] == len(response)


def test_capture_budget_is_explicit_prefix_retention_not_a_query_truncation():
    with tcp_peer(b"query", b"abcdef") as (port, _):
        selected = binding(port=port, read={"kind": "fixed_bytes", "count": 6})
        selected["command_profile"]["max_capture_bytes"] = 2
        command = request(selected, "tcp.query", data=data("query"))
        receipt = native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    response = receipt["evidence"]["response"]
    assert receipt["status"] == "response_received" and response["size"] == 6
    assert response["truncated"] is True and base64.b64decode(response["base64"]) == b"ab"


def crc16(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value.to_bytes(2, "little")


def receive_exact(connection, count):
    result = bytearray()
    while len(result) < count:
        block = connection.recv(count - len(result))
        if not block:
            raise EOFError("owned client disconnected")
        result.extend(block)
    return bytes(result)


@contextmanager
def modbus_peer(expected, response, *, framing="tcp", unit=255):
    listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(2)
    observed, errors = [], []
    def serve():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(2)
                if framing == "tcp":
                    header = receive_exact(connection, 6)
                    request_body = receive_exact(connection, int.from_bytes(header[4:6], "big"))
                    assert header[2:4] == b"\0\0"
                    assert request_body == bytes([unit]) + expected
                    observed.append(request_body)
                    if response is not None:
                        body = bytes([unit]) + response
                        connection.sendall(header[:4] + len(body).to_bytes(2, "big") + body)
                else:
                    packet = receive_exact(connection, 1 + len(expected) + 2)
                    assert packet == bytes([unit]) + expected + crc16(bytes([unit]) + expected)
                    observed.append(packet)
                    if response is not None:
                        body = bytes([unit]) + response
                        connection.sendall(body + crc16(body))
        except BaseException as error:
            errors.append(error)
    thread = Thread(target=serve, daemon=True); thread.start()
    try:
        yield listener.getsockname()[1], observed
    finally:
        thread.join(3); listener.close()
        assert not thread.is_alive() and not errors, errors


@pytest.mark.parametrize("operation,values,pdu,reply,status", [
    ("write_register", {"value": 65535}, "060010ffff", "060010ffff", "protocol_accepted"),
    ("write_registers", {"values": [1, 32767]}, "10001000020400017fff", "1000100002", "protocol_accepted"),
    ("write_coil", {"value": True}, "050010ff00", "050010ff00", "protocol_accepted"),
    ("write_coils", {"values": [True, False, True, False, True, False, True, False, True]}, "0f00100009025501", "0f00100009", "protocol_accepted"),
    ("read_holding_registers", {"count": 2}, "0300100002", "030400017fff", "response_received"),
    ("read_input_registers", {"count": 2}, "0400100002", "040400017fff", "response_received"),
    ("read_coils", {"count": 9}, "0100100009", "01025501", "response_received"),
    ("read_discrete_inputs", {"count": 9}, "0200100009", "02025501", "response_received"),
])
def test_real_pymodbus_standard_operations_have_exact_protocol_requests(operation, values, pdu, reply, status):
    with modbus_peer(bytes.fromhex(pdu), bytes.fromhex(reply)) as (port, observed):
        selected = binding("modbus", port=port)
        command = request(selected, "modbus." + operation, address=16, **values)
        result = native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    assert observed == [b"\xff" + bytes.fromhex(pdu)]
    assert result["status"] == status and result["device_application_confirmed"] is False
    trace = result["evidence"]["trace"]
    assert trace["summary"]["transport_wire_reconstruction"] is False
    assert {item["direction"] for item in trace["receipts"]} == {"tx", "rx"}


def test_real_rtu_over_tcp_and_explicit_no_response_broadcast():
    with modbus_peer(bytes.fromhex("0600101234"), bytes.fromhex("0600101234"), framing="rtu_over_tcp", unit=1) as (port, _):
        selected = binding("modbus", port=port, framing="rtu_over_tcp", device_id=1)
        command = request(selected, "modbus.write_register", address=16, value=0x1234)
        assert native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False,
                                                      authorize=lambda: command)["status"] == "protocol_accepted"
    with modbus_peer(bytes.fromhex("0600101234"), None, unit=0) as (port, _):
        selected = binding("modbus", port=port, device_id=0, response_expected=False)
        command = request(selected, "modbus.write_register", address=16, value=0x1234)
        result = native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    assert result["status"] == "delivered_unverified"
    assert result["evidence"]["sdk"]["response_expected"] is False


def test_modbus_cumulative_trace_buffers_are_never_concatenated_into_wire_evidence():
    class Client:
        def __init__(self, *args, trace_packet, **kwargs): self.trace = trace_packet
        def connect(self): return True
        def close(self): pass
        def write_register(self, address, value, *, device_id, no_response_expected=False):
            self.trace(True, b"request")
            self.trace(False, b"ab")
            self.trace(False, b"abc")
            return SimpleNamespace(isError=lambda: False, address=address, registers=[value], function_code=6)
    selected = binding("modbus"); selected["command_profile"]["max_capture_bytes"] = 2
    command = request(selected, "modbus.write_register", address=16, value=2)
    result = NativeCommandTransport(selected, libraries={"pymodbus.client": SimpleNamespace(ModbusTcpClient=Client)}).exchange(
        command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    trace = result["evidence"]["trace"]
    assert trace["summary"]["observed_buffer_bytes"]["rx"] == 5
    assert trace["summary"]["captured_bytes"]["rx"] == 2
    assert [item["captured_bytes"] for item in trace["receipts"] if item["direction"] == "rx"] == [2, 0]
    assert "response" not in result["evidence"]


@pytest.mark.parametrize("reply", [bytes.fromhex("0600111234"), bytes.fromhex("0600109999")])
def test_modbus_mismatched_write_echo_is_not_protocol_accepted(reply):
    with modbus_peer(bytes.fromhex("0600101234"), reply) as (port, _):
        selected = binding("modbus", port=port)
        command = request(selected, "modbus.write_register", address=16, value=0x1234)
        with pytest.raises(NativeCommandTransportError):
            native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)


def test_virtual_can_transmits_native_fields_and_exact_payload():
    can = pytest.importorskip("can")
    channel = "owned-" + uuid.uuid4().hex
    peer = can.Bus(interface="virtual", channel=channel, ignore_config=True)
    try:
        selected = binding("can", channel=channel)
        command = request(selected, "can.send", arbitration_id=0x18FF1234, data={"encoding": "hex", "value": "00ff31"},
                          is_extended_id=True, is_fd=False)
        receipt = native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
        message = peer.recv(.5)
        assert message is not None and message.arbitration_id == 0x18FF1234 and bytes(message.data) == b"\0\xff1"
        assert receipt["status"] == "delivered_unverified" and receipt["device_application_confirmed"] is False
    finally:
        peer.shutdown()


@contextmanager
def broker(tmp_path):
    selected=os.environ.get('TINYSTUDIO_TEST_MQTT_BROKER')
    if not selected:pytest.skip('set TINYSTUDIO_TEST_MQTT_BROKER to an explicitly owned Mosquitto executable')
    binary=Path(selected)
    if not binary.is_file():pytest.fail('configured owned Mosquitto executable is unavailable')
    reserve = socket.socket(); reserve.bind(("127.0.0.1", 0)); port = reserve.getsockname()[1]; reserve.close()
    config = tmp_path / "mosquitto.conf"
    config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
    process = subprocess.Popen([str(binary), "-c", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 3
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.1): break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    if process.poll() is None:
                        process.terminate()
                    _, error = process.communicate(timeout=3)
                    pytest.fail("owned broker failed to start: " + error.decode())
                time.sleep(.01)
        yield port
    finally:
        process.terminate(); process.communicate(timeout=3)


@pytest.mark.parametrize("qos", [0, 1, 2])
def test_real_mosquitto_publish_ack_is_only_broker_protocol(tmp_path, qos):
    mqtt = pytest.importorskip("paho.mqtt.client")
    with broker(tmp_path) as port:
        messages, ready = [], []
        peer = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        peer.on_connect = lambda client, userdata, flags, code, properties: client.subscribe("owned/exact", qos=2)
        peer.on_subscribe = lambda *args: ready.append(True)
        peer.on_message = lambda client, userdata, message: messages.append(bytes(message.payload))
        peer.connect("127.0.0.1", port)
        try:
            deadline = time.monotonic() + 2
            while not ready:
                assert time.monotonic() < deadline
                peer.loop(timeout=.05)
            selected = binding("mqtt", port=port)
            command = request(selected, "mqtt.publish", data=data("9007199254740993123456789"), qos=qos, retain=False)
            result = native_transport_for(selected).exchange(command, timeout=1, cancelled=lambda: False, authorize=lambda: command)
            while not messages:
                assert time.monotonic() < deadline
                peer.loop(timeout=.05)
            assert messages == [b"9007199254740993123456789"]
            assert result["status"] == ("protocol_accepted" if qos else "delivered_unverified")
            assert result["device_application_confirmed"] is False
            assert result["evidence"]["sdk"]["acknowledgement_scope"] == "broker_protocol_only"
        finally:
            peer.disconnect()


def test_custom_adapter_is_lazy_and_application_confirmation_remains_a_claim():
    calls = []
    class Adapter:
        def __init__(self, **kwargs): calls.append("construct")
        def exchange(self, request, *, timeout, cancelled, authorize):
            assert authorize() == request
            return {"status": "application_reported", "device_application_confirmed": True, "own_semantics": "researcher-defined"}
        def close(self): calls.append("close")
    selected = binding("custom", entrypoint="owned_adapter:Adapter", identity={"version": "fixture"})
    driver = NativeCommandTransport(selected, libraries={"owned_adapter": SimpleNamespace(Adapter=Adapter, __version__="fixture")})
    assert calls == []
    command = request(selected, "custom.operation", arbitrary={"decimal_as_string": "999999999999999999999.001"})
    receipt = driver.exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    assert calls == ["construct", "close"]
    assert receipt["application_confirmation_scope"] == "adapter_claim" and receipt["physical_outcome"] == "unverified"
    assert receipt["operational"]["application_write_attempts"] is None


def test_invalid_custom_receipt_retains_partial_delivery_evidence():
    class Adapter:
        def __init__(self, **kwargs): pass
        def exchange(self, request, **kwargs):
            kwargs["authorize"]()
            return {"status": "made_up_status"}
    selected = binding("custom", entrypoint="owned_adapter:Adapter", identity="fixture")
    driver = NativeCommandTransport(selected, libraries={"owned_adapter": SimpleNamespace(Adapter=Adapter)})
    command = request(selected, "custom.operation", arbitrary="payload")
    with pytest.raises(NativeCommandTransportError) as caught:
        driver.exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    assert caught.value.delivery_evidence["evidence"]["adapter_report"]["status"] == "made_up_status"


def test_accepted_qualified_custom_entrypoint_constructs_the_selected_nested_adapter():
    calls=[]
    class Adapter:
        def __init__(self,**kwargs):calls.append('construct')
        def exchange(self,selected,*,timeout,cancelled,authorize):
            assert authorize()==selected
            calls.append('exchange')
            return {'status':'delivered_unverified','selected':'nested-adapter'}
        def close(self):calls.append('close')
    selected=binding('custom',entrypoint='owned_adapter:Outer.Adapter',identity='owned-nested')
    module=SimpleNamespace(Outer=SimpleNamespace(Adapter=Adapter))
    driver=NativeCommandTransport(selected,libraries={'owned_adapter':module})
    command=request(selected,'custom.operation',exact='author-defined')
    receipt=driver.exchange(command,timeout=1,cancelled=lambda:False,authorize=lambda:command)
    assert calls==['construct','exchange','close']
    assert receipt['evidence']['adapter_report']['selected']=='nested-adapter'
    assert receipt['physical_outcome']=='unverified'


def test_actual_owned_pty_query_preserves_native_serial_request_and_reply():
    if not hasattr(os, "openpty"): pytest.skip("owned PTY requires POSIX")
    master, slave = os.openpty()
    expected, response = b"MEASURE 999999999999999999999\n", b"+0.0000000000001\r\n"
    received, errors = [], []
    def serve():
        try:
            result = bytearray()
            while len(result) < len(expected):
                assert select.select([master], [], [], 2)[0]
                result.extend(os.read(master, len(expected) - len(result)))
            received.append(bytes(result))
            os.write(master, response)
        except BaseException as error:
            errors.append(error)
    thread = Thread(target=serve, daemon=True); thread.start()
    try:
        selected = binding("serial", port=os.ttyname(slave), read={"kind": "delimiter", "delimiter": data("\r\n")})
        command = request(selected, "serial.query", data=data(expected.decode()))
        receipt = native_transport_for(selected).exchange(command, timeout=1, cancelled=lambda: False, authorize=lambda: command)
        assert received == [expected]
        assert base64.b64decode(receipt["evidence"]["response"]["base64"]) == response
    finally:
        thread.join(3); os.close(master); os.close(slave)
        assert not thread.is_alive() and not errors, errors


def test_actual_tls_loopback_verifies_selected_ca_and_keeps_runtime_secret_paths_private(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    from datetime import datetime, timedelta, timezone
    import ipaddress
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "owned-loopback")])
    cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(
        x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1)).not_valid_after(
        datetime.now(timezone.utc) + timedelta(days=1)).add_extension(
        x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False).sign(key, hashes.SHA256())
    ca = tmp_path / "private-ca-path.pem"; private = tmp_path / "private-key.pem"
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private.chmod(0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(ca, private)
    listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(2)
    errors = []
    def serve():
        try:
            with listener.accept()[0] as raw, context.wrap_socket(raw, server_side=True) as connection:
                assert connection.recv(5) == b"query"
                connection.sendall(b"reply")
        except BaseException as error:
            errors.append(error)
    thread = Thread(target=serve, daemon=True); thread.start()
    try:
        selected = binding(port=listener.getsockname()[1], tls=True, read={"kind": "fixed_bytes", "count": 5})
        command = request(selected, "tcp.query", data=data("query"))
        result = native_transport_for(selected, runtime_secrets={"ca_certs": str(ca), "server_hostname": None}).exchange(
            command, timeout=1, cancelled=lambda: False, authorize=lambda: command)
        assert base64.b64decode(result["evidence"]["response"]["base64"]) == b"reply"
        assert str(ca) not in json.dumps(result)
    finally:
        listener.close(); thread.join(3)
        assert not thread.is_alive() and not errors, errors


def test_delayed_native_setup_cannot_outlive_deadline_then_write():
    class Clock:
        now = 0
        def monotonic(self): return self.now
        def time(self): return 1000 + self.now
    clock, writes, closed = Clock(), [], []
    class Client:
        def __init__(self, *args, **kwargs): pass
        def connect(self): clock.now += 2; return True
        def close(self): closed.append(True)
        def write_register(self, address, value, *, device_id): writes.append(value)
    selected = binding("modbus")
    command = request(selected, "modbus.write_register", address=16, value=2)
    driver = NativeCommandTransport(selected, clock=clock, libraries={"pymodbus.client": SimpleNamespace(ModbusTcpClient=Client)})
    with pytest.raises(NativeCommandTransportError, match="wait expired") as caught:
        driver.exchange(command, timeout=1, cancelled=lambda: False, authorize=lambda: command)
    assert writes == [] and closed == [True]
    assert caught.value.delivery_evidence["operational"]["application_write_attempts"] == 0


def test_local_stop_bypasses_only_allowlist_for_exact_selected_operator_payload(monkeypatch):
    peer = FakeSocket(); monkeypatch.setattr(socket, "create_connection", lambda *a, **k: peer)
    selected = binding()
    selected["command_profile"]["allowed_operations"] = ["tcp.query"]
    selected["local_stop"] = {"operation": "tcp.write", "data": data("operator-selected-stop")}
    command = request(selected, "tcp.write", data=data("operator-selected-stop")); command["kind"] = "local_stop"
    result = native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    assert result["status"] == "delivered_unverified" and peer.writes == [b"operator-selected-stop"]
    command["payload"]["data"] = data("different")
    with pytest.raises(ValueError, match="stop"):
        native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    assert peer.writes == [b"operator-selected-stop"]


def test_zero_or_expired_selected_wait_has_no_write(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("expired request must not connect"))
    selected = binding(); command = request(selected, "tcp.write", data=data("bytes"))
    with pytest.raises(NativeCommandTransportError):
        native_transport_for(selected).exchange(command, timeout=0, cancelled=lambda: False, authorize=lambda: command)
    command["expires_at"] = time.time() - 1
    with pytest.raises(NativeCommandTransportError):
        native_transport_for(selected).exchange(command, timeout=1, cancelled=lambda: False, authorize=lambda: command)


def test_native_secrets_in_primary_error_are_redacted(monkeypatch):
    def fail(*args, **kwargs): raise OSError("failed private-runtime-sentinel")
    monkeypatch.setattr(socket, "create_connection", fail)
    selected = binding(tls=True); command = request(selected, "tcp.write", data=data("bytes"))
    with pytest.raises(NativeCommandTransportError) as caught:
        native_transport_for(selected, runtime_secrets={"certfile": "private-runtime-cert.pem", "key_password": "private-runtime-sentinel"}).exchange(
            command, timeout=1, cancelled=lambda: False, authorize=lambda: command)
    assert "private-runtime-sentinel" not in json.dumps(caught.value.delivery_evidence)


def test_decoded_modbus_reply_budget_uses_actual_bit_count_not_requested_count():
    class Client:
        def __init__(self, *args, **kwargs): pass
        def connect(self): return True
        def close(self): pass
        def read_coils(self, address, *, count, device_id, no_response_expected=False):
            return SimpleNamespace(isError=lambda: False, bits=[True] * 80)
    selected = binding("modbus"); selected["command_profile"]["max_response_bytes"] = 10
    command = request(selected, "modbus.read_coils", address=16, count=1)
    driver = NativeCommandTransport(selected, libraries={"pymodbus.client": SimpleNamespace(ModbusTcpClient=Client)})
    with pytest.raises(NativeCommandTransportError, match="byte budget") as caught:
        driver.exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    assert len(caught.value.delivery_evidence["evidence"]["sdk"]["response"]["bits"]) == 80


def test_binding_accessor_cannot_redirect_a_previously_validated_driver(monkeypatch):
    calls = []; peer = FakeSocket()
    def connect(address, **kwargs): calls.append(address); return peer
    monkeypatch.setattr(socket, "create_connection", connect)
    selected = binding(port=15000)
    driver = native_transport_for(selected)
    driver.binding["transport"]["port"] = 16000
    selected["transport"]["port"] = 17000
    command = request(binding(port=15000), "tcp.write", data=data("exact"))
    driver.exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    assert calls == [("127.0.0.1", 15000)]


@pytest.mark.parametrize("kind,transport,secrets", [
    ("tcp", {}, {"password": "not-used"}),
    ("tcp", {}, {"ca_certs": "not-used.pem"}),
    ("mqtt", {}, {"password": "missing-username"}),
    ("mqtt", {"tls": True}, {"server_hostname": "unsupported-native-override"}),
])
def test_unsupported_runtime_secrets_never_silently_change_or_disable_selected_security(kind, transport, secrets):
    with pytest.raises(ValueError, match="runtime|TLS|username"):
        native_transport_for(binding(kind, **transport), runtime_secrets=secrets)


def test_serial_native_write_timeout_observes_remaining_selected_operation_wait(monkeypatch):
    class Port:
        write_timeout = 10
        def close(self): pass
        def write(self, value):
            assert 0 < self.write_timeout <= .5
            return len(value)
    from tinystudio.lab.hardware_transports import SerialEndpoint
    monkeypatch.setattr(SerialEndpoint, "open", lambda *args, **kwargs: Port())
    selected = binding("serial"); command = request(selected, "serial.write", data=data("bytes"))
    result = native_transport_for(selected).exchange(command, timeout=.5, cancelled=lambda: False, authorize=lambda: command)
    assert result["operational"]["serial_write_timeout"]["selected_native"] == 10
