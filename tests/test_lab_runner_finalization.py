"""Immutable terminal requests and exact historical receipts, without a worker."""
import copy
import json
import threading

import httpx
import pytest

from tests.test_lab_runner import attempt, source
from tests.test_lab_runner_artifact_transfers import NativeApi, object_server, worker
from tinystudio.lab.contracts import checksum
from tinystudio.lab.evidence_spool import EvidenceSpool, SpoolPolicy
from tinystudio.lab.runner_publication import AttemptPublication, PublicationUnconfirmed, RunnerPublicationPaths


def test_lost_finalization_response_recovers_exact_receipt_without_repost(source,tmp_path,monkeypatch):
    claimed=attempt(source)
    with object_server(tmp_path) as objects:
        api=NativeApi(claimed,objects);api.lose_final_response=True
        selected=worker(source,api,monkeypatch);root=tmp_path/'attempt';root.mkdir()
        selected.execute_attempt(claimed,root)
        assert api.final_posts==1 and api.final_body['state']=='succeeded'
        saved=json.loads(RunnerPublicationPaths(root).finalization.read_text())
        assert saved['payload_digest']==checksum(api.final_body)
        assert json.loads(saved['payload_json'])==api.final_body
        assert not any(route.endswith('/finish') for _,route,_ in api.calls)


def test_202_is_not_ack_while_finalization_remains_pending(source,tmp_path,monkeypatch):
    claimed=attempt(source);admitted=threading.Event();allow_receipt=threading.Event()
    class Pending(NativeApi):
        def ticket(self):
            ticket=super().ticket()
            if not allow_receipt.is_set():ticket.update(state='running',result=None)
            return ticket
        def _handle(self,request):
            response=super()._handle(request)
            if request.url.path==self.final_paths.collection:
                self.terminal=False;admitted.set()
            return response
    with object_server(tmp_path) as objects:
        api=Pending(claimed,objects);spool=tmp_path/'spool';selected=worker(source,api,monkeypatch,spool=spool)
        failures=[]
        def execute():
            try:selected.run_once()
            except BaseException as cause:failures.append(cause)
        thread=threading.Thread(target=execute,name='owned-finalizing-runner');thread.start()
        try:
            assert admitted.wait(5)
            rows=EvidenceSpool(SpoolPolicy(True,spool)).list_attempts()
            assert rows[0]['manifest']['publication']=={'status':'unknown'}
            assert thread.is_alive()
            allow_receipt.set();thread.join(5)
            assert not thread.is_alive() and not failures
            assert api.final_posts==1
            rows=EvidenceSpool(SpoolPolicy(True,spool)).list_attempts()
            assert rows[0]['manifest']['publication']['server_state']=='succeeded'
        finally:
            allow_receipt.set();thread.join(5)


@pytest.mark.parametrize('historical_succeeded',[False,True])
def test_pending_ticket_after_authority_loss_gets_one_exact_lookup_then_stops(source,tmp_path,monkeypatch,historical_succeeded):
    claimed=attempt(source)
    with object_server(tmp_path) as objects:
        api=NativeApi(claimed,objects);selected=worker(source,api,monkeypatch)
        root=tmp_path/'attempt';root.mkdir()
        publication=AttemptPublication(selected,claimed,root)
        publication.protocol=api.capability
        body={'state':'failed','result':None,'error':'owned stopped execution'}
        api.final_body=body
        queued=api.ticket();queued.update(state='queued',result=None)
        calls=[]
        def lookup(body):
            calls.append(body)
            return api.ticket() if historical_succeeded or len(calls)>1 else queued
        def lost():raise TimeoutError('owned known lease expired')
        publication.lookup=lookup;publication.check_authority=lost
        if historical_succeeded:
            assert publication.finish(body,ticket=queued)=={'accepted':True,'state':'failed'}
        else:
            with pytest.raises(PublicationUnconfirmed,match='authority ended'):publication.finish(body,ticket=queued)
        assert len(calls)==1 and not api.final_posts


