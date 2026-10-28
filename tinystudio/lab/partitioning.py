"""Shared, strict partition labels and connected evidence groups.

Parsing a label never allocates a split. In particular, a held-out alias cannot
silently become unassigned evidence in another consumer.
"""
from __future__ import annotations

import json
from types import MappingProxyType


PARTITION_ALIASES = MappingProxyType({
    "train": "train", "training": "train",
    "val": "validation", "validation": "validation", "dev": "validation", "eval": "validation",
    "test": "test", "testing": "test", "final_test": "test", "final-test": "test",
    "finaltest": "test", "heldout": "test", "held_out": "test", "held-out": "test",
    "holdout": "test", "final": "test",
    "truth": "truth", "ground_truth": "truth", "ground-truth": "truth",
})
PARTITION_FIELDS = ("partition", "split", "bucket")
TRUTH_ROLES = frozenset(alias for alias, label in PARTITION_ALIASES.items() if label == "truth")
HELDOUT_ROLES = frozenset({"evaluation", *(alias for alias, label in PARTITION_ALIASES.items()
                                          if label in {"test", "validation"})})


def partition_label(row: dict, *, allow_truth: bool = False,
                    allow_unassigned: bool = False) -> str | None:
    labels = []
    for field in PARTITION_FIELDS:
        if row.get(field) is None:
            continue
        value = str(row[field]).casefold()
        if allow_unassigned and value in {"unassigned", "uncategorized"}:
            labels.append(None)
            continue
        label = PARTITION_ALIASES.get(value)
        if label is None or label == "truth" and not allow_truth:
            raise ValueError("partition labels are unsupported or conflicting")
        labels.append(label)
    if len(set(labels)) > 1:
        raise ValueError("partition labels are unsupported or conflicting")
    return labels[0] if labels else None


def partition_labels(rows: list[dict], **options) -> list[str | None]:
    labels = [partition_label(row, **options) for row in rows]
    if any(label is not None for label in labels) and any(label is None for label in labels):
        raise ValueError("every row must declare a partition when any partition is explicit")
    return labels


def source_protection(row: dict, *, visibility: str = "model", role: str = "source") -> str:
    """Combine source and row restrictions without promoting their authority."""
    values = [visibility, row.get("visibility", "model"), row.get("source_visibility", "model")]
    if any(not isinstance(value, str) or value not in {"model", "evaluation", "truth"} for value in values):
        raise ValueError("source visibility must be model, evaluation or truth")
    visibilities = set(values)
    values = [value for value in (role, row.get("role"), row.get("source_role")) if value is not None]
    if any(not isinstance(value, str) for value in values):
        raise ValueError("source roles must be strings")
    roles = {value.casefold() for value in values}
    label = partition_label(row, allow_truth=True, allow_unassigned=True)
    if "truth" in visibilities or roles & TRUTH_ROLES or label == "truth":
        return "truth"
    if "evaluation" in visibilities or roles & HELDOUT_ROLES or label in {"validation", "test"}:
        return "evaluation"
    return "model"


def isolation_groups(rows: list[dict], fields: list[str]) -> list[list[int]]:
    """A shared device OR scenario connects episodes into one isolation group."""
    if not isinstance(fields, list) or any(not isinstance(field, str) or not field for field in fields):
        raise ValueError("leakage_policy.group_by must be a list of record field names")
    if fields and any(any(row.get(field) is None for field in fields) for row in rows):
        raise ValueError("isolation group metadata is incomplete")
    identifiers = [str(row.get("source_record_id", row.get("record_id", f"row:{index}")))
                   for index, row in enumerate(rows)]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("snapshot source record IDs must be unique")
    parents = list(range(len(rows)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for field in fields:
        seen = {}
        for index, row in enumerate(rows):
            value = json.dumps(row[field], sort_keys=True, separators=(",", ":"))
            if value in seen:
                parents[find(index)] = find(seen[value])
            else:
                seen[value] = index
    groups = {}
    for index in range(len(rows)):
        groups.setdefault(find(index), []).append(index)
    return list(groups.values())
