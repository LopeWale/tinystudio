"""Real owned child execution retains evidence after publication/lease loss."""
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import httpx
import pytest

from tests.test_lab_runner import Transport, attempt, source
from tinystudio.lab import runner as module
from tinystudio.lab.evidence_spool import EvidenceSpool, SpoolPolicy
from tinystudio.lab.runner import Runner
from tinystudio.lab.spool_configuration import SpoolConfiguration


def prepare(source, *, wait_for_cancel=False):
    (source/'lab/engine.py').write_text('''import hashlib,json,os,time
def execute(spec,output,emit,cancelled):
    path=output/'raw.jsonl'
    with path.open('wb') as stream:
        stream.write(b'{"record_id":"owned:1","value":2}\\n')
        stream.flush();os.fsync(stream.fileno())
    emit({'type':'original_observation_committed'})
    if spec['definition']['parameters'].get('wait_for_cancel'):
        while not cancelled():time.sleep(.005)
    return {'records':1,'artifacts':[{'path':str(path),'name':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'role':'original'}]}
''')
    claimed=attempt(source)
    claimed['run_id']='run-one'
    claimed['spec']['definition']['parameters']['wait_for_cancel']=wait_for_cancel
    from tinystudio.lab.contracts import checksum
    claimed['spec_digest']=checksum(claimed['spec'])
    return claimed


class ClaimTransport(Transport):
    def __init__(self,claimed,*,lose_lease=False,bad_ack=False):
        super().__init__();self.claimed=claimed;self.lose_lease=lose_lease;self.bad_ack=bad_ack

    def post(self,path,**kwargs):
        body=kwargs.get('json') or {}
        if path.endswith('/claim'):
            response={'attempt':self.claimed};self.claimed=None
            return httpx.Response(200,json=response,request=httpx.Request('POST','http://localhost'+path))
        if path.endswith('/heartbeat') and self.lose_lease and any(
                event.get('type')=='original_observation_committed' for event in body.get('events',[])):
            raise httpx.ConnectError('owned origin disconnection')
        response=super().post(path,**kwargs)
        if path.endswith('/finish'):
            value={'accepted':True,'state':'different' if self.bad_ack else body['state']}
            return httpx.Response(200,json=value,request=response.request)
        return response


def agent(source,root,transport,monkeypatch):
    monkeypatch.setattr(module,'runtime_versions',lambda:{'fixture-runtime':'1'})
    return Runner('http://localhost','fixture','target-secret',client=transport,source_root=source,
        heartbeat_interval=.01,process_grace=.2,profile_config={'schema_version':1,'backends':{},
            'evidence_spool':{'enabled':True,'root':str(root)},
            'engine_secrets':{'synthetic.signal':{'telemetry':'private-context-secret'}}})


def one_entry(root):
    spool=EvidenceSpool(SpoolPolicy(True,root))
    entries=spool.list_attempts()
    assert len(entries)==1
    return spool.open_attempt(entries[0]['key']),entries[0]['manifest']


def test_success_keeps_private_attempt_and_exports_only_evidence(source,tmp_path,monkeypatch):
    claimed=prepare(source);transport=ClaimTransport(claimed);root=tmp_path/'spool'
    assert agent(source,root,transport,monkeypatch).run_once()
    entry,manifest=one_entry(root)
    assert entry.execution_root.is_dir()
    assert not (entry.execution_root/'context.json').exists()
    assert manifest['cleanup']['status']=='confirmed'
    assert manifest['publication']['server_state']=='succeeded'
    assert [item['path'] for item in manifest['files']]==['evidence/raw.jsonl']
    snapshot=entry.snapshot()
    exported=entry.export(tmp_path/'evidence.zip',expected_manifest_digest=snapshot['manifest_sha256'])
    with ZipFile(exported['path']) as archive:
        assert set(archive.namelist())=={'manifest.json','evidence/raw.jsonl'}
        content=b''.join(archive.read(name) for name in archive.namelist())
        assert b'private-context-secret' not in content and b'target-secret' not in content
    assert transport.finish['state']=='succeeded'


def test_origin_disconnect_preserves_synced_capture_and_never_reuses_authority(source,tmp_path,monkeypatch):
    claimed=prepare(source,wait_for_cancel=True);transport=ClaimTransport(claimed,lose_lease=True);root=tmp_path/'spool'
    with pytest.raises(RuntimeError,match='lost lease connectivity'):
        agent(source,root,transport,monkeypatch).run_once()
    entry,manifest=one_entry(root)
    assert manifest['publication']=={'status':'unknown'}
    assert manifest['cleanup']['status']=='confirmed'
    assert transport.finish is None
    assert (entry.execution_root/'work/evidence/raw.jsonl').read_bytes()==b'{"record_id":"owned:1","value":2}\n'
    assert not (entry.execution_root/'context.json').exists()
    with pytest.raises(FileExistsError):
        EvidenceSpool(SpoolPolicy(True,root)).allocate(claimed,server='http://localhost',target_id='fixture')


def test_unconfirmed_finish_ack_keeps_publication_unknown(source,tmp_path,monkeypatch):
    claimed=prepare(source);transport=ClaimTransport(claimed,bad_ack=True);root=tmp_path/'spool'
    with pytest.raises(RuntimeError,match='completion acknowledgement is unconfirmed'):
        agent(source,root,transport,monkeypatch).run_once()
    entry,manifest=one_entry(root)
    assert manifest['publication']=={'status':'unknown'}
    assert manifest['files'][0]['path']=='evidence/raw.jsonl'


