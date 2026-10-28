"""Read-only telemetry acquisition and non-actuating shadow inference.

Opening a serial port may toggle control lines; even that operation requires a
trusted runner grant. Modbus read requests and MQTT CONNECT/SUBSCRIBE are protocol
traffic, not actuator writes. No client here exposes write/publish commands.
Live device/broker qualification is intentionally separate from fixture evidence.
"""
from __future__ import annotations

import base64
from collections import deque
from decimal import Decimal, InvalidOperation
import hashlib
import importlib
import inspect
import ipaddress
import json
import math
from pathlib import Path
import re
import struct
import time

from .engine import ExecutionCancelled, digest
from .learning import artifact
from .signals import integer, number

ENGINES = frozenset({'acquisition.serial', 'acquisition.modbus', 'acquisition.mqtt'})
_DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
MAX_FRAME = 65536


def _finite(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('telemetry value must be a finite number')
    text = str(value).strip()
    if len(text) > 1024 or not _DECIMAL.fullmatch(text):
        raise ValueError('invalid numeric payload; punctuation/signs are not normalized')
    try:
        decimal = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError('invalid numeric payload') from exc
    result = float(decimal)
    if not math.isfinite(result):
        raise ValueError('telemetry value must be finite')
    return result


def _binding(spec, *, retained=False):
    definition = spec['definition']; params = definition.get('parameters', {})
    engine = definition['engine']
    if engine not in ENGINES:
        raise ValueError('unsupported acquisition engine')
    if definition.get('operating_mode') not in {'acquisition', 'shadow'}:
        raise ValueError('acquisition engines require acquisition or shadow mode; no physical control is supported')
    grant = spec.get('_runtime_grants', {}).get('telemetry')
    if retained and grant is None:
        return {'binding_id': 'retained', 'engine': engine, 'read_only': True,
                'limits': {'max_samples': 100000, 'max_duration': 3600, 'max_timeout': 1}, 'source_clock': {}}
    if not isinstance(grant, dict) or grant.get('engine') != engine or grant.get('read_only') is not True:
        raise ValueError('read-only acquisition requires a trusted exact telemetry binding')
    if not isinstance(grant.get('binding_id'), str) or params.get('binding_id') != grant['binding_id']:
        raise ValueError('telemetry binding identity mismatch')
    endpoint = grant.get('endpoint')
    if not isinstance(endpoint, dict):
        raise ValueError('telemetry binding requires an explicit endpoint')
    if 'endpoint' in params and params['endpoint'] != endpoint:
        raise ValueError('authored endpoint differs from trusted telemetry binding')
    if engine == 'acquisition.serial':
        if grant.get('serial_open') is not True or not isinstance(endpoint.get('port'), str) or not endpoint['port'].startswith('/dev/'):
            raise ValueError('serial open requires an explicit device binding and serial_open grant; control lines may toggle')
        integer(endpoint.get('baudrate'), 'baudrate', 50, 4000000)
    else:
        try:
            ipaddress.ip_address(endpoint.get('host'))
        except ValueError as exc:
            raise ValueError('telemetry endpoints require an explicitly pinned numeric IP; DNS discovery is unsupported') from exc
        integer(endpoint.get('port'), 'port', 1, 65535)
    if engine == 'acquisition.modbus':
        read = grant.get('read', {})
        if read.get('kind') not in {'input_registers', 'holding_registers'}:
            raise ValueError('only read input/holding register function codes are supported')
        address = integer(read.get('address'), 'address', 0, 65535)
        count = integer(read.get('count'), 'register count', 1, 125)
        if address + count > 65536:
            raise ValueError('register range exceeds protocol address space')
        integer(read.get('device_id'), 'device_id', 1, 247)
        if 'read' in params and params['read'] != read:
            raise ValueError('authored register binding differs from trusted read binding')
    if engine == 'acquisition.mqtt':
        topic = endpoint.get('topic')
        if not isinstance(topic, str) or not topic or len(topic.encode()) > 65535 or any(value in topic for value in ('+','#','\0')):
            raise ValueError('MQTT requires an exact non-wildcard trusted topic')
        if type(endpoint.get('tls')) is not bool:
            raise ValueError('MQTT binding must explicitly select TLS policy')
    return json.loads(json.dumps(grant))


def _limits(params, binding):
    declared = binding.get('limits', {})
    count = integer(params.get('samples', 100), 'samples', 1, 100000)
    duration = number(params.get('duration', 10), 'duration', minimum=.001)
    timeout = number(params.get('timeout', .5), 'timeout', minimum=.001)
    maximum_count = integer(declared.get('max_samples'), 'grant max_samples', 1, 100000)
    maximum_duration = number(declared.get('max_duration'), 'grant max_duration', minimum=.001)
    maximum_timeout = number(declared.get('max_timeout'), 'grant max_timeout', minimum=.001)
    if count > maximum_count or duration > min(maximum_duration,3600) or timeout > min(maximum_timeout,5):
        raise ValueError('acquisition exceeds its trusted sample/duration/timeout bounds')
    interval = number(params.get('interval', 0), 'interval', minimum=0)
    stale_after = number(params.get('stale_after', 5), 'stale_after', minimum=0)
    return count,duration,timeout,interval,stale_after


class SerialReader:
    def __init__(self, binding, timeout, max_frame, *, library=None):
        library = library or importlib.import_module('serial')
        endpoint = binding['endpoint']
        self.port = library.Serial(port=None, baudrate=endpoint['baudrate'], timeout=timeout,
                                   write_timeout=0, xonxoff=False, rtscts=False, dsrdtr=False)
        try:
            self.port.port = endpoint['port']
            self.port.open()
        except Exception:
            self.port.close()
            raise
        self.maximum = max_frame

    def read(self, timeout):
        self.port.timeout = timeout
        data = bytes(self.port.read_until(expected=b'\n', size=self.maximum))
        return {'wire':data,'wire_scope':'serial_received_bytes','truncated':len(data)==self.maximum and not data.endswith(b'\n')} if data else None

    def close(self):
        self.port.close()


class ModbusReader:
    def __init__(self, binding, timeout, max_frame, *, library=None):
        library = library or importlib.import_module('pymodbus.client')
        self.binding = binding; self.maximum = max_frame; self.received=[]; self.sent=[]
        def trace(sending, data):
            wire=bytes(data)
            bucket=self.sent if sending else self.received
            if sum(len(item) for item in bucket)+len(wire)>max_frame:
                raise ValueError('Modbus trace exceeds bounded frame size')
            bucket.append(wire)
            return data  # Trace is observational: never alter protocol bytes.
        self.client=library.ModbusTcpClient(binding['endpoint']['host'],port=binding['endpoint']['port'],timeout=timeout,retries=0,trace_packet=trace)
        try:
            if not self.client.connect():raise ConnectionError('Modbus read endpoint connection failed')
        except Exception:
            self.client.close();raise

    def read(self, timeout):
        read=self.binding['read']; method=getattr(self.client,'read_'+read['kind'])
        parameters=inspect.signature(method).parameters
        key='device_id' if 'device_id' in parameters else 'slave' if 'slave' in parameters else None
        if key is None:raise ValueError('unsupported PyModbus read API: device_id/slave parameter is required')
        self.received.clear();self.sent.clear()
        result=method(address=read['address'],count=read['count'],**{key:read['device_id']})
        if result is None:return None
        wire=b''.join(self.received)
        if result.isError():
            return {'wire':wire,'wire_scope':'modbus_received_tcp_bytes','registers':None,'protocol_error':str(result),'request_wire':b''.join(self.sent)}
        registers=list(result.registers)
        if len(registers)!=read['count'] or any(type(value)is not int or not 0<=value<=65535 for value in registers):
            raise ValueError('Modbus response does not match trusted register range')
        return {'wire':wire,'wire_scope':'modbus_received_tcp_bytes' if wire else 'wire_unavailable_decoded_registers_only',
                'registers':registers,'request_wire':b''.join(self.sent)}

    def close(self):
        self.client.close()


class MQTTReader:
    def __init__(self, binding, timeout, max_frame, *, library=None, secrets=None, clock=time):
        library=library or importlib.import_module('paho.mqtt.client')
        self.clock=clock;self.maximum=max_frame;self.queue=deque();self.error=None;self.ready=False
        endpoint=binding['endpoint'];self.topic=endpoint['topic']
        self.client=library.Client(callback_api_version=library.CallbackAPIVersion.VERSION2,clean_session=True,reconnect_on_failure=False)
        self.client.connect_timeout=timeout
        def on_connect(client, userdata, flags, reason_code, properties):
            if reason_code != 0:self.error=ConnectionError('MQTT connect was rejected');return
            code,_=client.subscribe(self.topic,qos=0)
            if code != 0:self.error=ConnectionError('MQTT telemetry subscription failed')
            else:self.ready=True
        def on_message(client,userdata,message):
            if message.topic != self.topic:
                self.error=ValueError('MQTT message does not match exact topic binding');return
            if len(self.queue)>=256:
                self.error=ValueError('MQTT incoming evidence queue exceeded bound');return
            payload=bytes(message.payload)
            self.queue.append({'wire':payload[:max_frame],'wire_scope':'mqtt_application_payload','truncated':len(payload)>max_frame,
                               'wire_sha256':hashlib.sha256(payload).hexdigest(),'original_wire_bytes':len(payload),
                               'topic':message.topic,'retained':bool(message.retain),'qos':message.qos,
                               'host_received_at':clock.time(),'host_received_monotonic':clock.monotonic()})
        self.client.on_connect=on_connect;self.client.on_message=on_message
        secrets=secrets or {}
        if secrets:
            if not endpoint['tls'] and not ipaddress.ip_address(endpoint['host']).is_loopback:
                raise ValueError('credentialed remote MQTT requires TLS')
            self.client.username_pw_set(secrets['username'],secrets.get('password'))
        if endpoint['tls']:self.client.tls_set(ca_certs=binding.get('ca_certs'))
        try:
            code=self.client.connect(endpoint['host'],endpoint['port'],keepalive=30)
            if code!=0:raise ConnectionError('MQTT connection initialization failed')
        except Exception:
            self.client.disconnect();raise

    def read(self, timeout):
        until=self.clock.monotonic()+timeout
        while self.clock.monotonic()<until:
            if self.error:raise self.error
            if self.queue:return self.queue.popleft()
            code=self.client.loop(timeout=min(.1,max(.001,until-self.clock.monotonic())))
            if code != 0:raise ConnectionError('MQTT read loop disconnected')
        if self.error:raise self.error
        return self.queue.popleft() if self.queue else None

    def close(self):
        self.client.disconnect()


def open_reader(binding, timeout, maximum, *, secrets=None, clock=time):
    if binding['engine']=='acquisition.serial':return SerialReader(binding,timeout,maximum)
    if binding['engine']=='acquisition.modbus':return ModbusReader(binding,timeout,maximum)
    return MQTTReader(binding,timeout,maximum,secrets=secrets,clock=clock)


def _decode(frame, params, engine):
    if frame.get('truncated'):raise ValueError('frame_truncated')
    if frame.get('protocol_error'):raise ValueError('protocol_error')
    if engine=='acquisition.modbus':
        registers=frame.get('registers')
        if not registers:raise ValueError('missing_registers')
        kind=params.get('decode','uint16')
        if kind in {'uint16','int16'}:
            index=integer(params.get('channel',0),'channel',0,len(registers)-1)
            value=registers[index];value=value-65536 if kind=='int16' and value>=32768 else value
        elif kind in {'float32','uint32','int32'}:
            if len(registers)!=2:raise ValueError('32-bit decode requires exactly two granted registers')
            words=registers if params.get('word_order','big')=='big' else list(reversed(registers))
            if params.get('word_order','big') not in {'big','little'}:raise ValueError('unsupported word order')
            byte_order=params.get('byte_order','big')
            if byte_order not in {'big','little'}:raise ValueError('unsupported byte order')
            data=b''.join(value.to_bytes(2,byte_order) for value in words)
            value=struct.unpack('>'+{'float32':'f','uint32':'I','int32':'i'}[kind],data)[0]
        else:raise ValueError('unsupported register decode')
        return _finite(value),{}
    wire=frame.get('wire',b'')
    decoder=params.get('decode','json')
    if decoder=='decimal_text':return _finite(wire.decode('utf-8')),{}
    if decoder!='json':raise ValueError('unsupported telemetry payload decode')
    body=json.loads(wire.decode('utf-8'))
    if not isinstance(body,dict):raise ValueError('JSON telemetry payload must be an object')
    return _finite(body.get(params.get('value_key','value'))),body


def _retained(spec):
    from .compatibility import resolve_role
    binding=resolve_role(spec,'telemetry')
    with Path(binding['path']).open() as stream:
        for index,line in enumerate(stream):
            if index>=100000:raise ValueError('retained telemetry exceeds bounded record count')
            if len(line.encode())>MAX_FRAME:raise ValueError('retained telemetry record exceeds bounded frame size')
            if line.strip():
                row=json.loads(line)
                if not isinstance(row,dict):raise ValueError('retained telemetry must contain JSON objects')
                yield {'wire':line.encode(),'wire_scope':'immutable_retained_jsonl','retained_record':row,'artifact_id':binding['artifact_id'],'artifact_sha256':binding['sha256'],'source_row':index}


def _shadow_predictor(spec, injected):
    if injected is not None:
        if not callable(injected):raise ValueError('injected shadow predictor must be callable')
        return injected,{'kind':'injected_fixture_predictor','qualification':'fixture_only'}
    from .compatibility import resolve_role
    from .timeseries import load_research_checkpoint,predict_from_checkpoint
    binding=resolve_role(spec,'shadow_model')
    _,state=load_research_checkpoint(binding['path'])
    architecture=state['architecture']
    if architecture.get('family') not in {'gru','transformer','mlp'} or architecture.get('input_size')!=1 or state.get('objective') not in {'forecasting','regression'}:
        raise ValueError('shadow model supports scalar numeric forecasting/regression checkpoints only')
    context=integer(architecture.get('context'),'shadow context',1,512)
    history=deque(maxlen=context)
    def predict(record):
        if record.get('value') is None or not record.get('observed'):
            history.clear();predict.source_record_ids=[];return None
        history.append((record['value'],record['record_id']))
        predict.source_record_ids=[item[1] for item in history]
        if len(history)<context:return None
        if digest(Path(binding['path']))!=binding['sha256']:raise ValueError('shadow model checkpoint changed')
        result=predict_from_checkpoint(binding['path'],[[item[0] for item in history]])[0]
        if digest(Path(binding['path']))!=binding['sha256']:raise ValueError('shadow model checkpoint changed during inference')
        return result
    return predict,{'kind':'tinystudio.timeseries.v1','artifact_id':binding['artifact_id'],'sha256':binding['sha256'],'context':context,'qualification':'checkpoint_inference_only_not_physical_control'}


def _parquet(path, output, cancelled):
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema=pa.schema([('record_id',pa.string()),('episode_id',pa.string()),('time',pa.float64()),('available_at',pa.float64()),
                      ('value',pa.float64()),('units',pa.string()),('observed',pa.bool_()),('quality',pa.string()),('record_json',pa.string())])
    with pq.ParquetWriter(output,schema) as writer, path.open() as stream:
        chunk=[]
        for line in stream:
            if cancelled():raise ExecutionCancelled('acquisition cancelled during Parquet serialization')
            row=json.loads(line);chunk.append({**{field.name:row.get(field.name) for field in schema if field.name!='record_json'},'record_json':line.rstrip('\n')})
            if len(chunk)==512:writer.write_table(pa.Table.from_pylist(chunk,schema=schema));chunk.clear()
        if chunk:writer.write_table(pa.Table.from_pylist(chunk,schema=schema))


def execute_acquisition(spec, output_dir, emit, cancelled, *, transport_factory=None, clock=time, predictor=None):
    definition=spec['definition'];params=definition.get('parameters',{})
    retained=definition.get('operating_mode')=='shadow' and params.get('source')=='retained'
    binding=_binding(spec,retained=retained)
    count,duration,timeout,interval,stale_after=_limits(params,binding)
    maximum=integer(params.get('max_frame_bytes',MAX_FRAME),'max_frame_bytes',1,MAX_FRAME)
    units=params.get('units','unitless')
    if not isinstance(units,str) or len(units)>200:raise ValueError('units must be a bounded string')
    if definition.get('pipeline'):raise ValueError('acquisition does not silently apply offline pipeline stages')
    shadow=definition.get('operating_mode')=='shadow'
    predict,model=_shadow_predictor(spec,predictor) if shadow else (None,None)
    if cancelled():raise ExecutionCancelled('acquisition cancelled before opening a transport')
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    if any(output_dir.iterdir()):raise ValueError('acquisition output directory must be empty')
    started=clock.monotonic();received_origin=clock.time();until=started+duration
    domain='host-monotonic:'+str(spec.get('research_identity','acquisition'))
    reader=None;rows=missing=stale=0
    source=iter(_retained(spec)) if retained else None
    try:
        if not retained:
            factory=transport_factory or open_reader
            reader=factory(binding,timeout,maximum,secrets=spec.get('_runtime_secrets',{}).get('telemetry',{}),clock=clock)
        with (output_dir/'raw.jsonl').open('w') as raw, (output_dir/'processed.jsonl').open('w') as derived:
            while rows<count and clock.monotonic()<until:
                if cancelled():raise ExecutionCancelled('acquisition cancelled')
                before=clock.monotonic()
                if retained:
                    try:frame=next(source)
                    except StopIteration:break
                else:frame=reader.read(min(timeout,max(.001,until-before)))
                completed=clock.monotonic();processing_at=clock.time()
                if completed<before or before<started:raise ValueError('host monotonic clock regressed')
                monotonic=(frame or {}).get('host_received_monotonic',completed)
                received=(frame or {}).get('host_received_at',processing_at)
                if not started<=monotonic<=completed:raise ValueError('transport receive timestamp is outside its host clock interval')
                if cancelled():raise ExecutionCancelled('acquisition cancelled after read')
                identity=f"{binding['binding_id']}:{rows}"
                common={'record_id':identity,'episode_id':str(spec.get('research_identity',binding['binding_id'])),'source_record_ids':[identity],
                        'time':monotonic-started,'available_at':monotonic-started,'received_at':received,'received_monotonic':monotonic,
                        'clock_domain':domain,'binding_id':binding['binding_id'],'units':units,'value':None,'observed':False,'quality':'missing'}
                raw_row={**common,'wire_base64':base64.b64encode(frame.get('wire',b'')).decode() if frame else '',
                         'wire_scope':frame.get('wire_scope') if frame else 'timeout_no_wire','raw_values':frame.get('registers') if frame else None,
                         'protocol_request_base64':base64.b64encode(frame.get('request_wire',b'')).decode() if frame else '',
                         'transport_metadata':{key:value for key,value in (frame or {}).items() if key not in {'wire','request_wire','registers','retained_record'}},
                         'original_record':frame.get('retained_record') if frame else None}
                row={**common,'source_record_ids':[identity],'quality_flags':[],'available_at':completed-started,'processed_monotonic':completed,'processing_delay_seconds':completed-monotonic}
                body={}
                if frame:
                    try:
                        if retained:
                            body=frame['retained_record'];value=_finite(body.get('value'))
                        else:value,body=_decode(frame,params,definition['engine'])
                        row.update(value=value,observed=True,quality='good')
                    except (ValueError,UnicodeDecodeError,TypeError) as exc:
                        row['quality']='invalid';row['quality_flags'].append(str(exc)[:200])
                else:row['quality_flags'].append('read_timeout')
                raw_row['decoded_payload']=body or None
                row['source_identity']=raw_row['source_identity']=body.get(params.get('source_id_key','record_id'))
                timestamp=body.get(params.get('timestamp_key','source_timestamp'))
                source_clock=binding.get('source_clock',{})
                row['source_timestamp']=raw_row['source_timestamp']=timestamp
                row['source_clock_domain']=raw_row['source_clock_domain']=source_clock.get('domain','unreported')
                if timestamp is not None and source_clock.get('kind')=='unix_seconds' and source_clock.get('synchronized') is True:
                    try:
                        age=processing_at-_finite(timestamp);row['source_age_seconds']=age
                        if age<0:row['quality_flags'].append('source_timestamp_in_future')
                        elif age>stale_after:row['quality_flags'].append('stale');stale+=1
                    except ValueError:row['quality_flags'].append('invalid_source_timestamp')
                elif timestamp is not None:row['quality_flags'].append('source_clock_unqualified')
                if completed-before>timeout:row['quality_flags'].append('read_deadline_exceeded')
                if completed-monotonic>stale_after:row['quality_flags'].append('delivery_queue_stale')
                if frame and frame.get('retained'):row['quality_flags'].append('mqtt_retained_message')
                if row['quality']=='good' and row['quality_flags']:row['quality']='stale' if 'stale' in row['quality_flags'] else 'uncertain'
                raw_row['quality']=row['quality'];raw_row['observed']=bool(frame);raw_row['value']=None
                if predict:
                    row['prediction']=predict(dict(row));row['prediction_is_actuation']=False
                    row['prediction_available_at']=clock.monotonic()-started
                    row['prediction_source_record_ids']=list(getattr(predict,'source_record_ids',row['source_record_ids']))
                if row['value'] is None:missing+=1
                raw.write(json.dumps(raw_row,allow_nan=False,sort_keys=True)+'\n')
                derived.write(json.dumps(row,allow_nan=False,sort_keys=True)+'\n')
                rows+=1
                emit({'type':'acquisition_progress','records':rows,'missing':missing,'stale':stale,'clock_domain':domain})
                if interval and not retained:
                    end=min(until,clock.monotonic()+interval)
                    while clock.monotonic()<end:
                        if cancelled():raise ExecutionCancelled('acquisition cancelled during poll interval')
                        clock.sleep(min(.05,end-clock.monotonic()))
    finally:
        if reader is not None:reader.close()
        if source is not None:source.close() if hasattr(source,'close') else None
    if rows==0:raise ValueError('acquisition produced no retained observations')
    for name in ('raw','processed'):_parquet(output_dir/(name+'.jsonl'),output_dir/(name+'.parquet'),cancelled)
    artifacts=[artifact(path,'original' if path.stem=='raw' else 'derived_records','application/vnd.apache.parquet' if path.suffix=='.parquet' else 'application/x-ndjson') for path in sorted(output_dir.iterdir())]
    return {'records':rows,'artifacts':artifacts,'metrics':{'records':rows,'missing_records':missing,'stale_records':stale,'actuator_writes':0},
            'qualification':{'status':'unqualified_live_source' if not retained else 'immutable_retained_shadow','physical_validation':False,
                             'operating_mode':definition['operating_mode'],'transport':definition['engine'],'binding_id':binding['binding_id'],
                             'source_clock_sync':'operator declaration only','host_clock_origin':{'monotonic':started,'received_at':received_origin,'domain':domain},
                             'maximum_duration_overshoot_seconds':timeout,'shadow_model':model,'predictions_route_to_actuator':False,
                             'wire_limits':'serial raw bytes; Modbus trace RX/TX if supported; MQTT application payload only, not encrypted wire',
                             'serial_open_notice':'opening/closing a granted serial port may toggle control lines' if not retained and definition['engine']=='acquisition.serial' else None}}
