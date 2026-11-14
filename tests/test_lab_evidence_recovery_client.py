"""Independent ZIP, CAS and HTTP boundaries for research-client recovery."""
from copy import deepcopy
from email import policy
from email.parser import BytesParser
import hashlib
import json
import stat
import zipfile

import httpx
import pytest

from tinystudio.lab import evidence_recovery_client as module
from tinystudio.lab.evidence_recovery_client import (
    RecoveryArchiveClient, RecoveryImportLimits, RecoveryImportPaths,
)
from tinystudio.lab.evidence_spool import EvidenceSpool, SpoolPolicy


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def archive_fixture(tmp_path, files=None):
    files = files if files is not None else {'evidence/raw.jsonl': b'{"x":1}\npartial'}
    manifest = {'format': 'tinystudio.evidence-spool.v1', 'classification': 'operator_host_evidence',
        'origin': {'server': 'https://research.example', 'run_id': 'run-one', 'attempt_id': 'attempt-one',
            'target_id': 'target-one', 'spec_digest': 'a' * 64, 'engine': 'hardware.discover',
            'implementation_digest': 'b' * 64, 'runtime': {'python': '3.12'}},
        'outcome': {'verification': 'unverified', 'physical_stop': 'unverified'},
        'cleanup': {'status': 'unknown', 'private_context_removed': False},
        'publication': {'status': 'unknown'}, 'policy': {'retention': 'keep'},
        'quota': {'max_bytes': None, 'used_bytes': 0, 'exceeded': False},
        'files': [{'path': name, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
                  for name, content in sorted(files.items())]}
    path = tmp_path / 'public.zip'
    write_archive(path, manifest, files)
    return path, manifest, files


def write_archive(path, manifest, files, *, extras=None):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('manifest.json', json.dumps(manifest).encode())
        for name, content in {**files, **(extras or {})}.items():
            zf.writestr(name, content)


class Server:
    """CAS bytes are parsed from actual multipart requests, never client refs."""
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.events = {}
        self.attach_bodies = []
        self.lose_response = False
        self.attach_error = None
        self.mutate = None
        self.receipt_mutate = None
        self.upload_mutate = None
        self.preview_mutate = None
        self.journal = None

    def handle(self, request):
        self.calls.append((request.method, request.url.path))
        assert request.headers['authorization'] == 'Bearer private-api-sentinel'
        if request.url.path.endswith('/inputs'):
            if self.journal:
                journal = json.loads(self.journal.read_text())
                assert journal['stage'] == 'reserved'
                assert stat.S_IMODE(self.journal.stat().st_mode) == 0o600
            wire = b'Content-Type: ' + request.headers['content-type'].encode() + b'\r\n\r\n' + request.read()
            part = next(BytesParser(policy=policy.default).parsebytes(wire).iter_parts())
            content = part.get_payload(decode=True)
            sha = hashlib.sha256(content).hexdigest()
            name = part.get_filename()
            assert len(name) <= 255 and ':' not in name and '/' not in name
            artifact = {'id': 'object-' + str(len(self.objects)), 'sha256': sha, 'size': len(content),
                        'name': name, 'media_type': part.get_content_type(), 'role': 'source', 'visibility': 'model'}
            self.objects[artifact['id']] = (content, artifact)
            if self.mutate:
                callback, self.mutate = self.mutate, None
                callback()
            if self.upload_mutate:
                self.upload_mutate(artifact)
            return httpx.Response(201, json=artifact)
        body = json.loads(request.read())
        if request.url.path.endswith('/preview'):
            manifest = json.loads(self.objects[body['manifest']['artifact_id']][0])
            preview = {'format': 'tinystudio.operator-recovered-evidence.v1',
                'classification': 'operator_recovered_evidence',
                'source': {'project_id': 'database-project-identity', 'run_id': 'run-one',
                    'attempt_id': body['source_attempt_id'], 'target_id': body['target_id'], 'spec_digest': body['spec_digest']},
                'source_states': {'run': 'recovery_required', 'attempt': 'recovery_required'},
                'source_states_observed_at': '2026-11-13T00:00:00+00:00',
                'manifest': deepcopy(self.objects[body['manifest']['artifact_id']][1]),
                'files': [{'name': ref['name'], 'artifact': deepcopy(self.objects[ref['artifact_id']][1]),
                    'attempt_id': None, 'verified_bytes': {'sha256': ref['sha256'], 'size': ref['size']}}
                    for ref in body['files']],
                'producer_manifest': manifest,
                'qualification': {'scope': 'bytes_and_existing_server_identity_only', 'operator_supplied': True,
                    'scientific_truth': False, 'physical_completion': False, 'runtime_attestation_verified': False,
                    'cleanup_and_outcome': 'producer_claims_only', 'execution_authority_granted': False},
                'parser_policy': {'manifest_in_memory': True, 'max_manifest_bytes': None}}
            preview['digest'] = digest({k: v for k, v in preview.items()
                                       if k not in {'source_states', 'source_states_observed_at'}})
            if self.preview_mutate:
                self.preview_mutate(preview)
            return httpx.Response(200, json=preview)
        self.attach_bodies.append(deepcopy(body))
        if self.journal:
            journal = json.loads(self.journal.read_text())
            assert journal['stage'] == 'prepared' and journal['request'] == body
            assert 'private-api-sentinel' not in self.journal.read_text()
        if self.attach_error:
            return httpx.Response(self.attach_error, json={'detail': 'retry rejected'})
        key = body['idempotency_key']
        if key in self.events:
            receipt = {**deepcopy(self.events[key]), 'replayed': True}
        else:
            receipt = {'classification': 'operator_recovered_evidence', 'idempotency_key': key,
                'request_digest': digest(body), 'author_id': 'researcher-one',
                'source_attempt_id': body['source_attempt_id'],
                'source_states': {'run': 'recovery_required', 'attempt': 'recovery_required'},
                'manifest_artifact_id': body['manifest']['artifact_id'], 'manifest_sha256': body['manifest']['sha256'],
                'receipt': {'id': 'receipt-one', 'sha256': 'c' * 64, 'size': 123,
                    'name': 'recovered-evidence-receipt.json', 'media_type': 'application/json',
                    'role': 'operator_recovered_evidence', 'visibility': 'evaluation'}, 'replayed': False}
            self.events[key] = deepcopy(receipt)
        if self.lose_response:
            self.lose_response = False
            raise httpx.ReadError('lost response after commit', request=request)
        if self.receipt_mutate:
            self.receipt_mutate(receipt)
        return httpx.Response(200, json=receipt)

    def client(self, **kwargs):
        http = httpx.Client(base_url='https://research.example', transport=httpx.MockTransport(self.handle),
                            headers={'Authorization': 'Bearer private-api-sentinel'})
        return RecoveryArchiveClient(http, '/api/p/project-one/lab', **kwargs)


def test_lost_commit_retry_replays_exact_body_after_4xx_and_source_loss(tmp_path):
    archive, _, _ = archive_fixture(tmp_path)
    server = Server()
    server.journal = RecoveryImportPaths(archive).request
    client = server.client()
    server.lose_response = True
    with pytest.raises(httpx.ReadError, match='lost response'):
        client.import_archive(archive, idempotency_key='recover-once')
    prepared = server.journal.read_bytes()
    assert len(server.events) == 1
    archive.unlink()
    server.objects.clear()
    server.attach_error = 409
    with pytest.raises(httpx.HTTPStatusError):
        client.import_archive(archive, idempotency_key='recover-once')
    assert server.journal.read_bytes() == prepared
    server.attach_error = None
    result = client.import_archive(archive, idempotency_key='recover-once')
    assert result['replayed'] is True and len(server.events) == 1
    assert server.attach_bodies[0] == server.attach_bodies[1] == server.attach_bodies[2]
    assert [path for _, path in server.calls].count('/api/p/project-one/lab/inputs') == 2
    assert sum(path.endswith('/preview') for _, path in server.calls) == 1
    assert json.loads(server.journal.read_text())['stage'] == 'completed'


def test_preview_only_reserves_then_prepares_without_attachment_and_is_resumable(tmp_path):
    archive, manifest, files = archive_fixture(tmp_path)
    server = Server()
    custom = tmp_path / 'chosen-request.json'
    server.journal = custom
    client = server.client()
    result = client.import_archive(archive, idempotency_key='chosen', request_path=custom, preview_only=True)
    assert result['preview_only'] is True and result['request_path'] == str(custom)
    assert not server.events
    assert {a['name']: content for content, a in server.objects.values()} == {
        'manifest.json': json.dumps(manifest).encode(), 'raw.jsonl': files['evidence/raw.jsonl']}
    archive.unlink()
    assert client.import_archive(archive, idempotency_key='chosen', request_path=custom, preview_only=True) == result
    assert client.import_archive(archive, idempotency_key='chosen', request_path=custom)['source_attempt_id'] == 'attempt-one'


@pytest.mark.parametrize('change', ['project', 'server', 'key'])
def test_journal_scope_cannot_be_rebound_before_any_retry(tmp_path, change):
    archive, _, _ = archive_fixture(tmp_path)
    server = Server()
    client = server.client()
    client.import_archive(archive, idempotency_key='original', preview_only=True)
    before = list(server.calls)
    if change == 'project':
        client = RecoveryArchiveClient(client.http, '/api/p/other-project/lab')
    with pytest.raises(ValueError, match='scope'):
        if change == 'server':
            client = RecoveryArchiveClient(client.http, '/api/p/project-one/lab', server='https://other.example')
        client.import_archive(archive, idempotency_key='different' if change == 'key' else 'original')
    assert server.calls == before


@pytest.mark.parametrize('field,value', [('idempotency_key', 'wrong'), ('request_digest', '0' * 64),
    ('source_attempt_id', 'wrong'), ('manifest_artifact_id', 'wrong'), ('manifest_sha256', '0' * 64),
    ('replayed', 'false'), ('receipt', {'id': 'only'})])
def test_mismatched_or_malformed_receipts_retain_exact_pending_request(tmp_path, field, value):
    archive, _, _ = archive_fixture(tmp_path)
    server = Server()
    client = server.client()
    client.import_archive(archive, idempotency_key='pending', preview_only=True)
    journal = RecoveryImportPaths(archive).request
    before = journal.read_bytes()
    server.receipt_mutate = lambda receipt: receipt.__setitem__(field, value)
    with pytest.raises(ValueError, match='receipt'):
        client.import_archive(archive, idempotency_key='pending')
    assert journal.read_bytes() == before


@pytest.mark.parametrize('extra', ['private/context.json', 'context.json', '../escape', '/escape',
                                  'evidence/../context.json', 'evidence/unlisted.txt'])
def test_archive_rejects_every_unlisted_or_unsafe_member_before_upload(tmp_path, extra):
    archive, manifest, files = archive_fixture(tmp_path)
    write_archive(archive, manifest, files, extras={extra: b'private'})
    server = Server()
    with pytest.raises(ValueError):
        server.client().import_archive(archive, idempotency_key='unsafe')
    assert not server.calls


def test_duplicate_members_and_symlinks_are_rejected_before_upload(tmp_path):
    archive, manifest, files = archive_fixture(tmp_path)
    with zipfile.ZipFile(archive, 'a') as zf:
        with pytest.warns(UserWarning, match='Duplicate'):
            zf.writestr('manifest.json', '{}')
    server = Server()
    with pytest.raises(ValueError):
        server.client().import_archive(archive, idempotency_key='duplicate')
    write_archive(archive, manifest, {})
    with zipfile.ZipFile(archive, 'a') as zf:
        info = zipfile.ZipInfo('evidence/raw.jsonl')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, b'/private/context.json')
    with pytest.raises(ValueError):
        server.client().import_archive(archive, idempotency_key='duplicate')
    assert not server.calls


