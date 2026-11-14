"""Actual output admission preserves project-first locking and raw auth identity."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text

from tests.test_lab_transfer_service import database, fixture, intent
from tinystudio.core.project import ProjectError
from tinystudio.db.models import Project
from tinystudio.lab import service as svc, transfer_api, transfer_service as transfers
from tinystudio.lab.api import runner_routes
from tinystudio.lab.models import LabTransfer
from tinystudio.services import projects


@pytest.fixture
def output_api(fixture, monkeypatch):
    from fastapi import FastAPI, Request
    from tinystudio.lab.operation_runtime import OperationRuntimeConfiguration, register_operation_runtime
    fixture['store'].storage = SimpleNamespace(remote=True)
    monkeypatch.setattr(transfer_api, '_store', lambda: fixture['store'])
    endpoint = next(route.endpoint for route in runner_routes.routes if route.name == 'start_output')
    app = FastAPI()
    register_operation_runtime(app, OperationRuntimeConfiguration(mode='external'))
    request = Request({'type': 'http', 'app': app})
    return lambda id_, body, db, target, lease: endpoint(id_=id_, body=body, request=request,
                                                       db=db, target=target, x_lab_lease=lease)


def test_output_api_admission_does_not_deadlock_with_active_project_deletion(fixture, output_api, monkeypatch):
    factory, project = fixture['factory'], fixture['project']
    engine = factory.kw['bind']
    output_at_admit, project_locked, delete_at_run = Event(), Event(), Event()
    cleanup = []
    from tinystudio import storage, vectors

    def forbidden(*args, **kwargs):
        cleanup.append(True)
        pytest.fail('active project deletion must not reach cloud or filesystem cleanup')

    monkeypatch.setattr(storage, 'storage', forbidden)
    monkeypatch.setattr(vectors, 'drop_collection', forbidden)
    monkeypatch.setattr(projects, 'config', forbidden)
    original = transfers.admit

    def pause_at_admit(*args, **kwargs):
        output_at_admit.set()
        assert delete_at_run.wait(5), 'deletion did not reach its run lock'
        return original(*args, **kwargs)

    monkeypatch.setattr(transfers, 'admit', pause_at_admit)

    def after(connection, cursor, statement, parameters, context, many):
        if (connection.info.get('admission_order_actor') == 'delete'
                and 'FROM projects' in statement and 'FOR UPDATE' in statement):
            project_locked.set()

    def before(connection, cursor, statement, parameters, context, many):
        if (connection.info.get('admission_order_actor') == 'delete'
                and 'FROM lab_runs' in statement and 'FOR UPDATE' in statement):
            assert project_locked.is_set()
            delete_at_run.set()

    def output():
        try:
            with factory() as db:
                # Allow PostgreSQL's deadlock detector to report an actual cycle;
                # a short lock timeout would mask the pre-fix 40P01 failure.
                db.execute(text("SET LOCAL lock_timeout = '0'"))
                target = svc.authenticate_target(db, fixture['target'], 'target-token')
                return output_api(fixture['attempt'], transfer_api.TransferRequest(
                    **intent(), idempotency_key='ordered-output'), db, target, 'attempt-token')
        except Exception as error:
            return error

    def deletion():
        assert output_at_admit.wait(5), 'output did not reach its admission boundary'
        try:
            with factory.begin() as db:
                db.execute(text("SET LOCAL lock_timeout = '0'"))
                connection = db.connection()
                connection.info['admission_order_actor'] = 'delete'
                try:
                    projects.delete_project(db, db.get(Project, project))
                finally:
                    connection.info.pop('admission_order_actor', None)
        except Exception as error:
            return error
        return None

    event.listen(engine, 'after_cursor_execute', after)
    event.listen(engine, 'before_cursor_execute', before)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            admission = executor.submit(output)
            removal = executor.submit(deletion)
            admitted, refused = admission.result(timeout=12), removal.result(timeout=12)
    finally:
        event.remove(engine, 'after_cursor_execute', after)
        event.remove(engine, 'before_cursor_execute', before)

    assert not isinstance(admitted, Exception), repr(admitted)
    assert admitted['state'] == 'uploading'
    assert isinstance(refused, ProjectError), repr(refused)
    assert 'active laboratory run' in str(refused)
    assert project_locked.is_set() and delete_at_run.is_set() and not cleanup
    with factory() as reader:
        assert reader.get(Project, project) is not None
        rows = list(reader.scalars(select(LabTransfer).where(LabTransfer.project_id == project)))
        assert len(rows) == 1 and rows[0].state == 'uploading'
    assert len([call for call in fixture['store'].calls if call[0] == 'begin']) == 1


def test_output_api_freezes_authenticated_identity_before_releasing_authority_locks(fixture, output_api, monkeypatch):
    factory, project = fixture['factory'], fixture['project']
    captured, rotated = [], []
    original = transfers.admit

    def observe_identity(*args, **kwargs):
        captured.append(deepcopy(kwargs['attempt_authority']))
        return original(*args, **kwargs)

    monkeypatch.setattr(transfers, 'admit', observe_identity)
    with factory() as db:
        # This reproduces a refreshing ORM session: deriving after commit could
        # otherwise turn a newly read target hash into authority for an old token.
        db.expire_on_commit = True
        target = svc.authenticate_target(db, fixture['target'], 'target-token')

        def rotate_after_commit(session):
            if not rotated:
                rotated.append(True)
                with factory.begin() as writer:
                    from tinystudio.lab.models import LabTarget
                    writer.get(LabTarget, fixture['target']).token_hash = svc.secret_hash('rotated-target-token')

        event.listen(db, 'after_commit', rotate_after_commit)
        try:
            with pytest.raises(svc.LabError) as refused:
                output_api(fixture['attempt'], transfer_api.TransferRequest(
                    **intent(), idempotency_key='rotation-output'), db, target, 'attempt-token')
            assert refused.value.status == 409
        finally:
            event.remove(db, 'after_commit', rotate_after_commit)

    assert rotated == [True] and len(captured) == 1
    assert captured[0]['target_token_hash'] == svc.secret_hash('target-token')
    assert captured[0]['token_hash'] == svc.secret_hash('attempt-token')
    with factory() as reader:
        assert list(reader.scalars(select(LabTransfer).where(LabTransfer.project_id == project))) == []
    assert fixture['store'].calls == []
