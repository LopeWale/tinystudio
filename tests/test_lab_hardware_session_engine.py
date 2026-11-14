"""Owned supervisor fixtures and a stub IPC client; no hardware authority."""
from dataclasses import FrozenInstanceError
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from tinystudio.lab.engine import ExecutionCancelled
from tinystudio.lab.execution_outcome import RetainedExecutionError


def module():
    assert importlib.util.find_spec('tinystudio.lab.hardware_session_engine') is not None
    from tinystudio.lab import hardware_session_engine
    return hardware_session_engine


def selected(tmp_path, source, *, kind='recorded', **parameters):
    path = tmp_path / 'downloaded-artifact'
    path.write_bytes(source.encode())
    ref = {'artifact_id': 'owned-source', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'role': 'supervisor'}
    supervisor = {'kind': kind, 'artifact_id': ref['artifact_id']}
    if kind == 'python':
        supervisor['callable'] = 'run'
    return {'definition': {'engine': 'hardware.session', 'operating_mode': 'simulation', 'seed': 9007199254740993,
        'execution': {'backend': 'native', 'profile': 'trusted_native'}, 'inputs': [ref],
        'parameters': {'binding_id': 'owned-binding', 'supervisor': supervisor, **parameters}},
        'input_paths': {ref['artifact_id']: str(path)},
        '_hardware_session': {'format': 'tinystudio.hardware-session-ipc.v1', 'path': '/owned/unused.sock',
            'capability': 'private-capability-do-not-retain', 'session_id': 'owned-session',
            'profile': {'profile_digest': 'a'*64, 'physical_outcome': 'unverified'}},
        '_runtime_grants': {'private-grant': 'not-public'}, '_runtime_secrets': {'password': 'private-password'}}


class Client:
    def __init__(self, descriptor, *, failure=None, on_submit=None):
        self.profile = descriptor['profile']
        self.calls = []
        self.closed = False
        self.failure, self.on_submit = failure, on_submit

    def submit(self, decision_id, payload, *, valid_for_seconds):
        self.calls.append((decision_id, payload, valid_for_seconds))
        if self.on_submit:
            self.on_submit()
        if self.failure:
            raise self.failure
        return {'command_id': decision_id, 'status': 'delivered_unverified', 'physical_outcome': 'unverified', 'payload': payload}

    def close(self):
        self.closed = True


def install_client(monkeypatch, spec, **kwargs):
    client = Client(spec['_hardware_session'], **kwargs)
    monkeypatch.setattr(module(), '_session_client', lambda descriptor, timeout: client)
    return client


def manifest(result):
    item = next(row for row in result['artifacts'] if row['role'] == 'hardware_session_manifest')
    assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() == item['sha256']
    return json.loads(Path(item['path']).read_text())


@pytest.mark.parametrize('mode', ['physical_control', 'shadow', 'acquisition', 'simulation'])
def test_selection_is_frozen_and_has_no_controller_or_row_count_assumptions(tmp_path, mode):
    spec = selected(tmp_path, '', supervisor_parameters={'arbitrary': [None, 9007199254740993]})
    spec['definition']['operating_mode'] = mode
    value = module().validate_selection(spec['definition'])
    assert value.binding_id == 'owned-binding'
    assert value.to_dict()['supervisor_parameters'] == {'arbitrary': [None, 9007199254740993]}
    with pytest.raises(FrozenInstanceError):
        value.binding_id = 'changed'
    with pytest.raises(TypeError):
        value.supervisor['kind'] = 'changed'