@pytest.mark.parametrize('kind', ['manifest', 'archive', 'evidence'])
def test_explicit_host_byte_budgets_reject_oversized_archives_before_upload(tmp_path, kind):
    archive, _, _ = archive_fixture(tmp_path)
    server = Server()
    with pytest.raises(ValueError, match='budget'):
        server.client(limits=RecoveryImportLimits(**{kind + '_bytes': 1})).import_archive(archive, idempotency_key='budget')
    assert not server.calls


@pytest.mark.parametrize('kind', ['size', 'hash', 'crc', 'private_manifest', 'jsonl'])
def test_archive_checks_inventory_bytes_crc_and_public_manifest(tmp_path, kind):
    archive, manifest, files = archive_fixture(tmp_path)
    if kind == 'size':
        manifest['files'][0]['size'] += 1
    elif kind == 'hash':
        manifest['files'][0]['sha256'] = '0' * 64
    elif kind == 'private_manifest':
        manifest['origin']['lease_token'] = 'private'
    elif kind == 'jsonl':
        manifest['files'][0]['jsonl'] = {'newline_count': 99, 'tail_bytes': 1,
            'ends_with_newline': True, 'semantic_validation': 'not_performed'}
    write_archive(archive, manifest, files)
    if kind == 'crc':
        wire = bytearray(archive.read_bytes())
        with zipfile.ZipFile(archive) as zf:
            member = zf.getinfo('evidence/raw.jsonl')
            offset = member.header_offset + 30 + len(member.filename.encode()) + len(member.extra)
        wire[offset + member.compress_size // 2] ^= 0x10
        archive.write_bytes(wire)
    server = Server()
    with pytest.raises(ValueError):
        server.client().import_archive(archive, idempotency_key='invalid')
    assert not server.calls


def test_archive_mutation_after_upload_cannot_prepare_or_attach(tmp_path):
    archive, _, _ = archive_fixture(tmp_path)
    server = Server()
    server.mutate = lambda: archive.write_bytes(b'changed after upload')
    with pytest.raises(ValueError, match='changed'):
        server.client().import_archive(archive, idempotency_key='mutation')
    assert len(server.calls) == 1
    assert not server.events
    assert json.loads(RecoveryImportPaths(archive).request.read_text())['stage'] == 'reserved'


@pytest.mark.parametrize('boundary', ['upload', 'preview'])
def test_invalid_upload_or_preview_identity_cannot_prepare(tmp_path, boundary):
    archive, _, _ = archive_fixture(tmp_path)
    server = Server()
    if boundary == 'upload':
        server.upload_mutate = lambda value: value.__setitem__('size', True)
    else:
        server.preview_mutate = lambda value: value['source'].__setitem__('attempt_id', 'foreign')
    with pytest.raises(ValueError):
        server.client().import_archive(archive, idempotency_key='boundary')
    assert not server.events
    assert json.loads(RecoveryImportPaths(archive).request.read_text())['stage'] == 'reserved'


def test_journal_must_be_private_regular_and_uncorrupted(tmp_path):
    archive, _, _ = archive_fixture(tmp_path)
    server = Server()
    client = server.client()
    client.import_archive(archive, idempotency_key='journal', preview_only=True)
    journal = RecoveryImportPaths(archive).request
    original = journal.read_bytes()
    journal.chmod(0o644)
    with pytest.raises(ValueError, match='0600'):
        client.import_archive(archive, idempotency_key='journal')
    journal.chmod(0o600)
    value = json.loads(original)
    value['request']['preview_digest'] = '0' * 64
    journal.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='checksum'):
        client.import_archive(archive, idempotency_key='journal')
    journal.unlink()
    other = tmp_path / 'other.json'
    other.write_bytes(original)
    journal.symlink_to(other)
    with pytest.raises(ValueError):
        client.import_archive(archive, idempotency_key='journal')


