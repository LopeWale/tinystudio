"""Parent-owned session authority, byte-preserving proposals and receipt evidence."""
from copy import deepcopy
import json
import socket
import threading
import time
from zipfile import ZipFile

import pytest

from tinystudio.lab.attempt_control import AttemptControl, AttemptIdentity
from tinystudio.lab.hardware_session_bridge import HardwareSessionBridge
from tinystudio.lab.hardware_session_client import HardwareSessionClient, HardwareSessionError
from tinystudio.lab.hardware_session_configuration import HardwareSessionBinding
from tests.test_lab_native_gateway import Device, binding, payload


def setup(tmp_path,**extra):
    state=tmp_path/'private-state';state.mkdir(mode=0o700)
    evidence=tmp_path/'evidence'
    identity=AttemptIdentity('http://127.0.0.1:8000','run-one','attempt-one','target-one','a'*64)
    control=AttemptControl(identity)
    control.renew(30,sent_at=time.monotonic())
    selected=HardwareSessionBinding.from_grants({'native_execution':True,'bindings':{'owned':{
        'gateway_binding':binding(local_stop=payload('OFF\n')),'state_directory':str(state),**extra}}},'owned')
    device=Device()
    bridge=HardwareSessionBridge(selected,control,evidence,runtime_secrets={'password':'owned-test-runtime-secret'},
        transport_factory=lambda selected,*,runtime_secrets:device)
    return bridge,control,device,evidence


def test_actual_ipc_roundtrip_keeps_exact_proposal_and_receipt_without_operator_credentials(tmp_path):
    bridge,control,device,evidence=setup(tmp_path)
    try:
        bridge.start()
        client=HardwareSessionClient(bridge.descriptor)
        request=payload('SET 9007199254740993\r\n')
        first=client.submit('decision-one',request,valid_for_seconds=5)
        assert first['physical_outcome']=='unverified'
        assert client.submit('decision-one',request,valid_for_seconds=5)==first
        assert len(device.effects)==1 and device.effects[0]['payload']==request
        assert device.effects[0]['owner']==control.identity.owner
        with pytest.raises(HardwareSessionError):client.submit('decision-one',payload('DIFFERENT'),valid_for_seconds=5)
        assert len(device.effects)==1
        client.close()
    finally:bridge.close()
    records=[json.loads(line) for line in (evidence/'hardware-session-parent.jsonl').read_text().splitlines()]
    assert any(row.get('decision',{}).get('payload')==request for row in records)
    manifest=json.loads((evidence/'hardware-session-parent.json').read_text())
    assert manifest['identity']==control.identity.as_dict()
    assert 'owned-test-runtime-secret' not in ''.join(path.read_text() for path in evidence.iterdir())


def test_attempt_revocation_during_sdk_setup_prevents_an_ordinary_write(tmp_path):
    bridge,control,device,evidence=setup(tmp_path)
    try:
        bridge.start()
        device.before_write=lambda:control.revoke('owned authority loss during setup')
        with pytest.raises(Exception):bridge.submit('one',payload(),valid_for_seconds=5)
        assert [item for item in device.effects if item['kind']=='command']==[]
        assert all(item['kind']=='local_stop' for item in device.effects)
    finally:bridge.close()


def test_parent_admission_evidence_failure_prevents_any_device_effect(tmp_path,monkeypatch):
    bridge,control,device,evidence=setup(tmp_path)
    try:
        bridge.start()
        def fail(row):raise OSError('owned parent evidence failure')
        monkeypatch.setattr(bridge.writer,'write_record',fail)
        with pytest.raises(OSError):bridge.submit('one',payload(),valid_for_seconds=5)
        assert device.effects==[]
    finally:
        # Restore the fixture patch before emitting shutdown provenance.
        monkeypatch.undo();bridge.close()


def test_receipt_evidence_failure_keeps_gateway_receipt_without_redelivery(tmp_path,monkeypatch):
    bridge,control,device,evidence=setup(tmp_path)
    try:
        bridge.start()
        original=bridge.writer.write_record;failed=[]
        def fail_receipt(row):
            if row['kind']=='receipt' and not failed:
                failed.append(True);raise OSError('owned receipt evidence failure')
            original(row)
        monkeypatch.setattr(bridge.writer,'write_record',fail_receipt)
        with pytest.raises(Exception):bridge.submit('one',payload(),valid_for_seconds=5)
        assert len(device.effects)==1
        receipt=bridge.submit('one',payload(),valid_for_seconds=5)
        assert receipt['status']=='delivered_unverified' and len(device.effects)==1
    finally:bridge.close()


