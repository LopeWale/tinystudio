"""Host-owned instrument registries and their public capability summaries."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class HardwareBindingRegistry:
    bindings: Mapping

    def __post_init__(self):
        object.__setattr__(self, 'bindings', _freeze(self.bindings))

    @classmethod
    def from_grants(cls, grants, legacy_key):
        if not isinstance(grants, Mapping):
            raise ValueError('execution-host hardware grants must be an object')
        if 'bindings' in grants and legacy_key in grants:
            raise ValueError('select one binding registry or legacy binding shape, not both')
        if 'bindings' in grants:
            entries = grants['bindings']
            if not isinstance(entries, Mapping):
                raise ValueError('hardware bindings must be a map of binding IDs to configurations')
        elif legacy_key in grants:
            binding = grants[legacy_key]
            if not isinstance(binding, Mapping) or not isinstance(binding.get('binding_id'), str):
                raise ValueError('legacy hardware binding requires an explicit binding_id')
            entries = {binding['binding_id']: binding}
        else:
            entries = {}
        normalized = {}
        for identity, binding in entries.items():
            if not isinstance(identity, str) or not identity.strip() or '\0' in identity or not isinstance(binding, Mapping):
                raise ValueError('hardware binding IDs and configurations must be explicit')
            if binding.get('binding_id', identity) != identity:
                raise ValueError('hardware binding identity differs from its registry key')
            normalized[identity] = {**binding, 'binding_id': identity}
        return cls(normalized)

    def select(self, identity):
        if identity not in self.bindings:
            raise ValueError('selected hardware binding is not configured on this execution host')
        return _plain(self.bindings[identity])

    def discovery_capabilities(self):
        """Advertise identifiers and allowed methods, never endpoint secrets."""
        rows = []
        for identity, binding in self.bindings.items():
            methods = binding.get('methods')
            if binding.get('read_only') is not True or not isinstance(methods, tuple) or any(
                    not isinstance(method, str) or not method for method in methods):
                raise ValueError('discovery bindings require read_only=true and explicit methods')
            rows.append({'binding_id': identity, 'read_only': True, 'methods': list(methods)})
        return rows
