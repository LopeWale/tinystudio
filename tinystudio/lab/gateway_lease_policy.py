"""Frozen operator-selected lease actions and optional watchdog cadence."""
from dataclasses import dataclass
import math


def _seconds(value, label, *, zero=False):
    if value is None:
        return None
    try:
        valid = (type(value) in {int, float} and math.isfinite(value)
                 and (value >= 0 if zero else value > 0))
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        raise ValueError(label + ' must be a positive finite number or None' if not zero else
                         label + ' must be a nonnegative finite number or None')
    return value


@dataclass(frozen=True)
class GatewayLeasePolicy:
    on_release: str = 'revoke_only'
    on_expiry: str = 'revoke_only'
    on_cancel: str = 'local_stop'
    watchdog_interval_seconds: float | None = None

    def __post_init__(self):
        for name in ('on_release', 'on_expiry', 'on_cancel'):
            action = getattr(self, name)
            if type(action) is not str or action not in {'revoke_only', 'local_stop'}:
                raise ValueError(name + ' must select revoke_only or local_stop')
        _seconds(self.watchdog_interval_seconds, 'watchdog interval')

    @classmethod
    def from_binding(cls, binding):
        if not isinstance(binding, dict):
            raise ValueError('gateway binding must be an object')
        selected = binding.get('lease_policy', {})
        if (not isinstance(selected, dict)
                or selected.keys() - {'on_release', 'on_expiry', 'on_cancel', 'watchdog_interval_seconds'}):
            raise ValueError('lease_policy must contain only supported lease action and watchdog fields')
        return cls(**selected)

    def as_dict(self):
        return {'on_release': self.on_release, 'on_expiry': self.on_expiry,
                'on_cancel': self.on_cancel, 'watchdog_interval_seconds': self.watchdog_interval_seconds}
