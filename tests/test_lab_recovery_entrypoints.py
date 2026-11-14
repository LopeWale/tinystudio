"""Recovery entrypoints preserve scoped authority and original evidence bytes."""
import json
from unittest.mock import Mock
from zipfile import ZipFile

from typer.testing import CliRunner

from tests.test_lab_evidence_spool import allocate, write_evidence, SECRET
from tinystudio.integrations.tools import tool_specs
from tinystudio.lab.cli import app


def test_local_spool_inspection_and_guarded_export_need_no_project_credential(tmp_path,monkeypatch):
    monkeypatch.delenv('TINYSTUDIO_API_KEY',raising=False)
    store,entry=allocate(tmp_path)
    payload=b'{"value":1}\npartial'
    write_evidence(entry,data=payload)
    snapshot=entry.snapshot()
    before=entry.manifest_path.read_bytes()
    cli=CliRunner()
    result=cli.invoke(app,['spool-inspect',entry.key,'--root',str(store.policy.root)],color=False)
    assert result.exit_code==0,result.output
    assert json.loads(result.output)['manifest_sha256']==snapshot['manifest_sha256']
    assert entry.manifest_path.read_bytes()==before
    output=tmp_path/'recovered.zip'
    result=cli.invoke(app,['spool-export',entry.key,str(output),'--manifest-sha256',snapshot['manifest_sha256'],
                           '--root',str(store.policy.root)],color=False)
    assert result.exit_code==0,result.output
    with ZipFile(output) as archive:
        assert archive.read('evidence/raw.jsonl')==payload
        assert SECRET.encode() not in b''.join(archive.read(name) for name in archive.namelist())


def test_recovery_tools_keep_read_and_attach_effects_distinct():
    studio=Mock();studio.project='research'
    specs={item.name:item for item in tool_specs(studio)}
    assert specs['lab_recovered_evidence'].read_only
    assert specs['lab_recovered_evidence_preview'].read_only
    assert not specs['lab_recovered_evidence_attach'].read_only
    assert specs['lab_recovered_evidence_attach'].long_running
    body={'source_attempt_id':'attempt-one','manifest':{'artifact_id':'owned'}}
    specs['lab_recovered_evidence_preview'].func('run-one',body)
    studio.call.assert_called_with('POST','/api/lab/runs/run-one/recovered-evidence/preview',json=body)
    request={**body,'idempotency_key':'same-request','preview_digest':'a'*64}
    specs['lab_recovered_evidence_attach'].func('run-one',request)
    studio.call.assert_called_with('POST','/api/lab/runs/run-one/recovered-evidence',json=request)


def test_preview_and_attach_cli_forward_the_same_explicit_request(tmp_path,monkeypatch):
    from tinystudio.lab import cli as module
    calls=[]
    monkeypatch.setattr(module,'call',lambda *args: calls.append(args) or {'accepted':True})
    body={'idempotency_key':'recover-once','preview_digest':'b'*64,'files':[]}
    file=tmp_path/'request.json';file.write_text(json.dumps(body))
    runner=CliRunner()
    for command in ('recover-evidence-preview','recover-evidence'):
        result=runner.invoke(app,[command,'run-one',str(file),'--project','research'],color=False)
        assert result.exit_code==0,result.output
    assert calls==[
        ('POST','research','/runs/run-one/recovered-evidence/preview',body),
        ('POST','research','/runs/run-one/recovered-evidence',body)]


def test_archive_cli_uses_project_transport_and_preserves_selected_retry_journal(tmp_path,monkeypatch):
    from contextlib import nullcontext
    from tinystudio.lab import cli as module
    from tinystudio.lab import evidence_recovery_client as recovery
    http=object();calls=[]
    archive=tmp_path/'original.zip'
    journal=tmp_path/'chosen-intent.json'
    monkeypatch.setattr(module,'client',lambda:nullcontext(http))
    class Importer:
        def __init__(self,transport,base):
            assert transport is http and base=='/api/p/research/lab'
        def import_archive(self,file,**options):
            calls.append((file,options))
            return {'preview_only':True,'request_path':str(options['request_path'])}
    monkeypatch.setattr(recovery,'RecoveryArchiveClient',Importer)
    result=CliRunner().invoke(app,['recover-archive',str(archive),'--project','research',
        '--idempotency-key','chosen-key','--request-file',str(journal),'--preview-only'],color=False)
    assert result.exit_code==0,result.output
    assert calls==[(archive,{'idempotency_key':'chosen-key','request_path':journal,'preview_only':True})]
    assert json.loads(result.output)['request_path']==str(journal)
