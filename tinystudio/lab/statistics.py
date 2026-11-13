"""Reproducible sample-size, power, detectable-effect and precision planning.

No model participates in arithmetic. SciPy provides distribution probabilities;
all inputs, numerical choices and approximation assumptions are retained.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from fractions import Fraction
from pathlib import Path
import platform

from .contracts import canonical, checksum
from .engine import ExecutionCancelled, digest
from .statistics_selection import METHODS, normalize_request, validate_statistics_selection


FORMAT = 'tinystudio.statistics-plan.v1'
MAX_EXACT_INTEGER = 2**53 - 1  # JSON/IEEE-754 exact integer boundary, not a scientific sample-count limit.


def _cancel(cancelled):
    if cancelled():
        raise ExecutionCancelled('statistical planning cancelled')


def _counts(p, n):
    if p['method'] == 'means.paired':
        return {'pairs': n}
    if p['method'] == 'proportion.precision':
        return {'observations': n}
    second = n * Fraction(str(p['ratio']))
    if second > MAX_EXACT_INTEGER:
        raise ValueError('allocation exceeds exact integer representation; choose another numerical method')
    return {'group_1': n, 'group_2': math.ceil(second)}


def _power(p, n, effect, cancelled):
    from scipy import stats
    _cancel(cancelled)
    counts = _counts(p, n)
    if 'group_2' in counts and counts['group_2'] < 2:
        return 0.0
    size = n if p['method'] == 'means.paired' else n * counts['group_2'] / (n + counts['group_2'])
    shift = effect * math.sqrt(size)
    if p['method'].startswith('means.'):
        df = n - 1 if p['method'] == 'means.paired' else n + counts['group_2'] - 2
        cutoff = stats.t.isf(p['alpha'] / (2 if p['alternative'] == 'two-sided' else 1), df)
        cdf = lambda x: stats.nct.cdf(x, df, shift)
        sf = lambda x: stats.nct.sf(x, df, shift)
    else:
        cutoff = stats.norm.isf(p['alpha'] / (2 if p['alternative'] == 'two-sided' else 1))
        cdf = lambda x: stats.norm.cdf(x - shift)
        sf = lambda x: stats.norm.sf(x - shift)
    value = (sf(cutoff) + cdf(-cutoff) if p['alternative'] == 'two-sided' else
             sf(cutoff) if p['alternative'] == 'greater' else cdf(-cutoff))
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('distribution calculation could not resolve finite power; choose another numerical method')
    return float(value)


def _sample_size(p, effect, cancelled):
    # Integer search recomputes the actual ceil-rounded group allocation rather
    # than ceil-rounding a continuous solution that may underpower group 2.
    target = p['power']
    # Bracket only feasible allocations, including recruitment and JSON totals.
    # The final doubling step must still search the upper half of that range.
    left, right = 0, MAX_EXACT_INTEGER
    while right - left > 1:
        _cancel(cancelled)
        middle = (left + right) // 2
        try:
            _recruitment(p, _counts(p, middle))
        except ValueError:
            right = middle
        else:
            left = middle
    try:
        _recruitment(p, _counts(p, right))
        limit = right
    except ValueError:
        limit = left
    if limit < 2:
        raise ValueError('sample size cannot be resolved within exact integer representation')
    low, high = 1, 2
    while _power(p, high, effect, cancelled) < target:
        low = high
        if high == limit:
            raise ValueError('sample size cannot be resolved within exact integer representation')
        high = min(high * 2, limit)
    while high - low > 1:
        middle = (low + high) // 2
        if _power(p, middle, effect, cancelled) >= target:
            high = middle
        else:
            low = middle
    return high


def _recruitment(p, counts):
    recruitment = {}
    for key, value in counts.items():
        inflated = value/(1-Fraction(str(p['attrition'])))
        if inflated > MAX_EXACT_INTEGER:
            raise ValueError('attrition-adjusted recruitment exceeds exact integer representation')
        recruitment[key] = math.ceil(inflated)
    if any(sum(values.values()) > MAX_EXACT_INTEGER for values in [counts, recruitment]):
        raise ValueError('total count exceeds exact integer representation')
    return recruitment


def _result(p, cancelled):
    from scipy import optimize, stats
    method, goal = p['method'], p['solve_for']
    effect = detectable = achieved = width = None
    warnings = []
    if method == 'proportion.precision':
        z = float(stats.norm.isf(p['alpha'] / 2))
        variance = p['expected_proportion'] * (1 - p['expected_proportion'])
        scale = z*math.sqrt(variance)/p['half_width'] if goal == 'sample_size' else None
        required = scale*scale if scale is not None else p['n']
        if not math.isfinite(required) or required > MAX_EXACT_INTEGER:
            raise ValueError('precision sample size exceeds exact integer representation')
        n = max(1, math.ceil(required))
        width = z * math.sqrt(variance/n)
        if not math.isfinite(width):
            raise ValueError('interval width exceeds finite numerical representation')
        if min(n*p['expected_proportion'], n*(1-p['expected_proportion'])) < 10:
            warnings.append('Fewer than 10 anticipated successes or failures; the normal interval approximation may be poor.')
    else:
        if method == 'proportions.independent':
            effect = 2*math.asin(math.sqrt(p['p1'])) - 2*math.asin(math.sqrt(p['p2']))
        elif goal != 'effect_size':
            effect = p['delta']/p['sd']
        if goal == 'effect_size':
            n = p['n']
            if _counts(p, n).get('group_2', 2) < 2:
                raise ValueError('allocation must provide at least two analysis observations per group')
            direction = -1 if p['alternative'] == 'less' else 1
            upper = 1.0
            while _power(p, n, direction*upper, cancelled) < p['power']:
                upper *= 2
                if not math.isfinite(upper):
                    raise ValueError('detectable effect cannot be resolved')
            effect = direction * optimize.brentq(
                lambda d: _power(p, n, direction*d, cancelled) - p['power'], 0, upper, xtol=1e-12)
            detectable = effect*p['sd']
            if not math.isfinite(detectable):
                raise ValueError('detectable difference exceeds finite numerical representation')
        else:
            n = _sample_size(p, effect, cancelled) if goal == 'sample_size' else p['n']
        if not math.isfinite(effect):
            raise ValueError('standardized effect must be finite')
        counts = _counts(p, n)
        if 'group_2' in counts and counts['group_2'] < 2:
            raise ValueError('allocation must provide at least two analysis observations per group')
        achieved = _power(p, n, effect, cancelled)
        if method == 'proportions.independent' and any(
                min(counts[group]*p[prop], counts[group]*(1-p[prop])) < 10
                for group, prop in [('group_1', 'p1'), ('group_2', 'p2')]):
            warnings.append('Fewer than 10 anticipated successes or failures in a group; normal/arcsine power may be poor.')
    counts = _counts(p, n)
    recruitment = _recruitment(p, counts)
    return {'analysis_n': counts, 'recruitment_n': recruitment,
            'total_analysis_n': sum(counts.values()), 'total_recruitment_n': sum(recruitment.values()),
            'achieved_power': achieved, 'target_power': p.get('power'), 'effect_size': effect,
            'detectable_delta': detectable, 'expected_half_width': width, 'warnings': warnings,
            'rounding': 'Smallest integer complete-pair count meeting target power; recruited pairs ceil(n/(1-attrition)).'
                        if goal == 'sample_size' and method == 'means.paired' else
                        'Smallest integer group-1 size meeting target power with ceil-rounded group 2; recruitment ceil(n/(1-attrition)).'
                        if goal == 'sample_size' and method != 'proportion.precision' else
                        'Normal-interval sample size rounded upward; recruitment ceil(n/(1-attrition)).'
                        if goal == 'sample_size' else
                        'Fixed integer analysis count; group 2 and attrition-adjusted recruitment rounded upward.'}


def calculate(parameters, cancelled=lambda: False):
    scenarios = normalize_request(parameters)
    _cancel(cancelled)
    # Import only after selection validation so servers may expose metadata and
    # preflight on hosts without the optional calculation dependency.
    try:
        import scipy
    except ImportError as exc:
        raise ValueError('SciPy is required on the calculation target; install tinystudio[statistics]') from exc
    records = []
    for identity, p in scenarios:
        _cancel(cancelled)
        records.append({'scenario_id': identity, 'inputs': p, 'result': _result(p, cancelled)})
    _cancel(cancelled)
    code = b''.join(Path(__file__).with_name(name).read_bytes()
                    for name in ('statistics_selection.py', 'statistics.py'))
    return {'format': FORMAT, 'method': deepcopy(METHODS[scenarios[0][1]['method']]),
            'request': deepcopy(parameters), 'request_sha256': checksum(parameters),
            'source_code_sha256': hashlib.sha256(code).hexdigest(),
            'runtime': {'python': platform.python_version(), 'scipy': scipy.__version__},
            'records': records, 'qualification': {'kind': 'planning_estimate',
                'scope': 'Declared assumptions and implemented distribution approximation only; not observed outcomes or a guarantee.',
                'input_sources': 'Researcher-authored notes; not independently verified pilot estimates.'}}


def execute_statistics(spec, output_dir, emit, cancelled):
    definition = spec['definition']
    validate_statistics_selection(definition)
    _cancel(cancelled)
    report = calculate(definition.get('parameters', {}), cancelled)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError('statistics output directory must be empty')
    _cancel(cancelled)
    path = output_dir/'statistics-plan.json'
    path.write_bytes(canonical(report))
    _cancel(cancelled)
    emit({'event': 'statistics_complete', 'method': report['method']['id'], 'scenarios': len(report['records'])})
    return {'records': report['records'], 'metrics': {'scenarios': len(report['records']),
                'primary': report['records'][0]['result']},
            'artifacts': [{'name': path.name, 'path': str(path), 'sha256': digest(path),
                'media_type': 'application/json', 'role': 'statistical_plan', 'visibility': 'evaluation'}],
            'qualification': {'kind': 'planning_estimate', 'execution': 'completed', 'physical_validation': False}}
