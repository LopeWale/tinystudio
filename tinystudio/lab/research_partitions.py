"""Pure source-index plans for numeric/fusion and offline-policy research.

Consumers must select rows before extracting features/targets, fit transforms only
on ``normalization_rows``, and retain ``source_record_ids`` in their evidence.
Test/truth rows are accounted for but never appear in optimization windows.
"""
from __future__ import annotations

import json
import math
import random

from .partitioning import isolation_groups, partition_labels, source_protection


def _fields(rows, group_by):
    inferred = [field for field in ("episode_id", "device_id", "scenario_id")
                if any(row.get(field) is not None for row in rows)]
    if group_by is None:
        return inferred
    if not isinstance(group_by, list) or any(not isinstance(field, str) or not field for field in group_by):
        raise ValueError("group_by must be a list of record field names")
    return list(dict.fromkeys([*inferred, *group_by]))


def _metadata(rows, group_by):
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError("research records must be a nonempty list of objects")
    labels = partition_labels(rows, allow_truth=True)
    fields = _fields(rows, group_by)
    groups = isolation_groups(rows, fields)
    identities = [str(row.get("source_record_id", row.get("record_id", f"row:{index}")))
                  for index, row in enumerate(rows)]
    for row, label in zip(rows, labels):
        protection = source_protection(row)
        if protection == "truth" and label != "truth":
            raise ValueError("truth source protection conflicts with declared partition")
        if protection == "evaluation" and label not in {"validation", "test"}:
            raise ValueError("evaluation source protection requires a held-out partition")
    return labels, fields, groups, identities


def _fraction(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1:
        raise ValueError("validation_fraction must be between zero and one")


def _plan(labels, identities, strategy, require_validation):
    result = {label: [index for index, value in enumerate(labels) if value == label]
              for label in ("train", "validation", "test", "truth")}
    if not result["train"] or require_validation and not result["validation"]:
        raise ValueError("research requires training and validation evidence")
    result.update(source_record_ids=identities, normalization_rows=list(result["train"]), strategy=strategy)
    return result


def record_partition_plan(rows: list[dict], *, validation_fraction: float = 0.2,
                          seed: int = 42, group_by: list[str] | None = None,
                          require_validation: bool = True) -> dict:
    """Preserve explicit labels or allocate whole connected source groups.

    Any shared episode, device OR scenario joins a group. ``group_by`` adds
    additional isolation fields; it cannot disable detected source groups. Explicit
    labels cannot cross groups, including final-test/truth groups. Unlabelled
    rows without group metadata retain the legacy seeded row holdout.
    """
    _fraction(validation_fraction)
    labels, _, groups, identities = _metadata(rows, group_by)
    if any(label is not None for label in labels):
        if any(len({labels[index] for index in group}) > 1 for group in groups):
            raise ValueError("source isolation group crosses declared partitions")
        return _plan(labels, identities, "preserved_explicit_groups", require_validation)
    if not require_validation:
        return _plan(["train"] * len(rows), identities, "unlabelled_training", False)
    order = list(groups)
    random.Random(seed).shuffle(order)
    if len(order) < 2:
        raise ValueError("research requires at least two independent isolation groups")
    target = max(1, int(len(rows) * validation_fraction))
    validation = set()
    for group in order[:-1]:
        validation.update(group)
        if len(validation) >= target:
            break
    labels = ["validation" if index in validation else "train" for index in range(len(rows))]
    return _plan(labels, identities, "seeded_connected_groups", require_validation)


def _episodes(rows):
    has_episode = any(row.get("episode_id") is not None for row in rows)
    if has_episode and any(row.get("episode_id") is None for row in rows):
        raise ValueError("episode metadata is incomplete")
    episodes = {}
    for index, row in enumerate(rows):
        key = json.dumps(row.get("episode_id"), sort_keys=True)
        episodes.setdefault(key, []).append(index)
    for indices in episodes.values():
        if any(rows[index].get("time") is not None for index in indices):
            try:
                times = [float(rows[index]["time"]) for index in indices]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("forecast episode times must be finite and strictly chronological") from exc
            if any(not math.isfinite(value) for value in times) or any(a >= b for a, b in zip(times, times[1:])):
                raise ValueError("forecast episode times must be finite and strictly chronological")
    return list(episodes.values())


def forecast_window_plan(rows: list[dict], *, context: int, horizon: int = 1,
                         validation_fraction: float = 0.2, seed: int = 42,
                         group_by: list[str] | None = None,
                         temporal_split: bool = False) -> dict:
    """Plan windows with original row indices, never crossing episode boundaries.

    Default explicit/grouped data keeps all context in its target partition.
    ``temporal_split=True`` instead permits causal train history for validation
    within an episode, with train preceding validation. This deliberately relaxes
    group isolation for an explicitly temporal experiment. Test/truth rows may
    occur in that episode but never enter window history, targets or transforms.
    A legacy unlabelled single series uses chronological internal holdout and
    the same causal-history rule. Input order defines chronology when no times
    are supplied; supplied times must increase strictly within each episode.
    """
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (context, horizon)):
        raise ValueError("context and horizon must be positive integers")
    _fraction(validation_fraction)
    labels, fields, groups, identities = _metadata(rows, group_by)
    episodes = _episodes(rows)
    explicit = any(label is not None for label in labels)
    legacy = not explicit and len(episodes) == 1 and not fields
    causal = temporal_split or legacy
    if explicit and causal:
        plan = _plan(labels, identities, "preserved_explicit_temporal", True)
    elif not explicit and causal:
        labels = [None] * len(rows)
        for indices in episodes:
            cutoff = int(len(indices) * (1 - validation_fraction))
            for position, index in enumerate(indices):
                labels[index] = "train" if position < cutoff else "validation"
        plan = _plan(labels, identities, "chronological_target_split", True)
        if legacy:
            plan["cutoff"] = cutoff
    else:
        plan = record_partition_plan(rows, validation_fraction=validation_fraction,
                                     seed=seed, group_by=group_by)
        labels = [None] * len(rows)
        for label in ("train", "validation", "test", "truth"):
            for index in plan[label]:
                labels[index] = label
    if causal:
        for indices in episodes:
            ranks = {"train": 0, "validation": 1, "test": 2}
            active = [ranks[labels[index]] for index in indices if labels[index] in ranks]
            if any(left > right for left, right in zip(active, active[1:])):
                raise ValueError("temporal partitions must place training before validation before test")
    windows, train, validation = [], [], []
    for indices in episodes:
        for position in range(context, len(indices) - horizon + 1):
            history = indices[position - context:position]
            targets = indices[position:position + horizon]
            label = labels[targets[0]]
            if label not in {"train", "validation"} or any(labels[index] != label for index in targets):
                continue
            allowed = {"train", "validation"} if causal and label == "validation" else {label}
            if any(labels[index] not in allowed for index in history):
                continue
            (train if label == "train" else validation).append(len(windows))
            windows.append({"context_rows": history, "target_rows": targets})
    if not train or not validation:
        raise ValueError("series cannot form training and validation windows within episodes")
    return {**plan, "record_partitions": {label: plan[label] for label in ("train", "validation", "test", "truth")},
            "train": train, "validation": validation, "windows": windows,
            "target_positions": [window["target_rows"][0] for window in windows],
            "temporal_history": causal}
