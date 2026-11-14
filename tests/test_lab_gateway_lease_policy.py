"""Lease actions are explicit operator configuration, independent of devices."""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from tinystudio.lab.gateway_lease_policy import GatewayLeasePolicy


def test_absent_policy_preserves_existing_cancellation_and_disables_background_work():
    binding = {'endpoint_id': 'owned', 'transport': {'kind': 'custom'}}
    original = deepcopy(binding)
    policy = GatewayLeasePolicy.from_binding(binding)
    assert policy.as_dict() == {'on_release': 'revoke_only', 'on_expiry': 'revoke_only',
        'on_cancel': 'local_stop', 'watchdog_interval_seconds': None}
    assert binding == original and 'lease_policy' not in binding
    assert GatewayLeasePolicy.from_binding({'lease_policy': {}}) == policy


@pytest.mark.parametrize('on_release', ['revoke_only', 'local_stop'])
@pytest.mark.parametrize('on_expiry', ['revoke_only', 'local_stop'])
@pytest.mark.parametrize('on_cancel', ['revoke_only', 'local_stop'])
def test_selected_actions_and_interval_are_retained_without_a_required_stop_payload(on_release, on_expiry, on_cancel):
    selected = {'on_release': on_release, 'on_expiry': on_expiry, 'on_cancel': on_cancel,
                'watchdog_interval_seconds': .125}
    binding = {'local_stop': None, 'lease_policy': selected}
    original = deepcopy(binding)
    policy = GatewayLeasePolicy.from_binding(binding)
    assert policy.as_dict() == selected and binding == original
    selected['on_release'] = 'changed'
    assert policy.on_release == on_release
    result = policy.as_dict()
    result['on_cancel'] = 'changed'
    assert policy.on_cancel == on_cancel
    with pytest.raises(FrozenInstanceError):
        policy.on_expiry = 'changed'


@pytest.mark.parametrize('binding', [None, [], True, 'binding',
    {'lease_policy': None}, {'lease_policy': False}, {'lease_policy': []},
    {'lease_policy': 'revoke_only'}, {'lease_policy': {'unknown': 1}},
    {'lease_policy': {'on_expiry': 'revoke_only', 'watchdog_interval': 1}}])
def test_malformed_policy_is_rejected(binding):
    with pytest.raises(ValueError):
        GatewayLeasePolicy.from_binding(binding)


@pytest.mark.parametrize('field', ['on_release', 'on_expiry', 'on_cancel'])
@pytest.mark.parametrize('action', [None, True, 1, [], 'stop', 'automatic'])
def test_action_fields_accept_only_the_selected_action_names(field, action):
    with pytest.raises(ValueError, match=field):
        GatewayLeasePolicy.from_binding({'lease_policy': {field: action}})
    with pytest.raises(ValueError, match=field):
        GatewayLeasePolicy(**{field: action})


@pytest.mark.parametrize('interval', [0, -1, True, '1', [], float('nan'), float('inf'),
    -float('inf'), 10 ** 1000])
def test_interval_must_be_positive_finite_and_usable_as_a_native_wait(interval):
    with pytest.raises(ValueError, match='interval'):
        GatewayLeasePolicy.from_binding({'lease_policy': {'watchdog_interval_seconds': interval}})
    with pytest.raises(ValueError, match='interval'):
        GatewayLeasePolicy(watchdog_interval_seconds=interval)


@pytest.mark.parametrize('interval', [None, 1e-6, .125, 1, 10 ** 30])
def test_interval_has_no_hidden_operational_minimum_or_maximum(interval):
    policy = GatewayLeasePolicy.from_binding({'lease_policy': {'watchdog_interval_seconds': interval}})
    assert policy.watchdog_interval_seconds == interval
    assert policy.as_dict()['watchdog_interval_seconds'] == interval
