"""Operator recovery attaches verified bytes without reviving execution authority."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import Barrier
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from tinystudio.db.models import Base, Instance, Project, User
from tinystudio.lab import service as svc
from tinystudio.lab.contracts import canonical, checksum
from tinystudio.lab.models import LabArtifact, LabAttempt, LabDefinition, LabEvent, LabRun, LabTarget, TABLES
from tinystudio.storage import ArtifactStorage


def recovery():
    from tinystudio.lab import recovered_evidence
    return recovered_evidence


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'metadata.db'))
    Base.metadata.create_all(engine, tables=[Instance.__table__, User.__table__, Project.__table__, *TABLES])
    factory = sessionmaker(engine, expire_on_commit=False)
    storage = ArtifactStorage(root=tmp_path / 'objects')
    monkeypatch.setattr(svc, 'storage', lambda: storage)
    with factory.begin() as db:
        project, other = Project(slug='recovery', name='Recovery'), Project(slug='other', name='Other')
        author = User(email='recovery@fixture.invalid', password_hash='no-login')
        db.add_all([project, other, author]); db.flush()
        target = LabTarget(project_id=project.id, name='owned-target')
        definition = LabDefinition(project_id=project.id, name='Evidence')
        db.add_all([target, definition]); db.flush()
        spec = {'definition': {'engine': 'acquisition.modbus'}, 'implementation': {'files': {}},
                'runtime': {'python': 'owned-fixture'}}
        run = LabRun(project_id=project.id, definition_id=definition.id, revision=1, target_id=target.id,
                     idempotency_key='original-run', spec=spec, spec_digest=checksum(spec), state='running',
                     result={'records': 0, 'artifacts': []}, error='original diagnostic')
        db.add(run); db.flush()
        attempt = LabAttempt(run_id=run.id, target_id=target.id, number=1, token_hash='expired-token-hash',
                             lease_until=svc.now() + timedelta(days=1), state='running', runtime={})
        foreign = LabAttempt(run_id=run.id, target_id=target.id, number=2, token_hash='different-token-hash',
                             lease_until=svc.now() - timedelta(days=1), state='lost', runtime={})
        db.add_all([attempt, foreign]); db.flush()
        ids = SimpleNamespace(project=project.id, other=other.id, author=author.id, run=run.id,
                              target=target.id, attempt=attempt.id, foreign=foreign.id, spec=spec)
    try:
        yield SimpleNamespace(**vars(ids), factory=factory, root=tmp_path, storage=storage)
    finally:
        engine.dispose()


def artifact_ref(artifact):
    return {'artifact_id': artifact.id, 'sha256': artifact.sha256, 'size': artifact.size}


def uploaded(db, store, *, data=b'{"reading": 7}\nnot-a-json-row', attempt_id=None, manifest_change=None):
    file = store.root / 'source.bin'
    file.write_bytes(data)
    row = svc.store_artifact(db, store.project, file, 'uploaded-evidence.jsonl', 'application/x-ndjson',
                             'original_capture', attempt_id, visibility='evaluation')
    tail = len(data.rsplit(b'\n', 1)[-1])
    manifest = {'format': 'tinystudio.evidence-spool.v1',
                'origin': {'server': 'https://origin.invalid', 'target_id': store.target,
                           'run_id': store.run, 'attempt_id': store.attempt,
                           'spec_digest': checksum(store.spec), 'engine': 'acquisition.modbus',
                           'implementation_digest': checksum(store.spec['implementation']), 'runtime': store.spec['runtime']},
                'classification': 'operator_host_evidence',
                'outcome': {'verification': 'unverified', 'physical_stop': 'unverified'},
                'cleanup': {'status': 'unknown', 'private_context_removed': False},
                'publication': {'status': 'unknown'}, 'policy': {'retention': 'keep'},
                'quota': {'max_bytes': None, 'used_bytes': 999999, 'exceeded': False},
                'files': [{'path': 'evidence/raw.jsonl', 'size': len(data),
                           'sha256': hashlib.sha256(data).hexdigest(),
                           'jsonl': {'newline_count': data.count(b'\n'), 'tail_bytes': tail,
                                     'ends_with_newline': data.endswith(b'\n'), 'semantic_validation': 'not_performed'}}]}
    if manifest_change:
        manifest_change(manifest)
    path = store.root / 'manifest.json'
    path.write_bytes(canonical(manifest) + b'\n')
    manifest_row = svc.store_artifact(db, store.project, path, 'manifest.json', 'application/json')
    body = {'source_attempt_id': store.attempt, 'target_id': store.target, 'spec_digest': checksum(store.spec),
            'manifest': artifact_ref(manifest_row), 'files': [{'name': 'evidence/raw.jsonl', **artifact_ref(row)}]}
    return body, manifest_row, row


def attach(db, store, body, preview, **changes):
    return recovery().attach_recovered_evidence(db, store.project, store.run,
        {**body, 'preview_digest': preview['digest'], 'idempotency_key': 'recover-one', **changes}, store.author)


def test_preview_is_read_only_and_byte_facts_do_not_parse_rows_or_infer_completion(store):
    with store.factory.begin() as db:
        body, _, row = uploaded(db, store)
        before = db.scalar(select(func.count()).select_from(LabArtifact))
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        assert db.scalar(select(func.count()).select_from(LabArtifact)) == before
        assert db.scalar(select(func.count()).select_from(LabEvent)) == 0
        assert preview['files'][0]['artifact']['id'] == row.id
        assert preview['files'][0]['verified_bytes']['jsonl'] == {
            'newline_count': 1, 'tail_bytes': 14, 'ends_with_newline': False, 'semantic_validation': 'not_performed'}
        assert preview['qualification']['scope'] == 'bytes_and_existing_server_identity_only'
        assert preview['qualification']['physical_completion'] is False
        assert preview['qualification']['runtime_attestation_verified'] is False
        assert preview['producer_manifest']['quota']['used_bytes'] == 999999


@pytest.mark.parametrize('state', ['running', 'failed', 'lost', 'recovery_required'])
@pytest.mark.parametrize('attribution', ['uploaded', 'exact_attempt'])
def test_attach_accepts_partial_snapshots_preserves_execution_and_artifact_attribution(store, state, attribution):
    with store.factory.begin() as db:
        run, attempt = db.get(LabRun, store.run), db.get(LabAttempt, store.attempt)
        run.state = attempt.state = state
        original = (deepcopy(run.result), run.error, attempt.token_hash, attempt.lease_until, attempt.result_digest)
        body, manifest, row = uploaded(db, store, attempt_id=store.attempt if attribution == 'exact_attempt' else None)
        metadata = svc.artifact_dict(row)
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        result = attach(db, store, body, preview)
        assert result['replayed'] is False and result['receipt']['role'] == 'operator_recovered_evidence'
        receipt = svc.get_artifact(db, store.project, result['receipt']['id'])
        assert receipt.attempt_id is None and receipt.visibility == 'evaluation'
        with tempfile.TemporaryDirectory() as directory:
            document = json.loads(svc.materialize(receipt, Path(directory)).read_bytes())
        assert document['author_id'] == store.author and document['source_states']['run'] == state
        assert document['qualification']['scientific_truth'] is False
        assert document['source']['attempt_id'] == store.attempt
        assert document['manifest']['id'] == manifest.id
        assert document['files'][0]['artifact']['id'] == row.id
        event = db.scalar(select(LabEvent))
        assert event.kind == 'server.evidence_recovered' and event.payload['author_id'] == store.author
        assert event.payload['classification'] == 'operator_recovered_evidence'
        assert len(canonical(event.payload)) < 2048 and 'files' not in event.payload
        assert run.state == attempt.state == state
        assert (run.result, run.error, attempt.token_hash, attempt.lease_until, attempt.result_digest) == original
        assert row.attempt_id == (store.attempt if attribution == 'exact_attempt' else None)
        assert svc.artifact_dict(row) == metadata


def test_idempotent_retry_returns_original_receipt_and_author_without_reopening_lost_objects(store, monkeypatch):
    with store.factory.begin() as db:
        body, manifest, row = uploaded(db, store)
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        first = attach(db, store, body, preview)
        for item in (manifest, row, svc.get_artifact(db, store.project, first['receipt']['id'])):
            (store.storage.root / item.storage_key).unlink()
        monkeypatch.setattr(svc, 'materialize', lambda *args: pytest.fail('replay must not read objects'))
        result = recovery().attach_recovered_evidence(db, store.project, store.run,
            {**body, 'preview_digest': preview['digest'], 'idempotency_key': 'recover-one'}, 'different-project-member')
        assert result['receipt'] == first['receipt'] and result['replayed'] is True
        assert result['author_id'] == store.author and db.scalar(select(func.count()).select_from(LabEvent)) == 1


def test_conflicting_key_or_preview_is_rejected_without_partial_publication(store):
    with store.factory.begin() as db:
        body, _, _ = uploaded(db, store)
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        with pytest.raises(svc.LabError, match='preview'):
            attach(db, store, body, preview, preview_digest='0' * 64)
        assert db.scalar(select(func.count()).select_from(LabEvent)) == 0
        attach(db, store, body, preview)
        with pytest.raises(svc.LabError, match='different') as error:
            attach(db, store, body, preview, preview_digest='0' * 64)
        assert error.value.status == 409


@pytest.mark.parametrize('change', [
    lambda body, manifest: body.update(source_attempt_id='unknown-attempt'),
    lambda body, manifest: body.update(target_id='unknown-target'),
    lambda body, manifest: body.update(spec_digest='0' * 64),
    lambda body, manifest: body['files'][0].update(size=900),
    lambda body, manifest: body['files'][0].update(sha256='0' * 64),
    lambda body, manifest: body['files'][0].update(name='evidence/other.jsonl'),
    lambda body, manifest: body['files'].append(deepcopy(body['files'][0])),
    lambda body, manifest: body.update(files=[]),
])
def test_request_identity_or_file_mismatch_is_rejected(store, change):
    with store.factory.begin() as db:
        body, manifest, _ = uploaded(db, store)
        change(body, manifest)
        with pytest.raises(svc.LabError):
            recovery().preview_recovered_evidence(db, store.project, store.run, body)
        assert db.scalar(select(func.count()).select_from(LabEvent)) == 0


@pytest.mark.parametrize('logical', ['evidence/channel:recordings/sensor:raw.jsonl',
    'evidence/' + '/'.join(['channel'] * 40) + '/raw.jsonl'])
def test_logical_evidence_paths_preserve_colons_and_deep_names(store, logical):
    with store.factory.begin() as db:
        body, _, _ = uploaded(db, store, manifest_change=lambda manifest: manifest['files'][0].update(path=logical))
        body['files'][0]['name'] = logical
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        assert preview['files'][0]['name'] == logical
        assert preview['producer_manifest']['files'][0]['path'] == logical
        assert attach(db, store, body, preview)['classification'] == 'operator_recovered_evidence'


@pytest.mark.parametrize('logical', ['evidence/../private/context.json', '/evidence/raw.jsonl',
    'evidence/raw\\private', 'evidence//raw.jsonl', 'evidence/./raw.jsonl', 'evidence/',
    'evidence', 'private/raw.jsonl', 'evidence/raw\x00.jsonl', 'evidence/raw\x7f.jsonl'])
def test_logical_evidence_path_validation_retains_scope_and_traversal_rejection(store, logical):
    with store.factory.begin() as db:
        body, _, _ = uploaded(db, store, manifest_change=lambda manifest: manifest['files'][0].update(path=logical))
        body['files'][0]['name'] = logical
        with pytest.raises(svc.LabError):
            recovery().preview_recovered_evidence(db, store.project, store.run, body)


@pytest.mark.parametrize('change', [
    lambda doc: doc.update(unknown='ignored'),
    lambda doc: doc.update(format='tinystudio.evidence-spool.v2'),
    lambda doc: doc['origin'].update(attempt_id='different'),
    lambda doc: doc['origin'].update(engine='different.engine'),
    lambda doc: doc['origin'].update(implementation_digest='0' * 64),
    lambda doc: doc['files'][0]['jsonl'].update(newline_count=20),
    lambda doc: doc['files'][0]['jsonl'].update(tail_bytes=0),
    lambda doc: doc['files'][0].update(path='../private/context.json'),
    lambda doc: doc['files'].append(deepcopy(doc['files'][0])),
])
def test_manifest_schema_identity_and_independent_byte_facts_are_verified(store, change):
    with store.factory.begin() as db:
        body, _, _ = uploaded(db, store, manifest_change=change)
        with pytest.raises(svc.LabError):
            recovery().preview_recovered_evidence(db, store.project, store.run, body)


def test_cross_project_or_foreign_attempt_artifacts_are_refused(store):
    with store.factory.begin() as db:
        body, manifest, row = uploaded(db, store, attempt_id=store.foreign)
        with pytest.raises(svc.LabError, match='attempt'):
            recovery().preview_recovered_evidence(db, store.project, store.run, body)
        row.attempt_id = None
        row.project_id = store.other
        with pytest.raises(svc.LabError, match='artifact not found'):
            recovery().preview_recovered_evidence(db, store.project, store.run, body)
        with pytest.raises(svc.LabError, match='run not found'):
            recovery().preview_recovered_evidence(db, store.other, store.run, body)


@pytest.mark.parametrize('data', [b'', b'\n', b'row', b'row\n', b'one\ntwo\nunfinished'])
def test_jsonl_facts_are_literal_byte_counts_without_row_semantics(store, data):
    with store.factory.begin() as db:
        body, _, _ = uploaded(db, store, data=data)
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        facts = preview['files'][0]['verified_bytes']['jsonl']
        assert facts['newline_count'] == data.count(b'\n')
        assert facts['tail_bytes'] == len(data.rsplit(b'\n', 1)[-1])
        assert facts['ends_with_newline'] == data.endswith(b'\n')


def test_missing_corrupt_or_size_mismatched_storage_objects_block_first_attach(store):
    with store.factory.begin() as db:
        body, _, row = uploaded(db, store)
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        (store.storage.root / row.storage_key).write_bytes(b'corrupt')
        with pytest.raises(svc.LabError):
            attach(db, store, body, preview)
        assert db.scalar(select(func.count()).select_from(LabEvent)) == 0


def test_parser_budget_is_operator_configuration_and_preview_discloses_in_memory_parsing(store):
    with store.factory.begin() as db:
        body, manifest, _ = uploaded(db, store)
        with pytest.raises(svc.LabError) as error:
            recovery().preview_recovered_evidence(db, store.project, store.run, body,
                limits=recovery().RecoveryParserLimits(manifest_bytes=manifest.size - 1))
        assert error.value.status == 413
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        assert preview['parser_policy'] == {'manifest_in_memory': True, 'max_manifest_bytes': None}


def test_natural_state_transition_after_preview_does_not_change_byte_import_identity(store):
    with store.factory.begin() as db:
        body, _, _ = uploaded(db, store)
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        db.get(LabRun, store.run).state = 'lost'
        db.get(LabAttempt, store.attempt).state = 'lost'
        result = attach(db, store, body, preview)
        assert result['source_states'] == {'run': 'lost', 'attempt': 'lost'}


def test_actual_spool_snapshot_upload_preview_and_attach_preserve_unverified_origin(store):
    from tinystudio.lab.evidence_spool import EvidenceSpool, SpoolPolicy

    spool = EvidenceSpool(SpoolPolicy(True, store.root / 'spool', retention=1.25))
    entry = spool.allocate({'run_id': store.run, 'attempt_id': store.attempt, 'spec': store.spec,
                            'spec_digest': checksum(store.spec)}, server='https://origin.invalid', target_id=store.target)
    evidence = entry.execution_root / 'work' / 'evidence'
    evidence.mkdir(parents=True)
    (evidence / 'raw.jsonl').write_bytes(b'partial\nunfinished')
    snapshot = entry.snapshot()
    with store.factory.begin() as db:
        manifest = svc.store_artifact(db, store.project, entry.manifest_path, 'manifest.json', 'application/json')
        raw = svc.store_artifact(db, store.project, evidence / 'raw.jsonl', 'raw.jsonl', 'application/x-ndjson')
        assert manifest.sha256 == snapshot['manifest_sha256']
        body = {'source_attempt_id': store.attempt, 'target_id': store.target, 'spec_digest': checksum(store.spec),
                'manifest': artifact_ref(manifest), 'files': [{'name': 'evidence/raw.jsonl', **artifact_ref(raw)}]}
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        assert preview['producer_manifest'] == snapshot['manifest']
        result = attach(db, store, body, preview)
        assert result['receipt']['visibility'] == 'evaluation'


def test_failed_event_publication_rolls_back_artifact_metadata_and_sequence(store, monkeypatch):
    with store.factory.begin() as db:
        body, _, _ = uploaded(db, store)
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        count = db.scalar(select(func.count()).select_from(LabArtifact))
        original = svc.event

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise svc.LabError('controlled publication failure', 409)

        monkeypatch.setattr(svc, 'event', fail)
        with pytest.raises(svc.LabError, match='controlled'):
            attach(db, store, body, preview)
        assert db.scalar(select(func.count()).select_from(LabArtifact)) == count
        assert db.scalar(select(func.count()).select_from(LabEvent)) == 0
        assert db.get(LabRun, store.run).next_seq == 0


def test_metadata_change_after_byte_verification_refuses_publication(store, monkeypatch):
    with store.factory.begin() as db:
        body, _, row = uploaded(db, store)
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
        original = svc.persist_verified

        def changed(*args, **kwargs):
            verified = original(*args, **kwargs)
            row.role = 'changed-after-verification'
            return verified

        monkeypatch.setattr(svc, 'persist_verified', changed)
        with pytest.raises(svc.LabError, match='metadata changed'):
            attach(db, store, body, preview)
        assert db.scalar(select(func.count()).select_from(LabEvent)) == 0


def test_manifest_with_duplicate_json_keys_is_rejected(store):
    with store.factory.begin() as db:
        body, manifest, _ = uploaded(db, store)
        content = (store.storage.root / manifest.storage_key).read_bytes()
        content = content.replace(b'"classification":"operator_host_evidence"',
                                  b'"classification":"operator_host_evidence","classification":"operator_host_evidence"')
        path = store.root / 'duplicate-manifest.json'
        path.write_bytes(content)
        duplicate = svc.store_artifact(db, store.project, path, path.name, 'application/json')
        body['manifest'] = artifact_ref(duplicate)
        with pytest.raises(svc.LabError, match='duplicate'):
            recovery().preview_recovered_evidence(db, store.project, store.run, body)


def test_manifest_source_artifact_requires_uploaded_input_attribution(store):
    with store.factory.begin() as db:
        body, manifest, _ = uploaded(db, store)
        manifest.attempt_id = store.attempt
        with pytest.raises(svc.LabError, match='manifest attribution'):
            recovery().preview_recovered_evidence(db, store.project, store.run, body)


@pytest.fixture
def postgres_store(store):
    url = os.environ.get('TINYSTUDIO_RECOVERY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('set an explicitly owned PostgreSQL admin URL for concurrent recovery verification')
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    name = 'tinystudio_recovery_' + uuid.uuid4().hex[:16]
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(admin.url.set(database=name), connect_args={
        'options': '-c lock_timeout=5000 -c statement_timeout=15000'})
    try:
        from tinystudio.db.migrate import upgrade
        upgrade('head', engine.url.render_as_string(hide_password=False))
        factory = sessionmaker(engine, expire_on_commit=False)
        with store.factory() as original, factory.begin() as db:
            for model in (User, Project, LabTarget, LabDefinition, LabRun, LabAttempt):
                for row in original.scalars(select(model)):
                    db.add(model(**{column.name: getattr(row, column.name) for column in model.__table__.columns}))
                db.flush()
        yield SimpleNamespace(**{**vars(store), 'factory': factory})
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


def test_postgres_concurrent_same_key_publishes_one_receipt_under_run_lock(postgres_store, monkeypatch):
    store = postgres_store
    with store.factory.begin() as db:
        body, _, _ = uploaded(db, store)
        preview = recovery().preview_recovered_evidence(db, store.project, store.run, body)
    barrier = Barrier(2)
    persist = svc.persist_verified

    def synchronize(*args, **kwargs):
        verified = persist(*args, **kwargs)
        barrier.wait(timeout=10)
        return verified

    monkeypatch.setattr(svc, 'persist_verified', synchronize)

    def apply():
        with store.factory.begin() as db:
            return attach(db, store, body, preview)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(apply) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert results[0]['receipt'] == results[1]['receipt']
    assert sorted(result['replayed'] for result in results) == [False, True]
    with store.factory() as db:
        assert db.scalar(select(func.count()).select_from(LabEvent)) == 1
        assert db.scalar(select(func.count()).select_from(LabArtifact)) == 3
        run, attempt = db.get(LabRun, store.run), db.get(LabAttempt, store.attempt)
        assert run.next_seq == 1 and run.state == attempt.state == 'running'
