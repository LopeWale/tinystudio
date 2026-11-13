"""Researcher-authored study metadata; numerical methods keep their own domains."""
from __future__ import annotations

from typing import Any
import math
import re

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .contracts import Contract, canonical


MAX_DOCUMENT_BYTES = 2 * 1024**2
MAX_EXACT_INTEGER = 2**53 - 1


def _numbers(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('study data requires finite numbers; use null and an explicit missingness reason')
    if type(value) is int and abs(value) > MAX_EXACT_INTEGER:
        raise ValueError('integer exceeds exact JSON representation; use a decimal string to preserve its value')
    if isinstance(value, dict):
        for child in value.values():
            _numbers(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _numbers(child)


def finite_document(value):
    _numbers(value)
    if len(canonical(value)) > MAX_DOCUMENT_BYTES:
        raise ValueError('study document or record exceeds2MiB; retain bulk data as an artifact')


class StudyProtocol(Contract):
    name: str = Field(min_length=1, max_length=200)
    description: str = ''
    hypothesis: str = ''
    independent_unit: str = ''
    conditions: list[Any] = Field(default_factory=list)
    outcomes: list[Any] = Field(default_factory=list)
    sampling_plan: dict[str, Any] = Field(default_factory=dict)
    stopping_rules: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default='draft', max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def portable(self):
        if not self.name.strip():
            raise ValueError('study name must be nonempty')
        finite_document(self.model_dump())
        return self


class StudyRecordRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, le=MAX_EXACT_INTEGER, strict=True)
    idempotency_key: str = Field(min_length=1, max_length=120)
    kind: str = Field(default='observation', min_length=1, max_length=120)
    unit_id: str = Field(default='', max_length=500)
    condition: str = Field(default='', max_length=500)
    observed_at: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    run_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    supersedes: str | None = None

    @field_validator('observed_at')
    @classmethod
    def observation_clock(cls, value):
        if value is not None:
            if not re.match(r'^\d{4}-\d{2}-\d{2}[Tt ]', value):
                raise ValueError('observed_at requires an ISO8601 timestamp with timezone; raw source clocks belong in data')
            TypeAdapter(AwareDatetime).validate_python(value)
        # Validation must not round authored sub-microsecond source timestamps.
        return value

    @model_validator(mode='after')
    def portable(self):
        if not self.kind.strip() or not self.idempotency_key.strip():
            raise ValueError('record kind and idempotency key must be nonempty')
        for name in ('run_ids', 'artifact_ids'):
            values = getattr(self, name)
            if any(not value or len(value) > 64 for value in values) or len(set(values)) != len(values):
                raise ValueError(f'{name} must contain unique nonempty identifiers')
        if self.supersedes is not None and not 1 <= len(self.supersedes) <= 64:
            raise ValueError('supersedes must identify an earlier record')
        # JSON-mode serialization can turn NaN/Infinity in Any fields into null.
        # Reject them while their original values are still available.
        _numbers(self.data)
        finite_document(self.model_dump(mode='json'))
        return self
