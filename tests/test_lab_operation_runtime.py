"""Independent capacity configuration, availability, fencing and owned shutdown."""
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import time

from fastapi import FastAPI
import pytest
from sqlalchemy import select

from tests.test_lab_finish_preparation import database, finish_case
from tests.test_lab_async_finalization import http, paths
from tinystudio.config import reload_config
from tinystudio.lab import finalization_service as final, operations as ops, service as svc
from tinystudio.lab.dispatch import Dispatcher
from tinystudio.lab.models import LabAttempt, LabOperation, LabRun, LabTarget
from tinystudio.lab.operation_runtime import (OperationExecutor, OperationRuntimeConfiguration,
    operation_runtime_status, register_operation_runtime)
from tinystudio.lab.transfer_configuration import AttemptArtifactApiPaths


def wait_until(predicate,seconds=5):
    deadline=time.monotonic()+seconds
    while not predicate():
        assert time.monotonic()<deadline,'owned operation condition did not settle'
        time.sleep(.01)


def test_configuration_is_pure_and_zero_modes_are_explicit(monkeypatch):
    def unexpected(*args,**kwargs):pytest.fail('pure configuration started I/O or a thread')
    monkeypatch.setattr('threading.Thread.start',unexpected)
    monkeypatch.setattr('tempfile.mkstemp',unexpected)
    selected=OperationRuntimeConfiguration(capacity=0,poll_seconds=0,shutdown_grace=0,startup_timeout_seconds=0)
    assert selected.mode=='disabled' and selected.capacity==0 and selected.poll_seconds==0
    external=OperationRuntimeConfiguration.from_environment({'TINYSTUDIO_LAB_OPERATION_MODE':'external'})
    assert external.status()['available'] and external.status()['alive'] is None and external.status()['ready'] is None
    assert external.status()['qualification']=='operator_declared_unqualified'
    assert not operation_runtime_status(FastAPI())['available']


@pytest.mark.parametrize('values',[{'capacity':True},{'capacity':-1},{'poll_seconds':-1},{'shutdown_grace':float('nan')},
                                  {'startup_timeout_seconds':float('inf')},{'mode':'automatic'}])
def test_invalid_runtime_configuration_is_rejected(values):
    with pytest.raises(ValueError):OperationRuntimeConfiguration(**values)


def test_delivery_excludes_only_owned_dependency_kinds(finish_case):
    project=finish_case['project'];ids={}
    from tinystudio.lab.contracts import checksum
    with finish_case['factory'].begin() as db:
        for kind in ('export','import','data_bridge','artifact_verify','attempt_finalize'):
            row=LabOperation(project_id=project,kind=kind,idempotency_key=kind,payload={},payload_digest=checksum({}))
            db.add(row);db.flush();ids[kind]=row.id
    sent=[]
    with finish_case['factory']() as db:
        ops.dispatch_pending(db,sent.append,excluded_kinds=OperationRuntimeConfiguration().excluded_delivery_kinds)
    assert set(sent)=={ids[kind] for kind in ('export','import','data_bridge')}


def test_new_admission_and_pending_runner_status_fail_closed_but_history_survives_off(finish_case,http):
    client,app=http;route=paths(finish_case)
    request={'state':'failed','result':None,'error':'owned stopped child'}
    response=client.post(route.collection,json=request);assert response.status_code==202,response.text
    identifier=response.json()['id']
    register_operation_runtime(app,OperationRuntimeConfiguration(mode='disabled'))
    assert client.get(AttemptArtifactApiPaths(route.base).policy_route).status_code==503
    assert client.get(route.operation(identifier)).status_code==503
    assert client.post(route.collection,json=request).status_code==503
    assert ops.run_operation(identifier,finish_case['factory'])['state']=='succeeded'
    with finish_case['factory'].begin() as db:db.get(LabAttempt,finish_case['attempt']).lease_until=svc.now()-timedelta(seconds=1)
    assert client.get(route.operation(identifier)).json()['result']=={'accepted':True,'state':'failed'}
    assert client.post(route.collection,json=request).json()['id']==identifier


def test_process_alive_without_worker_readiness_is_not_available_and_threads_die_on_close(tmp_path,monkeypatch):
    monkeypatch.setenv('TINYSTUDIO_DATA_DIR',str(tmp_path/'data'));reload_config()
    marker=tmp_path/'count'
    code="\n".join(['import signal,threading,time,pathlib',
        'signal.signal(signal.SIGTERM,signal.SIG_IGN)',f'p=pathlib.Path({str(marker)!r})',
        'def work():',' while True:','  with p.open("a") as f:f.write("tick\\n")','  time.sleep(.01)',
        'threading.Thread(target=work,daemon=True).start()','while True:time.sleep(1)'])
    runtime=Dispatcher(command=[sys.executable,'-c',code],configuration=OperationRuntimeConfiguration(shutdown_grace=.1)).start()
    pgid=runtime.group.pgid
    try:
        wait_until(marker.exists)
        assert runtime.status()['alive'] and not runtime.status()['ready'] and not runtime.status()['available']
        asyncio.run(runtime.close())
        with pytest.raises(ProcessLookupError):os.killpg(pgid,0)
        prior=marker.read_bytes();time.sleep(.04);assert marker.read_bytes()==prior
    finally:
        asyncio.run(runtime.close());monkeypatch.undo();reload_config()


