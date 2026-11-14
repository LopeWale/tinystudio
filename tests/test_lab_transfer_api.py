"""Authenticated transfer controls commit identities before acknowledging them."""
import hashlib
from datetime import timedelta
import json
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from tinystudio.lab import operations, service as svc, transfer_api, transfer_service
from tinystudio.lab.models import LabAttempt, LabTransfer
from tests.test_lab_foundation import database, setup, definition
from tests.test_lab_transfer_service import Store


@pytest.fixture
def fixture(setup, monkeypatch):
    factory, project, other, client, base, app = setup
    store = Store()
    store.storage = SimpleNamespace(remote=True)
    store.sign_download = lambda key: {'url': 'https://objects.fixture.invalid/download?signature=private',
                                       'headers': {}, 'expires_in': 900}
    monkeypatch.setattr(transfer_api, '_store', lambda: store)
    monkeypatch.setattr(transfer_service, '_make_store', lambda policy: store)
    return factory, project, other, client, base, app, store


def request(body=b'\x00\xff\n', **changes):
    return {'name': 'arbitrary-source.bin', 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
            'idempotency_key': 'request-' + uuid.uuid4().hex, **changes}


def test_project_transfer_committed_resume_scope_seal_and_async_publish(fixture):
    factory, project, other, client, base, app, store = fixture
    body = b'\x00\xff\n'
    intent = request(body, role='chosen-source', visibility='evaluation')
    response = client.post(base + '/transfers', json=intent)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row['state'] == 'uploading' and row['artifact'] is None
    with factory() as db:
        durable = transfer_service.get_transfer(db, project, row['id'])
        assert durable.state == 'uploading' and durable.upload_id in store.uploads
        upload = durable.upload_id
        storage_key = durable.storage_key
    assert client.post(base + '/transfers', json=intent).json()['id'] == row['id']
    assert client.post(base + '/transfers', json={**intent, 'name': 'different'}).status_code == 409
    path = base + '/transfers/' + row['id']
    assert client.get(path).status_code == 200
    assert client.get(path + '/parts').json()['parts'] == []
    signed = client.post(path + '/parts/1').json()
    assert (signed['part_number'], signed['offset'], signed['size']) == (1, 0, len(body))
    assert 'url' not in client.get(path).json()
    store.uploads[upload]['parts'][1] = body
    response = client.post(path + '/complete')
    assert response.status_code == 202, response.text
    sealed = response.json()
    assert sealed['state'] == 'verifying' and sealed['operation_id'] and sealed['artifact'] is None
    assert client.post(path + '/parts/1').status_code == 409
    assert operations.run_operation(sealed['operation_id'], factory)['state'] == 'succeeded'
    verified = client.get(path).json()
    assert verified['state'] == 'verified' and verified['operation_state'] == 'succeeded'
    artifact = verified['artifact']
    assert (artifact['size'], artifact['sha256'], artifact['role'], artifact['visibility']) == (
        len(body), hashlib.sha256(body).hexdigest(), 'chosen-source', 'evaluation')
    with factory() as db:
        assert svc.get_artifact(db, project, artifact['id']).storage_key == storage_key
    direct = client.post(base + '/artifacts/' + artifact['id'] + '/download')
    assert direct.status_code == 200 and direct.json()['mode'] == 'direct'
    assert direct.json()['artifact'] == artifact
    with TestClient(app) as anonymous:
        assert anonymous.get(path).status_code == 401
        assert anonymous.post(path + '/parts/1').status_code == 401


@pytest.mark.parametrize('changes', [{'size': True}, {'size': -1}, {'sha256': 'not-a-hash'},
    {'visibility': 'inferred'}, {'extra': 'unrecognized'}])
def test_invalid_transfer_metadata_never_initializes_provider(fixture, changes):
    factory, project, other, client, base, app, store = fixture
    response = client.post(base + '/transfers', json=request(**changes))
    assert response.status_code == 422
    assert store.calls == []
    with factory() as db:
        assert not list(db.scalars(select(LabTransfer).where(LabTransfer.project_id == project)))


