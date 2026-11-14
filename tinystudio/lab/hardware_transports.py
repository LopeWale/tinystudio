"""Explicit serial, Modbus and receive-only CAN transport configuration.

These helpers validate configuration, not runtime authority. Callers decide when
opening an operator-selected endpoint is authorized. Opening a device or its
native library can affect hardware state; no physical qualification is implied.
Optional native dependencies are imported only when the selected transport opens.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import importlib
import inspect
import ipaddress
import math
import re
from typing import Any
from urllib.parse import urlsplit


_SERIAL_OPTIONS = frozenset({
    'bytesize', 'parity', 'stopbits', 'xonxoff', 'rtscts', 'dsrdtr',
    'write_timeout', 'inter_byte_timeout', 'exclusive',
})
_NETWORK_SERIAL_SCHEMES = frozenset({'socket', 'rfc2217'})


def _integer(value: Any, label: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bound = f'{minimum}..{maximum}' if maximum is not None else f'at least {minimum}'
        raise ValueError(f'{label} must be an integer {bound}')
    return value


def _timeout(value: Any, label: str = 'timeout') -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError(f'{label} must be a finite nonnegative number')
    return float(value)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f'{label} must be an explicit mapping')
    return value


def _allow_dns(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError('allow_dns must be a boolean')
    return value


def _host(value: Any, allow_dns: bool) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or '\0' in value:
        raise ValueError('network endpoint requires an explicit host')
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError as exc:
        if not allow_dns:
            raise ValueError('network endpoint requires a numeric IP unless allow_dns is explicitly true') from exc
    try:
        hostname = value.encode('idna').decode('ascii').rstrip('.')
    except UnicodeError as exc:
        raise ValueError('invalid configured DNS hostname') from exc
    labels = hostname.split('.')
    if len(hostname) > 253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in labels):
        raise ValueError('invalid configured DNS hostname')
    # Validation does not resolve names, normalize the selected host or guess peers.
    return value


def _serial_scheme(port: Any, allow_dns: bool) -> str | None:
    if not isinstance(port, str) or not port or '\0' in port:
        raise ValueError('serial endpoint requires an explicit nonempty device name or URL')
    if '://' not in port:
        return None
    try:
        parsed = urlsplit(port)
        if parsed.scheme not in _NETWORK_SERIAL_SCHEMES | {'loop'}:
            raise ValueError('implemented serial URL schemes are rfc2217, socket and loop')
        if parsed.fragment or parsed.username is not None or parsed.password is not None:
            raise ValueError('serial URLs do not support fragments or user information')
        if parsed.scheme == 'loop':
            if parsed.netloc or parsed.path:
                raise ValueError('loop:// requires an explicit local virtual endpoint without a host or path')
        else:
            _host(parsed.hostname, allow_dns)
            _integer(parsed.port, 'serial URL port', 1, 65535)
            if parsed.path not in ('', '/'):
                raise ValueError('network serial URL cannot contain a device path')
    except (TypeError, ValueError) as exc:
        raise ValueError(f'invalid serial endpoint URL: {exc}') from exc
    return parsed.scheme


def _serial_options(options: Mapping[str, Any], scheme: str | None) -> tuple[tuple[str, Any], ...]:
    unsupported = set(options) - _SERIAL_OPTIONS
    if unsupported:
        raise ValueError(f'unsupported serial options: {sorted(unsupported)}')
    for key, value in options.items():
        if key in {'xonxoff', 'rtscts', 'dsrdtr'} and type(value) is not bool:
            raise ValueError(f'{key} must be a boolean')
        if key == 'exclusive' and value is not None and type(value) is not bool:
            raise ValueError('exclusive must be a boolean or None')
        if key == 'bytesize':
            _integer(value, 'bytesize', 5, 8)
        if key == 'parity' and value not in ('N', 'E', 'O', 'M', 'S'):
            raise ValueError('parity must select N, E, O, M or S')
        if key == 'stopbits' and (isinstance(value, bool) or value not in (1, 1.5, 2)):
            raise ValueError('stopbits must select 1, 1.5 or 2')
        if key in {'write_timeout', 'inter_byte_timeout'} and value is not None:
            _timeout(value, key)
    if scheme == 'rfc2217' and options.get('write_timeout') is not None:
        raise ValueError('RFC2217 does not support non-None write_timeout')
    return tuple(sorted(options.items()))


@dataclass(frozen=True)
class SerialEndpoint:
    """Immutable operator-selected device or implemented pySerial URL.

    ``options`` holds explicitly selected native pySerial keyword arguments.
    Local device names are platform-native and have no /dev prefix restriction.
    RFC2217 needs ``write_timeout=None``; native, socket and loop endpoints retain
    the existing nonblocking write timeout by default.
    """

    port: str
    baudrate: int
    allow_dns: bool = False
    options: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        _allow_dns(self.allow_dns)
        _integer(self.baudrate, 'baudrate', 1)
        scheme = _serial_scheme(self.port, self.allow_dns)
        object.__setattr__(self, 'options', _serial_options(dict(self.options), scheme))

    @classmethod
    def from_dict(cls, endpoint: Mapping[str, Any]) -> SerialEndpoint:
        endpoint = _mapping(endpoint, 'serial endpoint')
        unknown = set(endpoint) - {'port', 'baudrate', 'allow_dns', 'options'} - _SERIAL_OPTIONS
        if unknown:
            raise ValueError(f'unsupported serial endpoint fields: {sorted(unknown)}')
        options = dict(_mapping(endpoint.get('options', {}), 'serial options'))
        for key in _SERIAL_OPTIONS & endpoint.keys():
            if key in options:
                raise ValueError(f'serial option {key} was selected twice')
            options[key] = endpoint[key]
        return cls(port=endpoint.get('port'), baudrate=endpoint.get('baudrate'),
                   allow_dns=endpoint.get('allow_dns', False), options=tuple(options.items()))

    def open(self, timeout: float, library: Any = None) -> Any:
        library = library if library is not None else importlib.import_module('serial')
        scheme = _serial_scheme(self.port, self.allow_dns)
        kwargs = {'baudrate': self.baudrate, 'timeout': _timeout(timeout),
                  'write_timeout': None if scheme == 'rfc2217' else 0,
                  'xonxoff': False, 'rtscts': False, 'dsrdtr': False}
        kwargs.update(dict(self.options))
        if scheme is None:
            port = library.Serial(port=None, **kwargs)
        else:
            factory = getattr(library, 'serial_for_url', None)
            if not callable(factory):
                raise ValueError('selected serial URL requires the public serial_for_url API')
            port = factory(self.port, do_not_open=True, **kwargs)
        try:
            if scheme is None:
                port.port = self.port
            port.open()
        except BaseException:
            port.close()
            raise
        return port


@dataclass(frozen=True)
class ModbusEndpoint:
    """A selected network address and native TCP or RTU-over-TCP framing."""

    host: str
    port: int
    framing: str = 'tcp'
    allow_dns: bool = False

    def __post_init__(self) -> None:
        _host(self.host, _allow_dns(self.allow_dns))
        _integer(self.port, 'port', 1, 65535)
        if self.framing not in {'tcp', 'rtu_over_tcp'}:
            raise ValueError('Modbus framing must select tcp or rtu_over_tcp')

    @classmethod
    def from_dict(cls, endpoint: Mapping[str, Any]) -> ModbusEndpoint:
        endpoint = _mapping(endpoint, 'Modbus endpoint')
        unknown = set(endpoint) - {'host', 'port', 'framing', 'allow_dns'}
        if unknown:
            raise ValueError(f'unsupported Modbus endpoint fields: {sorted(unknown)}')
        return cls(host=endpoint.get('host'), port=endpoint.get('port'),
                   framing=endpoint.get('framing', 'tcp'), allow_dns=endpoint.get('allow_dns', False))

    def create_client(self, timeout: float, trace_packet: Callable[[bool, bytes], bytes] | None = None,
                      library: Any = None) -> Any:
        """Create an unopened native client; traces observe and preserve bytes."""
        library = library if library is not None else importlib.import_module('pymodbus.client')
        kwargs: dict[str, Any] = {'port': self.port, 'timeout': _timeout(timeout), 'retries': 0}
        if trace_packet is not None:
            if not callable(trace_packet):
                raise ValueError('trace_packet must be callable')
            def observe(sending: bool, packet: bytes) -> bytes:
                trace_packet(sending, packet)
                return packet
            kwargs['trace_packet'] = observe
        if self.framing == 'rtu_over_tcp':
            framer_type = getattr(library, 'FramerType', None)
            if framer_type is None and getattr(library, '__name__', '').split('.')[0] == 'pymodbus':
                framer_type = getattr(importlib.import_module('pymodbus'), 'FramerType', None)
            rtu = getattr(framer_type, 'RTU', None)
            if rtu is None:
                raise ValueError('selected RTU-over-TCP framing requires the public FramerType.RTU API')
            kwargs['framer'] = rtu
        # Unsupported selected keywords must fail visibly at this native boundary.
        return library.ModbusTcpClient(self.host, **kwargs)


def read_registers(client: Any, *, kind: str, address: int, count: int, device_id: int) -> Any:
    """Issue one bounded register read through a public device_id/slave API.

    Preserve the one-byte TCP Unit Identifier, including direct-TCP unit 255.
    RTU gateway/broadcast response semantics depend on the selected endpoint;
    this helper does not replace an operator-selected identifier.
    """
    if kind not in {'input_registers', 'holding_registers'}:
        raise ValueError('only input_registers and holding_registers reads are supported')
    _integer(address, 'register address', 0, 65535)
    _integer(count, 'register count', 1, 125)
    _integer(device_id, 'device_id', 0, 255)
    if address + count > 65536:
        raise ValueError('register range exceeds protocol address space')
    method = getattr(client, 'read_' + kind, None)
    if not callable(method):
        raise ValueError(f'selected Modbus client lacks the public read_{kind} API')
    parameters = inspect.signature(method).parameters
    key = 'device_id' if 'device_id' in parameters else 'slave' if 'slave' in parameters else None
    if key is None or parameters[key].kind is inspect.Parameter.POSITIONAL_ONLY:
        raise ValueError('unsupported PyModbus read API: public device_id/slave parameter is required')
    return method(address=address, count=count, **{key: device_id})


class CANReader:
    """Receive native CAN frames; wire evidence is the application payload.

    Neither physical CAN bitstream, arbitration traffic nor link CRC is exposed
    by python-can's Message API. Native frame metadata is retained separately.
    All driver options are explicit; python-can's ambient config is disabled.
    The capture budget applies to both wire and data_hex; zero retains metadata
    and the original payload length/hash without retaining payload bytes.
    """

    def __init__(self, binding: Mapping[str, Any], timeout: float, max_frame: int | None,
                 library: Any = None):
        endpoint = _mapping(_mapping(binding, 'CAN binding').get('endpoint'), 'CAN endpoint')
        interface = endpoint.get('interface')
        if not isinstance(interface, str) or not interface or '\0' in interface:
            raise ValueError('CAN endpoint requires an explicit native interface')
        if 'channel' not in endpoint:
            raise ValueError('CAN endpoint requires an explicitly selected channel')
        options = dict(_mapping(endpoint.get('options', {}), 'CAN native options'))
        reserved = {'interface', 'channel', 'ignore_config'}
        if reserved & options.keys() or 'ignore_config' in endpoint:
            raise ValueError('CAN options cannot override interface, channel or ignore_config')
        for key in endpoint.keys() - {'interface', 'channel', 'options'}:
            if key in options:
                raise ValueError(f'CAN option {key} was selected twice')
            options[key] = endpoint[key]
        self.timeout = _timeout(timeout)
        self.maximum = None if max_frame is None else _integer(max_frame, 'max_frame', 0)
        library = library if library is not None else importlib.import_module('can')
        self.library_metadata = {'name': 'python-can', 'version': getattr(library, '__version__', None)}
        self.bus = library.Bus(interface=interface, channel=endpoint['channel'], ignore_config=True, **options)

    def read(self, timeout: float) -> dict[str, Any] | None:
        message = self.bus.recv(timeout=_timeout(timeout))
        if message is None:
            return None
        payload = bytes(message.data)
        captured = payload if self.maximum is None else payload[:self.maximum]
        metadata = {key: getattr(message, key, None) for key in (
            'arbitration_id', 'is_extended_id', 'is_remote_frame', 'is_error_frame',
            'is_fd', 'bitrate_switch', 'error_state_indicator', 'is_rx',
            'dlc', 'channel', 'timestamp',
        )}
        metadata['data_hex'] = captured.hex()
        return {'wire': captured, 'wire_scope': 'can_application_payload',
                'truncated': len(captured) < len(payload), 'original_wire_bytes': len(payload),
                'wire_sha256': hashlib.sha256(payload).hexdigest(), 'can_frame': metadata}

    def close(self) -> None:
        self.bus.shutdown()