def test_r2_disposable_cache_requires_an_explicit_spool_location(tmp_path):
    host=SimpleNamespace(storage_backend='r2',lab_evidence_dir=None,
                         laboratory_evidence_spool=tmp_path/'disposable-cache/spool')
    selected=SpoolConfiguration.resolve(None,'acquisition.mqtt','native',host=host)
    with pytest.raises(ValueError,match='disposable host cache'):selected.require_ready()
    assert not selected.policy.root.exists()
    explicit=SpoolConfiguration.resolve({'evidence_spool':{'root':str(tmp_path/'selected-volume')}},
                                         'acquisition.mqtt','native',host=host)
    explicit.require_ready()
    assert explicit.advertised()['root_configured'] is True
    disabled=SpoolConfiguration.resolve({'evidence_spool':{'enabled':False}},'acquisition.mqtt','native',host=host)
    disabled.require_ready()


def test_spool_admission_failure_finishes_fresh_authority_before_any_child(source,tmp_path,monkeypatch):
    claimed=prepare(source);transport=ClaimTransport(claimed);root=tmp_path/'spool'
    worker=agent(source,root,transport,monkeypatch)
    worker._profile_config['evidence_spool']['max_bytes']=0
    monkeypatch.setattr(worker,'execute_attempt',lambda *a,**kw:pytest.fail('admission failure must not execute'))
    with pytest.raises(ValueError,match='disk budget'):worker.run_once()
    assert transport.finish['state']=='failed'
    assert 'before local execution' in transport.finish['error']
    assert transport.finish['result'] is None


def test_duplicate_spool_never_finishes_or_reexecutes_existing_authority(source,tmp_path,monkeypatch):
    claimed=prepare(source);transport=ClaimTransport(claimed);root=tmp_path/'spool'
    EvidenceSpool(SpoolPolicy(True,root)).allocate(claimed,server='http://localhost',target_id='fixture')
    worker=agent(source,root,transport,monkeypatch)
    monkeypatch.setattr(worker,'execute_attempt',lambda *a,**kw:pytest.fail('must not replay authority'))
    with pytest.raises(FileExistsError):worker.run_once()
    assert transport.finish is None


def test_evidence_environment_preserves_final_symlink_for_rejection(tmp_path,monkeypatch):
    from tinystudio.config import _load
    actual=tmp_path/'actual';actual.mkdir()
    alias=tmp_path/'evidence';alias.symlink_to(actual,target_is_directory=True)
    monkeypatch.setenv('TINYSTUDIO_LAB_EVIDENCE_DIR',str(alias))
    host=_load()
    assert host.lab_evidence_dir==alias
    with pytest.raises(ValueError,match='symlink'):
        SpoolConfiguration.resolve(None,'acquisition.mqtt','native',host=host)


@pytest.mark.parametrize('spool_location', [False, None, 'operator/archive/retained'])
def test_kubernetes_runner_mounts_the_staged_volume_paths(source,tmp_path,monkeypatch,spool_location):
    """Owned local staging and manifests only; no Kubernetes runtime qualification."""
    from tinystudio.lab.contracts import checksum

    claimed=prepare(source)
    claimed['spec']['definition']['execution'].update(backend='kubernetes',profile='isolated')
    claimed['spec']['runtime']={'os':'Linux'}
    claimed['spec_digest']=checksum(claimed['spec'])
    shared=tmp_path/'shared-volume';shared.mkdir()
    kubeconfig=tmp_path/'fixture-kubeconfig';kubeconfig.write_text('never used by a cluster')
    selected={'shared_work_root':str(shared),'image':'fixture@sha256:'+'a'*64,
              'kube_context':'fixture','kubeconfig':str(kubeconfig),'storage_claim':'fixture-pvc',
              'grants':{'network_policy_enforced':True}}
    spool={'enabled':spool_location is not False}
    if isinstance(spool_location,str):
        spool['root']=str(shared/spool_location)
    worker=Runner('http://localhost','fixture','target-secret',client=ClaimTransport(claimed),
                  source_root=source,profile_config={'schema_version':1,
                  'backends':{'kubernetes':selected},'evidence_spool':spool})
    monkeypatch.setattr(worker,'_advertised_capabilities',lambda:{})
    plans=[]

    def stage_manifest(attempt,root,**kwargs):
        staged=root/'runtime/tinystudio';staged.mkdir(parents=True)
        (staged/'fixture.py').write_text('owned source')
        (root/'inputs').mkdir();(root/'inputs/source.json').write_text('owned input')
        (root/'work').mkdir()
        (root/'spec.json').write_text('owned specification')
        plan=worker._profile(attempt['spec'],attempt,root,SimpleNamespace(root=staged),
                             selected,['/usr/bin/python','-m','tinystudio.lab.execution'])
        job=json.loads(plan.manifests_json)['items'][1]
        mounts=job['spec']['template']['spec']['containers'][0]['volumeMounts']
        mounted_root=shared/mounts[0]['subPath']
        mounted_output=shared/mounts[1]['subPath']
        assert mounted_root==root
        assert (mounted_root/'spec.json').read_text()=='owned specification'
        assert (mounted_root/'runtime/tinystudio/fixture.py').read_text()=='owned source'
        assert (mounted_root/'inputs/source.json').read_text()=='owned input'
        assert mounted_output==root/'work'
        assert mounts[0]['readOnly'] is True and mounts[1]['readOnly'] is False
        plans.append(plan)

    monkeypatch.setattr(worker,'execute_attempt',stage_manifest)
    assert worker.run_once()
    assert len(plans)==1
