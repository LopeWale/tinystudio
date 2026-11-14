"""Owned child/control fixtures and real loopback, credential-free object I/O."""
from contextlib import contextmanager
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import threading
from urllib.parse import urlsplit

import httpx
import pytest

from tests.test_lab_runner import attempt, source
from tinystudio.lab import runner as module
from tinystudio.lab.contracts import checksum
from tinystudio.lab.evidence_spool import EvidenceSpool, SpoolPolicy
from tinystudio.lab.finalization_configuration import FinalizationApiPaths
from tinystudio.lab.runner import Runner
from tinystudio.lab.runner_publication import AttemptPublication, PublicationUnconfirmed, RunnerPublicationPaths
from tinystudio.lab.transfer_configuration import AttemptArtifactApiPaths, TransferPolicy


def linear(path):
    sha=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024**2):sha.update(block)
    return sha.hexdigest()


@contextmanager
def object_server(directory,input_path=None):
    state=type('Objects',(),{})()
    state.parts={};state.requests=[];state.lose_next_put=False
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):pass
        def do_GET(self):
            state.requests.append(('GET',dict(self.headers)))
            path=input_path
            assert path is not None
            offset=0
            if self.headers.get('Range'):
                offset=int(self.headers['Range'].split('=')[1].split('-')[0])
            size=path.stat().st_size
            self.send_response(206 if offset else 200)
            self.send_header('Content-Length',str(size-offset))
            if offset:self.send_header('Content-Range',f'bytes {offset}-{size-1}/{size}')
            self.end_headers()
            with path.open('rb') as stream:
                stream.seek(offset)
                while block:=stream.read(1024**2):self.wfile.write(block)
        def do_PUT(self):
            state.requests.append(('PUT',dict(self.headers)))
            session,number=urlsplit(self.path).path.split('/')[-2:]
            path=directory/(session+'-'+number)
            remaining=int(self.headers['Content-Length'])
            with path.open('wb') as stream:
                while remaining:
                    block=self.rfile.read(min(remaining,1024**2))
                    assert block
                    stream.write(block);remaining-=len(block)
            state.parts[(session,int(number))]=path
            if state.lose_next_put:
                state.lose_next_put=False
                self.close_connection=True
                self.connection.close()
                return
            self.send_response(200);self.send_header('ETag','"owned-part"')
            self.send_header('Content-Length','0');self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    state.url=f'http://127.0.0.1:{server.server_address[1]}'
    thread=threading.Thread(target=server.serve_forever,name='owned-object-http')
    thread.start()
    try:yield state
    finally:
        server.shutdown();server.server_close();thread.join(3)
        assert not thread.is_alive()


