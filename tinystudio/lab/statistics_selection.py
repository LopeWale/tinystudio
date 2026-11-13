"""Versioned planning methods and dependency-free, goal-specific admission.

This registry describes implemented calculations, not the studies researchers are
allowed to author. New method implementations supply their own explicit fields,
assumptions and numerical kernel. No observation or variance is invented here.
"""
from __future__ import annotations

from copy import deepcopy

from .signals import number


def _field(name, label, *, type='number', goals=None, choices=None, description=None):
    field = {'name': name, 'label': label, 'type': type, 'required': name != 'attrition'}
    if goals:
        field['when'] = {'solve_for': goals}
    if choices:
        field['choices'] = [{'id': key, 'name': value} for key, value in choices]
    if description:
        field['description'] = description
    return field


def _method(identity, label, description, fields, assumptions, references):
    return {'id': identity, 'label': label, 'version': '1', 'description': description,
            'fields': fields, 'assumptions': assumptions, 'references': references}


def _power_fields(paired=False, proportions=False):
    goals = ['sample_size', 'power'] + ([] if proportions else ['effect_size'])
    labels = {'sample_size': 'Required sample size', 'power': 'Power at a fixed sample size',
              'effect_size': 'Minimum detectable effect'}
    fields = [
        _field('solve_for', 'Calculate', type='choice', choices=[(g, labels[g]) for g in goals]),
        _field('alpha', 'Significance level (alpha)'),
        _field('power', 'Target power', goals=[g for g in goals if g != 'power']),
        _field('alternative', 'Alternative hypothesis', type='choice', choices=[
            ('two-sided', 'Two-sided'), ('greater', 'Positive difference'), ('less', 'Negative difference')]),
        _field('n', 'Complete pairs' if paired else 'Group 1 analysis observations',
               goals=[g for g in goals if g != 'sample_size']),
    ]
    if proportions:
        fields += [_field('p1', 'Anticipated proportion in group 1'), _field('p2', 'Anticipated proportion in group 2')]
    else:
        fields += [_field('delta', 'Difference to detect', goals=['sample_size', 'power']),
                   _field('sd', 'Standard deviation of paired differences' if paired else 'Common standard deviation'),
                   _field('unit', 'Measurement unit', type='string')]
    if not paired:
        fields += [_field('ratio', 'Allocation ratio (group 2 / group 1)',
                          description='Group 2 count is rounded upward from group 1 × ratio.')]
    fields += [_field('attrition', 'Expected attrition fraction', description='Zero means no recruitment inflation; paired attrition refers to complete pairs.')]
    return fields


METHODS = {
    'means.independent': _method('means.independent', 'Independent means',
        'Noncentral-t planning for two independent groups with a common standard deviation.',
        _power_fields(),
        ['Independent observations and independent groups; no clustering or repeated measures adjustment.',
         'Normally distributed outcomes with a common population variance; pooled two-sample t test.',
         'Delta is group 1 minus group 2; delta and standard deviation use the declared unit.'],
        ['https://www.statsmodels.org/stable/generated/statsmodels.stats.power.TTestIndPower.html',
         'https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.nct.html']),
    'means.paired': _method('means.paired', 'Paired means',
        'Noncentral-t planning on independent, normally distributed within-pair differences.',
        _power_fields(paired=True),
        ['The independent unit is a complete pair; different pairs are independent.',
         'Use the standard deviation of within-pair differences, not the marginal measurement standard deviation.',
         'No clustering or serial-dependence adjustment; differences are normally distributed.'],
        ['https://www.statsmodels.org/stable/generated/statsmodels.stats.power.TTestPower.html']),
    'proportions.independent': _method('proportions.independent', 'Independent proportions',
        "Normal approximation using Cohen's arcsine h; not an exact binomial design.",
        _power_fields(proportions=True),
        ['Independent Bernoulli observations in two independent groups, without clustering.',
         "Normal approximation on the arcsine scale with h = 2 asin(sqrt(p1)) - 2 asin(sqrt(p2)).",
         'Adequate expected successes and failures are needed; rare-event or small-sample exact designs require another method.'],
        ['https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.proportion_effectsize.html',
         'https://www.statsmodels.org/stable/generated/statsmodels.stats.power.NormalIndPower.html']),
    'proportion.precision': _method('proportion.precision', 'Proportion interval precision',
        'Normal/Wald interval-width planning at an anticipated proportion; not a coverage guarantee.',
        [_field('solve_for', 'Calculate', type='choice', choices=[('sample_size', 'Required sample size'), ('precision', 'Precision at a fixed sample size')]),
         _field('alpha', 'Interval error rate (alpha)'),
         _field('expected_proportion', 'Anticipated proportion'),
         _field('half_width', 'Target interval half-width', goals=['sample_size']),
         _field('n', 'Analysis observations', goals=['precision']),
         _field('attrition', 'Expected attrition fraction')],
        ['Independent Bernoulli observations; two-sided normal/Wald interval planning.',
         'Width is evaluated at the supplied anticipated proportion; realized width and coverage may differ.',
         'No finite-population correction or clustering adjustment. Use a different method for exact/Wilson interval design.'],
        ['https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.samplesize_confint_proportion.html']),
}

