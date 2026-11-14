"""Lease termination records host authority and never invents a physical result."""
import sqlite3
import threading
import hashlib
import json
import uuid

import pytest

from tinystudio.lab.gateway import CommandGateway, GatewayError
from tests.test_lab_native_gateway import Clock, Device, binding, dispatch, payload


def selected(**policy):
    return binding(local_stop=payload('OUTPUT OFF\n'),lease_policy=policy)


def test_clean_release_is_durable_idempotent_and_does_not_stop_a_later_owner(tmp_path):
    clock=Clock();device=Device();path=tmp_path/'gateway.sqlite3'
    with CommandGateway(path,binding(),device,clock=clock) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        dispatch(gateway,lease)
        receipt=gateway.release('release-one',owner=lease['owner'],epoch=lease['epoch'])
        assert receipt['trigger']=='release' and receipt['action']=='revoke_only'
        assert receipt['host_state']=='idle' and receipt['physical_outcome']=='unverified'
        assert gateway.snapshot()['state']=='idle'
        later=gateway.acquire('run-two',ttl=20)
        clock.value=1040.
        assert gateway.release('release-one',owner=lease['owner'],epoch=lease['epoch'])==receipt
        assert len(device.effects)==1
        assert gateway.lease_events()[0]['receipt']==receipt
        assert later['epoch']>lease['epoch']
    with CommandGateway(path,binding(),Device(),clock=clock) as restarted:
        assert restarted.release('release-one',owner=lease['owner'],epoch=lease['epoch'])==receipt


def test_release_rejects_stale_identity_and_changed_request_without_effects(tmp_path):
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        with pytest.raises(GatewayError):gateway.release('bad',owner='other',epoch=lease['epoch'])
        with pytest.raises(GatewayError):gateway.release('bad',owner=lease['owner'],epoch=True)
        gateway.release('done',owner=lease['owner'],epoch=lease['epoch'])
        with pytest.raises(GatewayError):gateway.release('done',owner='other',epoch=lease['epoch'])
        assert device.effects==[] and len(gateway.lease_events())==1


def test_release_revoke_only_during_setup_never_sends_implicit_cancel_stop(tmp_path):
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_release='revoke_only'),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        def terminate():
            device.before_write=lambda:None
            gateway.release('release-during-setup',owner=lease['owner'],epoch=lease['epoch'])
        device.before_write=terminate
        with pytest.raises(GatewayError):dispatch(gateway,lease)
        assert device.effects==[]
        assert gateway.snapshot()['state']=='recovery_required'
        assert len(gateway.lease_events())==1
        assert gateway.journal()[0]['state']=='unknown'


def test_expiry_action_is_explicit_and_can_be_processed_after_lazy_expiry(tmp_path):
    clock=Clock();device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_expiry='local_stop'),device,clock=clock) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        clock.value=1021.
        assert gateway.snapshot()['state']=='recovery_required'
        assert device.effects==[]
        receipts=gateway.watchdog_tick()
        assert len(receipts)==1 and receipts[0]['trigger']=='expiry'
        assert receipts[0]['lease_epoch']==lease['epoch'] and receipts[0]['lease_expires']==1020.
        assert receipts[0]['physical_outcome']=='unverified'
        assert [item['kind'] for item in device.effects]==['local_stop']
        assert gateway.watchdog_tick()==[]
        assert gateway.snapshot()['state']=='recovery_required'


def test_reconciliation_supersedes_old_expiry_action_before_new_acquisition(tmp_path):
    clock=Clock();device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_expiry='local_stop'),device,clock=clock) as gateway:
        gateway.acquire('old-run',ttl=20)
        clock.value=1021.
        gateway.snapshot()
        gateway.reconcile('operator-one',evidence={'acknowledge_unverified_outcomes':True})
        lease=gateway.acquire('new-run',ttl=20)
        assert gateway.watchdog_tick()==[]
        gateway.dispatch('new-command',owner=lease['owner'],epoch=lease['epoch'],payload=payload(),expires_at=1030.)
        assert [item['kind'] for item in device.effects]==['command']
        assert gateway.lease_events()[0]['state']=='superseded'


