"""Real frozen supervisors and parent-owned peers; no physical device claims."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_lab_runner import Transport
from tests.test_lab_b_workflow import database, integrated
from tinystudio.lab import runner as runner_module
from tinystudio.lab.contracts import ExperimentDefinition, checksum
from tinystudio.lab.emulator_workspace import EmulatorWorkspace
from tinystudio.lab.hardware_session_engine import validate_selection
from tinystudio.lab.hardware_session_configuration import HardwareSessionRuntimePaths
from tinystudio.lab.service import implementation_manifest, runtime_versions


PEER = '''import json,os,socket,sys,time
port,events,marker,mode=int(sys.argv[1]),sys.argv[2],sys.argv[3],sys.argv[4]
def record(event,**values):
    with open(events,'a') as stream:stream.write(json.dumps({'event':event,'pid':os.getpid(),**values})+'\\n')
with socket.socket() as server:
    server.bind(('127.0.0.1',port));server.listen()
    record('listening')
    print(os.environ.get('PRIVATE_TEST_TOKEN','absent'),flush=True)
    print(marker,flush=True)
    if mode=='exit':time.sleep(.05);sys.exit(0)
    stopped=False
    while not stopped:
        connection,_=server.accept()
        with connection:
            data=connection.recv(4096)
            if not data:continue
            record('received',hex=data.hex())
            if data==b'\\x00':connection.sendall(b'K');stopped=True
            else:connection.sendall(bytes(value^0xa5 for value in data))
'''


def fixture(tmp_path, *, reaction='record', mode='serve', startup=3, python_supervisor=False):
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    marker = 'owned-peer-' + uuid.uuid4().hex
    if python_supervisor:
        supervisor = b'import time\ndef run(client,context):\n time.sleep(.35)\n return {"metrics":{"completed":1}}\n'
        supervisor_choice = {'kind': 'python', 'artifact_id': 'supervisor', 'callable': 'run'}
    else:
        supervisor = (json.dumps({'decision_id': 'query', 'payload': {'operation': 'tcp.query',
            'data': {'encoding': 'hex', 'value': 'ff'}}, 'valid_for_seconds': 5}) + '\n').encode()
        supervisor_choice = {'kind': 'recorded', 'artifact_id': 'supervisor'}
    blobs = {'supervisor': supervisor, 'peer': PEER.encode()}
    emulator = {'binding_id': 'peer', 'assets': [{'name': 'peer', 'artifact_id': 'peer', 'destination': 'peer.py'}],
        'argv': [{'asset': 'peer'}, str(port), {'workspace': 'peer-events.jsonl'}, marker, mode],
        'readiness': {'kind': 'log_bytes', 'stream': 'stdout', 'marker': {'encoding': 'utf8', 'value': marker + '\n'}},
        'startup_timeout_seconds': startup, 'on_exit': reaction}
    definition = ExperimentDefinition(name='Owned managed peer', engine='hardware.session', operating_mode='simulation',
        inputs=[{'artifact_id': name, 'sha256': hashlib.sha256(data).hexdigest()} for name, data in blobs.items()],
        parameters={'binding_id': 'device', 'supervisor': supervisor_choice, 'emulator': emulator},
        execution={'backend': 'native', 'profile': 'trusted_native', 'max_seconds': 30, 'max_output_bytes': None}).model_dump()
    spec = {'schema_version': 1, 'definition': definition, 'implementation': implementation_manifest(),
        'runtime': runtime_versions(), 'input_manifest': [{'id': name, 'name': name + '.txt', 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest()} for name, data in blobs.items()]}
    attempt = {'run_id': 'managed-run', 'attempt_id': 'managed-attempt', 'lease_token': 'private-lab-token',
               'spec': spec, 'spec_digest': checksum(spec)}
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    stop = {'operation': 'tcp.query', 'data': {'encoding': 'hex', 'value': '00'}}
    gateway = {'endpoint_id': 'owned-peer', 'operator_id': 'fixture',
        'transport': {'kind': 'tcp', 'host': '127.0.0.1', 'port': port, 'read': {'kind': 'fixed_bytes', 'count': 1}},
        'command_profile': {'format': 'tinystudio.native-command-profile.v1'}, 'send_timeout': 1,
        'lease_policy': {'on_release': 'local_stop' if mode == 'serve' else 'revoke_only',
                         'on_cancel': 'local_stop' if mode == 'serve' else 'revoke_only'}, 'local_stop': stop}
    profile = {'schema_version': 1, 'engine_grants': {'hardware.session': {'native_execution': True,
        'bindings': {'device': {'state_directory': str(state), 'gateway_binding': gateway}},
        'emulator_bindings': {'peer': {'executable': {'runtime_tool': 'python'}}}}},
        'engine_secrets': {'hardware.session': {'emulator_bindings': {'peer': {
            'environment': {'PRIVATE_TEST_TOKEN': 'private-emulator-value'}}}}}}
    class Control(Transport):
        cancel_when_started = False
        @contextmanager
        def stream(self, method, path, **kwargs):
            yield SimpleNamespace(raise_for_status=lambda: None,
                iter_bytes=lambda: iter([blobs[path.rsplit('/', 1)[-1]]]))
        def post(self, path, **kwargs):
            if path.endswith('/heartbeat'):
                self.started = getattr(self, 'started', False) or any(
                    event.get('event') == 'hardware_session_supervisor' and event.get('status') == 'started'
                    for event in kwargs['json'].get('events', []))
                return httpx.Response(200, json={'cancel_requested': self.cancel_when_started and self.started,
                    'lease_seconds': 60},
                    request=httpx.Request('POST', 'http://127.0.0.1' + path))
            return super().post(path, **kwargs)
    root = tmp_path / 'attempt'
    root.mkdir()
    control = Control()
    control.blobs, control.profile = blobs, profile
    runner = runner_module.Runner('http://127.0.0.1', 'managed-target', 'private-target-token',
        client=control, profile_config=profile, heartbeat_interval=.02, process_grace=.3)
    return runner, control, attempt, root, port


def test_real_runner_starts_peer_before_supervisor_and_stops_it_after_gateway(tmp_path):
    runner, transport, attempt, root, port = fixture(tmp_path)
    runner.execute_attempt(attempt, root)
    assert transport.finish['state'] == 'succeeded', transport.finish
    result = transport.finish['result']
    assert result['records'] == 1
    assert result['parent_session']['cleanup_confirmed'] is True
    assert result['parent_session']['qualification']['physical_outcome'] == 'unverified'
    roles = {item['role'] for item in transport.upload_params}
    assert {'hardware_session_runtime', 'emulator_asset_custody', 'managed_emulator_parent'} <= roles
    workspace = EmulatorWorkspace(root / 'work', validate_selection(attempt['spec']['definition']).emulator)
    events = [json.loads(line) for line in (workspace.root / 'peer-events.jsonl').read_text().splitlines()]
    assert [row['hex'] for row in events if row['event'] == 'received'] == ['ff', '00']
    with pytest.raises(ProcessLookupError):
        os.kill(events[0]['pid'], 0)
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', port)) != 0
    context = (root / 'context.json').read_text()
    assert json.loads(context)['_runtime_secrets'] == {}
    assert all(secret not in context for secret in ('private-emulator-value', 'private-lab-token', 'private-target-token'))
    assert all(b'private-emulator-value' not in body for body in transport.uploads)


@pytest.mark.parametrize('reaction, expected', [('record', 'succeeded'), ('fail_session', 'failed')])
def test_early_exit_reaction_is_authored_and_cleanup_is_retained(tmp_path, reaction, expected):
    runner, transport, attempt, root, port = fixture(tmp_path, mode='exit', reaction=reaction, python_supervisor=True)
    runner.execute_attempt(attempt, root)
    assert transport.finish['state'] == expected, transport.finish
    parent = json.loads(HardwareSessionRuntimePaths(root / 'work').manifest.read_text())
    assert parent['cleanup_confirmed'] and parent['emulator']['observation']['exit_code'] == 0
    assert parent['qualification']['scientific_outcome'] == 'not_inferred'
    assert attempt['spec']['definition']['parameters']['emulator']['on_exit'] == reaction


def test_zero_readiness_budget_never_starts_supervisor_and_retains_parent_audit(tmp_path, monkeypatch):
    runner, transport, attempt, root, port = fixture(tmp_path, startup=0)
    attempt['spec']['definition']['parameters']['emulator']['readiness']['marker']['value'] = 'not-the-authored-ready-line'
    attempt['spec_digest'] = checksum(attempt['spec'])
    started = []
    original = runner_module.ProcessGroup
    def launch(command, **kwargs):
        if 'tinystudio.lab.execution' in command:
            started.append(command)
        return original(command, **kwargs)
    monkeypatch.setattr(runner_module, 'ProcessGroup', launch)
    runner.execute_attempt(attempt, root)
    assert not started and transport.finish['state'] == 'failed'
    assert transport.finish['result'] is None
    assert json.loads(HardwareSessionRuntimePaths(root / 'work').manifest.read_text())['cleanup_confirmed']


def test_server_cancellation_keeps_peer_alive_for_scoped_stop_and_audit(tmp_path):
    runner, transport, attempt, root, port = fixture(tmp_path, python_supervisor=True)
    runner.process_grace = .7
    transport.cancel_when_started = True
    runner.execute_attempt(attempt, root)
    assert transport.finish['state'] == 'cancelled', transport.finish
    assert transport.finish['result']['execution_outcome']['audit_only'] is True
    parent = json.loads(HardwareSessionRuntimePaths(root / 'work').manifest.read_text())
    assert parent['cleanup_confirmed']
    assert parent['bridge']['outcome']['termination']['trigger'] == 'cancel'
    workspace = EmulatorWorkspace(root / 'work', validate_selection(attempt['spec']['definition']).emulator)
    events = [json.loads(line) for line in (workspace.root / 'peer-events.jsonl').read_text().splitlines()]
    assert [row['hex'] for row in events if row['event'] == 'received'] == ['00']
    with pytest.raises(ProcessLookupError):
        os.kill(events[0]['pid'], 0)


def test_unconfirmed_parent_cleanup_never_publishes_a_successful_child(tmp_path, monkeypatch):
    from tinystudio.lab.hardware_session_runtime import HardwareSessionRuntime
    runner, transport, attempt, root, port = fixture(tmp_path)
    # Real resources still close; inject only a missing parent cleanup proof.
    monkeypatch.setattr(HardwareSessionRuntime, 'cleanup_confirmed', property(lambda self: False))
    runner.execute_attempt(attempt, root)
    assert transport.finish['state'] == 'failed' and transport.finish['result'] is None
    assert not transport.uploads
    assert 'cleanup is unconfirmed' in transport.finish['error']
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', port)) != 0


@pytest.mark.parametrize('scenario,expected,primary', [
    ('cancel', 'cancelled', 'AttemptAuthorityLost'),
    ('cleanup_unknown', 'failed', 'AttemptAuthorityLost'),
    ('genuine_error', 'failed', 'ValueError'),
    ('expired_before_cancel', 'failed', 'AttemptAuthorityLost'),
])
def test_startup_cancellation_preserves_primary_reason_and_cleanup_proof(tmp_path, monkeypatch, scenario, expected, primary):
    from tinystudio.lab.hardware_session_runtime import HardwareSessionRuntime
    from tinystudio.lab.managed_emulator import ManagedEmulatorProcess
    runner, transport, attempt, root, port = fixture(tmp_path, startup=None)
    attempt['spec']['definition']['parameters']['emulator']['readiness']['marker']['value'] = 'never-produced-ready-line'
    attempt['spec_digest'] = checksum(attempt['spec'])
    delivered = threading.Event()
    readiness_entered = threading.Event()
    first_reason = []
    original_post, original_ready = transport.post, ManagedEmulatorProcess.wait_ready
    def post(path, **kwargs):
        if path.endswith('/heartbeat') and readiness_entered.is_set():
            ledger = HardwareSessionRuntimePaths(root / 'work').emulator.lifecycle
            rows = [json.loads(line) for line in ledger.read_bytes().split(b'\n')[:-1] if line] if ledger.exists() else []
            if any(row.get('event') == 'launched' for row in rows):
                delivered.set()
                return httpx.Response(200, json={'cancel_requested': True, 'lease_seconds': 60},
                    request=httpx.Request('POST', 'http://127.0.0.1' + path))
        return original_post(path, **kwargs)
    def readiness(process):
        if scenario == 'expired_before_cancel':
            process.control.revoke('lease_expired', termination='expiry')
        readiness_entered.set()
        assert delivered.wait(3), 'owned cancellation was not delivered after launch'
        # The response is observed by the heartbeat after the transport returns.
        assert process.control.snapshot()['revoked'] or _wait_for_cancel(process.control)
        first_reason.append(process.control.snapshot()['revocation_reason'])
        if scenario == 'genuine_error':
            raise ValueError('independent selected-readiness failure')
        return original_ready(process)
    transport.post = post
    monkeypatch.setattr(ManagedEmulatorProcess, 'wait_ready', readiness)
    if scenario == 'cleanup_unknown':
        # Owned resources really close; only the parent's cleanup proof is lost.
        monkeypatch.setattr(HardwareSessionRuntime, 'cleanup_confirmed', property(lambda self: False))
    runner.execute_attempt(attempt, root)
    assert first_reason == ['lease_expired' if scenario == 'expired_before_cancel' else 'server_requested_cancellation']
    assert delivered.is_set() and transport.finish['state'] == expected, transport.finish
    assert transport.finish['result'] is None and not transport.uploads
    if expected == 'cancelled':
        assert transport.finish['error'] is None
    else:
        assert transport.finish['error'] is not None
    manifest = json.loads(HardwareSessionRuntimePaths(root / 'work').manifest.read_text())
    assert manifest['primary_error']['type'] == primary
    assert manifest['cleanup_confirmed'] and manifest['bridge']['status'] == 'not_constructed'
    assert not (root / 'context.json').exists()
    with pytest.raises(ProcessLookupError):
        os.kill(manifest['emulator']['observation']['pid'], 0)
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', port)) != 0


def _wait_for_cancel(control):
    import time
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if control.snapshot()['revoked']:
            return True
        time.sleep(.001)
    return False


def test_registered_api_to_native_supervisor_and_managed_peer(integrated, tmp_path):
    client, base, other, app, connection, root, factory = integrated
    selected = tmp_path / 'selected-config'
    selected.mkdir()
    _, authored, attempt, _, port = fixture(selected)
    target = client.post(base + '/targets', json={'name': 'owned-managed-peer'}).json()
    definition = attempt['spec']['definition']
    retained = {}
    for name, data in authored.blobs.items():
        response = client.post(base + '/inputs', files={'file': (name + '.txt', data)})
        assert response.status_code == 201, response.text
        retained[name] = response.json()
    definition['inputs'] = [{'artifact_id': row['id'], 'sha256': row['sha256']} for row in retained.values()]
    definition['parameters']['supervisor']['artifact_id'] = retained['supervisor']['id']
    definition['parameters']['emulator']['assets'][0]['artifact_id'] = retained['peer']['id']
    with TestClient(app, headers={'Authorization': 'Bearer ' + target['token']}) as machine:
        runner = runner_module.Runner('http://127.0.0.1', target['id'], target['token'], client=machine,
            profile_config=authored.profile, work_root=selected, heartbeat_interval=.03, process_grace=.3)
        runner.advertise()
        checked = client.post(base + '/preflight', json={'definition': definition, 'target_id': target['id']})
        assert checked.status_code == 200 and checked.json()['compatible'], checked.text
        saved = client.post(base + '/definitions', json={'definition': definition}).json()
        response = client.post(base + '/runs', json={'definition_id': saved['id'], 'revision': saved['revision'],
            'target_id': target['id'], 'idempotency_key': 'managed-peer-' + uuid.uuid4().hex})
        assert response.status_code == 202, response.text
        run_id = response.json()['id']
        assert runner.run_once(run_id=run_id)
    inspected = client.get(base + '/runs/' + run_id).json()
    assert inspected['state'] == 'succeeded', inspected
    result = inspected['result']
    assert result['records'] == 1 and result['parent_session']['cleanup_confirmed'] is True
    assert {'hardware_session_runtime', 'managed_emulator_parent', 'emulator_asset_custody'} <= {
        row['role'] for row in result['artifacts']}
    for artifact in result['artifacts']:
        download = client.get(base + '/artifacts/' + artifact['id'])
        assert download.status_code == 200
        assert hashlib.sha256(download.content).hexdigest() == artifact['sha256']
        assert b'private-emulator-value' not in download.content
    assert result['parent_session']['qualification']['physical_outcome'] == 'unverified'
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', port)) != 0
