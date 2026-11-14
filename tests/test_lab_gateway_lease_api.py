"""Lease HTTP contracts use private journals and an explicit fake endpoint only."""
from copy import deepcopy
import hashlib
import json
import threading

from fastapi.testclient import TestClient
import pytest

from tests.test_lab_native_gateway import Clock, Device, binding, payload
from tinystudio.lab import gateway_api
from tinystudio.lab.gateway import CommandGateway
from tinystudio.lab.gateway_configuration import GatewayApiPaths, GatewayRequestPolicy


PERMISSIONS = {'state', 'journal', 'acquire', 'heartbeat', 'commands', 'stop', 'reconcile', 'release'}


def provision(tmp_path, monkeypatch, *, permissions=None, lease_policy=None, request_policy=None, local_stop=None):
    selected = binding()
    if local_stop is not None:
        selected['local_stop'] = local_stop
    if lease_policy is not None:
        selected['lease_policy'] = lease_policy
    secret = 'private-owned-lease-api-token'
    token = {'endpoint_id': selected['endpoint_id'], 'operator_id': selected['operator_id'],
             'token_sha256': hashlib.sha256(secret.encode()).hexdigest(),
             'permissions': sorted(PERMISSIONS if permissions is None else permissions)}
    files = []
    for name, value in [('binding', selected), ('token', token)]:
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        files.append(path)
    directory = tmp_path / 'journal'
    directory.mkdir(mode=0o700)
    device, clock = Device(), Clock()
    monkeypatch.setattr(gateway_api, 'CommandGateway', lambda *args, **kwargs: CommandGateway(*args, **kwargs, clock=clock))
    app = gateway_api.create_gateway_app(files[0], directory, files[1], lambda binding: device,
                                          request_policy=request_policy)
    return app, device, clock, {'Authorization': 'Bearer ' + secret}


def acquire(client, *, owner='run-one', ttl=20):
    response = client.post('/acquire', json={'endpoint_id': 'owned-native', 'owner': owner, 'ttl': ttl})
    assert response.status_code == 200, response.text
    return response.json()


def release(lease, *, request_id='release-one'):
    return {'endpoint_id': 'owned-native', 'request_id': request_id, 'owner': lease['owner'], 'epoch': lease['epoch']}


def test_release_and_lease_events_are_central_routes_with_explicit_permissions():
    paths = GatewayApiPaths(base='/selected')
    assert paths.route('release') == '/selected/release'
    assert paths.route('lease-events') == '/selected/lease-events'
    assert '/selected/release' in paths.mutation_routes
    assert '/selected/lease-events' not in paths.mutation_routes
    assert 'release' in gateway_api.PERMISSIONS


def test_release_receipt_replays_after_expiry_without_affecting_a_later_owner(tmp_path, monkeypatch):
    app, device, clock, headers = provision(tmp_path, monkeypatch)
    with TestClient(app, headers=headers) as client:
        lease = acquire(client)
        body = release(lease)
        response = client.post('/release', json=body)
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt['trigger'] == 'release' and receipt['action'] == 'revoke_only'
        assert receipt['host_state'] == 'idle' and receipt['host_authority_revoked'] is True
        assert receipt['physical_outcome'] == 'unverified' and receipt['device_application_confirmed'] is False
        later = acquire(client, owner='run-two', ttl=1000)
        clock.value = 1040.
        repeated = client.post('/release', json=body)
        assert repeated.status_code == 200 and repeated.json() == receipt
        state = client.get('/state').json()
        assert state['owner'] == later['owner'] and state['epoch'] == later['epoch']
        assert state['lease_watchdog']['enabled'] is False
        assert state['lease_watchdog']['alive'] is False
        assert state['lease_policy']['watchdog_interval_seconds'] is None
        events = client.get('/lease-events').json()
        assert len(events['items']) == 1 and events['items'][0]['receipt'] == receipt
        assert isinstance(events['items'][0]['request'], dict)
        assert 'private-owned-lease-api-token' not in json.dumps([receipt, events, state])
        assert device.effects == []


