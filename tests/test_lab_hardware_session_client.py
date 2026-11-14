"""Attempt clients use owned local sockets and never obtain gateway lease APIs."""
from contextlib import contextmanager
from copy import deepcopy
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from tinystudio.lab.hardware_session_client import HardwareSessionClient, HardwareSessionError
from tinystudio.lab.session_protocol import encode_frame, read_frame


def descriptor(path):
    return {'format': 'tinystudio.hardware-session-ipc.v1', 'path': str(path),
            'capability': 'private-per-attempt-capability-sentinel', 'session_id': 'run-attempt-owner',
            'profile': {'allowed_operations': ['serial.write'], 'capability_claims': {}}}


@contextmanager
def server(handler, count=1):
    with tempfile.TemporaryDirectory(prefix='ts-session-test-') as directory:
        path = Path(directory) / 'ipc.sock'
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path)); listener.listen(); listener.settimeout(2)
        errors = []
        def serve():
            try:
                for _ in range(count):
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(2)
                        handler(connection)
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=serve)
        worker.start()
        try:
            yield path
        finally:
            listener.close()
            worker.join(3)
        assert not worker.is_alive(), 'owned Unix server did not finish'
        assert not errors, errors


def test_actual_unix_submissions_use_exact_envelopes_and_preserve_authored_payload_bytes():
    received = []
    def handle(connection):
        request = read_frame(connection)
        received.append(request)
        connection.sendall(encode_frame({'ok': True, 'receipt': {'decision_id': request['decision_id'],
            'payload': request['payload'], 'physical_outcome': 'unverified'}}))
    payload = {'operation': 'serial.write', 'data': {'encoding': 'utf8', 'value': 'é\x00\r\n9007199254740993'}}
    with server(handle, count=2) as path:
        supplied = descriptor(path)
        client = HardwareSessionClient(supplied, timeout=1)
        for identity in ('decision-one', 'decision-two'):
            receipt = client.submit(identity, payload, valid_for_seconds=.125)
            assert receipt['payload'] == payload and receipt['decision_id'] == identity
        client.close()
    assert received == [{'capability': supplied['capability'], 'decision_id': identity,
        'payload': payload, 'valid_for_seconds': .125} for identity in ('decision-one', 'decision-two')]


def test_remote_error_keeps_delivery_and_lifecycle_evidence_without_echoing_descriptor_secret():
    delivery = {'status': 'unknown', 'evidence': {'response': {'base64': 'AP8K', 'size': 3}}}
    lifecycle = {'physical_outcome': 'unverified', 'status': 'recovery_required'}
    def handle(connection):
        request = read_frame(connection)
        connection.sendall(encode_frame({'ok': False, 'error': {'type': 'GatewayError',
            'message': 'rejected ' + request['capability'], 'delivery_receipt': delivery, 'lifecycle_receipt': lifecycle}}))
    with server(handle) as path:
        supplied = descriptor(path)
        client = HardwareSessionClient(supplied, timeout=1)
        with pytest.raises(HardwareSessionError) as caught:
            client.submit('one', {'operation': 'x'}, valid_for_seconds=1)
        client.close()
    assert supplied['capability'] not in str(caught.value)
    assert caught.value.remote_type == 'GatewayError'
    assert caught.value.delivery_receipt == delivery and caught.value.lifecycle_receipt == lifecycle


def test_lost_reply_is_explicit_and_does_not_retry_a_submitted_decision():
    requests = []
    def handle(connection):
        requests.append(read_frame(connection))
    with server(handle) as path:
        client = HardwareSessionClient(descriptor(path), timeout=.2)
        with pytest.raises(HardwareSessionError, match='response'):
            client.submit('once', {'operation': 'x'}, valid_for_seconds=1)
        client.close()
    assert len(requests) == 1 and requests[0]['decision_id'] == 'once'


@pytest.mark.parametrize('response', [{'ok': True}, {'ok': 1, 'receipt': {}},
    {'ok': True, 'receipt': []}, {'ok': False, 'error': 'bad'}, {'ok': False, 'error': {'type': 'x'}},
    {'ok': True, 'receipt': {}, 'capability': 'unexpected'}])
