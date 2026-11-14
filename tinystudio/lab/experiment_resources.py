"""Explicit resource budgets, independent of scientific sample-size decisions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from decimal import Decimal, InvalidOperation
import math
from typing import ClassVar


def count(value, name, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or value is None:
        raise ValueError(f'{name} must be a nonnegative integer or an explicit null budget')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            raise ValueError(f'{name} must be a nonnegative integer')
        return int(number)
    except (InvalidOperation, TypeError, OverflowError) as error:
        raise ValueError(f'{name} must be a nonnegative integer') from error


def seconds(value, name, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or value is None:
        raise ValueError(f'{name} must be a finite nonnegative duration')
    try:
        selected = float(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError(f'{name} must be a finite nonnegative duration') from error
    if not math.isfinite(selected) or selected < 0:
        raise ValueError(f'{name} must be a finite nonnegative duration')
    return selected


@dataclass(frozen=True)
class ResourceLimits:
    """None means the operator has not imposed a bound on that resource.

    Byte/window/stage sizes are resource controls, never evidence that a dataset
    or experiment is scientifically sufficient. Protocol limits live with their
    transport implementations.
    """
    max_samples: int | None = None
    max_duration: float | None = None
    max_timeout: float | None = None
    max_frame_bytes: int | None = None
    max_queue_frames: int | None = None
    max_models: int | None = None
    max_rows: int | None = None
    source_bytes: int | None = None
    per_source_bytes: int | None = None
    pipeline_stages: int | None = None
    window_records: int | None = None

    def __post_init__(self):
        for field in fields(self):
            parser = seconds if field.name in {'max_duration', 'max_timeout'} else count
            object.__setattr__(self, field.name, parser(getattr(self, field.name), field.name, optional=True))

    @classmethod
    def from_dict(cls, value=None):
        value = {} if value is None else value
        if not isinstance(value, dict) or value.keys() - {field.name for field in fields(cls)}:
            raise ValueError('resource limits contain an unsupported field')
        return cls(**value)

    def authorize(self, name, amount):
        limit = getattr(self, name)
        if limit is not None and (amount is None or amount > limit):
            raise ValueError(f'{name} exceeds its explicit operator resource budget ({limit})')

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class CollectionPlan:
    default_frame_bytes: ClassVar[int] = 65536
    samples: int | None
    duration: float | None
    timeout: float
    interval: float
    stale_after: float
    max_frame_bytes: int | None
    max_queue_frames: int | None
    operator_limits: ResourceLimits

    @classmethod
    def resolve(cls, parameters, limits=None):
        if not isinstance(parameters, dict):
            raise ValueError('collection parameters must be an object')
        operator = ResourceLimits.from_dict(limits)
        samples = count(parameters.get('samples', 100), 'samples', optional=True)
        duration = seconds(parameters.get('duration', 10), 'duration', optional=True)
        timeout = seconds(parameters.get('timeout', .5), 'timeout')
        interval = seconds(parameters.get('interval', 0), 'interval')
        stale = seconds(parameters.get('stale_after', 5), 'stale_after')
        frame_default = cls.default_frame_bytes if operator.max_frame_bytes is None else min(cls.default_frame_bytes, operator.max_frame_bytes)
        frame = count(parameters.get('max_frame_bytes', frame_default), 'max_frame_bytes', optional=True)
        queue = count(parameters.get('max_queue_frames', operator.max_queue_frames), 'max_queue_frames', optional=True)
        for name, amount in (('max_samples', samples), ('max_duration', duration),
                             ('max_timeout', timeout), ('max_frame_bytes', frame), ('max_queue_frames', queue)):
            operator.authorize(name, amount)
        return cls(samples, duration, timeout, interval, stale, frame, queue, operator)

    def to_dict(self):
        selected = {field.name: getattr(self, field.name) for field in fields(self) if field.name != 'operator_limits'}
        return {'selected': selected, 'operator_limits': self.operator_limits.to_dict(),
                'scientific_validity_rule': False,
                'basis': 'Authored collection plan and explicit execution-host resource budgets.'}
