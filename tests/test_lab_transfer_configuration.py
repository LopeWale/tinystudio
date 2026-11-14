"""Operational budgets and provider geometry never crop authored artifacts."""
from dataclasses import FrozenInstanceError

import pytest

from tinystudio.lab.transfer_configuration import (
    TransferBudget, TransferBudgetExceeded, MultipartGeometry, TransferPolicy,
    TransferPaths, TransferApiPaths, MultipartGeometryError,
)


def test_unbounded_budget_accepts_arbitrary_integer_sizes_without_an_implicit_ceiling():
    budget = TransferBudget()
    budget.check_size(10 ** 1000)
    budget.check_count(10 ** 1000)
    budget.check_total(10 ** 1000, 10 ** 1000)
    assert budget.as_dict() == {'max_bytes': None, 'max_files': None}
    with pytest.raises(FrozenInstanceError):
        budget.max_bytes = 1


def test_zero_budget_accepts_only_zero_and_records_explicit_failure_facts():
    budget = TransferBudget(max_bytes=0, max_files=0)
    budget.check_size(0); budget.check_count(0); budget.check_total(0, 0)
    assert budget.as_dict() == {'max_bytes': 0, 'max_files': 0}
    with pytest.raises(TransferBudgetExceeded) as caught:
        budget.check_size(1)
    assert (caught.value.resource, caught.value.maximum, caught.value.actual) == ('bytes', 0, 1)
    with pytest.raises(TransferBudgetExceeded):
        budget.check_count(1)


def test_total_budget_checks_bytes_and_count_without_omitting_a_selected_count_limit():
    budget = TransferBudget(max_bytes=10, max_files=2)
    budget.check_total(10, 2)
    for total, count in [(11, 2), (10, 3)]:
        with pytest.raises(TransferBudgetExceeded):
            budget.check_total(total, count)
    with pytest.raises(ValueError, match='count'):
        budget.check_total(1)


@pytest.mark.parametrize('value', [-1, True, 1.5, float('inf'), '1'])
def test_budget_and_actual_counts_require_nonnegative_exact_integers(value):
    for field in ('max_bytes', 'max_files'):
        with pytest.raises(ValueError):
            TransferBudget(**{field: value})
    budget = TransferBudget()
    for method in (budget.check_size, budget.check_count):
        with pytest.raises(ValueError):
            method(value)


@pytest.mark.parametrize('size,count,last', [(0, 0, None), (1, 1, 1),
    (16 * 1024**2, 1, 16 * 1024**2), (16 * 1024**2 + 1, 2, 1),
    (33 * 1024**2, 3, 1024**2)])
def test_plan_is_compact_and_retains_exact_boundaries_and_last_part(size, count, last):
    geometry = MultipartGeometry()
    plan = geometry.plan(size)
    assert plan.size == size and plan.part_count == count
    assert plan.zero is (size == 0)
    assert plan.one_part is (count == 1)
    assert plan.mode == ('empty_put' if size == 0 else 'multipart')
    assert not isinstance(plan, (list, tuple))
    if count:
        assert plan.part(1)['offset'] == 0
        assert plan.part(count) == {'offset': (count - 1) * geometry.part_bytes, 'size': last}
        segment = plan.part(count)
        segment['size'] = -1
        assert plan.part(count)['size'] == last
    else:
        with pytest.raises(ValueError):
            plan.part(1)
    with pytest.raises(FrozenInstanceError):
        plan.part_count = 1


@pytest.mark.parametrize('number', [0, -1, True, 1.0, '1', 3])
def test_part_number_is_an_explicit_one_based_integer_within_the_plan(number):
    with pytest.raises(ValueError):
        MultipartGeometry().plan(17 * 1024**2).part(number)


def test_selected_geometry_is_never_automatically_enlarged_to_fit_more_parts():
    geometry = MultipartGeometry()
    size = geometry.part_bytes * geometry.maximum_parts + 1
    with pytest.raises(ValueError) as caught:
        geometry.plan(size)
    needed = (size + geometry.maximum_parts - 1) // geometry.maximum_parts
    assert str(needed) in str(caught.value)
    assert geometry.part_bytes == 16 * 1024**2
    assert MultipartGeometry(part_bytes=needed).plan(size).part_count <= geometry.maximum_parts


