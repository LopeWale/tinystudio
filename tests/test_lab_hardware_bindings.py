"""Multiple instrument bindings remain operator authority, never portable grants."""
from copy import deepcopy

import pytest


def registry(grants, kind='discovery'):
    from tinystudio.lab.hardware_bindings import HardwareBindingRegistry
    return HardwareBindingRegistry.from_grants(grants, kind)


def test_registry_selects_multiple_instruments_without_mutating_host_configuration():
    grants = {'bindings': {'one': {'read_only': True, 'methods': ['serial.enumerate']},
                           'two': {'read_only': True, 'methods': ['sunspec.scan'], 'secret': 'private'}}}
    before = deepcopy(grants)
    selected = registry(grants)
    assert selected.select('one')['binding_id'] == 'one'
    assert selected.select('two')['secret'] == 'private'
    assert grants == before
    public = selected.discovery_capabilities()
    assert {row['binding_id'] for row in public} == {'one', 'two'}
    assert 'private' not in repr(public)
    exposed = selected.select('two')
    exposed['secret'] = 'changed'
    assert selected.select('two')['secret'] == 'private'


def test_legacy_single_binding_shape_remains_supported():
    binding = {'binding_id': 'old', 'read_only': True, 'methods': ['serial.enumerate']}
    assert registry({'discovery': binding}).select('old') == binding
    with pytest.raises(ValueError, match='binding'):
        registry({'discovery': binding}).select('another')


@pytest.mark.parametrize('grants', [
    {'discovery': {}, 'bindings': {'one': {}}},
    {'bindings': {'one': {'binding_id': 'different'}}},
    {'bindings': {'': {}}}, {'bindings': []},
])
def test_ambiguous_or_inconsistent_host_binding_maps_are_rejected(grants):
    with pytest.raises(ValueError):
        registry(grants)
