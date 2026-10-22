"""Datasets, datapoints, buckets, and snapshots.

The record format is the one Studio uses and every fine-tuning stack accepts:
one JSON object per line with a ``messages`` array of ``system`` / ``user`` /
``assistant`` turns. A datapoint also carries a ``bucket`` (``uncategorized``,
``training``, ``validation``) and optional ``labels``.
"""

from __future__ import annotations

import json
import random
import shutil
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from tinystudio.core.project import Project, ProjectError, now, read_json, write_json

ROLES = ("system", "user", "assistant")
BUCKETS = ("uncategorized", "training", "validation")


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
        # preference pairs for DPO: the assistant turn is the chosen answer, `rejected` the dispreferred one
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


def dataset_meta(project: Project, name: str) -> dict[str, Any]:
    path = project.dataset_dir(name) / "meta.json"
    if not path.exists():
        raise ProjectError(f"dataset {name!r} does not exist")
    return read_json(path)


def load_dataset(project: Project, name: str) -> list[dict[str, Any]]:
    dataset_meta(project, name)
    return list(read_jsonl(project.dataset_dir(name) / "datapoints.jsonl"))


def save_dataset(project: Project, name: str, rows: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    directory = project.dataset_dir(name)
    count = write_jsonl(directory / "datapoints.jsonl", rows)
    existing = read_json(directory / "meta.json") if (directory / "meta.json").exists() else {}
    buckets = {bucket: sum(1 for row in rows if row["bucket"] == bucket) for bucket in BUCKETS}
    merged = {
        **existing,
        **(meta or {}),
        "name": name,
        "datapoints": count,
        "buckets": buckets,
        "updated": now(),
        "created": existing.get("created", now()),
    }
    write_json(directory / "meta.json", merged)
    return merged


def create_dataset_from_jsonl(project: Project, name: str, source: Path) -> dict[str, Any]:
    if (project.dataset_dir(name) / "meta.json").exists():
        raise ProjectError(f"dataset {name!r} already exists")
    rows = list(read_jsonl(source))
    if not rows:
        raise ProjectError(f"{source} holds no datapoints")
    return save_dataset(project, name, rows, {"origin": {"kind": "jsonl", "path": str(source.resolve())}})


def add_datapoints(project: Project, name: str, rows: Iterable[dict[str, Any]], bucket: str | None = None) -> dict[str, Any]:
    """Append records. With ``bucket`` every record is forced into that bucket; otherwise each record's own bucket (default uncategorized) is kept."""
    if bucket is not None and bucket not in BUCKETS:
        raise DatapointError(f"bucket must be one of {BUCKETS}")
    existing = load_dataset(project, name)
    added = []
    for row in rows:
        clean = validate_datapoint(row)
        if bucket is not None:
            clean["bucket"] = bucket
        added.append(clean)
    return save_dataset(project, name, existing + added)


def autosplit(project: Project, name: str, training_pct: int = 80, seed: int = 0, allow_synthetic_in_validation: bool = False) -> dict[str, Any]:
    """Assign every uncategorized datapoint to training or validation.

    Mirrors Studio's Autosplit: only uncategorized datapoints move, so manual
    bucket choices survive, and synthetic datapoints can be kept out of
    validation to avoid grading a model on data the same generator wrote.
    """
    if not 1 <= training_pct <= 99:
        raise ProjectError("training_pct must be between 1 and 99")
    rows = load_dataset(project, name)
    pending = [index for index, row in enumerate(rows) if row["bucket"] == "uncategorized"]
    rng = random.Random(seed)
    rng.shuffle(pending)
    validation_target = round(len(pending) * (100 - training_pct) / 100)
    validation_set: set[int] = set()
    for index in pending:
        if len(validation_set) >= validation_target:
            break
        if not allow_synthetic_in_validation and rows[index].get("source", "").startswith("synthetic"):
            continue
        validation_set.add(index)
    for index in pending:
        rows[index]["bucket"] = "validation" if index in validation_set else "training"
    return save_dataset(project, name, rows)


def create_snapshot(project: Project, dataset: str, name: str, min_training: int = 10) -> dict[str, Any]:
    """Freeze the current training and validation buckets into an immutable split."""
    rows = load_dataset(project, dataset)
    train = [row for row in rows if row["bucket"] == "training"]
    val = [row for row in rows if row["bucket"] == "validation"]
    if len(train) < min_training:
        raise ProjectError(
            f"snapshot needs at least {min_training} training datapoints; dataset {dataset!r} has {len(train)} "
            f"(run `tinystudio dataset split {dataset}` first)"
        )
    directory = project.snapshot_dir(name)
    if (directory / "meta.json").exists():
        raise ProjectError(f"snapshot {name!r} already exists; snapshots are immutable, pick a new name")
    write_jsonl(directory / "train.jsonl", train)
    write_jsonl(directory / "val.jsonl", val)
    meta = {
        "name": name,
        "dataset": dataset,
        "training_count": len(train),
        "validation_count": len(val),
        "created": now(),
    }
    write_json(directory / "meta.json", meta)
    return meta


def snapshot_meta(project: Project, name: str) -> dict[str, Any]:
    path = project.snapshot_dir(name) / "meta.json"
    if not path.exists():
        raise ProjectError(f"snapshot {name!r} does not exist")
    return read_json(path)


def load_snapshot(project: Project, name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    snapshot_meta(project, name)
    directory = project.snapshot_dir(name)
    return list(read_jsonl(directory / "train.jsonl")), list(read_jsonl(directory / "val.jsonl"))


def set_labels(project: Project, name: str, labels: list[dict[str, str]]) -> dict[str, Any]:
    """Define the label vocabulary of a dataset (name + description), as Studio's create-labels does."""
    meta = dataset_meta(project, name)
    clean = []
    seen: set[str] = set()
    for label in labels:
        label_name = str(label.get("name", "")).strip()
        if not label_name or label_name in seen:
            continue
        seen.add(label_name)
        clean.append({"name": label_name, "description": str(label.get("description", "")).strip()})
    meta["labels"] = clean
    write_json(project.dataset_dir(name) / "meta.json", meta)
    return meta


def delete_dataset(project: Project, name: str) -> None:
    dataset_meta(project, name)
    shutil.rmtree(project.dataset_dir(name))


def delete_snapshot(project: Project, name: str) -> None:
    snapshot_meta(project, name)
    shutil.rmtree(project.snapshot_dir(name))


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