def test_maximum_part_count_is_a_scalar_plan_instead_of_ten_thousand_allocated_segments():
    geometry = MultipartGeometry()
    plan = geometry.plan(geometry.part_bytes * geometry.maximum_parts)
    assert plan.part_count == 10_000
    assert plan.part(10_000) == {'offset': 9999 * geometry.part_bytes, 'size': geometry.part_bytes}


def test_provider_limits_use_exact_r2_platform_footnotes_rather_than_rounded_guide_values():
    geometry = MultipartGeometry(part_bytes=5_363_466_240)
    assert geometry.minimum_part_bytes == 5_242_880
    assert geometry.maximum_part_bytes == 5_363_466_240
    assert geometry.maximum_object_bytes == 5_492_189_429_760
    maximum = geometry.plan(5_492_189_429_760)
    assert maximum.part_count == 1024
    assert maximum.part(1024)['size'] == 5_363_466_240
    with pytest.raises(MultipartGeometryError, match='object'):
        geometry.plan(5_492_189_429_761)
    with pytest.raises(MultipartGeometryError, match='object'):
        geometry.plan(10 ** 1000)
    with pytest.raises(MultipartGeometryError):
        MultipartGeometry(part_bytes=5 * 1024**3)


@pytest.mark.parametrize('part_bytes', [0, -1, True, 1.0, '5242880', 5_242_879, 5_363_466_241])
def test_selected_part_size_must_be_within_provider_bounds_before_any_object_is_planned(part_bytes):
    with pytest.raises(ValueError):
        MultipartGeometry(part_bytes=part_bytes)


@pytest.mark.parametrize('value', [-1, True, 1.5, float('inf'), '1'])
def test_invalid_object_sizes_are_rejected_before_planning(value):
    with pytest.raises(ValueError):
        MultipartGeometry().plan(value)


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '4'])
def test_concurrency_is_an_explicit_positive_integer(value):
    with pytest.raises(ValueError):
        MultipartGeometry(concurrency=value)


def test_environment_defaults_and_true_none_zero_budgets_are_explicit():
    default = TransferPolicy.from_environment({})
    assert default.per_file.as_dict() == {'max_bytes': None, 'max_files': None}
    assert default.aggregate.as_dict() == {'max_bytes': None, 'max_files': None}
    assert default.geometry.part_bytes == 16 * 1024**2 and default.geometry.concurrency == 4
    assert default.signed_url_expiry_seconds == 900 and default.verifier_timeout_seconds is None
    selected = TransferPolicy.from_environment({'TINYSTUDIO_TRANSFER_MAX_FILE_BYTES': '0',
        'TINYSTUDIO_TRANSFER_MAX_TOTAL_BYTES': 'null', 'TINYSTUDIO_TRANSFER_MAX_FILES': '0',
        'TINYSTUDIO_TRANSFER_VERIFIER_TIMEOUT_SECONDS': '0'})
    assert selected.per_file.max_bytes == 0 and selected.aggregate.max_bytes is None
    assert selected.aggregate.max_files == 0 and selected.verifier_timeout_seconds == 0


@pytest.mark.parametrize('value', ['', 'none', 'None', 'null'])
def test_optional_environment_limits_have_an_explicit_unbounded_spelling(value):
    policy = TransferPolicy.from_environment({'TINYSTUDIO_TRANSFER_MAX_FILE_BYTES': value,
        'TINYSTUDIO_TRANSFER_MAX_TOTAL_BYTES': value, 'TINYSTUDIO_TRANSFER_MAX_FILES': value,
        'TINYSTUDIO_TRANSFER_VERIFIER_TIMEOUT_SECONDS': value})
    assert policy.per_file.max_bytes is policy.aggregate.max_bytes is policy.aggregate.max_files is None
    assert policy.verifier_timeout_seconds is None


@pytest.mark.parametrize('key,value', [('MAX_FILE_BYTES', '-1'), ('MAX_FILES', '1.0'),
    ('MAX_TOTAL_BYTES', True), ('PART_BYTES', '1'), ('CONCURRENCY', '0'),
    ('SIGNED_URL_SECONDS', '0'), ('SIGNED_URL_SECONDS', '604801'),
    ('VERIFIER_TIMEOUT_SECONDS', '-1'), ('VERIFIER_TIMEOUT_SECONDS', 'inf'),
    ('VERIFIER_TIMEOUT_SECONDS', '1e999')])
