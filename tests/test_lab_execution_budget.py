"""Execution resource choices distinguish omitted defaults, None and zero."""
from dataclasses import FrozenInstanceError
from math import inf, nan
import sys

import pytest
from pydantic import ValidationError

from tinystudio.lab.contracts import ExecutionProfile
from tinystudio.lab.execution_configuration import (
    DEFAULT_MAX_OUTPUT_BYTES, DEFAULT_MAX_SECONDS, ExecutionBudget, ExecutionBudgetExceeded,
)


def test_omitted_fields_keep_legacy_defaults_from_one_importable_configuration():
    profile = ExecutionProfile(profile='trusted_native')
    budget = ExecutionBudget.from_execution(profile.model_dump())
    assert DEFAULT_MAX_SECONDS == 300 and DEFAULT_MAX_OUTPUT_BYTES == 512 * 1024 ** 2
    assert budget == ExecutionBudget() == ExecutionBudget.from_execution({})
    assert budget.as_dict() == {'max_seconds': profile.max_seconds, 'max_output_bytes': profile.max_output_bytes}
    assert budget.deadline(10.25) == 310.25


@pytest.mark.parametrize('maximum', [None, 0, 1, 86401, 10 ** 100])
def test_whole_second_duration_choices_round_trip_above_old_ceiling(maximum):
    profile = ExecutionProfile(profile='trusted_native', max_seconds=maximum)
    restored = ExecutionProfile.model_validate_json(profile.model_dump_json())
    budget = ExecutionBudget.from_execution(restored.model_dump())
    assert restored.max_seconds == budget.max_seconds == maximum
    assert ExecutionBudget.from_execution(budget.as_dict()) == budget
    deadline = budget.deadline(2)
    assert deadline == (None if maximum is None else 2.0 + maximum)


@pytest.mark.parametrize('maximum', [None, 0, 1, 10 * 1024 ** 3 + 1, 10 ** 1000])
def test_byte_choices_are_exact_unbounded_integers_without_an_old_transfer_ceiling(maximum):
    profile = ExecutionProfile(profile='trusted_native', max_output_bytes=maximum)
    budget = ExecutionBudget.from_execution(profile.model_dump())
    assert budget.max_output_bytes == maximum
    assert budget.as_dict()['max_output_bytes'] == maximum
    prior = 10 ** 1000 if maximum is None else maximum
    budget.check_output(prior)
    assert budget.remaining_output(prior) == (None if maximum is None else 0)


def test_none_is_no_authored_cap_and_still_validates_observed_bytes_and_clock():
    budget = ExecutionBudget(None, None)
    assert budget.deadline(123.5) is None
    assert budget.check_wall(10 ** 100, None) is None
    assert budget.check_output(10 ** 1000) is None
    assert budget.remaining_output(10 ** 1000) is None
    for actual in (-1, True, 1.0, None):
        with pytest.raises(ValueError): budget.check_output(actual)
        with pytest.raises(ValueError): budget.remaining_output(actual)
    for now in (True, inf, nan, 10 ** 1000):
        with pytest.raises(ValueError): budget.check_wall(now, None)


def test_zero_duration_is_exhausted_at_the_exact_start_and_zero_bytes_is_a_real_budget():
    budget = ExecutionBudget(0, 0)
    deadline = budget.deadline(125.25)
    assert deadline == 125.25
    with pytest.raises(ExecutionBudgetExceeded) as raised:
        budget.check_wall(125.25, deadline)
    assert raised.value.resource == 'wall_time'
    assert raised.value.maximum == deadline and raised.value.actual == 125.25
    budget.check_output(0)
    assert budget.remaining_output(0) == 0
    with pytest.raises(ExecutionBudgetExceeded) as raised:
        budget.check_output(1)
    assert (raised.value.resource, raised.value.maximum, raised.value.actual) == ('output_bytes', 0, 1)


def test_explicit_remaining_bytes_never_crop_an_over_budget_observation():
    budget = ExecutionBudget(max_output_bytes=7)
    assert budget.remaining_output(0) == 7 and budget.remaining_output(3) == 4
    assert budget.remaining_output(7) == 0
    with pytest.raises(ExecutionBudgetExceeded) as raised:
        budget.remaining_output(8)
    assert raised.value.maximum == 7 and raised.value.actual == 8


def test_failure_metadata_does_not_decimal_format_arbitrarily_large_integer_counts():
    maximum = 10 ** 10000
    budget = ExecutionBudget(max_output_bytes=maximum)
    with pytest.raises(ExecutionBudgetExceeded) as raised:
        budget.check_output(maximum + 1)
    assert raised.value.maximum == maximum and raised.value.actual == maximum + 1
    assert 'output' in str(raised.value)