def test_revoked_attempt_allows_only_the_selected_scoped_cleanup_stop(tmp_path):
    bridge,control,device,evidence=setup(tmp_path,gateway_binding=binding(local_stop=payload('OFF\n'),
        lease_policy={'on_release':'local_stop','on_cancel':'revoke_only'}))
    try:
        bridge.start();bridge.submit('one',payload(),valid_for_seconds=5)
        control.revoke('owned cancellation')
        with pytest.raises(Exception):bridge.submit('two',payload(),valid_for_seconds=5)
        bridge.close()
        assert [item['kind'] for item in device.effects]==['command','local_stop']
    finally:bridge.close()


def test_ipc_descriptor_is_defensive_and_closed_session_leaves_no_listener(tmp_path):
    bridge,control,device,evidence=setup(tmp_path)
    bridge.start();descriptor=bridge.descriptor
    changed=deepcopy(descriptor);changed['profile']['format']='changed'
    assert bridge.descriptor['profile']['format']!='changed'
    bridge.close();bridge.close()
    with pytest.raises((HardwareSessionError,OSError)):
        HardwareSessionClient(descriptor,timeout=.2).submit('one',payload(),valid_for_seconds=5)
    assert device.effects==[]


def test_accepted_client_after_close_snapshot_cannot_leave_the_parent_reader_alive(tmp_path):
    bridge,control,device,evidence=setup(tmp_path)
    bridge.start();accepted=threading.Event();resume=threading.Event();shutdown=threading.Event();done=threading.Event();gate=threading.Event()
    original=bridge._listener
    class Gated:
        def __getattr__(self,name):return getattr(original,name)
        def accept(self):
            result=original.accept()
            if gate.is_set():
                accepted.set();assert resume.wait(2)
            return result
        def shutdown(self,*args):
            result=original.shutdown(*args);shutdown.set();return result
    bridge._listener=Gated()
    # A running accept may already have resolved the prior listener. Wake that
    # call first, then gate the next accept before exercising close.
    wake=HardwareSessionClient(bridge.descriptor)
    wake.submit('wake',payload(),valid_for_seconds=5);wake.close()
    gate.set()
    client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    client.connect(bridge.descriptor['path'])
    closer=None
    try:
        assert accepted.wait(2)
        def close():
            try:bridge.close()
            finally:done.set()
        closer=threading.Thread(target=close);closer.start()
        assert shutdown.wait(2)
        resume.set()
        assert done.wait(1),'accepted unregistered client kept the parent server alive'
        assert bridge.cleanup_confirmed
    finally:
        resume.set();client.close()
        if closer is not None:closer.join(2)
        bridge.close()


def test_generated_identifiers_honor_a_selected_64_character_identity_budget(tmp_path):
    gateway=binding(local_stop=payload('OFF\n'))
    gateway['command_profile']['max_identity_chars']=64
    bridge,control,device,evidence=setup(tmp_path,gateway_binding=gateway)
    try:
        bridge.start();receipt=bridge.submit('one',payload(),valid_for_seconds=5)
        assert len(receipt['command_id'])==64 and len(device.effects)==1
        outcome=bridge.close()
        assert outcome['release_receipt']['host_state']=='idle'
    finally:bridge.close()


def test_parent_receipt_remains_in_supported_spool_export_after_final_manifest_failure(tmp_path,monkeypatch):
    from tinystudio.lab.contracts import checksum
    from tinystudio.lab.evidence_spool import EvidenceSpool,SpoolPolicy
    from tinystudio.lab.hardware_session_bridge import SessionBridgePaths
    spec={'definition':{'engine':'hardware.session'},'implementation':{'version':1,'files':{}},'runtime':{}}
    attempt={'run_id':'run-one','attempt_id':'attempt-one','spec':spec,'spec_digest':checksum(spec)}
    retained=EvidenceSpool(SpoolPolicy(enabled=True,root=tmp_path/'spool')).allocate(attempt,
        server='http://127.0.0.1:8000',target_id='target-one')
    bridge,control,device,evidence=setup(tmp_path)
    bridge.evidence_directory=SessionBridgePaths.evidence_in_work(retained.execution_root/'work')
    bridge.start();bridge.submit('one',payload(),valid_for_seconds=5)
    def fail():raise OSError('owned final manifest storage failure')
    monkeypatch.setattr(bridge,'_manifest',fail)
    with pytest.raises(OSError):bridge.close()
    assert bridge.cleanup_confirmed
    retained.mark_cleanup(True)
    snapshot=retained.snapshot()
    ledger=[row for row in snapshot['files'] if row['path'].endswith('hardware-session-parent.jsonl')]
    assert len(ledger)==1,'raw parent receipt is outside supported recovery inventory'
    archive=tmp_path/'recovery.zip'
    retained.export(archive,expected_manifest_digest=snapshot['manifest_sha256'])
    with ZipFile(archive) as opened:
        rows=[json.loads(line) for line in opened.read(ledger[0]['path']).splitlines()]
    assert any(row['kind']=='receipt' and row['receipt']['status']=='delivered_unverified' for row in rows)
