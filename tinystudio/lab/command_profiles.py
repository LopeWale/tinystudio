"""Native command contracts, separate from protocol-1 device acknowledgements.

Construction validates selected configuration without importing optional drivers
or opening endpoints. Bytes, protocol fields and author choices are preserved.
Delivery receipts and declared device capabilities never prove physical outcomes.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, TypedDict

from .hardware_transports import ModbusEndpoint, SerialEndpoint, _host


PROFILE_FORMAT = 'tinystudio.native-command-profile.v1'
COMMAND_FORMAT = 'tinystudio.native-command.v1'
RECEIPT_FORMAT = 'tinystudio.command-delivery.v1'
COMPLETED_NATIVE_STATUSES = frozenset({'delivered_unverified', 'protocol_accepted', 'response_received', 'application_reported'})
COMPLETED_RECEIPT_STATUSES = COMPLETED_NATIVE_STATUSES
_OPERATIONS = {
    'serial': ('serial.write', 'serial.query'),
    'tcp': ('tcp.write', 'tcp.query'),
    'modbus': ('modbus.write_register', 'modbus.write_registers', 'modbus.write_coil', 'modbus.write_coils',
               'modbus.read_holding_registers', 'modbus.read_input_registers', 'modbus.read_coils', 'modbus.read_discrete_inputs'),
    'mqtt': ('mqtt.publish',), 'can': ('can.send',), 'custom': (),
}
_CAN_FLAGS = {'is_remote_frame', 'is_error_frame', 'bitrate_switch', 'error_state_indicator'}


class CommandProfileError(ValueError):
    pass


class UnavailableNativeOperation(TypedDict):
    reason: str
    required_fields: list[str]


class NativeCommandCapabilities(TypedDict):
    format: str
    profile_digest: str
    transport_kind: str
    implemented_operations: list[str] | None
    selected_operations: list[str] | None
    effective_operations: list[str] | None
    unavailable_operations: dict[str, UnavailableNativeOperation]
    allowed_operations: list[str] | None
    completed_statuses: list[str]
    payload_schema: dict[str, Any]
    max_request_bytes: int | None
    max_response_bytes: int | None
    max_capture_bytes: int | None
    max_identity_chars: int | None
    byte_budget_scopes: dict[str, str]
    capability_claims: dict[str, Any]
    local_stop_configured: bool
    ownership_key: str | None
    qualification: dict[str, str]


def _require(condition, message):
    if not condition:
        raise CommandProfileError(message)


def _finite_json(value):
    """JSON values only: never silently convert tuples, keys, bytes or NaN."""
    if value is None or type(value) in {str, int, bool}:
        return
    if type(value) is float:
        _require(math.isfinite(value), 'command configuration and payload must contain finite JSON numbers')
    elif type(value) is list:
        for item in value:
            _finite_json(item)
    elif type(value) is dict:
        _require(all(type(key) is str for key in value), 'JSON object keys must be strings')
        for item in value.values():
            _finite_json(item)
    else:
        raise CommandProfileError('command configuration and payload must contain exact JSON values')


def _json(value):
    try:
        _finite_json(value)
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        encoded.encode('utf-8')
        return encoded
    except CommandProfileError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise CommandProfileError('command configuration and payload require encodable finite JSON') from exc


def _integer(value, label, minimum=0, maximum=None):
    _require(type(value) is int and value >= minimum and (maximum is None or value <= maximum),
             label + ' is outside its integer protocol/configuration range')
    return value


def _positive(value, label):
    _require(type(value) in {int, float} and value > 0, label + ' must be a positive finite number')
    if type(value) is float:
        _require(math.isfinite(value), label + ' must be a positive finite number')
    return value


def _budget(value, label):
    return None if value is None else _integer(value, label)


def _exact(value, required, optional=(), *, label):
    _require(type(value) is dict and set(required) <= value.keys()
             and not value.keys() - set(required) - set(optional), label + ' has missing or unsupported fields')


def decode_bytes(data) -> bytes:
    """Decode one explicit data object; do not append, strip or normalize bytes."""
    _exact(data, {'encoding', 'value'}, label='byte data')
    _require(type(data['value']) is str, 'byte data value must be a string')
    try:
        if data['encoding'] == 'utf8':
            return data['value'].encode('utf-8')
        if data['encoding'] == 'hex':
            return bytes.fromhex(data['value'])
        if data['encoding'] == 'base64':
            return base64.b64decode(data['value'], validate=True)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise CommandProfileError('invalid explicitly encoded byte data') from exc
    raise CommandProfileError('byte encoding must select utf8, hex or base64')


def validate_read(settings) -> dict[str, Any]:
    _require(type(settings) is dict, 'query read settings must be an explicit object')
    kind = settings.get('kind')
    if kind == 'delimiter':
        _exact(settings, {'kind', 'delimiter'}, {'max_bytes'}, label='delimiter read settings')
        _require(bool(decode_bytes(settings['delimiter'])), 'query read delimiter must contain bytes')
    elif kind == 'fixed_bytes':
        _exact(settings, {'kind', 'count'}, {'max_bytes'}, label='fixed-byte read settings')
        _integer(settings['count'], 'fixed-byte read count')
    else:
        raise CommandProfileError('query read kind must select delimiter or fixed_bytes')
    result = deepcopy(settings)
    result['max_bytes'] = _budget(settings.get('max_bytes'), 'query read max_bytes')
    return result


def _network(config):
    _require(type(config.get('allow_dns', False)) is bool, 'allow_dns must be boolean')
    _host(config.get('host'), config.get('allow_dns', False))
    _integer(config.get('port'), 'network port', 1, 65535)


def validate_transport(config) -> dict[str, Any]:
    """Validate an unopened endpoint, retaining explicitly selected native options."""
    _require(type(config) is dict and type(config.get('kind')) is str and config['kind'] in _OPERATIONS,
             'unsupported native command transport kind')
    _finite_json(config)
    result = deepcopy(config)
    kind = config['kind']
    try:
        if kind == 'serial':
            SerialEndpoint.from_dict({key: value for key, value in config.items() if key not in {'kind', 'read'}})
            result.setdefault('allow_dns', False)
            result.setdefault('options', {})
        elif kind == 'modbus':
            ModbusEndpoint.from_dict({key: value for key, value in config.items()
                                      if key not in {'kind', 'device_id', 'response_expected'}})
            _integer(config.get('device_id'), 'Modbus device_id', 0, 255)
            _require(type(config.get('response_expected', True)) is bool, 'response_expected must be boolean')
            result.setdefault('framing', 'tcp')
            result.setdefault('allow_dns', False)
            result.setdefault('response_expected', True)
        elif kind in {'tcp', 'mqtt'}:
            required = {'kind', 'host', 'port'} | ({'topic'} if kind == 'mqtt' else set())
            optional = {'allow_dns', 'tls'} | ({'read'} if kind == 'tcp' else set())
            _exact(config, required, optional, label=kind + ' transport')
            _network(config)
            _require(type(config.get('tls', False)) is bool, 'tls must be boolean; runtime TLS secrets are supplied separately')
            result.setdefault('allow_dns', False)
            result.setdefault('tls', False)
            if kind == 'mqtt':
                topic = config['topic']
                _require(type(topic) is str and bool(topic) and not any(c in topic for c in ('\0', '#', '+')),
                         'MQTT publish topic must be explicitly selected without subscription wildcards')
                _require(len(topic.encode('utf-8')) <= 65535, 'MQTT topic exceeds the two-byte protocol string length')
        elif kind == 'can':
            _exact(config, {'kind', 'interface', 'channel'}, {'options'}, label='CAN transport')
            _require(type(config['interface']) is str and bool(config['interface']) and '\0' not in config['interface'],
                     'CAN requires an explicit native interface')
            options = config.get('options', {})
            _require(type(options) is dict and not {'interface', 'channel', 'ignore_config'} & options.keys(),
                     'CAN options cannot replace the explicitly selected interface/channel or enable ambient configuration')
            result.setdefault('options', {})
        else:
            _exact(config, {'kind', 'entrypoint', 'identity'}, {'adapter_config'}, label='custom adapter transport')
            entrypoint = config['entrypoint']
            _require(type(entrypoint) is str and entrypoint.count(':') == 1
                     and all(part.isidentifier() for section in entrypoint.split(':') for part in section.split('.')),
                     'custom adapter entrypoint must select module:Class without importing it')
            _require(type(config['identity']) in {str, dict} and bool(config['identity']),
                     'custom adapter requires an explicit nonempty public identity')
            _require(type(config.get('adapter_config', {})) is dict, 'adapter_config must be an explicit JSON object')
            result.setdefault('adapter_config', {})
        if kind in {'serial', 'tcp'}:
            result['read'] = validate_read(config['read']) if config.get('read') is not None else None
        _json(result).encode('utf-8')
        return result
    except CommandProfileError:
        raise
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
        raise CommandProfileError('invalid selected native endpoint configuration: ' + str(exc)) from exc


def _data_schema():
    return {'type': 'object', 'required': ['encoding', 'value'], 'additionalProperties': False,
            'properties': {'encoding': {'enum': ['utf8', 'hex', 'base64']}, 'value': {'type': 'string'}}}


def _schema(operation):
    properties = {'operation': {'const': operation}}
    required = ['operation']
    def field(name, schema, *, optional=False):
        properties[name] = schema
        if not optional:
            required.append(name)
    if operation in {'serial.write', 'serial.query', 'tcp.write', 'tcp.query', 'mqtt.publish', 'can.send'}:
        field('data', _data_schema())
    if operation == 'mqtt.publish':
        field('qos', {'type': 'integer', 'enum': [0, 1, 2]})
        field('retain', {'type': 'boolean'})
    elif operation == 'can.send':
        field('arbitration_id', {'type': 'integer', 'minimum': 0, 'maximum': 0x1FFFFFFF})
        field('is_extended_id', {'type': 'boolean'})
        field('is_fd', {'type': 'boolean'})
        for name in sorted(_CAN_FLAGS):
            field(name, {'type': 'boolean'}, optional=True)
        field('dlc', {'type': 'integer', 'minimum': 0, 'maximum': 64}, optional=True)
    elif operation.startswith('modbus.'):
        field('address', {'type': 'integer', 'minimum': 0, 'maximum': 65535})
        suffix = operation.removeprefix('modbus.')
        if suffix in {'write_register', 'write_coil'}:
            field('value', {'type': 'boolean'} if suffix == 'write_coil' else {'type': 'integer', 'minimum': 0, 'maximum': 65535})
        elif suffix in {'write_registers', 'write_coils'}:
            registers = suffix == 'write_registers'
            field('values', {'type': 'array', 'minItems': 1, 'maxItems': 123 if registers else 1968,
                  'items': {'type': 'integer', 'minimum': 0, 'maximum': 65535} if registers else {'type': 'boolean'}})
        else:
            field('count', {'type': 'integer', 'minimum': 1,
                            'maximum': 125 if suffix.endswith('registers') else 2000})
    return {'type': 'object', 'required': required, 'properties': properties, 'additionalProperties': False}


@dataclass(frozen=True)
class NativeCommandProfile:
    _binding_json: str
    digest: str

    @classmethod
    def from_binding(cls, binding) -> NativeCommandProfile:
        try:
            _exact(binding, {'endpoint_id', 'operator_id', 'transport', 'command_profile'},
                   {'send_timeout', 'max_lease_seconds', 'limits', 'local_stop', 'ownership_key'}, label='native command binding')
            _finite_json(binding)
            value = deepcopy(binding)
            for name in ('endpoint_id', 'operator_id'):
                _require(type(value[name]) is str and bool(value[name]) and '\0' not in value[name], 'invalid ' + name)
            if 'ownership_key' in value:
                _require(type(value['ownership_key']) is str and bool(value['ownership_key']),
                         'ownership_key must be an explicit nonempty operator-selected namespace')
            _require(value.get('limits') in (None, {}),
                     'native limits cannot silently apply legacy typed controls; use a custom adapter or legacy typed profile')
            value.pop('limits', None)
            value['transport'] = validate_transport(value['transport'])
            selected = value['command_profile']
            _exact(selected, {'format'}, {'allowed_operations', 'max_request_bytes', 'max_response_bytes',
                   'max_capture_bytes', 'max_identity_chars', 'capability_claims'}, label='native command profile')
            _require(selected['format'] == PROFILE_FORMAT, 'native command profile format must be explicitly selected')
            operations = selected.get('allowed_operations')
            _require(operations is None or type(operations) is list
                     and all(type(item) is str and bool(item) for item in operations)
                     and len(operations) == len(set(operations)), 'allowed_operations must be None or a unique explicit operation list')
            if operations is not None and value['transport']['kind'] != 'custom':
                _require(all(item in _OPERATIONS[value['transport']['kind']] for item in operations),
                         'allowed_operations must belong to the selected native transport')
            selected.setdefault('allowed_operations', None)
            for name in ('max_request_bytes', 'max_response_bytes', 'max_capture_bytes'):
                selected[name] = _budget(selected.get(name), name)
            identity_budget = selected.get('max_identity_chars')
            selected['max_identity_chars'] = (None if identity_budget is None else
                                               _integer(identity_budget, 'max_identity_chars', 1))
            _require(identity_budget is None or all(len(value[name]) <= identity_budget for name in ('endpoint_id', 'operator_id')),
                     'native binding identity exceeds the selected character budget')
            _require(type(selected.get('capability_claims', {})) is dict, 'capability_claims must be a public JSON object')
            selected.setdefault('capability_claims', {})
            value['send_timeout'] = _positive(value.get('send_timeout', 2), 'send_timeout')
            lease = value.get('max_lease_seconds')
            value['max_lease_seconds'] = None if lease is None else _positive(lease, 'max_lease_seconds')
            value.setdefault('local_stop', None)
            digest = hashlib.sha256(_json({key: value[key] for key in ('command_profile', 'transport')}).encode()).hexdigest()
            profile = cls(_json(value), digest)
            if value['local_stop'] is not None:
                profile.validate_payload(value['local_stop'], operator_action=True)
            return profile
        except CommandProfileError:
            raise
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
            raise CommandProfileError('invalid native command profile: ' + str(exc)) from exc

    @property
    def binding(self) -> dict[str, Any]:
        return json.loads(self._binding_json)

    @property
    def local_stop_payload(self) -> dict[str, Any] | None:
        return self.binding['local_stop']

    decode_bytes = staticmethod(decode_bytes)

    def validate_payload(self, payload, *, operator_action=False) -> dict[str, Any]:
        _require(type(operator_action) is bool, 'operator_action must be boolean')
        _require(type(payload) is dict, 'native command payload must be a JSON object')
        encoded = _json(payload).encode('utf-8')
        operation = payload.get('operation')
        _require(type(operation) is str and bool(operation), 'native command requires an explicit operation')
        binding = self.binding
        transport = binding['transport']
        kind = transport['kind']
        selected = binding['command_profile']
        allowed = selected['allowed_operations']
        if not operator_action and allowed is not None:
            _require(operation in allowed, 'command operation is not in the selected allowed_operations')
        if kind == 'custom':
            size = len(encoded)
        else:
            _require(operation in _OPERATIONS[kind], 'operation does not belong to the bound native transport')
            schema = _schema(operation)
            _exact(payload, schema['required'], set(schema['properties']) - set(schema['required']), label='native command payload')
            if kind in {'serial', 'tcp', 'mqtt', 'can'}:
                wire = decode_bytes(payload['data'])
                size = len(wire)
                if operation.endswith('.query'):
                    _require(transport['read'] is not None, 'query operation requires explicit bound read framing')
                if kind == 'mqtt':
                    _integer(payload['qos'], 'MQTT qos', 0, 2)
                    _require(type(payload['retain']) is bool, 'MQTT retain must be explicitly boolean')
                elif kind == 'can':
                    self._can(payload, wire)
            else:
                size = self._modbus(payload, transport)
        maximum = selected['max_request_bytes']
        _require(maximum is None or size <= maximum, 'command exceeds the selected request byte budget')
        return deepcopy(payload)

    @staticmethod
    def _modbus(payload, transport):
        operation = payload['operation'].removeprefix('modbus.')
        address = _integer(payload['address'], 'Modbus address', 0, 65535)
        count = 1
        extra = 0
        if operation == 'write_register':
            _integer(payload['value'], 'Modbus register value', 0, 65535)
        elif operation == 'write_coil':
            _require(type(payload['value']) is bool, 'Modbus coil value must be boolean')
        elif operation in {'write_registers', 'write_coils'}:
            values = payload['values']
            _require(type(values) is list, 'Modbus values must be an explicit list')
            registers = operation == 'write_registers'
            count = _integer(len(values), 'Modbus values count', 1, 123 if registers else 1968)
            for value in values:
                if registers:
                    _integer(value, 'Modbus register value', 0, 65535)
                else:
                    _require(type(value) is bool, 'Modbus coil values must be boolean')
            extra = 1 + (2 * count if registers else (count + 7) // 8)
        else:
            count = _integer(payload['count'], 'Modbus read count', 1, 125 if operation.endswith('registers') else 2000)
        _require(address + count <= 65536, 'Modbus range exceeds its protocol address space')
        return (12 if transport['framing'] == 'tcp' else 8) + extra

    @staticmethod
    def _can(payload, wire):
        for name in {'is_extended_id', 'is_fd'} | (_CAN_FLAGS & payload.keys()):
            _require(type(payload[name]) is bool, 'CAN ' + name + ' must be boolean')
        _integer(payload['arbitration_id'], 'CAN arbitration_id', 0, 0x1FFFFFFF if payload['is_extended_id'] else 0x7FF)
        maximum = 64 if payload['is_fd'] else 8
        _require(len(wire) <= maximum, 'CAN payload exceeds the selected classic/FD protocol length')
        remote = payload.get('is_remote_frame', False)
        _require(not remote or not payload['is_fd'] and not wire, 'CAN remote frames cannot carry data or use FD')
        _require(not remote or not payload.get('is_error_frame', False), 'CAN remote and error frame flags are mutually exclusive')
        _require(payload['is_fd'] or not payload.get('bitrate_switch', False)
                 and not payload.get('error_state_indicator', False), 'CAN FD flags require is_fd=true')
        if 'dlc' in payload:
            dlc = _integer(payload['dlc'], 'CAN dlc', 0, maximum)
            _require(remote or dlc == len(wire), 'CAN dlc differs from the authored data length')

    def validate_receipt(self, request, receipt) -> dict[str, Any]:
        _require(type(request) is dict and type(receipt) is dict, 'native request and receipt must be JSON objects')
        _json(request)
        _json(receipt)
        binding = self.binding
        _require(request.get('endpoint_id') == binding['endpoint_id']
                 and request.get('profile_digest') == self.digest, 'native receipt request differs from the selected endpoint/profile')
        for name in ('endpoint_id', 'command_id'):
            _require(type(request.get(name)) is str and bool(request[name])
                     and type(receipt.get(name)) is str and receipt[name] == request[name], 'native receipt identity does not match its request')
        _require(type(request.get('epoch')) is int and request['epoch'] >= 0
                 and type(receipt.get('epoch')) is int and receipt['epoch'] == request['epoch'], 'native receipt epoch does not match its request')
        _require(receipt.get('format') == RECEIPT_FORMAT and receipt.get('profile_digest') == self.digest,
                 'native receipt format/profile digest does not match')
        _require(type(receipt.get('status')) is str and receipt['status'] in COMPLETED_NATIVE_STATUSES,
                 'native receipt status is uncertain or is not a completed delivery status')
        _require(receipt.get('physical_outcome') == 'unverified', 'native receipt cannot confirm a physical outcome')
        confirmed = receipt.get('device_application_confirmed')
        _require(type(confirmed) is bool, 'native receipt device_application_confirmed must be boolean')
        if receipt['status'] == 'application_reported':
            _require(binding['transport']['kind'] == 'custom',
                     'only a custom adapter can report an application outcome')
            _require(receipt.get('application_confirmation_scope') == 'adapter_claim',
                     'custom application report must remain an adapter claim')
        if binding['transport']['kind'] == 'custom' and confirmed:
            _require(receipt['status'] == 'application_reported'
                     and receipt.get('application_confirmation_scope') == 'adapter_claim',
                     'custom application acknowledgement must remain an adapter claim')
        else:
            _require(confirmed is False, 'built-in transport delivery is not device application confirmation')
        return deepcopy(receipt)

    def describe(self) -> NativeCommandCapabilities:
        binding = self.binding
        kind = binding['transport']['kind']
        selected = binding['command_profile']
        implemented = None if kind == 'custom' else list(_OPERATIONS[kind])
        selection = selected['allowed_operations']
        operations = deepcopy(implemented if selection is None and kind != 'custom' else selection)
        unavailable = {}
        if kind in {'serial', 'tcp'} and binding['transport']['read'] is None:
            unavailable = {operation: {'reason': 'missing_configuration', 'required_fields': ['transport.read']}
                           for operation in operations if operation.endswith('.query')}
            operations = [operation for operation in operations if operation not in unavailable]
        if operations == []:
            schema = {'not': {}}
        elif kind == 'custom':
            operation_schema = {'type': 'string', 'minLength': 1}
            if operations is not None:
                operation_schema['enum'] = operations
            schema = {'type': 'object', 'required': ['operation'], 'properties': {'operation': operation_schema},
                      'additionalProperties': True}
        else:
            schema = {'oneOf': [_schema(operation) for operation in operations]}
        scope = ('protocol_adu_bytes' if kind == 'modbus' else 'canonical_json_payload_bytes' if kind == 'custom' else 'authored_application_payload_bytes')
        statuses = COMPLETED_NATIVE_STATUSES if kind == 'custom' else COMPLETED_NATIVE_STATUSES - {'application_reported'}
        return {'format': PROFILE_FORMAT, 'profile_digest': self.digest, 'transport_kind': kind,
                'implemented_operations': implemented, 'selected_operations': selection,
                'effective_operations': deepcopy(operations), 'unavailable_operations': unavailable,
                'allowed_operations': operations, 'payload_schema': schema, 'completed_statuses': sorted(statuses),
                'max_request_bytes': selected['max_request_bytes'], 'max_response_bytes': selected['max_response_bytes'],
                'max_capture_bytes': selected['max_capture_bytes'],
                'max_identity_chars': selected['max_identity_chars'],
                'byte_budget_scopes': {'request': scope, 'response': 'protocol_adu_bytes' if kind == 'modbus' else 'received_application_bytes',
                                      'capture': 'retained_transport_evidence_bytes'},
                'capability_claims': selected['capability_claims'], 'local_stop_configured': binding['local_stop'] is not None,
                'ownership_key': binding.get('ownership_key'),
                'qualification': {'device_capabilities': 'operator_claims_only', 'delivery': 'transport_or_adapter_report_only',
                                  'physical_outcome': 'unverified', 'device_fencing': 'unverified',
                                  'ownership_namespace': 'operator_claim_only_not_single_writer_proof'}}
