"""Shared path-segment validation for authenticated Lab HTTP clients."""
import re


def path_identifier(value: str, *, maximum: int = 64) -> str:
    """Accept one opaque Lab URL segment; never permit path or query syntax."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,"+str(maximum)+r"}", value):
        raise ValueError("invalid laboratory identifier")
    return value

