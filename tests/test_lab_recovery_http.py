"""Actual project-authorized HTTP recovery never restores runner authority."""
from datetime import timedelta
import hashlib
import json
import threading
from zipfile import ZipFile

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from tests.test_lab_native_workflow import native_http, posted, enroll, submit_signal
from tinystudio.lab import service as svc
from tinystudio.lab.evidence_spool import EvidenceSpool, SpoolPolicy
from tinystudio.lab.models import LabArtifact, LabAttempt, LabRun


def prepare_recovery(f):
    target,runner=enroll(f,'recovery-http')
    runner.client.close()
    run=submit_signal(f,target,maximum=1024)
    with f.factory.begin() as db:
        row=db.get(LabRun,run['id'])
        row.state='recovery_required'
        row.error='original execution unconfirmed'
        source=LabAttempt(run_id=row.id,target_id=target['id'],number=1,
            token_hash=svc.secret_hash('expired-owned-lease'),lease_until=svc.now()-timedelta(seconds=60),
            state='recovery_required',runtime={})
        db.add(source);db.flush()
        source_id=source.id
    spool=EvidenceSpool(SpoolPolicy(True,f.root/'owned-spool'))
    entry=spool.allocate({'run_id':run['id'],'attempt_id':source_id,'spec':run['spec'],
        'spec_digest':run['spec_digest'],'lease_token':'never-public'},server=f.url,target_id=target['id'])
    output=entry.execution_root/'work/evidence';output.mkdir(parents=True)
    wire=b'{"reading":7}\npartial'
    (output/'raw.jsonl').write_bytes(wire)
    snapshot=entry.snapshot()
    def upload(name,data,media):
        artifact=posted(f.http,f.prefix+'/inputs',files={'file':(name,data,media)})
        return {'artifact_id':artifact['id'],'sha256':artifact['sha256'],'size':artifact['size']}
    manifest=upload('spool-manifest.json',entry.manifest_path.read_bytes(),'application/json')
    raw=upload('raw.jsonl',wire,'application/x-ndjson')
    body={'source_attempt_id':source_id,'target_id':target['id'],'spec_digest':run['spec_digest'],
        'manifest':manifest,'files':[{'name':'evidence/raw.jsonl',**raw}]}
    path=f.prefix+'/runs/'+run['id']+'/recovered-evidence'
    return target,run,body,path


def test_http_recovery_preserves_source_state_and_replays_after_object_loss(native_http):
    f=native_http
    target,run,body,path=prepare_recovery(f)
    original=f.http.get(f.prefix+'/runs/'+run['id']).json()
    preview=posted(f.http,path+'/preview',json=body)
    assert f.http.get(path).json()['references']==[]
    assert preview['qualification']['scope']=='bytes_and_existing_server_identity_only'
    assert preview['files'][0]['verified_bytes']['jsonl']['tail_bytes']==7
    request={**body,'preview_digest':preview['digest'],'idempotency_key':'one-recovered-snapshot'}
    imported=posted(f.http,path,json=request)
    assert imported['classification']=='operator_recovered_evidence' and not imported['replayed']
    assert imported['receipt']['visibility']=='evaluation'
    observed=f.http.get(f.prefix+'/runs/'+run['id']).json()
    for key in ('state','result','error','spec_digest','cancel_requested','attempts'):
        assert observed[key]==original[key]
    refs=f.http.get(path).json()['references']
    assert len(refs)==1 and refs[0]['receipt']['id']==imported['receipt']['id']
    for suffix,payload in [('/preview',body),('',request)]:
        response=f.http.post(path+suffix,json=payload,headers={'Authorization':'Bearer '+target['token']})
        assert response.status_code in {401,403}
        assert f.http.post((path+suffix).replace(f.prefix,f.restored,1),json=payload).status_code==404
    with f.factory() as db:
        for id_ in [body['manifest']['artifact_id'],body['files'][0]['artifact_id'],imported['receipt']['id']]:
            row=db.get(LabArtifact,id_)
            assert row.attempt_id is None
            (f.store.root/row.storage_key).unlink()
    replay=posted(f.http,path,json=request)
    assert replay['replayed'] and replay['receipt']==imported['receipt']
    assert len(f.http.get(path).json()['references'])==1