def test_actual_spool_export_imports_without_retaining_runner_private_context(tmp_path):
    spec = {'research_identity': 'run-one', 'definition': {'engine': 'hardware.discover'},
            'implementation': {'version': 1}, 'runtime': {'python': '3.12'}}
    entry = EvidenceSpool(SpoolPolicy(True, tmp_path / 'spool')).allocate(
        {'run_id': 'run-one', 'attempt_id': 'attempt-one', 'spec': spec, 'spec_digest': digest(spec)},
        server='https://research.example', target_id='target-one')
    output = entry.execution_root / 'work/evidence'
    output.mkdir(parents=True)
    (output / 'raw.jsonl').write_bytes(b'partial\nlast')
    (entry.execution_root / 'context.json').write_text('runner-private-sentinel')
    snapshot = entry.snapshot()
    archive = tmp_path / 'export.zip'
    entry.export(archive, expected_manifest_digest=snapshot['manifest_sha256'])
    server = Server()
    result = server.client().import_archive(archive, idempotency_key='real-export')
    assert result['classification'] == 'operator_recovered_evidence'
    assert all(b'runner-private-sentinel' not in content for content, _ in server.objects.values())
    assert 'private-api-sentinel' not in RecoveryImportPaths(archive).request.read_text()


def test_producer_url_is_a_claim_while_deployment_path_binds_the_journal(tmp_path):
    archive, manifest, files = archive_fixture(tmp_path)
    manifest['origin']['server'] = 'http://runner-loopback.example:8123'
    write_archive(archive, manifest, files)
    server = Server()
    client = server.client()
    client.http.base_url = 'https://research.example/deployment-one/'
    client = RecoveryArchiveClient(client.http, '/api/p/project-one/lab')
    client.import_archive(archive, idempotency_key='deployment', preview_only=True)
    journal = json.loads(RecoveryImportPaths(archive).request.read_text())
    assert journal['scope']['server'] == 'https://research.example/deployment-one'
    calls = list(server.calls)
    client.http.base_url = 'https://research.example/deployment-two/'
    with pytest.raises(ValueError, match='scope'):
        RecoveryArchiveClient(client.http, '/api/p/project-one/lab').import_archive(archive, idempotency_key='deployment')
    assert server.calls == calls


