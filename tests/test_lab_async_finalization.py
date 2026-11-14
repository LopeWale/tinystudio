"""Durable finish envelopes use owned PostgreSQL and local immutable objects."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from threading import Event

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.test_lab_finish_preparation import database, finish_case
from tinystudio.api.deps import get_db
from tinystudio.lab import api, finalization_service as final, operations as ops, service as svc
from tinystudio.lab.contracts import checksum
from tinystudio.db.models import Project
from tinystudio.lab.models import LabArtifact, LabAttempt, LabEvent, LabOperation, LabRun, LabTarget


@pytest.fixture
def http(finish_case):
    app=FastAPI();api.install(app)
    def sessions():
        with finish_case['factory']() as db:
            try:yield db;db.commit()
            except BaseException:db.rollback();raise
    app.dependency_overrides[get_db]=sessions
    with TestClient(app,headers={'Authorization':'Bearer owned-target','X-Lab-Lease':'owned-attempt'},
                    raise_server_exceptions=False) as client:
        yield client,app


def paths(case):
    from tinystudio.lab.finalization_configuration import FinalizationApiPaths
    return FinalizationApiPaths.for_runner(case['target'],case['attempt'])


def body(case,state='succeeded'):
    return {'state':state,'result':{'artifacts':[case['artifact']],
        'metrics':{'large':1e30,'negative_zero':-0.0}},'error':None}


def test_durable_admission_exact_float_envelope_and_historical_terminal_status(finish_case,http,monkeypatch):
    client,_=http;route=paths(finish_case);request=body(finish_case);digest=checksum(request)
    assert client.get(route.by_digest(digest)).status_code==404
    response=client.post(route.collection,json=request)
    assert response.status_code==202,response.text
    receipt=response.json()
    assert receipt['kind']=='attempt_finalize' and receipt['state']=='queued' and receipt['result'] is None
    assert receipt['payload_digest']==digest
    assert {key:receipt[key] for key in ('attempt_id','run_id','target_id','spec_digest')}=={
        'attempt_id':finish_case['attempt'],'run_id':finish_case['run'],'target_id':finish_case['target'],
        'spec_digest':checksum({'definition':{'execution':{'max_output_bytes':128}},'identity':'frozen'})}
    with finish_case['factory']() as db:
        row=db.get(LabOperation,receipt['id'])
        text=row.payload['envelope_json']
        assert hashlib.sha256(text.encode()).hexdigest()==row.payload['envelope_sha256']
        envelope=json.loads(text)
        assert '1e+30' in envelope['request_json'] and '-0.0' in envelope['request_json']
        assert 'owned-target' not in text and 'owned-attempt' not in text
        assert db.get(LabAttempt,finish_case['attempt']).state=='running'
    assert client.post(route.collection,json=request).json()['id']==receipt['id']
    assert client.get(route.by_digest(digest)).json()['id']==receipt['id']
    def materialize(*args):pytest.fail('asynchronous finalization materialized large evidence')
    monkeypatch.setattr(svc,'materialize',materialize)
    result=ops.run_operation(receipt['id'],finish_case['factory'])
    assert result['state']=='succeeded' and result['result']=={'accepted':True,'state':'succeeded'}
    with finish_case['factory'].begin() as db:
        db.get(LabAttempt,finish_case['attempt']).lease_until=svc.now()-timedelta(seconds=1)
    assert client.post(route.base+'/heartbeat',json={'events':[]}).status_code==409
    historical=client.get(route.operation(receipt['id']))
    assert historical.status_code==200,historical.text
    assert historical.json()['result']=={'accepted':True,'state':'succeeded'}
    assert client.post(route.collection,json=request).json()['id']==receipt['id']
    assert client.post(route.collection,json={**request,'error':'different outcome'}).status_code==409
    assert ops.run_operation(receipt['id'],finish_case['factory']) is None
    with finish_case['factory']() as db:
        archive=db.scalar(select(LabArtifact).where(LabArtifact.attempt_id==finish_case['attempt'],LabArtifact.role=='attempt_outcome'))
        text=finish_case['storage']._path(archive.storage_key).read_text()
        assert '1e+30' in text and '-0.0' in text
        assert db.scalar(select(func.count()).select_from(LabEvent).where(LabEvent.run_id==finish_case['run'],LabEvent.kind=='server.outcome'))==1


@pytest.mark.parametrize('state',['failed','cancelled'])
def test_cancel_requested_retains_audit_finalization_without_execution_deadline(finish_case,http,state):
    with finish_case['factory'].begin() as db:
        run=db.get(LabRun,finish_case['run']);run.cancel_requested=True
        run.spec={'definition':{'execution':{'max_seconds':0,'max_output_bytes':128}},'identity':'frozen'}
        run.spec_digest=checksum(run.spec)
    client,_=http;route=paths(finish_case)
    response=client.post(route.collection,json=body(finish_case,state))
    assert response.status_code==202,response.text
    assert ops.run_operation(response.json()['id'],finish_case['factory'])['result']=={'accepted':True,'state':state}


def test_cancel_requested_rejects_success_and_historical_scope_never_admits_expired_work(finish_case,http):
    client,_=http;route=paths(finish_case)
    with finish_case['factory'].begin() as db:db.get(LabRun,finish_case['run']).cancel_requested=True
    assert client.post(route.collection,json=body(finish_case)).status_code==409
    request=body(finish_case,'cancelled')
    response=client.post(route.collection,json=request)
    assert response.status_code==202,response.text
    with finish_case['factory'].begin() as db:db.get(LabAttempt,finish_case['attempt']).lease_until=svc.now()-timedelta(seconds=1)
    assert client.get(route.by_digest(checksum(request))).status_code==200
    assert client.get(route.operation(response.json()['id']),headers={'X-Lab-Lease':'wrong'}).status_code==403
    assert client.post(route.collection,json=body(finish_case,'failed')).status_code==409
    assert ops.run_operation(response.json()['id'],finish_case['factory']) is None
    status=client.get(route.operation(response.json()['id'])).json()
    assert status['state']=='failed' and status['result'] is None
    assert client.post(route.collection,json=request).json()['state']=='failed'


@pytest.mark.parametrize('route_kind',['async','sync'])
def test_authenticated_target_snapshot_survives_expire_on_commit_and_rotation(finish_case,http,monkeypatch,route_kind):
    client,app=http
    factory=sessionmaker(finish_case['factory'].kw['bind'],expire_on_commit=True)
    def sessions():
        with factory() as db:
            try:yield db;db.commit()
            except BaseException:db.rollback();raise
    app.dependency_overrides[get_db]=sessions
    original=Session.commit;rotated=[]
    def commit(db):
        original(db)
        if not rotated:
            rotated.append(True)
            with finish_case['factory'].begin() as writer:
                writer.get(LabTarget,finish_case['target']).token_hash=svc.secret_hash('rotated-target')
    monkeypatch.setattr(Session,'commit',commit)
    route=paths(finish_case)
    url=route.collection if route_kind=='async' else route.legacy_finish
    response=client.post(url,json=body(finish_case))
    assert response.status_code==403,response.text
    with finish_case['factory']() as db:
        assert db.get(LabAttempt,finish_case['attempt']).state=='running'
        assert db.scalar(select(func.count()).select_from(LabOperation).where(LabOperation.project_id==finish_case['project']))==0


def test_lost_admission_response_recovers_exact_durable_id_without_requeue(finish_case,http,monkeypatch):
    client,_=http;route=paths(finish_case);request=body(finish_case)
    original=Session.commit;calls=[]
    def commit(db):
        original(db);calls.append(True)
        if len(calls)==2:raise RuntimeError('owned lost admission response')
    with monkeypatch.context() as patch:
        patch.setattr(Session,'commit',commit)
        assert client.post(route.collection,json=request).status_code==500
    status=client.get(route.by_digest(checksum(request)))
    assert status.status_code==200,status.text
    receipt=status.json()
    assert receipt['state']=='queued' and receipt['result'] is None
    with finish_case['factory'].begin() as db:ops.cancel_operation(db,finish_case['project'],receipt['id'])
    assert client.post(route.collection,json=request).json()['state']=='cancelled'
    assert ops.run_operation(receipt['id'],finish_case['factory']) is None


def test_lost_worker_commit_response_is_recovered_as_the_exact_terminal_receipt(finish_case,http):
    client,_=http;route=paths(finish_case);request=body(finish_case)
    admitted=client.post(route.collection,json=request);assert admitted.status_code==202,admitted.text
    identifier=admitted.json()['id'];lost=[]
    def arm_before_flush(db,context,instances):
        if any(isinstance(row,LabOperation) and row.state=='succeeded' for row in db.dirty):
            db.info['owned_commit_loss']=True
    def lose_after_commit(db):
        if not lost and db.info.pop('owned_commit_loss',False):
            lost.append(True);raise RuntimeError('owned lost worker commit receipt')
    event.listen(Session,'before_flush',arm_before_flush)
    event.listen(Session,'after_commit',lose_after_commit)
    try:assert ops.run_operation(identifier,finish_case['factory']) is None
    finally:
        event.remove(Session,'before_flush',arm_before_flush)
        event.remove(Session,'after_commit',lose_after_commit)
    assert lost==[True]
    status=client.get(route.by_digest(checksum(request)))
    assert status.status_code==200,status.text
    assert status.json()['id']==identifier and status.json()['state']=='succeeded'
    assert status.json()['result']=={'accepted':True,'state':'succeeded'}
    assert client.post(route.collection,json=request).json()['id']==identifier
    assert ops.run_operation(identifier,finish_case['factory']) is None


def test_admission_releases_authenticated_target_before_waiting_for_project_deletion(finish_case,http):
    from tinystudio.services.projects import ProjectError, delete_project
    client,_=http;route=paths(finish_case);reached=Event()
    engine=finish_case['factory'].kw['bind']
    def observe(connection,cursor,statement,parameters,context,many):
        if statement.startswith('SELECT projects.id \n') and 'FOR UPDATE' in statement:
            reached.set()
    event.listen(engine,'before_cursor_execute',observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            with finish_case['factory']() as deleting:
                project=deleting.scalar(select(Project).where(Project.id==finish_case['project']).with_for_update())
                future=workers.submit(client.post,route.collection,json=body(finish_case))
                try:
                    assert reached.wait(3),'owned admission did not reach its project lock'
                    with pytest.raises(ProjectError,match='active laboratory run'):
                        delete_project(deleting,project)
                finally:deleting.rollback()
            response=future.result(timeout=5)
            assert response.status_code==202,response.text
    finally:event.remove(engine,'before_cursor_execute',observe)


def test_historical_status_is_scoped_to_exact_attempt_even_with_same_target_and_raw_secret(finish_case,http):
    client,_=http;route=paths(finish_case);request=body(finish_case)
    response=client.post(route.collection,json=request);assert response.status_code==202,response.text
    identifier=response.json()['id']
    with finish_case['factory'].begin() as db:
        original=db.get(LabRun,finish_case['run'])
        run=LabRun(project_id=original.project_id,definition_id=original.definition_id,revision=1,target_id=original.target_id,
            idempotency_key='other-owned-run',spec=original.spec,spec_digest=original.spec_digest,state='running')
        db.add(run);db.flush()
        attempt=LabAttempt(run_id=run.id,target_id=original.target_id,number=1,token_hash=svc.secret_hash('owned-attempt'),
            lease_until=svc.now()+timedelta(minutes=1),state='running',runtime={})
        db.add(attempt);db.flush();other=attempt.id
    from tinystudio.lab.finalization_configuration import FinalizationApiPaths
    outside=FinalizationApiPaths.for_runner(finish_case['target'],other)
    assert client.get(outside.operation(identifier)).status_code==403
    assert client.get(outside.by_digest(checksum(request))).status_code==404
    assert client.get(route.operation(identifier),headers={'X-Lab-Lease':svc.secret_hash('owned-attempt')}).status_code==403
    assert client.post(route.collection,json=request,headers={'X-Lab-Lease':svc.secret_hash('owned-attempt')}).status_code==403


@pytest.mark.parametrize('state',['failed','lost','cancelled'])
def test_retained_unsuccessful_operation_status_never_autoretries(finish_case,http,state):
    client,_=http;route=paths(finish_case);request=body(finish_case)
    response=client.post(route.collection,json=request);assert response.status_code==202,response.text
    identifier=response.json()['id']
    with finish_case['factory'].begin() as db:
        row=db.get(LabOperation,identifier);row.state=state;row.finished=svc.now()
    assert client.get(route.by_digest(checksum(request))).json()['state']==state
    assert client.post(route.collection,json=request).json()['state']==state
    assert ops.run_operation(identifier,finish_case['factory']) is None
    with finish_case['factory']() as db:
        assert db.get(LabAttempt,finish_case['attempt']).state=='running'
        assert db.get(LabOperation,identifier).generation==0


@pytest.mark.parametrize(('maximum','payload','expected'),[(None,b'owned',201),(0,b'',201),(0,b'x',413),(5,b'owned',201)])
def test_proxy_output_optional_budget_and_exact_replay(finish_case,http,maximum,payload,expected):
    client,_=http
    with finish_case['factory'].begin() as db:
        db.delete(db.get(LabArtifact,finish_case['artifact']['id']))
        run=db.get(LabRun,finish_case['run']);run.spec={'definition':{'execution':{'max_output_bytes':maximum}}}
        run.spec_digest=checksum(run.spec)
    route='/api/lab/runner/'+finish_case['target']+'/attempts/'+finish_case['attempt']+'/artifacts'
    sha=hashlib.sha256(payload).hexdigest()
    response=client.post(route,params={'sha256':sha},files={'file':('owned.bin',payload,'application/octet-stream')})
    assert response.status_code==expected,response.text
    if expected==201:
        replay=client.post(route,params={'sha256':sha},files={'file':('owned.bin',payload,'application/octet-stream')})
        assert replay.status_code==201,replay.text
        assert replay.json()['id']==response.json()['id']
