"""Native delivery remains experimentable without invented application proofs."""
from copy import deepcopy
import base64
import hashlib
import json

import pytest

from tinystudio.lab.gateway import CommandGateway, GatewayError


class Clock:
    def __init__(self):self.value=1000.
    def __call__(self):return self.value


def binding(**extra):
    return {'endpoint_id':'owned-native','operator_id':'operator-one',
        'transport':{'kind':'serial','port':'loop://','baudrate':9600},
        'command_profile':{'format':'tinystudio.native-command-profile.v1'},
        'send_timeout':.2,**extra}


def payload(text='SET 9007199254740993\r\n'):
    return {'operation':'serial.write','data':{'encoding':'utf8','value':text}}


class Device:
    kind='serial'
    def __init__(self):
        self.effects=[];self.before_write=lambda:None;self.failure=None;self.mutate=lambda ack:None
    def exchange(self,request,*,timeout,cancelled,authorize):
        self.before_write()
        selected=authorize()
        assert selected==request
        assert not cancelled()
        self.effects.append(deepcopy(selected))
        data=selected['payload']['data']['value'].encode()
        ack={'format':'tinystudio.command-delivery.v1',
            **{key:selected[key] for key in ('endpoint_id','command_id','epoch','profile_digest')},
            'status':'delivered_unverified','device_application_confirmed':False,'physical_outcome':'unverified',
            'transport':{'kind':'serial','operation':'serial.write'},
            'request':{'scope':'owned_fixture_bytes','size':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                'bytes_base64':base64.b64encode(data).decode()},'response':None}
        self.mutate(ack)
        if self.failure:
            self.failure.delivery_evidence=ack
            raise self.failure
        return ack


def dispatch(gateway,lease,id_='command-one',**extra):
    return gateway.dispatch(id_,owner=lease['owner'],epoch=lease['epoch'],
        payload=payload(),expires_at=1010.,**extra)


def test_native_delivery_is_retained_and_can_continue_without_application_ack(tmp_path):
    device=Device();clock=Clock()
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(),device,clock=clock) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        receipt=dispatch(gateway,lease)
        assert receipt['status']=='delivered_unverified'
        assert receipt['device_application_confirmed'] is False and receipt['physical_outcome']=='unverified'
        assert dispatch(gateway,lease)==receipt
        dispatch(gateway,lease,'command-two')
        assert len(device.effects)==2
        assert device.effects[0]['payload']==payload()
        assert '9007199254740993' in device.effects[0]['payload']['data']['value']
        assert gateway.snapshot()['state']=='owned'
        assert [row['state'] for row in gateway.journal()]==['delivered_unverified','delivered_unverified']
        assert gateway.snapshot()['command_profile']['format']=='tinystudio.native-command-profile.v1'


def test_native_final_authority_is_checked_after_setup_without_repeating_effect(tmp_path):
    clock=Clock();device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(),device,clock=clock) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        device.before_write=lambda:setattr(clock,'value',1030.)
        with pytest.raises(GatewayError):dispatch(gateway,lease)
        assert device.effects==[]
        assert gateway.snapshot()['state']=='recovery_required'
        assert gateway.journal()[0]['state']=='unknown'


def test_native_failure_keeps_partial_delivery_evidence_and_consumes_id(tmp_path):
    device=Device();device.failure=TimeoutError('owned response lost after write')
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        with pytest.raises(GatewayError) as caught:dispatch(gateway,lease)
        assert len(device.effects)==1
        row=gateway.journal()[0]
        assert row['state']=='unknown'
        assert json.loads(row['ack'])['request']['bytes_base64']
        assert caught.value.delivery_receipt['physical_outcome']=='unverified'
        with pytest.raises(GatewayError):dispatch(gateway,lease)
        assert len(device.effects)==1


def test_native_unconfigured_stop_revokes_authority_without_claiming_a_stop(tmp_path):
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        receipt=gateway.local_stop('operator-one',request_id='stop-one')
        assert receipt['status']=='not_configured' and receipt['physical_outcome']=='unverified'
        assert gateway.local_stop('operator-one',request_id='stop-one')==receipt
        assert device.effects==[]
        assert gateway.snapshot()['state']=='recovery_required'
        assert gateway.journal()[0]['state']=='not_configured'
        with pytest.raises(GatewayError):dispatch(gateway,lease)