METHODS['means.independent']['equations'] = [
    'd = delta / sd; n2 = ceil(ratio * n1); df = n1 + n2 - 2.',
    'Noncentrality = d * sqrt(n1 * n2 / (n1 + n2)); critical values use the central t distribution.',
    'Two-sided power sums both noncentral-t rejection tails; one-sided power uses the declared tail.',
]
METHODS['means.paired']['equations'] = [
    'd = delta / sd_of_differences; df = complete_pairs - 1; noncentrality = d * sqrt(complete_pairs).',
    'Power uses the noncentral t distribution and the declared central-t rejection boundary.',
]
METHODS['proportions.independent']['equations'] = [
    'h = 2 * asin(sqrt(p1)) - 2 * asin(sqrt(p2)); n2 = ceil(ratio * n1).',
    'Normal shift = h * sqrt(n1 * n2 / (n1 + n2)); rejection boundaries use the standard normal quantile.',
]
METHODS['proportion.precision']['equations'] = [
    'n = ceil(z_(1-alpha/2)^2 * p * (1-p) / target_half_width^2).',
    'Planned half-width at fixed n = z_(1-alpha/2) * sqrt(p * (1-p) / n).',
]


def method_catalog():
    return {'format': 'tinystudio.statistics-methods.v1', 'methods': deepcopy(list(METHODS.values()))}


def _probability(value, name, *, zero=False):
    value = number(value, name)
    if not (0 <= value < 1 if zero else 0 < value < 1):
        raise ValueError(f'{name} must be {"in [0, 1)" if zero else "in (0, 1)"}')
    return value


def normalize_parameters(parameters):
    if not isinstance(parameters, dict):
        raise ValueError('statistical planning parameters must be an object')
    p = deepcopy(parameters)
    method = METHODS.get(p.get('method')) if isinstance(p.get('method'), str) else None
    if method is None:
        raise ValueError('statistical method is not implemented; inspect the method catalog')
    goal = p.setdefault('solve_for', 'sample_size')
    goals = {item['id'] for item in method['fields'][0]['choices']}
    if not isinstance(goal, str) or goal not in goals:
        raise ValueError('solve_for is not implemented by the selected statistical method')
    allowed = {'method', 'input_sources'}
    p.setdefault('attrition', 0)
    if p['method'] in {'means.independent', 'proportions.independent'}:
        p.setdefault('ratio', 1)
    for field in method['fields']:
        if goal not in field.get('when', {}).get('solve_for', [goal]):
            continue
        name = field['name']
        allowed.add(name)
        if field['required'] and name not in p:
            raise ValueError(f'{name} is required for {p["method"]}/{goal}')
        if name not in p:
            continue
        if field['type'] == 'number':
            p[name] = number(p[name], name)
        elif field['type'] == 'choice':
            if not isinstance(p[name], str) or p[name] not in {choice['id'] for choice in field['choices']}:
                raise ValueError(f'{name} has an unsupported choice')
        elif not isinstance(p[name], str) or not p[name].strip():
            raise ValueError(f'{name} must be an explicit nonempty string')
    extra = p.keys() - allowed
    if extra:
        raise ValueError(f'unsupported or inapplicable planning parameter: {sorted(extra)[0]}')
    p['alpha'] = _probability(p['alpha'], 'alpha')
    p['attrition'] = _probability(p['attrition'], 'attrition', zero=True)
    if 'power' in p:
        p['power'] = _probability(p['power'], 'power')
        if p['power'] <= p['alpha']:
            raise ValueError('target power must exceed alpha for nonzero-effect planning')
    if 'n' in p:
        minimum = 1 if p['method'] == 'proportion.precision' else 2
        if not p['n'].is_integer() or not minimum <= p['n'] <= 2**53 - 1:
            raise ValueError(f'n must be an exactly representable integer >= {minimum}')
        p['n'] = int(p['n'])
    if 'sd' in p and p['sd'] <= 0:
        raise ValueError('sd must be positive')
    if 'ratio' in p and p['ratio'] <= 0:
        raise ValueError('ratio must be positive')
    for name in ('p1', 'p2', 'expected_proportion', 'half_width'):
        if name in p:
            p[name] = _probability(p[name], name)
    difference = p.get('delta', p.get('p1', 0) - p.get('p2', 0))
    if goal == 'sample_size' and p['method'] != 'proportion.precision':
        if difference == 0:
            raise ValueError('nonzero difference is required for sample-size planning')
        if ((p['alternative'] == 'greater' and difference < 0) or
                (p['alternative'] == 'less' and difference > 0)):
            raise ValueError('the supplied difference points away from the one-sided alternative; target power is unattainable')
    sources = p.setdefault('input_sources', {})
    if not isinstance(sources, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v.strip() for k, v in sources.items()):
        raise ValueError('input_sources must map input names to nonempty source notes')
    return p


def normalize_request(parameters):
    if not isinstance(parameters, dict):
        raise ValueError('statistical planning parameters must be an object')
    primary = deepcopy(parameters)
    sensitivity = primary.pop('sensitivity', [])
    if not isinstance(sensitivity, list):
        raise ValueError('sensitivity must be an array of parameter overrides')
    scenarios = [('primary', normalize_parameters(primary))]
    for index, patch in enumerate(sensitivity, 1):
        if not isinstance(patch, dict) or not patch or set(patch) & {'method', 'solve_for', 'sensitivity'}:
            raise ValueError('sensitivity entries must be nonempty input overrides for the same method and goal')
        scenarios.append((f'sensitivity-{index}', normalize_parameters({**primary, **patch})))
    return scenarios


def validate_statistics_selection(definition):
    if definition.get('mode', 'offline') != 'offline' or definition.get('operating_mode', 'simulation') != 'simulation':
        raise ValueError('statistics.plan is a planning calculation; select offline/simulation mode')
    for field in ('inputs', 'pipeline', 'components', 'connections', 'scenarios', 'clocks', 'objectives', 'learning', 'retrieval', 'rewards', 'evaluators'):
        if definition.get(field):
            raise ValueError(f'statistics.plan does not consume {field}; supply planning assumptions and input_sources explicitly')
    return {'scenarios': normalize_request(definition.get('parameters', {}))}