def test_heartbeat_extension_is_observed_by_watchdog_without_old_deadline_action(tmp_path):
    clock=Clock();device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_expiry='local_stop'),device,clock=clock) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        clock.value=1019.
        gateway.heartbeat(lease['owner'],lease['epoch'],ttl=20)
        clock.value=1021.
        assert gateway.watchdog_tick()==[] and gateway.lease_events()==[]
        assert gateway.snapshot()['state']=='owned' and device.effects==[]


def test_cancellation_can_select_revoke_only_without_an_implicit_stop(tmp_path):
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_cancel='revoke_only'),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        with pytest.raises(GatewayError):dispatch(gateway,lease,cancelled=lambda:True)
        assert device.effects==[]
        assert gateway.snapshot()['state']=='recovery_required'
        assert gateway.lease_events()[0]['trigger']=='cancel'


def test_selected_stop_failure_keeps_delivery_evidence_and_is_never_replayed(tmp_path):
    device=Device();device.failure=TimeoutError('owned lost receipt')
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_release='local_stop'),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        with pytest.raises(GatewayError):gateway.release('release-one',owner=lease['owner'],epoch=lease['epoch'])
        assert len(device.effects)==1
        event=gateway.lease_events()[0]
        assert event['state']=='unknown' and event['receipt']['physical_outcome']=='unverified'
        assert event['receipt']['delivery_evidence']
        with pytest.raises(GatewayError):gateway.release('release-one',owner=lease['owner'],epoch=lease['epoch'])
        assert gateway.watchdog_tick()==[] and len(device.effects)==1


def test_release_stop_during_setup_survives_the_old_commands_unknown_outcome(tmp_path):
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_release='local_stop'),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        def terminate():
            device.before_write=lambda:None
            gateway.release('release-during-setup',owner=lease['owner'],epoch=lease['epoch'])
        device.before_write=terminate
        with pytest.raises(GatewayError):dispatch(gateway,lease)
        assert [item['kind'] for item in device.effects]==['local_stop']
        event=gateway.lease_events()[0]
        assert event['state']=='completed'
        assert event['revocation_epoch']==gateway.snapshot()['epoch']
        assert gateway.watchdog_tick()==[]


def test_missing_configured_stop_is_explicit_without_a_device_write(tmp_path):
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(lease_policy={'on_release':'local_stop'}),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        receipt=gateway.release('release-one',owner=lease['owner'],epoch=lease['epoch'])
        assert receipt['delivery_receipt']['status']=='not_configured'
        assert receipt['physical_outcome']=='unverified' and device.effects==[]


def test_intent_commit_failure_prevents_stop_and_preserves_original_authority(tmp_path,monkeypatch):
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_release='local_stop'),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        original=gateway._commit
        def fail():
            if gateway._db.execute('SELECT 1 FROM lease_events').fetchone():
                gateway._rollback(RuntimeError('owned fault'))
                raise sqlite3.OperationalError('owned intent commit failure')
            original()
        monkeypatch.setattr(gateway,'_commit',fail)
        with pytest.raises(sqlite3.OperationalError):gateway.release('release-one',owner=lease['owner'],epoch=lease['epoch'])
        monkeypatch.setattr(gateway,'_commit',original)
        assert device.effects==[] and gateway.lease_events()==[]
        dispatch(gateway,lease)
        assert gateway.snapshot()['state']=='owned'


def test_lifecycle_receipt_commit_failure_preserves_observed_stop_without_replay(tmp_path,monkeypatch):
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_release='local_stop'),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        original=gateway._commit;injected=[]
        def fail():
            complete=gateway._db.execute("SELECT 1 FROM lease_events WHERE state='completed'").fetchone()
            if complete and not injected:
                injected.append(True);gateway._rollback(RuntimeError('owned fault'))
                raise sqlite3.OperationalError('owned lifecycle receipt commit failure')
            original()
        monkeypatch.setattr(gateway,'_commit',fail)
        with pytest.raises(sqlite3.OperationalError):gateway.release('release-one',owner=lease['owner'],epoch=lease['epoch'])
        event=gateway.lease_events()[0]
        assert event['state']=='unknown' and len(device.effects)==1
        evidence=event['receipt']['delivery_evidence']
        assert evidence['classification']=='lease_receipt_commit_unconfirmed'
        assert evidence['observed_delivery']['status']=='delivered_unverified'
        assert gateway.watchdog_tick()==[] and gateway.snapshot()['state']=='recovery_required'


