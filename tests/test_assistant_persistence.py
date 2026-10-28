"""A provider reply remains durable when its browser stream disappears."""

import threading

import pytest
from fastapi.testclient import TestClient

from tinystudio import db as db_mod
from tinystudio.api.app import create_app
from tinystudio.integrations import assistant
from tinystudio.integrations.conversation_turn import ConversationTurn
from tinystudio.services import assistant_conversations as conversations
from tinystudio.services.users import create_user


def seed(db, project):
    user = create_user(db, "durable@example.com", "password-123", "Durable", "admin")
    snapshot = conversations.save(db, project.id, user.id, conversations.ConversationWrite(
        revision=0, history=[{"role": "user", "content": "Remember the evaluation"}], provider="openai", model="test"))
    db.commit()
    return user, snapshot


def test_reply_persists_after_stream_close(db, project):
    user, snapshot = seed(db, project)
    release, saved = threading.Event(), threading.Event()
    turn = ConversationTurn(project.id, user.id, snapshot, "openai", "test")

    def runner(*args):
        yield {"type": "text", "delta": "First "}
        assert release.wait(5)
        yield {"type": "text", "delta": "and final"}

    def finish(events):
        try:
            return turn(events)
        finally:
            saved.set()

    stream = assistant.stream_events("openai", None, [], with_ui=False, runner=runner, on_complete=finish)
    assert next(stream) == {"type": "text", "delta": "First "}
    stream.close()
    release.set()
    assert saved.wait(5)
    with db_mod.session() as independent:
        result = conversations.load(independent, project.id, user.id)
    assert result["revision"] == 2
    assert result["history"][-1]["content"] == "First and final"


def test_delayed_reply_cannot_restore_cleared_conversation(db, project):
    user, snapshot = seed(db, project)
    conversations.save(db, project.id, user.id, conversations.ConversationWrite(revision=1, history=[]))
    db.commit()
    turn = ConversationTurn(project.id, user.id, snapshot, "openai", "test")
    with pytest.raises(conversations.ConversationConflict):
        turn([{"type": "text", "delta": "obsolete reply"}])
    assert conversations.load(db, project.id, user.id)["history"] == []


def test_chat_endpoint_saves_reply_and_rejects_stale_turn(db, project, monkeypatch):
    user, _ = seed(db, project)
    def runner(*args):
        yield {"type": "text", "delta": "Saved evaluation review"}
        yield {"type": "done", "model": "test", "usage": {"input_tokens": 1, "output_tokens": 1}}
    monkeypatch.setattr(assistant, "run", runner)
    with TestClient(create_app(run_migrations=False), headers={"X-Requested-With": "tinystudio"}) as client:
        assert client.post("/api/auth/login", json={"email": user.email, "password": "password-123"}).status_code == 200
        body = {"project": project.slug, "provider": "openai", "model": "test", "with_ui": False,
                "messages": [{"role": "user", "content": "Remember the evaluation"}], "conversation_revision": 1}
        result = client.post("/api/assistant/chat", json=body)
        assert result.status_code == 200
        assert '"type": "conversation_saved", "revision": 3' in result.text
        restored = client.get(f"/api/p/{project.slug}/assistant/conversation").json()
        assert restored["history"][-1]["content"] == "Saved evaluation review"
        assert client.post("/api/assistant/chat", json=body).status_code == 409


@pytest.mark.parametrize("conflict", [False, True])
def test_completion_failure_distinguishes_storage_from_revision_conflicts(conflict):
    def runner(*args):
        yield {"type": "text", "delta": "Keep this generated reply"}
    def failed_save(events):
        if conflict:
            raise conversations.ConversationConflict("newer conversation")
        raise RuntimeError("database outage with private connection details")
    events = list(assistant.stream_events("openai", None, [], with_ui=False, runner=runner, on_complete=failed_save))
    assert events[0]["delta"] == "Keep this generated reply"
    assert events[-1]["type"] == "conversation_error"
    assert events[-1]["conflict"] is conflict
    assert "private connection details" not in events[-1]["message"]