@pytest.mark.parametrize('field,value,status', [
    ('endpoint_id', 'other-endpoint', 403), ('owner', 'other-owner', 409),
    ('epoch', True, 422), ('epoch', 1.0, 422), ('epoch', '1', 422), ('epoch', 0, 422),
    ('request_id', '', 422), ('owner', '', 422),
])
def test_release_rejects_invalid_scope_or_strict_identity_before_any_action(tmp_path, monkeypatch, field, value, status):
    app, device, _, headers = provision(tmp_path, monkeypatch)
    with TestClient(app, headers=headers) as client:
        lease = acquire(client)
        body = {**release(lease), field: value}
        response = client.post('/release', json=body)
        assert response.status_code == status, response.text
        assert device.effects == []
        assert client.get('/lease-events').json()['items'] == []
        assert client.get('/state').json()['owner'] == lease['owner']


def test_existing_token_permissions_do_not_silently_gain_release_or_lease_event_access(tmp_path, monkeypatch):
    app, device, _, headers = provision(tmp_path, monkeypatch, permissions={'state', 'acquire'})
    with TestClient(app, headers=headers) as client:
        lease = acquire(client)
        assert client.post('/release', json=release(lease), headers={'Authorization': 'Bearer wrong'}).status_code == 401
        assert client.post('/release', json=release(lease)).status_code == 403
        assert client.get('/lease-events').status_code == 403
        assert client.get('/state').status_code == 200
        assert device.effects == []


def test_lease_events_use_journal_permission_and_page_without_removing_retained_events(tmp_path, monkeypatch):
    app, device, _, headers = provision(tmp_path, monkeypatch, permissions={'journal', 'acquire', 'release'})
    with TestClient(app, headers=headers) as client:
        for index in range(5):
            lease = acquire(client, owner='run-' + str(index))
            assert client.post('/release', json=release(lease, request_id='release-' + str(index))).status_code == 200
        first = client.get('/lease-events?offset=0&limit=2').json()
        middle = client.get('/lease-events?offset=2&limit=2').json()
        last = client.get('/lease-events?offset=4&limit=2').json()
        assert first['endpoint_id'] == 'owned-native'
        assert first['next_offset'] == 2 and middle['next_offset'] == 4 and last['next_offset'] is None
        assert [len(page['items']) for page in (first, middle, last)] == [2, 2, 1]
        assert len(client.get('/lease-events').json()['items']) == 5
        assert client.get('/lease-events?endpoint_id=other-endpoint').status_code == 403
        assert client.get('/lease-events?limit=0').status_code == 422
        assert client.get('/lease-events?offset=-1').status_code == 422
        assert client.get('/state').status_code == 403
        assert device.effects == []


def test_uncertain_selected_stop_returns_the_core_lifecycle_receipt_without_redelivery(tmp_path, monkeypatch):
    app, device, _, headers = provision(tmp_path, monkeypatch, lease_policy={'on_release': 'local_stop'},
                                          local_stop=payload('OUTPUT OFF\n'))
    device.failure = TimeoutError('owned unknown stop response')
    with TestClient(app, headers=headers) as client:
        lease = acquire(client)
        response = client.post('/release', json=release(lease))
        assert response.status_code == 409, response.text
        retained = response.json()['lifecycle_receipt']
        assert retained['state'] == 'unknown' and retained['host_authority_revoked'] is True
        assert retained['physical_outcome'] == 'unverified'
        assert retained['delivery_evidence']
        assert response.json()['retry_allowed'] is False
        assert response.json()['recovery_requires_operator'] is True
        assert len(device.effects) == 1
        assert client.post('/release', json=release(lease)).status_code == 409
        assert len(device.effects) == 1
        assert client.get('/lease-events').json()['items'][0]['receipt'] == retained