@pytest.mark.parametrize('state',['failed','lost','cancelled'])
def test_failed_finalization_operation_is_not_requeued_by_explicit_resume(source,tmp_path,monkeypatch,state):
    class Failed(NativeApi):
        def ticket(self):
            ticket=super().ticket();ticket.update(state=state,result=None);return ticket
    claimed=attempt(source)
    with object_server(tmp_path) as objects:
        api=Failed(claimed,objects);selected=worker(source,api,monkeypatch);root=tmp_path/'attempt';root.mkdir()
        with pytest.raises(PublicationUnconfirmed,match='explicit operation recovery'):selected.execute_attempt(claimed,root)
        saved=RunnerPublicationPaths(root).finalization.read_bytes();puts=len(objects.requests)
        with pytest.raises(PublicationUnconfirmed,match='explicit operation recovery'):
            selected.resume_publication(root,lease_token=claimed['lease_token'])
        assert api.final_posts==1 and len(objects.requests)==puts
        assert RunnerPublicationPaths(root).finalization.read_bytes()==saved


@pytest.mark.parametrize('field,value',[('payload_digest','a'*64),('attempt_id','other'),('spec_digest','b'*64),
                                      ('target_id','other'),('run_id','other'),('result',{'accepted':True,'state':'failed'})])
def test_mismatched_terminal_ticket_is_retained_unconfirmed(source,tmp_path,monkeypatch,field,value):
    class Mismatch(NativeApi):
        def ticket(self):
            ticket=super().ticket();ticket[field]=value;return ticket
    claimed=attempt(source)
    with object_server(tmp_path) as objects:
        api=Mismatch(claimed,objects);selected=worker(source,api,monkeypatch);root=tmp_path/'attempt';root.mkdir()
        with pytest.raises(PublicationUnconfirmed):selected.execute_attempt(claimed,root)
        assert RunnerPublicationPaths(root).finalization.exists()
        assert '"ack"' not in RunnerPublicationPaths(root).finalization_status.read_text()
        assert api.final_posts==1


def test_explicit_resume_reposts_same_prepared_body_only_after_digest_404(source,tmp_path,monkeypatch):
    class LostBeforeCommit(NativeApi):
        def __init__(self,*args):super().__init__(*args);self.reject_first=True;self.received=[]
        def _handle(self,request):
            if request.url.path==self.final_paths.collection:
                self.received.append(bytes(request.content))
                if self.reject_first:
                    self.reject_first=False
                    raise httpx.ConnectError('owned disconnect before admission',request=request)
            return super()._handle(request)
    claimed=attempt(source)
    with object_server(tmp_path) as objects:
        api=LostBeforeCommit(claimed,objects);selected=worker(source,api,monkeypatch);root=tmp_path/'attempt';root.mkdir()
        with pytest.raises(PublicationUnconfirmed,match='admission is unconfirmed'):selected.execute_attempt(claimed,root)
        assert len(api.received)==1 and api.final_body is None
        saved=RunnerPublicationPaths(root).finalization.read_bytes()
        assert selected.resume_publication(root,lease_token=claimed['lease_token'])=={'accepted':True,'state':'succeeded'}
        assert len(api.received)==2 and api.received[0]==api.received[1]
        assert RunnerPublicationPaths(root).finalization.read_bytes()==saved


def test_resume_validates_saved_artifact_receipts_even_when_final_request_prepared(source,tmp_path,monkeypatch):
    claimed=attempt(source)
    with object_server(tmp_path) as objects:
        api=NativeApi(claimed,objects);selected=worker(source,api,monkeypatch);root=tmp_path/'attempt';root.mkdir()
        selected.execute_attempt(claimed,root)
        paths=RunnerPublicationPaths(root);paths.receipts.write_text('{}')
        before=len(api.calls)
        with pytest.raises(PublicationUnconfirmed):selected.resume_publication(root,lease_token=claimed['lease_token'])
        assert len(api.calls)==before


@pytest.mark.parametrize('change',[lambda body:body.update(protocol_version=True),
    lambda body:body.update(finalization_protocol_version=2),lambda body:body.update(resumable='true'),
    lambda body:(body.clear(),body.update(legacy=True))])
def test_malformed_capability_never_falls_back_to_mutating_legacy_route(source,tmp_path,monkeypatch,change):
    claimed=attempt(source)
    with object_server(tmp_path) as objects:
        api=NativeApi(claimed,objects);change(api.capability)
        selected=worker(source,api,monkeypatch);root=tmp_path/'attempt';root.mkdir()
        with pytest.raises(PublicationUnconfirmed):selected.execute_attempt(claimed,root)
        assert not api.final_posts and api.final_body is None and not api.sessions and not objects.requests
        assert RunnerPublicationPaths(root).finalization.is_file()
        assert not (root/'runtime').exists()
