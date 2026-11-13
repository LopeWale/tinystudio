"""Registered authenticated HTTP and shared clients, with real native evidence."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from tinystudio.cli.main import app
from tinystudio.integrations.tools import Studio, ToolSpec
from tinystudio.lab import cli, service as svc
from tinystudio.lab.tools import tool_specs
from tests.test_lab_native_workflow import native_http, enroll, posted
from tests.test_lab_studies import protocol, record
from tests.test_lab_statistics_integration import definition


def test_http_tracks_real_planning_evidence_across_protocol_edit_and_reopen(native_http):
    f = native_http
    target, runner = enroll(f, 'study-planning')
    try:
        calculation = posted(f.http, f.prefix+'/definitions', json={'definition': definition()})
        run = posted(f.http, f.prefix+'/runs', json={'definition_id': calculation['id'], 'revision': 1,
            'target_id': target['id'], 'idempotency_key': 'planning-for-study'})
        assert runner.run_once(run_id=run['id'])
    finally:
        runner.client.close()
    completed = f.http.get(f.prefix+'/runs/'+run['id']).json()
    artifact = next(item for item in completed['result']['artifacts'] if item['role'] == 'statistical_plan')
    study = posted(f.http, f.prefix+'/studies', json={'study': protocol(), 'idempotency_key': 'study-create'})
    entry = posted(f.http, f.prefix+'/studies/'+study['id']+'/records',
                   json=record(kind='planning', run_ids=[run['id']], artifact_ids=[artifact['id']]))
    assert entry['run_references'][0]['state_at_link'] == 'succeeded'
    assert entry['run_references'][0]['result_artifacts']
    assert entry['artifact_references'][0]['sha256'] == artifact['sha256']
    response = f.http.put(f.prefix+'/studies/'+study['id'], json={'study': protocol(name='Updated protocol'), 'expected_revision': 1})
    assert response.status_code == 200, response.text
    path = f.prefix+'/studies/'+study['id']
    reloaded = f.http.get(path).json()
    assert reloaded['revision'] == 2 and reloaded['summary']['records'] == 1
    assert f.http.get(path, params={'revision': 1}).json()['study']['name'] == protocol()['name']
    assert f.http.get(path+'/records').json()['records'][0] == entry
    assert len(f.http.get(path+'/revisions').json()) == 2
    manifest = f.http.get(path+'/manifest').json()
    downloaded = f.http.get(manifest['export_path'].replace('/api/lab', f.prefix, 1))
    assert downloaded.status_code == 200, downloaded.text
    assert hashlib.sha256(downloaded.content).hexdigest() == downloaded.headers['x-content-sha256']
    documents = [json.loads(line) for line in downloaded.content.splitlines()]
    assert documents[0]['external_evidence_bytes'] is False
    assert documents[-1]['records'] == 1 and documents[-1]['protocol_revisions'] == 2
    assert entry['digest'] in downloaded.text
    assert 'storage_key' not in downloaded.text
    for suffix in ('', '/records', '/revisions', '/manifest', '/export'):
        assert f.http.get(f.restored+'/studies/'+study['id']+suffix).status_code == 404
    # Target-only credentials cannot read project study records.
    with httpx.Client(base_url=f.url, headers={'Authorization':'Bearer '+target['token']}) as target_client:
        assert target_client.get(path).status_code == 401
    # New run associations verify current object bytes, even if old associations
    # remain readable as historical receipts after external damage.
    from tinystudio.lab.models import LabArtifact
    with f.factory() as db:
        stored=db.get(LabArtifact,artifact['id'])
        retained=f.store.root/stored.storage_key
    retained.write_bytes(b'CORRUPT BYTES')
    bad=f.http.post(path+'/records',json=record(key='corrupt-run-source',run_ids=[run['id']]))
    assert bad.status_code==409,bad.text
    assert f.http.get(path).json()['last_sequence']==1


def test_http_record_references_survive_cancelled_run_retry(native_http):
    f = native_http
    target, runner = enroll(f, 'retry-study')
    try:
        saved = posted(f.http, f.prefix+'/definitions', json={'definition': {
            'name':'Cancelled fixture','engine':'synthetic.signal','parameters':{'samples':1},
            'execution':{'profile':'trusted_native'}}})
        run = posted(f.http, f.prefix+'/runs', json={'definition_id': saved['id'], 'revision':1,
            'target_id':target['id'],'idempotency_key':'failed-source'})
        response = f.http.post(f.prefix+'/runs/'+run['id']+'/cancel', json={})
        assert response.status_code == 200 and response.json()['state'] == 'cancelled'
        study = posted(f.http, f.prefix+'/studies', json={'study': protocol(), 'idempotency_key':'retry-study'})
        entry = posted(f.http, f.prefix+'/studies/'+study['id']+'/records', json=record(kind='cancelled-run',run_ids=[run['id']]))
        assert entry['run_references'][0]['state_at_link'] == 'cancelled'
        retried = posted(f.http, f.prefix+'/runs/'+run['id']+'/retry', json={})
        assert retried['state'] == 'queued'
        retained = f.http.get(f.prefix+'/studies/'+study['id']+'/records').json()['records'][0]
        assert retained == entry and retained['run_references'][0]['state_at_link'] == 'cancelled'
    finally:
        runner.client.close()


def test_http_preserves_execution_failure_diagnostics_after_retry(native_http):
    f = native_http
    target, runner = enroll(f, 'failed-study')
    try:
        saved = posted(f.http, f.prefix+'/definitions', json={'definition': {
            'name':'Failing fixture','engine':'synthetic.signal','parameters':{'samples':1,'dt':0},
            'execution':{'profile':'trusted_native'}}})
        run = posted(f.http, f.prefix+'/runs', json={'definition_id':saved['id'],'revision':1,
            'target_id':target['id'],'idempotency_key':'execution-failure'})
        assert runner.run_once(run_id=run['id'])
        failed = f.http.get(f.prefix+'/runs/'+run['id']).json()
        assert failed['state'] == 'failed' and failed['error']
        study = posted(f.http, f.prefix+'/studies', json={'study':protocol(),'idempotency_key':'failed-study'})
        entry = posted(f.http, f.prefix+'/studies/'+study['id']+'/records', json=record(kind='execution-error',run_ids=[run['id']]))
        assert entry['run_references'][0]['error_at_link'] == failed['error']
        assert entry['run_references'][0]['outcomes']
        posted(f.http, f.prefix+'/runs/'+run['id']+'/retry', json={})
        assert f.http.get(f.prefix+'/studies/'+study['id']+'/records').json()['records'][0] == entry
    finally:
        runner.client.close()


def test_http_conflicts_and_bad_inputs_leave_protocol_and_sequence_unchanged(native_http):
    f = native_http
    study = posted(f.http, f.prefix+'/studies', json={'study':protocol(),'idempotency_key':'conflicts'})
    path = f.prefix+'/studies/'+study['id']
    assert f.http.put(path,json={'study':protocol(name='overwrite'),'expected_revision':0}).status_code in {409,422}
    assert f.http.post(path+'/records',json=record(artifact_ids=['foreign-or-missing'])).status_code == 404
    assert f.http.post(path+'/records',json=record(revision=True)).status_code == 422
    current = f.http.get(path).json()
    assert current['revision'] == 1 and current['last_sequence'] == 0
    assert f.http.get(f.prefix+'/studies').json()[0]['id'] == study['id']


def test_shared_study_tools_are_thin_scoped_http_clients():
    calls = []
    class Client:
        def call(self, method, path, **kwargs):
            calls.append((method,path,kwargs)); return {'accepted':True}
    tools = {tool.name:tool for tool in tool_specs(Client(), ToolSpec)}
    expected = {'lab_studies','lab_study','lab_study_save','lab_study_revisions','lab_study_records','lab_study_record','lab_study_manifest'}
    assert expected <= tools.keys(), 'study tools are missing'
    tools['lab_studies'].func()
    tools['lab_study_save'].func(protocol(), 'create-key')
    tools['lab_study_save'].func(protocol(), 'unused', study_id='study-1', expected_revision=3)
    tools['lab_study'].func('study-1', revision=2)
    tools['lab_study_record'].func('study-1', record())
    tools['lab_study_records'].func('study-1', after=5)
    tools['lab_study_manifest'].func('study-1')
    assert calls[1] == ('POST','/api/lab/studies',{'json':{'study':protocol(),'idempotency_key':'create-key'}})
    assert calls[2] == ('PUT','/api/lab/studies/study-1',{'json':{'study':protocol(),'expected_revision':3}})
    assert calls[3][1] == '/api/lab/studies/study-1?revision=2'
    assert calls[-1][1] == '/api/lab/studies/study-1/manifest'
    assert tools['lab_study_manifest'].read_only and not tools['lab_study_record'].read_only


def test_study_list_tool_can_reach_studies_beyond_the_default_page():
    calls=[]
    class Client:
        def call(self,method,path,**kwargs):
            calls.append((method,path,kwargs));return []
    tools={tool.name:tool for tool in tool_specs(Client(),ToolSpec)}
    tools['lab_studies'].func(offset=100,limit=100)
    assert calls==[('GET','/api/lab/studies?offset=100&limit=100',{})]


def test_cli_can_save_and_inspect_study_protocol(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(cli,'call',lambda *args: calls.append(args) or {'id':'study','revision':1})
    source=tmp_path/'protocol.json';source.write_text(json.dumps(protocol()))
    runner=CliRunner()
    saved=runner.invoke(app,['lab','study-save',str(source),'--project','research','--idempotency-key','fixed'])
    assert saved.exit_code==0,saved.output
    assert calls[-1] == ('POST','research','/studies',{'study':protocol(),'idempotency_key':'fixed'})
    inspected=runner.invoke(app,['lab','study-inspect','study','--project','research','--revision','1'])
    assert inspected.exit_code==0,inspected.output
    assert calls[-1] == ('GET','research','/studies/study?revision=1')


def test_cli_download_retains_exact_tracking_bytes_and_refuses_wrong_checksum(tmp_path,monkeypatch):
    from contextlib import contextmanager
    content=b'{"kind":"manifest"}\n'
    wanted=hashlib.sha256(content).hexdigest()
    def factory(sha):
        @contextmanager
        def client():
            with httpx.Client(base_url='http://fixture',transport=httpx.MockTransport(
                lambda _:httpx.Response(200,content=content,headers={'x-content-sha256':sha}))) as http:
                yield http
        return client
    monkeypatch.setattr(cli,'client',factory(wanted))
    output=tmp_path/'study.jsonl'
    result=CliRunner().invoke(app,['lab','study-export','study-1',str(output),'--project','research'])
    assert result.exit_code==0,result.output
    assert output.read_bytes()==content and not output.with_suffix('.jsonl.partial').exists()
    monkeypatch.setattr(cli,'client',factory('0'*64))
    bad=tmp_path/'bad.jsonl'
    result=CliRunner().invoke(app,['lab','study-export','study-1',str(bad),'--project','research'])
    assert result.exit_code != 0
    assert not bad.exists() and not bad.with_suffix('.jsonl.partial').exists()
