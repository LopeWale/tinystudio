"""Deterministic transport/client fixtures only: no devices, sockets or broker."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

from tinystudio.lab.acquisition import SerialReader,ModbusReader,MQTTReader,execute_acquisition
from tinystudio.lab.engine import ExecutionCancelled,digest


class Clock:
    def __init__(self):self.now=0
    def monotonic(self):return self.now
    def time(self):return 1000+self.now
    def sleep(self,seconds):self.now+=seconds


class Reader:
    def __init__(self,clock,frames,delays=()):self.clock=clock;self.frames=iter(frames);self.delays=iter(delays);self.closed=False;self.reads=0
    def read(self,timeout):
        self.reads+=1;self.clock.sleep(next(self.delays,.01))
        return next(self.frames,None)
    def close(self):self.closed=True


def spec(engine='acquisition.serial',mode='acquisition',**params):
    endpoint={'port':'/dev/fixture-not-a-real-device','baudrate':9600} if engine.endswith('serial') else {'host':'192.0.2.1','port':502} if engine.endswith('modbus') else {'host':'192.0.2.1','port':8883,'topic':'telemetry/exact','tls':True}
    binding={'binding_id':'fixture-binding','engine':engine,'read_only':True,'endpoint':endpoint,
             'limits':{'max_samples':20,'max_duration':10,'max_timeout':1},'serial_open':True,
             'source_clock':{'domain':'device-utc','kind':'unix_seconds','synchronized':True}}
    if engine.endswith('modbus'):binding['read']={'kind':'input_registers','address':10,'count':2,'device_id':1}
    return {'research_identity':'fixture-episode','definition':{'engine':engine,'operating_mode':mode,'parameters':{'binding_id':'fixture-binding','samples':2,'duration':1,'timeout':.1,**params},'inputs':[]},'_runtime_grants':{'telemetry':binding}}


def run(tmp_path,document,frames,delays=(),predictor=None):
    clock=Clock();reader=Reader(clock,frames,delays);calls=[]
    def factory(binding,timeout,maximum,**kwargs):calls.append((binding,timeout,maximum));return reader
    result=execute_acquisition(document,tmp_path,lambda event:None,lambda:False,transport_factory=factory,clock=clock,predictor=predictor)
    rows=[json.loads(line) for line in (tmp_path/'processed.jsonl').read_text().splitlines()]
    raw=[json.loads(line) for line in (tmp_path/'raw.jsonl').read_text().splitlines()]
    return result,rows,raw,reader,calls


def test_acquisition_retains_wire_sign_decimal_source_and_receive_clocks(tmp_path):
    document=spec(units='V');original=copy.deepcopy(document)
    first=b'{"value":-0.125,"source_timestamp":1000,"record_id":"device:7"}\r\n'
    result,rows,raw,reader,calls=run(tmp_path,document,[{'wire':first},{'wire':b'{"value":0}'}])
    assert document==original
    assert reader.closed and len(calls)==1
    assert rows[0]['value']==-.125 and rows[1]['value']==0
    assert base64.b64decode(raw[0]['wire_base64'])==first
    assert rows[0]['source_identity']=='device:7'
    assert rows[0]['source_timestamp']==1000 and rows[0]['received_at']==1000.01
    assert rows[0]['received_monotonic']==.01
    assert rows[0]['time']==rows[0]['available_at']==.01
    assert rows[0]['source_clock_domain']=='device-utc'
    assert rows[0]['clock_domain']=='host-monotonic:fixture-episode'
    assert rows[0]['units']=='V'
    assert result['metrics']['actuator_writes']==0
    assert result['qualification']['status']=='unqualified_live_source'
    assert result['qualification']['physical_validation'] is False
    assert {item['name'] for item in result['artifacts']}=={'raw.jsonl','processed.jsonl','raw.parquet','processed.parquet'}
    import pyarrow.parquet as pq
    stored=pq.read_table(tmp_path/'processed.parquet').to_pylist()
    assert stored[0]['value']==-.125 and json.loads(stored[0]['record_json'])['source_identity']=='device:7'


def test_delayed_stale_timeout_and_invalid_numeric_are_not_filled_with_zero(tmp_path):
    document=spec(samples=4,stale_after=1)
    result,rows,raw,reader,_=run(tmp_path,document,[{'wire':b'{"value":2,"source_timestamp":995}'},None,{'wire':b'{"value":"1,250"}'},{'wire':b'{"value":-0.125}'}],delays=[.2,.1,.01,.01])
    assert rows[0]['quality']=='stale'
    assert {'stale','read_deadline_exceeded'}<=set(rows[0]['quality_flags'])
    assert rows[1]['quality']=='missing' and rows[1]['value'] is None and rows[1]['observed'] is False
    assert rows[2]['quality']=='invalid' and rows[2]['value'] is None
    assert rows[3]['value']==-.125
    assert result['metrics']['missing_records']==2
    assert result['metrics']['stale_records']==1
    assert reader.closed


@pytest.mark.parametrize('change',[
    lambda value:value.pop('_runtime_grants'),
    lambda value:value['_runtime_grants']['telemetry'].update(read_only=False),
    lambda value:value['_runtime_grants']['telemetry'].update(serial_open=False),
    lambda value:value['definition']['parameters'].update(binding_id='other'),
    lambda value:value['definition']['parameters'].update(endpoint={'port':'/dev/unauthorized','baudrate':9600}),
    lambda value:value['definition'].update(operating_mode='physical_control'),
    lambda value:value['definition']['parameters'].update(samples=1000),
])
def test_untrusted_or_mismatched_bindings_and_excessive_limits_never_open_transport(tmp_path,change):
    document=spec();change(document)
    def forbidden(*args,**kwargs):pytest.fail('transport must not open')
    with pytest.raises(ValueError):execute_acquisition(document,tmp_path,lambda _:None,lambda:False,transport_factory=forbidden,clock=Clock())


def test_cancellation_after_read_closes_reader_and_never_returns_success(tmp_path):
    clock=Clock();reader=Reader(clock,[{'wire':b'{"value":1}'}])
    with pytest.raises(ExecutionCancelled):
        execute_acquisition(spec(),tmp_path,lambda _:None,lambda:reader.reads>0,transport_factory=lambda *a,**k:reader,clock=clock)
    assert reader.closed


def test_transport_error_always_closes_owned_reader(tmp_path):
    clock=Clock();reader=Reader(clock,[])
    def fail(timeout):raise TimeoutError('controlled protocol failure')
    reader.read=fail
    with pytest.raises(TimeoutError,match='controlled protocol'):
        execute_acquisition(spec(),tmp_path,lambda _:None,lambda:False,transport_factory=lambda *a,**k:reader,clock=clock)
    assert reader.closed


def test_real_pyserial_method_path_through_fake_library_has_no_control_writes():
    calls=[]
    class Serial:
        def __init__(self,**kwargs):calls.append(('construct',kwargs));self.port=None
        def open(self):calls.append(('open',self.port))
        def read_until(self,**kwargs):calls.append(('read_until',kwargs));return b'-0.125\r\n'
        def close(self):calls.append(('close',))
        def write(self,*args):pytest.fail('serial writes forbidden')
        def setDTR(self,*args):pytest.fail('control line writes forbidden')
        def setRTS(self,*args):pytest.fail('control line writes forbidden')
    binding=spec()['_runtime_grants']['telemetry']
    reader=SerialReader(binding,.2,128,library=SimpleNamespace(Serial=Serial))
    assert reader.read(.1)['wire']==b'-0.125\r\n';reader.close()
    assert calls[0][1]['port'] is None
    assert calls[1]==('open','/dev/fixture-not-a-real-device')
    assert calls[2]==('read_until',{'expected':b'\n','size':128})
    assert calls[-1]==('close',)


def test_real_modbus_read_invocation_keeps_request_response_and_register_decode(tmp_path):
    calls=[]
    class Client:
        def __init__(self,host,**kwargs):self.trace=kwargs['trace_packet'];calls.append(('construct',host,kwargs['retries']))
        def connect(self):calls.append(('connect',));return True
        def read_input_registers(self,*,address,count,device_id):
            calls.append(('read',address,count,device_id));self.trace(True,b'READ-request');self.trace(False,b'RX-response')
            return SimpleNamespace(isError=lambda:False,registers=[0xbe00,0]) # float32 -0.125
        def close(self):calls.append(('close',))
        def write_register(self,*a,**k):pytest.fail('Modbus writes forbidden')
        def write_coil(self,*a,**k):pytest.fail('Modbus writes forbidden')
    binding=spec('acquisition.modbus')['_runtime_grants']['telemetry']
    reader=ModbusReader(binding,.1,128,library=SimpleNamespace(ModbusTcpClient=Client))
    clock=Clock()
    document=spec('acquisition.modbus',samples=1,decode='float32')
    result=execute_acquisition(document,tmp_path,lambda _:None,lambda:False,transport_factory=lambda *a,**k:reader,clock=clock)
    row=json.loads((tmp_path/'processed.jsonl').read_text());raw=json.loads((tmp_path/'raw.jsonl').read_text())
    assert row['value']==-.125
    assert raw['raw_values']==[0xbe00,0]
    assert base64.b64decode(raw['wire_base64'])==b'RX-response'
    assert base64.b64decode(raw['protocol_request_base64'])==b'READ-request'
    assert ('read',10,2,1) in calls and calls[-1]==('close',)
    assert result['metrics']['actuator_writes']==0


def test_real_paho_connect_subscribe_loop_path_never_publishes():
    calls=[];clock=Clock()
    class Client:
        def __init__(self,**kwargs):calls.append(('construct',kwargs));self.loops=0
        def tls_set(self,**kwargs):calls.append(('tls',kwargs))
        def connect(self,host,port,**kwargs):calls.append(('connect',host,port));return 0
        def subscribe(self,topic,qos):calls.append(('subscribe',topic,qos));return (0,1)
        def loop(self,timeout):
            clock.sleep(timeout);self.loops+=1
            if self.loops==1:self.on_connect(self,None,{},0,None)
            elif self.loops==2:self.on_message(self,None,SimpleNamespace(topic='telemetry/exact',payload=b'{"value":0}',retain=True,qos=0))
            return 0
        def disconnect(self):calls.append(('disconnect',))
        def publish(self,*args,**kwargs):pytest.fail('MQTT publishing forbidden')
    library=SimpleNamespace(Client=Client,CallbackAPIVersion=SimpleNamespace(VERSION2=2))
    reader=MQTTReader(spec('acquisition.mqtt')['_runtime_grants']['telemetry'],.5,128,library=library,clock=clock)
    frame=reader.read(.5);reader.close()
    assert frame['wire']==b'{"value":0}' and frame['retained'] is True
    assert ('subscribe','telemetry/exact',0) in calls
    assert calls[-1]==('disconnect',)
    assert calls[0][1]['reconnect_on_failure'] is False


def test_shadow_default_requires_a_real_frozen_model_before_live_open(tmp_path):
    document=spec(mode='shadow')
    with pytest.raises(ValueError,match='shadow_model'):
        execute_acquisition(document,tmp_path,lambda _:None,lambda:False,transport_factory=lambda *a,**k:pytest.fail('cannot open'))


def test_retained_shadow_prediction_is_separate_and_has_no_transport_or_actuator(tmp_path):
    source=tmp_path/'telemetry.jsonl';source.write_text('{"record_id":"source:1","value":-0.125}\n{"record_id":"source:2","value":0}\n')
    document=spec(mode='shadow',source='retained');document.pop('_runtime_grants')
    document['definition']['inputs']=[{'artifact_id':'recorded','sha256':digest(source),'role':'telemetry'}]
    document['input_paths']={'recorded':str(source)}
    output=tmp_path/'output';seen=[]
    def predict(row):seen.append(row['value']);return row['value']*2
    result=execute_acquisition(document,output,lambda _:None,lambda:False,transport_factory=lambda *a,**k:pytest.fail('retained shadow must not connect'),clock=Clock(),predictor=predict)
    rows=[json.loads(line) for line in (output/'processed.jsonl').read_text().splitlines()]
    assert seen==[-.125,0]
    assert [row['prediction'] for row in rows]==[-.25,0]
    assert all(row['prediction_is_actuation'] is False for row in rows)
    assert rows[0]['source_identity']=='source:1'
    assert result['qualification']['shadow_model']['qualification']=='fixture_only'
    assert result['qualification']['predictions_route_to_actuator'] is False
    assert result['metrics']['actuator_writes']==0


def test_actual_frozen_tiny_checkpoint_is_the_default_shadow_path(tmp_path):
    import torch
    from tinystudio.lab.timeseries import _checkpoint_model
    architecture={'family':'mlp','context':2,'input_size':1,'output_size':1,'hidden_size':4,'layers':1,'heads':1,'horizon':1}
    model=_checkpoint_model(architecture)
    for parameter in model.parameters():parameter.data.zero_()
    checkpoint=tmp_path/'frozen.pt'
    torch.save({'format':'tinystudio.timeseries.v1','architecture':architecture,'model':model.state_dict(),'objective':'forecasting','normalization':{'mean':torch.tensor(0.),'std':torch.tensor(1.)}},checkpoint)
    source=tmp_path/'telemetry.jsonl';source.write_text('{"value":1}\n{"value":2}\n{"value":3}\n')
    document=spec(mode='shadow',source='retained',samples=3);document.pop('_runtime_grants')
    document['definition']['inputs']=[{'artifact_id':'telemetry','sha256':digest(source),'role':'telemetry'},{'artifact_id':'model','sha256':digest(checkpoint),'role':'shadow_model'}]
    document['input_paths']={'telemetry':str(source),'model':str(checkpoint)}
    result=execute_acquisition(document,tmp_path/'output',lambda _:None,lambda:False,clock=Clock())
    rows=[json.loads(line) for line in (tmp_path/'output/processed.jsonl').read_text().splitlines()]
    assert rows[0]['prediction'] is None
    assert rows[1]['prediction']==[0.] and rows[2]['prediction']==[0.]
    assert rows[1]['prediction_source_record_ids']==['retained:0','retained:1']
    assert result['qualification']['shadow_model']['sha256']==digest(checkpoint)
    assert result['qualification']['shadow_model']['kind']=='tinystudio.timeseries.v1'


def test_live_qualification_is_not_performed():
    pytest.skip('No physical serial/Modbus endpoint or MQTT broker was authorized; fake transport evidence only')