def test_real_postgresql_concurrent_http_turn_has_one_execution_and_durable_fence(db, project, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from tinystudio.integrations.conversation_admission import ConversationLease
    user, _ = seed(db, project)
    entered, release = threading.Event(), threading.Event()
    effects = []
    def runner(*args, **kwargs):
        # This controlled side effect represents a provider/tool invocation; no
        # external model, HTTP tool or production resource is contacted.
        effects.append('controlled provider/tool execution')
        entered.set()
        assert release.wait(5)
        yield {'type':'text','delta':'Exactly one answer'}
    monkeypatch.setattr(assistant,'run',runner)
    app=create_app(run_migrations=False)
    first=TestClient(app,headers={'X-Requested-With':'tinystudio'})
    second=TestClient(app,headers={'X-Requested-With':'tinystudio'})
    for client in (first,second):
        assert client.post('/api/auth/login',json={'email':user.email,'password':'password-123'}).status_code==200
    body={'project':project.slug,'provider':'openai','model':'test','with_ui':False,'allow_destructive':True,
          'messages':[{'role':'user','content':'Remember the evaluation'}],'conversation_revision':1}
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(first.post,'/api/assistant/chat',json=body)
            try:
                assert entered.wait(3)
                assert second.post('/api/assistant/chat',json=body).status_code==409
                claimed=second.get(f'/api/p/{project.slug}/assistant/conversation').json()
                assert claimed['revision']==2 and claimed['history']==body['messages']
                assert second.post('/api/assistant/chat',json={**body,'conversation_revision':2}).status_code==409
                assert effects==['controlled provider/tool execution']
            finally:release.set()
            complete=pending.result(timeout=5)
            assert complete.status_code==200
            assert '"revision": 3' in complete.text
        final=second.get(f'/api/p/{project.slug}/assistant/conversation').json()
        assert final['history']==body['messages']+[{'role':'assistant','content':'Exactly one answer','calls':[],'sources':[],'searches':[]}]
        assert second.post('/api/assistant/chat',json=body).status_code==409
        lease=ConversationLease.acquire(project.id,user.id);lease.close()
    finally:
        first.close();second.close()


def test_real_postgresql_disconnected_worker_keeps_lease_and_saves_original_history(db, project):
    from tinystudio.integrations.conversation_admission import ConversationLease,TurnInProgress,claim_revision
    user,snapshot=seed(db,project)
    lease=ConversationLease.acquire(project.id,user.id)
    claimed=claim_revision(project.id,user.id,snapshot,'openai','test')
    release,finished=threading.Event(),threading.Event()
    def runner(*args):
        yield {'type':'text','delta':'Before disconnect '}
        assert release.wait(5)
        yield {'type':'text','delta':'and after'}
    def done():
        lease.close();finished.set()
    stream=assistant.stream_events('openai',None,[],with_ui=False,runner=runner,
        on_complete=ConversationTurn(project.id,user.id,claimed,'openai','test'),on_finished=done)
    try:
        assert next(stream)['delta']=='Before disconnect '
        stream.close()
        with pytest.raises(TurnInProgress):ConversationLease.acquire(project.id,user.id)
    finally:release.set()
    assert finished.wait(3)
    with db_mod.session() as independent:
        final=conversations.load(independent,project.id,user.id)
    assert final['revision']==3
    assert final['history'][0]==snapshot['history'][0]
    assert final['history'][-1]['content']=='Before disconnect and after'
    recovered=ConversationLease.acquire(project.id,user.id);recovered.close()


def test_real_postgresql_provider_failure_releases_lease_but_retains_claimed_history(db,project,monkeypatch):
    from tinystudio.integrations.conversation_admission import ConversationLease
    user,snapshot=seed(db,project)
    def runner(*args,**kwargs):
        raise RuntimeError('controlled provider failure')
        yield
    monkeypatch.setattr(assistant,'run',runner)
    client=TestClient(create_app(run_migrations=False),headers={'X-Requested-With':'tinystudio'})
    try:
        assert client.post('/api/auth/login',json={'email':user.email,'password':'password-123'}).status_code==200
        body={'project':project.slug,'provider':'openai','model':'test','with_ui':False,
              'messages':[{'role':'user','content':'Remember the evaluation'}],'conversation_revision':1}
        result=client.post('/api/assistant/chat',json=body)
        assert result.status_code==200 and 'controlled provider failure' in result.text
        assert '"revision": 3' in result.text
        restored=client.get(f'/api/p/{project.slug}/assistant/conversation').json()
        assert restored['history']==snapshot['history']
        assert client.post('/api/assistant/chat',json=body).status_code==409
        lease=ConversationLease.acquire(project.id,user.id);lease.close()
    finally:client.close()


def test_real_postgresql_clear_during_turn_wins_cas_and_releases_worker_lease(db,project,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from tinystudio.integrations.conversation_admission import ConversationLease
    user,_=seed(db,project)
    entered,release=threading.Event(),threading.Event()
    def runner(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        yield {'type':'text','delta':'Must not restore cleared history'}
    monkeypatch.setattr(assistant,'run',runner)
    app=create_app(run_migrations=False)
    first=TestClient(app,headers={'X-Requested-With':'tinystudio'})
    second=TestClient(app,headers={'X-Requested-With':'tinystudio'})
    for client in (first,second):
        assert client.post('/api/auth/login',json={'email':user.email,'password':'password-123'}).status_code==200
    body={'project':project.slug,'provider':'openai','model':'test','with_ui':False,
          'messages':[{'role':'user','content':'Remember the evaluation'}],'conversation_revision':1}
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(first.post,'/api/assistant/chat',json=body)
            try:
                assert entered.wait(3)
                cleared=second.put(f'/api/p/{project.slug}/assistant/conversation',json={'revision':2,'history':[]})
                assert cleared.status_code==200 and cleared.json()['revision']==3
            finally:release.set()
            answer=pending.result(timeout=5)
        assert '"type": "conversation_error", "conflict": true' in answer.text
        final=second.get(f'/api/p/{project.slug}/assistant/conversation').json()
        assert final['revision']==3 and final['history']==[]
        lease=ConversationLease.acquire(project.id,user.id);lease.close()
    finally:first.close();second.close()


@pytest.mark.parametrize('competing_write',[False,True])
def test_admitted_revision_recovers_failed_final_save_without_overwriting_other_writer(db,project,monkeypatch,competing_write):
    import json
    user,snapshot=seed(db,project)
    outage={'active':True}
    original_save=conversations.save
    executions=[]
    def save(session,project_id,user_id,body):
        if outage['active'] and body.history and body.history[-1].role=='assistant':
            raise RuntimeError('controlled final storage outage')
        return original_save(session,project_id,user_id,body)
    def runner(*args,**kwargs):
        executions.append('provider')
        yield {'type':'text','delta':'Generated reply retained for recovery'}
        yield {'type':'done','model':'test','usage':{'input_tokens':1,'output_tokens':1}}
    monkeypatch.setattr(conversations,'save',save)
    monkeypatch.setattr(assistant,'run',runner)
    client=TestClient(create_app(run_migrations=False),headers={'X-Requested-With':'tinystudio'})
    base=f'/api/p/{project.slug}/assistant/conversation'
    try:
        assert client.post('/api/auth/login',json={'email':user.email,'password':'password-123'}).status_code==200
        response=client.post('/api/assistant/chat',json={'project':project.slug,'provider':'openai','model':'test','with_ui':False,
            'messages':snapshot['history'],'conversation_revision':snapshot['revision']})
        assert response.status_code==200
        events=[json.loads(line.removeprefix('data: ')) for line in response.text.splitlines() if line.startswith('data: ')]
        assert events[0]=={'type':'conversation_admitted','revision':2}
        error=next(event for event in events if event['type']=='conversation_error')
        assert error['revision']==2 and error['conflict'] is False
        assert 'controlled final storage outage' not in error['message']
        stored=client.get(base).json()
        assert stored['revision']==2 and stored['history']==snapshot['history']
        if competing_write:
            competing=client.put(base,json={'revision':2,'history':[{'role':'user','content':'A newer writer owns this history'}]})
            assert competing.status_code==200 and competing.json()['revision']==3
        outage['active']=False
        recovered=snapshot['history']+[{'role':'assistant','content':''.join(event['delta'] for event in events if event['type']=='text')}]
        result=client.put(base,json={'revision':error['revision'],'history':recovered,'provider':'openai','model':'test'})
        assert executions==['provider'], 'Retry persistence must not invoke a provider again'
        if competing_write:
            assert result.status_code==409
            assert client.get(base).json()['history']==[{'role':'user','content':'A newer writer owns this history'}]
        else:
            assert result.status_code==200 and result.json()['revision']==3
            assert client.get(base).json()['history']==recovered
    finally:client.close()