@pytest.mark.parametrize('claimed',[False,True])
def test_restart_resumes_only_unclaimed_intent_and_never_replays_an_executing_action(tmp_path,claimed):
    path=tmp_path/'gateway.sqlite3';clock=Clock();config=selected(on_expiry='local_stop')
    with CommandGateway(path,config,Device(),clock=clock) as gateway:
        gateway.acquire('run-one',ttl=20);clock.value=1021.;gateway.snapshot()
        if claimed:
            with gateway._transaction():gateway._db.execute("UPDATE lease_events SET state='executing'")
    device=Device()
    with CommandGateway(path,config,device,clock=clock) as restarted:
        receipts=restarted.watchdog_tick()
        if claimed:
            assert receipts==[] and device.effects==[]
            assert restarted.lease_events()[0]['state']=='unknown'
        else:
            assert len(receipts)==1 and len(device.effects)==1
        assert restarted.snapshot()['state']=='recovery_required'


def test_revocation_interrupts_an_inflight_read_without_an_extra_stop(tmp_path):
    entered=threading.Event();finished=threading.Event();cancel_seen=threading.Event();failures=[]
    class ReadingDevice(Device):
        def exchange(self,request,*,timeout,cancelled,authorize):
            result=super().exchange(request,timeout=timeout,cancelled=cancelled,authorize=authorize)
            entered.set()
            while not finished.wait(.001):
                if cancelled():
                    cancel_seen.set();error=TimeoutError('owned cancelled read');error.delivery_evidence=result;raise error
            return result
    device=ReadingDevice()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_release='revoke_only'),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        def run():
            try:dispatch(gateway,lease)
            except Exception as error:failures.append(error)
        thread=threading.Thread(target=run);thread.start()
        try:
            assert entered.wait(2)
            receipt=gateway.release('release-read',owner=lease['owner'],epoch=lease['epoch'])
            assert cancel_seen.is_set() and receipt['host_state']=='recovery_required'
            assert receipt['physical_outcome']=='unverified'
        finally:
            finished.set();thread.join(2)
        assert not thread.is_alive() and len(failures)==1
        assert [request['kind'] for request in device.effects]==['command']


def test_cancellation_records_the_renewed_lease_deadline_after_an_inflight_failure(tmp_path):
    device=Device();cancelled=[]
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_cancel='revoke_only'),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        def renew_then_cancel():
            gateway.heartbeat(lease['owner'],lease['epoch'],ttl=40)
            cancelled.append(True)
        device.before_write=renew_then_cancel
        with pytest.raises(GatewayError):dispatch(gateway,lease,cancelled=lambda:bool(cancelled))
        assert gateway.lease_events()[0]['lease_expires']==1040.
        assert device.effects==[]


def test_post_commit_error_reads_back_completed_receipt_without_creating_unknown_idle_state(tmp_path,monkeypatch):
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_release='local_stop'),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        original=gateway._commit;injected=[]
        def fail_after_commit():
            complete=gateway._db.execute("SELECT 1 FROM lease_events WHERE state='completed'").fetchone()
            original()
            if complete and not injected:
                injected.append(True)
                raise sqlite3.OperationalError('owned commit acknowledgement loss')
        monkeypatch.setattr(gateway,'_commit',fail_after_commit)
        receipt=gateway.release('release-one',owner=lease['owner'],epoch=lease['epoch'])
        assert receipt['state']=='completed' and len(device.effects)==1
        assert gateway.lease_events()[0]['state']=='completed'
        assert gateway.release('release-one',owner=lease['owner'],epoch=lease['epoch'])==receipt
        gateway.acquire('new-run',ttl=20)


