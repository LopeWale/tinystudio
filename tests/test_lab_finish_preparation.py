"""Finish preparation preserves exact bytes and caller-owned transactions."""
from dataclasses import FrozenInstanceError, replace
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import json
import uuid
from threading import Event, current_thread

import pytest
from sqlalchemy import event, select

from tests.test_lab_transfer_service import database
from tinystudio.db.models import Project
from tinystudio.lab import service as svc
from tinystudio.lab.contracts import checksum
from tinystudio.lab.models import LabArtifact, LabAttempt, LabDefinition, LabEvent, LabRun, LabTarget
from tinystudio.storage import ArtifactStorage


@pytest.fixture
def finish_case(database,tmp_path,monkeypatch):
    backend=ArtifactStorage(root=tmp_path/'objects')
    monkeypatch.setattr(svc,'storage',lambda:backend)
    with database.begin() as db:
        project=Project(slug=uuid.uuid4().hex,name='Finish preparation');db.add(project);db.flush()
        definition=LabDefinition(project_id=project.id,name='Finish fixture')
        target=LabTarget(project_id=project.id,name='owned',enabled=True,token_hash=svc.secret_hash('owned-target'))
        db.add_all([definition,target]);db.flush()
        spec={'definition':{'execution':{'max_output_bytes':128}},'identity':'frozen'}
        run=LabRun(project_id=project.id,definition_id=definition.id,revision=1,target_id=target.id,
            idempotency_key='owned-run',spec=spec,spec_digest=checksum(spec),state='running')
        db.add(run);db.flush()
        attempt=LabAttempt(run_id=run.id,target_id=target.id,number=1,token_hash=svc.secret_hash('owned-attempt'),
            lease_until=svc.now()+timedelta(minutes=10),state='running',runtime={})
        db.add(attempt);db.flush()
        path=tmp_path/'owned.bin';path.write_bytes(b'owned evidence')
        artifact=svc.store_artifact(db,project.id,path,'owned.bin','application/octet-stream','evidence',attempt.id)
        return {'factory':database,'project':project.id,'run':run.id,'attempt':attempt.id,'target':target.id,
            'artifact':svc.artifact_dict(artifact),'storage':backend}


def prepared(case,state='succeeded',error=None):
    from tinystudio.lab import finalization_service as finish
    body={'artifacts':[case['artifact']],'metrics':{'large':1e30,'signed_zero':-0.0}}
    with case['factory']() as db:
        authenticated=finish._authenticate_finish(db,db.get(LabTarget,case['target']),case['attempt'],
            'owned-attempt',state,body,error)
        identity,request=authenticated
        value=finish._prepare_finish(db,identity,request)
    return value,body


def test_prepared_finish_is_frozen_exact_and_reusable_after_preparation_session_closes(finish_case,monkeypatch):
    from tinystudio.lab import finalization_service as finish
    value,body=prepared(finish_case)
    with pytest.raises(FrozenInstanceError):value.payload_digest='0'*64
    assert value.payload_digest==checksum({'state':'succeeded','result':body,'error':None})
    assert '1e+30' in value.outcome_json and '-0.0' in value.outcome_json
    assert 'owned-attempt' not in repr(value) and 'owned-target' not in repr(value)
    body['metrics']['large']=99
    def provider_io(*args,**kwargs):pytest.fail('publication performed provider I/O')
    monkeypatch.setattr(finish_case['storage'],'download_file',provider_io)
    monkeypatch.setattr(finish_case['storage'],'upload_file',provider_io)
    with finish_case['factory']() as db:
        assert finish._publish_prepared_finish(db,value)=={'accepted':True,'state':'succeeded'}
        with finish_case['factory']() as reader:
            assert reader.get(LabRun,finish_case['run']).state=='running'
        db.commit()
    with finish_case['factory']() as db:
        archive=db.scalar(select(LabArtifact).where(LabArtifact.attempt_id==finish_case['attempt'],LabArtifact.role=='attempt_outcome'))
        data=finish_case['storage']._path(archive.storage_key).read_bytes()
        assert data==value.outcome_json.encode() and hashlib.sha256(data).hexdigest()==archive.sha256
        assert json.loads(data)['result']['metrics']['large']==1e30


