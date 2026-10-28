"""Assistant admission prevents duplicate provider/tool execution before persistence."""
from concurrent.futures import ThreadPoolExecutor
import copy
from types import SimpleNamespace
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tinystudio.api.deps import current_user, get_db
from tinystudio.api.routes import system
from tinystudio.integrations import assistant, conversation_admission as admission
from tinystudio.integrations.conversation_turn import ConversationTurn
from tinystudio.services import assistant_conversations as conversations


@pytest.fixture
def admitted_api(monkeypatch):
    lock = threading.Lock()
    gate = threading.Lock()
    snapshot = {'revision': 1, 'history': [{'role': 'user', 'content': 'Inspect the result'}], 'provider': 'openai', 'model': 'fixture', 'updated': None}
    class Lease:
        def __init__(self): self.closed = False
        @classmethod
        def acquire(cls, project, user):
            if not lock.acquire(blocking=False): raise admission.TurnInProgress('assistant turn already running')
            return cls()
        def close(self):
            if not self.closed:
                self.closed = True
                lock.release()
    def load(*args):
        with gate: return copy.deepcopy(snapshot)
    def claim(project, user, previous, provider, model):
        with gate:
            if previous['revision'] != snapshot['revision']: raise conversations.ConversationConflict('stale admission')
            snapshot.update(revision=previous['revision']+1, provider=provider, model=model)
            return copy.deepcopy(snapshot)
    def finish(self, events):
        with gate:
            if self.snapshot['revision'] != snapshot['revision']: raise conversations.ConversationConflict('stale completion')
            text = ''.join(event['delta'] for event in events if event['type']=='text')
            if text: snapshot['history'] = self.snapshot['history'] + [{'role':'assistant','content':text}]
            snapshot['revision'] += 1
            return {'type':'conversation_saved','revision':snapshot['revision']}
    monkeypatch.setattr(admission, 'ConversationLease', Lease)
    monkeypatch.setattr(admission, 'claim_revision', claim)
    monkeypatch.setattr(conversations, 'load', load)
    monkeypatch.setattr(ConversationTurn, '__call__', finish)
    monkeypatch.setattr(system.projects_svc, 'get_project', lambda *args:SimpleNamespace(id='project-id'))
    monkeypatch.setattr(system.settings_svc, 'apply', lambda *args:None)
    monkeypatch.setattr(system, 'instance_secret', lambda *args:'fixture-not-a-real-secret')
    monkeypatch.setattr(system, 'issue_service_token', lambda *args:'fixture-service-token')
    app=FastAPI();app.include_router(system.router)
    app.dependency_overrides[current_user]=lambda:SimpleNamespace(user=SimpleNamespace(id='fixture-user'))
    app.dependency_overrides[get_db]=lambda:object()
    body={'project':'fixture','provider':'openai','model':'fixture','with_ui':False,'messages':[{'role':'user','content':'Inspect the result'}],'conversation_revision':1}
    return app,body,snapshot,lock


def test_concurrent_same_revision_runs_provider_and_authorized_tool_only_once(admitted_api,monkeypatch):
    app,body,snapshot,lock=admitted_api
    entered,release=threading.Event(),threading.Event()
    executions=[]
    def runner(*args,**kwargs):
        executions.append('provider-and-authorized-tool')
        entered.set()
        assert release.wait(5)
        yield {'type':'text','delta':'One saved answer'}
    monkeypatch.setattr(assistant,'run',runner)
    with TestClient(app) as first,TestClient(app) as second,ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(first.post,'/api/assistant/chat',json=body)
        try:
            assert entered.wait(3)
            denied=second.post('/api/assistant/chat',json=body)
            assert denied.status_code==409
            refreshed=second.post('/api/assistant/chat',json={**body,'conversation_revision':2})
            assert refreshed.status_code==409, 'A refreshed revision cannot overlap an active worker'
            assert executions==['provider-and-authorized-tool']
            assert snapshot['revision']==2 and snapshot['history']==body['messages']
        finally:release.set()
        completed=pending.result(timeout=5)
        assert completed.status_code==200
        assert '"revision": 3' in completed.text
        assert snapshot['history']==body['messages']+[{'role':'assistant','content':'One saved answer'}]
        assert second.post('/api/assistant/chat',json=body).status_code==409
    assert lock.acquire(blocking=False);lock.release()


def test_invalid_effective_context_does_not_claim_or_execute(admitted_api,monkeypatch):
    app,body,snapshot,lock=admitted_api
    calls=[]
    monkeypatch.setattr(assistant,'run',lambda *a,**k:calls.append(True))
    with TestClient(app) as client:
        response=client.post('/api/assistant/chat',json={**body,'context':{'value':'x'*256001}})
    assert response.status_code==413 and snapshot['revision']==1 and calls==[]
    assert lock.acquire(blocking=False);lock.release()


