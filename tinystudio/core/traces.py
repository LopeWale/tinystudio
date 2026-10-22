"""Traces: inferences logged by the server, scorable and promotable into datasets.

Studio's monitoring tab lets you look at real usage, score it, and add good
(or corrected) examples to a dataset. Here the same loop is three commands
over ``traces.jsonl``: list, score, promote.
"""

from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from tinystudio.core.data import add_datapoints
from tinystudio.core.project import Project, ProjectError


@contextmanager
def traces_lock(project: Project) -> Iterator[None]:
    """Advisory file lock shared by the server's appends and the CLI's rewrites of traces.jsonl."""
    lock_path = project.traces_path.with_suffix(".jsonl.lock")
    with lock_path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def append_trace(project: Project, record: dict[str, Any]) -> None:
    with traces_lock(project):
        with project.traces_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_traces(project: Project) -> list[dict[str, Any]]:
    path = project.traces_path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_traces(project: Project, rows: list[dict[str, Any]]) -> None:
    tmp = project.traces_path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(project.traces_path)


def score_trace(project: Project, trace_id: str, score: float, note: str = "", corrected: str | None = None) -> dict[str, Any]:
    with traces_lock(project):
        rows = read_traces(project)
        for row in rows:
            if row.get("id") == trace_id:
                row["score"] = score
                if note:
                    row["note"] = note
                if corrected is not None:
                    row["corrected"] = corrected
                write_traces(project, rows)
                return row
    raise ProjectError(f"trace {trace_id!r} not found")


def delete_trace(project: Project, trace_id: str) -> None:
    with traces_lock(project):
        rows = read_traces(project)
        remaining = [row for row in rows if row.get("id") != trace_id]
        if len(remaining) == len(rows):
            raise ProjectError(f"trace {trace_id!r} not found")
        write_traces(project, remaining)


def promote(project: Project, dataset: str, min_score: float | None = None, ids: list[str] | None = None, prefer_corrected: bool = True, preference: bool = False) -> int:
    """Turn traces into datapoints. The response (or your correction) becomes the assistant turn.

    With ``preference=True`` only corrected traces are used, and each becomes a DPO pair: the correction is the
    chosen answer and the model's original response is stored as ``rejected``.
    """
    chosen = []
    for row in read_traces(project):
        if ids and row.get("id") not in ids:
            continue
        if min_score is not None and (row.get("score") is None or row["score"] < min_score):
            continue
        if not ids and min_score is None and not preference:
            continue
        response = row.get("response", "")
        if preference:
            corrected = row.get("corrected") or ""
            if not corrected.strip() or not response.strip() or corrected.strip() == response.strip():
                continue
            chosen.append({"messages": [*row["messages"], {"role": "assistant", "content": corrected}], "rejected": response, "labels": ["trace", "preference"], "source": f"trace:{row.get('model', '')}"})
            continue
        answer = row.get("corrected") if prefer_corrected and row.get("corrected") else response
        if not answer:
            continue
        chosen.append({"messages": [*row["messages"], {"role": "assistant", "content": answer}], "labels": ["trace"], "source": f"trace:{row.get('model', '')}"})
    if not chosen:
        raise ProjectError("no corrected traces to pair" if preference else "no traces matched; pass --min-score or --id")
    add_datapoints(project, dataset, chosen)
    return len(chosen)
