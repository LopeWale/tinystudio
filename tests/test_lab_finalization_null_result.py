"""Bare terminal outcomes qualify SQL NULL and JSON null legacy projections."""
from datetime import timedelta
import json

import pytest
from sqlalchemy import JSON, Text, cast, literal, null, select, update
from sqlalchemy.dialects.postgresql import JSONB

from tests.test_lab_finish_preparation import database, finish_case
from tests.test_lab_async_finalization import http, paths
from tinystudio.lab import finalization_service as final, operations as ops, service as svc
from tinystudio.lab.contracts import checksum
from tinystudio.lab.models import LabArtifact, LabAttempt, LabRun


@pytest.mark.parametrize('state',['failed','cancelled'])
@pytest.mark.parametrize('initial_projection',['sql_null','json_null'])
def test_bare_terminal_finalization_retains_none_outcome_and_exact_error(finish_case,http,monkeypatch,state,initial_projection):
    client,_=http;route=paths(finish_case)
    if initial_projection=='json_null':
        with finish_case['factory'].begin() as db:
            db.execute(update(LabRun).where(LabRun.id==finish_case['run']).values(result=JSON.NULL))
    error='Traceback (most recent call last):\n  owned fixture child\nValueError: stale annotation checksum changed\n'
    request={'state':state,'result':None,'error':error}
    response=client.post(route.collection,json=request)
    assert response.status_code==202,response.text
    identifier=response.json()['id'];observed={}
    original=final.check_finalization_publication_authority
    def inspect_projection(db,row,result):
        projection=cast(literal('null',type_=Text),JSONB)
        value=db.execute(select(LabRun.result.is_(None),LabRun.result==projection)
                         .where(LabRun.id==finish_case['run'])).one()
        observed.update(sql_null=value[0],json_null=value[1])
        try:return original(db,row,result)
        except svc.LabError as exc:
            observed['failure']=str(exc);raise
    monkeypatch.setattr(final,'check_finalization_publication_authority',inspect_projection)
    result=ops.run_operation(identifier,finish_case['factory'])
    assert result is not None,observed
    assert result['state']=='succeeded' and result['result']=={'accepted':True,'state':state}
    assert observed==({'sql_null':True,'json_null':None} if initial_projection=='sql_null'
                      else {'sql_null':False,'json_null':True})
    with finish_case['factory'].begin() as db:
        run=db.get(LabRun,finish_case['run']);attempt=db.get(LabAttempt,finish_case['attempt'])
        assert run.state==state and run.result is None and run.error==error
        assert attempt.state==state and attempt.result_digest==checksum(request)
        archive=db.scalar(select(LabArtifact).where(LabArtifact.attempt_id==attempt.id,LabArtifact.role=='attempt_outcome'))
        document=json.loads(finish_case['storage']._path(archive.storage_key).read_bytes())
        assert document['state']==state and document['result'] is None and document['error']==error
        attempt.lease_until=svc.now()-timedelta(seconds=1)
    assert client.get(route.by_digest(checksum(request))).json()['result']=={'accepted':True,'state':state}
    assert client.post(route.collection,json=request).json()['id']==identifier
    assert ops.run_operation(identifier,finish_case['factory']) is None


@pytest.mark.parametrize('expected_null',[True,False])
def test_null_projection_compatibility_rejects_a_different_provisional_result(finish_case,http,monkeypatch,expected_null):
    client,_=http;route=paths(finish_case)
    request={'state':'failed','result':None if expected_null else {'artifacts':[finish_case['artifact']]},
             'error':'owned child failure'}
    response=client.post(route.collection,json=request);assert response.status_code==202,response.text
    publish=final._publish_prepared_finish
    def changed(db,prepared,**options):
        receipt=publish(db,prepared,**options)
        if expected_null:db.get(LabRun,finish_case['run']).result={'unexpected':'projection'}
        else:db.execute(update(LabRun).where(LabRun.id==finish_case['run']).values(result=null()))
        return receipt
    monkeypatch.setattr(final,'_publish_prepared_finish',changed)
    assert ops.run_operation(response.json()['id'],finish_case['factory']) is None
    with finish_case['factory']() as db:
        assert db.get(LabRun,finish_case['run']).state=='running'
        assert db.get(LabAttempt,finish_case['attempt']).state=='running'
        assert db.scalar(select(LabArtifact).where(LabArtifact.attempt_id==finish_case['attempt'],LabArtifact.role=='attempt_outcome')) is None