def test_recorded_decisions_keep_binary_data_exact_and_publish_hash_bound_source(tmp_path, monkeypatch):
    decision = {'decision_id': 'binary-1', 'payload': {'operation': 'mqtt.publish',
        'data': {'encoding': 'base64', 'value': 'AAH/DQo='}, 'qos': 1, 'retain': False}, 'valid_for_seconds': .0000000000005}
    source = json.dumps(decision) + '\n'
    spec = selected(tmp_path, source, evidence={'flush_records': None, 'fsync': False})
    client = install_client(monkeypatch, spec)
    result = module().execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    assert client.calls == [(decision['decision_id'], decision['payload'], decision['valid_for_seconds'])]
    assert client.closed and result['qualification']['physical_outcome'] == 'unverified'
    report = manifest(result)
    assert report['status'] == 'completed' and report['seed'] == 9007199254740993
    assert report['evidence_writes']['submissions']['written_records'] == 1
    assert report['evidence_writes']['receipts']['written_records'] == 1
    source_artifact = next(row for row in result['artifacts'] if row['role'] == 'hardware_session_supervisor')
    assert Path(source_artifact['path']).read_bytes() == source.encode()
    assert source_artifact['sha256'] == spec['definition']['inputs'][0]['sha256']
    assert all(item['visibility'] == 'evaluation' for item in result['artifacts'])
    assert 'private-capability-do-not-retain' not in json.dumps(report)


def test_python_download_without_extension_uses_public_context_and_preserves_raw_report(tmp_path, monkeypatch):
    source = '''def run(session, context):
    assert set(context) == {'definition', 'seed', 'supervisor_parameters', 'inputs', 'input_paths', 'output_dir', 'profile'}
    assert context['supervisor_parameters'] == {'rule': 'arbitrary'}
    profile = session.profile
    profile['changed'] = True
    assert 'changed' not in session.profile
    receipt = session.submit('query', {'operation': 'tcp.query', 'data': {'encoding': 'utf8', 'value': 'READ?\\r\\n'}}, valid_for_seconds=1)
    return {'records': [{'receipt': receipt}], 'metrics': {'custom_value': 9007199254740993}, 'seed': context['seed']}
'''
    spec = selected(tmp_path, source, kind='python', supervisor_parameters={'rule': 'arbitrary'})
    client = install_client(monkeypatch, spec)
    result = module().execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    assert client.calls[0][1]['data']['value'] == 'READ?\r\n'
    assert result['metrics'] == {'custom_value': 9007199254740993}
    assert result['records'][0]['receipt']['command_id'] == 'query'
    output = '\n'.join(Path(item['path']).read_text() for item in result['artifacts'])
    assert 'private-capability-do-not-retain' not in output and 'private-password' not in output
    assert 'private-grant' not in output


@pytest.mark.parametrize('tail', ['{broken}\n', '{"decision_id":"bad","payload":{},"valid_for_seconds":1,"extra":true}\n'])
def test_malformed_recorded_row_retains_completed_receipt_and_failure_audit(tmp_path, monkeypatch, tail):
    row = {'decision_id': 'first', 'payload': {'arbitrary': 'unchanged'}, 'valid_for_seconds': 1}
    spec = selected(tmp_path, json.dumps(row)+'\n'+tail)
    client = install_client(monkeypatch, spec)
    with pytest.raises(RetainedExecutionError) as caught:
        module().execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    result = caught.value.evidence_result
    assert manifest(result)['status'] == 'failed'
    assert len(client.calls) == 1 and client.closed
    assert result['qualification']['complete'] is False
    receipt = next(row for row in result['artifacts'] if row['role'] == 'hardware_session_receipts')
    assert len(Path(receipt['path']).read_text().splitlines()) == 1


def test_cancelled_supervisor_keeps_partial_local_evidence(tmp_path, monkeypatch):
    row = {'decision_id': 'first', 'payload': {}, 'valid_for_seconds': 1}
    spec = selected(tmp_path, json.dumps(row)+'\n'+json.dumps({**row, 'decision_id': 'second'})+'\n')
    state = {'cancelled': False}
    client = install_client(monkeypatch, spec, on_submit=lambda: state.update(cancelled=True))
    with pytest.raises(ExecutionCancelled) as caught:
        module().execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: state['cancelled'])
    assert manifest(caught.value.evidence_result)['status'] == 'cancelled'
    assert len(client.calls) == 1 and client.closed