def test_prepared_digest_tampering_cannot_publish(finish_case):
    from tinystudio.lab import finalization_service as finish
    value,_=prepared(finish_case)
    with finish_case['factory']() as db:
        with pytest.raises(svc.LabError):finish._publish_prepared_finish(db,replace(value,payload_digest='0'*64))
    with finish_case['factory']() as db:
        assert db.get(LabRun,finish_case['run']).state=='running'


def test_prepared_authority_stays_frozen_when_original_target_is_refreshed(finish_case):
    from tinystudio.lab import finalization_service as finish
    with finish_case['factory']() as db:
        target=db.get(LabTarget,finish_case['target'])
        identity,request=finish._authenticate_finish(db,target,finish_case['attempt'],
            'owned-attempt','failed',None,'controlled failure')
        with finish_case['factory'].begin() as writer:
            writer.get(LabTarget,finish_case['target']).token_hash=svc.secret_hash('new-target')
        db.refresh(target)
        assert target.token_hash!=identity.target_token_hash
        assert identity.target_token_hash==svc.secret_hash('owned-target')
        value=finish._prepare_finish(db,identity,request)
        with pytest.raises(svc.LabError,match='credential changed'):
            finish._publish_prepared_finish(db,value)


def test_heartbeat_can_extend_lease_during_unlocked_preparation(finish_case,monkeypatch):
    clock=[svc.now()]
    with finish_case['factory'].begin() as db:
        db.get(LabAttempt,finish_case['attempt']).lease_until=clock[0]+timedelta(seconds=10)
    original=svc._verify_result
    def verify(db,project,result,directory):
        original(db,project,result,directory)
        clock[0]+=timedelta(seconds=5)
        with finish_case['factory'].begin() as heartbeat:
            svc.heartbeat(heartbeat,heartbeat.get(LabTarget,finish_case['target']),
                finish_case['attempt'],'owned-attempt',[])
        clock[0]+=timedelta(seconds=10)
    monkeypatch.setattr(svc,'now',lambda:clock[0])
    monkeypatch.setattr(svc,'_verify_result',verify)
    with finish_case['factory'].begin() as db:
        assert svc.finish(db,db.get(LabTarget,finish_case['target']),finish_case['attempt'],
            'owned-attempt','succeeded',{'artifacts':[finish_case['artifact']]},None)=={'accepted':True,'state':'succeeded'}


def test_publication_waits_for_project_before_locking_run_needed_by_deletion(finish_case):
    from tinystudio.lab import finalization_service as finish
    from tinystudio.services.projects import ProjectError, delete_project
    value,_=prepared(finish_case)
    waiting=Event()
    engine=finish_case['factory'].kw['bind']
    def observe(connection,cursor,statement,parameters,context,many):
        if (current_thread().name.startswith('finish-publication')
                and statement.startswith('SELECT projects.id') and 'FOR UPDATE' in statement):
            waiting.set()
    def publish():
        with finish_case['factory'].begin() as db:return finish._publish_prepared_finish(db,value)
    event.listen(engine,'before_cursor_execute',observe)
    try:
        with ThreadPoolExecutor(max_workers=1,thread_name_prefix='finish-publication') as workers:
            with finish_case['factory']() as deleting:
                project=deleting.scalar(select(Project).where(Project.id==finish_case['project']).with_for_update())
                future=workers.submit(publish)
                try:
                    assert waiting.wait(timeout=3),'publication did not reach owned project lock'
                    with pytest.raises(ProjectError,match='active laboratory run'):
                        delete_project(deleting,project)
                finally:deleting.rollback()
            assert future.result(timeout=5)=={'accepted':True,'state':'succeeded'}
    finally:event.remove(engine,'before_cursor_execute',observe)