class NativeApi:
    """Independent immutable transfer inventory and terminal receipt emulator."""
    def __init__(self,claimed,objects):
        self.claimed=claimed;self.objects=objects;self.calls=[];self.sessions={};self.by_key={}
        self.paths=AttemptArtifactApiPaths(FinalizationApiPaths.for_runner('fixture',claimed['attempt_id']).base)
        self.final_paths=FinalizationApiPaths(self.paths.base)
        self.capability={'protocol_version':1,'finalization_protocol_version':1,'backend':'r2',
            'resumable':True,'direct_download':True,'policy':TransferPolicy().as_dict()}
        self.descriptor_change=lambda value:value
        self.capability_status=200;self.final_body=None;self.final_posts=0;self.complete_posts=0
        self.lose_final_response=False;self.verifier_failed=False;self.terminal=False
        self.lock=threading.RLock()

    def ticket(self):
        return {'id':'final-one','kind':'attempt_finalize','state':'succeeded',
            'payload_digest':checksum(self.final_body),'run_id':self.claimed['run_id'],
            'attempt_id':self.claimed['attempt_id'],'target_id':'fixture','spec_digest':self.claimed['spec_digest'],
            'result':{'accepted':True,'state':self.final_body['state']}}

    def row(self,session):return copy.deepcopy(self.sessions[session])

    def handle(self,request):
        with self.lock:return self._handle(request)

    def _handle(self,request):
        route=request.url.path;method=request.method
        self.calls.append((method,route,dict(request.headers)))
        def reply(body,status=200):return httpx.Response(status,json=body)
        if route.endswith('/claim'):return reply({'attempt':self.claimed})
        if route.endswith('/heartbeat'):
            return reply({'error':'terminal'},409) if self.terminal else reply({'cancel_requested':False,'lease_seconds':30})
        if route==self.paths.policy_route:return reply(self.capability,self.capability_status)
        if route==self.paths.input_download('input-one'):
            ref=self.claimed['spec']['input_manifest'][0]
            body={'mode':'direct','artifact':copy.deepcopy(ref),'url':self.objects.url+'/input?signed=do-not-retain'}
            return reply(self.descriptor_change(body))
        transfers=self.paths.transfers
        if route==transfers.collection and method=='POST':
            body=json.loads(request.content);key=body.pop('idempotency_key')
            if key in self.by_key:return reply(self.row(self.by_key[key]))
            session='transfer-'+str(len(self.sessions)+1);self.by_key[key]=session
            size=body['size'];part_bytes=16*1024**2
            self.sessions[session]={'id':session,'state':'admitted','intent':body,
                'plan':{'size':size,'part_bytes':part_bytes,'part_count':(size+part_bytes-1)//part_bytes},
                'operation_state':None,'artifact':None}
            return reply(self.row(session))
        for session in self.sessions:
            row=self.sessions[session]
            if route==transfers.initialize(session):
                row['state']='uploading';return reply(self.row(session))
            if route==transfers.parts(session) and method=='GET':
                return reply({'transfer':self.row(session),'parts':[{'number':number,'size':path.stat().st_size,'etag':'owned-part'}
                    for (selected,number),path in sorted(self.objects.parts.items()) if selected==session]})
            if route.startswith(transfers.parts(session)+'/'):
                number=int(route.rsplit('/',1)[1]);offset=(number-1)*row['plan']['part_bytes']
                size=min(row['plan']['part_bytes'],row['intent']['size']-offset)
                return reply({'url':self.objects.url+'/'+session+'/'+str(number)+'?signed=do-not-retain',
                    'headers':{'Content-Length':str(size)},'part_number':number,'offset':offset,'size':size})
            if route==transfers.complete(session):
                self.complete_posts+=1
                digest=hashlib.sha256();size=0
                for number in range(1,row['plan']['part_count']+1):
                    with self.objects.parts[(session,number)].open('rb') as stream:
                        while block:=stream.read(1024**2):digest.update(block);size+=len(block)
                assert size==row['intent']['size'] and digest.hexdigest()==row['intent']['sha256']
                row['state']='verifying';row['operation_state']='failed' if self.verifier_failed else 'running'
                return reply(self.row(session),202)
            if route==transfers.session(session):
                if row['state']=='verifying' and row['operation_state']=='running':
                    row.update(state='verified',operation_state='succeeded',artifact={'id':'artifact-'+session,**row['intent']})
                return reply(self.row(session))
        if route==self.final_paths.collection:
            self.final_posts+=1;body=json.loads(request.content)
            assert self.final_body is None or self.final_body==body
            self.final_body=body;self.terminal=True
            if self.lose_final_response:
                self.lose_final_response=False
                raise httpx.ReadError('owned lost admission response',request=request)
            return reply(self.ticket(),202)
        if route.startswith(self.final_paths.collection+'/by-digest/'):
            return reply(self.ticket()) if self.final_body is not None and route.endswith(checksum(self.final_body)) else reply({},404)
        if route==self.final_paths.legacy_finish:
            self.final_body=json.loads(request.content);return reply({'accepted':True,'state':self.final_body['state']})
        raise AssertionError((method,route))


def worker(source,api,monkeypatch,*,spool=None):
    monkeypatch.setattr(module,'runtime_versions',lambda:{'fixture-runtime':'1'})
    client=httpx.Client(base_url='http://localhost',transport=httpx.MockTransport(api.handle),
        headers={'Authorization':'Bearer target-secret','CF-Access-Client-Secret':'access-secret'},
        cookies={'studio':'cookie-secret'})
    profile={'schema_version':1,'backends':{}}
    if spool is not None:profile['evidence_spool']={'root':str(spool)}
    selected=Runner('http://localhost','fixture','unused',client=client,source_root=source,
        heartbeat_interval=.01,process_grace=.2,profile_config=profile)
    monkeypatch.setattr(selected,'_advertised_capabilities',lambda:{})
    return selected


def input_attempt(source,path):
    claimed=attempt(source)
    sha=linear(path);size=path.stat().st_size
    claimed['spec']['definition']['inputs']=[{'artifact_id':'input-one','sha256':sha}]
    claimed['spec']['input_manifest']=[{'id':'input-one','name':'input.bin','sha256':sha,'size':size}]
    claimed['spec_digest']=checksum(claimed['spec'])
    return claimed


def test_native_signed_input_and_direct_output_use_separate_object_credentials(source,tmp_path,monkeypatch):
    path=tmp_path/'input';path.write_bytes(b'owned frozen input')
    claimed=input_attempt(source,path)
    with object_server(tmp_path,path) as objects:
        api=NativeApi(claimed,objects);selected=worker(source,api,monkeypatch)
        root=tmp_path/'attempt';root.mkdir()
        selected.execute_attempt(claimed,root)
        assert api.final_body['state']=='succeeded',api.final_body
        assert (root/'inputs/input-one.bin').read_bytes()==path.read_bytes()
        assert (root/'inputs/input-one.bin').stat().st_mode&0o222==0
        assert {method for method,_ in objects.requests}=={'GET','PUT'}
        for _,headers in objects.requests:
            assert not {'authorization','cookie','cf-access-client-secret','x-lab-lease'}&{key.lower() for key in headers}
        journal=b''.join(path.read_bytes() for path in RunnerPublicationPaths(root).directory.iterdir() if path.is_file())
        for secret in (b'target-secret',b'access-secret',b'cookie-secret',b'never-child-secret',b'?signed=',b'http://127.0.0.1:'):
            assert secret not in journal
        assert all(path.stat().st_mode&0o777==0o600 for path in RunnerPublicationPaths(root).directory.iterdir() if path.is_file())


@pytest.mark.parametrize('field,value',[('id','other'),('sha256','f'*64),('size',100),('size',True)])
def test_signed_descriptor_mismatch_is_rejected_before_object_io(source,tmp_path,monkeypatch,field,value):
    path=tmp_path/'input';path.write_bytes(b'owned frozen input')
    claimed=input_attempt(source,path)
    with object_server(tmp_path,path) as objects:
        api=NativeApi(claimed,objects)
        def change(body):body['artifact'][field]=value;return body
        api.descriptor_change=change
        selected=worker(source,api,monkeypatch);root=tmp_path/'attempt';root.mkdir()
        selected.execute_attempt(claimed,root)
        assert api.final_body['state']=='failed'
        assert not objects.requests
        assert not (root/'work/evidence').exists()


def test_lost_part_ack_explicit_resume_reuses_inventory_and_never_executes_again(source,tmp_path,monkeypatch):
    claimed=attempt(source);spool=tmp_path/'retained'
    with object_server(tmp_path) as objects:
        objects.lose_next_put=True
        api=NativeApi(claimed,objects);selected=worker(source,api,monkeypatch,spool=spool)
        with pytest.raises(PublicationUnconfirmed,match='explicit resume'):selected.run_once()
        entry=EvidenceSpool(SpoolPolicy(True,spool)).open_attempt(EvidenceSpool(SpoolPolicy(True,spool)).list_attempts()[0]['key'])
        assert entry.snapshot()['manifest']['publication']=={'status':'unknown'}
        root=entry.execution_root
        assert (root/'work/evidence/record.json').is_file()
        assert RunnerPublicationPaths(root).transfer(0).is_file()
        assert not (root/'context.json').exists()
        writes=len(objects.requests)
        monkeypatch.setattr(module,'ProcessGroup',lambda *a,**kw:pytest.fail('resume cannot execute a child'))
        replacement=worker(source,api,monkeypatch,spool=spool)
        assert replacement.resume_publication(root,lease_token=claimed['lease_token'])=={'accepted':True,'state':'succeeded'}
        assert len(objects.requests)==writes
        assert api.complete_posts==1 and api.final_posts==1
        manifest=entry.snapshot()['manifest']
        assert manifest['publication']['status']=='server_acknowledged'
        assert all('publication' not in row['path'] for row in manifest['files'])


def test_failed_verifier_is_retained_and_explicit_resume_does_not_retry_operation(source,tmp_path,monkeypatch):
    claimed=attempt(source)
    with object_server(tmp_path) as objects:
        api=NativeApi(claimed,objects);api.verifier_failed=True
        selected=worker(source,api,monkeypatch);root=tmp_path/'attempt';root.mkdir()
        with pytest.raises(PublicationUnconfirmed):selected.execute_attempt(claimed,root)
        writes=len(objects.requests)
        with pytest.raises(PublicationUnconfirmed):selected.resume_publication(root,lease_token=claimed['lease_token'])
        assert api.complete_posts==1 and not api.final_posts and len(objects.requests)==writes


def test_changed_retained_source_or_destination_rejects_resume_before_network(source,tmp_path,monkeypatch):
    claimed=attempt(source)
    with object_server(tmp_path) as objects:
        objects.lose_next_put=True
        api=NativeApi(claimed,objects);selected=worker(source,api,monkeypatch);root=tmp_path/'attempt';root.mkdir()
        with pytest.raises(PublicationUnconfirmed):selected.execute_attempt(claimed,root)
        before=len(api.calls)
        other=Runner('https://other.example','fixture','unused',client=selected.client,source_root=source)
        with pytest.raises(PublicationUnconfirmed):other.resume_publication(root,lease_token=claimed['lease_token'])
        assert len(api.calls)==before
        (root/'work/evidence/record.json').write_bytes(b'changed')
        with pytest.raises(PublicationUnconfirmed,match='source bytes changed'):
            selected.resume_publication(root,lease_token=claimed['lease_token'])
        assert all(route.endswith('/heartbeat') for _,route,_ in api.calls[before:])


def test_explicit_resume_renews_live_lease_during_streamed_source_verification(source,tmp_path,monkeypatch):
    claimed=attempt(source);renewed=threading.Event()
    class Renewed(NativeApi):
        def __init__(self,*args):super().__init__(*args);self.resume=False;self.beats=0
        def _handle(self,request):
            if self.resume and request.url.path.endswith('/heartbeat'):
                self.beats+=1
                if self.beats>=2:renewed.set()
            return super()._handle(request)
    with object_server(tmp_path) as objects:
        objects.lose_next_put=True
        api=Renewed(claimed,objects);selected=worker(source,api,monkeypatch)
        root=tmp_path/'attempt';root.mkdir()
        with pytest.raises(PublicationUnconfirmed):selected.execute_attempt(claimed,root)
        api.resume=True
        original=AttemptPublication.source
        def slow_hash(self,item):
            assert renewed.wait(2),'resume must start heartbeats before reading a large retained source'
            return original(self,item)
        monkeypatch.setattr(AttemptPublication,'source',slow_hash)
        assert selected.resume_publication(root,lease_token=claimed['lease_token'])=={'accepted':True,'state':'succeeded'}
        assert api.beats>=2


def test_explicitly_disabled_retention_is_volatile_and_never_promises_deleted_source_resume(source,tmp_path,monkeypatch):
    claimed=attempt(source)
    with object_server(tmp_path) as objects:
        objects.lose_next_put=True
        api=NativeApi(claimed,objects);selected=worker(source,api,monkeypatch)
        selected._profile_config['evidence_spool']={'enabled':False}
        selected.work_root=tmp_path/'volatile';selected.work_root.mkdir()
        with pytest.raises(PublicationUnconfirmed,match='volatile staging.*cannot be resumed'):selected.run_once()
        assert list(selected.work_root.iterdir())==[]


@pytest.mark.parametrize('maximum',[None,514*1024**2])
def test_actual_native_streams_input_and_output_above_old_512mib_cap(source,tmp_path,monkeypatch,maximum):
    path=tmp_path/'large-input';size=512*1024**2+1
    with path.open('wb') as stream:stream.truncate(size)
    (source/'lab/engine.py').write_text('''import hashlib,shutil
from pathlib import Path
def execute(spec,output,emit,cancelled):
    target=output/'large.bin'
    with Path(spec['input_paths']['input-one']).open('rb') as source,target.open('wb') as destination:
        shutil.copyfileobj(source,destination,1024**2)
    sha=hashlib.sha256()
    with target.open('rb') as stream:
        while block:=stream.read(1024**2):sha.update(block)
    return {'artifacts':[{'path':str(target),'name':target.name,'sha256':sha.hexdigest(),'role':'evidence'}]}
''')
    claimed=input_attempt(source,path)
    claimed['spec']['definition']['execution'].update(max_seconds=None,max_output_bytes=maximum)
    claimed['spec_digest']=checksum(claimed['spec'])
    with object_server(tmp_path,path) as objects:
        api=NativeApi(claimed,objects);selected=worker(source,api,monkeypatch)
        root=tmp_path/'attempt';root.mkdir()
        selected.execute_attempt(claimed,root)
        assert api.final_body['state']=='succeeded',api.final_body
        artifact=api.final_body['result']['artifacts'][0]
        assert artifact['size']==size and artifact['sha256']==linear(path)
        assert sum(part.stat().st_size for part in objects.parts.values())==size
        assert len([method for method,_ in objects.requests if method=='PUT'])>1