def test_source_hash_mismatch_never_constructs_a_client(tmp_path, monkeypatch):
    spec = selected(tmp_path, 'def run(session, context): return None', kind='python')
    Path(spec['input_paths']['owned-source']).write_text('raise RuntimeError("changed")')
    monkeypatch.setattr(module(), '_session_client', lambda *args: pytest.fail('source mismatch must precede IPC'))
    with pytest.raises(RetainedExecutionError) as caught:
        module().execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    assert manifest(caught.value.evidence_result)['status'] == 'failed'


def test_descriptor_is_required_before_output_io(tmp_path):
    spec = selected(tmp_path, '')
    del spec['_hardware_session']
    with pytest.raises(ValueError, match='parent'):
        module().execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    assert not (tmp_path/'output').exists()


@pytest.mark.parametrize('visibility', ['model', 'evaluation', 'truth'])
def test_success_visibility_is_authored_but_failure_stays_audit_only(tmp_path, monkeypatch, visibility):
    spec = selected(tmp_path, '', artifact_visibility=visibility)
    install_client(monkeypatch, spec)
    assert module().validate_selection(spec['definition']).to_dict()['artifact_visibility'] == visibility
    result = module().execute_hardware_session(spec, tmp_path/'success', lambda row: None, lambda: False)
    assert {row['visibility'] for row in result['artifacts']} == {visibility}
    Path(spec['input_paths']['owned-source']).write_text('{broken}\n')
    spec['definition']['inputs'][0]['sha256'] = hashlib.sha256(b'{broken}\n').hexdigest()
    with pytest.raises(RetainedExecutionError) as caught:
        module().execute_hardware_session(spec, tmp_path/'failure', lambda row: None, lambda: False)
    assert {row['visibility'] for row in caught.value.evidence_result['artifacts']} == {'evaluation'}


def test_python_context_mutation_cannot_rewrite_frozen_provenance(tmp_path, monkeypatch):
    spec = selected(tmp_path, '''def run(session, context):
    context['definition']['seed'] = 'changed'
    context['definition']['inputs'][0]['sha256'] = '0' * 64
    context['profile']['profile_digest'] = 'changed'
    return None
''', kind='python')
    install_client(monkeypatch, spec)
    result = module().execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    retained = manifest(result)
    assert retained['seed'] == spec['definition']['seed']
    assert retained['supervisor_source']['sha256'] == spec['definition']['inputs'][0]['sha256']
    assert retained['profile'] == spec['_hardware_session']['profile']


def test_remote_failure_keeps_exact_error_receipts_and_primary_failure(tmp_path, monkeypatch):
    row = {'decision_id': 'uncertain', 'payload': {'fault': 'literal'}, 'valid_for_seconds': 2}
    spec = selected(tmp_path, json.dumps(row)+'\n')
    error = RuntimeError('private-capability-do-not-retain failed delivery')
    error.delivery_receipt = {'status': 'unknown', 'physical_outcome': 'unverified', 'bytes': 'AAE='}
    error.lifecycle_receipt = {'state': 'recovery_required', 'physical_outcome': 'unverified'}
    client = install_client(monkeypatch, spec, failure=error)
    with pytest.raises(RetainedExecutionError) as caught:
        module().execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    assert caught.value.__cause__ is error and client.closed
    artifact = next(row for row in caught.value.evidence_result['artifacts'] if row['role'] == 'hardware_session_receipts')
    retained = json.loads(Path(artifact['path']).read_text())
    assert retained['delivery_receipt'] == error.delivery_receipt
    assert retained['lifecycle_receipt'] == error.lifecycle_receipt
    assert 'private-capability-do-not-retain' not in Path(artifact['path']).read_text()


@pytest.mark.parametrize('patch', [
    {'artifact_visibility': 'guessed'}, {'ipc_timeout_seconds': 0}, {'ipc_timeout_seconds': float('inf')},
    {'supervisor_parameters': {'bad': float('nan')}}, {'evidence': {'unknown': 1}}, {'max_rows': 5},
])
def test_invalid_choices_fail_pure_validation(tmp_path, patch):
    spec = selected(tmp_path, '', **patch)
    with pytest.raises(ValueError):
        module().validate_selection(spec['definition'])