def test_malformed_response_never_becomes_a_delivery_receipt(response):
    def handle(connection):
        read_frame(connection)
        connection.sendall(encode_frame(response))
    with server(handle) as path:
        client = HardwareSessionClient(descriptor(path), timeout=1)
        with pytest.raises(HardwareSessionError):
            client.submit('one', {'operation': 'x'}, valid_for_seconds=1)
        client.close()


def test_profile_is_defensive_constructor_does_no_io_and_close_prevents_new_calls(monkeypatch):
    import tinystudio.lab.hardware_session_client as module
    monkeypatch.setattr(module.socket, 'socket', lambda *args, **kwargs: pytest.fail('no socket may be opened'))
    supplied = descriptor('/operator-private/ipc.sock')
    original = deepcopy(supplied)
    client = HardwareSessionClient(supplied)
    supplied['profile']['allowed_operations'].clear()
    client.profile['allowed_operations'].clear()
    assert client.profile == original['profile']
    for method in ('acquire', 'heartbeat', 'reconcile', 'local_stop', 'release'):
        assert not hasattr(client, method)
    client.close(); client.close()
    with pytest.raises(HardwareSessionError, match='closed'):
        client.submit('one', {'operation': 'x'}, valid_for_seconds=1)


def test_connect_failure_is_not_retried_and_never_logs_descriptor_or_native_exception_text(monkeypatch):
    import tinystudio.lab.hardware_session_client as module
    supplied = descriptor('/operator-private/ipc.sock')
    attempts = []
    def refused(*args, **kwargs):
        attempts.append((args, kwargs))
        raise OSError(supplied['capability'] + ' ' + supplied['path'])
    monkeypatch.setattr(module.socket, 'socket', refused)
    client = HardwareSessionClient(supplied)
    with pytest.raises(HardwareSessionError) as caught:
        client.submit('once', {'operation': 'x'}, valid_for_seconds=1)
    assert len(attempts) == 1
    assert supplied['capability'] not in str(caught.value) and supplied['path'] not in str(caught.value)
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize('timeout', [-1, True, '1', float('nan'), float('inf')])
def test_invalid_timeout_is_rejected_without_constructing_a_socket(monkeypatch, timeout):
    import tinystudio.lab.hardware_session_client as module
    monkeypatch.setattr(module.socket, 'socket', lambda *args, **kwargs: pytest.fail('no socket construction'))
    with pytest.raises(HardwareSessionError):
        HardwareSessionClient(descriptor('/operator-private/ipc.sock'), timeout=timeout)


@pytest.mark.parametrize('payload', [None, [], {'operation': 'x', 'extra': float('nan')},
    {'operation': 'x', 'raw': b'bytes'}, {'operation': 'x', 'tuple': (1, 2)}])
def test_invalid_payload_is_rejected_before_connecting(monkeypatch, payload):
    import tinystudio.lab.hardware_session_client as module
    monkeypatch.setattr(module.socket, 'socket', lambda *args, **kwargs: pytest.fail('invalid payload must not connect'))
    client = HardwareSessionClient(descriptor('/operator-private/ipc.sock'))
    with pytest.raises(HardwareSessionError):
        client.submit('one', payload, valid_for_seconds=1)


@pytest.mark.parametrize('valid_for', [0, -1, True, None, float('inf'), float('nan')])
def test_invalid_validity_is_rejected_before_connecting(monkeypatch, valid_for):
    import tinystudio.lab.hardware_session_client as module
    monkeypatch.setattr(module.socket, 'socket', lambda *args, **kwargs: pytest.fail('invalid request must not connect'))
    client = HardwareSessionClient(descriptor('/operator-private/ipc.sock'))
    with pytest.raises(HardwareSessionError):
        client.submit('one', {'operation': 'x'}, valid_for_seconds=valid_for)


@pytest.mark.parametrize('change', [{'path': 'relative'}, {'capability': ''}, {'session_id': ''},
    {'format': 'other'}, {'profile': []}, {'extra': 'field'}])
def test_malformed_descriptor_is_rejected_without_secret_in_error(change):
    supplied = {**descriptor('/operator-private/ipc.sock'), **change}
    with pytest.raises(HardwareSessionError) as caught:
        HardwareSessionClient(supplied)
    assert 'private-per-attempt-capability-sentinel' not in str(caught.value)
