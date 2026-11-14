"""Real engine/client/parent/native loopback; no physical device or cloud calls."""
import hashlib
import json
import time

import pytest

from tinystudio.lab.attempt_control import AttemptControl,AttemptIdentity
from tinystudio.lab.execution import execute
from tinystudio.lab.hardware_session_bridge import HardwareSessionBridge
from tinystudio.lab.hardware_session_configuration import HardwareSessionBinding
from tinystudio.lab.runner import LeaseHeartbeat


def test_engine_routes_through_parent_and_actual_serial_loop_with_exact_binary_bytes(tmp_path):
    pytest.importorskip('serial')
    state=tmp_path/'state';state.mkdir(mode=0o700)
    source=tmp_path/'decisions.jsonl'
    source.write_text(json.dumps({'decision_id':'query-one','payload':{'operation':'serial.query',
        'data':{'encoding':'hex','value':'00ff0a'}},'valid_for_seconds':5})+'\n')
    selected=HardwareSessionBinding.from_grants({'native_execution':True,'bindings':{'loop':{
        'state_directory':str(state),'gateway_binding':{'endpoint_id':'owned-loop','operator_id':'operator',
        'transport':{'kind':'serial','port':'loop://','baudrate':9600,'write_timeout':1,
                     'read':{'kind':'fixed_bytes','count':3}},
        'command_profile':{'format':'tinystudio.native-command-profile.v1'},'send_timeout':1}}}},'loop')
    control=AttemptControl(AttemptIdentity('http://127.0.0.1','run','attempt','target','a'*64))
    control.renew(20,sent_at=time.monotonic())
    bridge=HardwareSessionBridge(selected,control,tmp_path/'parent')
    definition={'name':'Owned native loop','engine':'hardware.session','operating_mode':'physical_control','seed':42,
        'execution':{'backend':'native','profile':'trusted_native'},
        'inputs':[{'artifact_id':'source','sha256':hashlib.sha256(source.read_bytes()).hexdigest()}],
        'parameters':{'binding_id':'loop','supervisor':{'kind':'recorded','artifact_id':'source'}}}
    try:
        bridge.start()
        result=execute({'definition':definition},tmp_path/'output',lambda item:None,lambda:False,
            context={'input_paths':{'source':str(source)},'hardware_session':bridge.descriptor})
        assert result['records']==1
        receipt=json.loads((tmp_path/'output/session-receipts.jsonl').read_text())['receipt']
        assert receipt['status']=='response_received' and receipt['physical_outcome']=='unverified'
        response=receipt['evidence']['response']
        assert response['sha256']==hashlib.sha256(bytes.fromhex('00ff0a')).hexdigest()
    finally:bridge.close()


def test_heartbeat_uses_send_time_and_revokes_parent_on_server_cancel(tmp_path,monkeypatch):
    clock=[100.]
    monkeypatch.setattr('tinystudio.lab.runner.time.monotonic',lambda:clock[0])
    control=AttemptControl(AttemptIdentity('http://127.0.0.1','run','attempt','target','a'*64),monotonic=lambda:clock[0])
    class Runner:
        cancel=False
        def post(self,*args):
            clock[0]+=5
            return {'lease_seconds':20,'cancel_requested':self.cancel}
    runner=Runner()
    heartbeat=LeaseHeartbeat(runner,'/attempts/attempt','private-token',tmp_path,interval=5,deadline=200,authority=control)
    heartbeat.renew()
    assert control.snapshot()['lease_deadline']==120.
    assert control.remaining()==15.
    runner.cancel=True;heartbeat.renew()
    assert control.snapshot()['revoked'] and (tmp_path/'cancel').exists()


def test_heartbeat_connection_failure_revokes_parent_before_child_teardown(tmp_path):
    control=AttemptControl(AttemptIdentity('http://127.0.0.1','run','attempt','target','a'*64))
    control.renew(20,sent_at=time.monotonic())
    class Runner:
        def post(self,*args):raise ConnectionError('owned transport failure')
    heartbeat=LeaseHeartbeat(Runner(),'/attempts/attempt','private-token',tmp_path,interval=5,
        deadline=time.monotonic()+100,authority=control)
    with pytest.raises(ConnectionError):heartbeat.renew()
    assert control.snapshot()['revoked'] and heartbeat.connectivity_lost


@pytest.mark.parametrize('visibility',['model','evaluation','truth'])
def test_authored_visibility_also_applies_to_the_shared_record_sidecar(tmp_path,visibility):
    from tests.test_lab_hardware_session_bridge import setup
    bridge,control,device,evidence=setup(tmp_path)
    source=tmp_path/'supervisor.py'
    source.write_text('def run(client,context):\n    return {"records":[{"value":7}]}\n')
    definition={'name':'Owned record visibility','engine':'hardware.session','operating_mode':'simulation','seed':42,
        'execution':{'backend':'native','profile':'trusted_native'},
        'inputs':[{'artifact_id':'source','sha256':hashlib.sha256(source.read_bytes()).hexdigest()}],
        'parameters':{'binding_id':'owned','supervisor':{'kind':'python','artifact_id':'source','callable':'run'},
                      'artifact_visibility':visibility}}
    try:
        bridge.start()
        result=execute({'definition':definition},tmp_path/'output',lambda item:None,lambda:False,
            context={'input_paths':{'source':str(source)},'hardware_session':bridge.descriptor})
        assert result['records']==1
        assert all(row.get('visibility')==visibility for row in result['artifacts'])
        assert json.loads((tmp_path/'output/processed.jsonl').read_text())=={'value':7}
    finally:bridge.close()


def test_runtime_credentials_select_only_the_bound_endpoint_and_remain_defensive(tmp_path):
    from tests.test_lab_hardware_session_bridge import setup
    bridge,*_=setup(tmp_path)
    values={'bindings':{'owned':{'password':'selected-private'},'other':{'password':'unselected-private'}}}
    selected=bridge.binding.select_runtime_secrets(values)
    assert selected=={'password':'selected-private'}
    selected['password']='changed'
    assert values['bindings']['owned']['password']=='selected-private'
    assert bridge.binding.select_runtime_secrets(None)=={}
    for invalid in (False,[],0,'',{'bindings':None},{'bindings':{'owned':[]}}):
        with pytest.raises(ValueError):bridge.binding.select_runtime_secrets(invalid)