def test_output_scope_requires_exact_attempt_and_input_manifest(fixture):
    factory, project, other, client, base, app, store = fixture
    enrollment = client.post(base + '/targets', json={'name': 'owned-transfer-target'})
    assert enrollment.status_code == 201, enrollment.text
    target = enrollment.json()
    from tinystudio.lab.runner import capabilities
    with TestClient(app, headers={'Authorization': 'Bearer ' + target['token']}) as machine:
        runner = '/api/lab/runner/' + target['id']
        assert machine.post(runner + '/advertise', json={'capabilities': capabilities()}).status_code == 200
        saved = client.post(base + '/definitions', json={'definition': definition()}).json()
        submitted = client.post(base + '/runs', json={'definition_id': saved['id'], 'revision': 1,
            'target_id': target['id'], 'idempotency_key': uuid.uuid4().hex})
        assert submitted.status_code == 202, submitted.text
        claimed = machine.post(runner + '/claim', json={'capabilities': capabilities()}).json()['attempt']
        headers = {'X-Lab-Lease': claimed['lease_token']}
        scope = runner + '/attempts/' + claimed['attempt_id']
        created = machine.post(scope + '/transfers', json=request(), headers=headers)
        assert created.status_code == 201, created.text
        identifier = created.json()['id']
        assert machine.get(scope + '/transfers/' + identifier).status_code == 403
        assert client.post(base + '/transfers/' + identifier + '/parts/1').status_code == 403
        assert machine.post(scope + '/inputs/missing/download', headers=headers).status_code == 403
        assert machine.get(scope + '/transfers/' + identifier, headers=headers).status_code == 200
        with factory.begin() as db:
            db.get(LabAttempt, claimed['attempt_id']).lease_until = svc.now() - timedelta(seconds=1)
        # Historical exact credentials can inspect; stale execution cannot sign
        # or abort. Project-authorized cleanup never grants publication rights.
        assert machine.get(scope + '/transfers/' + identifier, headers=headers).status_code == 200
        assert machine.post(scope + '/transfers/' + identifier + '/parts/1', headers=headers).status_code == 409
        assert machine.post(scope + '/transfers/' + identifier + '/abort', headers=headers).status_code == 409
        assert client.get(base + '/transfers/' + identifier).status_code == 200
        listing = client.get(base + '/transfers').json()
        assert [item['id'] for item in listing] == [identifier]
        assert client.get(base + '/transfers?limit=0').json() == []
        assert client.post(base + '/transfers/' + identifier + '/abort').json()['state'] == 'aborted'


def test_policy_and_local_storage_admission_do_not_leave_unusable_sessions(fixture):
    factory, project, other, client, base, app, store = fixture
    store.storage.remote = False
    policy = client.get(base + '/transfer-policy')
    assert policy.status_code == 200
    assert policy.json()['backend'] == 'local' and policy.json()['resumable'] is False
    assert policy.json()['policy']['per_file']['max_bytes'] is None
    assert client.post(base + '/transfers', json=request()).status_code == 409
    assert store.calls == []
    with factory() as db:
        assert not list(db.scalars(select(LabTransfer).where(LabTransfer.project_id == project)))


def test_proxy_input_budget_none_and_zero_preserve_exact_empty_bytes(fixture, monkeypatch):
    factory, project, other, client, base, app, store = fixture
    monkeypatch.setenv('TINYSTUDIO_TRANSFER_MAX_FILE_BYTES', 'none')
    body = b'\x00\xff\n'
    response = client.post(base + '/inputs', files={'file': ('authored.bin', body)})
    assert response.status_code == 201
    assert response.json()['sha256'] == hashlib.sha256(body).hexdigest()
    monkeypatch.setenv('TINYSTUDIO_TRANSFER_MAX_FILE_BYTES', '0')
    assert client.post(base + '/inputs', files={'file': ('blocked.bin', b'x')}).status_code == 413
    empty = client.post(base + '/inputs', files={'file': ('empty.bin', b'')})
    assert empty.status_code == 201 and empty.json()['size'] == 0
