"""Transfer durability uses a disposable owned PostgreSQL DB and fake R2 objects."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import os
import uuid
from threading import Barrier, Event

import pytest
from sqlalchemy import create_engine, event, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tinystudio.config import config
from tinystudio.db.models import Project
from tinystudio.lab import operations as ops, service as svc, transfer_service as transfers
from tinystudio.lab.contracts import checksum
from tinystudio.lab.models import LabArtifact, LabAttempt, LabDefinition, LabOperation, LabRun, LabTarget, LabTransfer
from tinystudio.lab.transfer_configuration import TransferBudget, TransferPolicy


@pytest.fixture(scope='module')
def database():
    admin = create_engine(os.environ.get('TINYSTUDIO_TRANSFER_TEST_DATABASE_URL') or config().database_url,
                          isolation_level='AUTOCOMMIT')
    name = 'tinystudio_transfer_test_' + uuid.uuid4().hex[:12]
    with admin.connect() as connection:
        connection.execute(text('CREATE DATABASE "' + name + '"'))
    engine = create_engine(admin.url.set(database=name), connect_args={'options': '-c lock_timeout=3000 -c statement_timeout=15000'})
    try:
        from tinystudio.db.migrate import upgrade
        upgrade('head', engine.url.render_as_string(hide_password=False))
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '"'))
        admin.dispose()


class Store:
    def __init__(self):
        self.uploads, self.objects = {}, {}
        self.calls = []
        self.begin_lost = self.complete_lost = False
        self.before_verify = lambda: None
        self.before_begin = lambda: None

    def begin(self, key, media):
        self.before_begin()
        self.calls.append(('begin', key))
        identifier = uuid.uuid4().hex
        self.uploads[identifier] = {'key': key, 'parts': {}}
        if self.begin_lost:
            self.begin_lost = False
            raise OSError('private-presigned-secret-must-not-be-retained')
        return identifier

    def inspect_uploads(self, key):
        return [identifier for identifier, value in self.uploads.items() if value['key'] == key]

    def parts(self, key, identifier):
        return [{'number': number, 'size': len(data), 'etag': hashlib.md5(data).hexdigest()}
                for number, data in sorted(self.uploads[identifier]['parts'].items())]

    def sign_part(self, key, identifier, number, size, expiry=None):
        self.calls.append(('sign', key, identifier, number, size, expiry))
        return {'url': 'https://r2.fixture.invalid/?private-presigned-secret', 'headers': {}}

    def complete(self, key, identifier, parts):
        self.calls.append(('complete', key))
        self.objects[key] = b''.join(self.uploads[identifier]['parts'][item['number']] for item in parts)
        del self.uploads[identifier]
        if self.complete_lost:
            self.complete_lost = False
            raise OSError('private-completion-response-lost')

    def abort(self, key, identifier):
        self.calls.append(('abort', key))
        self.uploads.pop(identifier, None)

    def head(self, key):
        return {'size': len(self.objects[key])} if key in self.objects else None

    def put_empty(self, key, media):
        self.calls.append(('empty', key))
        self.objects[key] = b''

    def verify(self, key, size, sha, *, check_authority, deadline=None):
        check_authority(); self.before_verify(); check_authority()
        body = self.objects[key]
        if len(body) != size or hashlib.sha256(body).hexdigest() != sha:
            raise ValueError('stored content mismatch')
        return {'storage_key': key, 'size': size, 'sha256': sha}


@pytest.fixture
def fixture(database, monkeypatch):
    store = Store()
    with database.begin() as db:
        project, other = Project(slug=uuid.uuid4().hex, name='Transfer'), Project(slug=uuid.uuid4().hex, name='Other')
        db.add_all([project, other]); db.flush()
        definition = LabDefinition(project_id=project.id, name='Output fixture')
        target = LabTarget(project_id=project.id, name='owned', token_hash=svc.secret_hash('target-token'), enabled=True)
        db.add_all([definition, target]); db.flush()
        spec = {'definition': {'execution': {'max_output_bytes': 128}}, 'identity': 'frozen'}
        run = LabRun(project_id=project.id, definition_id=definition.id, revision=1, target_id=target.id,
                     idempotency_key='owned-run', spec=spec, spec_digest=checksum(spec), state='running')
        db.add(run); db.flush()
        attempt = LabAttempt(run_id=run.id, target_id=target.id, number=1, token_hash=svc.secret_hash('attempt-token'),
                             lease_until=svc.now() + timedelta(minutes=10), state='running', runtime={})
        db.add(attempt); db.flush()
        result = {'factory': database, 'project': project.id, 'other': other.id, 'target': target.id,
                  'run': run.id, 'attempt': attempt.id, 'authority': transfers.attempt_identity(run, attempt, target), 'store': store}
    monkeypatch.setattr(transfers, '_make_store', lambda policy: store)
    return result


def intent(body=b'\x00\xff\n', **changes):
    return {'name': 'weights.safetensors', 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest(), **changes}


def create(fixture, *, body=b'\x00\xff\n', output=False, policy=None, key=None, metadata=None):
    with fixture['factory']() as db:
        row = transfers.admit(db, fixture['project'], metadata or intent(body), key or uuid.uuid4().hex,
            attempt_authority=fixture['authority'] if output else None, policy=policy)
        db.commit()
        transfers.initialize(db, fixture['project'], row.id, fixture['store']); db.commit()
        return row.id


def seal(fixture, identifier, body=b'\x00\xff\n'):
    with fixture['factory']() as db:
        row = transfers.get_transfer(db, fixture['project'], identifier)
        if body:
            fixture['store'].uploads[row.upload_id]['parts'][1] = body
        row = transfers.complete(db, fixture['project'], identifier, fixture['store']); db.commit()
        return row.operation_id


def test_intent_identity_is_committed_before_provider_io_and_scope_is_exact(fixture):
    factory, project, store = fixture['factory'], fixture['project'], fixture['store']
    with factory() as db:
        row = transfers.admit(db, project, intent(), 'same')
        identifier = row.id
        assert transfers.admit(db, project, intent(), 'same').id == identifier
        with pytest.raises(svc.LabError):
            transfers.admit(db, project, intent(name='different'), 'same')
        db.commit()
        def committed():
            with factory() as reader:
                assert reader.get(LabTransfer, identifier).state == 'initializing'
        store.before_begin = committed
        transfers.initialize(db, project, identifier, store); db.commit()
        assert row.state == 'uploading'
        signed = transfers.sign_part(db, project, identifier, 1, store)
        assert (signed['part_number'], signed['offset'], signed['size']) == (1, 0, 3)
        with pytest.raises(svc.LabError):
            transfers.get_transfer(db, fixture['other'], identifier)
        public = transfers.transfer_dict(row, db)
        assert not any(key in public for key in ('upload_id', 'storage_key', 'attempt_identity'))
        assert 'private-presigned-secret' not in str(public)


def test_begin_ack_loss_reconciles_the_exact_existing_upload_without_a_second_begin(fixture):
    fixture['store'].begin_lost = True
    identifier = create(fixture)
    with fixture['factory']() as db:
        row = transfers.get_transfer(db, fixture['project'], identifier)
        assert row.state == 'initialization_uncertain' and row.error_class == 'OSError'
        transfers.initialize(db, fixture['project'], identifier, fixture['store']); db.commit()
        assert row.state == 'uploading' and row.upload_id
        assert len([call for call in fixture['store'].calls if call[0] == 'begin']) == 1


def test_lost_completion_ack_is_reconciled_and_verified_without_recompleting(fixture):
    identifier = create(fixture)
    fixture['store'].complete_lost = True
    assert seal(fixture, identifier) is None
    with fixture['factory']() as db:
        row = transfers.get_transfer(db, fixture['project'], identifier)
        assert row.state == 'completion_uncertain'
        row = transfers.complete(db, fixture['project'], identifier, fixture['store']); db.commit()
        operation = row.operation_id
        with pytest.raises(svc.LabError):
            transfers.sign_part(db, fixture['project'], identifier, 1, fixture['store'])
    result = ops.run_operation(operation, fixture['factory'])
    assert result['state'] == 'succeeded'
    with fixture['factory']() as db:
        row = transfers.get_transfer(db, fixture['project'], identifier)
        artifact = db.get(LabArtifact, row.artifact_id)
        assert row.state == 'verified' and artifact.storage_key == row.storage_key
        assert fixture['store'].objects[artifact.storage_key] == b'\x00\xff\n'
        assert transfers.transfer_dict(row, db)['artifact']['sha256'] == intent()['sha256']
        transfers.complete(db, fixture['project'], identifier, fixture['store'])
    assert len([call for call in fixture['store'].calls if call[0] == 'complete']) == 1


def test_zero_object_uses_empty_put_and_never_creates_a_multipart_upload(fixture):
    identifier = create(fixture, body=b'')
    operation = seal(fixture, identifier, b'')
    assert ops.run_operation(operation, fixture['factory'])['state'] == 'succeeded'
    assert [call[0] for call in fixture['store'].calls] == ['empty']


def test_abort_cancels_verification_without_deleting_the_sealed_object(fixture):
    identifier = create(fixture)
    operation = seal(fixture, identifier)
    with fixture['factory']() as db:
        row = transfers.abort(db, fixture['project'], identifier, fixture['store']); db.commit()
        assert row.state == 'aborted' and row.storage_key in fixture['store'].objects
        assert db.get(LabOperation, operation).state == 'cancelled'
    assert ops.run_operation(operation, fixture['factory']) is None


def test_aggregate_budget_reserves_active_sessions_and_abort_releases_only_unpublished_reservations(fixture):
    policy = TransferPolicy(aggregate=TransferBudget(max_bytes=3, max_files=1))
    identifier = create(fixture, policy=policy)
    with fixture['factory']() as db:
        with pytest.raises(svc.LabError) as caught:
            transfers.admit(db, fixture['project'], intent(), 'over-budget', policy=policy)
        assert caught.value.status == 413
        transfers.abort(db, fixture['project'], identifier, fixture['store']); db.commit()
        row = transfers.admit(db, fixture['project'], intent(), 'after-abort', policy=policy)
        db.commit()
        assert row.id != identifier


def test_database_prevents_mutation_of_committed_transfer_identity(fixture):
    identifier = create(fixture)
    with pytest.raises(DBAPIError, match='immutable'):
        with fixture['factory'].begin() as db:
            db.execute(update(LabTransfer).where(LabTransfer.id == identifier).values(intent=intent(name='changed')))


def test_missing_initialization_result_never_silently_begins_another_upload(fixture):
    attempts = []
    def refused():
        attempts.append(True)
        raise OSError('begin failed before response')
    fixture['store'].before_begin = refused
    identifier = create(fixture)
    fixture['store'].before_begin = lambda: None
    with fixture['factory']() as db:
        row = transfers.initialize(db, fixture['project'], identifier, fixture['store']); db.commit()
        assert row.state == 'initialization_uncertain' and row.upload_id is None
    assert attempts == [True] and fixture['store'].uploads == {}


def test_missing_object_after_ambiguous_completion_does_not_retry_or_refresh_sealed_etags(fixture):
    identifier = create(fixture)
    attempts = []
    def unavailable(*args):
        attempts.append(True)
        raise OSError('completion unavailable')
    fixture['store'].complete = unavailable
    assert seal(fixture, identifier) is None
    with fixture['factory']() as db:
        row = transfers.get_transfer(db, fixture['project'], identifier)
        retained = deepcopy(row.parts)
        fixture['store'].uploads[row.upload_id]['parts'][1] = b'new'
        transfers.complete(db, fixture['project'], identifier, fixture['store']); db.commit()
        assert row.state == 'completion_uncertain' and row.parts == retained
        assert row.operation_id is None
    assert attempts == [True]


def test_incomplete_or_wrong_size_inventory_cannot_seal_or_complete(fixture):
    identifier = create(fixture)
    with fixture['factory']() as db:
        row = transfers.get_transfer(db, fixture['project'], identifier)
        for parts in ({}, {1: b'x'}):
            fixture['store'].uploads[row.upload_id]['parts'] = parts
            with pytest.raises(svc.LabError):
                transfers.complete(db, fixture['project'], identifier, fixture['store'])
            assert row.state == 'uploading'
    assert not any(call[0] == 'complete' for call in fixture['store'].calls)


def test_verification_failure_retains_operation_and_requires_explicit_retry(fixture):
    identifier = create(fixture)
    operation = seal(fixture, identifier)
    with fixture['factory']() as db:
        key = transfers.get_transfer(db, fixture['project'], identifier).storage_key
    fixture['store'].objects[key] = b'bad'
    assert ops.run_operation(operation, fixture['factory']) is None
    with fixture['factory']() as db:
        row = transfers.complete(db, fixture['project'], identifier, fixture['store']); db.commit()
        public = transfers.transfer_dict(row, db)
        assert public['operation_state'] == 'failed' and public['artifact'] is None
        assert db.get(LabOperation, operation).generation == 1
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == fixture['project'])) == 0
        ops.retry_operation(db, fixture['project'], operation); db.commit()
    fixture['store'].objects[key] = b'\x00\xff\n'
    assert ops.run_operation(operation, fixture['factory'])['state'] == 'succeeded'


@pytest.mark.parametrize('change', ['disabled_target', 'target_token', 'attempt_token', 'run_ended',
    'attempt_ended', 'lease_expired', 'changed_spec'])
def test_slow_verification_holds_no_target_run_locks_and_rechecks_exact_attempt_identity(fixture, change):
    identifier = create(fixture, output=True)
    operation = seal(fixture, identifier)
    def invalidate():
        with fixture['factory'].begin() as db:
            if change == 'disabled_target': db.get(LabTarget, fixture['target']).enabled = False
            elif change == 'target_token': db.get(LabTarget, fixture['target']).token_hash = svc.secret_hash('new-target')
            elif change == 'attempt_token': db.get(LabAttempt, fixture['attempt']).token_hash = svc.secret_hash('new-attempt')
            elif change == 'run_ended': db.get(LabRun, fixture['run']).state = 'failed'
            elif change == 'attempt_ended': db.get(LabAttempt, fixture['attempt']).state = 'lost'
            elif change == 'lease_expired': db.get(LabAttempt, fixture['attempt']).lease_until = svc.now() - timedelta(seconds=1)
            else: db.get(LabRun, fixture['run']).spec = {'corrupted': True}
    fixture['store'].before_verify = invalidate
    assert ops.run_operation(operation, fixture['factory']) is None
    with fixture['factory']() as db:
        assert db.get(LabTransfer, identifier).artifact_id is None
        assert db.get(LabOperation, operation).state == 'failed'


@pytest.mark.parametrize('action', ['cancel_operation', 'abort'])
def test_cancellation_during_streaming_verification_cannot_publish(fixture, action):
    identifier = create(fixture)
    operation = seal(fixture, identifier)
    def cancel():
        with fixture['factory']() as db:
            if action == 'abort':
                transfers.abort(db, fixture['project'], identifier, fixture['store'])
            else:
                ops.cancel_operation(db, fixture['project'], operation)
            db.commit()
    fixture['store'].before_verify = cancel
    assert ops.run_operation(operation, fixture['factory']) is None
    with fixture['factory']() as db:
        assert db.get(LabTransfer, identifier).artifact_id is None
        assert db.get(LabOperation, operation).state == 'cancelled'


def test_output_scope_and_identical_name_reuse_are_explicit_and_do_not_open_provider_upload(fixture):
    with fixture['factory'].begin() as db:
        existing = LabArtifact(project_id=fixture['project'], attempt_id=fixture['attempt'],
            storage_key='retained/existing', **intent(media_type='application/octet-stream', role='evidence', visibility='model'))
        db.add(existing); db.flush()
        artifact_id = existing.id
    identifier = create(fixture, output=True)
    with fixture['factory']() as db:
        row = transfers.get_transfer(db, fixture['project'], identifier)
        assert row.state == 'verified' and row.artifact_id == artifact_id
        transfers.require_scope(row, fixture['authority'])
        with pytest.raises(svc.LabError): transfers.require_scope(row)
        with pytest.raises(svc.LabError):
            transfers.require_scope(row, {**fixture['authority'], 'attempt_id': 'foreign'})
        with pytest.raises(svc.LabError):
            transfers.admit(db, fixture['project'], intent(sha256='a' * 64), 'conflict', attempt_authority=fixture['authority'])
    assert fixture['store'].calls == []


def test_cancel_request_can_retain_audit_bytes_without_changing_execution_outcome(fixture):
    identifier = create(fixture, output=True, metadata=intent(visibility='evaluation'))
    operation = seal(fixture, identifier)
    with fixture['factory'].begin() as db:
        db.get(LabRun, fixture['run']).cancel_requested = True
    assert ops.run_operation(operation, fixture['factory'])['state'] == 'succeeded'
    with fixture['factory']() as db:
        assert db.get(LabRun, fixture['run']).state == 'running'
        assert db.get(LabRun, fixture['run']).cancel_requested is True
        assert db.get(LabArtifact, db.get(LabTransfer, identifier).artifact_id).visibility == 'evaluation'


def test_final_output_budget_is_rechecked_after_other_artifacts_publish(fixture):
    identifier = create(fixture, output=True)
    operation = seal(fixture, identifier)
    def consume_budget():
        with fixture['factory'].begin() as db:
            db.add(LabArtifact(project_id=fixture['project'], attempt_id=fixture['attempt'], name='other.bin',
                size=127, sha256='a' * 64, storage_key='retained/other', media_type='application/octet-stream', role='evidence', visibility='model'))
    fixture['store'].before_verify = consume_budget
    assert ops.run_operation(operation, fixture['factory']) is None
    with fixture['factory']() as db:
        assert db.get(LabTransfer, identifier).artifact_id is None
        assert db.get(LabOperation, operation).error_detail['status'] == 413


def test_database_cannot_refresh_parts_after_a_durable_completion_intent(fixture):
    identifier = create(fixture)
    seal(fixture, identifier)
    with pytest.raises(DBAPIError, match='immutable'):
        with fixture['factory'].begin() as db:
            db.execute(update(LabTransfer).where(LabTransfer.id == identifier).values(parts=[]))


def test_concurrent_initialize_holds_only_the_transfer_control_lock_and_begins_once(fixture):
    with fixture['factory']() as db:
        identifier = transfers.admit(db, fixture['project'], intent(), 'parallel-initialize').id
        db.commit()
    entered, release, second_started = Event(), Event(), Event()
    def before_begin():
        entered.set()
        assert release.wait(3), 'owned test did not release provider control'
    fixture['store'].before_begin = before_begin
    def initialize(second=False):
        with fixture['factory']() as db:
            if second: second_started.set()
            row = transfers.initialize(db, fixture['project'], identifier, fixture['store']); db.commit()
            return row.upload_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(initialize)
        try:
            assert entered.wait(1)
            second = pool.submit(initialize, True)
            assert second_started.wait(1) and not second.done()
        finally:
            release.set()
        assert first.result(timeout=4) == second.result(timeout=4)
    assert len([call for call in fixture['store'].calls if call[0] == 'begin']) == 1


def test_concurrent_admission_reserves_project_budget_atomically(fixture):
    policy = TransferPolicy(aggregate=TransferBudget(max_bytes=3, max_files=1))
    ready = Barrier(2)
    def admit(index):
        ready.wait(timeout=2)
        try:
            with fixture['factory'].begin() as db:
                transfers.admit(db, fixture['project'], intent(), 'parallel-' + str(index), policy=policy)
            return 'accepted'
        except svc.LabError as error:
            assert error.status == 413
            return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(admit, range(2)))
    assert sorted(results) == ['accepted', 'rejected']
    with fixture['factory']() as db:
        assert db.scalar(select(func.count()).select_from(LabTransfer).where(LabTransfer.project_id == fixture['project'])) == 1


def test_concurrent_identical_output_verification_publishes_one_artifact(fixture):
    first, second = create(fixture, output=True), create(fixture, output=True)
    operations = [seal(fixture, first), seal(fixture, second)]
    both = Barrier(2)
    fixture['store'].before_verify = lambda: both.wait(timeout=3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda identifier: ops.run_operation(identifier, fixture['factory']), operations))
    assert all(result['state'] == 'succeeded' for result in results)
    with fixture['factory']() as db:
        assert db.get(LabTransfer, first).artifact_id == db.get(LabTransfer, second).artifact_id
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.attempt_id == fixture['attempt'])) == 1


def test_publication_commit_failure_rolls_back_artifact_transfer_and_success_together(fixture):
    identifier = create(fixture)
    operation = seal(fixture, identifier)
    engine = fixture['factory'].kw['bind']
    fired = []
    def arm(connection, cursor, statement, parameters, context, many):
        if statement.startswith('INSERT INTO lab_artifacts'):
            connection.info['owned_transfer_commit_failure'] = True
    def fail(connection):
        if connection.info.pop('owned_transfer_commit_failure', False):
            fired.append(True)
            raise RuntimeError('owned publication commit failure')
    event.listen(engine, 'after_cursor_execute', arm)
    event.listen(engine, 'commit', fail)
    try:
        assert ops.run_operation(operation, fixture['factory']) is None
    finally:
        event.remove(engine, 'after_cursor_execute', arm)
        event.remove(engine, 'commit', fail)
    assert fired == [True]
    with fixture['factory']() as db:
        assert db.get(LabTransfer, identifier).artifact_id is None
        assert db.get(LabTransfer, identifier).state == 'verifying'
        assert db.get(LabOperation, operation).state == 'failed'
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == fixture['project'])) == 0


def test_lost_ack_after_publication_commit_is_readable_and_cannot_duplicate_publication(fixture):
    identifier = create(fixture)
    operation = seal(fixture, identifier)
    factory = fixture['factory']
    fired = []
    def arm(db, context):
        if any(isinstance(row, LabArtifact) for row in db.new):
            db.info['owned_transfer_ack_loss'] = True
    def lose_ack(db):
        if db.info.pop('owned_transfer_ack_loss', False):
            fired.append(True)
            raise RuntimeError('owned ACK lost after database commit')
    event.listen(factory, 'after_flush', arm)
    event.listen(factory, 'after_commit', lose_ack)
    try:
        assert ops.run_operation(operation, factory) is None
    finally:
        event.remove(factory, 'after_flush', arm)
        event.remove(factory, 'after_commit', lose_ack)
    assert fired == [True]
    with factory() as db:
        row = transfers.get_transfer(db, fixture['project'], identifier)
        original = transfers.transfer_dict(row, db)
        assert original['state'] == 'verified' and original['operation_state'] == 'succeeded'
        assert original['artifact']['id'] == row.artifact_id
        assert transfers.transfer_dict(transfers.complete(db, fixture['project'], identifier, fixture['store']), db) == original
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == fixture['project'])) == 1
    assert ops.run_operation(operation, factory) is None


def test_abort_intent_and_operation_cancel_are_committed_even_when_provider_abort_is_uncertain(fixture):
    identifier = create(fixture)
    operation = seal(fixture, identifier)
    def fail(key, upload_id):
        with fixture['factory']() as reader:
            assert reader.get(LabTransfer, identifier).state == 'aborting'
            assert reader.get(LabOperation, operation).state == 'cancelled'
        raise OSError('private-abort-url-secret')
    fixture['store'].abort = fail
    with fixture['factory']() as db:
        row = transfers.abort(db, fixture['project'], identifier, fixture['store']); db.commit()
        assert row.state == 'abort_uncertain' and row.error_class == 'OSError'
        assert 'private-abort-url-secret' not in str(transfers.transfer_dict(row, db))
    assert ops.run_operation(operation, fixture['factory']) is None


def test_aborting_an_admitted_session_does_not_require_any_provider_io(fixture):
    with fixture['factory']() as db:
        row = transfers.admit(db, fixture['project'], intent(), 'never-initialized'); db.commit()
        called = []
        def unavailable(key):
            called.append(True)
            raise OSError('provider unavailable')
        fixture['store'].inspect_uploads = unavailable
        row = transfers.abort(db, fixture['project'], row.id, fixture['store']); db.commit()
        assert row.state == 'aborted' and not called


@pytest.mark.parametrize('maximum,body,accepted', [(None, b'abc', True), (0, b'', True),
    (0, b'abc', False), (3, b'abc', True)])
def test_output_budget_helper_honors_future_none_and_zero_without_fallback(fixture, maximum, body, accepted):
    with fixture['factory'].begin() as db:
        run, attempt, target = db.get(LabRun, fixture['run']), db.get(LabAttempt, fixture['attempt']), db.get(LabTarget, fixture['target'])
        run.spec = {'definition': {'execution': {'max_output_bytes': maximum}}, 'identity': 'frozen'}
        run.spec_digest = checksum(run.spec)
        fixture['authority'] = transfers.attempt_identity(run, attempt, target)
    if not accepted:
        with pytest.raises(svc.LabError) as caught:
            create(fixture, output=True, body=body)
        assert caught.value.status == 413
    else:
        identifier = create(fixture, output=True, body=body)
        assert ops.run_operation(seal(fixture, identifier, body), fixture['factory'])['state'] == 'succeeded'


def test_verified_abort_is_read_only_and_does_not_release_retained_artifact_budget(fixture):
    policy = TransferPolicy(aggregate=TransferBudget(max_bytes=3, max_files=1))
    identifier = create(fixture, policy=policy)
    assert ops.run_operation(seal(fixture, identifier), fixture['factory'])['state'] == 'succeeded'
    before = list(fixture['store'].calls)
    with fixture['factory']() as db:
        row = transfers.abort(db, fixture['project'], identifier, fixture['store']); db.commit()
        assert row.state == 'verified' and row.artifact_id
        with pytest.raises(svc.LabError) as caught:
            transfers.admit(db, fixture['project'], intent(), 'still-over-budget', policy=policy)
        assert caught.value.status == 413
    assert fixture['store'].calls == before


def test_existing_runner_authentication_cannot_authorize_a_null_hash_local_target(fixture):
    with fixture['factory'].begin() as db:
        target = db.get(LabTarget, fixture['target'])
        target.token_hash = None
    with fixture['factory']() as db:
        with pytest.raises(svc.LabError) as caught:
            svc.authenticate_target(db, fixture['target'], 'target-token')
        assert caught.value.status == 401


@pytest.mark.parametrize('timeout', [-0.0, 1e30])
def test_policy_snapshot_hash_retains_canonical_numeric_spelling_across_postgres(fixture, timeout):
    policy = TransferPolicy(verifier_timeout_seconds=timeout)
    with fixture['factory']() as db:
        row = transfers.admit(db, fixture['project'], intent(), 'numeric-policy', policy=policy)
        identifier, digest = row.id, row.request_digest
        db.commit()
    with fixture['factory']() as db:
        row = transfers.get_transfer(db, fixture['project'], identifier)
        assert row.request_digest == digest
        assert checksum(row.policy) == checksum(policy.as_dict())