def test_actual_client_delivers_exact_recorded_decision_to_owned_unix_server(tmp_path):
    from tests.test_lab_hardware_session_client import server
    from tinystudio.lab.session_protocol import encode_frame, read_frame
    row = {'decision_id': 'actual-ipc', 'payload': {'operation': 'serial.write',
        'data': {'encoding': 'utf8', 'value': 'SET 9007199254740993\r\n'}}, 'valid_for_seconds': .125}
    spec = selected(tmp_path, json.dumps(row)+'\n', ipc_timeout_seconds=1)
    received = []

    def handle(connection):
        request = read_frame(connection)
        assert request == {'capability': spec['_hardware_session']['capability'], **row}
        received.append(request)
        connection.sendall(encode_frame({'ok': True, 'receipt': {'decision_id': row['decision_id'],
            'status': 'delivered_unverified', 'physical_outcome': 'unverified'}}))

    with server(handle) as path:
        spec['_hardware_session']['path'] = str(path)
        result = module().execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    assert len(received) == 1 and manifest(result)['status'] == 'completed'
    assert spec['_hardware_session']['capability'] not in json.dumps(result)


def test_python_loader_does_not_reuse_same_filename_timestamp_or_module_cache(tmp_path):
    path = tmp_path/'downloaded'
    path.write_text('def run(session, context): return {"version": 1}\n')
    before = path.stat()
    first = module()._python_supervisor(path, hashlib.sha256(path.read_bytes()).hexdigest(), 'run', None, {})
    path.write_text('def run(session, context): return {"version": 2}\n')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    second = module()._python_supervisor(path, hashlib.sha256(path.read_bytes()).hexdigest(), 'run', None, {})
    assert first == {'version': 1} and second == {'version': 2}


def test_selected_sync_failure_precedes_ipc_and_retains_actual_written_count(tmp_path, monkeypatch):
    from tinystudio.lab import evidence_io
    row = {'decision_id': 'not-admitted', 'payload': {}, 'valid_for_seconds': 1}
    spec = selected(tmp_path, json.dumps(row)+'\n')
    client = install_client(monkeypatch, spec)

    def broken_sync(descriptor):
        raise OSError('controlled sync failure')

    monkeypatch.setattr(evidence_io.os, 'fsync', broken_sync)
    with pytest.raises(RetainedExecutionError) as caught:
        module().execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    assert client.calls == [] and client.closed
    receipt = manifest(caught.value.evidence_result)['evidence_writes']['submissions']
    assert receipt['written_records'] == receipt['flushed_records'] == 1
    assert receipt['fsynced_records'] == 0 and receipt['errors']


def test_completion_emit_failure_still_retains_returned_report(tmp_path, monkeypatch):
    spec = selected(tmp_path, 'def run(session, context): return {"metrics": {"kept": 7}}\n', kind='python')
    install_client(monkeypatch, spec)

    def emit(event):
        if event['status'] == 'completed':
            raise OSError('controlled completion event failure')

    with pytest.raises(RetainedExecutionError) as caught:
        module().execute_hardware_session(spec, tmp_path/'output', emit, lambda: False)
    assert caught.value.evidence_result['metrics'] == {'kept': 7}
    assert manifest(caught.value.evidence_result)['status'] == 'failed'


def test_python_system_exit_retains_receipts_and_cleanup_does_not_replace_it(tmp_path, monkeypatch):
    source = '''def run(session, context):
    session.submit('before-exit', {}, valid_for_seconds=1)
    raise SystemExit('controlled supervisor exit')
'''
    spec = selected(tmp_path, source, kind='python')
    client = install_client(monkeypatch, spec)

    def failed_close():
        raise OSError('controlled client close failure')

    client.close = failed_close
    with pytest.raises(RetainedExecutionError) as caught:
        module().execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    assert isinstance(caught.value.__cause__, SystemExit)
    retained = manifest(caught.value.evidence_result)
    assert retained['error']['type'] == 'SystemExit'
    assert retained['qualification']['cleanup_errors'][0]['type'] == 'OSError'
    assert retained['evidence_writes']['receipts']['written_records'] == 1
