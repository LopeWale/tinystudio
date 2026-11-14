"""Resumable controls with real owned bytes and a fake object-data plane.

MockTransport models Studio acknowledgements; FakeData never contacts an object
store. These checks do not qualify R2 availability, durability or SDK behavior.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat

import httpx
import pytest

from tinystudio.lab.transfer_client import ArtifactTransferError, ResumableTransferClient
from tinystudio.lab.transfer_configuration import MultipartPlan, R2_MINIMUM_PART_BYTES, TransferApiPaths


class Server:
    def __init__(self, content):
        self.content = content
        self.paths = TransferApiPaths('/api/p/owned/lab/transfers')
        self.calls = []
        self.intent = None
        self.session_id = 'session-one'
        self.state = 'uploading'
        self.operation_state = None
        self.parts = {}
        self.complete_state = 'verified'
        self.complete_operation_state = None
        self.poll_states = []
        self.lose_complete_ack = False
        self.row_override = None
        self.descriptor_override = None
        self.plan_override = None
        self.inventory_override = None
        self.artifact_override = None
        self.response_mutator = None

    def row(self):
        result = {'id': self.session_id, 'state': self.state, 'intent': deepcopy(self.intent),
                  'plan': MultipartPlan(len(self.content), R2_MINIMUM_PART_BYTES).as_dict()}
        if self.plan_override is not None:
            result['plan'].update(self.plan_override)
        if self.operation_state is not None:
            result['operation_state'] = self.operation_state
        if self.state == 'verified':
            result['artifact'] = {'id': 'artifact-one', **deepcopy(self.intent)}
            if self.artifact_override is not None:
                result['artifact'].update(self.artifact_override)
        if self.row_override is not None:
            result.update(self.row_override)
        return result

    def handle(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body, dict(request.headers)))
        path = request.url.path
        if path == self.paths.collection and request.method == 'POST':
            self.intent = {key: value for key, value in body.items() if key != 'idempotency_key'}
            result = self.row()
        elif path == self.paths.session(self.session_id) and request.method == 'GET':
            if self.poll_states:
                self.state, self.operation_state = self.poll_states.pop(0)
            result = self.row()
        elif path == self.paths.initialize(self.session_id):
            self.state = 'uploading'
            result = self.row()
        elif path == self.paths.parts(self.session_id):
            result = {'transfer': self.row(), 'parts': [{'number': number, 'size': len(data), 'etag': 'owned-'+str(number)}
                                for number, data in sorted(self.parts.items())]}
            if self.inventory_override is not None:
                result['parts'] = self.inventory_override
        elif path.startswith(self.paths.parts(self.session_id)+'/'):
            number = int(path.rsplit('/', 1)[1])
            selected = MultipartPlan(len(self.content), R2_MINIMUM_PART_BYTES).part(number)
            result = {'part_number': number, **selected,
                'url': 'https://objects.example/owned-part?signature=private-signed-url', 'headers': {}}
            if self.descriptor_override is not None:
                result.update(self.descriptor_override)
        elif path == self.paths.complete(self.session_id):
            self.state, self.operation_state = self.complete_state, self.complete_operation_state
            result = self.row()
            if self.lose_complete_ack:
                self.lose_complete_ack = False
                raise httpx.ReadError('owned lost control acknowledgement', request=request)
        else:
            raise AssertionError((request.method, path))
        if self.response_mutator is not None:
            result = self.response_mutator(request, result)
        return httpx.Response(200, json=result)

    def count(self, method, path):
        return sum(row[0] == method and row[1] == path for row in self.calls)


class FakeData:
    """Read actual source parts locally and model provider acceptance/ACK loss."""
    def __init__(self, server):
        self.server = server
        self.uploads = []
        self.identity_reads = 0
        self.lose_part_ack = None
        self.after_upload = None

    def source_identity(self, path):
        self.identity_reads += 1
        with Path(path).open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        return {'sha256': digest, 'size': Path(path).stat().st_size}

    def upload_part(self, descriptor, path, *, offset, size):
        with Path(path).open('rb') as stream:
            stream.seek(offset)
            data = stream.read(size)
        assert len(data) == size
        number = offset // R2_MINIMUM_PART_BYTES + 1
        self.uploads.append({'number': number, 'offset': offset, 'size': size, 'descriptor': deepcopy(descriptor)})
        self.server.parts[number] = data
        if self.after_upload is not None:
            self.after_upload()
        if self.lose_part_ack == number:
            self.lose_part_ack = None
            raise ArtifactTransferError('owned provider ACK lost after accepting part')
        return {'size': size, 'etag': 'owned-'+str(number)}


class Harness:
    def __init__(self, tmp_path, content=b'\x00\xff\n', *, origin='https://studio.example'):
        self.source = tmp_path/'owned-source.bin'
        self.source.write_bytes(content)
        self.state = tmp_path/'private-state.json'
        self.server = Server(content)
        self.data = FakeData(self.server)
        self.http = httpx.Client(base_url=origin, transport=httpx.MockTransport(self.server.handle))

    def client(self, **kwargs):
        return ResumableTransferClient(self.http, self.server.paths, data_client=self.data,
                                      poll_seconds=.0001, **kwargs)

    def upload(self, client=None, **kwargs):
        return (client or self.client()).upload(self.source, state_path=self.state, **kwargs)


def test_restart_after_lost_part_ack_skips_accepted_part_and_preserves_actual_bytes(tmp_path):
    content = b'A'*R2_MINIMUM_PART_BYTES+b'\x00\xff\n'
    h = Harness(tmp_path, content)
    h.data.lose_part_ack = 1
    with pytest.raises(ArtifactTransferError, match='ACK lost'):
        h.upload()
    assert [part['number'] for part in h.data.uploads] == [1]
    assert stat.S_IMODE(h.state.stat().st_mode) == 0o600
    h.data = FakeData(h.server)  # A fresh client/process sees only persisted identity and server inventory.
    artifact = h.upload()
    assert [part['number'] for part in h.data.uploads] == [2]
    assert b''.join(h.server.parts[number] for number in sorted(h.server.parts)) == content
    assert artifact['sha256'] == hashlib.sha256(content).hexdigest()
    assert h.server.count('POST', h.server.paths.collection) == 1
    assert h.server.count('POST', h.server.paths.complete(h.server.session_id)) == 1


def test_completion_ack_loss_resumes_verifier_without_repeating_completion(tmp_path):
    h = Harness(tmp_path)
    h.server.complete_state = 'verifying'
    h.server.complete_operation_state = 'running'
    h.server.lose_complete_ack = True
    with pytest.raises(ArtifactTransferError, match='unconfirmed'):
        h.upload()
    h.server.poll_states = [('verifying', 'running'), ('verified', 'succeeded')]
    assert h.upload()['id'] == 'artifact-one'
    assert len(h.data.uploads) == 1
    assert h.server.count('POST', h.server.paths.complete(h.server.session_id)) == 1


@pytest.mark.parametrize('phase', ['completion_uncertain', 'sealing', 'sealed'])
def test_completion_uncertainty_requires_an_explicit_new_invocation(tmp_path, phase):
    h = Harness(tmp_path)
    h.server.complete_state = phase
    with pytest.raises(ArtifactTransferError, match='unpublished'):
        h.upload()
    assert h.server.count('POST', h.server.paths.complete(h.server.session_id)) == 1
    h.server.complete_state = 'verified'
    assert h.upload()['id'] == 'artifact-one'
    assert h.server.count('POST', h.server.paths.complete(h.server.session_id)) == 2
    assert len(h.data.uploads) == 1  # Only the control reconcile request was repeated.


@pytest.mark.parametrize('operation_state', ['failed', 'lost', 'cancelled'])
def test_terminal_verifier_is_never_automatically_retried_even_on_explicit_resume(tmp_path, operation_state):
    h = Harness(tmp_path)
    h.server.complete_state = 'verifying'
    h.server.complete_operation_state = operation_state
    for _ in range(2):
        with pytest.raises(ArtifactTransferError, match='unpublished'):
            h.upload()
    assert h.server.count('POST', h.server.paths.complete(h.server.session_id)) == 1
    assert len(h.data.uploads) == 1


@pytest.mark.parametrize('content', [b'', b'\x00\x01\xff\r\n'])
def test_raw_metadata_and_zero_objects_have_no_hidden_total_or_sample_floor(tmp_path, content):
    h = Harness(tmp_path, content)
    artifact = h.upload(name='  owned é raw.bin  ', media_type='application/x-owned', role='arbitrary-role', visibility='truth')
    assert artifact == {'id': 'artifact-one', 'name': '  owned é raw.bin  ', 'media_type': 'application/x-owned',
        'role': 'arbitrary-role', 'visibility': 'truth', 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
    assert len(h.data.uploads) == bool(content)
    assert h.server.count('POST', h.server.paths.complete(h.server.session_id)) == 1


def test_saved_state_never_contains_urls_or_control_credentials_or_raw_idempotency_key(tmp_path):
    h = Harness(tmp_path)
    headers = {'Authorization': 'Bearer private-app', 'CF-Access-Client-Id': 'private-access-id',
               'CF-Access-Client-Secret': 'private-access-secret', 'X-Lab-Lease': 'private-lease'}
    h.upload(h.client(headers=headers), idempotency_key='private-request-key')
    text = h.state.read_text()
    assert all(secret not in text for secret in (*headers.values(), 'private-request-key', 'signature=', 'objects.example', 'https://'))
    assert json.loads(text)['origin'] == ['https', 'studio.example', 443]
    assert all(call[3]['authorization'] == headers['Authorization'] for call in h.server.calls)
    assert all(part['descriptor']['headers'] == {} for part in h.data.uploads)


def test_default_idempotency_key_is_deterministic_and_binds_raw_metadata(tmp_path):
    h = Harness(tmp_path)
    for name in ('owned-name', 'owned-name', 'different-name'):
        h.client().upload(h.source, name=name)
    admissions = [call[2] for call in h.server.calls if call[0] == 'POST' and call[1] == h.server.paths.collection]
    assert len(admissions) == 3
    keys = [body['idempotency_key'] for body in admissions]
    assert len(keys[0]) == 64 and keys[0] == keys[1] and keys[0] != keys[2]


@pytest.mark.parametrize('state', ['failed', 'lost', 'cancelled', 'aborted'])
def test_terminal_session_never_reinitializes_or_resends_data(tmp_path, state):
    h = Harness(tmp_path)
    h.server.state = state
    for _ in range(2):
        with pytest.raises(ArtifactTransferError, match='unpublished'):
            h.upload()
    assert h.data.uploads == []
    assert h.server.count('POST', h.server.paths.collection) == 1
    assert h.server.count('POST', h.server.paths.initialize(h.server.session_id)) == 0
    assert h.server.count('POST', h.server.paths.complete(h.server.session_id)) == 0


@pytest.mark.parametrize('changed', ['origin', 'name', 'key'])
def test_existing_state_is_bound_to_origin_and_exact_request_before_control_io(tmp_path, changed):
    h = Harness(tmp_path)
    h.upload(idempotency_key='original-key')
    before = h.state.read_bytes()
    h.server.calls.clear()
    if changed == 'origin':
        h.http = httpx.Client(base_url='https://other.example', transport=httpx.MockTransport(h.server.handle))
    options = {'idempotency_key': 'new-key' if changed == 'key' else 'original-key'}
    if changed == 'name':
        options['name'] = 'different-name'
    with pytest.raises(ArtifactTransferError, match='different or invalid request'):
        h.upload(**options)
    assert h.server.calls == [] and h.state.read_bytes() == before


@pytest.mark.parametrize('deadline', [0., -1.])
def test_expired_deadline_precedes_source_state_or_control_io(tmp_path, deadline):
    h = Harness(tmp_path)
    h.source.unlink()
    with pytest.raises(ArtifactTransferError, match='deadline expired'):
        h.upload(h.client(deadline=deadline))
    assert h.data.identity_reads == 0 and h.server.calls == [] and not h.state.exists()


def test_authority_failure_precedes_all_io_and_none_deadline_is_unbounded(tmp_path):
    h = Harness(tmp_path)
    def expired():
        raise RuntimeError('owned authority expired')
    with pytest.raises(RuntimeError, match='authority expired'):
        h.upload(h.client(check_authority=expired, deadline=None))
    assert h.data.identity_reads == 0 and h.server.calls == []
    assert h.upload(h.client(deadline=None))['id'] == 'artifact-one'


@pytest.mark.parametrize('geometry', [{'part_bytes': 1}, {'part_count': 2}, {'size': 99}, {'part_count': True}])
def test_invalid_provider_geometry_is_rejected_before_data_upload(tmp_path, geometry):
    h = Harness(tmp_path)
    h.server.plan_override = geometry
    with pytest.raises(ArtifactTransferError, match='geometry'):
        h.upload()
    assert h.data.uploads == []
    assert h.server.count('POST', h.server.paths.complete(h.server.session_id)) == 0


@pytest.mark.parametrize('parts', [
    [{'number': 1, 'size': 3}, {'number': 1, 'size': 3}],
    [{'number': True, 'size': 3}], [{'number': 1, 'size': 2}],
])
def test_retained_inventory_never_silently_replaces_duplicate_or_wrong_parts(tmp_path, parts):
    h = Harness(tmp_path)
    h.server.inventory_override = parts
    with pytest.raises(ArtifactTransferError, match='part'):
        h.upload()
    assert h.data.uploads == []
    assert h.server.count('POST', h.server.paths.complete(h.server.session_id)) == 0


def test_expected_source_sha_and_authority_after_hash_precede_control_io(tmp_path):
    h = Harness(tmp_path)
    with pytest.raises(ArtifactTransferError, match='expected SHA256'):
        h.upload(expected_sha256='0'*64)
    assert h.server.calls == [] and not h.state.exists()
    active = [True]
    identity = h.data.source_identity
    def hash_then_expire(path):
        result = identity(path)
        active[0] = False
        return result
    def check():
        if not active[0]:
            raise RuntimeError('owned authority expired after hash')
    h.data.source_identity = hash_then_expire
    with pytest.raises(RuntimeError, match='expired after hash'):
        h.upload(h.client(check_authority=check))
    assert h.server.calls == [] and h.data.uploads == []


def test_changed_local_source_after_part_prevents_completion(tmp_path):
    h = Harness(tmp_path)
    h.data.after_upload = lambda: h.source.write_bytes(b'new')
    with pytest.raises(ArtifactTransferError, match='changed during transfer'):
        h.upload()
    assert h.server.count('POST', h.server.paths.complete(h.server.session_id)) == 0


@pytest.mark.parametrize('descriptor', [{'part_number': True}, {'offset': 0.0}, {'size': 3.0}])
def test_signed_descriptor_requires_exact_integer_geometry_before_data_io(tmp_path, descriptor):
    h = Harness(tmp_path)
    h.server.descriptor_override = descriptor
    with pytest.raises(ArtifactTransferError, match='geometry'):
        h.upload()
    assert h.data.uploads == []


def test_bad_inspection_response_cannot_replace_saved_session_identity(tmp_path):
    h = Harness(tmp_path)
    h.upload()
    before = h.state.read_bytes()
    h.server.row_override = {'id': 'different-session', 'intent': {'name': 'wrong'}}
    with pytest.raises(ArtifactTransferError, match='metadata|identity'):
        h.upload()
    assert h.state.read_bytes() == before


def test_identical_intent_does_not_authorize_a_different_inspected_session_id(tmp_path):
    h = Harness(tmp_path)
    h.upload()
    before = h.state.read_bytes()
    h.server.row_override = {'id': 'different-session'}
    with pytest.raises(ArtifactTransferError, match='identity'):
        h.upload()
    assert h.state.read_bytes() == before


@pytest.mark.parametrize('identity', ['https://objects.example/object?signature=private-capability', '../other-session'])
def test_invalid_response_session_identity_is_rejected_before_state_persistence(tmp_path, identity):
    h = Harness(tmp_path)
    h.server.row_override = {'id': identity}
    with pytest.raises((ArtifactTransferError, ValueError)):
        h.upload()
    assert not h.state.exists(), 'invalid session response must not persist a URL or unusable resume identity'
    assert h.data.uploads == []


@pytest.mark.parametrize('artifact', [{'id': None}, {'id': ''}, {'size': True}])
def test_verified_receipt_requires_an_artifact_id_and_exact_typed_identity(tmp_path, artifact):
    h = Harness(tmp_path, b'x')
    h.server.artifact_override = artifact
    with pytest.raises(ArtifactTransferError, match='receipt|identity'):
        h.upload()


@pytest.mark.parametrize('transition', ['initialize', 'complete', 'poll'])
def test_every_session_transition_rejects_a_different_response_identity(tmp_path, transition):
    h = Harness(tmp_path)
    if transition == 'initialize':
        h.server.state = 'admitted'
        target = h.server.paths.initialize(h.server.session_id)
    elif transition == 'complete':
        target = h.server.paths.complete(h.server.session_id)
    else:
        h.server.complete_state = 'verifying'
        h.server.complete_operation_state = 'running'
        h.server.poll_states = [('verified', 'succeeded')]
        target = h.server.paths.session(h.server.session_id)
    h.server.response_mutator = lambda request, row: ({**row, 'id': 'different-session'}
        if request.url.path == target else row)
    with pytest.raises(ArtifactTransferError, match='identity|session'):
        h.upload()
    assert json.loads(h.state.read_text())['session_id'] == 'session-one'
    if transition == 'initialize':
        assert h.data.uploads == []


def test_initialization_metadata_mismatch_precedes_data_io(tmp_path):
    h = Harness(tmp_path)
    h.server.state = 'admitted'
    h.server.response_mutator = lambda request, row: ({**row, 'intent': {**row['intent'], 'role': 'different-role'}}
        if request.url.path == h.server.paths.initialize(h.server.session_id) else row)
    with pytest.raises(ArtifactTransferError, match='metadata'):
        h.upload()
    assert h.data.uploads == []


def test_inventory_session_correlation_precedes_any_data_effect(tmp_path):
    h = Harness(tmp_path)
    h.server.response_mutator = lambda request, inventory: ({**inventory,
        'transfer': {**inventory['transfer'], 'id': 'different-session'}}
        if request.url.path == h.server.paths.parts(h.server.session_id) else inventory)
    with pytest.raises(ArtifactTransferError, match='identity|session'):
        h.upload()
    assert h.data.uploads == []
    assert h.server.count('POST', h.server.paths.complete(h.server.session_id)) == 0
    assert json.loads(h.state.read_text())['session_id'] == 'session-one'