def test_invalid_environment_configuration_is_not_replaced_with_defaults(key, value):
    with pytest.raises(ValueError):
        TransferPolicy.from_environment({'TINYSTUDIO_TRANSFER_' + key: value})


def test_environment_is_read_only_when_explicitly_requested(monkeypatch):
    monkeypatch.setenv('TINYSTUDIO_TRANSFER_MAX_FILE_BYTES', '123')
    assert TransferPolicy.from_environment({}).per_file.max_bytes is None
    assert TransferPolicy.from_environment().per_file.max_bytes == 123


def test_large_operational_budgets_do_not_change_provider_geometry_and_descriptions_are_defensive():
    policy = TransferPolicy.from_environment({'TINYSTUDIO_TRANSFER_MAX_FILE_BYTES': str(10 ** 1000),
        'TINYSTUDIO_TRANSFER_MAX_TOTAL_BYTES': '0', 'TINYSTUDIO_TRANSFER_PART_BYTES': '5242880',
        'TINYSTUDIO_TRANSFER_CONCURRENCY': '7', 'TINYSTUDIO_TRANSFER_SIGNED_URL_SECONDS': '604800',
        'TINYSTUDIO_TRANSFER_VERIFIER_TIMEOUT_SECONDS': '.125'})
    assert policy.per_file.max_bytes == 10 ** 1000
    assert policy.aggregate.max_bytes == 0
    assert policy.geometry.part_bytes == 5_242_880 and policy.geometry.concurrency == 7
    assert policy.signed_url_expiry_seconds == 604800 and policy.verifier_timeout_seconds == .125
    described = policy.as_dict()
    described['per_file']['max_bytes'] = 1
    described['geometry']['provider']['maximum_object_bytes'] = 1
    assert policy.as_dict()['per_file']['max_bytes'] == 10 ** 1000
    assert policy.geometry.maximum_object_bytes == 5_492_189_429_760
    with pytest.raises(FrozenInstanceError):
        policy.aggregate = TransferBudget()


@pytest.mark.parametrize('environment', [False, [], 'environment'])
def test_environment_reader_requires_an_explicit_mapping(environment):
    with pytest.raises(ValueError):
        TransferPolicy.from_environment(environment)


def test_paths_are_pure_scoped_strings_and_never_accept_traversal():
    paths = TransferPaths('project-one', 'session-one')
    assert paths.prefix == 'lab/project-one/transfers/session-one'
    assert paths.object_key == paths.staging_key == paths.prefix + '/object'
    api = TransferApiPaths()
    assert api.collection == '/api/lab/transfers'
    assert api.session('session-one') == '/api/lab/transfers/session-one'
    assert api.part('session-one', 2) == '/api/lab/transfers/session-one/parts/2'
    assert api.complete('session-one') == '/api/lab/transfers/session-one/complete'
    assert api.abort('session-one') == '/api/lab/transfers/session-one/abort'
    assert TransferApiPaths(base='/transfers').session('one') == '/transfers/one'
    for value in ('', '.', '..', 'a/b', 'a\\b', 'a?x', 'a#x', '\0'):
        with pytest.raises(ValueError):
            TransferPaths(value, 'session-one')
        with pytest.raises(ValueError):
            api.session(value)


def test_scoped_key_limit_counts_utf8_bytes_and_routes_quote_opaque_segments():
    assert len(TransferPaths('p', 'a' * 1001).object_key.encode('utf-8')) == 1024
    assert len(TransferPaths('p', 'é' * 500 + 'a').object_key.encode('utf-8')) == 1024
    with pytest.raises(ValueError, match='key-length'):
        TransferPaths('p', 'a' * 1002)
    assert TransferApiPaths().session('session:é%') == '/api/lab/transfers/session%3A%C3%A9%25'


@pytest.mark.parametrize('base', ['relative', '//external.example', '/x/../y', '/x?query',
    '/x/', '/x\\y', '/x\0y', '/\ud800'])
def test_api_base_is_a_route_path_not_an_origin_or_ambiguous_path(base):
    with pytest.raises(ValueError):
        TransferApiPaths(base=base)