def test_evidence_is_read_and_uploaded_in_bounded_chunks(tmp_path, monkeypatch):
    content = b'abcdefgh\n' * (400 * 1024)
    archive, _, _ = archive_fixture(tmp_path, {'evidence/large.dat': content})
    original = zipfile.ZipExtFile.read
    evidence_reads = []
    def checked_read(stream, size=-1):
        if stream.name == 'evidence/large.dat':
            assert 0 < size <= 1024 * 1024
            evidence_reads.append(size)
        return original(stream, size)
    monkeypatch.setattr(zipfile.ZipExtFile, 'read', checked_read)
    server = Server()
    receipt = server.client().import_archive(archive, idempotency_key='streamed')
    assert receipt['classification'] == 'operator_recovered_evidence'
    assert len(evidence_reads) > 4
    assert next(data for data, artifact in server.objects.values() if artifact['name'] == 'large.dat') == content


def test_prepared_retry_requires_successful_local_fsync_before_attachment(tmp_path, monkeypatch):
    archive, _, _ = archive_fixture(tmp_path)
    server = Server()
    client = server.client()
    client.import_archive(archive, idempotency_key='durable', preview_only=True)
    journal = RecoveryImportPaths(archive).request
    before = journal.read_bytes()
    def fail_fsync(descriptor):
        raise OSError('owned fsync failure')
    with monkeypatch.context() as patch:
        patch.setattr(module.os, 'fsync', fail_fsync)
        with pytest.raises(OSError, match='owned fsync failure'):
            client.import_archive(archive, idempotency_key='durable')
    assert not server.attach_bodies and journal.read_bytes() == before
    assert client.import_archive(archive, idempotency_key='durable')['replayed'] is False


