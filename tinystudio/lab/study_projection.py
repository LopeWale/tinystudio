"""Pure, streaming JSONL projection into explicitly authored study observations.

This module does not associate artifacts, write records, reduce repeated units,
choose independent units, or impose a sample count. Services own those actions.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType

from .contracts import canonical
from .study_contracts import StudyRecordRequest


METHOD_ID = 'jsonl.study_projection'
VERSION = '1.0.0'
PREVIEW_COUNT = 5  # Display only; every selected row contributes to the digest.
NUMBER_ENCODINGS = ('json', 'decimal_strings')


def implementation_sha256():
    digest = hashlib.sha256()
    for name in ('study_projection.py', 'study_contracts.py', 'contracts.py'):
        digest.update(name.encode() + b'\0' + Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def method_descriptor():
    return {'id': METHOD_ID, 'version': VERSION, 'implementation_sha256': implementation_sha256()}


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
class _Selector:
    kind: str
    value: object

    @classmethod
    def from_dict(cls, value, name):
        if not isinstance(value, Mapping) or set(value) not in ({'pointer'}, {'literal'}):
            raise ValueError(f'{name} requires exactly one pointer or literal selector')
        kind = next(iter(value))
        selected = _plain(value[kind])
        if kind == 'pointer':
            _pointer_tokens(selected)
        else:
            try:
                canonical(selected)
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValueError(f'{name} literal must be finite JSON data') from exc
        return cls(kind, _freeze(selected))

    def to_dict(self):
        return {self.kind: _plain(self.value)}

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
                    raise ValueError(f'pointer {self.value!r} does not resolve in this source row')
                current = current[index]
            else:
                raise ValueError(f'pointer {self.value!r} does not resolve in this source row')
        return current


@dataclass(frozen=True)
class ProjectionRow:
    line: int
    raw_line_sha256: str
    decoded_row_sha256: str
    unit_id: str
    condition: str
    kind: str
    observed_at: str | None
    data: object

    def __post_init__(self):
        object.__setattr__(self, 'data', _freeze(self.data))

    def to_dict(self):
        return {**{name: getattr(self, name) for name in ('line', 'raw_line_sha256', 'decoded_row_sha256',
                'unit_id', 'condition', 'kind', 'observed_at')}, 'data': _plain(self.data)}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key {key!r}')
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f'nonfinite JSON constant {value} is not supported')


@dataclass(frozen=True)
class StudyProjection:
    selection: object
    unit_id: _Selector
    condition: _Selector
    data: object
    number_encoding: str
    kind: str = 'observation'
    observed_at: _Selector | None = None

    def __post_init__(self):
        object.__setattr__(self, 'selection', _freeze(self.selection))
        object.__setattr__(self, 'data', _freeze(self.data))

    @classmethod
    def from_dict(cls, mapping):
        required = {'selection', 'unit_id', 'condition', 'data', 'number_encoding'}
        if not isinstance(mapping, Mapping):
            raise ValueError('study projection mapping must be an object')
        for name in sorted(required - set(mapping)):
            raise ValueError(f'study projection requires explicit {name}')
        if set(mapping) - required - {'kind', 'observed_at'}:
            raise ValueError('study projection mapping contains unsupported fields')
        encoding = mapping['number_encoding']
        if not isinstance(encoding, str) or encoding not in NUMBER_ENCODINGS:
            raise ValueError('number_encoding must explicitly select json or decimal_strings')
        supplied = mapping['selection']
        if not isinstance(supplied, Mapping):
            raise ValueError('selection requires all:true or explicit physical lines')
        if set(supplied) == {'all'} and supplied['all'] is True:
            selection = {'all': True}
        elif set(supplied) == {'lines'}:
            lines = supplied['lines']
            if (not isinstance(lines, (list, tuple)) or any(type(line) is not int or line < 1 for line in lines)
                    or len(set(lines)) != len(lines)):
                raise ValueError('selected lines must be unique positive 1-based physical line integers')
            selection = {'lines': sorted(lines)}
        else:
            raise ValueError('selection requires exactly all:true or lines:[...]')
        supplied_data = mapping['data']
        if not isinstance(supplied_data, Mapping) or any(not isinstance(key, str) for key in supplied_data):
            raise ValueError('data must explicitly map string field names to selectors')
        if '_source_projection' in supplied_data:
            raise ValueError('data._source_projection is reserved for server-derived provenance')
        kind = mapping.get('kind', 'observation')
        if not isinstance(kind, str):
            raise ValueError('kind must be a study record string')
        return cls(selection=selection, unit_id=_Selector.from_dict(mapping['unit_id'], 'unit_id'),
            condition=_Selector.from_dict(mapping['condition'], 'condition'),
            data={key: _Selector.from_dict(value, 'data.' + key) for key, value in supplied_data.items()},
            number_encoding=encoding, kind=kind,
            observed_at=_Selector.from_dict(mapping['observed_at'], 'observed_at') if 'observed_at' in mapping else None)

    def to_dict(self):
        return {'selection': _plain(self.selection), 'unit_id': self.unit_id.to_dict(), 'condition': self.condition.to_dict(),
            'data': {key: selector.to_dict() for key, selector in self.data.items()}, 'kind': self.kind,
            'number_encoding': self.number_encoding,
            **({'observed_at': self.observed_at.to_dict()} if self.observed_at is not None else {})}

    def _project(self, row, raw, line):
        unit, condition = self.unit_id.resolve(row), self.condition.resolve(row)
        if not isinstance(unit, str) or not isinstance(condition, str):
            raise ValueError('projected unit_id and condition must be strings; no implicit conversion is performed')
        observed = self.observed_at.resolve(row) if self.observed_at is not None else None
        if observed is not None and not isinstance(observed, str):
            raise ValueError('projected observed_at must be a timezone-aware timestamp string or null')
        data = {key: selector.resolve(row) for key, selector in self.data.items()}
        # Reuse the actual study admission boundary; no scientific sample,
        # condition-vocabulary or independence rule is introduced here.
        validated = StudyRecordRequest.model_validate({'revision': 1, 'idempotency_key': 'projection',
            'kind': self.kind, 'unit_id': unit, 'condition': condition, 'observed_at': observed, 'data': data})
        return ProjectionRow(line, hashlib.sha256(raw).hexdigest(), hashlib.sha256(canonical(row)).hexdigest(),
            validated.unit_id, validated.condition, validated.kind, validated.observed_at, validated.data)

    def _iter_rows(self, path, counts):
        remaining = None if self.selection.get('all') is True else set(self.selection['lines'])
        numeric = {'parse_int': str, 'parse_float': str} if self.number_encoding == 'decimal_strings' else {}
        with Path(path).open('rb') as stream:
            for line, raw in enumerate(stream, 1):
                counts['physical_lines'] = line
                try:
                    text = raw.decode('utf-8')
                    if not text.strip():
                        counts['blank_lines'] += 1
                        if remaining is not None and line in remaining:
                            raise ValueError('selected line is blank and has no JSON object')
                        continue
                    row = json.loads(text, object_pairs_hook=_object, parse_constant=_constant, **numeric)
                    if not isinstance(row, dict):
                        raise ValueError('JSONL source row must be an object')
                    counts['source_rows'] += 1
                    if remaining is not None and line not in remaining:
                        continue
                    projected = self._project(row, raw, line)
                    if remaining is not None:
                        remaining.remove(line)
                except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
                    raise ValueError(f'source line {line}: {exc}') from exc
                yield projected
        if remaining:
            shown = ', '.join(str(line) for line in sorted(remaining)[:PREVIEW_COUNT])
            raise ValueError(f'selected line(s) not present in source: {shown}; missing count={len(remaining)}')

    def iter_rows(self, path):
        """Yield immutable selected observations in physical source-file order.

        Exhaust the iterator to validate all source rows and selected line
        references. No source file or mapping is changed.
        """
        yield from self._iter_rows(path, {'physical_lines': 0, 'source_rows': 0, 'blank_lines': 0})

    def stream_summary(self, path):
        """Hash every selected observation while retaining only five previews."""
        counts = {'physical_lines': 0, 'source_rows': 0, 'blank_lines': 0}
        digest, preview, count = hashlib.sha256(), [], 0
        for row in self._iter_rows(path, counts):
            value = row.to_dict()
            encoded = canonical(value)
            digest.update(len(encoded).to_bytes(8, 'big'))
            digest.update(encoded)
            count += 1
            if len(preview) < PREVIEW_COUNT:
                preview.append(value)
        return {'count': count, 'rows_sha256': digest.hexdigest(), 'preview': preview,
            'number_encoding': self.number_encoding, 'method': method_descriptor(), **counts,
            'disclosure': {
                'numbers': ('JSON float tokens use native binary64 and may round; integer tokens use exact Python integers. Portable integer limits apply to projected study data.'
                            if self.number_encoding == 'json' else
                            'JSON integer and floating-point tokens are retained as their exact lexical strings; JSON strings remain strings.'),
                'literals': 'Literal selector values retain their authored mapping types; portable study validation applies to every projected record.',
                'raw_line_sha256': 'SHA-256 of exact physical line bytes, including any LF or CRLF terminator.',
                'decoded_row_sha256': 'SHA-256 of canonical complete decoded source JSON under the selected numeric parser.',
                'rows_sha256': 'SHA-256 over canonical projected row receipts in source order, each prefixed by its unsigned 8-byte big-endian byte length.',
                'preview': 'First five selected observations for display only; the count and digest cover every selected row.'}}
