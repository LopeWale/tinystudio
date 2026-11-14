"""Owned MQTT/native disconnect -> retained bytes -> authenticated recovery.

This proves software evidence transport, not device shutdown, independence,
filesystem power-loss survival, or physical/scientific validity.
"""
from datetime import timedelta
import hashlib
import json

import httpx
import pytest

from hardware_emulators import MqttTestDevice
from tests.test_lab_hardware_study_workflow import owned_study_broker
from tests.test_lab_native_workflow import native_http, posted
from tinystudio.lab.evidence_recovery_client import RecoveryArchiveClient
from tinystudio.lab.evidence_spool import EvidenceSpool, SpoolPolicy
from tinystudio.lab.models import LabAttempt
from tinystudio.lab.runner import Runner
from tinystudio.lab import service as svc


def test_synced_native_capture_survives_origin_disconnect_and_recovers_without_execution_authority(
        native_http,owned_study_broker,monkeypatch):
    f,broker=native_http,owned_study_broker
    target=posted(f.http,f.prefix+'/targets',json={'name':'owned-recovery-mqtt'})
    topics={name:'owned-recovery/'+name for name in ('command','ack','telemetry')}
    root=f.root/'persistent-host-evidence'
    binding={'engine':'acquisition.mqtt','read_only':True,
        'endpoint':{'host':broker.host,'port':broker.port,'topic':topics['telemetry'],'tls':False},
        'source_clock':{'domain':'owned-simulator-host-unix','kind':'unix_seconds','synchronized':False}}
    runner=Runner(f.url,target['id'],target['token'],heartbeat_interval=.03,process_grace=.3,
        profile_config={'schema_version':1,'backends':{},'evidence_spool':{'root':str(root)},
            'engine_grants':{'acquisition.mqtt':{'bindings':{'owned':binding}}}})
    try:
        runner.advertise()
        definition={'name':'Owned disconnect capture','engine':'acquisition.mqtt','domain':'hardware',
            'mode':'online','operating_mode':'acquisition',
            'parameters':{'binding_id':'owned','samples':None,'duration':20,'timeout':1,
                'decode':'json','value_key':'value','source_id_key':'record_id',
                'timestamp_key':'source_timestamp','evidence':{'flush_records':1,'fsync':True}},
            'execution':{'profile':'trusted_native','backend':'native','max_seconds':30}}
        saved=posted(f.http,f.prefix+'/definitions',json={'definition':definition})
        submitted=posted(f.http,f.prefix+'/runs',json={'definition_id':saved['id'],'revision':1,
            'target_id':target['id'],'idempotency_key':'disconnect-owned-capture'})
        original_post=runner.post
        disconnected=[]
        def post(path,body,lease=None):
            if path.endswith('/heartbeat') and any(
                    event.get('type')=='acquisition_progress' for event in body.get('events',[])):
                disconnected.append(True)
                raise httpx.ConnectError('owned fault: origin unavailable after fsynced observation')
            return original_post(path,body,lease)
        monkeypatch.setattr(runner,'post',post)
        initial={'diagnostic':{'value':.25,'unit':'fixture_units'}}
        with MqttTestDevice(broker,topics,'owned-recovery-reset',initial,stop_parameters=initial,
                telemetry_parameter='diagnostic',sample_period=.03,noise=0,seed=2026) as device:
            with pytest.raises(RuntimeError,match='lost lease connectivity'):
                runner.run_once(run_id=submitted['id'])
            assert disconnected
            assert device.received_requests==[] and device.effects==[]
        spool=EvidenceSpool(SpoolPolicy(True,root))
        entries=spool.list_attempts()
        assert len(entries)==1
        retained=spool.open_attempt(entries[0]['key'])
        snapshot=retained.snapshot()
        assert snapshot['manifest']['cleanup']['status']=='confirmed'
        assert snapshot['manifest']['publication']['status']=='unknown'
        assert not (retained.execution_root/'context.json').exists()
        raw_path=retained.execution_root/'work/evidence/raw.jsonl'
        original_bytes=raw_path.read_bytes()
        raw_rows=[json.loads(line) for line in original_bytes.splitlines()]
        assert raw_rows and any(row['source_identity'] for row in raw_rows)
        # The server never received a finish acknowledgement. Advance only this
        # owned fixture's lease clock, then use explicit project recovery.
        with f.factory.begin() as db:
            attempt=db.get(LabAttempt,snapshot['manifest']['origin']['attempt_id'])
            assert attempt.state=='running'
            attempt.lease_until=svc.now()-timedelta(seconds=1)
        recovered=posted(f.http,f.prefix+'/targets/'+target['id']+'/recover',json={})
        assert recovered['recovered']==1
        before=f.http.get(f.prefix+'/runs/'+submitted['id']).json()
        assert before['state']=='recovery_required' and before['result'] is None
        archive=f.root/'exported-evidence.zip'
        retained.export(archive,expected_manifest_digest=snapshot['manifest_sha256'])
        client=RecoveryArchiveClient(f.http,f.prefix)
        attached=client.import_archive(archive,idempotency_key='owned-disconnect-snapshot')
        receipt=f.http.get(f.prefix+'/artifacts/'+attached['receipt']['id']).json()
        assert receipt['qualification']['execution_authority_granted'] is False
        file=next(item for item in receipt['files'] if item['name']=='evidence/raw.jsonl')
        uploaded=f.http.get(f.prefix+'/artifacts/'+file['artifact']['id']).content
        assert uploaded==original_bytes and hashlib.sha256(uploaded).hexdigest()==file['verified_bytes']['sha256']
        after=f.http.get(f.prefix+'/runs/'+submitted['id']).json()
        for key in ('state','result','attempts','spec_digest','error'):
            assert after[key]==before[key]
        archive.unlink()
        retry=client.import_archive(archive,idempotency_key='owned-disconnect-snapshot')
        assert retry['receipt']==attached['receipt']
        references=f.http.get(f.prefix+'/runs/'+submitted['id']+'/recovered-evidence').json()['references']
        assert len(references)==1
    finally:
        runner.client.close()