def test_completed_receipt_is_source_independent_and_guarded_against_destination_changes(tmp_path):
    archive, _, _ = archive_fixture(tmp_path)
    server = Server()
    client = server.client()
    result = client.import_archive(archive, idempotency_key='complete')
    archive.unlink()
    calls = list(server.calls)
    assert client.import_archive(archive, idempotency_key='complete') == result
    assert server.calls == calls
    client.http.base_url = 'https://research.example/other-deployment/'
    with pytest.raises(ValueError, match='scope'):
        client.import_archive(archive, idempotency_key='complete')
    assert server.calls == calls


def test_unknown_upload_response_may_reupload_before_an_attach_intent_exists(tmp_path):
    archive, _, _ = archive_fixture(tmp_path)
    server = Server()
    client = server.client()
    def lost_response(artifact):
        raise httpx.ReadError('unknown upload response')
    server.upload_mutate = lost_response
    with pytest.raises(httpx.ReadError, match='unknown upload'):
        client.import_archive(archive, idempotency_key='upload-retry')
    assert len(server.objects) == 1 and not server.events
    journal = RecoveryImportPaths(archive).request
    assert json.loads(journal.read_text())['stage'] == 'reserved'
    server.upload_mutate = None
    client.import_archive(archive, idempotency_key='upload-retry')
    assert len(server.objects) == 3 and len(server.events) == 1


