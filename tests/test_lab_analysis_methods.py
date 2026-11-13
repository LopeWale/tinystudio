"""Observed means inference checked against hand and independent references."""
import builtins
import importlib.util
import json
import math
import subprocess
import sys

import pytest

from tinystudio.lab.engine import ExecutionCancelled


def module():
    assert importlib.util.find_spec('tinystudio.lab.analysis_methods') is not None, 'observed inference methods are missing'
    from tinystudio.lab import analysis_methods
    return analysis_methods


def calculate(a, b, *, method='means.welch', confidence=.95, alternative='two-sided', **kwargs):
    return module().calculate(method, a, b, confidence=confidence, alternative=alternative, **kwargs)


def codes(result):
    return {row['code'] for row in result['diagnostics']}


def test_catalog_exposes_qualified_methods_without_importing_scipy():
    # This fails if a metadata import starts depending on the calculation extra.
    script = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'scipy' or name.startswith('scipy.'):
        raise ImportError('SciPy deliberately absent')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from tinystudio.lab.analysis_methods import method_catalog
import json
print(json.dumps(method_catalog(), allow_nan=False))
'''
    assert module()
    completed = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True)
    catalog = json.loads(completed.stdout)
    assert {row['id'] for row in catalog} == {'means.welch', 'means.paired'}
    assert all(row['version'] and row['label'] and row['description'] and row['assumptions'] and row['references'] for row in catalog)
    catalog[0]['assumptions'].clear()
    assert module().method_catalog()[0]['assumptions']


def test_welch_hand_fixture_preserves_direction_and_unpooled_uncertainty():
    # A: mean=2, s^2=1, n=3. B is constant zero. SE=sqrt(1/3), df=2.
    result = calculate([1, 2, 3], [0, 0, 0])
    assert result['estimate'] == 2
    assert result['counts'] == {'group_a': 3, 'group_b': 3}
    assert result['standard_error'] == pytest.approx(.5773502691896257)
    assert result['degrees_of_freedom'] == pytest.approx(2)
    assert result['interval']['lower'] == pytest.approx(-.4841377117195456)
    assert result['interval']['upper'] == pytest.approx(4.484137711719546)
    assert 'zero_variance' in codes(result)
    assert result['method']['id'] == 'means.welch' and result['method']['version']
    assert result['runtime']['python'] and result['runtime']['scipy']
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('alternative,reference_alternative', [
    ('two-sided', 'two-sided'), ('less', 'smaller'), ('greater', 'larger'),
])
def test_welch_unequal_sizes_and_variances_agree_with_statsmodels(alternative, reference_alternative):
    reference = pytest.importorskip('statsmodels.stats.weightstats')
    a, b = [1, 2, 4], [3, 4, 9, 15]
    expected = reference.CompareMeans(reference.DescrStatsW(a), reference.DescrStatsW(b))
    result = calculate(a, b, alternative=alternative, confidence=.9)
    lower, upper = expected.tconfint_diff(alpha=.1, alternative=reference_alternative, usevar='unequal')
    assert result['estimate'] == pytest.approx(-5.416666666666667)
    assert result['standard_error'] == pytest.approx(expected.std_meandiff_separatevar)
    assert result['degrees_of_freedom'] == pytest.approx(expected.dof_satt())
    interval = result['interval']
    assert interval['confidence'] == .9
    assert interval['lower_unbounded'] is (alternative == 'less')
    assert interval['upper_unbounded'] is (alternative == 'greater')
    assert interval['lower'] == (None if math.isinf(lower) else pytest.approx(lower))
    assert interval['upper'] == (None if math.isinf(upper) else pytest.approx(upper))
    json.dumps(result, allow_nan=False)


def test_paired_hand_fixture_uses_within_pair_differences():
    # Differences [2,3,3,4] have mean 3, s^2=2/3, SE=sqrt(1/6), df=3.
    result = calculate([3, 5, 9, 7], [1, 2, 6, 3], method='means.paired')
    assert result['counts'] == {'group_a': 4, 'group_b': 4, 'complete_pairs': 4}
    assert result['estimate'] == 3
    assert result['standard_error'] == pytest.approx(.408248290463863)
    assert result['degrees_of_freedom'] == 3
    assert result['interval']['lower'] == pytest.approx(1.700771736374889)
    assert result['interval']['upper'] == pytest.approx(4.2992282636251105)


@pytest.mark.parametrize('alternative,reference_alternative', [
    ('two-sided', 'two-sided'), ('less', 'smaller'), ('greater', 'larger'),
])
def test_paired_intervals_agree_with_statsmodels(alternative, reference_alternative):
    reference = pytest.importorskip('statsmodels.stats.weightstats')
    # The independently written difference fixture preserves the declared pairing.
    expected = reference.DescrStatsW([2, 3, 3, 4])
    result = calculate([3, 5, 9, 7], [1, 2, 6, 3], method='means.paired', confidence=.8, alternative=alternative)
    lower, upper = expected.tconfint_mean(alpha=.2, alternative=reference_alternative)
    assert result['interval']['lower'] == (None if math.isinf(lower) else pytest.approx(lower))
    assert result['interval']['upper'] == (None if math.isinf(upper) else pytest.approx(upper))


@pytest.mark.parametrize('method,a,b,estimate,counts', [
    ('means.welch', [], [], None, {'group_a': 0, 'group_b': 0}),
    ('means.welch', [], [4, 5], None, {'group_a': 0, 'group_b': 2}),
    ('means.welch', [4], [1, 3], 2, {'group_a': 1, 'group_b': 2}),
    ('means.paired', [], [], None, {'group_a': 0, 'group_b': 0, 'complete_pairs': 0}),
    ('means.paired', [4], [1], 3, {'group_a': 1, 'group_b': 1, 'complete_pairs': 1}),
])
def test_insufficient_observations_return_defined_estimate_without_admission_rules(method, a, b, estimate, counts):
    result = calculate(a, b, method=method)
    assert result['estimate'] == estimate and result['counts'] == counts
    assert result['standard_error'] is None and result['degrees_of_freedom'] is None
    assert result['interval']['lower'] is None and result['interval']['upper'] is None
    assert not result['interval']['lower_unbounded'] and not result['interval']['upper_unbounded']
    assert 'insufficient_observations' in codes(result)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('method,a,b,estimate,df', [
    ('means.welch', [3, 3], [1, 1, 1], 2, None),
    ('means.paired', [3, 4, 5], [1, 2, 3], 2, 2),
])
def test_zero_variance_has_explicit_diagnostics_and_no_unsupported_interval(method, a, b, estimate, df):
    result = calculate(a, b, method=method)
    assert result['estimate'] == estimate and result['standard_error'] == 0
    assert result['degrees_of_freedom'] == df
    assert result['interval']['lower'] is None and result['interval']['upper'] is None
    assert 'zero_variance' in codes(result)
    assert all(row['message'] for row in result['diagnostics'])


@pytest.mark.parametrize('confidence', [True, False, None, '0.95', math.nan, math.inf, -math.inf, 0, 1, -1])
def test_confidence_requires_an_explicit_finite_probability(confidence):
    with pytest.raises(ValueError, match='confidence'):
        calculate([1, 3], [2, 4], confidence=confidence)


def test_confidence_and_alternative_have_no_silent_defaults_in_public_api():
    with pytest.raises(TypeError):
        module().calculate('means.welch', [1, 3], [2, 4])


@pytest.mark.parametrize('value', [None, math.nan, math.inf, -math.inf, True, '1.5'])
@pytest.mark.parametrize('group', ['a', 'b'])
def test_missing_nonfinite_and_unconverted_values_are_not_silently_omitted(value, group):
    a, b = [1, 2, 3], [4, 5, 6]
    (a if group == 'a' else b)[1] = value
    with pytest.raises(ValueError, match=f'group_{group}'):
        calculate(a, b)


@pytest.mark.parametrize('method', ['unknown', None, [], {}])
def test_unknown_method_is_rejected(method):
    with pytest.raises(ValueError, match='method'):
        calculate([1, 2], [3, 4], method=method)


@pytest.mark.parametrize('alternative', ['larger', None, [], {}])
def test_unknown_alternative_is_rejected(alternative):
    with pytest.raises(ValueError, match='alternative'):
        calculate([1, 2], [3, 4], alternative=alternative)


def test_paired_inputs_must_have_equal_lengths():
    with pytest.raises(ValueError, match='equal|length'):
        calculate([1, 2, 3], [1, 2], method='means.paired')


def test_scipy_is_required_only_for_calculation(monkeypatch):
    analysis = module()
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == 'scipy' or name.startswith('scipy.'):
            raise ImportError('SciPy deliberately absent')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)
    assert analysis.method_catalog()
    with pytest.raises(ValueError, match=r'SciPy.*statistics'):
        calculate([1, 2, 3], [4, 5, 6])


def test_cancelled_calculation_uses_engine_cancellation():
    with pytest.raises(ExecutionCancelled):
        calculate([1, 2, 3], [4, 5, 6], cancelled=lambda: True)


def test_cancellation_remains_responsive_during_large_input_consumption():
    consumed = 0

    def observations():
        nonlocal consumed
        while consumed < 1_000_000:
            consumed += 1
            yield float(consumed)

    with pytest.raises(ExecutionCancelled):
        calculate(observations(), [1, 2, 3], cancelled=lambda: consumed >= 3000)
    assert consumed <= 4096


def test_no_sample_cap_or_recommendation_gate_is_applied():
    result = calculate((float(i % 2) for i in range(100_001)), [1, 2, 3])
    assert result['counts']['group_a'] == 100_001
    assert result['interval']['lower'] is not None


def test_finite_extreme_values_do_not_overflow_defined_means_and_standard_errors():
    result = calculate([-1e308, 1e308], [-1e308, 1e308])
    assert result['estimate'] == 0
    assert result['standard_error'] == pytest.approx(1.4142135623730951e308)
    assert result['degrees_of_freedom'] == pytest.approx(2)
    # The requested two-sided interval cannot be represented as finite JSON floats.
    assert result['interval']['lower'] is None and result['interval']['upper'] is None
    assert 'numerical_resolution' in codes(result)
    json.dumps(result, allow_nan=False)


def test_extreme_paired_differences_do_not_publish_infinities():
    result = calculate([1e308, 1e308], [-1e308, -1e308], method='means.paired')
    assert result['estimate'] is None
    assert 'numerical_resolution' in codes(result)
    json.dumps(result, allow_nan=False)


def test_mean_retains_small_remainder_after_cancelling_large_observations():
    result = calculate([1e308, -1e308, 1e-300], [0, 0, 0])
    assert result['estimate'] == pytest.approx(3.3333333333333334e-301, rel=1e-14, abs=0)


def test_finite_mean_survives_overflowing_intermediate_sum():
    result = calculate([1e308, 1e308, -1e308], [0, 0, 0])
    assert result['estimate'] == pytest.approx(3.333333333333333e307)


def test_unresolvable_positive_two_sided_confidence_has_a_diagnostic():
    result = calculate([1, 3], [0, 2], confidence=1e-300)
    assert result['estimate'] == 1
    assert result['interval']['lower'] is None and result['interval']['upper'] is None
    assert 'numerical_resolution' in codes(result)


def test_underflowing_interval_width_is_not_reported_as_zero_uncertainty():
    result = calculate([-5e-324, 5e-324], [-5e-324, 5e-324], confidence=.01)
    assert result['standard_error'] > 0
    assert result['interval']['lower'] is None and result['interval']['upper'] is None
    assert 'numerical_resolution' in codes(result)