@pytest.mark.parametrize('change',['target_token','disabled','attempt_token','lease','run_state','spec','cancel','metadata'])
def test_unlocked_preparation_cannot_publish_after_authority_changes(finish_case,monkeypatch,change):
    original=svc._verify_result
    def verify(db,project,result,directory):
        original(db,project,result,directory)
        with finish_case['factory'].begin() as writer:
            if change=='target_token':writer.get(LabTarget,finish_case['target']).token_hash=svc.secret_hash('changed-target')
            elif change=='disabled':writer.get(LabTarget,finish_case['target']).enabled=False
            elif change=='attempt_token':writer.get(LabAttempt,finish_case['attempt']).token_hash=svc.secret_hash('changed-attempt')
            elif change=='lease':writer.get(LabAttempt,finish_case['attempt']).lease_until=svc.now()-timedelta(seconds=1)
            elif change=='run_state':writer.get(LabRun,finish_case['run']).state='failed'
            elif change=='spec':writer.get(LabRun,finish_case['run']).spec={'changed':True}
            elif change=='cancel':writer.get(LabRun,finish_case['run']).cancel_requested=True
            else:writer.get(LabArtifact,finish_case['artifact']['id']).name='changed.bin'
    monkeypatch.setattr(svc,'_verify_result',verify)
    with finish_case['factory']() as db:
        with pytest.raises(svc.LabError):
            svc.finish(db,db.get(LabTarget,finish_case['target']),finish_case['attempt'],'owned-attempt',
                'succeeded',{'artifacts':[finish_case['artifact']]},None)
        db.rollback()
    with finish_case['factory']() as db:
        assert db.get(LabAttempt,finish_case['attempt']).state=='running'
        assert db.scalar(select(LabArtifact).where(LabArtifact.attempt_id==finish_case['attempt'],LabArtifact.role=='attempt_outcome')) is None


@pytest.mark.parametrize('state',['succeeded','failed','cancelled'])
def test_public_finish_ack_idempotency_and_atomic_caller_rollback(finish_case,state):
    body={'artifacts':[finish_case['artifact']]};error='owned error' if state=='failed' else None
    with finish_case['factory']() as db:
        target=db.get(LabTarget,finish_case['target'])
        ack=svc.finish(db,target,finish_case['attempt'],'owned-attempt',state,body,error)
        assert ack=={'accepted':True,'state':state}
        db.rollback()
    with finish_case['factory']() as db:
        assert db.get(LabAttempt,finish_case['attempt']).state=='running'
        assert list(db.scalars(select(LabEvent).where(LabEvent.run_id==finish_case['run'])))==[]
        target=db.get(LabTarget,finish_case['target'])
        assert svc.finish(db,target,finish_case['attempt'],'owned-attempt',state,body,error)==ack
        db.commit()
    with finish_case['factory']() as db:
        target=db.get(LabTarget,finish_case['target'])
        assert svc.finish(db,target,finish_case['attempt'],'owned-attempt',state,body,error)==ack
        with pytest.raises(svc.LabError):svc.finish(db,target,finish_case['attempt'],'owned-attempt',state,body,'different')
        assert len(list(db.scalars(select(LabEvent).where(LabEvent.run_id==finish_case['run'],LabEvent.kind=='server.outcome'))))==1


def test_internal_local_target_none_hash_and_raw_attempt_gate_remain_compatible(finish_case):
    with finish_case['factory'].begin() as db:db.get(LabTarget,finish_case['target']).token_hash=None
    with finish_case['factory']() as db:
        target=db.get(LabTarget,finish_case['target'])
        with pytest.raises(svc.LabError) as denied:
            svc.finish(db,target,finish_case['attempt'],svc.secret_hash('owned-attempt'),'failed',None,None)
        assert denied.value.status==403
        assert svc.finish(db,target,finish_case['attempt'],'owned-attempt','failed',None,None)=={'accepted':True,'state':'failed'}
        db.commit()
