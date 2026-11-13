"""Operator-authorized structural discovery with retained, unqualified evidence.

Selection and catalog access never import native hardware SDKs. Discovery does
not write actuator values or establish equipment identity, model semantics, or
physical qualification. CAN providers may themselves send discovery traffic.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import importlib
from importlib import metadata
import json
from pathlib import Path
import platform
import sys
import time
from types import MappingProxyType

from .device_discovery import (
    SunSpecDiscovery, SunSpecScanConfig, enumerate_can_interfaces, enumerate_serial_ports,
)
from .engine import ExecutionCancelled, digest
from .experiment_resources import CollectionPlan, ResourceLimits, count, seconds
from .hardware_bindings import HardwareBindingRegistry
from .hardware_transports import ModbusEndpoint
from .modbus_trace import RawModbusTrace


FORMAT = 'tinystudio.hardware-discovery.v1'
METHODS = ('serial.enumerate', 'can.enumerate', 'sunspec.scan')
DEFAULT_FRAME_BYTES = CollectionPlan.default_frame_bytes
_SEMANTIC_FIELDS = ('pipeline', 'inputs', 'components', 'connections', 'scenarios',
                    'clocks', 'objectives', 'learning', 'retrieval', 'rewards', 'evaluators')
_DEFINITION_FIELDS = frozenset({'schema_version', 'name', 'domain', 'engine', 'seed', 'mode', 'operating_mode',
                              'parameters', 'execution', 'research_overrides', *_SEMANTIC_FIELDS})
_SCAN_FIELDS = frozenset({'base_addresses', 'device_id', 'register_kind', 'max_models', 'read_model_data'})


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _plain(value):
    if isinstance(value, (dict, MappingProxyType)):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain(child) for child in value]
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError(f'{name} requires an explicit nonempty string')
    return value


def _strings(value, name):
    if not isinstance(value, list) or not value:
        raise ValueError(f'{name} requires an explicit nonempty list')
    selected = tuple(_text(item, name) for item in value)
    if len(selected) != len(set(selected)):
        raise ValueError(f'{name} must not contain duplicate choices')
    return selected


def _count(value, name):
    if value is not None and type(value) is not int:
        raise ValueError(f'{name} must be a nonnegative integer or null')
    return count(value, name, optional=True)


@dataclass(frozen=True)
class HardwareDiscoveryPaths:
    root: Path

    def __post_init__(self):
        object.__setattr__(self, 'root', Path(self.root))

    @property
    def report(self):
        return self.root / 'device-discovery.json'

    @property
    def wire(self):
        return self.root / 'discovery-wire.jsonl'


@dataclass(frozen=True)
class HardwareDiscoverySelection:
    method: str
    binding_id: str
    timeout: float | None
    interfaces: tuple[str, ...]
    scan: SunSpecScanConfig | None
    max_frame_bytes: int | None
    requested: object

    def __post_init__(self):
        object.__setattr__(self, 'interfaces', tuple(self.interfaces))
        object.__setattr__(self, 'requested', _freeze(_plain(self.requested)))

    @property
    def requested_configuration(self):
        return _plain(self.requested)

    def to_dict(self):
        result = {'method': self.method, 'binding_id': self.binding_id,
                  'max_frame_bytes': self.max_frame_bytes}
        if self.timeout is not None:
            result['timeout'] = self.timeout
        if self.method == 'can.enumerate':
            result['interfaces'] = list(self.interfaces)
        if self.scan is not None:
            result['scan'] = self.scan.as_dict()
        return result


def implementation_sha256():
    digest_value = hashlib.sha256()
    root = Path(__file__).parent
    for name in ('hardware_discovery_engine.py', 'device_discovery.py',
                 'hardware_transports.py', 'hardware_bindings.py', 'experiment_resources.py', 'modbus_trace.py'):
        digest_value.update(name.encode() + b'\0' + (root / name).read_bytes())
    return digest_value.hexdigest()


def method_catalog():
    """Return exact parameter schemas without opening or importing SDKs."""
    common = {'method': {}, 'binding_id': {'type': 'string', 'minLength': 1},
              'max_frame_bytes': {'type': ['integer', 'null'], 'minimum': 0,
                                  'default': DEFAULT_FRAME_BYTES}}
    timeout = {'type': 'number', 'minimum': 0, 'default': 5}
    scan = {'type': 'object', 'required': ['base_addresses', 'device_id'],
            'additionalProperties': False, 'properties': {
                'base_addresses': {'type': 'array', 'minItems': 1,
                                   'items': {'type': 'integer', 'minimum': 0, 'maximum': 65535}},
                'device_id': {'type': 'integer', 'minimum': 0, 'maximum': 255},
                'register_kind': {'enum': ['holding_registers', 'input_registers'], 'default': 'holding_registers'},
                'max_models': {'type': ['integer', 'null'], 'minimum': 0},
                'read_model_data': {'type': 'boolean', 'default': False}}}
    definitions = (
        ('serial.enumerate', 'Enumerate serial port metadata', 'pyserial',
         'Obtain serial port metadata without opening a Serial device.'),
        ('can.enumerate', 'Detect selected CAN providers', 'python-can',
         'Query only explicitly selected, operator-authorized providers; providers may send discovery traffic.'),
        ('sunspec.scan', 'Scan authored SunSpec addresses', 'pymodbus',
         'Read the selected marker and model-length chains without schema inference or actuator writes.'),
    )
    methods = []
    for identifier, label, dependency, description in definitions:
        properties = deepcopy(common)
        properties['method'] = {'const': identifier}
        required = ['method', 'binding_id']
        if identifier != 'serial.enumerate':
            properties['timeout'] = deepcopy(timeout)
        if identifier == 'can.enumerate':
            properties['interfaces'] = {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                                        'items': {'type': 'string', 'minLength': 1}}
            required.append('interfaces')
        if identifier == 'sunspec.scan':
            properties['scan'] = deepcopy(scan)
            required.append('scan')
        schema = {'type': 'object', 'required': required, 'properties': properties, 'additionalProperties': False}
        methods.append({'id': identifier, 'version': '1.0.0', 'label': label, 'description': description,
                        'required': required[:], 'parameters': deepcopy(properties), 'schema': schema,
                        'dependencies': [dependency], 'assumptions': [
                            'An execution-host read-only discovery grant is required; authored definitions cannot grant authority.',
                            'Live discovery requires acquisition mode; no immutable discovery replay input path is implemented.',
                            'Enumeration or structural discovery does not establish physical qualification.',
                            'Model-count budgets apply separately to each authored candidate base; counts are bookkeeping, not scientific validity rules.']})
    return {'format': 'tinystudio.hardware-discovery-methods.v1',
            'implementation_sha256': implementation_sha256(), 'methods': methods}


def validate_selection(value):
    """Validate authored choices only; runtime authority is checked separately."""
    if not isinstance(value, dict):
        raise ValueError('hardware discovery selection must be an object')
    if 'parameters' in value:
        if value.keys() - _DEFINITION_FIELDS:
            raise ValueError('unsupported hardware discovery definition field')
        if type(value.get('schema_version', 1)) is not int or value.get('schema_version', 1) != 1:
            raise ValueError('hardware discovery definition schema_version must be 1')
        if value.get('engine', 'hardware.discover') != 'hardware.discover':
            raise ValueError('hardware discovery requires the hardware.discover engine')
        if value.get('operating_mode', 'acquisition') != 'acquisition':
            raise ValueError('live hardware discovery requires acquisition mode')
        if value.get('mode', 'offline') not in {'offline', 'online'}:
            raise ValueError('unsupported hardware discovery execution mode')
        for field in _SEMANTIC_FIELDS:
            if value.get(field):
                raise ValueError(f'hardware discovery does not execute {field}')
        execution = value.get('execution', {})
        if not isinstance(execution, dict) or execution.get('grants'):
            raise ValueError('authored execution grants cannot supply discovery authority')
        params = value['parameters']
    else:
        params = value
    if not isinstance(params, dict):
        raise ValueError('hardware discovery parameters must be an object')
    method = params.get('method')
    if not isinstance(method, str) or method not in METHODS:
        raise ValueError('method requires an explicit implemented discovery choice')
    allowed = {'method', 'binding_id', 'max_frame_bytes'}
    if method != 'serial.enumerate':
        allowed.add('timeout')
    if method == 'can.enumerate':
        allowed.add('interfaces')
    if method == 'sunspec.scan':
        allowed.add('scan')
    if params.keys() - allowed:
        raise ValueError('unsupported hardware discovery parameter: ' + sorted(params.keys() - allowed)[0])
    binding_id = _text(params.get('binding_id'), 'binding_id')
    timeout = None
    if method != 'serial.enumerate':
        timeout = params.get('timeout', 5)
        if type(timeout) not in (float, int):
            raise ValueError('timeout must be a finite nonnegative number')
        timeout = seconds(timeout, 'timeout')
    interfaces = _strings(params.get('interfaces'), 'interfaces') if method == 'can.enumerate' else ()
    selected_scan = None
    if method == 'sunspec.scan':
        scan = params.get('scan')
        if not isinstance(scan, dict) or scan.keys() - _SCAN_FIELDS:
            raise ValueError('scan requires an exact SunSpec scan configuration')
        selected_scan = SunSpecScanConfig.from_dict(scan)
    maximum = _count(params.get('max_frame_bytes', DEFAULT_FRAME_BYTES), 'max_frame_bytes')
    return HardwareDiscoverySelection(method, binding_id, timeout, interfaces, selected_scan, maximum, params)


def _authorize(spec, selected):
    registry = HardwareBindingRegistry.from_grants(spec.get('_runtime_grants') or {}, 'discovery')
    grant = registry.select(selected.binding_id)
    if not isinstance(grant, dict) or grant.get('read_only') is not True:
        raise ValueError('discovery requires an execution-host read-only discovery grant')
    if grant.get('binding_id') != selected.binding_id:
        raise ValueError('discovery binding identity mismatch')
    methods = _strings(grant.get('methods'), 'granted methods')
    if selected.method not in methods:
        raise ValueError('selected discovery method is not granted')
    limits = ResourceLimits.from_dict(grant.get('limits'))
    endpoint = None
    if selected.method == 'can.enumerate':
        interfaces = _strings(grant.get('interfaces'), 'granted interfaces')
        if not set(selected.interfaces).issubset(interfaces):
            raise ValueError('selected CAN interfaces exceed the execution-host grant')
    if selected.scan is not None:
        if type(grant.get('device_id')) is not int or selected.scan.device_id != grant['device_id']:
            raise ValueError('selected SunSpec device_id differs from the execution-host grant')
        if 'base_addresses' in grant:
            bases = grant['base_addresses']
            if not isinstance(bases, list) or any(type(base) is not int or not 0 <= base <= 65535 for base in bases):
                raise ValueError('granted base_addresses must be explicit protocol addresses')
            if not set(selected.scan.base_addresses).issubset(bases):
                raise ValueError('selected SunSpec base_addresses exceed the execution-host grant')
        source = grant.get('endpoint')
        if not isinstance(source, dict) or set(source) != {'host', 'port', 'framing', 'allow_dns'}:
            raise ValueError('SunSpec discovery requires an explicit host, port, framing and DNS policy')
        endpoint = ModbusEndpoint.from_dict(source)
        if 'max_models' not in selected.requested_configuration['scan']:
            selected = replace(selected, scan=replace(selected.scan, max_models=limits.max_models))
        limits.authorize('max_models', selected.scan.max_models)
    if selected.timeout is not None:
        limits.authorize('max_timeout', selected.timeout)
    if 'max_frame_bytes' not in selected.requested_configuration and limits.max_frame_bytes is not None:
        selected = replace(selected, max_frame_bytes=min(DEFAULT_FRAME_BYTES, limits.max_frame_bytes))
    limits.authorize('max_frame_bytes', selected.max_frame_bytes)
    return selected, endpoint, limits


def _runtime(method, client):
    distribution, module = {'serial.enumerate': ('pyserial', 'serial'),
                            'can.enumerate': ('python-can', 'can'),
                            'sunspec.scan': ('pymodbus', 'pymodbus')}[method]
    try:
        version = metadata.version(distribution)
    except metadata.PackageNotFoundError:
        version = None
    loaded = sys.modules.get(module)
    dependency = {'name': distribution, 'version': version, 'loaded': loaded is not None,
                  'module_version': getattr(loaded, '__version__', None)}
    result = {'python': sys.version, 'platform': platform.platform(), 'dependencies': [dependency]}
    if client is not None:
        result['client'] = {'module': type(client).__module__, 'class': type(client).__qualname__}
    return result


def _found(method, payload):
    if not isinstance(payload, dict):
        return 0
    if method == 'sunspec.scan':
        return sum(len(trial.get('models', [])) for trial in payload.get('trials', []))
    return len(payload.get('ports' if method == 'serial.enumerate' else 'configurations', []))


def _status(method, payload):
    status = payload.get('status')
    if status in {'ok', 'complete', 'not_found'}:
        return 'complete'
    if status in {'unavailable', 'unavailable_or_empty'}:
        return 'partial' if _found(method, payload) else 'unavailable'
    return 'partial'


def _error(exc):
    return {'type': type(exc).__name__, 'message': str(exc)}


def execute_hardware_discovery(spec, output_dir, emit, cancelled, *, client_factory=None, clock=time):
    """Execute a validated grant; always retain evidence after execution starts.

    ``client_factory(endpoint, *, timeout, trace_packet)`` must return an unopened
    client exposing public connect/close/read methods. It is a test/integration
    seam, never selected by an authored definition. Host time is observation
    timing, never a claim about a hardware clock.
    """
    selected = validate_selection(spec['definition'])
    selected, endpoint, limits = _authorize(spec, selected)
    paths = HardwareDiscoveryPaths(output_dir)
    paths.root.mkdir(parents=True, exist_ok=True)

    def stamp():
        return {'wall_time': clock.time(), 'monotonic': clock.monotonic()}

    source = {'binding_id': selected.binding_id}
    if endpoint is not None:
        source['endpoint'] = {'host': endpoint.host, 'port': endpoint.port,
                              'framing': endpoint.framing, 'allow_dns': endpoint.allow_dns}
    report = {'format': FORMAT, 'engine': 'hardware.discover', 'method': selected.method,
              'binding_id': selected.binding_id, 'status': 'running',
              'requested_configuration': selected.requested_configuration,
              'effective_configuration': selected.to_dict(), 'resource_limits': limits.to_dict(),
              'budget_scope': {'max_models': 'per_candidate_base', 'max_frame_bytes': 'per_sdk_callback_buffer_prefix'},
              'trace_capture': {'scope': RawModbusTrace.capture_scope, 'transport_wire_reconstruction': False,
                                'disclosure': 'SDK receive callbacks may contain overlapping decoder buffers; these are not disjoint receive chunks or proof of successful sends.'},
              'source': source, 'discovery': None,
              'clock': {'basis': 'execution_host_observation', 'started': stamp(), 'finished': None},
              'implementation_sha256': implementation_sha256(),
              'qualification': {'qualified': False, 'physical_validation': False,
                                'scope': 'Selected metadata enumeration or structural read-only discovery only; no device identity, point semantics, or physical qualification.'}}
    client = None
    discovery = None
    cancellation = None
    capture = RawModbusTrace(selected.max_frame_bytes, clock=clock,
                             framing=endpoint.framing if endpoint is not None else 'tcp')
    with paths.wire.open('w', encoding='utf-8') as wire:
        def trace(sending, packet):
            # Discovery's disclosed budget applies independently to each SDK
            # callback buffer. Preserve buffers individually without inventing
            # a transport stream from potentially overlapping snapshots.
            capture.reset()
            capture.observe(sending, packet)
            row = capture.receipts[-1]
            wire.write(json.dumps(row, allow_nan=False) + '\n')
            wire.flush()
            return packet

        def check_cancelled():
            if cancelled():
                raise ExecutionCancelled('hardware discovery cancelled')

        try:
            check_cancelled()
            emit({'event': 'hardware_discovery_started', 'method': selected.method, 'binding_id': selected.binding_id})
            if selected.method == 'serial.enumerate':
                report['discovery'] = enumerate_serial_ports()
            elif selected.method == 'can.enumerate':
                report['discovery'] = enumerate_can_interfaces(list(selected.interfaces), timeout=selected.timeout)
            else:
                discovery = SunSpecDiscovery(selected.scan)
                client = (endpoint.create_client(selected.timeout, trace_packet=trace) if client_factory is None
                          else client_factory(endpoint, timeout=selected.timeout, trace_packet=trace))
                check_cancelled()
                if not client.connect():
                    raise ConnectionError('SunSpec discovery endpoint connection failed')
                check_cancelled()
                report['discovery'] = discovery.scan(client, cancelled=cancelled)
            check_cancelled()
            report['status'] = _status(selected.method, report['discovery'])
        except ExecutionCancelled as exc:
            cancellation = exc
            report.update(status='cancelled', error=_error(exc))
        except Exception as exc:
            report.update(status='unavailable' if isinstance(exc, ImportError) else 'failed', error=_error(exc))
        finally:
            if report['discovery'] is None and discovery is not None:
                report['discovery'] = discovery.last_report
            if client is not None:
                try:
                    client.close()
                except ExecutionCancelled as exc:
                    cancellation = cancellation or exc
                    report.update(status='cancelled', error=_error(cancellation))
                except Exception as exc:
                    report['close_error'] = _error(exc)
                    if cancellation is None:
                        report['status'] = 'failed'
            if cancellation is None:
                try:
                    check_cancelled()
                except ExecutionCancelled as exc:
                    cancellation = exc
                    report.update(status='cancelled', error=_error(exc))
            report['clock']['finished'] = stamp()
    report['runtime'] = _runtime(selected.method, client)
    paths.report.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    artifacts = [{'name': path.name, 'path': str(path), 'role': role, 'media_type': media,
                  'sha256': digest(path), 'visibility': 'evaluation'} for path, role, media in (
        (paths.report, 'device_discovery', 'application/json'),
        (paths.wire, 'discovery_wire', 'application/x-ndjson'))]
    found = _found(selected.method, report['discovery'])
    result = {'records': found, 'metrics': {'discovery_status': report['status'], 'discovered_count': found,
                                          'discovery_qualified': False},
              'artifacts': artifacts, 'qualification': deepcopy(report['qualification'])}
    if cancellation is not None:
        cancellation.evidence_result = result
        raise cancellation
    emit({'event': 'hardware_discovery_complete', 'method': selected.method, 'status': report['status'], 'discovered_count': found})
    return result
