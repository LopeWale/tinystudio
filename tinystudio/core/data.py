"""The datapoint record: validation, JSONL reading and writing, and pure helpers over rows.

A datapoint is ``{"messages": [...], "bucket": ..., "labels": [...]}`` with optional ``source``,
``rating``, and ``rejected`` (the dispreferred answer for DPO). Storage lives in the services layer.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

ROLES = ("system", "user", "assistant")
BUCKETS = ("uncategorized", "training", "validation")
OPTIONAL_FIELDS = ("source", "rating", "rejected")


class DatapointError(ValueError):
    """A record does not have the messages shape; the message says where."""


def validate_datapoint(value: Any, where: str = "record") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatapointError(f"{where}: expected a JSON object")
    messages = value.get("messages")
    if not isinstance(messages, list) or not messages:
        raise DatapointError(f"{where}: 'messages' must be a non-empty array")
    roles: list[str] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise DatapointError(f"{where}: messages[{index}] must be an object")
        role, content = message.get("role"), message.get("content")
        if role not in ROLES:
            raise DatapointError(f"{where}: messages[{index}].role must be one of {ROLES}")
        if not isinstance(content, str) or not content.strip():
            raise DatapointError(f"{where}: messages[{index}].content must be non-empty text")
        roles.append(role)
    if "system" in roles[1:]:
        raise DatapointError(f"{where}: a system message is only allowed first")
    if roles[-1] != "assistant":
        raise DatapointError(f"{where}: the last message must be the assistant's answer")
    if "user" not in roles:
        raise DatapointError(f"{where}: at least one user message is required")
    bucket = value.get("bucket", "uncategorized")
    if bucket not in BUCKETS:
        raise DatapointError(f"{where}: bucket must be one of {BUCKETS}")
    labels = value.get("labels", [])
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise DatapointError(f"{where}: labels must be a list of strings")
    rating = value.get("rating")
    return {
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "bucket": bucket,
        "labels": labels,
        **({"source": value["source"]} if isinstance(value.get("source"), str) else {}),
        **({"rating": float(rating)} if isinstance(rating, (int, float)) and not isinstance(rating, bool) else {}),
        **({"rejected": value["rejected"]} if isinstance(value.get("rejected"), str) and value["rejected"].strip() else {}),
    }


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as cause:
                raise DatapointError(f"{path}:{number}: invalid JSON ({cause.msg})") from cause
            yield validate_datapoint(raw, f"{path}:{number}")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    tmp.replace(path)
    return count


def bucket_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {bucket: 0 for bucket in BUCKETS}
    for row in rows:
        counts[row.get("bucket", "uncategorized")] = counts.get(row.get("bucket", "uncategorized"), 0) + 1
    return counts


def assign_split(rows: list[dict[str, Any]], training_pct: int = 80, seed: int = 0, allow_synthetic_in_validation: bool = False) -> dict[int, str]:
    """Studio's Autosplit as a pure function: new buckets for the uncategorized rows, by index.

    Only uncategorized rows move, so manual choices survive, and synthetic rows can be kept out of
    validation to avoid grading a model on data the same generator wrote.
    """
    from tinystudio.core.project import ProjectError

    if not 1 <= training_pct <= 99:
        raise ProjectError("training_pct must be between 1 and 99")
    pending = [index for index, row in enumerate(rows) if row.get("bucket", "uncategorized") == "uncategorized"]
    rng = random.Random(seed)
    rng.shuffle(pending)
    validation_target = round(len(pending) * (100 - training_pct) / 100)
    validation_set: set[int] = set()
    for index in pending:
        if len(validation_set) >= validation_target:
            break
        if not allow_synthetic_in_validation and str(rows[index].get("source", "")).startswith("synthetic"):
            continue
        validation_set.add(index)
    return {index: ("validation" if index in validation_set else "training") for index in pending}


def last_user_message(row: dict[str, Any]) -> str:
    return next(m["content"] for m in reversed(row["messages"]) if m["role"] == "user")


def system_prompt(row: dict[str, Any]) -> str | None:
    first = row["messages"][0]
    return first["content"] if first["role"] == "system" else None


def reference_answer(row: dict[str, Any]) -> str:
    return row["messages"][-1]["content"]


def prompt_messages(row: dict[str, Any]) -> list[dict[str, str]]:
    """Everything up to, but excluding, the final assistant turn."""
    return row["messages"][:-1]
