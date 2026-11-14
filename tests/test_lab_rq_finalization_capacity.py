"""Real RQ workers cannot consume the capacity their own publication awaits."""
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import uuid

import httpx
import pytest
from rq import Queue, Worker
from sqlalchemy import select
import uvicorn

from tests.support.environment import TestEnvironment
from tests.test_lab_foundation import database
from tests.test_lab_transfer_service import Store
from tinystudio.api.deps import forget_secret
from tinystudio.auth import ensure_instance, issue_service_token
from tinystudio.config import reload_config
from tinystudio.db.models import Project, User
from tinystudio.lab import service as svc, transfer_service as transfers
from tinystudio.lab.models import LabOperation, LabRun, LabTransfer
from tinystudio.lab.rq_adapter import DEFAULT_PROFILE
from tinystudio.lab.runner import ProcessGroup, Runner
from tinystudio.storage import storage


@pytest.mark.parametrize('worker_count',[1,2])
def test_all_execution_workers_busy_still_verify_and_finalize_with_production_owner(database,tmp_path,monkeypatch,worker_count):
    from tinystudio import db as db_module, worker as worker_module
    from tinystudio.api.app import create_app
    with TestEnvironment.from_environment().owned_redis(tmp_path) as redis:
        values={'TINYSTUDIO_DATABASE_URL':database.kw['bind'].url.render_as_string(hide_password=False),
            'TINYSTUDIO_REDIS_URL':redis.url,'TINYSTUDIO_DATA_DIR':str(tmp_path/'data'),
            'TINYSTUDIO_STORAGE_BACKEND':'local','TINYSTUDIO_CF_ACCESS_REQUIRED':'0',
            'CF_ACCESS_TEAM_DOMAIN':'','CF_ACCESS_AUD':'','TINYSTUDIO_LAB_OPERATION_MODE':'managed',
            'TINYSTUDIO_LAB_OPERATION_SHUTDOWN_GRACE':'.2'}
        credential=tmp_path/'operator.json';values['TINYSTUDIO_LAB_RQ_CREDENTIAL_FILE']=str(credential)
        for name,value in values.items():monkeypatch.setenv(name,value)
        reload_config();db_module.reset();worker_module.reset();forget_secret()
        with database.begin() as db:
            user=User(email=uuid.uuid4().hex+'@owned-rq.invalid',password_hash='not-a-login',role='admin')
            project=Project(slug='owned-'+uuid.uuid4().hex[:12],name='Independent RQ capacity')
            db.add_all([user,project]);db.flush()
            token=issue_service_token(ensure_instance(db).secret_key,user.id);prefix='/api/p/'+project.slug+'/lab';project_id=project.id
        app=create_app(run_migrations=False,ui=False)  # Actual API-owned dispatcher lifecycle.
        waiting=threading.Event();release=threading.Event();arrived=[]
        @app.middleware('http')
        async def hold_completed_children(request,call_next):
            if request.method=='POST' and request.url.path.endswith('/finalizations'):
                arrived.append(True)
                if len(arrived)==worker_count:waiting.set()
                import asyncio
                while not release.is_set():await asyncio.sleep(.01)
            return await call_next(request)
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},name='owned-rq-api');thread.start()
        groups=[]
        try:
            deadline=time.monotonic()+15
            while not server.started:
                assert thread.is_alive() and time.monotonic()<deadline
                time.sleep(.02)
            server_url='http://127.0.0.1:'+str(listener.getsockname()[1])
            with httpx.Client(base_url=server_url,headers={'Authorization':'Bearer '+token},trust_env=False,timeout=10) as author:
                response=author.post(prefix+'/targets',json={'name':'saturated-rq'});assert response.status_code==201,response.text
                target=response.json()
                credential.write_text(json.dumps({'server':server_url,'target_id':target['id'],'token':target['token']}));credential.chmod(0o600)
                advertiser=Runner(server_url,target['id'],target['token'],profile_config=DEFAULT_PROFILE)
                try:advertiser.advertise()
                finally:advertiser.client.close()
                definition={'name':'Owned capacity fixture','engine':'synthetic.signal','parameters':{'samples':2},
                    'execution':{'profile':'trusted_native','backend':'rq','max_seconds':10,'max_output_bytes':1048576}}
                response=author.post(prefix+'/definitions',json={'definition':definition});assert response.status_code==201,response.text
                definition_id=response.json()['id'];runs=[]
                for index in range(worker_count):
                    response=author.post(prefix+'/runs',json={'definition_id':definition_id,'revision':1,
                        'target_id':target['id'],'idempotency_key':uuid.uuid4().hex})
                    assert response.status_code==202,response.text;runs.append(response.json()['id'])
                for index in range(worker_count):
                    log=(tmp_path/('worker-'+str(index)+'.log')).open('wb')
                    group=ProcessGroup([sys.executable,'-c','from tinystudio.worker import work; work()'],
                        grace=.2,env=dict(os.environ),stdin=__import__('subprocess').DEVNULL,stdout=log,stderr=log)
                    groups.append((group,log))
                assert waiting.wait(20),'all actual RQ jobs must reach the publication wait'
                delivery=Queue(worker_module.QUEUE_NAME,connection=redis.connection)
                assert delivery.started_job_registry.count==worker_count
                assert len(Worker.all(connection=redis.connection))==worker_count
                # Seed owned provider-shaped control ACKs, then let the actual
                # independent production worker linearly verify local bytes.
                body=b'owned verification while execution workers wait'
                intent={'name':'capacity.bin','size':len(body),'sha256':hashlib.sha256(body).hexdigest(),
                        'media_type':'application/octet-stream','role':'source','visibility':'model'}
                store=Store()
                with database() as db:
                    row=transfers.admit(db,project_id,intent,uuid.uuid4().hex);db.commit()
                    transfers.initialize(db,project_id,row.id,store);db.commit()
                    path=storage()._path(row.storage_key);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
                    store.uploads[row.upload_id]['parts'][1]=body
                    transfers.complete(db,project_id,row.id,store);db.commit();transfer_id=row.id
                deadline=time.monotonic()+10
                while True:
                    with database() as db:
                        row=db.get(LabTransfer,transfer_id)
                        if row.state=='verified':
                            assert db.get(LabOperation,row.operation_id).state=='succeeded';break
                    assert time.monotonic()<deadline,'artifact_verify must not wait for the occupied RQ workers'
                    time.sleep(.02)
                assert delivery.started_job_registry.count==worker_count
                release.set()
                deadline=time.monotonic()+15
                while True:
                    with database() as db:
                        states=[db.get(LabRun,identifier).state for identifier in runs]
                        if states==['succeeded']*worker_count:
                            finalizers=list(db.scalars(select(LabOperation).where(LabOperation.project_id==project_id,
                                LabOperation.kind=='attempt_finalize')))
                            assert len(finalizers)==worker_count and all(row.state=='succeeded' and row.generation==1 for row in finalizers)
                            break
                    assert time.monotonic()<deadline,states
                    time.sleep(.03)
                assert not any(job.func_name=='tinystudio.lab.operations.run_operation' for job in delivery.get_jobs())
        finally:
            release.set()
            for group,log in groups:
                try:group.close()
                finally:log.close()
            server.should_exit=True;thread.join(10);listener.close()
            db_module.reset();worker_module.reset();forget_secret()
            assert not thread.is_alive()
            monkeypatch.undo();reload_config()
