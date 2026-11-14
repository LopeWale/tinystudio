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
from .experiment_resources import CollectionPlan, ResourceLimits
from .hardware_transports import SerialEndpoint, ModbusEndpoint, CANReader, read_registers
from .hardware_bindings import HardwareBindingRegistry
from .acquisition_paths import AcquisitionPaths
from .serial_framing import FramedSerialReader, SerialFraming
from .device_discovery import SunSpecDiscovery, SunSpecScanConfig
from .modbus_trace import RawModbusTrace
from .evidence_io import JsonlEvidenceWriter, RecordWritePolicy

ENGINES = frozenset({'acquisition.serial', 'acquisition.modbus', 'acquisition.mqtt', 'acquisition.can'})
_DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
MAX_FRAME = CollectionPlan.default_frame_bytes


def _finite(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('telemetry value must be a finite number')
    text = str(value).strip()
    if not _DECIMAL.fullmatch(text):
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
    registry = HardwareBindingRegistry.from_grants(spec.get('_runtime_grants', {}), 'telemetry')
    if retained and not registry.bindings:
        return {'binding_id': 'retained', 'engine': engine, 'read_only': True,
                'limits': {}, 'source_clock': {}}
    grant = registry.select(params.get('binding_id'))
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
        if grant.get('serial_open') is not True:
            raise ValueError('serial open requires an explicit device binding and serial_open grant; control lines may toggle')
        SerialEndpoint.from_dict(endpoint)
        SerialFraming.from_dict(grant.get('framing'))
        if 'framing' in params and params['framing']!=grant.get('framing',{}):
            raise ValueError('authored framing differs from trusted serial binding')
    elif engine == 'acquisition.modbus':
        ModbusEndpoint.from_dict(endpoint)
    elif engine == 'acquisition.can':
        if not isinstance(endpoint.get('interface'), str) or not endpoint['interface'] or 'channel' not in endpoint:
            raise ValueError('CAN acquisition requires an explicit interface and channel')
    else:
        try:
            ipaddress.ip_address(endpoint.get('host'))
        except ValueError as exc:
            raise ValueError('telemetry endpoints require an explicitly pinned numeric IP; DNS discovery is unsupported') from exc
        integer(endpoint.get('port'), 'port', 1, 65535)
    if engine == 'acquisition.modbus':
        read = grant.get('read', {})
        ModbusReader.validate_read(read, grant.get('limits'))
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
    plan = CollectionPlan.resolve(params, binding.get('limits', {}))
    return plan.samples, plan.duration, plan.timeout, plan.interval, plan.stale_after


class SerialReader(FramedSerialReader):
    """Compatibility name for the shared framed serial acquisition reader."""


class ModbusReader:
    """Bind a direct range or one discovered model point, without writes.

    Trace budgets affect retained evidence only. Decoded register values come
    from the native protocol client and remain usable when the trace prefix is
    truncated or metadata-only. No model schemas, scaling or writable points
    are inferred from discovery. SDK callback buffers can be cumulative decoder
    snapshots; their receipts do not reconstruct transport wire bytes.
    """

    @staticmethod
    def validate_read(read, limits=None):
        """Validate operator authority without importing or opening a client."""
        if not isinstance(read, dict) or read.get('kind') not in ('input_registers', 'holding_registers'):
            raise ValueError('only read input/holding register function codes are supported')

        def bounded(value, name, minimum=0, maximum=None):
            if type(value) is not int or value < minimum or maximum is not None and value > maximum:
                raise ValueError(f'{name} is outside its integer protocol bounds')
            return value

        bounded(read.get('device_id'), 'device_id', 0, 255)
        if 'sunspec' not in read:
            if set(read) != {'kind', 'address', 'count', 'device_id'}:
                raise ValueError('direct register read requires only kind, address, count and device_id')
            address = bounded(read['address'], 'register address', 0, 65535)
            size = bounded(read['count'], 'register count', 1, 125)
            if address + size > 65536:
                raise ValueError('register range exceeds protocol address space')
            return None
        if set(read) != {'kind', 'device_id', 'sunspec'}:
            raise ValueError('SunSpec and direct register address/count bindings are mutually exclusive')
        selected = read['sunspec']
        if not isinstance(selected, dict) or set(selected) != {'scan', 'point'}:
            raise ValueError('SunSpec measurement requires an explicit scan and point')
        scan = selected['scan']
        scan_fields = {'base_addresses', 'device_id', 'register_kind', 'max_models', 'read_model_data'}
        if not isinstance(scan, dict) or set(scan) - scan_fields:
            raise ValueError('SunSpec scan contains an unsupported field')
        scan = json.loads(json.dumps(scan))
        operator = ResourceLimits.from_dict(limits)
        if 'max_models' not in scan:
            scan['max_models'] = operator.max_models
        config = SunSpecScanConfig.from_dict(scan)
        operator.authorize('max_models', config.max_models)
        if config.register_kind != read['kind'] or config.device_id != read['device_id']:
            raise ValueError('SunSpec scan register kind/device_id must match the operator read binding')
        point = selected['point']
        if not isinstance(point, dict) or set(point) != {'model_id', 'instance', 'offset', 'count'}:
            raise ValueError('SunSpec point requires only model_id, instance, offset and count')
        bounded(point['model_id'], 'model_id', 0, 65534)
        bounded(point['instance'], 'instance')
        bounded(point['offset'], 'offset')
        bounded(point['count'], 'point register count', 1, 125)
        return config

    def __init__(self, binding, timeout, max_frame, *, library=None, clock=time, cancelled=lambda: False, deadline=None):
        self.binding = json.loads(json.dumps(binding))
        self.maximum = max_frame
        if max_frame is not None and (type(max_frame) is not int or max_frame < 0):
            raise ValueError('max_frame must be a nonnegative evidence byte budget or None')
        read = self.binding.get('read')
        config = self.validate_read(read, self.binding.get('limits'))
        endpoint = ModbusEndpoint.from_dict(self.binding['endpoint'])
        if deadline is not None and (type(deadline) not in (float, int) or not math.isfinite(deadline)):
            raise ValueError('deadline must be a finite absolute host-monotonic time or None')
        if not callable(cancelled):
            raise ValueError('cancelled must be callable')
        self._clock, self._cancelled, self._deadline = clock, cancelled, deadline
        self._wire_scope = f'modbus_{endpoint.framing}_sdk_trace_callback_buffers'
        self.source_binding = {'format': 'tinystudio.modbus-source-binding.v1',
                               'selection': 'sunspec' if config is not None else 'direct',
                               'requested_read': read, 'resolved_read': None, 'discovery': None,
                               'resolution': None, 'wire_scope': self._wire_scope, 'discovery_wire': [],
                               'execution_budget': {'deadline_monotonic': deadline, 'native_timeout': timeout,
                                                    'deadline_scope': 'checks_between_native_operations',
                                                    'inflight_operation': 'may_finish_after_deadline_under_native_timeout'}}
        self._trace = RawModbusTrace(max_frame, clock=clock, framing=endpoint.framing)
        self.client = None
        discovery = None
        deadline_reached = False

        def scan_cancelled():
            nonlocal deadline_reached
            if cancelled():
                return True
            if deadline is not None and clock.monotonic() >= deadline:
                deadline_reached = True
                return True
            return False

        try:
            self._check_execution()
            library = library if library is not None else importlib.import_module('pymodbus.client')
            self.client = endpoint.create_client(timeout, trace_packet=self._trace.observe, library=library)
            self._check_execution()
            if not self.client.connect():
                raise ConnectionError('Modbus read endpoint connection failed')
            self._check_execution()
            if config is not None:
                discovery = SunSpecDiscovery(config)
                report = discovery.scan(self.client, cancelled=scan_cancelled)
                self.source_binding['discovery'] = report
                self.source_binding['discovery_wire'] = self._trace.receipts
                self._check_execution()
                if not report['complete']:
                    raise ValueError('SunSpec discovery must complete before binding a measurement point')
                point = read['sunspec']['point']
                resolution = discovery.resolve_point(report, **point)
                self.source_binding['resolution'] = resolution
                self.resolved_read = {name: resolution[name] for name in ('kind', 'address', 'count', 'device_id')}
            else:
                self.resolved_read = dict(read)
            self.source_binding['resolved_read'] = self.resolved_read
            self.source_binding['discovery_trace_capture'] = self._trace.summary()
            self._trace.reset()
        except BaseException as exc:
            if discovery is not None and self.source_binding['discovery'] is None:
                self.source_binding['discovery'] = discovery.last_report
            self.source_binding['discovery_wire'] = self._trace.receipts
            self.source_binding['discovery_trace_capture'] = self._trace.summary()
            report = self.source_binding['discovery']
            if deadline_reached and isinstance(exc, ExecutionCancelled):
                if report is not None:
                    report.update(status='deadline_exceeded', complete=False)
                    for trial in report.get('trials', []):
                        if trial['status'] == 'cancelled':
                            trial['status'] = 'deadline_exceeded'
                original = exc
                exc = TimeoutError('SunSpec discovery exceeded the selected total deadline between native operations')
                exc.__cause__ = original
            if self.client is not None:
                try:
                    self.client.close()
                except Exception as close_error:
                    self.source_binding['close_error'] = {'type': type(close_error).__name__, 'message': str(close_error)}
            exc.source_binding = self.source_binding
            exc.partial_report = report
            raise exc

    def _check_execution(self):
        if self._cancelled():
            error = ExecutionCancelled('Modbus acquisition cancelled between native operations')
        elif self._deadline is not None and self._clock.monotonic() >= self._deadline:
            error = TimeoutError('Modbus acquisition exceeded the selected total deadline between native operations')
        else:
            return
        error.source_binding = self.source_binding
        error.partial_report = self.source_binding['discovery']
        raise error

    def read(self, timeout):
        self._check_execution()
        read = self.resolved_read
        self._trace.reset()
        result = read_registers(self.client, **read)
        if result is None:
            return None
        frame = {'wire': b'', 'request_wire': b'', 'wire_scope': self._wire_scope,
                 'evidence_truncated': self._trace.truncated, 'trace_packets': self._trace.receipts,
                 'trace_capture': self._trace.summary(), 'registers': None}
        if result.isError():
            frame['protocol_error'] = str(result)
            return frame
        registers = list(result.registers)
        if len(registers) != read['count'] or any(type(value) is not int or not 0 <= value <= 65535 for value in registers):
            raise ValueError('Modbus response does not match trusted register range')
        frame['registers'] = registers
        return frame

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
            if binding.get('max_queue_frames') is not None and len(self.queue)>=binding['max_queue_frames']:
                self.error=ValueError('MQTT incoming evidence queue exceeded bound');return
            payload=bytes(message.payload)
            self.queue.append({'wire':payload if max_frame is None else payload[:max_frame],'wire_scope':'mqtt_application_payload','truncated':max_frame is not None and len(payload)>max_frame,
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
        first_poll=True
        while first_poll or self.clock.monotonic()<until:
            first_poll=False
            if self.error:raise self.error
            if self.queue:return self.queue.popleft()
            code=self.client.loop(timeout=min(.1,max(0,until-self.clock.monotonic())))
            if code != 0:raise ConnectionError('MQTT read loop disconnected')
        if self.error:raise self.error
        return self.queue.popleft() if self.queue else None

    def close(self):
        self.client.disconnect()


def open_reader(binding, timeout, maximum, *, secrets=None, clock=time, cancelled=lambda:False, deadline=None):
    if binding['engine']=='acquisition.serial':return SerialReader(binding,timeout,maximum,clock=clock,cancelled=cancelled,deadline=deadline)
    if binding['engine']=='acquisition.modbus':return ModbusReader(binding,timeout,maximum,clock=clock,cancelled=cancelled,deadline=deadline)
    if binding['engine']=='acquisition.can':return CANReader(binding,timeout,maximum)
    return MQTTReader(binding,timeout,maximum,secrets=secrets,clock=clock)


def _decode(frame, params, engine):
    if engine=='acquisition.can':
        body={'frame':frame.get('can_frame',{}),'source_timestamp':frame.get('can_frame',{}).get('timestamp')}
        decoder=params.get('decode','frame')
        if decoder=='frame' or body['frame'].get('is_error_frame') is True:return None,body
        if frame.get('truncated'):raise ValueError('frame_truncated')
        formats={'uint8':'B','int8':'b','uint16':'H','int16':'h','uint32':'I','int32':'i','float32':'f','float64':'d'}
        if decoder not in formats:raise ValueError('unsupported CAN payload decode')
        order=params.get('byte_order','big')
        if order not in {'big','little'}:raise ValueError('CAN byte_order must be big or little')
        offset=params.get('byte_offset',0)
        if type(offset)is not int or offset<0:raise ValueError('CAN byte_offset must be a nonnegative integer')
        fmt=('>' if order=='big' else '<')+formats[decoder]
        width=struct.calcsize(fmt);wire=frame.get('wire',b'')
        if offset+width>len(wire):raise ValueError('CAN payload does not contain the selected measurement')
        return _finite(struct.unpack_from(fmt,wire,offset)[0]),body
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
    if engine=='acquisition.serial' and frame.get('framing') is not None:
        framing=SerialFraming.from_dict(frame['framing'])
        if framing.kind=='delimiter':
            if frame.get('frame_complete') is not True or frame.get('frame_continuation'):
                raise ValueError('incomplete_serial_frame')
            if not wire.endswith(framing.delimiter):raise ValueError('missing_serial_frame_delimiter')
            wire=wire[:-len(framing.delimiter)]
    decoder=params.get('decode','json')
    if decoder=='decimal_text':return _finite(wire.decode('utf-8')),{}
    if decoder not in {'json','json_object'}:raise ValueError('unsupported telemetry payload decode')
    def unique_object(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate telemetry JSON key')
            result[key]=value
        return result
    def invalid_constant(value):raise ValueError('nonfinite telemetry JSON value: '+value)
    body=json.loads(wire.decode('utf-8'),object_pairs_hook=unique_object,parse_constant=invalid_constant)
    if not isinstance(body,dict):raise ValueError('JSON telemetry payload must be an object')
    def finite_payload(value):
        if isinstance(value,float) and not math.isfinite(value):raise ValueError('nonfinite telemetry JSON value')
        if isinstance(value,dict):
            for nested in value.values():finite_payload(nested)
        elif isinstance(value,list):
            for nested in value:finite_payload(nested)
    finite_payload(body)
    if decoder=='json_object':return None,body
    return _finite(body.get(params.get('value_key','value'))),body


def _retained(spec, maximum=None):
    from .compatibility import resolve_role
    binding=resolve_role(spec,'telemetry')
    with Path(binding['path']).open() as stream:
        for index,line in enumerate(stream):
            if maximum is not None and len(line.encode())>maximum:raise ValueError('retained telemetry record exceeds its selected frame budget')
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
    write_policy=RecordWritePolicy.from_parameters(params)
    retained=definition.get('operating_mode')=='shadow' and params.get('source')=='retained'
    binding=_binding(spec,retained=retained)
    plan=CollectionPlan.resolve(params,binding.get('limits',{}))
    count,duration,timeout,interval,stale_after=plan.samples,plan.duration,plan.timeout,plan.interval,plan.stale_after
    maximum=plan.max_frame_bytes
    binding['max_queue_frames']=plan.max_queue_frames
    units=params.get('units','unitless')
    if not isinstance(units,str) or len(units)>200:raise ValueError('units must be a bounded string')
    if definition.get('pipeline'):raise ValueError('acquisition does not silently apply offline pipeline stages')
    shadow=definition.get('operating_mode')=='shadow'
    predict,model=_shadow_predictor(spec,predictor) if shadow else (None,None)
    if cancelled():raise ExecutionCancelled('acquisition cancelled before opening a transport')
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    if any(output_dir.iterdir()):raise ValueError('acquisition output directory must be empty')
    paths=AcquisitionPaths(output_dir)
    started=clock.monotonic();received_origin=clock.time();until=None if duration is None else started+duration
    domain='host-monotonic:'+str(spec.get('research_identity','acquisition'))
    reader=None;rows=raw_rows=missing=stale=0;pending_frame=None
    source=iter(_retained(spec,maximum)) if retained else None
    failure=None;cleanup_errors=[];raw_writer=processed_writer=None

    def evidence_writes():
        return {name:writer.receipt() for name,writer in (('raw',raw_writer),('processed',processed_writer))
                if writer is not None}

    def failure_evidence(cause):
        nonlocal raw_rows,rows,pending_frame
        if raw_writer is not None:
            written=raw_writer.receipt()['written_records']
            if written>raw_rows:pending_frame=None
            raw_rows=written
        if processed_writer is not None:rows=processed_writer.receipt()['written_records']
        attached=getattr(cause,'source_binding',None)
        if attached is not None:
            paths.source_binding.write_text(json.dumps(attached,allow_nan=False,sort_keys=True))
        if pending_frame is not None:
            pending={'format':'tinystudio.unprocessed-receive.v1','complete':False,
                     'binding_id':binding['binding_id'],
                     'disclosure':'Native receive result retained before clock validation, decoding or inference completed.'}
            if isinstance(pending_frame,dict):
                for field in ('wire','request_wire'):
                    value=pending_frame.get(field,b'')
                    if isinstance(value,(bytes,bytearray)):
                        pending[field+'_base64']=base64.b64encode(value).decode('ascii')
                metadata={key:value for key,value in pending_frame.items() if key not in {'wire','request_wire'}}
            else:metadata=pending_frame
            try:pending['metadata']=json.loads(json.dumps(metadata,allow_nan=False))
            except (TypeError,ValueError):pending['metadata_repr']=repr(metadata)
            paths.unprocessed_receive.write_text(json.dumps(pending,allow_nan=False,sort_keys=True))
        audit={'format':'tinystudio.acquisition-audit.v1','complete':False,
               'status':'cancelled' if isinstance(cause,ExecutionCancelled) else 'failed',
               'error':{'type':type(cause).__name__,'message':str(cause)},
               'binding_id':binding['binding_id'],'completed_record_pairs':rows,
               'retained_raw_records':raw_rows,'retained_processed_records':rows,
               'unprocessed_receive_retained':paths.unprocessed_receive.is_file(),
               'native_close_errors':list(cleanup_errors),'closure_unconfirmed':bool(cleanup_errors),
               'resource_plan':plan.to_dict(),'evidence_writes':evidence_writes(),'physical_validation':False,
               'clock':{'started_monotonic':started,'finished_monotonic':clock.monotonic()},
               'disclosure':'Partial acquisition audit only; files and bookkeeping counts do not establish completion or statistical sufficiency.'}
        paths.failure.write_text(json.dumps(audit,allow_nan=False,sort_keys=True))
        evidence=[artifact(path,role,'application/x-ndjson') for path,role in paths.streams if path.is_file()]
        if paths.unprocessed_receive.is_file():evidence.append(artifact(paths.unprocessed_receive,'original'))
        if paths.source_binding.is_file():
            evidence.append({**artifact(paths.source_binding,'measurement_binding'),'visibility':'evaluation'})
        evidence.append({**artifact(paths.failure,'acquisition_audit'),'visibility':'evaluation'})
        partial={'records':rows,'artifacts':evidence,
                 'metrics':{'records':rows,'missing_records':missing,'stale_records':stale,'actuator_writes':0},
                 'qualification':{'status':'partial_acquisition_audit','complete':False,'physical_validation':False,
                                  'resource_plan':plan.to_dict(),'binding_id':binding['binding_id'],
                                  'evidence_writes':evidence_writes(),
                                  'availability_boundary':'decoded_and_validated_before_evidence_commit_and_inference',
                                  'native_close_errors':list(cleanup_errors),'closure_unconfirmed':bool(cleanup_errors)}}
        return partial

    def retain_failure(cause):
        from .execution_outcome import RetainedExecutionError
        try:
            partial=failure_evidence(cause)
        except Exception as retention_error:
            cause.add_note(f'Acquisition evidence retention failed; no audit result was published: '
                           f'{type(retention_error).__name__}: {retention_error}')
            if hasattr(cause,'evidence_result'):cause.evidence_result=None
            # Keep the same primary exception and its original cause chain.
            # The retention error remains visible as context and in the note.
            raise cause
        if isinstance(cause,ExecutionCancelled):
            cause.evidence_result=partial
            raise cause
        raise RetainedExecutionError(f'{type(cause).__name__}: {cause}',evidence_result=partial) from cause

    try:
        if not retained and count != 0 and duration != 0:
            factory=transport_factory or open_reader
            reader=factory(binding,timeout,maximum,secrets=spec.get('_runtime_secrets',{}).get('telemetry',{}),
                           clock=clock,cancelled=cancelled,deadline=until)
            source_binding=getattr(reader,'source_binding',None)
            if source_binding is not None:
                paths.source_binding.write_text(json.dumps(source_binding,allow_nan=False,sort_keys=True))
        raw_writer=JsonlEvidenceWriter(paths.raw,write_policy,clock)
        processed_writer=JsonlEvidenceWriter(paths.processed,write_policy,clock)
        with raw_writer as raw, processed_writer as derived:
            while (count is None or rows<count) and (until is None or clock.monotonic()<until):
                if cancelled():raise ExecutionCancelled('acquisition cancelled')
                before=clock.monotonic()
                if retained:
                    try:frame=next(source)
                    except StopIteration:break
                else:frame=reader.read(timeout if until is None else min(timeout,max(0,until-before)))
                pending_frame=frame
                read_completed=clock.monotonic();read_completed_at=clock.time()
                if read_completed<before or before<started:raise ValueError('host monotonic clock regressed')
                monotonic=(frame or {}).get('host_received_monotonic',read_completed)
                received=(frame or {}).get('host_received_at',read_completed_at)
                if not started<=monotonic<=read_completed:raise ValueError('transport receive timestamp is outside its host clock interval')
                identity=f"{binding['binding_id']}:{rows}"
                common={'record_id':identity,'episode_id':str(spec.get('research_identity',binding['binding_id'])),'source_record_ids':[identity],
                        'time':monotonic-started,'available_at':monotonic-started,'received_at':received,'received_monotonic':monotonic,
                        'clock_domain':domain,'binding_id':binding['binding_id'],'units':units,'value':None,'observed':False,'quality':'missing'}
                raw_row={**common,'wire_base64':base64.b64encode(frame.get('wire',b'')).decode() if frame else '',
                         'wire_scope':frame.get('wire_scope') if frame else 'timeout_no_wire','raw_values':frame.get('registers') if frame else None,
                         'protocol_request_base64':base64.b64encode(frame.get('request_wire',b'')).decode() if frame else '',
                         'transport_metadata':{key:value for key,value in (frame or {}).items() if key not in {'wire','request_wire','registers','retained_record'}},
                         'original_record':frame.get('retained_record') if frame else None}
                row={**common,'source_record_ids':[identity],'quality_flags':[]}
                body={}
                if frame:
                    try:
                        if retained:
                            body=frame['retained_record'];value=_finite(body.get('value'))
                        else:value,body=_decode(frame,params,definition['engine'])
                        row.update(value=value,observed=value is not None,quality='good' if value is not None else 'frame_only')
                        if definition['engine']=='acquisition.can' and frame.get('can_frame',{}).get('is_error_frame') is True:
                            row['quality_flags'].append('can_error_frame')
                        if frame.get('truncated') or frame.get('evidence_truncated'):row['quality_flags'].append('wire_evidence_truncated')
                    except (ValueError,UnicodeDecodeError,TypeError) as exc:
                        row['quality']='invalid';row['quality_flags'].append(str(exc)[:200])
                else:row['quality_flags'].append('read_timeout')
                raw_row['decoded_payload']=body or None
                row['source_identity']=raw_row['source_identity']=body.get(params.get('source_id_key','record_id'))
                timestamp=body.get(params.get('timestamp_key','source_timestamp'))
                source_clock=binding.get('source_clock',{})
                row['source_timestamp']=raw_row['source_timestamp']=timestamp
                row['source_clock_domain']=raw_row['source_clock_domain']=source_clock.get('domain','unreported')
                qualified_timestamp=None
                if timestamp is not None and source_clock.get('kind')=='unix_seconds' and source_clock.get('synchronized') is True:
                    try:qualified_timestamp=_finite(timestamp)
                    except ValueError:row['quality_flags'].append('invalid_source_timestamp')
                elif timestamp is not None:row['quality_flags'].append('source_clock_unqualified')
                decoded_completed=clock.monotonic();decoded_at=clock.time()
                if decoded_completed<read_completed:raise ValueError('host monotonic clock regressed during decoding')
                boundary={'available_at':decoded_completed-started,'processed_monotonic':decoded_completed,
                          'read_completed_monotonic':read_completed,'processing_delay_seconds':decoded_completed-monotonic,
                          'availability_boundary':'decoded_and_validated_before_evidence_commit_and_inference'}
                row.update(boundary);raw_row.update(boundary)
                if qualified_timestamp is not None:
                    age=decoded_at-qualified_timestamp;row['source_age_seconds']=age
                    if age<0:row['quality_flags'].append('source_timestamp_in_future')
                    elif age>stale_after:row['quality_flags'].append('stale');stale+=1
                if read_completed-before>timeout:row['quality_flags'].append('read_deadline_exceeded')
                if read_completed-monotonic>stale_after:row['quality_flags'].append('delivery_queue_stale')
                if frame and frame.get('retained'):row['quality_flags'].append('mqtt_retained_message')
                if row['quality']=='good' and row['quality_flags']:row['quality']='stale' if 'stale' in row['quality_flags'] else 'uncertain'
                raw_row['quality']=row['quality'];raw_row['observed']=bool(frame);raw_row['value']=None
                raw.write_record(raw_row)
                raw_rows+=1;pending_frame=None
                if predict and not cancelled():
                    row['prediction']=predict(dict(row));row['prediction_is_actuation']=False
                    row['prediction_available_at']=clock.monotonic()-started
                    row['prediction_source_record_ids']=list(getattr(predict,'source_record_ids',row['source_record_ids']))
                if row['value'] is None:missing+=1
                derived.write_record(row)
                rows+=1
                if cancelled():raise ExecutionCancelled('acquisition cancelled after retaining received frame')
                if frame and frame.get('fatal_transport_error'):
                    detail=frame['fatal_transport_error']
                    raise RuntimeError(f"{detail['type']}: {detail['message']}")
                emit({'type':'acquisition_progress','records':rows,'missing':missing,'stale':stale,'clock_domain':domain})
                if interval and not retained:
                    end=clock.monotonic()+interval
                    if until is not None:end=min(until,end)
                    while clock.monotonic()<end:
                        if cancelled():raise ExecutionCancelled('acquisition cancelled during poll interval')
                        clock.sleep(min(.05,end-clock.monotonic()))
    except Exception as cause:
        failure=cause
    finally:
        for resource in (reader,source):
            if resource is not None and hasattr(resource,'close'):
                try:resource.close()
                except Exception as cause:
                    cleanup_errors.append({'resource':type(resource).__name__,'type':type(cause).__name__,'message':str(cause)})
                    if failure is None:failure=cause
                    else:failure.add_note(f'Acquisition resource cleanup also failed: {cause}')
    if failure is not None:retain_failure(failure)
    artifacts=[]
    for path,role in paths.streams:
        parquet=path.with_suffix('.parquet')
        try:_parquet(path,parquet,cancelled)
        except Exception as cause:retain_failure(cause)
        artifacts.extend((artifact(path,role,'application/x-ndjson'),artifact(parquet,role,'application/vnd.apache.parquet')))
    if paths.source_binding.exists():
        artifacts.append({**artifact(paths.source_binding,'measurement_binding'),'visibility':'evaluation'})
    return {'records':rows,'artifacts':artifacts,'metrics':{'records':rows,'missing_records':missing,'stale_records':stale,'actuator_writes':0},
            'qualification':{'status':'unqualified_live_source' if not retained else 'immutable_retained_shadow','physical_validation':False,
                             'operating_mode':definition['operating_mode'],'transport':definition['engine'],'binding_id':binding['binding_id'],
                             'resource_plan':plan.to_dict(),
                             'evidence_writes':evidence_writes(),
                             'availability_boundary':'decoded_and_validated_before_evidence_commit_and_inference',
                             'source_clock_sync':'operator declaration only','host_clock_origin':{'monotonic':started,'received_at':received_origin,'domain':domain},
                             'duration_policy':{'scope':'transport_open_discovery_and_collection',
                                                'checks':'between native operations', 'native_timeout_requested':timeout,
                                                'hard_upper_bound_verified':False,
                                                'disclosure':'An in-flight SDK operation, DNS resolution or host scheduling may finish after the selected deadline.'},
                             'shadow_model':model,'predictions_route_to_actuator':False,
                             'wire_limits':'serial received bytes; Modbus TCP/RTU-over-TCP trace RX/TX if supported; MQTT application payload; CAN payload/metadata, not physical bus/CRC',
                             'serial_open_notice':'opening/closing a granted serial port may toggle control lines' if not retained and definition['engine']=='acquisition.serial' else None}}
