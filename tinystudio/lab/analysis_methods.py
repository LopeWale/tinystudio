"""Observed-data mean contrasts with explicit, versioned inference methods.

The caller selects observations, units and complete pairs. These kernels never
drop values, choose a missing-data policy, or impose study admission criteria.
SciPy is optional until a calculation is requested.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from copy import deepcopy
import math
from numbers import Real
import platform
from statistics import mean as exact_mean

from .engine import ExecutionCancelled


METHOD_VERSION = '1.0.0'
ALTERNATIVES = ('two-sided', 'less', 'greater')
METHODS = {
    'means.welch': {
        'id': 'means.welch', 'version': METHOD_VERSION,
        'label': 'Welch independent means',
        'description': 'Mean of group A minus mean of group B, with an unequal-variance t confidence interval.',
        'assumptions': [
            'The selected units are independent within each group and between groups; no clustering or serial-dependence adjustment.',
            'Outcomes are measured on a scale on which means and their difference are meaningful.',
            'Welch–Satterthwaite t inference is an approximation; normal populations support its small-sample use, while large-sample justification requires finite variances and suitable sampling conditions.',
            'Population variances may differ. Selection, assignment and missingness determine which population and causal interpretations are justified; the calculation does not establish them.',
        ],
        'references': [
            'https://doi.org/10.1093/biomet/34.1-2.28',
            'https://www.itl.nist.gov/div898/handbook/eda/section3/eda353.htm',
            'https://www.statsmodels.org/stable/generated/statsmodels.stats.weightstats.CompareMeans.tconfint_diff.html',
        ],
        'equations': [
            'estimate = mean(A) - mean(B); SE = sqrt(s_A^2/n_A + s_B^2/n_B), using sample variances with n - 1 denominators.',
            'df = (s_A^2/n_A + s_B^2/n_B)^2 / ((s_A^2/n_A)^2/(n_A-1) + (s_B^2/n_B)^2/(n_B-1)).',
        ],
        'dependencies': ['scipy'],
    },
    'means.paired': {
        'id': 'means.paired', 'version': METHOD_VERSION,
        'label': 'Paired means',
        'description': 'Mean of aligned within-pair differences A minus B, with a one-sample t confidence interval.',
        'assumptions': [
            'Each aligned A and B value belongs to the same complete pair, and different pairs are independent.',
            'Differences are measured on a scale on which their mean is meaningful.',
            'Student t inference is exact for independent normally distributed differences with positive variance; approximate large-sample inference requires finite variance and suitable sampling conditions.',
            'The interval describes the selected complete pairs. Selection, assignment and missingness determine population and causal interpretations; the calculation does not establish them.',
        ],
        'references': [
            'https://www.itl.nist.gov/div898/handbook/eda/section3/eda353.htm',
            'https://www.statsmodels.org/stable/generated/statsmodels.stats.weightstats.DescrStatsW.tconfint_mean.html',
        ],
        'equations': [
            'd_i = A_i - B_i; estimate = mean(d); SE = s_d/sqrt(n), using sample variance with n - 1 denominator; df = n - 1.',
        ],
        'dependencies': ['scipy'],
    },
}


def method_catalog() -> list[dict]:
    """Return independent copies of metadata without a SciPy import."""
    return deepcopy(list(METHODS.values()))


def _cancel(cancelled):
    if cancelled():
        raise ExecutionCancelled('observed-data inference cancelled')


def _checked(values, cancelled):
    # Every linear pass, including fsum's iterator, remains interruptible.
    for index, value in enumerate(values):
        if index % 1024 == 0:
            _cancel(cancelled)
        yield value
    _cancel(cancelled)


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f'{name} must be a finite numeric value, not missing or unconverted data')
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f'{name} must be finite') from exc
    if not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    return value


def _observations(values, name, cancelled):
    if isinstance(values, (str, bytes, dict)):
        raise ValueError(f'{name} must be an iterable of finite numeric observations')
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError(f'{name} must be an iterable of finite numeric observations') from exc
    return [_number(value, f'{name}[{index}]')
            for index, value in enumerate(_checked(iterator, cancelled))]


def _mean(values, cancelled):
    try:
        return math.fsum(_checked(values, cancelled)) / len(values)
    except OverflowError:
        # statistics.mean sums float integer ratios exactly before dividing.
        # This uncommon path handles overflowing partial sums with a finite
        # mean without discarding tiny cancellation remainders by scaling.
        return exact_mean(_checked(values, cancelled))


def _moments(values, cancelled):
    """Return mean, mean's SE and observed constancy without squaring raw units.

    Centering before scaling preserves small spreads around large offsets.
    A midpoint of the extrema keeps each centered difference representable,
    even when the range or unscaled sum of squares would overflow.
    """
    n = len(values)
    if not n:
        return None, None, False
    low = high = values[0]
    for value in _checked(values, cancelled):
        low, high = min(low, value), max(high, value)
    if low == high:
        return low, 0.0 if n > 1 else None, True
    center = low / 2 + high / 2
    scale = max(abs(low - center), abs(high - center))
    normalized = [(value - center) / scale for value in _checked(values, cancelled)]
    mean_normalized = math.fsum(_checked(normalized, cancelled)) / n
    mean = _mean(values, cancelled)
    if n == 1:
        return mean, None, False
    sum_squares = math.fsum((value - mean_normalized) ** 2
                            for value in _checked(normalized, cancelled))
    se = scale * math.sqrt(sum_squares / (n - 1) / n)
    return mean, se, False


def _diagnostic(result, code, message):
    result['diagnostics'].append({'code': code, 'message': message})


def _finite_output(value, result, name):
    if value is None or math.isfinite(value):
        return value
    _diagnostic(result, 'numerical_resolution', f'{name} exceeds finite numerical representation; it is undefined in this result.')
    return None


def _paired(a, b, result, cancelled):
    differences = [left - right for left, right in _checked(zip(a, b), cancelled)]
    multiplier = 1
    if any(not math.isfinite(value) for value in _checked(differences, cancelled)):
        # Half differences are representable for every finite input pair.
        # Rescale results only after averaging, so opposing huge differences
        # can still yield a defined mean and standard error.
        differences = [left / 2 - right / 2 for left, right in _checked(zip(a, b), cancelled)]
        multiplier = 2
    mean, se, constant = _moments(differences, cancelled)
    result['estimate'] = _finite_output(None if mean is None else mean * multiplier, result, 'Paired mean difference')
    if len(a) < 2:
        _diagnostic(result, 'insufficient_observations', 'A paired t interval needs at least two complete pairs to estimate the variance of differences; the study and any defined point estimate are retained.')
        return
    result['degrees_of_freedom'] = len(a) - 1
    result['standard_error'] = _finite_output(se * multiplier, result, 'Paired standard error')
    if constant:
        _diagnostic(result, 'zero_variance', 'All observed paired differences are equal. The estimated standard error is zero; a Student t interval is undefined because studentization requires positive variance.')
    elif result['standard_error'] == 0:
        _diagnostic(result, 'numerical_resolution', 'The nonconstant paired differences have an unresolvable standard error at this floating-point scale; no interval is reported.')


def _welch(a, b, result, cancelled):
    mean_a, se_a, constant_a = _moments(a, cancelled)
    mean_b, se_b, constant_b = _moments(b, cancelled)
    if mean_a is not None and mean_b is not None:
        result['estimate'] = _finite_output(mean_a - mean_b, result, 'Independent mean difference')
    if len(a) < 2 or len(b) < 2:
        _diagnostic(result, 'insufficient_observations', 'Welch inference needs at least two observations in each group to estimate both sample variances; the study and any defined point estimate are retained.')
        return
    result['standard_error'] = _finite_output(math.hypot(se_a, se_b), result, 'Welch standard error')
    if constant_a or constant_b:
        groups = 'both groups' if constant_a and constant_b else 'group A' if constant_a else 'group B'
        _diagnostic(result, 'zero_variance', f'Zero sample variance was observed in {groups}. '
                    + ('Both variance contributions are zero, so Welch–Satterthwaite degrees of freedom and its t interval are undefined.'
                       if constant_a and constant_b else 'The Welch calculation uses the nonzero variance contribution; assess whether its sampling assumptions are appropriate.'))
    scale = max(se_a, se_b)
    if scale == 0:
        if not (constant_a and constant_b):
            _diagnostic(result, 'numerical_resolution', 'Nonconstant observations have unresolvable standard errors at this floating-point scale; no degrees of freedom or interval are reported.')
        return
    # Scale the squared SE terms before applying Satterthwaite's equation.
    # This avoids overflow and underflow from s^2/n or their fourth powers.
    weight_a, weight_b = (se_a / scale) ** 2, (se_b / scale) ** 2
    df = (weight_a + weight_b) ** 2 / (weight_a ** 2 / (len(a) - 1) + weight_b ** 2 / (len(b) - 1))
    result['degrees_of_freedom'] = _finite_output(df, result, 'Welch degrees of freedom')


def _interval(result, confidence, alternative, stats, cancelled):
    estimate, se, df = (result[key] for key in ('estimate', 'standard_error', 'degrees_of_freedom'))
    if estimate is None or se is None or not se > 0 or df is None:
        return
    _cancel(cancelled)
    tail_probability = (1 - confidence) / (2 if alternative == 'two-sided' else 1)
    if alternative == 'two-sided' and tail_probability == .5:
        _diagnostic(result, 'numerical_resolution', 'The positive confidence is too small to resolve the two-sided t quantile at floating-point precision; no interval is reported.')
        return
    critical = float(stats.t.isf(tail_probability, df))
    _cancel(cancelled)
    if not math.isfinite(critical):
        _diagnostic(result, 'numerical_resolution', 'The requested t quantile cannot be resolved as finite at this confidence and degrees of freedom; no interval is reported.')
        return
    margin = critical * se
    if margin == 0 and critical != 0:
        _diagnostic(result, 'numerical_resolution', 'The nonzero interval margin underflows floating-point representation; no interval is reported.')
        return
    interval = result['interval']
    if alternative != 'less':
        interval['lower'] = _finite_output(estimate - margin, result, 'Interval lower bound')
    else:
        interval['lower_unbounded'] = True
    if alternative != 'greater':
        interval['upper'] = _finite_output(estimate + margin, result, 'Interval upper bound')
    else:
        interval['upper_unbounded'] = True


def calculate(method: str, group_a: Iterable[float], group_b: Iterable[float], *,
              confidence: float, alternative: str,
              cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Calculate the declared A-minus-B contrast from finite selected values.

    Invalid values or unequal paired lengths raise ValueError. Insufficient
    observations and degenerate data retain counts and defined estimates with
    diagnostics. A None bound with its unbounded flag false is undefined; a
    true unbounded flag denotes the mathematical infinite tail of a valid
    one-sided interval. No NaN or infinity is emitted.
    """
    _cancel(cancelled)
    if not isinstance(method, str) or method not in METHODS:
        raise ValueError('observed-data method is not implemented; inspect the method catalog')
    confidence = _number(confidence, 'confidence')
    if not 0 < confidence < 1:
        raise ValueError('confidence must be in (0, 1)')
    if not isinstance(alternative, str) or alternative not in ALTERNATIVES:
        raise ValueError('alternative must be two-sided, less, or greater')
    a = _observations(group_a, 'group_a', cancelled)
    b = _observations(group_b, 'group_b', cancelled)
    if method == 'means.paired' and len(a) != len(b):
        raise ValueError('paired groups must have equal lengths and already aligned complete pairs')
    try:
        import scipy
        from scipy import stats
    except ImportError as exc:
        raise ValueError('SciPy is required on the calculation target; install tinystudio[statistics]') from exc
    _cancel(cancelled)
    result = {
        'method': {'id': method, 'version': METHODS[method]['version']},
        'estimate': None, 'counts': {'group_a': len(a), 'group_b': len(b)},
        'standard_error': None, 'degrees_of_freedom': None,
        'interval': {'confidence': confidence, 'lower': None, 'upper': None,
                     'lower_unbounded': False, 'upper_unbounded': False},
        'diagnostics': [],
        'runtime': {'python': platform.python_version(), 'scipy': scipy.__version__},
    }
    if method == 'means.paired':
        result['counts']['complete_pairs'] = len(a)
        _paired(a, b, result, cancelled)
    else:
        _welch(a, b, result, cancelled)
    _interval(result, confidence, alternative, stats, cancelled)
    _cancel(cancelled)
    return result
