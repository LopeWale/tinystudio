"""Planning calculations checked against published/reference values, not learned outputs."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import math

import pytest


def module():
    assert importlib.util.find_spec('tinystudio.lab.statistics') is not None, 'statistics planning engine is missing'
    from tinystudio.lab import statistics
    return statistics


def means(**patch):
    return {'method': 'means.independent', 'solve_for': 'sample_size', 'alpha': .05,
            'power': .8, 'alternative': 'two-sided', 'delta': .5, 'sd': 1,
            'unit': 'V', 'ratio': 1, 'attrition': 0, **patch}


def primary(parameters):
    return module().calculate(parameters)['records'][0]['result']


def test_independent_t_reference_integer_power_and_minimality():
    # Standard two-sided t-test planning: d=.5, alpha=.05, power=.8 -> 63.7656 per group.
    result = primary(means(attrition=.1))
    assert result['analysis_n'] == {'group_1': 64, 'group_2': 64}
    assert result['recruitment_n'] == {'group_1': 72, 'group_2': 72}
    assert result['total_analysis_n'] == 128
    assert result['achieved_power'] >= .8
    fixed = means(solve_for='power', n=63)
    fixed.pop('power')
    previous = primary(fixed)
    assert previous['achieved_power'] < .8


def test_paired_reference_uses_sd_of_differences_and_pair_count():
    parameters = means(method='means.paired')
    parameters.pop('ratio')
    result = primary(parameters)
    assert result['analysis_n'] == {'pairs': 34}
    assert result['effect_size'] == .5
    assert result['achieved_power'] >= .8


def test_proportions_arcsine_reference_and_precision_reference():
    result = primary({'method': 'proportions.independent', 'alpha': .05, 'power': .8,
                      'alternative': 'two-sided', 'p1': .5, 'p2': .6, 'ratio': 1})
    assert result['analysis_n'] == {'group_1': 388, 'group_2': 388}
    precision = primary({'method': 'proportion.precision', 'alpha': .05,
                         'expected_proportion': .5, 'half_width': .05})
    assert precision['analysis_n'] == {'observations': 385}
    assert precision['expected_half_width'] <= .05
    assert precision['achieved_power'] is None


def test_fixed_power_detectable_effect_and_precision_are_recomputable():
    p = means(solve_for='effect_size', n=40)
    p.pop('delta')
    result = primary(p)
    # Independently obtained from statsmodels TTestIndPower.solve_power(nobs1=40, alpha=.05, power=.8).
    assert result['effect_size'] == pytest.approx(.6342985086, abs=1e-7)
    check = {**p, 'solve_for': 'power', 'delta': result['detectable_delta']}
    check.pop('power')
    assert primary(check)['achieved_power'] == pytest.approx(.8, abs=1e-9)
    width = primary({'method': 'proportion.precision', 'solve_for': 'precision',
                     'alpha': .05, 'expected_proportion': .5, 'n': 400})
    assert width['expected_half_width'] == pytest.approx(.0489990996, abs=1e-9)


def test_allocation_sensitivity_sources_and_input_immutability():
    params = means(ratio=1.7, input_sources={'sd': 'pilot revision 3'},
                  sensitivity=[{'delta': .25}, {'power': .9}])
    before = deepcopy(params)
    report = module().calculate(params)
    assert params == before and report['request'] == before
    assert [r['scenario_id'] for r in report['records']] == ['primary', 'sensitivity-1', 'sensitivity-2']
    first, smaller, higher = [r['result'] for r in report['records']]
    assert first['analysis_n']['group_2'] == math.ceil(first['analysis_n']['group_1'] * 1.7)
    assert smaller['total_analysis_n'] > first['total_analysis_n']
    assert higher['total_analysis_n'] > first['total_analysis_n']
    assert report['records'][1]['inputs']['input_sources'] == params['input_sources']
    assert report['method']['assumptions'] and report['method']['references']
    assert report['runtime']['scipy']
    assert len(report['request_sha256']) == len(report['source_code_sha256']) == 64


@pytest.mark.parametrize('patch', [
    {'alpha': True}, {'alpha': 0}, {'alpha': 1}, {'power': math.nan},
    {'sd': 0}, {'sd': None}, {'sd': math.inf}, {'delta': 0},
    {'ratio': 0}, {'attrition': 1}, {'alternative': 'larger'},
    {'alternative': 'less', 'delta': .5}, {'unexpected': 'ignored'},
    {'n': 10}, {'sensitivity': [{'delta': 0}]},
    {'sensitivity': [{'method': 'means.paired'}]}, {'input_sources': {'sd': 12}},
])
def test_invalid_or_unattainable_assumptions_are_rejected(patch):
    with pytest.raises(ValueError):
        module().calculate(means(**patch))


def test_missing_inputs_and_inapplicable_fields_are_not_invented():
    p = means()
    del p['sd']
    with pytest.raises(ValueError, match='sd'):
        module().calculate(p)
    with pytest.raises(ValueError, match='power'):
        module().calculate({'method': 'proportion.precision', 'alpha': .05,
                            'expected_proportion': .5, 'half_width': .05, 'power': .8})
    p = means(solve_for='power', n=3.2)
    p.pop('power')
    with pytest.raises(ValueError, match='integer'):
        module().calculate(p)


def test_directional_effect_round_trip_and_zero_effect_fixed_power():
    p = means(alternative='less', delta=-.5)
    r = primary(p)
    assert r['analysis_n']['group_1'] < 64
    p.update(solve_for='effect_size', n=40)
    del p['delta']
    effect = primary(p)
    assert effect['detectable_delta'] < 0
    p.update(solve_for='power', delta=0)
    p.pop('power')
    assert primary(p)['achieved_power'] == pytest.approx(.05, abs=1e-9)


def test_executor_publishes_complete_reproducible_receipt_and_honors_cancellation(tmp_path):
    stats = module()
    spec = {'definition': {'engine': 'statistics.plan', 'parameters': means(), 'mode': 'offline'}}
    before = deepcopy(spec)
    result = stats.execute_statistics(spec, tmp_path / 'out', lambda _: None, lambda: False)
    artifact = next(a for a in result['artifacts'] if a['role'] == 'statistical_plan')
    content = (tmp_path / 'out' / artifact['name']).read_bytes()
    assert hashlib.sha256(content).hexdigest() == artifact['sha256']
    report = json.loads(content)
    assert report['format'] == 'tinystudio.statistics-plan.v1'
    assert report['qualification']['kind'] == 'planning_estimate'
    assert report['records'][0]['result']['analysis_n']['group_1'] == 64
    assert spec == before
    from tinystudio.lab.engine import ExecutionCancelled
    with pytest.raises(ExecutionCancelled):
        stats.execute_statistics(spec, tmp_path / 'cancelled', lambda _: None, lambda: True)
    assert not (tmp_path / 'cancelled' / 'statistics-plan.json').exists()


def test_registry_is_copy_safe_and_exposes_goal_specific_fields():
    from tinystudio.lab.statistics_selection import method_catalog
    data = method_catalog()
    assert data['format'] == 'tinystudio.statistics-methods.v1'
    independent = next(m for m in data['methods'] if m['id'] == 'means.independent')
    n = next(f for f in independent['fields'] if f['name'] == 'n')
    assert set(n['when']['solve_for']) == {'power', 'effect_size'}
    independent['label'] = 'mutated'
    assert next(m for m in method_catalog()['methods'] if m['id'] == 'means.independent')['label'] != 'mutated'


@pytest.mark.parametrize('paired,ratio,alternative,delta', [
    (False, 1, 'two-sided', .5), (False, 2, 'greater', .3),
    (False, .5, 'less', -.7), (True, None, 'two-sided', .4), (True, None, 'less', -.2),
])
def test_power_agrees_with_independent_statsmodels(paired, ratio, alternative, delta):
    reference = pytest.importorskip('statsmodels.stats.power')
    parameters = means(method='means.paired' if paired else 'means.independent',
                       solve_for='power', n=40, alternative=alternative, delta=delta)
    parameters.pop('power')
    if paired:
        parameters.pop('ratio')
        expected = reference.TTestPower().power(delta, 40, .05,
            alternative={'greater': 'larger', 'less': 'smaller'}.get(alternative, 'two-sided'))
    else:
        parameters['ratio'] = ratio
        expected = reference.TTestIndPower().power(delta, 40, .05, ratio=ratio,
            alternative={'greater': 'larger', 'less': 'smaller'}.get(alternative, 'two-sided'))
    assert primary(parameters)['achieved_power'] == pytest.approx(expected, abs=1e-10)


def test_decimal_allocation_and_attrition_do_not_round_exact_integers_twice():
    p = means(solve_for='power', n=90, ratio=1.1, attrition=.9)
    p.pop('power')
    result = primary(p)
    assert result['analysis_n'] == {'group_1': 90, 'group_2': 99}
    assert result['recruitment_n'] == {'group_1': 900, 'group_2': 990}
    p = means(method='means.paired', solve_for='power', n=2, attrition=.9)
    p.pop('power'); p.pop('ratio')
    assert primary(p)['recruitment_n'] == {'pairs': 20}


@pytest.mark.parametrize('patch', [{'solve_for': []}, {'alternative': {}}, {'alpha': []}])
def test_structured_input_errors_are_actionable_values(patch):
    with pytest.raises(ValueError):
        module().calculate(means(**patch))


def test_extreme_precision_request_reports_numerical_limit_without_division_error():
    with pytest.raises(ValueError, match='represent'):
        module().calculate({'method': 'proportion.precision', 'alpha': .05,
                            'expected_proportion': .5, 'half_width': 1e-200})


def test_sample_size_search_uses_the_full_representable_pair_count_range():
    parameters = means(method='means.paired', delta=3.8e-8)
    parameters.pop('ratio')
    result = primary(parameters)
    assert 2**52 < result['analysis_n']['pairs'] <= module().MAX_EXACT_INTEGER
    assert result['achieved_power'] >= .8


@pytest.mark.parametrize('patch', [{'ratio': 100}, {'attrition': .99}])
def test_unresolvable_sample_size_reports_count_limit_after_feasible_search(patch):
    with pytest.raises(ValueError, match='exact integer representation'):
        primary(means(delta=1e-10, **patch))


def test_cancellation_during_numerical_search_does_not_publish_a_report(tmp_path):
    from tinystudio.lab.engine import ExecutionCancelled
    calls = 0
    events = []
    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 8
    with pytest.raises(ExecutionCancelled):
        module().execute_statistics({'definition': {'engine': 'statistics.plan',
            'parameters': means(delta=1e-8)}}, tmp_path, events.append, cancelled)
    assert calls == 8
    assert events == []
    assert not (tmp_path/'statistics-plan.json').exists()


def test_cancellation_during_artifact_write_does_not_emit_completion(tmp_path, monkeypatch):
    from pathlib import Path
    from tinystudio.lab.engine import ExecutionCancelled
    write = Path.write_bytes
    stopped = False
    events = []
    def cancel_after_write(path, content):
        nonlocal stopped
        result = write(path, content)
        stopped = True
        return result
    monkeypatch.setattr(Path, 'write_bytes', cancel_after_write)
    with pytest.raises(ExecutionCancelled):
        module().execute_statistics({'definition': {'engine': 'statistics.plan',
            'parameters': means()}}, tmp_path, events.append, lambda: stopped)
    assert stopped
    assert events == []


def test_detectable_difference_overflow_is_an_explicit_numerical_error():
    parameters = means(method='means.paired', solve_for='effect_size', n=3, sd=1e308)
    parameters.pop('ratio'); parameters.pop('delta')
    with pytest.raises(ValueError, match='finite numerical representation'):
        primary(parameters)


def test_unresolvable_fixed_precision_cannot_return_an_infinite_interval():
    with pytest.raises(ValueError, match='finite numerical representation'):
        primary({'method': 'proportion.precision', 'solve_for': 'precision',
                 'alpha': 5e-324, 'expected_proportion': .5, 'n': 100})
