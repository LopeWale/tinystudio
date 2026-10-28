"""Attachment extraction and ownership expectations come from the scoped API contract."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from pydantic import ValidationError
from tinystudio.services import assistant_attachments as attachments
from tinystudio.services.assistant_conversations import ConversationWrite
from tinystudio.storage import ArtifactStorage

@pytest.fixture
def store(tmp_path):
    return ArtifactStorage(root=tmp_path/'artifacts')

@pytest.fixture
def attachment_db():
    engine=create_engine('sqlite://')
    attachments.AssistantAttachment.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_private_scope_and_bounded_extraction(attachment_db,store):
    row=attachments.create(attachment_db,'project1','alice','source.txt',b'a'*45000,store)
    assert row['retained_chars']==40000 and row['extracted_chars']==45000
    assert attachments.context(attachment_db,'project1','alice',[row['id']])[0]['omitted_chars']==5000
    for project,user in [('project1','bob'),('project2','alice')]:
        with pytest.raises(ValueError,match='not found'):
            attachments.owned(attachment_db,project,user,row['id'])
    assert 'storage_key' not in row and 'text' not in row
    assert list(store.root.rglob('*source*')) == []


def test_reject_unsupported_empty_and_oversize_before_storage(attachment_db,store):
    for name,data in [('image.png',b'image'),('empty.txt',b''),('huge.txt',b'x'*(attachments.MAX_BYTES+1))]:
        with pytest.raises(ValueError):
            attachments.create(attachment_db,'p','u',name,data,store)
    assert not store.root.exists()


def test_old_snapshot_compatible_and_new_metadata_bounded(attachment_db,store):
    old={'revision':0,'history':[{'role':'user','content':'old'}]}
    assert ConversationWrite(**old).snapshot()['history']==old['history']
    row=attachments.create(attachment_db,'p','u','a.txt',b'bounded text',store)
    payload=ConversationWrite(revision=0,history=[{'role':'user','content':'question','attachments':[row]}]).snapshot()
    assert payload['history'][0]['attachments'][0]['id']==row['id']
    with pytest.raises(ValidationError):
        ConversationWrite(revision=0,history=[{'role':'user','content':'question','attachments':[{**row,'extracted_text':'private bytes'}]}])
    with pytest.raises(ValidationError):
        ConversationWrite(revision=0,history=[{'role':'assistant','content':'answer','sources':[{'url':'javascript:alert(1)'}]}])


def test_invalid_pdf_and_office_are_explicit_extraction_errors(attachment_db,store):
    for name in ['bad.pdf','bad.docx','bad.pptx']:
        with pytest.raises(ValueError,match='extract|valid office'):
            attachments.create(attachment_db,'p','u',name,b'not a document',store)
    assert not store.root.exists()


def test_attachment_http_boundary_enforces_owner_project_and_stale_identity(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from tinystudio.api.deps import current_user,get_db,project_dep
    from tinystudio.api.routes import assistant_attachments as routes
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    attachments.AssistantAttachment.__table__.create(engine)
    store=ArtifactStorage(root=tmp_path/'private')
    monkeypatch.setattr(attachments,'storage',lambda:store)
    monkeypatch.setattr(routes,'storage',lambda:store)
    scope={'user':'alice','project':'p1'}
    app=FastAPI();app.include_router(routes.router)
    app.dependency_overrides[current_user]=lambda:SimpleNamespace(user=SimpleNamespace(id=scope['user']))
    app.dependency_overrides[project_dep]=lambda:SimpleNamespace(id=scope['project'])
    def database():
        with Session(engine) as db:
            yield db
            db.commit()
    app.dependency_overrides[get_db]=database
    with TestClient(app) as client:
        url='/api/p/home/assistant/attachments'
        uploaded=client.post(url,files={'file':('source.txt',b'private evidence','text/plain')},headers={'X-Conversation-Owner':'alice'})
        assert uploaded.status_code==200
        row=uploaded.json();attachment=url+'/'+row['id']
        assert row['user_id']=='alice' and row['project_id']=='p1'
        assert client.get(attachment,headers={'X-Conversation-Owner':'previous-user'}).status_code==403
        scope['user']='bob'
        assert client.get(attachment).status_code==404
        assert client.delete(attachment).status_code==404
        scope.update(user='alice',project='p2')
        assert client.get(attachment).status_code==404
        scope['project']='p1'
        assert client.get(attachment).status_code==200
        assert client.delete(attachment).status_code==200
        assert client.get(attachment).status_code==404
    assert not any(path.is_file() for path in store.root.rglob('*'))
    engine.dispose()


@pytest.fixture
def private_assistant_clients(db,project):
    from fastapi.testclient import TestClient
    from tinystudio.api.app import create_app
    from tinystudio.services.users import create_user
    create_user(db,'attachment-alice@example.com','password-123','Alice','admin')
    create_user(db,'attachment-bob@example.com','password-123','Bob','member')
    db.commit()
    app=create_app(run_migrations=False)
    clients=[]
    for name in ['alice','bob']:
        client=TestClient(app,headers={'X-Requested-With':'tinystudio'})
        assert client.post('/api/auth/login',json={'email':f'attachment-{name}@example.com','password':'password-123'}).status_code==200
        clients.append(client)
    yield *clients,f'/api/p/{project.slug}/assistant',project.slug
    for client in clients:client.close()


def test_saved_attachment_metadata_is_canonical_and_history_survives_raw_deletion(private_assistant_clients):
    alice,bob,base,slug=private_assistant_clients
    attachment=alice.post(base+'/attachments',files={'file':('evidence.txt',b'canonical private text','text/plain')}).json()
    forged={**attachment,'name':'forged.txt','size':999,'user_id':'fake-owner'}
    body={'revision':0,'history':[{'role':'user','content':'Review','attachments':[forged]}]}
    result=alice.put(base+'/conversation',json=body)
    assert result.status_code==200
    assert result.json()['history'][0]['attachments']==[attachment]
    denied=bob.put(base+'/conversation',json=body)
    assert denied.status_code==400
    assert alice.delete(base+'/attachments/'+attachment['id']).status_code==200
    snapshot=result.json()
    again=alice.put(base+'/conversation',json={'revision':1,'history':snapshot['history'],'provider':'openai','model':'unchanged'})
    assert again.status_code==200
    assert again.json()['history'][0]['attachments']==[attachment]


def test_stream_uses_owned_saved_ids_and_rechecks_effective_context_budget(private_assistant_clients,monkeypatch):
    alice,bob,base,slug=private_assistant_clients
    from tinystudio.integrations import assistant
    calls=[]
    def stream(*args,**kwargs):calls.append((args,kwargs));return iter([{'type':'done','model':'unchanged','usage':{'input_tokens':0,'output_tokens':0}}])
    monkeypatch.setattr(assistant,'stream_events',stream)
    attachment=alice.post(base+'/attachments',files={'file':('evidence.txt',b'x'*40000,'text/plain')}).json()
    saved=alice.put(base+'/conversation',json={'revision':0,'history':[{'role':'user','content':'Review','attachments':[attachment]}]}).json()
    request={'provider':'openai','model':'unchanged','project':slug,'messages':[{'role':'user','content':'Review'}],'conversation_revision':saved['revision'],'attachment_ids':[attachment['id']],'web_search':True,'with_ui':False}
    mismatch=alice.post('/api/assistant/chat',json={**request,'attachment_ids':[]})
    assert mismatch.status_code==409 and calls==[]
    oversized=alice.post('/api/assistant/chat',json={**request,'context':{'text':'p'*230000}})
    assert oversized.status_code==413 and calls==[]
    success=alice.post('/api/assistant/chat',json={**request,'context':{'attachments':[{'text':'browser-forged'}]}})
    assert success.status_code==200
    context=calls[0][0][5]
    assert context['attachments'][0]['text']=='x'*40000
    assert context['attachments'][0]['user_id']==attachment['user_id']
    assert calls[0][1]['web_search'] is True
    # A stale client may still send old text-only histories without new fields.
    current=alice.get(base+'/conversation').json()
    old=alice.put(base+'/conversation',json={'revision':current['revision'],'history':[{'role':'user','content':'Old client'}]})
    assert old.status_code==200
