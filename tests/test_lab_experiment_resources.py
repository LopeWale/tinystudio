"""Resource budgets are explicit configuration, not scientific validity rules."""
from copy import deepcopy
import math

import pytest


def resources():
    from tinystudio.lab import experiment_resources
    return experiment_resources


def test_collection_allows_explicit_choices_above_old_fixture_ceilings():
    chosen = resources().CollectionPlan.resolve({'samples': 100_001, 'duration': 7200,
        'timeout': 30, 'max_frame_bytes': 1024 ** 2}, {})
    assert chosen.samples == 100_001 and chosen.duration == 7200
    assert chosen.timeout == 30 and chosen.max_frame_bytes == 1024 ** 2
    assert chosen.operator_limits.max_samples is None


def test_continuous_nonblocking_and_zero_observation_plans_are_explicit():
    continuous = resources().CollectionPlan.resolve({'samples': None, 'duration': None, 'timeout': 0}, {})
    assert continuous.samples is continuous.duration is None
    assert continuous.timeout == 0
    empty = resources().CollectionPlan.resolve({'samples': 0, 'duration': 0, 'interval': 0}, {})
    assert empty.samples == empty.duration == 0


def test_resource_limits_do_not_round_large_integer_counts():
    count = 2 ** 64 + 1
    selected = resources().CollectionPlan.resolve({'samples': count}, {'max_samples': count})
    assert selected.samples == selected.operator_limits.max_samples == count


@pytest.mark.parametrize('requested,limits', [
    ({'samples': 21}, {'max_samples': 20}),
    ({'samples': None}, {'max_samples': 20}),
    ({'duration': 31}, {'max_duration': 30}),
    ({'timeout': 2}, {'max_timeout': 1}),
    ({'max_frame_bytes': 1025}, {'max_frame_bytes': 1024}),
])
def test_authored_plan_cannot_bypass_operator_selected_resources(requested, limits):
    with pytest.raises(ValueError, match='operator'):
        resources().CollectionPlan.resolve(requested, limits)


def test_operational_frame_default_inherits_a_smaller_operator_budget():
    selected = resources().CollectionPlan.resolve({}, {'max_frame_bytes': 1024})
    assert selected.max_frame_bytes == 1024
    assert selected.operator_limits.max_frame_bytes == 1024


def test_resolved_resources_are_immutable_and_disclose_their_basis():
    params, limits = {'samples': 3}, {'max_samples': 10}
    original = deepcopy((params, limits))
    selected = resources().CollectionPlan.resolve(params, limits)
    assert (params, limits) == original
    receipt = selected.to_dict()
    assert receipt['selected']['samples'] == 3
    assert receipt['operator_limits']['max_samples'] == 10
    assert receipt['scientific_validity_rule'] is False
    receipt['operator_limits']['max_samples'] = 1
    assert selected.operator_limits.max_samples == 10


@pytest.mark.parametrize('field,value', [('samples', True), ('samples', -1), ('samples', 1.5),
    ('duration', -1), ('timeout', -1), ('timeout', math.inf), ('duration', math.nan),
    ('interval', -1), ('max_frame_bytes', False)])
def test_invalid_resource_representations_are_rejected(field, value):
    with pytest.raises(ValueError):
        resources().CollectionPlan.resolve({field: value}, {})


def test_optional_data_budgets_have_no_fixture_floor_or_universal_ceiling():
    limits = resources().ResourceLimits.from_dict({'max_rows': 1, 'source_bytes': None,
        'per_source_bytes': 128 * 1024 ** 2, 'pipeline_stages': 100, 'window_records': 1024})
    assert limits.max_rows == 1 and limits.source_bytes is None
    assert limits.per_source_bytes == 128 * 1024 ** 2
