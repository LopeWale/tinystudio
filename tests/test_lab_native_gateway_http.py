"""Actual owned HTTP -> native Paho -> Mosquitto delivery, without physical claims."""
from contextlib import contextmanager
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import threading
import time
from types import SimpleNamespace
import uuid

import httpx
import pytest
import uvicorn

from hardware_emulators import OwnedMqttBroker
from tinystudio.lab.gateway_api import PERMISSIONS, create_gateway_app


@pytest.fixture
def broker(tmp_path):
    binary = os.environ.get('TINYSTUDIO_TEST_MQTT_BROKER')
    if not binary or not Path(binary).is_file() or not os.access(binary, os.X_OK):
        pytest.skip('native HTTP/MQTT wire evidence unavailable: select an owned executable with TINYSTUDIO_TEST_MQTT_BROKER')
    pytest.importorskip('paho.mqtt.client', reason='native HTTP/MQTT wire evidence requires paho-mqtt')
    with OwnedMqttBroker(binary, tmp_path / 'broker') as owned:
        yield owned


def provision(root, broker, *, permissions=None, local_stop=None, send_timeout=2):
    root.mkdir(mode=0o700)
    topic = 'owned-native-gateway/' + uuid.uuid4().hex + '/command'
    binding = {'endpoint_id': 'owned-native-endpoint', 'operator_id': 'owned-operator',
        'transport': {'kind': 'mqtt', 'host': broker.host, 'port': broker.port, 'topic': topic, 'allow_dns': False},
        'command_profile': {'format': 'tinystudio.native-command-profile.v1'},
        'send_timeout': send_timeout, 'max_lease_seconds': None, 'local_stop': local_stop}
    secret = 'owned-gateway-token-' + uuid.uuid4().hex
    token = {'endpoint_id': binding['endpoint_id'], 'operator_id': binding['operator_id'],
             'token_sha256': hashlib.sha256(secret.encode()).hexdigest(),
             'permissions': sorted(PERMISSIONS if permissions is None else permissions)}
    binding_file, token_file = root / 'binding.json', root / 'token.json'
    for path, value in ((binding_file, binding), (token_file, token)):
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    journal = root / 'journal'
    journal.mkdir(mode=0o700)
    return SimpleNamespace(binding=binding, binding_file=binding_file, token_file=token_file,
        journal=journal, headers={'Authorization': 'Bearer ' + secret}, secret=secret, topic=topic)


@contextmanager
def gateway_http(selected, **options):
    app = create_gateway_app(selected.binding_file, selected.journal, selected.token_file, **options)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, lifespan='on', log_level='error', access_log=False,
        timeout_graceful_shutdown=2))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise AssertionError('owned gateway HTTP server failed to start')
            time.sleep(.01)
        with httpx.Client(base_url='http://127.0.0.1:' + str(listener.getsockname()[1]),
                          headers=selected.headers, trust_env=False, timeout=5) as client:
            yield client
    finally:
        server.should_exit = True
        thread.join(timeout=4)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=2)
        listener.close()
        assert not thread.is_alive(), 'owned HTTP server/lifespan failed to close'