def test_recovery_commit_failure_cannot_return_a_published_receipt(native_http,monkeypatch):
    f=native_http
    target,run,body,path=prepare_recovery(f)
    preview=posted(f.http,path+'/preview',json=body)
    attempted=threading.Event()
    def fail_commit(db):
        attempted.set()
        raise RuntimeError('owned recovery transaction failure')
    with f.factory() as db:engine=db.get_bind()
    def begin(connection):connection.exec_driver_sql('BEGIN')
    event.listen(engine,'begin',begin)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(Session,'commit',fail_commit)
            response=f.http.post(path,json={**body,'preview_digest':preview['digest'],'idempotency_key':'uncertain-commit'})
            assert attempted.wait(5)
    finally:event.remove(engine,'begin',begin)
    assert response.status_code==500
    assert f.http.get(path).json()['references']==[]


def test_input_upload_commit_failure_cannot_acknowledge_a_recovery_file(native_http,monkeypatch):
    from sqlalchemy import select
    f=native_http
    attempted=threading.Event()
    def fail_commit(db):
        attempted.set()
        raise RuntimeError('owned recovery upload commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(Session,'commit',fail_commit)
        response=f.http.post(f.prefix+'/inputs',files={'file':('uncommitted.jsonl',b'{"x":1}\n','application/x-ndjson')})
        assert attempted.wait(5)
        assert response.status_code==500
    with f.factory() as db:
        assert list(db.scalars(select(LabArtifact).where(LabArtifact.name=='uncommitted.jsonl')))==[]


def test_target_recovery_commit_failure_cannot_acknowledge_a_state_transition(native_http,monkeypatch):
    f=native_http
    target,run,body,path=prepare_recovery(f)
    with f.factory.begin() as db:
        db.get(LabRun,run['id']).state='running'
        db.get(LabAttempt,body['source_attempt_id']).state='running'
    attempted=threading.Event()
    def fail_commit(db):
        attempted.set()
        raise RuntimeError('owned target recovery commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(Session,'commit',fail_commit)
        response=f.http.post(f.prefix+'/targets/'+target['id']+'/recover',json={})
        assert attempted.wait(5)
        assert response.status_code==500
    observed=f.http.get(f.prefix+'/runs/'+run['id']).json()
    assert observed['state']=='running'
    with f.factory() as db:
        assert db.get(LabAttempt,body['source_attempt_id']).state=='running'


def test_archive_client_uses_real_project_transport_and_survives_source_loss(native_http):
    from tinystudio.lab.evidence_recovery_client import RecoveryArchiveClient
    f=native_http
    target,run,body,path=prepare_recovery(f)
    manifest=f.http.get(f.prefix+'/artifacts/'+body['manifest']['artifact_id']).content
    raw=f.http.get(f.prefix+'/artifacts/'+body['files'][0]['artifact_id']).content
    archive=f.root/'exported-capture.zip'
    with ZipFile(archive,'w') as zip:
        zip.writestr('manifest.json',manifest)
        zip.writestr('evidence/raw.jsonl',raw)
    journal=f.root/'archive-import-request.json'
    client=RecoveryArchiveClient(f.http,f.prefix)
    prepared=client.import_archive(archive,idempotency_key='archive-one',request_path=journal,preview_only=True)
    assert prepared['preview_only'] and prepared['run_id']==run['id']
    assert f.http.get(path).json()['references']==[]
    assert journal.stat().st_mode & 0o777 == 0o600
    attached=client.import_archive(archive,idempotency_key='archive-one',request_path=journal)
    assert attached['classification']=='operator_recovered_evidence'
    assert len(f.http.get(path).json()['references'])==1
    archive.unlink()
    replay=client.import_archive(archive,idempotency_key='archive-one',request_path=journal)
    assert replay['receipt']==attached['receipt']
    assert len(f.http.get(path).json()['references'])==1
    assert f.http.get(f.prefix+'/runs/'+run['id']).json()['state']=='recovery_required'
