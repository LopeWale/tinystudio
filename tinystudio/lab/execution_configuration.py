"""Authored execution budgets, separate from delivery or provider constraints.

Omitted values retain legacy defaults. None selects no authored cap; zero is
an exact zero budget. These checks use the output scope supplied by the caller
and do not exempt artifacts, reserve transfer capacity, or inspect any files.
"""
from collections.abc import Mapping
from dataclasses import dataclass
import math


DEFAULT_MAX_SECONDS = 300
DEFAULT_MAX_OUTPUT_BYTES = 512 * 1024 ** 2


def _integer(value, label, *, optional=False):
    if value is None and optional:
        return
    if type(value) is not int or value < 0:
        raise ValueError(label + ' must be a nonnegative integer' + (' or None' if optional else ''))


def _clock(value, label):
    if type(value) not in {int, float}:
        raise ValueError(label + ' must be a finite representable clock value')
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(label + ' must be a finite representable clock value') from None
    if not math.isfinite(result):
        raise ValueError(label + ' must be a finite representable clock value')
    return result


class ExecutionBudgetExceeded(ValueError):
    """Observed bytes or monotonic time exhausted the selected budget.

    For output_bytes, maximum and actual are exact byte counts. For wall_time,
    they are the absolute monotonic deadline and observed clock value. The
    selected whole-second duration remains available on ExecutionBudget.
    """
    def __init__(self, resource, maximum, actual):
        # Avoid decimal conversion limits on valid arbitrarily large counts.
        super().__init__('execution ' + resource + ' exhausted the selected budget')
        self.resource, self.maximum, self.actual = resource, maximum, actual


@dataclass(frozen=True)
class ExecutionBudget:
    max_seconds: int | None = DEFAULT_MAX_SECONDS
    max_output_bytes: int | None = DEFAULT_MAX_OUTPUT_BYTES

    def __post_init__(self):
        _integer(self.max_seconds, 'max_seconds', optional=True)
        _integer(self.max_output_bytes, 'max_output_bytes', optional=True)

    @classmethod
    def from_execution(cls, execution):
        """Extract resource fields from an already validated execution mapping.

        This is not a whole-contract validator. ExecutionProfile owns validation
        of profile/backend, other fields and unknown keys. New contract fields
        therefore need no second whitelist here. An empty mapping uses defaults.
        """
        if not isinstance(execution, Mapping):
            raise ValueError('execution budget extraction requires a mapping')
        return cls(max_seconds=execution.get('max_seconds', DEFAULT_MAX_SECONDS),
                   max_output_bytes=execution.get('max_output_bytes', DEFAULT_MAX_OUTPUT_BYTES))

    def as_dict(self):
        return {'max_seconds': self.max_seconds, 'max_output_bytes': self.max_output_bytes}

    def deadline(self, started_monotonic):
        started = _clock(started_monotonic, 'started_monotonic')
        if self.max_seconds is None:
            return None
        duration = _clock(self.max_seconds, 'max_seconds')
        final = started + duration
        if not math.isfinite(final) or (self.max_seconds > 0 and final <= started):
            raise ValueError('execution deadline is not representable on the selected clock')
        return final

    def check_wall(self, now, deadline):
        observed = _clock(now, 'now')
        if deadline is None:
            return
        final = _clock(deadline, 'deadline')
        if observed >= final:
            raise ExecutionBudgetExceeded('wall_time', final, observed)

    def check_output(self, actual_bytes):
        _integer(actual_bytes, 'actual_bytes')
        if self.max_output_bytes is not None and actual_bytes > self.max_output_bytes:
            raise ExecutionBudgetExceeded('output_bytes', self.max_output_bytes, actual_bytes)

    def remaining_output(self, prior):
        self.check_output(prior)
        return None if self.max_output_bytes is None else self.max_output_bytes - prior