def test_release_route_rejects_ambiguous_json_and_selected_http_budget_before_actions(tmp_path, monkeypatch):
    app, device, _, headers = provision(tmp_path, monkeypatch)
    with TestClient(app, headers=headers) as client:
        wire = b'{"endpoint_id":"owned-native","request_id":"a","request_id":"b","owner":"run-one","epoch":1}'
        response = client.post('/release', content=wire, headers={'Content-Type': 'application/json'})
        assert response.status_code == 422 and 'duplicate' in response.json()['detail']
        assert client.get('/lease-events').json()['items'] == []
        assert device.effects == []
    second = tmp_path / 'second'
    second.mkdir()
    app, device, _, headers = provision(second, monkeypatch, request_policy=GatewayRequestPolicy(0))
    with TestClient(app, headers=headers) as client:
        response = client.post('/release', json=release({'owner': 'run-one', 'epoch': 1}))
        assert response.status_code == 413
        assert client.get('/lease-events').json()['items'] == [] and device.effects == []


def test_lifespan_reports_the_selected_watchdog_and_closes_it_before_the_gateway(tmp_path, monkeypatch):
    calls = []
    selected = {'enabled': True, 'alive': True, 'error_count': 3, 'physical_outcome': 'unverified'}
    class Watchdog:
        def __init__(self, gateway):
            self.gateway = gateway
            calls.append('watchdog.created')
        def start(self):
            calls.append('watchdog.started')
            return self
        def close(self):
            calls.append('watchdog.closed')
            self.gateway.snapshot()  # The core must still be open here.
        def snapshot(self):
            return deepcopy(selected)
    actual_gateway = gateway_api.CommandGateway
    actual_close = actual_gateway.close
    def close(gateway):
        calls.append('gateway.closed')
        return actual_close(gateway)
    monkeypatch.setattr(actual_gateway, 'close', close)
    monkeypatch.setattr(gateway_api, 'GatewayWatchdog', Watchdog)
    app, _, _, headers = provision(tmp_path, monkeypatch)
    with TestClient(app, headers=headers) as client:
        assert client.get('/state').json()['lease_watchdog'] == selected
    assert calls == ['watchdog.created', 'watchdog.started', 'watchdog.closed', 'gateway.closed']
    assert app.state.gateway is None and app.state.lease_watchdog is None


def test_watchdog_start_failure_closes_the_allocated_gateway_without_losing_the_error(tmp_path, monkeypatch):
    calls = []
    class Watchdog:
        def __init__(self, gateway): self.gateway = gateway
        def start(self): raise RuntimeError('owned watchdog start failure')
        def close(self):
            calls.append('watchdog.closed')
            self.gateway.snapshot()
    actual_gateway = gateway_api.CommandGateway
    actual_close = actual_gateway.close
    def close(gateway):
        calls.append('gateway.closed')
        return actual_close(gateway)
    monkeypatch.setattr(actual_gateway, 'close', close)
    monkeypatch.setattr(gateway_api, 'GatewayWatchdog', Watchdog)
    app, _, _, headers = provision(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match='owned watchdog start failure'):
        with TestClient(app, headers=headers):
            pytest.fail('startup failure must not serve HTTP')
    assert calls == ['watchdog.closed', 'gateway.closed']
    assert app.state.gateway is None and app.state.lease_watchdog is None


def test_selected_watchdog_ticks_in_the_lifespan_and_joins_before_core_shutdown(tmp_path, monkeypatch):
    app, device, clock, headers = provision(tmp_path, monkeypatch,
        lease_policy={'on_expiry': 'revoke_only', 'watchdog_interval_seconds': .01})
    finished = threading.Event()
    with TestClient(app, headers=headers) as client:
        gateway, worker = app.state.gateway, app.state.lease_watchdog
        actual = gateway.watchdog_tick
        def tick():
            receipts = actual()
            if receipts:
                finished.set()
            return receipts
        monkeypatch.setattr(gateway, 'watchdog_tick', tick)
        acquire(client)
        clock.value = 1021.
        assert finished.wait(2), 'owned scheduled watchdog did not process the selected expiry action'
        state = client.get('/state').json()
        assert state['lease_watchdog']['enabled'] is True and state['lease_watchdog']['alive'] is True
        assert state['state'] == 'recovery_required' and device.effects == []
        assert len(client.get('/lease-events').json()['items']) == 1
    assert worker.snapshot()['alive'] is False and worker.snapshot()['stop_requested'] is True
