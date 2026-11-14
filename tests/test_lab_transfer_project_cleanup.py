"""Project deletion settles transfer work before removing its scoped evidence."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from tests.test_lab_transfer_service import create, database, fixture, intent, seal
from tinystudio.core.project import ProjectError
from tinystudio.db.models import Project, Task
from tinystudio.lab import operations as ops, service as svc, transfer_service as transfers
from tinystudio.lab.models import LabArtifact, LabAttempt, LabOperation, LabRun, LabTransfer
from tinystudio.services import projects


@pytest.fixture
def cleanup(fixture, tmp_path, monkeypatch):
    from tinystudio import storage, vectors
    calls = []
    monkeypatch.setattr(storage, 'storage', lambda: SimpleNamespace(remote=True, delete_prefix=calls.append))
    monkeypatch.setattr(vectors, 'drop_collection', lambda collection: pytest.fail('unexpected vector cleanup'))
    monkeypatch.setattr(projects, 'config', lambda: SimpleNamespace(project_dir=lambda identity: tmp_path / identity))
    for identity in (fixture['project'], fixture['other']):
        directory = tmp_path / identity
        directory.mkdir()
        (directory / 'evidence').write_bytes(b'owned evidence\x00\xff')
    return {'calls': calls, 'root': tmp_path}


def end_attempt(fixture):
    with fixture['factory'].begin() as db:
        db.get(LabRun, fixture['run']).state = 'failed'
        db.get(LabAttempt, fixture['attempt']).state = 'failed'


def remove_project(fixture):
    with fixture['factory'].begin() as db:
        projects.delete_project(db, db.get(Project, fixture['project']))


def assert_preserved(fixture, cleanup):
    assert cleanup['calls'] == []
    assert (cleanup['root'] / fixture['project'] / 'evidence').read_bytes() == b'owned evidence\x00\xff'
    with fixture['factory']() as db:
        assert db.get(Project, fixture['project']) is not None


def assert_removed(fixture, cleanup):
    with fixture['factory']() as db:
        assert db.get(Project, fixture['project']) is None
        assert db.get(Project, fixture['other']) is not None
        for model in (LabTransfer, LabOperation, LabArtifact, LabRun):
            assert db.scalar(select(func.count()).select_from(model).where(model.project_id == fixture['project'])) == 0
        assert db.get(LabAttempt, fixture['attempt']) is None
    assert not (cleanup['root'] / fixture['project']).exists()
    assert (cleanup['root'] / fixture['other'] / 'evidence').read_bytes() == b'owned evidence\x00\xff'
    # Completed or abandoned immutable objects have only project-scoped cleanup;
    # deleting a project does not infer verification or issue multipart aborts.
    assert set(cleanup['calls']) == {
        f'{category}/{fixture["project"]}' for category in
        ('snapshots', 'models', 'captures', 'retained-captures', 'sources', 'uploads', 'lab')}


@pytest.mark.parametrize('state', [
    'admitted', 'initializing', 'initialization_uncertain', 'uploading', 'sealing',
    'completion_uncertain', 'sealed', 'verifying', 'aborting', 'abort_uncertain',
])
def test_unsettled_transfer_blocks_project_deletion_before_any_cleanup(fixture, cleanup, state):
    with fixture['factory'].begin() as db:
        row = transfers.admit(db, fixture['project'], intent(), uuid4().hex)
        row.state = state
    end_attempt(fixture)
    with pytest.raises(ProjectError, match='laboratory transfer.*settle or abort'):
        remove_project(fixture)
    assert_preserved(fixture, cleanup)
    assert fixture['store'].calls == []


@pytest.mark.parametrize('output', [False, True])
def test_verified_transfer_rows_are_deleted_before_artifact_cascades(fixture, cleanup, output):
    identifier = create(fixture, output=output)
    operation = seal(fixture, identifier)
    result = ops.run_operation(operation, fixture['factory'])
    assert result['state'] == 'succeeded'
    with fixture['factory']() as db:
        row = db.get(LabTransfer, identifier)
        artifact = db.get(LabArtifact, row.artifact_id)
        assert row.state == 'verified' and artifact is not None
        assert artifact.attempt_id == (fixture['attempt'] if output else None)
    end_attempt(fixture)
    provider_calls = list(fixture['store'].calls)
    remove_project(fixture)
    assert_removed(fixture, cleanup)
    assert fixture['store'].calls == provider_calls


@pytest.mark.parametrize('state', ['cancelled', 'lost', 'failed'])
def test_ended_verifier_requires_explicit_abort_but_does_not_block_afterward(fixture, cleanup, state):
    identifier = create(fixture)
    operation = seal(fixture, identifier)
    with fixture['factory'].begin() as db:
        db.get(LabOperation, operation).state = state
    end_attempt(fixture)
    assert ops.run_operation(operation, fixture['factory']) is None
    with pytest.raises(ProjectError, match='laboratory transfer.*settle or abort'):
        remove_project(fixture)
    assert_preserved(fixture, cleanup)
    with fixture['factory']() as db:
        row = transfers.abort(db, fixture['project'], identifier, fixture['store'])
        assert row.state == 'aborted' and row.artifact_id is None
        assert row.storage_key in fixture['store'].objects
    remove_project(fixture)
    assert_removed(fixture, cleanup)


def test_explicit_abort_during_verification_allows_cleanup_without_late_publication(fixture, cleanup):
    identifier = create(fixture)
    operation = seal(fixture, identifier)
    end_attempt(fixture)
    verifying, resume = Event(), Event()

    def wait_for_abort():
        verifying.set()
        assert resume.wait(5), 'owned verifier was not released'

    fixture['store'].before_verify = wait_for_abort
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(ops.run_operation, operation, fixture['factory'])
        try:
            assert verifying.wait(5)
            with pytest.raises(ProjectError, match='active laboratory operation'):
                remove_project(fixture)
            assert_preserved(fixture, cleanup)
            with fixture['factory']() as db:
                row = transfers.abort(db, fixture['project'], identifier, fixture['store'])
                assert row.state == 'aborted' and row.artifact_id is None
                assert db.get(LabOperation, operation).state == 'cancelled'
            remove_project(fixture)
        finally:
            resume.set()
        assert worker.result(timeout=5) is None
    assert_removed(fixture, cleanup)


def test_uninitialized_transfer_can_be_aborted_without_provider_io_then_deleted(fixture, cleanup):
    with fixture['factory']() as db:
        row = transfers.admit(db, fixture['project'], intent(), uuid4().hex)
        db.commit()
        assert transfers.abort(db, fixture['project'], row.id, fixture['store']).state == 'aborted'
    end_attempt(fixture)
    remove_project(fixture)
    assert_removed(fixture, cleanup)
    assert fixture['store'].calls == []


@pytest.mark.parametrize('kind', ['task', 'run', 'attempt', 'operation'])
def test_existing_active_work_still_blocks_before_cleanup(fixture, cleanup, kind):
    end_attempt(fixture)
    with fixture['factory'].begin() as db:
        if kind == 'task':
            db.add(Task(project_id=fixture['project'], kind='mock', state='running'))
        elif kind == 'operation':
            db.add(LabOperation(project_id=fixture['project'], kind='export', idempotency_key='active',
                                payload={}, payload_digest='a' * 64, state='running'))
        else:
            model, identity = (LabRun, fixture['run']) if kind == 'run' else (LabAttempt, fixture['attempt'])
            db.get(model, identity).state = 'running'
    with pytest.raises(ProjectError, match='active .*' + kind):
        remove_project(fixture)
    assert_preserved(fixture, cleanup)


def wait_for_lock(factory, pid):
    deadline = monotonic() + 2
    with factory() as observer:
        while monotonic() < deadline:
            state = observer.execute(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid'), {'pid': pid}).scalar()
            observer.rollback()
            if state == 'Lock':
                return
            Event().wait(.01)
    pytest.fail('owned worker did not reach the PostgreSQL project lock')


def test_project_delete_commit_prevents_waiting_transfer_admission(fixture, cleanup):
    end_attempt(fixture)
    started, identity = Event(), {}

    def admit_after_delete():
        with fixture['factory']() as db:
            identity['pid'] = db.scalar(text('SELECT pg_backend_pid()'))
            started.set()
            try:
                transfers.admit(db, fixture['project'], intent(), uuid4().hex)
                db.commit()
            except svc.LabError as error:
                return error.status, str(error)
        pytest.fail('a transfer was admitted after project deletion')

    with fixture['factory']() as deleting, ThreadPoolExecutor(max_workers=1) as executor:
        projects.delete_project(deleting, deleting.get(Project, fixture['project']))
        worker = executor.submit(admit_after_delete)
        try:
            assert started.wait(2)
            wait_for_lock(fixture['factory'], identity['pid'])
            assert not worker.done()
            deleting.commit()
        finally:
            deleting.rollback()
        assert worker.result(timeout=5) == (404, 'project not found')
    assert_removed(fixture, cleanup)


def test_admission_commit_is_seen_by_waiting_project_deletion(fixture, cleanup):
    end_attempt(fixture)
    started, identity = Event(), {}

    def delete_after_admission():
        with fixture['factory']() as db:
            project = db.get(Project, fixture['project'])
            identity['pid'] = db.scalar(text('SELECT pg_backend_pid()'))
            started.set()
            try:
                projects.delete_project(db, project)
                db.commit()
            except ProjectError as error:
                return str(error)
        pytest.fail('project deletion missed a newly committed transfer')

    with fixture['factory']() as admitting, ThreadPoolExecutor(max_workers=1) as executor:
        row = transfers.admit(admitting, fixture['project'], intent(), uuid4().hex)
        worker = executor.submit(delete_after_admission)
        try:
            assert started.wait(2)
            wait_for_lock(fixture['factory'], identity['pid'])
            assert not worker.done()
            admitting.commit()
        finally:
            admitting.rollback()
        assert 'laboratory transfer' in worker.result(timeout=5)
    assert_preserved(fixture, cleanup)
    with fixture['factory']() as db:
        assert db.get(LabTransfer, row.id).state == 'admitted'
