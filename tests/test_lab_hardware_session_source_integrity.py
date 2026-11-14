"""Executed private supervisor files and a stub IPC client; no hardware access."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from tinystudio.lab import hardware_session_engine as engine
from tinystudio.lab.engine import ExecutionCancelled
from tinystudio.lab.execution_outcome import RetainedExecutionError


class Client:
    def __init__(self):
        self.calls = []
        self.closed = False

    def submit(self, decision_id, payload, *, valid_for_seconds):
        self.calls.append((decision_id, payload, valid_for_seconds))
        return {'command_id': decision_id, 'status': 'delivered_unverified', 'physical_outcome': 'unverified'}

    def close(self):
        self.closed = True


def setup(tmp_path, monkeypatch, source):
    downloaded = tmp_path/'downloaded.py'
    downloaded.write_text(source)
    frozen_sha = hashlib.sha256(downloaded.read_bytes()).hexdigest()
    spec = {'definition': {'engine': 'hardware.session', 'operating_mode': 'simulation',
        'execution': {'backend': 'native', 'profile': 'trusted_native'},
        'inputs': [{'artifact_id': 'source', 'sha256': frozen_sha}],
        'parameters': {'binding_id': 'owned', 'supervisor': {'kind': 'python', 'artifact_id': 'source', 'callable': 'run'},
                       'artifact_visibility': 'model'}},
        'input_paths': {'source': str(downloaded)},
        '_hardware_session': {'format': 'tinystudio.hardware-session-ipc.v1', 'path': '/owned/unused.sock',
            'capability': 'private-test-capability', 'session_id': 'owned-session', 'profile': {}}}
    client = Client()
    monkeypatch.setattr(engine, '_session_client', lambda *args: client)
    return spec, client, frozen_sha


def retained(result):
    artifact = next(row for row in result['artifacts'] if row['role'] == 'hardware_session_manifest')
    return json.loads(Path(artifact['path']).read_text())


def assert_audit(result, client, frozen_sha, status):
    manifest = retained(result)
    assert manifest['status'] == 'failed'
    assert manifest['supervisor_source']['sha256'] == frozen_sha
    assert manifest['supervisor_source']['verified'] is False
    assert manifest['supervisor_source']['integrity']['status'] == status
    assert result['qualification']['complete'] is False
    assert result['qualification']['physical_outcome'] == 'unverified'
    assert {row['visibility'] for row in result['artifacts']} == {'evaluation'}
    assert len(client.calls) == 1 and client.closed
    roles = {row['role']: row for row in result['artifacts']}
    assert {'hardware_session_manifest', 'hardware_session_report',
            'hardware_session_submissions', 'hardware_session_receipts'} <= roles.keys()
    assert json.loads(Path(roles['hardware_session_report']['path']).read_text()) == {'metrics': {'retained': 7}}
    assert len(Path(roles['hardware_session_receipts']['path']).read_text().splitlines()) == 1
    return manifest, roles


@pytest.mark.parametrize('location', ["context['input_paths']['source']", '__file__'])
def test_supervisor_overwrite_is_failed_audit_with_actual_source_hash(tmp_path, monkeypatch, location):
    source = f'''from pathlib import Path
def run(session, context):
    session.submit('before-mutation', {{'authored': 'unchanged'}}, valid_for_seconds=1)
    Path({location}).write_text('modified retained source\\n')
    return {{'metrics': {{'retained': 7}}}}
'''
    spec, client, frozen_sha = setup(tmp_path, monkeypatch, source)
    with pytest.raises(RetainedExecutionError, match='retained supervisor source') as caught:
        engine.execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    manifest, roles = assert_audit(caught.value.evidence_result, client, frozen_sha, 'sha256_mismatch')
    changed = b'modified retained source\n'
    actual = hashlib.sha256(changed).hexdigest()
    assert manifest['supervisor_source']['integrity']['observed_sha256'] == actual
    artifact = roles['hardware_session_supervisor']
    assert artifact['sha256'] == actual != frozen_sha
    assert Path(artifact['path']).read_bytes() == changed


@pytest.mark.parametrize('mutation,status', [
    ('source.unlink()', 'missing'),
    ("source.unlink(); source.symlink_to(Path(context['output_dir']).parent/'outside.py')", 'symlink'),
    ('source.unlink(); source.mkdir()', 'not_regular'),
    ('source.unlink(); os.mkfifo(source)', 'not_regular'),
])
def test_missing_link_or_nonregular_source_does_not_drop_report_or_audit(tmp_path, monkeypatch, mutation, status):
    (tmp_path/'outside.py').write_text('external target must not become retained source\n')
    source = f'''from pathlib import Path
import os
def run(session, context):
    session.submit('before-mutation', {{}}, valid_for_seconds=1)
    source = Path(__file__)
    {mutation}
    return {{'metrics': {{'retained': 7}}}}
'''
    spec, client, frozen_sha = setup(tmp_path, monkeypatch, source)
    with pytest.raises(RetainedExecutionError, match='retained supervisor source') as caught:
        engine.execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    manifest, roles = assert_audit(caught.value.evidence_result, client, frozen_sha, status)
    assert 'hardware_session_supervisor' not in roles
    assert manifest['supervisor_source']['integrity']['observed_sha256'] is None
    assert all('external target must not' not in Path(row['path']).read_text() for row in roles.values())


@pytest.mark.parametrize('failure', ['RuntimeError', 'ExecutionCancelled'])
def test_mutation_keeps_the_original_supervisor_failure_primary(tmp_path, monkeypatch, failure):
    source = f'''from pathlib import Path
from tinystudio.lab.engine import ExecutionCancelled
def run(session, context):
    session.submit('before-mutation', {{}}, valid_for_seconds=1)
    Path(__file__).write_text('modified source\\n')
    raise {failure}('original supervisor failure')
'''
    spec, client, frozen_sha = setup(tmp_path, monkeypatch, source)
    expected = ExecutionCancelled if failure == 'ExecutionCancelled' else RetainedExecutionError
    with pytest.raises(expected, match='original supervisor failure') as caught:
        engine.execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    result = caught.value.evidence_result
    manifest = retained(result)
    assert manifest['error']['type'] == failure
    assert manifest['supervisor_source']['verified'] is False
    assert manifest['supervisor_source']['integrity']['status'] == 'sha256_mismatch'
    assert manifest['status'] == ('cancelled' if failure == 'ExecutionCancelled' else 'failed')
    assert next(row for row in result['artifacts'] if row['role'] == 'hardware_session_supervisor')['sha256'] != frozen_sha
    assert client.closed and len(client.calls) == 1


def test_manifest_write_boundary_rechecks_source_before_artifact_collection(tmp_path, monkeypatch):
    spec, client, frozen_sha = setup(tmp_path, monkeypatch,
        'def run(session, context):\n    return {"metrics": {"retained": 7}}\n')
    write_text = Path.write_text
    changed = []

    def write_manifest(path, data, *args, **kwargs):
        value = write_text(path, data, *args, **kwargs)
        if path.name == 'hardware-session.json' and not changed:
            changed.append(True)
            (path.parent/'supervisor-source').write_bytes(b'changed at manifest boundary\n')
        return value

    monkeypatch.setattr(Path, 'write_text', write_manifest)
    with pytest.raises(RetainedExecutionError, match='retained supervisor source') as caught:
        engine.execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    manifest = retained(caught.value.evidence_result)
    assert manifest['status'] == 'failed' and manifest['supervisor_source']['verified'] is False
    assert manifest['supervisor_source']['integrity']['observed_sha256'] != frozen_sha
    assert client.closed


def test_unchanged_source_retains_verified_frozen_identity(tmp_path, monkeypatch):
    spec, client, frozen_sha = setup(tmp_path, monkeypatch, 'def run(session, context): return None\n')
    result = engine.execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    manifest = retained(result)
    assert manifest['status'] == 'completed'
    assert manifest['supervisor_source']['verified'] is True
    assert manifest['supervisor_source']['integrity'] == {'status': 'matched', 'observed_sha256': frozen_sha}
    assert next(row for row in result['artifacts'] if row['role'] == 'hardware_session_supervisor')['sha256'] == frozen_sha
    assert client.closed


@pytest.mark.parametrize('replacement,entry_type', [
    ("source.symlink_to(outside)", 'symlink'),
    ('os.mkfifo(source)', 'fifo'),
    ("source.mkdir(); (source/'link').symlink_to(outside)", 'directory'),
    ("source.mkdir(); os.mkfifo(source/'pipe')", 'directory'),
])
def test_unpublishable_source_replacement_keeps_audit_exportable(tmp_path, monkeypatch, replacement, entry_type):
    from tinystudio.lab.contracts import checksum
    from tinystudio.lab.evidence_spool import EvidenceSpool, SpoolPolicy
    from tinystudio.lab.runner import Runner

    (tmp_path/'outside.py').write_text('external source must not enter export\n')
    source = f'''from pathlib import Path
import os
def run(session, context):
    session.submit('before-mutation', {{}}, valid_for_seconds=1)
    source = Path(__file__)
    outside = Path(context['supervisor_parameters']['outside'])
    source.unlink()
    {replacement}
    return {{'metrics': {{'retained': 7}}}}
'''
    spec, client, frozen_sha = setup(tmp_path, monkeypatch, source)
    spec['definition']['parameters']['supervisor_parameters'] = {'outside': str(tmp_path/'outside.py')}
    spec.update(implementation={'version': 1, 'files': {}}, runtime={})
    attempt = {'run_id': 'owned-run', 'attempt_id': 'owned-attempt', 'spec': spec, 'spec_digest': checksum(spec)}
    spool = EvidenceSpool(SpoolPolicy(root=tmp_path/'spool', enabled=True)).allocate(
        attempt, server='http://127.0.0.1', target_id='owned-target')
    output = spool.execution_root/'work/evidence'
    with pytest.raises(RetainedExecutionError) as caught:
        engine.execute_hardware_session(spec, output, lambda row: None, lambda: False)
    manifest, roles = assert_audit(caught.value.evidence_result, client, frozen_sha,
        'symlink' if entry_type == 'symlink' else 'not_regular')
    assert manifest['supervisor_source']['integrity']['cleanup'] == {'status': 'removed', 'entry_type': entry_type}
    assert not (output/'supervisor-source').exists() and not (output/'supervisor-source').is_symlink()
    assert Runner._output_size(spool.execution_root) > 0
    spool.mark_cleanup(True)
    snapshot = spool.snapshot()
    archive = tmp_path/'audit.zip'
    spool.export(archive, expected_manifest_digest=snapshot['manifest_sha256'])
    with ZipFile(archive) as retained_zip:
        assert 'evidence/supervisor-report.json' in retained_zip.namelist()
        assert 'evidence/session-receipts.jsonl' in retained_zip.namelist()
        assert 'evidence/supervisor-source' not in retained_zip.namelist()
        assert b'"verified": false' in retained_zip.read('evidence/hardware-session.json')
        assert all(b'external source must not enter export' not in retained_zip.read(name)
                   for name in retained_zip.namelist())


def test_source_cleanup_failure_is_explicit_without_replacing_primary_failure(tmp_path, monkeypatch):
    source = '''from pathlib import Path
def run(session, context):
    source = Path(__file__)
    source.unlink()
    source.mkdir()
    raise RuntimeError('original supervisor error')
'''
    spec, client, _ = setup(tmp_path, monkeypatch, source)

    def failed_cleanup(path):
        raise PermissionError('owned source cleanup failure')

    monkeypatch.setattr(engine.shutil, 'rmtree', failed_cleanup)
    with pytest.raises(RetainedExecutionError, match='original supervisor error') as caught:
        engine.execute_hardware_session(spec, tmp_path/'output', lambda row: None, lambda: False)
    manifest = retained(caught.value.evidence_result)
    assert manifest['supervisor_source']['verified'] is False
    assert manifest['supervisor_source']['integrity']['cleanup'] == {
        'status': 'failed', 'entry_type': 'directory', 'error_type': 'PermissionError'}
    assert manifest['error']['type'] == 'RuntimeError'
    assert any('cleanup' in note for note in caught.value.__cause__.__notes__)
    assert client.closed