def test_native_fixed_operator_stop_is_distinct_from_command_allowlist(tmp_path):
    selected=binding(local_stop=payload('OUTPUT OFF\n'))
    selected['command_profile']['allowed_operations']=['serial.query']
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',selected,device,clock=Clock()) as gateway:
        gateway.acquire('run-one',ttl=20)
        receipt=gateway.local_stop('operator-one',request_id='stop-one')
        assert receipt['status']=='delivered_unverified'
        assert device.effects[0]['payload']==payload('OUTPUT OFF\n')
        assert device.effects[0]['kind']=='local_stop'


def test_native_operator_reconciliation_preserves_unknown_effects_as_claims(tmp_path):
    device=Device();device.failure=TimeoutError('owned uncertain outcome')
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        with pytest.raises(GatewayError):dispatch(gateway,lease)
        with pytest.raises(GatewayError):gateway.reconcile('operator-one')
        evidence={'acknowledge_unverified_outcomes':True,'notes':'Owned fixture manually inspected; physical effect remains unknown.'}
        result=gateway.reconcile('operator-one',evidence=evidence)
        assert result['state']=='idle' and result['replay'] is False
        assert result['evidence']['classification']=='operator_attestation'
        assert result['evidence']['physical_outcome']=='unverified'
        assert gateway.journal()[0]['state']=='reconciled_consumed'
        device.failure=None
        new=gateway.acquire('run-two',ttl=20)
        with pytest.raises(GatewayError):dispatch(gateway,new)
        assert len(device.effects)==1


def test_completed_native_receipt_can_be_redelivered_after_lease_expiry_without_authority(tmp_path):
    clock=Clock();device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(),device,clock=clock) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        original=dispatch(gateway,lease)
        clock.value=1040.
        assert dispatch(gateway,lease)==original
        assert len(device.effects)==1
        assert gateway.snapshot()['state']=='recovery_required'
        with pytest.raises(GatewayError):dispatch(gateway,lease,'a-new-effect')
        assert len(device.effects)==1


def test_completed_stop_replay_does_not_cancel_a_later_owner_or_send_another_stop(tmp_path):
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(local_stop=payload('OFF\n')),device,clock=Clock()) as gateway:
        gateway.acquire('run-one',ttl=20)
        stopped=gateway.local_stop('operator-one',request_id='stop-original')
        gateway.reconcile('operator-one',evidence={'acknowledge_unverified_outcomes':True})
        later=gateway.acquire('run-two',ttl=20)
        assert gateway.local_stop('operator-one',request_id='stop-original')==stopped
        receipt=dispatch(gateway,later,'new-observation-command')
        assert receipt['status']=='delivered_unverified'
        assert len(device.effects)==2
        assert device.effects[0]['payload']==payload('OFF\n')
        assert device.effects[1]['payload']==payload()
        assert gateway.snapshot()['state']=='owned'


def test_invalid_native_receipt_is_retained_as_unverified_claim(tmp_path):
    device=Device();device.mutate=lambda ack:ack.update(physical_outcome='confirmed')
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        with pytest.raises(GatewayError) as caught:dispatch(gateway,lease)
        retained=json.loads(gateway.journal()[0]['ack'])
        assert retained['classification']=='unverified_transport_receipt'
        assert retained['physical_outcome']=='unverified'
        assert retained['returned_receipt']['physical_outcome']=='confirmed'
        assert caught.value.delivery_receipt==retained
        assert gateway.journal()[0]['state']=='unknown'


def test_receipt_commit_failure_retains_observation_without_acknowledging_publication(tmp_path):
    import sqlite3
    device=Device()
    with CommandGateway(tmp_path/'gateway.sqlite3',binding(),device,clock=Clock()) as gateway:
        lease=gateway.acquire('run-one',ttl=20)
        actual=gateway._db
        class Fault:
            armed=False;failed=False
            def __getattr__(self,key):return getattr(actual,key)
            def execute(self,sql,*args):
                if sql.startswith('UPDATE commands SET state=?,ack=?'):self.armed=True
                if sql=='COMMIT' and self.armed and not self.failed:
                    self.failed=True
                    raise sqlite3.OperationalError('owned commit storage failure')
                return actual.execute(sql,*args)
        fault=Fault();gateway._db=fault
        with pytest.raises(GatewayError) as caught:dispatch(gateway,lease)
        assert fault.failed and len(device.effects)==1
        receipt=caught.value.delivery_receipt
        assert receipt['classification']=='delivery_receipt_commit_unconfirmed'
        assert receipt['observed_delivery']['status']=='delivered_unverified'
        assert receipt['physical_outcome']=='unverified'
        assert gateway.snapshot()['state']=='recovery_required'
        assert gateway.journal()[0]['state']=='unknown'
        with pytest.raises(GatewayError):dispatch(gateway,lease)
        assert len(device.effects)==1