def test_arbitrary_release_identity_cannot_collide_with_a_generated_expiry_identity(tmp_path):
    clock=Clock();device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(),device,clock=clock) as gateway:
        lease=gateway.acquire('old-run',ttl=20)
        future={'endpoint_id':'owned-native','owner':'next-run','lease_epoch':lease['epoch']+2,
                'lease_expires':1020.,'trigger':'expiry'}
        identity='expiry:'+hashlib.sha256(json.dumps(future,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        gateway.release(identity,owner=lease['owner'],epoch=lease['epoch'])
        next_lease=gateway.acquire('next-run',ttl=20)
        assert next_lease['epoch']==future['lease_epoch']
        clock.value=1021.
        assert gateway.snapshot()['state']=='recovery_required'
        receipts=gateway.watchdog_tick()
        assert receipts[0]['event_id']==identity
        events=gateway.lease_events()
        assert len(events)==2 and events[0]['event_key']!=events[1]['event_key']
        gateway.reconcile('operator-one',evidence={'acknowledge_unverified_outcomes':True})
        assert gateway.snapshot()['state']=='idle' and device.effects==[]


def test_completed_release_readback_never_revokes_a_new_owner_after_acknowledgement_loss(tmp_path,monkeypatch):
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected(on_release='local_stop'),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        original=gateway._commit;later=[]
        def lose_ack_after_new_acquisition():
            completed=gateway._db.execute("SELECT 1 FROM lease_events WHERE state='completed'").fetchone()
            original()
            if completed and not later:
                later.append(True)
                gateway.acquire('new-run',ttl=20)
                raise sqlite3.OperationalError('owned lost acknowledgement after next acquisition')
        monkeypatch.setattr(gateway,'_commit',lose_ack_after_new_acquisition)
        receipt=gateway.release('release-one',owner=lease['owner'],epoch=lease['epoch'])
        assert receipt['state']=='completed' and len(device.effects)==1
        state=gateway.snapshot()
        assert state['state']=='owned' and state['owner']=='new-run'
        assert gateway.lease_events()[0]['state']=='completed'
        assert gateway.watchdog_tick()==[]


@pytest.mark.parametrize('claimed',[False,True])
def test_restart_preserves_expiry_fence_with_a_crashed_pending_command(tmp_path,monkeypatch,claimed):
    class OwnedCrash(BaseException):pass
    path=tmp_path/'gateway.sqlite3';clock=Clock();device=Device()
    config=selected(on_expiry='local_stop')
    with CommandGateway(path,config,device,clock=clock) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        original=gateway._commit;injected=[]
        def commit_then_crash():
            expiry=gateway._db.execute("SELECT 1 FROM lease_events WHERE trigger='expiry' AND state='pending'").fetchone()
            pending=gateway._db.execute("SELECT 1 FROM commands WHERE state='pending'").fetchone()
            original()
            if expiry and pending and not injected:
                injected.append(True)
                raise OwnedCrash('owned crash after durable expiry, before command outcome recording')
        monkeypatch.setattr(gateway,'_commit',commit_then_crash)
        device.before_write=lambda:setattr(clock,'value',1021.)
        with pytest.raises(OwnedCrash):dispatch(gateway,lease)
        monkeypatch.setattr(gateway,'_commit',original)
        event=gateway.lease_events()[0]
        assert event['state']=='pending' and gateway.journal()[0]['state']=='pending'
        assert device.effects==[]
        if claimed:
            with gateway._transaction():gateway._db.execute("UPDATE lease_events SET state='executing'")
    restarted_device=Device()
    with CommandGateway(path,config,restarted_device,clock=clock) as restarted:
        assert restarted.snapshot()['epoch']==event['revocation_epoch']
        receipts=restarted.watchdog_tick()
        if claimed:
            assert receipts==[] and restarted_device.effects==[]
            assert restarted.lease_events()[0]['state']=='unknown'
        else:
            assert len(receipts)==1 and receipts[0]['state']=='completed'
            assert [row['kind'] for row in restarted_device.effects]==['local_stop']
        assert restarted.watchdog_tick()==[]
        assert restarted.journal()[0]['state']=='pending'
        assert restarted.snapshot()['state']=='recovery_required'


@pytest.mark.parametrize('persisted_state',['owned','idle'])
def test_restart_still_revokes_unfenced_pending_commands(tmp_path,persisted_state):
    class OwnedCrash(BaseException):pass
    path=tmp_path/'gateway.sqlite3';clock=Clock();device=Device()
    with CommandGateway(path,binding(),device,clock=clock) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        def crash_before_write():raise OwnedCrash('owned interrupted admitted command')
        device.before_write=crash_before_write
        with pytest.raises(OwnedCrash):dispatch(gateway,lease)
        if persisted_state=='idle':
            # Represent an inconsistent persisted idle journal with unsettled work.
            with gateway._transaction():
                gateway._db.execute("UPDATE gateway SET state='idle',owner=NULL,lease_expires=0")
        assert gateway.journal()[0]['state']=='pending'
    with CommandGateway(path,binding(),Device(),clock=clock) as restarted:
        state=restarted.snapshot()
        assert state['state']=='recovery_required' and state['epoch']>lease['epoch']
        with pytest.raises(GatewayError):dispatch(restarted,lease)
        assert restarted.lease_events()==[]


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('trigger',['expiry','release'])
def test_selected_stop_does_not_collide_with_an_authored_predictable_old_stop_id(tmp_path,legacy,trigger):
    if legacy:
        from tests.test_lab_gateway import Clock as LegacyClock,Device as LegacyDevice,binding as legacy_binding
        clock=LegacyClock();device=LegacyDevice(clock);config=legacy_binding()
        config['lease_policy']={'on_expiry':'local_stop','on_release':'local_stop'}
    else:
        clock=Clock();device=Device();config=selected(on_expiry='local_stop',on_release='local_stop')
    with CommandGateway(tmp_path/'gateway.sqlite3',config,device,clock=clock) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        identity={'endpoint_id':lease['endpoint_id'],'owner':lease['owner'],'lease_epoch':lease['epoch'],
                  'lease_expires':lease['expires_at'],'trigger':trigger}
        event_id='authored-release' if trigger=='release' else 'expiry:'+hashlib.sha256(
            json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        event_key=json.dumps([trigger,event_id],separators=(',',':'))
        old_stop_id='lease-stop:'+hashlib.sha256(event_key.encode()).hexdigest()
        command={'parameters':{'voltage':{'value':1,'unit':'V'}}} if legacy else {'payload':payload()}
        gateway.dispatch(old_stop_id,owner=lease['owner'],epoch=lease['epoch'],expires_at=1010.,**command)
        if trigger=='expiry':
            if legacy:clock.now=1021.
            else:clock.value=1021.
            receipt=gateway.watchdog_tick()[0]
        else:
            receipt=gateway.release(event_id,owner=lease['owner'],epoch=lease['epoch'])
            assert gateway.release(event_id,owner=lease['owner'],epoch=lease['epoch'])==receipt
        assert receipt['state']=='completed' and receipt['stop_command_id']!=old_stop_id
        assert [row['kind'] for row in device.effects]==['command','local_stop']
        assert gateway.watchdog_tick()==[]


def test_stop_identity_skips_an_occupied_random_candidate_and_commits_before_write(tmp_path,monkeypatch):
    path=tmp_path/'gateway.sqlite3';candidates=[uuid.UUID('a'*32),uuid.UUID('b'*32)]
    occupied='lease-stop:'+candidates[0].hex;fresh='lease-stop:'+candidates[1].hex
    admitted=[]
    class InspectingDevice(Device):
        def exchange(self,request,**kwargs):
            if request['kind']=='local_stop':
                with sqlite3.connect(path) as reader:
                    assert reader.execute('SELECT state FROM lease_events WHERE stop_command_id=?',
                                          (request['command_id'],)).fetchone()[0]=='executing'
                    assert reader.execute('SELECT state FROM commands WHERE command_id=?',
                                          (request['command_id'],)).fetchone()[0]=='pending'
                admitted.append(request['command_id'])
            return super().exchange(request,**kwargs)
    device=InspectingDevice()
    with CommandGateway(path,selected(on_release='local_stop'),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        gateway.dispatch(occupied,owner=lease['owner'],epoch=lease['epoch'],payload=payload(),expires_at=1010.)
        sequence=iter(candidates)
        monkeypatch.setattr(uuid,'uuid4',lambda:next(sequence))
        receipt=gateway.release('release-one',owner=lease['owner'],epoch=lease['epoch'])
        assert receipt['stop_command_id']==fresh and admitted==[fresh]
        assert [row['command_id'] for row in gateway.journal()]==[occupied,fresh]
        assert [row['kind'] for row in device.effects]==['command','local_stop']