def test_owned_worker_death_expires_only_operation_and_requires_explicit_retry(finish_case,tmp_path,monkeypatch):
    from tinystudio import db as db_module
    factory=finish_case['factory']
    monkeypatch.setenv('TINYSTUDIO_DATABASE_URL',factory.kw['bind'].url.render_as_string(hide_password=False))
    monkeypatch.setenv('TINYSTUDIO_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('TINYSTUDIO_STORAGE_BACKEND','local')
    monkeypatch.delenv('TINYSTUDIO_LAB_RQ_CREDENTIAL_FILE',raising=False)
    reload_config();db_module.reset()
    config=OperationRuntimeConfiguration(delivery_enabled=False,shutdown_grace=.1,poll_seconds=.01)
    with factory.begin() as db:
        attempt=db.get(LabAttempt,finish_case['attempt']);original_deadline=attempt.lease_until
        row=final.request_finalization(db,db.get(LabTarget,finish_case['target']),finish_case['attempt'],
            'owned-attempt','failed',None,'owned failure')
        identifier=row.id
    runtime=None;replacement=None
    try:
        with factory() as locked:
            locked.scalar(select(LabRun).where(LabRun.id==finish_case['run']).with_for_update())
            runtime=Dispatcher(configuration=config).start()
            def claimed():
                with factory() as db:return db.get(LabOperation,identifier).state=='running'
            wait_until(claimed)
            assert runtime.status()['available']
            asyncio.run(runtime.close())
            assert not runtime.status()['available']
        with factory.begin() as db:
            row=db.get(LabOperation,identifier)
            assert row.state=='running' and row.generation==1
            assert db.get(LabAttempt,finish_case['attempt']).lease_until==original_deadline
            row.lease_until=svc.now()-timedelta(seconds=1);db.flush();ops.recover_expired(db)
        replacement=Dispatcher(configuration=config).start()
        time.sleep(.1)
        with factory.begin() as db:
            row=db.get(LabOperation,identifier);assert row.state=='lost' and row.generation==1
            ops.retry_operation(db,finish_case['project'],identifier)
        def completed():
            with factory() as db:return db.get(LabOperation,identifier).state=='succeeded'
        wait_until(completed)
        with factory() as db:
            assert db.get(LabOperation,identifier).generation==2
            assert db.get(LabAttempt,finish_case['attempt']).lease_until==original_deadline
    finally:
        if replacement:asyncio.run(replacement.close())
        if runtime:asyncio.run(runtime.close())
        db_module.reset();monkeypatch.undo();reload_config()


def test_fatal_slot_reports_unavailable_instead_of_silent_capacity_loss(finish_case,tmp_path,monkeypatch):
    from tinystudio.lab.contracts import checksum
    with finish_case['factory'].begin() as db:
        db.add(LabOperation(project_id=finish_case['project'],kind='artifact_verify',idempotency_key='fatal-slot',
            payload={},payload_digest=checksum({})))
    def fatal(*args,**kwargs):raise SystemExit('private-provider-credential-must-not-be-status')
    monkeypatch.setattr(ops,'run_operation',fatal)
    marker=tmp_path/'capacity.json'
    executor=OperationExecutor(finish_case['factory'],OperationRuntimeConfiguration(poll_seconds=.01),
        status_path=marker,instance='owned-instance').start()
    try:
        wait_until(lambda:executor.failure is not None)
        with pytest.raises(RuntimeError,match='terminated'):executor.check()
        wait_until(lambda:json.loads(marker.read_text())['ready'] is False)
        document=marker.read_text()
        assert json.loads(document)['ready'] is False and 'private-provider' not in document
    finally:executor.close()


def test_cli_once_is_explicit_delivery_only_without_owned_threads(monkeypatch,capsys):
    from tinystudio.lab import cli, dispatch
    monkeypatch.setattr(dispatch,'tick',lambda:{'dependency_capacity_owner':False})
    monkeypatch.setattr(Dispatcher,'start',lambda *a:pytest.fail('--once started a background owner'))
    cli.dispatch(once=True)
    assert '"dependency_capacity_owner": false' in capsys.readouterr().out


def test_foreground_cli_shutdown_closes_its_owned_capacity_process(finish_case,tmp_path,monkeypatch):
    from tinystudio.lab.runner import ProcessGroup
    from tinystudio.lab.operation_runtime import OperationRuntimePaths
    monkeypatch.setenv('TINYSTUDIO_DATABASE_URL',finish_case['factory'].kw['bind'].url.render_as_string(hide_password=False))
    monkeypatch.setenv('TINYSTUDIO_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('TINYSTUDIO_LAB_OPERATION_DELIVERY_ENABLED','0')
    monkeypatch.setenv('TINYSTUDIO_LAB_OPERATION_SHUTDOWN_GRACE','.1')
    reload_config()
    controller=ProcessGroup([sys.executable,'-c','from tinystudio.lab.dispatch import run_foreground; run_foreground()'],
        grace=1,env=dict(os.environ),stdin=__import__('subprocess').DEVNULL)
    directory=OperationRuntimePaths(tmp_path/'data').directory
    try:
        def ready():return directory.exists() and any(directory.glob('owner-*/capacity.json'))
        wait_until(ready)
        status=next(directory.glob('owner-*/capacity.json'))
        pid=json.loads(status.read_text())['pid']
        controller.close()
        with pytest.raises(ProcessLookupError):os.kill(pid,0)
        assert list(directory.iterdir())==[]
    finally:
        controller.close();monkeypatch.undo();reload_config()