def test_worker_lease_survives_stream_close_until_runner_and_persistence_finish():
    running,release,finished=threading.Event(),threading.Event(),threading.Event()
    lock=threading.Lock();assert lock.acquire(blocking=False)
    def runner(*args):
        running.set()
        yield {'type':'text','delta':'First'}
        assert release.wait(5)
        yield {'type':'text','delta':' final'}
    def persist(events):
        assert lock.locked()
        assert ''.join(event['delta'] for event in events)=='First final'
        return {'type':'conversation_saved','revision':3}
    def done():lock.release();finished.set()
    stream=assistant.stream_events('openai',None,[],with_ui=False,runner=runner,on_complete=persist,on_finished=done)
    try:
        assert running.wait(2), 'Worker admission must begin before first HTTP event is consumed'
        assert next(stream)['delta']=='First'
        stream.close()
        assert lock.locked(), 'HTTP close must not release an executing worker admission'
    finally:release.set()
    assert finished.wait(3)
    assert not lock.locked()


def test_worker_completion_releases_admission_after_provider_and_persistence_errors():
    finished=threading.Event()
    def runner(*args):
        raise RuntimeError('fixture provider failure')
        yield
    def failed_save(events):raise RuntimeError('fixture storage failure')
    events=list(assistant.stream_events('openai',None,[],with_ui=False,runner=runner,on_complete=failed_save,on_finished=finished.set))
    assert [event['type'] for event in events]==['error','conversation_error']
    assert finished.is_set()


def test_worker_start_failure_releases_admission(monkeypatch):
    finished=threading.Event()
    def fail(self):raise RuntimeError('cannot start worker')
    monkeypatch.setattr(assistant.threading.Thread,'start',fail)
    with pytest.raises(RuntimeError,match='cannot start'):
        assistant.stream_events('openai',None,[],with_ui=False,on_finished=finished.set)
    assert finished.is_set()


def test_postgresql_lease_uses_try_session_lock_and_never_returns_locked_connection(monkeypatch):
    records=[]
    class Connection:
        def execution_options(self,**kw):records.append(kw);return self
        def scalar(self,sql,params):records.append((str(sql),params));return True
        def execute(self,sql,params):records.append((str(sql),params))
        def close(self):records.append('closed')
    monkeypatch.setattr(admission.db,'engine',lambda:SimpleNamespace(connect=Connection))
    lease=admission.ConversationLease.acquire('p','u');lease.close();lease.close()
    assert records[0]=={'isolation_level':'AUTOCOMMIT'}
    assert records[1][0]=='SELECT pg_try_advisory_lock(:key)'
    assert records[2][0]=='SELECT pg_advisory_unlock(:key)'
    assert records[1][1]==records[2][1]
    assert -2**63<=records[1][1]['key']<2**63
    assert records.count('closed')==1


def test_failed_unlock_invalidates_physical_session_before_pool_return(monkeypatch):
    records=[]
    class Connection:
        def execute(self,*args):raise RuntimeError('fixture connection lost')
        def invalidate(self):records.append('invalidated')
        def close(self):records.append('closed')
    lease=admission.ConversationLease(Connection(),123)
    lease.close()
    assert records==['invalidated','closed']


def test_failed_start_releases_lease_but_durable_claim_rejects_replay(admitted_api,monkeypatch):
    app,body,snapshot,lock=admitted_api
    calls=[]
    def fail(*args,**kwargs):
        calls.append('attempted start')
        raise RuntimeError('fixture start failure')
    monkeypatch.setattr(assistant,'stream_events',fail)
    with TestClient(app,raise_server_exceptions=False) as client:
        assert client.post('/api/assistant/chat',json=body).status_code==500
        assert snapshot['history']==body['messages'] and snapshot['revision']==2
        assert client.post('/api/assistant/chat',json=body).status_code==409
        assert calls==['attempted start']
    assert lock.acquire(blocking=False);lock.release()


def test_durable_revision_claim_commits_existing_history_through_cas(monkeypatch):
    from contextlib import contextmanager
    calls=[];session=object()
    @contextmanager
    def transaction():
        calls.append('begin')
        yield session
        calls.append('committed')
    snapshot={'revision':4,'history':[{'role':'user','content':'Retain original'}],'model':'prior'}
    def save(db,project,user,body):
        assert db is session and project=='p' and user=='u'
        assert body.revision==4 and body.snapshot()['history']==snapshot['history']
        calls.append('CAS')
        return {**snapshot,'revision':5}
    monkeypatch.setattr(admission.db,'session',transaction)
    monkeypatch.setattr(conversations,'save',save)
    assert admission.claim_revision('p','u',snapshot,'openai','chosen')['revision']==5
    assert calls==['begin','CAS','committed']


@pytest.mark.parametrize('conflict',[False,True])
def test_recovery_revision_is_this_turn_claim_not_a_newer_writer_revision(conflict):
    def runner(*args):
        yield {'type':'text','delta':'Preserve generated reply'}
    def failed_save(events):
        if conflict:raise conversations.ConversationConflict('a newer writer committed revision 99')
        raise RuntimeError('storage unavailable')
    events=list(assistant.stream_events('openai',None,[],with_ui=False,runner=runner,
        on_complete=failed_save,admitted_revision=7))
    assert events[0]=={'type':'conversation_admitted','revision':7}
    assert events[1]=={'type':'text','delta':'Preserve generated reply'}
    assert events[-1]['type']=='conversation_error'
    assert events[-1]['revision']==7 and events[-1]['conflict'] is conflict


def test_stateless_stream_does_not_invent_a_recovery_revision():
    def runner(*args):yield {'type':'text','delta':'Stateless reply'}
    assert list(assistant.stream_events('openai',None,[],with_ui=False,runner=runner))==[{'type':'text','delta':'Stateless reply'}]
