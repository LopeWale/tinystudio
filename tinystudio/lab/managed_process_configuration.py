"""Pure selections and paths for an explicitly managed native process.

These objects do not resolve paths or hosts, inspect executables, create files,
or launch probes. Readiness describes the selected predicate, not device or
physical qualification. Runtime credentials belong to the parent, not this plan.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import math
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .command_profiles import decode_bytes
from .hardware_transports import _host


def _text(value, label, *, empty=False):
    if type(value) is not str or (not empty and not value) or '\0' in value:
        raise ValueError(label + ' must be a UTF-8 string without NUL')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ValueError(label + ' must contain valid UTF-8 text') from None
    return value


def _argv(value):
    if type(value) not in {list, tuple} or not value:
        raise ValueError('argv must be an explicit nonempty list or tuple')
    return tuple(_text(argument, 'argv argument', empty=index > 0) for index, argument in enumerate(value))


def _path(value, label):
    if isinstance(value, Path):
        _text(str(value), label)
        return value
    return Path(_text(value, label))


def _seconds(value, label, *, optional=False):
    if value is None and optional:
        return value
    try:
        valid = type(value) in {int, float} and math.isfinite(value) and value >= 0
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        raise ValueError(label + ' must be finite and nonnegative' + (' or None' if optional else ''))
    return value


def _fields(value, required, optional=(), *, label):
    if (type(value) is not dict or not set(required) <= value.keys()
            or value.keys() - set(required) - set(optional)):
        raise ValueError(label + ' has missing or unsupported fields')


def _stream(value):
    if type(value) is not str or value not in {'stdout', 'stderr'}:
        raise ValueError('log stream must select stdout or stderr')
    return value


@dataclass(frozen=True)
class ReadinessSelection:
    kind: str
    host: str | None = field(default=None, kw_only=True)
    port: int | None = field(default=None, kw_only=True)
    stream: str | None = field(default=None, kw_only=True)
    marker: bytes | None = field(default=None, kw_only=True)
    argv: tuple[str, ...] | None = field(default=None, kw_only=True)
    cwd: Path | None = field(default=None, kw_only=True)
    expected_exit_code: int = field(default=0, kw_only=True)

    def __post_init__(self):
        allowed = {
            'process_running': set(), 'tcp': {'host', 'port'},
            'log_bytes': {'stream', 'marker'}, 'command': {'argv', 'cwd', 'expected_exit_code'},
        }
        if type(self.kind) is not str or self.kind not in allowed:
            raise ValueError('readiness requires process_running, tcp, log_bytes or command')
        selected = {name for name in ('host', 'port', 'stream', 'marker', 'argv', 'cwd')
                    if getattr(self, name) is not None}
        if self.expected_exit_code != 0 or type(self.expected_exit_code) is not int:
            selected.add('expected_exit_code')
        if selected - allowed[self.kind]:
            raise ValueError('readiness contains fields for a different selected kind')
        if self.kind == 'tcp':
            _host(_text(self.host, 'readiness host'), True)
            if type(self.port) is not int or not 1 <= self.port <= 65535:
                # TCP's destination port field is a protocol constraint, not a
                # scientific or deployment-specific resource budget.
                raise ValueError('TCP readiness port must be an integer from 1 through 65535')
        elif self.kind == 'log_bytes':
            _stream(self.stream)
            if type(self.marker) not in {bytes, bytearray}:
                raise ValueError('log readiness marker must be explicitly selected bytes')
            object.__setattr__(self, 'marker', bytes(self.marker))
        elif self.kind == 'command':
            object.__setattr__(self, 'argv', _argv(self.argv))
            if self.cwd is not None:
                object.__setattr__(self, 'cwd', _path(self.cwd, 'readiness cwd'))
            if type(self.expected_exit_code) is not int:
                raise ValueError('readiness expected_exit_code must be an integer')

    @classmethod
    def from_dict(cls, value):
        kinds = {
            'process_running': (set(), set()),
            'tcp': ({'host', 'port'}, set()),
            'log_bytes': ({'stream', 'marker'}, set()),
            'command': ({'argv'}, {'cwd', 'expected_exit_code'}),
        }
        if type(value) is not dict or type(value.get('kind')) is not str or value['kind'] not in kinds:
            raise ValueError('readiness requires an explicit supported kind')
        required, optional = kinds[value['kind']]
        _fields(value, required | {'kind'}, optional, label='readiness')
        fields = dict(value)
        if value['kind'] == 'log_bytes':
            fields['marker'] = decode_bytes(value['marker'])
        return cls(**fields)

    @property
    def scope(self):
        return {'process_running': 'owned_process_alive_only', 'tcp': 'endpoint_reachability_only',
                'log_bytes': 'owned_log_byte_sequence_observed', 'command': 'selected_command_exit_status'}[self.kind]

    def as_dict(self):
        if self.kind == 'tcp':
            return {'kind': self.kind, 'host': self.host, 'port': self.port}
        if self.kind == 'log_bytes':
            return {'kind': self.kind, 'stream': self.stream,
                    'marker': {'encoding': 'base64', 'value': base64.b64encode(self.marker).decode('ascii')}}
        if self.kind == 'command':
            return {'kind': self.kind, 'argv': list(self.argv), 'cwd': str(self.cwd) if self.cwd is not None else None,
                    'expected_exit_code': self.expected_exit_code}
        return {'kind': self.kind}


@dataclass(frozen=True)
class ManagedProcessSelection:
    argv: tuple[str, ...]
    cwd: Path
    readiness: ReadinessSelection
    public_env: Mapping[str, str] = field(default_factory=dict, kw_only=True)
    startup_timeout_seconds: float | None = field(default=None, kw_only=True)
    shutdown_grace_seconds: float = field(default=3, kw_only=True)
    max_log_bytes: int | None = field(default=None, kw_only=True)
    poll_interval_seconds: float = field(default=.02, kw_only=True)
    raw_log_visibility: str | None = field(default=None, kw_only=True)

    def __post_init__(self):
        object.__setattr__(self, 'argv', _argv(self.argv))
        object.__setattr__(self, 'cwd', _path(self.cwd, 'process cwd'))
        if not isinstance(self.readiness, ReadinessSelection):
            raise ValueError('process readiness requires a ReadinessSelection')
        if not isinstance(self.public_env, Mapping):
            raise ValueError('public_env must be an explicit string mapping')
        environment = {}
        for key, value in self.public_env.items():
            _text(key, 'public environment key')
            if '=' in key:
                raise ValueError('public environment keys must not contain =')
            environment[key] = _text(value, 'public environment value', empty=True)
        object.__setattr__(self, 'public_env', MappingProxyType(environment))
        _seconds(self.startup_timeout_seconds, 'startup_timeout_seconds', optional=True)
        _seconds(self.shutdown_grace_seconds, 'shutdown_grace_seconds')
        _seconds(self.poll_interval_seconds, 'poll_interval_seconds')
        if self.max_log_bytes is not None and (type(self.max_log_bytes) is not int or self.max_log_bytes < 0):
            raise ValueError('max_log_bytes must be a nonnegative integer or None')
        if self.raw_log_visibility is not None and (type(self.raw_log_visibility) is not str
                or self.raw_log_visibility not in {'model', 'evaluation', 'truth'}):
            raise ValueError('raw_log_visibility must select model, evaluation, truth or None for private logs')

    @classmethod
    def from_dict(cls, value):
        _fields(value, {'argv', 'cwd', 'readiness'},
                {'public_env', 'startup_timeout_seconds', 'shutdown_grace_seconds', 'max_log_bytes',
                 'poll_interval_seconds', 'raw_log_visibility'}, label='managed process selection')
        fields = dict(value)
        fields['readiness'] = ReadinessSelection.from_dict(value['readiness'])
        return cls(**fields)

    @property
    def export_raw_logs(self):
        return self.raw_log_visibility is not None

    @property
    def raw_log_policy(self):
        return {'export_raw_logs': self.export_raw_logs, 'visibility': self.raw_log_visibility,
                'automatic_redaction': False, 'raw_rewrite': False}

    def as_dict(self):
        return {'argv': list(self.argv), 'cwd': str(self.cwd), 'readiness': self.readiness.as_dict(),
                'public_env': dict(self.public_env), 'startup_timeout_seconds': self.startup_timeout_seconds,
                'shutdown_grace_seconds': self.shutdown_grace_seconds, 'max_log_bytes': self.max_log_bytes,
                'poll_interval_seconds': self.poll_interval_seconds, 'raw_log_visibility': self.raw_log_visibility}


@dataclass(frozen=True)
class ManagedProcessPaths:
    work: Path

    def __post_init__(self):
        object.__setattr__(self, 'work', _path(self.work, 'managed process work directory'))

    @property
    def evidence_directory(self):
        return self.work / 'evidence' / 'parent-emulator'

    @property
    def private_directory(self):
        return self.work / 'parent-emulator-private'

    @property
    def lifecycle(self):
        return self.evidence_directory / 'lifecycle.jsonl'

    @property
    def readiness(self):
        return self.evidence_directory / 'readiness.jsonl'

    @property
    def manifest(self):
        return self.evidence_directory / 'manifest.json'

    def private_log(self, stream):
        return self.private_directory / (_stream(stream) + '.bin')

    def exported_log(self, stream):
        return self.evidence_directory / ('raw-' + _stream(stream) + '.bin')