@pytest.mark.parametrize('field,value', [('manifest', None), ('files', None), ('stage', [])])
def test_even_checksum_consistent_malformed_journals_are_rejected(tmp_path, field, value):
    archive, _, _ = archive_fixture(tmp_path)
    server = Server()
    client = server.client()
    client.import_archive(archive, idempotency_key='invalid-journal', preview_only=True)
    journal = RecoveryImportPaths(archive).request
    document = json.loads(journal.read_text())
    if field == 'stage':
        document[field] = value
    else:
        document['request'][field] = value
    document['checksum'] = digest({key: item for key, item in document.items() if key != 'checksum'})
    journal.write_text(json.dumps(document))
    calls = list(server.calls)
    with pytest.raises(ValueError):
        client.import_archive(archive, idempotency_key='invalid-journal')
    assert server.calls == calls


def test_actual_spool_export_preserves_deep_logical_paths_and_colon_components(tmp_path):
    spec = {'research_identity': 'run-one', 'definition': {'engine': 'hardware.discover'},
            'implementation': {'version': 1}, 'runtime': {'python': '3.12'}}
    entry = EvidenceSpool(SpoolPolicy(True, tmp_path / 'spool')).allocate(
        {'run_id': 'run-one', 'attempt_id': 'attempt-one', 'spec': spec, 'spec_digest': digest(spec)},
        server='https://research.example', target_id='target-one')
    relative = '/'.join(['channel:recordings'] * 20 + ['sensor:raw.jsonl'])
    logical = 'evidence/' + relative
    assert len(logical) > 255
    output = entry.execution_root / 'work/evidence' / relative
    output.parent.mkdir(parents=True)
    content = b'{"reading":7}\npartial'
    output.write_bytes(content)
    snapshot = entry.snapshot()
    archive = tmp_path / 'deep-export.zip'
    entry.export(archive, expected_manifest_digest=snapshot['manifest_sha256'])
    server = Server()
    result = server.client().import_archive(archive, idempotency_key='deep-logical-path')
    assert result['classification'] == 'operator_recovered_evidence'
    attached = server.attach_bodies[0]['files']
    assert len(attached) == 1 and attached[0]['name'] == logical
    uploaded, artifact = server.objects[attached[0]['artifact_id']]
    assert uploaded == content
    assert artifact['name'] == 'evidence-' + hashlib.sha256(logical.encode()).hexdigest()