def test_wall_boundary_and_arbitrary_finite_monotonic_clock_origin():
    budget = ExecutionBudget(max_seconds=5)
    assert budget.deadline(-20) == -15.0
    budget.check_wall(-15.001, -15)
    for now in (-15, -14):
        with pytest.raises(ExecutionBudgetExceeded): budget.check_wall(now, -15)


@pytest.mark.parametrize('field', ['max_seconds', 'max_output_bytes'])
@pytest.mark.parametrize('value', [True, False, -1, '0', '300', 1.0, inf, -inf, nan, [], {}])
def test_resource_fields_reject_bool_coercion_and_malformed_values_in_both_contracts(field, value):
    with pytest.raises(ValueError): ExecutionBudget(**{field: value})
    with pytest.raises(ValidationError): ExecutionProfile(profile='trusted_native', **{field: value})


@pytest.mark.parametrize('value', [True, False, '1', None, inf, -inf, nan, 10 ** 1000])
def test_deadline_rejects_invalid_or_unrepresentable_clock_inputs_cleanly(value):
    with pytest.raises(ValueError): ExecutionBudget().deadline(value)
    with pytest.raises(ValueError): ExecutionBudget(None, None).deadline(value)


@pytest.mark.parametrize('now,deadline', [(True, 1), (1, True), (nan, 1), (1, inf),
    (1, '1'), (10 ** 1000, None), (1, 10 ** 1000)])
def test_wall_checks_never_treat_invalid_clock_values_as_unbounded(now, deadline):
    with pytest.raises(ValueError): ExecutionBudget().check_wall(now, deadline)


def test_clock_addition_rejects_overflow_or_a_positive_duration_lost_to_clock_precision():
    with pytest.raises(ValueError): ExecutionBudget(10 ** 1000).deadline(0)
    with pytest.raises(ValueError): ExecutionBudget(int(sys.float_info.max)).deadline(sys.float_info.max)
    with pytest.raises(ValueError): ExecutionBudget(1).deadline(10 ** 100)
    assert ExecutionBudget(10 ** 100).deadline(1) == 1e100


def test_frozen_budget_and_dictionary_results_do_not_mutate_the_authored_selection():
    authored = {'profile': 'trusted_native', 'backend': 'rq', 'max_seconds': None, 'max_output_bytes': 0,
                'requirements': {'cpu': 1}, 'grants': {}}
    budget = ExecutionBudget.from_execution(authored)
    authored['max_output_bytes'] = 100
    public = budget.as_dict()
    public['max_seconds'] = 5
    assert budget.max_seconds is None and budget.max_output_bytes == 0
    with pytest.raises(FrozenInstanceError): budget.max_seconds = 5


@pytest.mark.parametrize('mapping', [None, [], 'execution'])
def test_budget_extraction_requires_an_execution_mapping(mapping):
    with pytest.raises(ValueError): ExecutionBudget.from_execution(mapping)


def test_budget_extraction_leaves_whole_contract_validation_and_future_fields_to_the_contract():
    from types import MappingProxyType
    supplied = MappingProxyType({'max_seconds': None, 'max_output_bytes': 0, 'future_contract_field': {}})
    assert ExecutionBudget.from_execution(supplied) == ExecutionBudget(None, 0)
    with pytest.raises(TypeError): ExecutionBudget(max_second=5)


def test_execution_contract_still_rejects_unknown_fields_and_missing_profile():
    with pytest.raises(ValidationError): ExecutionProfile(max_seconds=None)
    with pytest.raises(ValidationError): ExecutionProfile(profile='trusted_native', unknown=1)
    with pytest.raises(ValidationError): ExecutionProfile(profile='unknown', max_seconds=None)


def test_public_input_schema_exposes_nullable_nonnegative_integer_resources_without_old_maxima():
    schema = ExecutionProfile.model_json_schema()
    for field, default in [('max_seconds', DEFAULT_MAX_SECONDS), ('max_output_bytes', DEFAULT_MAX_OUTPUT_BYTES)]:
        resource = schema['properties'][field]
        assert resource['default'] == default
        assert {'type': 'null'} in resource['anyOf']
        integers = [branch for branch in resource['anyOf'] if branch.get('type') == 'integer']
        assert len(integers) == 1 and integers[0]['minimum'] == 0
        assert 'maximum' not in integers[0]
    assert schema['additionalProperties'] is False
