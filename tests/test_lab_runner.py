"""Runner boundary counterexamples; fake transport, real child, no databases."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

import httpx
import pytest
from fastapi import UploadFile

from tinystudio.lab.contracts import checksum
from tinystudio.lab.execution import execute
from tinystudio.lab import runner as module
from tinystudio.lab.runner import ImplementationSnapshot, ProcessGroup, Runner


def sha(content):
    return hashlib.sha256(content).hexdigest()


def manifest(source):
    return {"version":1,"files":{path.relative_to(source).as_posix():sha(path.read_bytes()) for path in source.rglob('*.py')}}


@pytest.fixture
def source(tmp_path):
    package = tmp_path / 'source' / 'tinystudio'
    (package / 'lab').mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / 'settings.py').write_text('')
    (package / 'lab' / '__init__.py').write_text('')
    (package / 'lab' / 'execution.py').write_bytes(Path(module.__file__).with_name('execution.py').read_bytes())
    (package / 'lab' / 'contracts.py').write_text('''import json, hashlib
def checksum(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
''')
    (package / 'lab' / 'service.py').write_text('def runtime_versions():\n    return {"fixture-runtime":"1"}\n')
    (package / 'lab' / 'engine.py').write_text('''import os, json, hashlib, time
from pathlib import Path
def execute(spec,output,emit,cancelled):
    spec["definition"]["parameters"]["child_modified_copy"] = True
    if spec["definition"]["parameters"].get("pause"):
        time.sleep(.12)
    emit({"event":"fixture_progress"})
    target=output/"record.json"
    payload={"marker":"original", "input_paths":spec.get("input_paths"),
             "grants":spec.get("_runtime_grants"), "secrets":spec.get("_runtime_secrets"),
             "env":{key:os.environ.get(key) for key in ("TINYSTUDIO_SKIP_DOTENV","OPENAI_API_KEY","TINYSTUDIO_TARGET_TOKEN","PYTHONPATH","HOME")}}
    target.write_text(json.dumps(payload))
    return {"records":1,"artifacts":[{"path":str(target),"name":target.name,"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"role":"evidence"}]}
''')
    return package


def attempt(source, *, payload=None, runtime=None):
    refs = [] if payload is None else [{"artifact_id":"input-one","sha256":sha(payload)}]
    spec = {"schema_version":1,"definition":{"name":"Runner fixture","engine":"synthetic.signal","parameters":{},"inputs":refs,
            "execution":{"profile":"trusted_native","backend":"native","max_seconds":10,"max_output_bytes":1024**2}},
            "implementation":manifest(source),"runtime":runtime or {"fixture-runtime":"1"},
            "input_manifest":[] if payload is None else [{"id":"input-one","name":"source.jsonl","sha256":sha(payload),"size":len(payload)}]}
    return {"attempt_id":"attempt-one","lease_token":"never-child-secret","spec":spec,"spec_digest":checksum(spec)}


class SlowStream:
    def __init__(self, client):
        self.client = client
    def __enter__(self):
        self.client.downloading.set()
        return self
    def __exit__(self, *args):
        self.client.downloading.clear()
    def raise_for_status(self):
        pass
    def iter_bytes(self):
        for block in self.client.blocks:
            time.sleep(self.client.transfer_delay)
            self.client.blocks_received += 1
            yield block


class Transport:
    def __init__(self, *, blocks=(), transfer_delay=0, cancel_download=False, cancel_upload=False, fail_heartbeat=False):
        self.blocks = blocks
        self.transfer_delay = transfer_delay
        self.cancel_download = cancel_download
        self.cancel_upload = cancel_upload
        self.fail_heartbeat = fail_heartbeat
        self.downloading = threading.Event()
        self.uploading = threading.Event()
        self.beats = []
        self.finish = None
        self.uploads = []
        self.upload_params = []
        self.blocks_received = 0

    def stream(self, *args, **kwargs):
        return SlowStream(self)

    def post(self, path, *, json=None, files=None, params=None, headers=None):
        if path.endswith('/heartbeat'):
            self.beats.append((self.downloading.is_set(),self.uploading.is_set()))
            if self.fail_heartbeat and self.downloading.is_set():
                raise httpx.ConnectError('controlled lease transport failure')
            body={'cancel_requested':(self.cancel_download and self.downloading.is_set()) or (self.cancel_upload and self.uploading.is_set())}
        elif path.endswith('/finish'):
            self.finish=copy.deepcopy(json)
            body={}
        elif path.endswith('/artifacts'):
            self.uploading.set()
            self.upload_params.append(params)
            try:
                data=b''
                while True:
                    time.sleep(self.transfer_delay)
                    block=files['file'][1].read(8)
                    if not block:break
                    data+=block
                self.uploads.append(data)
                body={'id':'published-'+str(len(self.uploads)), 'name':files['file'][0], 'sha256':sha(data),'size':len(data),'visibility':params.get('visibility','model')}
            finally:
                self.uploading.clear()
        else:
            raise AssertionError(path)
        response=httpx.Response(200,json=body,request=httpx.Request('POST','http://localhost'+path))
        return response


def runner(source,client,monkeypatch):
    monkeypatch.setattr(module,'runtime_versions',lambda:{'fixture-runtime':'1'})
    return Runner('http://localhost','fixture','token',client=client,source_root=source,heartbeat_interval=.01,process_grace=.15)


def test_frozen_spec_is_unchanged_and_child_executes_only_snapshot_without_credentials(source,tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','parent-only-key')
    monkeypatch.setenv('TINYSTUDIO_TARGET_TOKEN','parent-only-target-token')
    claimed=attempt(source,payload=b'{"time":0,"value":-0.125}\n')
    original=copy.deepcopy(claimed)
    transport=Transport(blocks=[b'{"time":0,"value":-0.125}\n'])
    root=tmp_path/'attempt';root.mkdir()
    runner(source,transport,monkeypatch).execute_attempt(claimed,root)
    assert claimed == original
    assert json.loads((root/'spec.json').read_text()) == claimed['spec']
    assert 'input_paths' not in claimed['spec']
    assert transport.finish['state']=='succeeded',transport.finish
    record=json.loads(transport.uploads[0])
    assert record['env']['OPENAI_API_KEY'] is None
    assert record['env']['TINYSTUDIO_TARGET_TOKEN'] is None
    assert record['env']['TINYSTUDIO_SKIP_DOTENV']=='1'
    assert record['env']['PYTHONPATH']==str(root/'runtime')
    assert record['env']['HOME']==str(root/'work/home')
    assert record['input_paths']['input-one'].endswith('input-one.jsonl')
    assert record['grants']==record['secrets']=={}
    assert (root/'runtime/tinystudio/lab/engine.py').stat().st_mode & 0o222 == 0
    assert transport.finish['result']['execution_receipt']['implementation_digest']==checksum(claimed['spec']['implementation'])


def test_source_mutation_after_claim_fails_before_execution(source,tmp_path,monkeypatch):
    claimed=attempt(source)
    (source/'lab/engine.py').write_text('raise RuntimeError("changed after claim")')
    transport=Transport();root=tmp_path/'attempt';root.mkdir()
    runner(source,transport,monkeypatch).execute_attempt(claimed,root)
    assert transport.finish['state']=='failed'
    assert 'source changed after claim' in transport.finish['error']
    assert not transport.uploads


def test_deployment_after_snapshot_cannot_change_executed_source(source,tmp_path,monkeypatch):
    claimed=attempt(source)
    original=Runner._download
    def mutate_after_snapshot(self,*args,**kwargs):
        (source/'lab/engine.py').write_text('raise RuntimeError("later deployment")')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Runner,'_download',mutate_after_snapshot)
    transport=Transport();root=tmp_path/'attempt';root.mkdir()
    runner(source,transport,monkeypatch).execute_attempt(claimed,root)
    assert transport.finish['state']=='succeeded',transport.finish
    assert json.loads(transport.uploads[0])['marker']=='original'


def test_child_rechecks_actual_runtime_and_parent_rejects_runtime_mismatch(source,tmp_path,monkeypatch):
    claimed=attempt(source,runtime={'fixture-runtime':'different'})
    # Parent comparison is intentionally controlled to pass so the child check is tested.
    monkeypatch.setattr(module,'runtime_versions',lambda:{'fixture-runtime':'different'})
    transport=Transport();root=tmp_path/'attempt';root.mkdir()
    agent=Runner('http://localhost','fixture','token',client=transport,source_root=source,heartbeat_interval=.01,process_grace=.1)
    agent.execute_attempt(claimed,root)
    assert transport.finish['state']=='failed'
    assert 'executing runtime differs' in transport.finish['error']
    assert not transport.uploads


def test_heartbeat_renews_during_slow_download_and_upload(source,tmp_path,monkeypatch):
    payload=b'abcdefgh'
    transport=Transport(blocks=[bytes([byte]) for byte in payload],transfer_delay=.025)
    root=tmp_path/'attempt';root.mkdir()
    runner(source,transport,monkeypatch).execute_attempt(attempt(source,payload=payload),root)
    assert transport.finish['state']=='succeeded',transport.finish
    assert sum(download for download,_ in transport.beats)>=3
    assert sum(upload for _,upload in transport.beats)>=3


@pytest.mark.parametrize('phase',['download','upload'])
def test_cancellation_stops_transfer_without_publishing_success(source,tmp_path,monkeypatch,phase):
    payload=b'0123456789'
    transport=Transport(blocks=[bytes([byte]) for byte in payload],transfer_delay=.025,cancel_download=phase=='download',cancel_upload=phase=='upload')
    root=tmp_path/'attempt';root.mkdir()
    runner(source,transport,monkeypatch).execute_attempt(attempt(source,payload=payload),root)
    assert transport.finish['state']=='cancelled',transport.finish
    assert transport.finish['result'] is None
    assert not transport.uploads
    if phase=='download':assert transport.blocks_received<len(payload)


def test_lost_lease_stops_transfer_and_does_not_send_authoritative_finish(source,tmp_path,monkeypatch):
    payload=b'0123456789'
    transport=Transport(blocks=[bytes([byte]) for byte in payload],transfer_delay=.025,fail_heartbeat=True)
    root=tmp_path/'attempt';root.mkdir()
    with pytest.raises(RuntimeError,match='lost lease connectivity'):
        runner(source,transport,monkeypatch).execute_attempt(attempt(source,payload=payload),root)
    assert transport.finish is None
    assert transport.blocks_received<len(payload)


@pytest.mark.parametrize('name',['../outside.py','/absolute.py','lab/../../outside.py','lab/engine.py/../other.py','lab\\engine.py','lab/engine.txt'])
def test_snapshot_rejects_unsafe_manifest_paths(source,tmp_path,name):
    invalid=manifest(source);invalid['files'][name]='0'*64
    with pytest.raises(ValueError,match='unsafe'):
        ImplementationSnapshot(source,tmp_path/'frozen/tinystudio',invalid).create()


def test_snapshot_hash_verification_detects_post_execution_mutation(source,tmp_path):
    frozen=ImplementationSnapshot(source,tmp_path/'frozen/tinystudio',manifest(source)).create()
    path=frozen.root/'lab/engine.py';path.chmod(0o644);path.write_text('# tampered')
    with pytest.raises(ValueError,match='checksum'):
        frozen.verify()


def test_process_group_kills_descendant_that_outlives_its_parent(tmp_path,monkeypatch):
    pidfile=tmp_path/'descendant.pid'
    ready=tmp_path/'descendant.ready'
    child=f'import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path({str(ready)!r}).touch(); time.sleep(30)'
    leader=f'import subprocess,sys,pathlib,time; p=subprocess.Popen([sys.executable,"-c",{child!r}]); pathlib.Path({str(pidfile)!r}).write_text(str(p.pid));\nwhile not pathlib.Path({str(ready)!r}).exists(): time.sleep(.01)'
    original=os.killpg;sent=[]
    def observe(group,sig):
        if sig:sent.append(sig)
        return original(group,sig)
    monkeypatch.setattr(os,'killpg',observe)
    group=ProcessGroup([sys.executable,'-c',leader],grace=.2,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        group.process.wait(timeout=3)
        pid=int(pidfile.read_text())
        os.kill(pid,0)
        group.close()
        with pytest.raises(ProcessLookupError):os.kill(pid,0)
        assert group.closed
        assert signal.SIGTERM in sent and signal.SIGKILL in sent
    finally:
        try:os.killpg(group.pgid,signal.SIGKILL)
        except (ProcessLookupError,PermissionError):pass


def test_cleanup_failure_is_not_silently_qualified(source,tmp_path,monkeypatch):
    original=ProcessGroup.close
    def unknown_cleanup(self):
        original(self)
        raise RuntimeError('controlled cleanup uncertainty')
    monkeypatch.setattr(ProcessGroup,'close',unknown_cleanup)
    transport=Transport();root=tmp_path/'attempt';root.mkdir()
    runner(source,transport,monkeypatch).execute_attempt(attempt(source),root)
    assert transport.finish['state']=='failed'
    assert 'cleanup unconfirmed' in transport.finish['error']
    assert not transport.uploads


def test_dispatch_never_promotes_authored_grants_and_does_not_mutate_input(monkeypatch,tmp_path):
    from tinystudio.lab import engine
    observed={}
    def fixture(view,*args):
        observed.update(view)
        view['definition']['parameters']['mutated']=True
        return {'artifacts':[]}
    monkeypatch.setattr(engine,'execute',fixture)
    spec={'definition':{'engine':'synthetic.signal','parameters':{}},'_runtime_grants':{'native_execution':True},'_runtime_secrets':{'token':'authored'}}
    original=copy.deepcopy(spec)
    execute(spec,tmp_path,lambda _:None,lambda:False)
    assert spec==original
    assert observed['_runtime_grants']==observed['_runtime_secrets']=={}


def test_parent_rejects_a_mismatched_child_identity_receipt(source,tmp_path,monkeypatch):
    original=ProcessGroup.close
    def alter_receipt(self):
        original(self)
        path=tmp_path/'attempt/work/runtime-receipt.json'
        if path.exists():
            value=json.loads(path.read_text());value['implementation_digest']='f'*64
            path.write_text(json.dumps(value))
    monkeypatch.setattr(ProcessGroup,'close',alter_receipt)
    transport=Transport();root=tmp_path/'attempt';root.mkdir()
    runner(source,transport,monkeypatch).execute_attempt(attempt(source),root)
    assert transport.finish['state']=='failed'
    assert 'identity receipt mismatch' in transport.finish['error']
    assert not transport.uploads


def test_http_testclient_transport_supports_heartbeat_and_real_multipart(source,tmp_path,monkeypatch):
    from fastapi import FastAPI, UploadFile
    from fastapi.testclient import TestClient
    app=FastAPI()
    prefix='/api/lab/runner/fixture/attempts/attempt-one'
    finished=[];uploaded=[];beats=[]
    @app.post(prefix+'/heartbeat')
    def heartbeat(body:dict):
        beats.append(body)
        return {'cancel_requested':False}
    @app.post(prefix+'/artifacts')
    async def artifact(file:UploadFile,visibility:str="model"):
        data=await file.read();uploaded.append(data)
        return {'id':'http-artifact','name':file.filename,'sha256':sha(data),'size':len(data),'visibility':visibility}
    @app.post(prefix+'/finish')
    def finish(body:dict):
        finished.append(body);return {}
    root=tmp_path/'attempt';root.mkdir()
    with TestClient(app) as client:
        runner(source,client,monkeypatch).execute_attempt(attempt(source),root)
    assert finished[0]['state']=='succeeded',finished
    assert uploaded and beats
    assert finished[0]['result']['artifacts'][0]['id']=='http-artifact'


def test_actual_package_engine_runs_from_complete_snapshot_without_database(tmp_path):
    import tinystudio
    source=Path(tinystudio.__file__).resolve().parent
    claimed=attempt(source,runtime=module.runtime_versions())
    claimed['spec']['definition']['parameters']={'samples':3,'dt':.25}
    claimed['spec_digest']=checksum(claimed['spec'])
    transport=Transport();root=tmp_path/'attempt';root.mkdir()
    agent=Runner('http://localhost','fixture','token',client=transport,source_root=source,heartbeat_interval=.02,process_grace=.2)
    agent.execute_attempt(claimed,root)
    assert transport.finish['state']=='succeeded',transport.finish
    assert transport.finish['result']['records']==3
    assert {item['name'] for item in transport.finish['result']['artifacts']} >= {'raw.jsonl','processed.jsonl','raw.parquet','processed.parquet'}


def test_permission_to_probe_group_is_not_mistaken_for_confirmed_cleanup(monkeypatch):
    group=object.__new__(ProcessGroup)
    group.pgid=12345
    def denied(*args):raise PermissionError('controlled missing visibility')
    monkeypatch.setattr(os,'killpg',denied)
    assert group._exists() is True


def isolated_native_config():
    return {'schema_version':1,'backends':{'native':{'enabled':True,'profiles':['isolated'],'runtime_read_paths':[str(Path(sys.prefix)),str(Path(sys.base_prefix))]}}}


def test_profile_capabilities_are_explicit_and_never_reuse_host_runtime_for_container():
    default=module.capabilities()
    assert default['profiles_by_backend']=={'native':['trusted_native']}
    assert default['runtime_by_backend']=={'native':default['runtime']}
    with pytest.raises(ValueError,match='operator runtime evidence'):
        module.capabilities({'schema_version':1,'backends':{'container':{'enabled':True,'profiles':['isolated'],'image':'image@sha256:'+'a'*64}}})
    identity={'image':'image@sha256:'+'a'*64,'runtime':{'python':'3.12.13','os':'Linux','machine':'aarch64'}}
    config={'schema_version':1,'backends':{'container':{'enabled':True,'profiles':['isolated'],'image':identity['image'],'runtime_evidence':{**identity,'sha256':checksum(identity)}}}}
    capability=module.capabilities(config)
    assert capability['runtime_by_backend']['container']==identity['runtime']
    assert capability['profiles_by_backend']['container']==['isolated']
    config['backends']['container']['runtime_evidence']['runtime']['os']='Darwin'
    with pytest.raises(ValueError,match='operator runtime evidence'):module.capabilities(config)


def test_only_exact_engine_operator_grants_and_secrets_enter_context(source,tmp_path,monkeypatch):
    claimed=attempt(source)
    claimed['spec']['definition']['execution']['grants']={'native_execution':True}
    claimed['spec_digest']=checksum(claimed['spec'])
    config={'schema_version':1,'engine_grants':{'other.engine':{'native_execution':True}},'engine_secrets':{'other.engine':{'fixture':'unrelated'}}}
    transport=Transport();root=tmp_path/'attempt';root.mkdir()
    agent=runner(source,transport,monkeypatch);agent._profile_config=config
    agent.execute_attempt(claimed,root)
    assert transport.finish['state']=='succeeded'
    record=json.loads(transport.uploads[0]);assert record['grants']==record['secrets']=={}
    manifest=transport.finish['result']['execution_receipt']['profile_manifest']
    assert 'unrelated' not in json.dumps(manifest)
    assert manifest['qualification']=='trusted_native_not_isolated'


@pytest.mark.skipif(sys.platform!='darwin',reason='isolated-native full workflow requires macOS')
def test_actual_isolated_native_signal_workflow_is_bound_to_snapshot(tmp_path):
    import tinystudio
    source=Path(tinystudio.__file__).resolve().parent
    claimed=attempt(source,runtime=module.runtime_versions())
    claimed['spec']['definition']['parameters']={'samples':3,'dt':.25}
    claimed['spec']['definition']['execution']['profile']='isolated'
    claimed['spec_digest']=checksum(claimed['spec'])
    original=copy.deepcopy(claimed)
    transport=Transport();root=tmp_path/'attempt';root.mkdir()
    agent=Runner('http://localhost','fixture','target-token-not-child',client=transport,source_root=source,heartbeat_interval=.02,process_grace=.2,profile_config=isolated_native_config())
    agent.execute_attempt(claimed,root)
    if transport.finish['state']=='failed' and ('sandbox_apply: Operation not permitted' in transport.finish['error'] or 'sandbox_init: Operation not permitted' in transport.finish['error']):
        pytest.skip('outer sandbox blocks nested sandbox-exec; isolated full workflow remains unqualified in this context')
    assert transport.finish['state']=='succeeded',transport.finish
    assert claimed==original
    assert transport.finish['result']['records']==3
    receipt=transport.finish['result']['execution_receipt']
    assert receipt['profile_manifest']['backend']=='native'
    assert receipt['profile_manifest']['profile']=='isolated'
    assert 'target-token-not-child' not in json.dumps(receipt)
    assert (root/'work/evidence/raw.parquet').exists()
    assert (root/'work/runtime-receipt.json').exists()
    assert (root/'work/result.json').exists()
    assert not (root/'output').exists()


def test_output_visibility_is_forwarded_and_server_metadata_is_retained(source,tmp_path,monkeypatch):
    engine=source/'lab/engine.py'
    engine.write_text(engine.read_text().replace('"role":"evidence"','"role":"evidence","visibility":"truth"'))
    transport=Transport();root=tmp_path/'attempt';root.mkdir()
    runner(source,transport,monkeypatch).execute_attempt(attempt(source),root)
    assert transport.finish['state']=='succeeded',transport.finish
    assert transport.upload_params[0]['visibility']=='truth'
    assert transport.finish['result']['artifacts'][0]['visibility']=='truth'


def test_authored_isolated_profile_without_operator_config_cannot_fall_back(source,tmp_path,monkeypatch):
    claimed=attempt(source)
    claimed['spec']['definition']['execution']['profile']='isolated'
    claimed['spec_digest']=checksum(claimed['spec'])
    transport=Transport();root=tmp_path/'attempt';root.mkdir()
    runner(source,transport,monkeypatch).execute_attempt(claimed,root)
    assert transport.finish['state']=='failed'
    assert 'not enabled by operator' in transport.finish['error']
    assert not transport.uploads


class ClaimLoopStopped(Exception):
    pass


def claim_loop_fixture(monkeypatch, actions):
    calls, sleeps, executed = [], [], []
    queue = iter(actions)
    def handle(request):
        calls.append(request.url.path)
        assert request.url.path.endswith('/claim')
        item = next(queue, ClaimLoopStopped())
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, int):
            return httpx.Response(item, text='credential-and-server-payload-must-not-be-logged')
        return httpx.Response(200, json={'attempt': item})
    client = httpx.Client(base_url='https://private-host-must-not-be-logged.invalid', transport=httpx.MockTransport(handle))
    instance = module.Runner('https://unused.invalid', 'target', 'target-secret-must-not-be-logged', client=client)
    def execute(attempt, root):
        assert root.is_dir()
        executed.append(attempt['attempt_id'])
    instance.execute_attempt = execute
    monkeypatch.setattr(module, 'Runner', lambda *args, **kwargs: instance)
    monkeypatch.setattr(module, 'capabilities', lambda *args: {})
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: sleeps.append(seconds))
    return instance, calls, sleeps, executed


def claimed_attempt():
    return {'attempt_id': 'attempt-once', 'lease_token': 'lease-secret',
            'spec': {'definition': {'execution': {'backend': 'native'}}}}


def test_claim_loop_recovers_502_then_executes_received_authority_once(monkeypatch, caplog):
    instance, calls, sleeps, executed = claim_loop_fixture(monkeypatch, [502, claimed_attempt()])
    with pytest.raises(ClaimLoopStopped):
        module.run('https://unused.invalid', 'target', 'target-secret-must-not-be-logged')
    assert executed == ['attempt-once']
    assert sleeps == [1.0]
    assert len(calls) == 3 and all(path.endswith('/claim') for path in calls)
    assert instance.client.is_closed
    assert 'HTTP 502' in caplog.text
    assert not any(secret in caplog.text for secret in ('credential-and-server-payload', 'private-host', 'target-secret', 'lease-secret'))


def test_claim_loop_bounds_backoff_and_resets_after_clean_empty_claim(monkeypatch):
    instance, calls, sleeps, executed = claim_loop_fixture(monkeypatch, [502]*7 + [None, 502])
    with pytest.raises(ClaimLoopStopped):
        module.run('https://unused.invalid', 'target', 'secret', poll_seconds=2)
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 2, 1.0]
    assert executed == [] and len(calls) == 10
    assert instance.client.is_closed


@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
def test_claim_loop_retries_only_selected_transient_statuses(monkeypatch, status):
    _, calls, sleeps, executed = claim_loop_fixture(monkeypatch, [status, None])
    with pytest.raises(ClaimLoopStopped):
        module.run('https://unused.invalid', 'target', 'secret')
    assert sleeps == [1.0, 2] and executed == [] and len(calls) == 3


@pytest.mark.parametrize('status', [400, 401, 403, 404, 409, 501, 505])
def test_claim_loop_auth_conflict_and_other_statuses_fail_closed(monkeypatch, status):
    instance, calls, sleeps, executed = claim_loop_fixture(monkeypatch, [status])
    with pytest.raises(httpx.HTTPStatusError) as failure:
        module.run('https://unused.invalid', 'target', 'secret')
    assert failure.value.response.status_code == status
    assert sleeps == [] and executed == [] and len(calls) == 1
    assert instance.client.is_closed


@pytest.mark.parametrize('error_type', [httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.ProxyError])
def test_claim_loop_reconnects_transient_transport_without_replaying_unknown_attempt(monkeypatch, error_type):
    instance, calls, sleeps, executed = claim_loop_fixture(monkeypatch, [error_type('lost claim response'), None])
    with pytest.raises(ClaimLoopStopped):
        module.run('https://unused.invalid', 'target', 'secret')
    # A lost reply conveys no attempt authority; a later empty claim never causes
    # execution, finish, cancellation or automatic run retry.
    assert executed == [] and all(path.endswith('/claim') for path in calls)
    assert sleeps == [1.0, 2] and instance.client.is_closed


@pytest.mark.parametrize('error_type', [httpx.LocalProtocolError, httpx.UnsupportedProtocol])
def test_claim_loop_does_not_treat_local_configuration_as_transient(monkeypatch, error_type):
    error = error_type('invalid local protocol')
    instance, calls, sleeps, executed = claim_loop_fixture(monkeypatch, [error])
    with pytest.raises(error_type) as failure:
        module.run('https://unused.invalid', 'target', 'secret')
    assert failure.value is error and sleeps == [] and executed == [] and len(calls) == 1
    assert instance.client.is_closed


@pytest.mark.parametrize('failure', [502, httpx.ReadTimeout('ambiguous claim reply')])
def test_claim_loop_once_preserves_original_failure_and_never_sleeps(monkeypatch, failure):
    instance, calls, sleeps, executed = claim_loop_fixture(monkeypatch, [failure])
    expected = httpx.HTTPStatusError if isinstance(failure, int) else type(failure)
    with pytest.raises(expected) as caught:
        module.run('https://unused.invalid', 'target', 'secret', once=True)
    if isinstance(failure, int): assert caught.value.response.status_code == failure
    else: assert caught.value is failure
    assert sleeps == [] and executed == [] and len(calls) == 1
    assert instance.client.is_closed


@pytest.mark.parametrize('phase', ['execution', 'artifact', 'finish'])
def test_claim_loop_never_swallows_active_attempt_failures(monkeypatch, phase):
    instance, calls, sleeps, executed = claim_loop_fixture(monkeypatch, [claimed_attempt()])
    def active(attempt, root):
        if phase == 'execution':
            raise httpx.ReadTimeout('active execution transport failure')
        request = httpx.Request('POST', 'https://unused.invalid/'+phase)
        raise httpx.HTTPStatusError('active '+phase+' failed', request=request, response=httpx.Response(502, request=request))
    instance.execute_attempt = active
    expected = httpx.ReadTimeout if phase == 'execution' else httpx.HTTPStatusError
    with pytest.raises(expected):
        module.run('https://unused.invalid', 'target', 'secret')
    assert sleeps == [] and executed == [] and len(calls) == 1
    assert instance.client.is_closed


def test_claim_loop_capability_configuration_errors_remain_fail_fast(monkeypatch):
    instance, calls, sleeps, executed = claim_loop_fixture(monkeypatch, [])
    def invalid(*args): raise ValueError('operator profile invalid')
    monkeypatch.setattr(module, 'capabilities', invalid)
    with pytest.raises(ValueError, match='operator profile invalid'):
        module.run('https://unused.invalid', 'target', 'secret')
    assert calls == [] and sleeps == [] and executed == [] and instance.client.is_closed
