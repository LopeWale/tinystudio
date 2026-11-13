"""Immutable, explicit JSON Pointer and literal selection without coercion."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType

from .contracts import canonical


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _pointer_tokens(pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')) or re.search(r'~(?![01])', pointer):
        raise ValueError('pointer must be an RFC 6901 JSON Pointer with ~0 and ~1 escapes')
    return () if pointer == '' else tuple(token.replace('~1', '/').replace('~0', '~') for token in pointer[1:].split('/'))


@dataclass(frozen=True)
class JsonSelector:
    kind: str
    value: object
    missing: str = 'error'

    def __post_init__(self):
        if self.kind == 'pointer':
            _pointer_tokens(self.value)
        elif self.kind == 'literal':
            try:
                canonical(_plain(self.value))
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValueError('literal must be finite JSON data') from exc
        else:
            raise ValueError('selector requires exactly one pointer or literal')
        if self.missing not in {'error', 'null'} or self.kind == 'literal' and self.missing != 'error':
            raise ValueError('missing policy must be error or null for a pointer selector')
        object.__setattr__(self, 'value', _freeze(_plain(self.value)))

    @classmethod
    def from_dict(cls, value, name='selector', *, allow_missing=False):
        if not isinstance(value, Mapping):
            raise ValueError(f'{name} requires exactly one pointer or literal selector')
        supplied = set(value)
        if supplied not in ({'pointer'}, {'literal'}) and not (
                allow_missing and supplied == {'pointer', 'missing'}):
            raise ValueError(f'{name} requires exactly one pointer or literal selector')
        kind = 'pointer' if 'pointer' in value else 'literal'
        try:
            return cls(kind, _plain(value[kind]), value.get('missing', 'error'))
        except (ValueError, TypeError) as exc:
            raise ValueError(f'{name}: {exc}') from exc

    def to_dict(self):
        return {self.kind: _plain(self.value), **({'missing': self.missing} if self.missing != 'error' else {})}

    def resolve(self, row):
        if self.kind == 'literal':
            return _plain(self.value)
        current = row
        for token in _pointer_tokens(self.value):
            if isinstance(current, dict) and token in current:
                current = current[token]
            elif isinstance(current, list) and re.fullmatch(r'0|[1-9][0-9]*', token):
                index = int(token)
                if index >= len(current):
                    if self.missing == 'null':
                        return None
                    raise ValueError(f'pointer {self.value!r} does not resolve in this source row')
                current = current[index]
            else:
                if self.missing == 'null':
                    return None
                raise ValueError(f'pointer {self.value!r} does not resolve in this source row')
        return current
