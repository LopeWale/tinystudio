"""Version-one clock coordinates shared by data preparation and visualization.

Origin aliases identify the same origin. Session aliases identify the same
session; reset identity is a separate dimension. Missing coordinates permit
source-bounded inspection, never qualified cross-source timing.
"""
from __future__ import annotations

from .contracts import canonical
from .signals import number

CLOCK_FIELDS = ("clock_domain", "clock_units", "clock_origin", "clock_epoch", "time_origin",
                "clock_session", "session_id", "clock_reset_id", "clock_reset", "capture_id", "acquisition_id")


def _scalar(value, field):
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        return number(value, field)
    raise ValueError(f"{field} requires a nonempty string or finite numeric clock identifier")


def _alias(row, fields):
    values = [_scalar(row[field], field) for field in fields if row.get(field) is not None]
    if values and any(value != values[0] or type(value) is not type(values[0]) for value in values):
        raise ValueError(f"conflicting clock aliases: {', '.join(fields)}")
    return values[0] if values else None


def clock_reference(row, source_scope=None):
    domain, units = row.get("clock_domain"), row.get("clock_units")
    for field, value in (("clock_domain", domain), ("clock_units", units)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{field} must be a nonempty clock label")
    domain = domain.strip() if domain else None
    units = units.strip() if units else None
    if units == "unspecified":
        units = None
    origin = _alias(row, ("clock_origin", "clock_epoch", "time_origin"))
    session = _alias(row, ("clock_session", "session_id"))
    reset = _scalar(row.get("clock_reset_id"), "clock_reset_id")
    if row.get("clock_reset") is True and reset is None:
        raise ValueError("clock_reset requires an explicit clock_reset_id for the new coordinate segment")
    qualified = domain is not None and units is not None and origin is not None and session is not None
    capture = next((row[field] for field in ("capture_id", "acquisition_id", "source_artifact_id")
                    if row.get(field) is not None), source_scope)
    scope = None if qualified else [capture, row.get("episode_id"), row.get("device_id") if domain is None else None]
    coordinate = [domain, units, origin, session, reset, scope]
    return {"schema_version": 1, "key": canonical(coordinate).decode(), "coordinate": coordinate,
            "domain": domain, "units": units, "origin": origin, "session": session,
            "reset": reset, "source_scope": scope, "qualified": qualified}
