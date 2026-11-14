"""Publication deadlines remain fresh after real PostgreSQL row-lock waits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, current_thread
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text

from tests.test_lab_transfer_project_cleanup import assert_removed, cleanup, end_attempt, remove_project
from tests.test_lab_transfer_service import create, database, fixture, seal
from tinystudio.lab import operations as ops, service as svc, transfer_service as transfers
from tinystudio.lab.contracts import checksum
from tinystudio.lab.models import LabArtifact, LabAttempt, LabOperation, LabTarget, LabTransfer


@pytest.fixture
def clock(monkeypatch):
    # Time advances deterministically while a real PostgreSQL lock is held.
    value = [svc.now()]
    monkeypatch.setattr(svc, 'now', lambda: value[0])
    return value


def wait_for_database_lock(factory, pid):
    deadline = monotonic() + 2
    with factory() as db:
        while monotonic() < deadline:
            waiting = db.execute(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid'), {'pid': pid}).scalar()
            db.rollback()
            if waiting == 'Lock':
                return
            Event().wait(.01)
    pytest.fail('owned worker did not block on the PostgreSQL row lock')


@contextmanager
def blocked_verification(fixture, *, output):
    identifier = create(fixture, output=output)
    operation = seal(fixture, identifier)
    factory, engine = fixture['factory'], fixture['factory'].kw['bind']
    streaming, proceed, final_lock = Event(), Event(), Event()
    observed = {}

    def pause_stream():
        streaming.set()
        assert proceed.wait(5), 'owned test did not release streaming verification'

    def observe(connection, cursor, statement, parameters, context, many):
        if 'FROM lab_operations' in statement and 'FOR UPDATE' in statement and current_thread().name.startswith('finish-fence'):
            observed['pid'] = connection.connection.driver_connection.info.backend_pid
            final_lock.set()

    fixture['store'].before_verify = pause_stream
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='finish-fence') as executor:
            worker = executor.submit(ops.run_operation, operation, factory)
            try:
                assert streaming.wait(5)
                with factory() as locker:
                    row = locker.scalar(select(LabOperation).where(LabOperation.id == operation).with_for_update())

                    def reach_final_lock():
                        proceed.set()
                        assert final_lock.wait(5)
                        wait_for_database_lock(factory, observed['pid'])
                        assert not worker.done()

                    yield identifier, operation, locker, row, reach_final_lock, worker
                    locker.commit()
            finally:
                proceed.set()
            # Context exit joins only after the lock-owning session has closed.
            observed['result'] = worker.result(timeout=5)
    finally:
        event.remove(engine, 'before_cursor_execute', observe)


def assert_no_publication(fixture, identifier, operation):
    with fixture['factory']() as db:
        row, job = db.get(LabTransfer, identifier), db.get(LabOperation, operation)
        assert row.state == 'verifying' and row.artifact_id is None and row.artifact_receipt is None
        assert job.state != 'succeeded' and job.result is None
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(LabArtifact.project_id == fixture['project'])) == 0
        assert row.storage_key in fixture['store'].objects


@pytest.mark.parametrize(('deadline', 'output'), [('attempt', True), ('operation', True), ('operation', False)])
def test_publication_rechecks_deadlines_after_waiting_for_final_operation_lock(fixture, clock, deadline, output):
    with blocked_verification(fixture, output=output) as (identifier, operation, locker, row, reach, worker):
        cutoff = clock[0] + timedelta(seconds=1)
        if deadline == 'attempt':
            with fixture['factory'].begin() as db:
                db.get(LabAttempt, fixture['attempt']).lease_until = cutoff
        else:
            row.lease_until = cutoff
            locker.flush()
        reach()
        clock[0] = cutoff + timedelta(seconds=1)
        locker.commit()
        assert worker.result(timeout=5) is None
    assert_no_publication(fixture, identifier, operation)
    with fixture['factory'].begin() as db:
        if deadline == 'attempt':
            assert db.get(LabOperation, operation).state == 'failed'
        else:
            # An expired generation cannot write even its own failure; recovery
            # records lost authority and explicit retry remains the only restart.
            ops.recover_expired(db)
            assert db.get(LabOperation, operation).state == 'lost'


@pytest.mark.parametrize('output', [False, True])
def test_fresh_input_and_output_publication_survive_a_lock_wait(fixture, clock, output):
    with blocked_verification(fixture, output=output) as (identifier, operation, locker, row, reach, worker):
        if not output:
            with fixture['factory'].begin() as db:
                db.get(LabAttempt, fixture['attempt']).lease_until = clock[0] + timedelta(seconds=1)
        reach()
        clock[0] += timedelta(seconds=2)
        locker.commit()
        assert worker.result(timeout=5)['state'] == 'succeeded'
    with fixture['factory']() as db:
        row = db.get(LabTransfer, identifier)
        assert row.state == 'verified'
        artifact = db.get(LabArtifact, row.artifact_id)
        assert artifact.attempt_id == (fixture['attempt'] if output else None)


@pytest.mark.parametrize('change', ['cancelled', 'new_generation'])
def test_final_row_lock_rejects_cancellation_and_a_replacement_generation(fixture, clock, change):
    with blocked_verification(fixture, output=True) as (identifier, operation, locker, row, reach, worker):
        reach()
        if change == 'cancelled':
            ops.cancel_operation(locker, fixture['project'], operation)
        else:
            row.generation += 1
            row.claim_token = 'replacement-generation'
        locker.commit()
        assert worker.result(timeout=5) is None
    assert_no_publication(fixture, identifier, operation)
    with fixture['factory']() as db:
        row = db.get(LabOperation, operation)
        if change == 'cancelled':
            assert row.state == 'cancelled'
        else:
            assert row.state == 'running' and row.generation == 2
            assert row.claim_token == 'replacement-generation'


@pytest.mark.parametrize('kind', ['export', 'import', 'data_bridge'])
def test_every_operation_kind_rechecks_its_lease_after_the_row_lock(fixture, clock, kind):
    factory, engine = fixture['factory'], fixture['factory'].kw['bind']
    cutoff = clock[0] + timedelta(seconds=1)
    with factory.begin() as db:
        row = LabOperation(project_id=fixture['project'], kind=kind, idempotency_key=uuid4().hex,
            payload={}, payload_digest=checksum({}), state='running', claim_token='owned', generation=1, lease_until=cutoff)
        db.add(row); db.flush()
        identifier = row.id
    lease = ops._Lease(factory, identifier, 'owned', 1)
    reached, observed = Event(), {}

    def observe(connection, cursor, statement, parameters, context, many):
        if 'FROM lab_operations' in statement and 'FOR UPDATE' in statement and current_thread().name.startswith('generic-fence'):
            observed['pid'] = connection.connection.driver_connection.info.backend_pid
            reached.set()

    def finish():
        with factory.begin() as db:
            try:
                lease.finish(db, {'owned': True})
            except svc.LabError as error:
                return error.status
        return 'published'

    event.listen(engine, 'before_cursor_execute', observe)
    try:
        with factory() as locker, ThreadPoolExecutor(max_workers=1, thread_name_prefix='generic-fence') as executor:
            locker.scalar(select(LabOperation).where(LabOperation.id == identifier).with_for_update())
            worker = executor.submit(finish)
            try:
                assert reached.wait(3)
                wait_for_database_lock(factory, observed['pid'])
                clock[0] = cutoff + timedelta(seconds=1)
                locker.commit()
            finally:
                locker.rollback()
            assert worker.result(timeout=5) == 409
    finally:
        event.remove(engine, 'before_cursor_execute', observe)


def test_blocked_heartbeat_cannot_renew_an_already_expired_generation(fixture, clock, monkeypatch):
    identifier = create(fixture)
    operation = seal(fixture, identifier)
    factory, engine = fixture['factory'], fixture['factory'].kw['bind']
    cutoff = clock[0] + timedelta(seconds=1)
    with factory.begin() as db:
        row = db.get(LabOperation, operation)
        row.state, row.claim_token, row.generation, row.lease_until = 'running', 'owned', 1, cutoff
    lease = ops._Lease(factory, operation, 'owned', 1)
    monkeypatch.setattr(ops, 'HEARTBEAT_SECONDS', .001)
    reached, observed = Event(), {}

    def is_renewal(statement):
        return statement.startswith('UPDATE lab_operations') or ('FROM lab_operations' in statement and 'FOR UPDATE' in statement)

    def observe(connection, cursor, statement, parameters, context, many):
        if current_thread() is lease.thread and is_renewal(statement):
            observed['pid'] = connection.connection.driver_connection.info.backend_pid
            reached.set()

    def stop_after_wait(connection, cursor, statement, parameters, context, many):
        if current_thread() is lease.thread and is_renewal(statement):
            lease.stop.set()

    event.listen(engine, 'before_cursor_execute', observe)
    event.listen(engine, 'after_cursor_execute', stop_after_wait)
    try:
        with factory() as locker:
            locker.scalar(select(LabOperation).where(LabOperation.id == operation).with_for_update())
            lease.thread.start()
            try:
                assert reached.wait(3)
                wait_for_database_lock(factory, observed['pid'])
                clock[0] = cutoff + timedelta(seconds=1)
            finally:
                locker.commit()
        lease.thread.join(timeout=5)
        assert not lease.thread.is_alive()
    finally:
        lease.stop.set()
        lease.thread.join(timeout=5)
        event.remove(engine, 'before_cursor_execute', observe)
        event.remove(engine, 'after_cursor_execute', stop_after_wait)
    with factory.begin() as db:
        row = db.get(LabOperation, operation)
        assert row.lease_until == cutoff
        ops.recover_expired(db)
        assert row.state == 'lost'


@pytest.mark.parametrize('ended', ['expired', 'target_disabled', 'attempt_lost'])
def test_project_authorized_abort_settles_unpublished_outputs_after_attempt_authority_ends(fixture, cleanup, ended):
    identifier = create(fixture, output=True)
    operation = seal(fixture, identifier)
    with fixture['factory'].begin() as db:
        if ended == 'expired':
            db.get(LabAttempt, fixture['attempt']).lease_until = svc.now() - timedelta(seconds=1)
        elif ended == 'target_disabled':
            db.get(LabTarget, fixture['target']).enabled = False
        else:
            db.get(LabAttempt, fixture['attempt']).state = 'lost'
    with fixture['factory']() as db:
        with pytest.raises(svc.LabError, match='attempt authority'):
            transfers.abort(db, fixture['project'], identifier, fixture['store'])
        db.rollback()
        with pytest.raises(svc.LabError, match='transfer not found'):
            transfers.abort(db, fixture['other'], identifier, fixture['store'], project_authority=True)
        db.rollback()
        row = transfers.abort(db, fixture['project'], identifier, fixture['store'], project_authority=True)
        assert row.state == 'aborted' and row.artifact_id is None
        assert db.get(LabOperation, operation).state == 'cancelled'
        assert row.storage_key in fixture['store'].objects
    assert ops.run_operation(operation, fixture['factory']) is None
    end_attempt(fixture)
    remove_project(fixture)
    assert_removed(fixture, cleanup)


def test_project_authorized_abort_remains_read_only_for_verified_outputs(fixture):
    identifier = create(fixture, output=True)
    operation = seal(fixture, identifier)
    assert ops.run_operation(operation, fixture['factory'])['state'] == 'succeeded'
    with fixture['factory'].begin() as db:
        db.get(LabAttempt, fixture['attempt']).lease_until = svc.now() - timedelta(seconds=1)
    before = list(fixture['store'].calls)
    with fixture['factory']() as db:
        row = transfers.abort(db, fixture['project'], identifier, fixture['store'], project_authority=True)
        assert row.state == 'verified' and row.artifact_id is not None
    assert fixture['store'].calls == before


def test_project_authorized_abort_fences_an_inflight_expired_output_verifier(fixture, cleanup):
    identifier = create(fixture, output=True)
    operation = seal(fixture, identifier)
    streaming, proceed = Event(), Event()

    def pause_stream():
        streaming.set()
        assert proceed.wait(5)

    fixture['store'].before_verify = pause_stream
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(ops.run_operation, operation, fixture['factory'])
        try:
            assert streaming.wait(3)
            with fixture['factory'].begin() as db:
                db.get(LabAttempt, fixture['attempt']).lease_until = svc.now() - timedelta(seconds=1)
            with fixture['factory']() as db:
                row = transfers.abort(db, fixture['project'], identifier, fixture['store'], project_authority=True)
                assert row.state == 'aborted' and row.artifact_id is None
                assert db.get(LabOperation, operation).state == 'cancelled'
            end_attempt(fixture)
            remove_project(fixture)
        finally:
            proceed.set()
        assert worker.result(timeout=5) is None
    assert_removed(fixture, cleanup)


def test_abort_and_project_delete_follow_the_same_project_first_lock_order(fixture, cleanup):
    identifier = create(fixture)
    operation = seal(fixture, identifier)
    end_attempt(fixture)
    with fixture['factory'].begin() as db:
        db.get(LabOperation, operation).state = 'failed'
    factory, engine = fixture['factory'], fixture['factory'].kw['bind']
    before_cancel, proceed, deleting = Event(), Event(), Event()
    observed = {}

    def observe(connection, cursor, statement, parameters, context, many):
        if current_thread().name.startswith('ordered-abort') and 'FROM lab_operations' in statement and 'FOR UPDATE' in statement:
            before_cancel.set()
            assert proceed.wait(5), 'owned abort was not released'
        if current_thread().name.startswith('ordered-delete') and 'FROM projects' in statement and 'FOR UPDATE' in statement:
            observed['pid'] = connection.connection.driver_connection.info.backend_pid
            deleting.set()

    def abort():
        with factory() as db:
            return transfers.abort(db, fixture['project'], identifier, fixture['store']).state

    def delete():
        from tinystudio.core.project import ProjectError
        try:
            remove_project(fixture)
        except ProjectError as error:
            return str(error)
        return 'deleted'

    event.listen(engine, 'before_cursor_execute', observe)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='ordered-abort') as aborter, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix='ordered-delete') as deleter:
            aborting = aborter.submit(abort)
            try:
                assert before_cancel.wait(3)
                deletion = deleter.submit(delete)
                assert deleting.wait(3)
                wait_for_database_lock(factory, observed['pid'])
            finally:
                proceed.set()
            assert aborting.result(timeout=5) == 'aborted'
            outcome = deletion.result(timeout=5)
            assert outcome == 'deleted' or 'active laboratory transfer' in outcome
    finally:
        proceed.set()
        event.remove(engine, 'before_cursor_execute', observe)
