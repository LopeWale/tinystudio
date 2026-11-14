"""Real PostgreSQL waits and streamed evidence fence asynchronous completion."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, current_thread
from time import monotonic

import pytest
from sqlalchemy import event, func, select, text

from tests.test_lab_finish_preparation import database, finish_case
from tests.test_lab_async_finalization import body
from tinystudio.lab import finalization_service as final, operations as ops, service as svc
from tinystudio.lab.models import LabArtifact, LabAttempt, LabEvent, LabOperation, LabRun, LabTarget


def admit(case,state='succeeded'):
    request=body(case,state)
    with case['factory'].begin() as db:
        row=final.request_finalization(db,db.get(LabTarget,case['target']),case['attempt'],
            'owned-attempt',**request)
        return row.id


def assert_unpublished(case,operation):
    with case['factory']() as db:
        assert db.get(LabAttempt,case['attempt']).state=='running'
        assert db.get(LabOperation,operation).state!='succeeded'
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(
            LabArtifact.attempt_id==case['attempt'],LabArtifact.role=='attempt_outcome'))==0
        assert db.scalar(select(func.count()).select_from(LabEvent).where(
            LabEvent.run_id==case['run'],LabEvent.kind=='server.outcome'))==0


def wait_for_lock(factory,pid):
    deadline=monotonic()+2
    with factory() as db:
        while monotonic()<deadline:
            value=db.execute(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid'),{'pid':pid}).scalar()
            db.rollback()
            if value=='Lock':return
            Event().wait(.01)
    pytest.fail('owned finalization worker did not reach the PostgreSQL row lock')


@contextmanager
def blocked_final_lock(case,monkeypatch):
    identifier=admit(case)
    store=final._make_store();verify=store.verify
    streaming,proceed,reached=Event(),Event(),Event();observed={}
    def pause(*args,**kwargs):
        streaming.set();assert proceed.wait(5),'owned verification barrier timed out'
        return verify(*args,**kwargs)
    monkeypatch.setattr(store,'verify',pause)
    monkeypatch.setattr(final,'_make_store',lambda:store)
    engine=case['factory'].kw['bind']
    def observe(connection,cursor,statement,parameters,context,many):
        if ('FROM lab_operations' in statement and 'FOR UPDATE' in statement
                and current_thread().name.startswith('finalization-fence')):
            observed['pid']=connection.connection.driver_connection.info.backend_pid;reached.set()
    event.listen(engine,'before_cursor_execute',observe)
    try:
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='finalization-fence') as workers:
            worker=workers.submit(ops.run_operation,identifier,case['factory'])
            try:
                assert streaming.wait(5)
                with case['factory']() as locker:
                    row=locker.scalar(select(LabOperation).where(LabOperation.id==identifier).with_for_update())
                    def reach():
                        proceed.set();assert reached.wait(5)
                        wait_for_lock(case['factory'],observed['pid'])
                        assert not worker.done()
                    yield identifier,locker,row,reach,worker
                    locker.commit()
            finally:proceed.set()
            worker.result(timeout=5)
    finally:event.remove(engine,'before_cursor_execute',observe)


@pytest.mark.parametrize('deadline',['attempt','operation'])
def test_final_publication_rechecks_ttl_after_actual_operation_lock_wait(finish_case,monkeypatch,deadline):
    clock=[svc.now()];monkeypatch.setattr(svc,'now',lambda:clock[0])
    with blocked_final_lock(finish_case,monkeypatch) as (identifier,locker,row,reach,worker):
        cutoff=clock[0]+timedelta(seconds=1)
        if deadline=='attempt':
            with finish_case['factory'].begin() as db:db.get(LabAttempt,finish_case['attempt']).lease_until=cutoff
        else:row.lease_until=cutoff;locker.flush()
        reach();clock[0]=cutoff+timedelta(seconds=1);locker.commit()
        assert worker.result(timeout=5) is None
    assert_unpublished(finish_case,identifier)
    with finish_case['factory'].begin() as db:
        if deadline=='operation':
            ops.recover_expired(db)
            assert db.get(LabOperation,identifier).state=='lost'
        else:assert db.get(LabOperation,identifier).state=='failed'


def test_fresh_finalization_survives_actual_operation_lock_wait(finish_case,monkeypatch):
    with blocked_final_lock(finish_case,monkeypatch) as (identifier,locker,row,reach,worker):
        reach();locker.commit()
        assert worker.result(timeout=5)['result']=={'accepted':True,'state':'succeeded'}


def test_actual_wall_clock_attempt_expiry_while_final_operation_lock_is_held(finish_case,monkeypatch):
    with blocked_final_lock(finish_case,monkeypatch) as (identifier,locker,row,reach,worker):
        cutoff=svc.now()+timedelta(seconds=1.5)
        with finish_case['factory'].begin() as db:db.get(LabAttempt,finish_case['attempt']).lease_until=cutoff
        reach()
        Event().wait(max(0,(cutoff-svc.now()).total_seconds())+.03)
        assert svc.now()>cutoff
        locker.commit();assert worker.result(timeout=5) is None
    assert_unpublished(finish_case,identifier)


@pytest.mark.parametrize('change',['cancelled','replacement'])
def test_operation_cancel_or_new_generation_discards_provisional_finish(finish_case,monkeypatch,change):
    with blocked_final_lock(finish_case,monkeypatch) as (identifier,locker,row,reach,worker):
        reach()
        if change=='cancelled':ops.cancel_operation(locker,finish_case['project'],identifier)
        else:row.generation+=1;row.claim_token='replacement'
        locker.commit();assert worker.result(timeout=5) is None
    assert_unpublished(finish_case,identifier)
    with finish_case['factory']() as db:
        row=db.get(LabOperation,identifier)
        assert row.state==('cancelled' if change=='cancelled' else 'running')
        if change=='replacement':assert row.claim_token=='replacement' and row.generation==2


@pytest.mark.parametrize('change',['target_token','attempt_token','disabled','cancel','spec','metadata','storage_key','lease'])
def test_stream_checkpoint_and_publication_reject_changed_authority_or_content(finish_case,monkeypatch,change):
    identifier=admit(finish_case)
    store=final._make_store();verify=store.verify
    def changed(*args,**kwargs):
        with finish_case['factory'].begin() as writer:
            if change=='target_token':writer.get(LabTarget,finish_case['target']).token_hash=svc.secret_hash('new-target')
            elif change=='attempt_token':writer.get(LabAttempt,finish_case['attempt']).token_hash=svc.secret_hash('new-attempt')
            elif change=='disabled':writer.get(LabTarget,finish_case['target']).enabled=False
            elif change=='cancel':writer.get(LabRun,finish_case['run']).cancel_requested=True
            elif change=='spec':writer.get(LabRun,finish_case['run']).spec={'changed':True}
            elif change=='metadata':writer.get(LabArtifact,finish_case['artifact']['id']).name='renamed.bin'
            elif change=='storage_key':writer.get(LabArtifact,finish_case['artifact']['id']).storage_key='lab/owned/changed'
            else:writer.get(LabAttempt,finish_case['attempt']).lease_until=svc.now()-timedelta(seconds=1)
        return verify(*args,**kwargs)
    monkeypatch.setattr(store,'verify',changed);monkeypatch.setattr(final,'_make_store',lambda:store)
    assert ops.run_operation(identifier,finish_case['factory']) is None
    assert_unpublished(finish_case,identifier)


def test_streaming_verification_closes_remote_body_and_never_materializes_evidence(finish_case,monkeypatch,tmp_path):
    from tinystudio.lab.transfer_storage import MultipartArtifactStore
    from tinystudio.storage import ArtifactStorage
    source=tmp_path/'large-owned.bin';source.write_bytes(b'owned'*(1024*1024//5*3)+b'final')
    with finish_case['factory'].begin() as db:
        artifact=svc.store_artifact(db,finish_case['project'],source,'stream.bin','application/octet-stream',
            'evidence',finish_case['attempt'])
        finish_case['artifact']=svc.artifact_dict(artifact);key=artifact.storage_key
    identifier=admit(finish_case)
    class Body:
        def __init__(self):self.file=source.open('rb');self.closed=False;self.reads=[]
        def read(self,size):self.reads.append(size);assert 0<size<=1024**2;return self.file.read(size)
        def close(self):self.closed=True;self.file.close()
    body=Body()
    class Client:
        def get_object(self,**parameters):
            assert parameters=={'Bucket':'owned','Key':key}
            return {'Body':body,'ContentLength':source.stat().st_size}
    store=MultipartArtifactStore(ArtifactStorage(client=Client(),bucket='owned'))
    monkeypatch.setattr(final,'_make_store',lambda:store)
    monkeypatch.setattr(svc,'materialize',lambda *a:pytest.fail('evidence materialized instead of streamed'))
    assert ops.run_operation(identifier,finish_case['factory'])['state']=='succeeded'
    assert body.closed and len(body.reads)>=4


def test_checkpoint_heartbeat_renewal_keeps_finalization_live_beyond_admitted_deadline(finish_case,monkeypatch):
    clock=[svc.now()];monkeypatch.setattr(svc,'now',lambda:clock[0])
    with finish_case['factory'].begin() as db:db.get(LabAttempt,finish_case['attempt']).lease_until=clock[0]+timedelta(seconds=10)
    identifier=admit(finish_case);store=final._make_store();verify=store.verify
    def renew(*args,**kwargs):
        clock[0]+=timedelta(seconds=5)
        with finish_case['factory'].begin() as heartbeat:
            svc.heartbeat(heartbeat,heartbeat.get(LabTarget,finish_case['target']),finish_case['attempt'],'owned-attempt',[])
        clock[0]+=timedelta(seconds=10)
        return verify(*args,**kwargs)
    monkeypatch.setattr(store,'verify',renew);monkeypatch.setattr(final,'_make_store',lambda:store)
    assert ops.run_operation(identifier,finish_case['factory'])['state']=='succeeded'


def test_crashed_verifier_retains_running_generation_without_automatic_replay(finish_case,monkeypatch):
    identifier=admit(finish_case);store=final._make_store();calls=[]
    def crash(*args,**kwargs):calls.append(True);raise KeyboardInterrupt('owned worker crash')
    monkeypatch.setattr(store,'verify',crash);monkeypatch.setattr(final,'_make_store',lambda:store)
    with pytest.raises(KeyboardInterrupt):ops.run_operation(identifier,finish_case['factory'])
    assert_unpublished(finish_case,identifier)
    assert ops.run_operation(identifier,finish_case['factory']) is None and calls==[True]
    with finish_case['factory'].begin() as db:
        db.get(LabOperation,identifier).lease_until=svc.now()-timedelta(seconds=1)
        db.flush();ops.recover_expired(db)
    with finish_case['factory']() as db:assert db.get(LabOperation,identifier).state=='lost'


def test_crash_after_provisional_outcome_rolls_back_all_rows_before_operation_ack(finish_case,monkeypatch):
    identifier=admit(finish_case)
    def crash(lease,db,result):
        assert db.get(LabAttempt,finish_case['attempt']).state=='succeeded'
        assert db.scalar(select(func.count()).select_from(LabArtifact).where(
            LabArtifact.attempt_id==finish_case['attempt'],LabArtifact.role=='attempt_outcome'))==1
        raise KeyboardInterrupt('owned crash after provisional archive association')
    monkeypatch.setattr(ops._Lease,'finish',crash)
    with pytest.raises(KeyboardInterrupt):ops.run_operation(identifier,finish_case['factory'])
    assert_unpublished(finish_case,identifier)
    with finish_case['factory']() as db:
        assert db.get(LabRun,finish_case['run']).state=='running'
        assert db.get(LabOperation,identifier).state=='running'
    assert ops.run_operation(identifier,finish_case['factory']) is None


def test_provider_exception_text_is_not_retained_in_failed_operation(finish_case,monkeypatch):
    identifier=admit(finish_case);store=final._make_store()
    def fail(*args,**kwargs):raise RuntimeError('https://private/?signed-token=owned-secret')
    monkeypatch.setattr(store,'verify',fail);monkeypatch.setattr(final,'_make_store',lambda:store)
    assert ops.run_operation(identifier,finish_case['factory']) is None
    with finish_case['factory']() as db:
        row=db.get(LabOperation,identifier)
        assert row.state=='failed' and 'owned-secret' not in row.error and 'https://' not in row.error


def test_authority_loss_during_remote_read_closes_body_and_retains_unpublished_evidence(finish_case,monkeypatch):
    from tinystudio.lab.transfer_storage import MultipartArtifactStore
    from tinystudio.storage import ArtifactStorage
    identifier=admit(finish_case)
    content=b'owned evidence'
    class Body:
        closed=False
        def read(self,size):
            with finish_case['factory'].begin() as db:ops.cancel_operation(db,finish_case['project'],identifier)
            return content
        def close(self):self.closed=True
    stream=Body()
    class Client:
        def get_object(self,**parameters):return {'Body':stream,'ContentLength':len(content)}
    store=MultipartArtifactStore(ArtifactStorage(client=Client(),bucket='owned'))
    monkeypatch.setattr(final,'_make_store',lambda:store)
    assert ops.run_operation(identifier,finish_case['factory']) is None
    assert stream.closed
    assert_unpublished(finish_case,identifier)
    with finish_case['factory']() as db:assert db.get(LabOperation,identifier).state=='cancelled'


@pytest.mark.parametrize('change',['result_projection','error','attempt_digest','association','archive_type','ack_type'])
def test_final_kind_fence_rejects_a_changed_provisional_publication(finish_case,monkeypatch,change):
    identifier=admit(finish_case);publish=final._publish_prepared_finish
    def changed(db,prepared,**options):
        result=publish(db,prepared,**options)
        if change=='result_projection':db.get(LabRun,finish_case['run']).result={'changed':True}
        elif change=='error':db.get(LabRun,finish_case['run']).error='changed diagnostic'
        elif change=='attempt_digest':db.get(LabAttempt,finish_case['attempt']).result_digest='0'*64
        elif change=='association':
            row=db.scalar(select(LabEvent).where(LabEvent.run_id==finish_case['run'],LabEvent.kind=='server.outcome'))
            row.payload={**row.payload,'outcome_artifact_id':finish_case['artifact']['id']}
        elif change=='archive_type':
            row=db.scalar(select(LabArtifact).where(LabArtifact.attempt_id==finish_case['attempt'],LabArtifact.role=='attempt_outcome'))
            row.media_type='application/changed'
        else:return {'accepted':1,'state':'succeeded'}
        return result
    monkeypatch.setattr(final,'_publish_prepared_finish',changed)
    assert ops.run_operation(identifier,finish_case['factory']) is None
    assert_unpublished(finish_case,identifier)


def test_legacy_authority_rechecks_token_after_actual_run_lock_wait(finish_case):
    reached=Event();observed={};engine=finish_case['factory'].kw['bind']
    def observe(connection,cursor,statement,parameters,context,many):
        if ('FROM lab_runs' in statement and 'FOR UPDATE' in statement
                and current_thread().name.startswith('legacy-authority')):
            observed['pid']=connection.connection.driver_connection.info.backend_pid;reached.set()
    def authority():
        with finish_case['factory'].begin() as db:
            with pytest.raises(svc.LabError,match='invalid attempt authority'):
                svc.attempt_authority(db,db.get(LabTarget,finish_case['target']),finish_case['attempt'],'owned-attempt')
    event.listen(engine,'before_cursor_execute',observe)
    try:
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='legacy-authority') as workers:
            with finish_case['factory']() as locker:
                locker.scalar(select(LabRun).where(LabRun.id==finish_case['run']).with_for_update())
                locker.get(LabAttempt,finish_case['attempt']).token_hash=svc.secret_hash('rotated-during-wait')
                locker.flush();worker=workers.submit(authority)
                try:
                    assert reached.wait(3);wait_for_lock(finish_case['factory'],observed['pid'])
                finally:locker.commit()
            worker.result(timeout=5)
    finally:event.remove(engine,'before_cursor_execute',observe)