@contextmanager
def subscriber(broker, topic):
    import paho.mqtt.client as mqtt
    ready = threading.Event()
    messages = queue.Queue()
    errors = []
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id='owned-http-observer-' + uuid.uuid4().hex, reconnect_on_failure=False)
    def connected(client, userdata, flags, code, properties):
        if code.is_failure:
            errors.append(str(code)); ready.set(); return
        result, _ = client.subscribe(topic, qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            errors.append('owned subscriber subscription failed'); ready.set()
    def subscribed(client, userdata, mid, codes, properties):
        if any(code.is_failure for code in codes): errors.append('owned subscription rejected')
        ready.set()
    client.on_connect = connected
    client.on_subscribe = subscribed
    client.on_message = lambda client, userdata, message: messages.put({
        'payload': bytes(message.payload), 'qos': message.qos, 'retain': bool(message.retain)})
    assert client.connect(broker.host, broker.port, keepalive=30) == mqtt.MQTT_ERR_SUCCESS
    client.loop_start()
    try:
        assert ready.wait(timeout=3) and not errors, errors
        yield messages
    finally:
        client.disconnect()
        client.loop_stop()


def acquire(client, selected, *, owner='owned-run', ttl=30):
    response = client.post('/acquire', json={'endpoint_id': selected.binding['endpoint_id'], 'owner': owner, 'ttl': ttl})
    assert response.status_code == 200, response.text
    return response.json()


def command(selected, lease, *, id_='owned-command', data=None, qos=1):
    return {'endpoint_id': selected.binding['endpoint_id'], 'owner': lease['owner'], 'epoch': lease['epoch'],
        'command_id': id_, 'expires_at': time.time() + 5,
        'payload': {'operation': 'mqtt.publish', 'data': data or {'encoding': 'utf8', 'value': 'SET -0.125\r\n'},
                    'qos': qos, 'retain': False}}


def assert_no_publication(messages):
    with pytest.raises(queue.Empty): messages.get(timeout=.15)


@pytest.mark.parametrize('data,wire', [
    ({'encoding': 'utf8', 'value': 'SET -0.125 µA 9007199254740993\r\n'}, 'SET -0.125 µA 9007199254740993\r\n'.encode()),
    ({'encoding': 'hex', 'value': '00ff10c37f0a'}, bytes.fromhex('00ff10c37f0a')),
    ({'encoding': 'base64', 'value': base64.b64encode(b'\x00\x01\xff\r\npayload').decode()}, b'\x00\x01\xff\r\npayload'),
])
def test_authenticated_http_delivers_exact_native_bytes_and_duplicate_never_republishes(tmp_path, broker, data, wire):
    selected = provision(tmp_path / 'gateway', broker)
    with subscriber(broker, selected.topic) as messages, gateway_http(selected) as client:
        profile = client.get('/command-profile')
        assert profile.status_code == 200
        assert profile.json()['format'] == 'tinystudio.native-command-profile.v1'
        lease = acquire(client, selected, ttl=7200)
        body = command(selected, lease, data=data)
        original = deepcopy(body)
        response = client.post('/commands', json=body)
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt['status'] == 'protocol_accepted'
        assert receipt['device_application_confirmed'] is False and receipt['physical_outcome'] == 'unverified'
        assert receipt['evidence']['sdk']['acknowledgement_scope'] == 'broker_protocol_only'
        assert receipt['evidence']['sdk']['wire_capture_available'] is False
        actual = messages.get(timeout=3)
        assert actual == {'payload': wire, 'qos': 1, 'retain': False}
        captured = receipt['evidence']['request']
        assert base64.b64decode(captured['base64']) == wire
        assert captured['size'] == len(wire) and captured['sha256'] == hashlib.sha256(wire).hexdigest()
        duplicate = client.post('/commands', json=body)
        assert duplicate.status_code == 200 and duplicate.json() == receipt
        assert_no_publication(messages)
        assert body == original
        row, = client.get('/journal').json()['items']
        assert row['state'] == 'protocol_accepted'
        assert json.loads(row['request'])['payload']['data'] == data
        assert json.loads(row['ack']) == receipt
        state = client.get('/state').json()
        assert state['state'] == 'owned' and state['physical_qualified'] is False
        assert state['command_profile']['profile_digest'] == receipt['profile_digest']
        assert selected.secret not in json.dumps([receipt, row, state])


def test_profile_permissions_endpoint_scope_and_invalid_envelopes_cause_no_native_io(tmp_path, broker):
    selected = provision(tmp_path / 'gateway', broker, permissions={'acquire', 'journal'})
    with subscriber(broker, selected.topic) as messages, gateway_http(selected) as client:
        assert client.get('/command-profile').status_code == 403
        assert client.get('/state').status_code == 403
        assert client.post('/acquire', headers={'Authorization': 'Bearer wrong'},
            json={'endpoint_id': selected.binding['endpoint_id'], 'owner': 'owned-run'}).status_code == 401
        assert client.post('/acquire', json={'endpoint_id': 'foreign-endpoint', 'owner': 'owned-run'}).status_code == 403
        lease = acquire(client, selected)
        response = client.post('/commands', json=command(selected, lease))
        assert response.status_code == 403 and selected.secret not in response.text
        assert client.get('/journal').json()['items'] == []
        assert_no_publication(messages)
    broker.close()
    assert broker.log_text.count('New client connected') == 1, broker.log_text
    assert 'Received PUBLISH' not in broker.log_text


def test_unconfigured_stop_revokes_host_authority_without_sending_or_claiming_device_stop(tmp_path, broker):
    selected = provision(tmp_path / 'gateway', broker)
    with subscriber(broker, selected.topic) as messages, gateway_http(selected) as client:
        lease = acquire(client, selected)
        assert client.get('/command-profile').json()['local_stop_configured'] is False
        stop = {'endpoint_id': selected.binding['endpoint_id'], 'request_id': 'owned-stop'}
        response = client.post('/stop', json=stop)
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt['status'] == 'not_configured' and receipt['device_application_confirmed'] is False
        assert receipt['physical_outcome'] == 'unverified'
        assert client.post('/stop', json=stop).json() == receipt
        assert client.get('/state').json()['state'] == 'recovery_required'
        assert client.post('/commands', json=command(selected, lease)).status_code == 409
        row, = client.get('/journal').json()['items']
        assert row['state'] == 'not_configured'
        assert_no_publication(messages)
    broker.close()
    assert broker.log_text.count('New client connected') == 1


def test_selected_stop_payload_is_sent_once_without_promoting_broker_receipt_to_stopped_state(tmp_path, broker):
    wire = b'STOP\x00\r\n'
    stop_payload = {'operation': 'mqtt.publish', 'data': {'encoding': 'base64', 'value': base64.b64encode(wire).decode()}, 'qos': 1, 'retain': False}
    selected = provision(tmp_path / 'gateway', broker, local_stop=stop_payload)
    with subscriber(broker, selected.topic) as messages, gateway_http(selected) as client:
        acquire(client, selected)
        assert client.get('/command-profile').json()['local_stop_configured'] is True
        body = {'endpoint_id': selected.binding['endpoint_id'], 'request_id': 'owned-configured-stop'}
        response = client.post('/stop', json=body)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'protocol_accepted'
        assert response.json()['device_application_confirmed'] is False
        assert messages.get(timeout=3)['payload'] == wire
        assert client.post('/stop', json=body).json() == response.json()
        assert_no_publication(messages)
        assert client.get('/state').json()['state'] == 'recovery_required'
        assert client.post('/reconcile', json={'endpoint_id': selected.binding['endpoint_id']}).status_code == 409
        evidence = {'acknowledge_unverified_outcomes': True, 'operator_note': 'Host reset only; no device stop was verified.'}
        reconciled = client.post('/reconcile', json={'endpoint_id': selected.binding['endpoint_id'], 'evidence': evidence})
        assert reconciled.status_code == 200
        assert reconciled.json()['evidence']['classification'] == 'operator_attestation'
        assert reconciled.json()['evidence']['physical_outcome'] == 'unverified'
        assert reconciled.json()['evidence']['operator_evidence'] == evidence


def test_failed_native_delivery_receipt_survives_http_restart_and_operator_consumption(tmp_path, broker):
    selected = provision(tmp_path / 'gateway', broker, send_timeout=.2)
    broker.close()
    with gateway_http(selected) as client:
        lease = acquire(client, selected)
        body = command(selected, lease)
        response = client.post('/commands', json=body)
        assert response.status_code == 409, response.text
        assert response.json()['retry_allowed'] is False and response.json()['recovery_requires_operator'] is True
        receipt = response.json()['delivery_receipt']
        assert receipt['status'] == 'unknown' and receipt['device_application_confirmed'] is False
        assert receipt['physical_outcome'] == 'unverified'
        original_row, = client.get('/journal').json()['items']
        assert original_row['state'] == 'unknown' and json.loads(original_row['ack']) == receipt
    with gateway_http(selected) as restarted:
        assert restarted.get('/state').json()['state'] == 'recovery_required'
        assert restarted.get('/journal').json()['items'][0]['ack'] == original_row['ack']
        assert restarted.post('/commands', json=body).status_code == 409
        evidence = {'acknowledge_unverified_outcomes': True, 'operator_note': 'Unknown delivery; physical state remains unverified.'}
        result = restarted.post('/reconcile', json={'endpoint_id': selected.binding['endpoint_id'], 'evidence': evidence})
        assert result.status_code == 200, result.text
        retained = result.json()['evidence']
        assert retained['classification'] == 'operator_attestation'
        assert retained['consumed_command_ids'] == [body['command_id']]
        assert retained['physical_outcome'] == 'unverified' and retained['device_application_confirmed'] is False
        row, = restarted.get('/journal').json()['items']
        assert row['state'] == 'reconciled_consumed' and row['ack'] == original_row['ack']
        next_lease = acquire(restarted, selected, owner='new-owned-run')
        assert restarted.post('/commands', json=command(selected, next_lease, id_=body['command_id'])).status_code == 409
        assert len(restarted.get('/journal').json()['items']) == 1


@pytest.mark.parametrize('malformation', ['nested_duplicate', 'outer_duplicate', 'NaN', 'Infinity', '1e999'])
def test_ambiguous_or_nonfinite_http_envelopes_fail_before_native_publication(tmp_path, broker, malformation):
    selected = provision(tmp_path / 'gateway', broker)
    with subscriber(broker, selected.topic) as messages, gateway_http(selected) as client:
        lease = acquire(client, selected)
        body = command(selected, lease, data={'encoding': 'utf8', 'value': 'owned-first'})
        wire = json.dumps(body, separators=(',', ':'))
        if malformation == 'nested_duplicate':
            wire = wire.replace('"value":"owned-first"', '"value":"owned-first","value":"owned-second"')
        elif malformation == 'outer_duplicate':
            wire = wire.replace('"command_id":"owned-command"', '"command_id":"owned-first","command_id":"owned-command"')
        else:
            wire = wire.replace('"expires_at":' + str(body['expires_at']), '"expires_at":' + malformation)
        response = client.post('/commands', content=wire, headers={'content-type': 'application/json'})
        assert response.status_code == 422, response.text
        assert selected.secret not in response.text
        assert client.get('/journal').json()['items'] == []
        assert_no_publication(messages)
    broker.close()
    assert broker.log_text.count('New client connected') == 1


def test_literal_json_fault_injection_payload_and_large_default_http_body_remain_byte_exact(tmp_path, broker):
    selected = provision(tmp_path / 'gateway', broker)
    literal = '{"same":1,"same":2,"reading":NaN,"infinite":Infinity,"exponent":1e999}\r\n' + 'x' * (70 * 1024)
    with subscriber(broker, selected.topic) as messages, gateway_http(selected) as client:
        lease = acquire(client, selected)
        body = command(selected, lease, data={'encoding': 'utf8', 'value': literal})
        assert len(json.dumps(body).encode()) > 65536
        response = client.post('/commands', json=body)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'protocol_accepted'
        assert messages.get(timeout=3)['payload'] == literal.encode()


def test_foreign_command_scope_and_ambiguous_command_forms_do_not_open_the_native_driver(tmp_path, broker):
    selected = provision(tmp_path / 'gateway', broker)
    with subscriber(broker, selected.topic) as messages, gateway_http(selected) as client:
        lease = acquire(client, selected)
        original = command(selected, lease)
        assert client.get('/command-profile?endpoint_id=foreign-endpoint').status_code == 403
        assert client.post('/commands', json={**original, 'endpoint_id': 'foreign-endpoint'}).status_code == 403
        both = {**original, 'parameters': {'voltage': {'value': 1.0, 'unit': 'V'}}}
        neither = {key: value for key, value in original.items() if key != 'payload'}
        legacy = {**neither, 'parameters': {'voltage': {'value': 1.0, 'unit': 'V'}}}
        for body in (both, neither, legacy):
            response = client.post('/commands', json=body)
            assert response.status_code == 422, response.text
        assert client.get('/journal').json()['items'] == []
        assert_no_publication(messages)
    broker.close()
    assert broker.log_text.count('New client connected') == 1


def test_actual_tcp_query_failure_after_write_retains_unknown_delivery_through_http_restart(tmp_path):
    wire = b'MEASURE -0.125\r\n'
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(3)
    received, failures = [], []
    def serve():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(3)
                data = bytearray()
                while not data.endswith(b'\n'):
                    block = connection.recv(4096)
                    if not block: raise EOFError('owned query did not finish its command bytes')
                    data.extend(block)
                received.append(bytes(data))
                # Receiving command bytes is not application/physical evidence.
                # Close without the requested response to force an uncertain outcome.
                connection.shutdown(socket.SHUT_RDWR)
        except BaseException as exc:
            failures.append(exc)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    selected = provision(tmp_path / 'gateway', SimpleNamespace(host='127.0.0.1', port=listener.getsockname()[1]))
    selected.binding['transport'] = {'kind': 'tcp', 'host': '127.0.0.1', 'port': listener.getsockname()[1],
        'allow_dns': False, 'tls': False, 'read': {'kind': 'delimiter', 'delimiter': {'encoding': 'hex', 'value': '0a'}}}
    selected.binding_file.write_text(json.dumps(selected.binding))
    try:
        with gateway_http(selected) as client:
            lease = acquire(client, selected)
            body = command(selected, lease)
            body['payload'] = {'operation': 'tcp.query', 'data': {'encoding': 'base64', 'value': base64.b64encode(wire).decode()}}
            response = client.post('/commands', json=body)
            assert response.status_code == 409, response.text
            receipt = response.json()['delivery_receipt']
            assert receipt['status'] == 'unknown' and receipt['device_application_confirmed'] is False
            assert receipt['physical_outcome'] == 'unverified'
            assert base64.b64decode(receipt['evidence']['written']['base64']) == wire
            assert receipt['evidence']['written']['sha256'] == hashlib.sha256(wire).hexdigest()
            assert receipt['evidence']['response']['size'] == 0
            assert received == [wire]
            row, = client.get('/journal').json()['items']
            assert row['state'] == 'unknown' and json.loads(row['ack']) == receipt
            assert client.post('/commands', json=body).status_code == 409
        with gateway_http(selected) as restarted:
            retained, = restarted.get('/journal').json()['items']
            assert retained['state'] == 'unknown' and retained['ack'] == row['ack']
            assert restarted.get('/state').json()['state'] == 'recovery_required'
            assert restarted.post('/commands', json=body).status_code == 409
            assert received == [wire]
    finally:
        thread.join(timeout=3)
        listener.close()
        assert not thread.is_alive() and not failures, failures


@pytest.mark.parametrize('margin,status', [(None, 413), (-1, 413), (0, 200)])
def test_optional_http_body_byte_budget_zero_exceeded_and_exact(tmp_path, broker, margin, status):
    from tinystudio.lab.gateway_configuration import GatewayRequestPolicy
    selected = provision(tmp_path / 'gateway', broker)
    wire = json.dumps({'endpoint_id': selected.binding['endpoint_id'], 'owner': 'owned-run', 'ttl': 30}, separators=(',', ':')).encode()
    budget = 0 if margin is None else len(wire) + margin
    with gateway_http(selected, request_policy=GatewayRequestPolicy(max_bytes=budget)) as client:
        response = client.post('/acquire', content=wire, headers={'content-type': 'application/json'})
        assert response.status_code == status, response.text
        assert selected.secret not in response.text
        assert client.get('/journal').json()['items'] == []


def test_actual_server_accepts_large_private_binding_under_an_exact_selected_file_budget(tmp_path, broker):
    from tinystudio.lab.gateway_configuration import GatewayFilePolicy
    stop = {'operation': 'mqtt.publish', 'data': {'encoding': 'utf8', 'value': 'x' * (70 * 1024)}, 'qos': 1, 'retain': False}
    selected = provision(tmp_path / 'gateway', broker, local_stop=stop)
    size = selected.binding_file.stat().st_size
    assert size > 65536
    with pytest.raises(ValueError, match='byte budget'):
        create_gateway_app(selected.binding_file, selected.journal, selected.token_file,
                           file_policy=GatewayFilePolicy(max_bytes=size - 1))
    with gateway_http(selected, file_policy=GatewayFilePolicy(max_bytes=size)) as client:
        response = client.get('/command-profile')
        assert response.status_code == 200 and response.json()['local_stop_configured'] is True
        assert client.get('/state').json()['state'] == 'idle'
        assert client.get('/journal').json()['items'] == []
    broker.close()
    assert broker.log_text.count('New client connected') == 0, 'reading a command profile must not open its broker'
