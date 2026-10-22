"""Traces: inferences logged by the inference service and the playground, scorable and promotable into datasets."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from tinystudio.core.project import ProjectError, iso
from tinystudio.db.models import Project, Trace
from tinystudio.services import datasets as datasets_svc


def trace_dict(trace: Trace) -> dict[str, Any]:
    out = {"id": trace.id, "time": iso(trace.time), "model": trace.model, "messages": trace.messages, "response": trace.response, "seconds": trace.seconds, "prompt_tokens": trace.prompt_tokens, "completion_tokens": trace.completion_tokens, "score": trace.score}
    for key in ("note", "corrected", "origin", "document_chunks"):
        value = getattr(trace, key)
        if value is not None:
            out[key] = value
    return out


def append_trace(db: Session, project: Project, record: dict[str, Any]) -> dict[str, Any]:
    time_value = record.get("time")
    when = datetime.fromisoformat(time_value) if isinstance(time_value, str) else datetime.now(timezone.utc)
    chunks = record.get("document_chunks")
    trace = Trace(id=str(record["id"]), project_id=project.id, time=when, model=str(record.get("model", "")), messages=record.get("messages", []), response=str(record.get("response", "")), seconds=record.get("seconds"), prompt_tokens=record.get("prompt_tokens"), completion_tokens=record.get("completion_tokens"), score=record.get("score"), note=record.get("note"), corrected=record.get("corrected"), origin=record.get("origin"), document_chunks=chunks if isinstance(chunks, list) else ([{"count": chunks}] if isinstance(chunks, int) and chunks else None))
    db.add(trace)
    db.flush()
    return trace_dict(trace)


def list_traces(db: Session, project: Project, limit: int = 100, offset: int = 0, newest_first: bool = True) -> list[dict[str, Any]]:
    order = Trace.time.desc() if newest_first else Trace.time
    return [trace_dict(t) for t in db.scalars(select(Trace).where(Trace.project_id == project.id).order_by(order, Trace.id).offset(offset).limit(limit))]


def get_trace(db: Session, project: Project, trace_id: str) -> Trace:
    trace = db.scalar(select(Trace).where(Trace.project_id == project.id, Trace.id == trace_id))
    if trace is None:
        raise ProjectError(f"trace {trace_id!r} not found")
    return trace


def score_trace(db: Session, project: Project, trace_id: str, score: float, note: str = "", corrected: str | None = None) -> dict[str, Any]:
    trace = get_trace(db, project, trace_id)
    trace.score = score
    if note:
        trace.note = note
    if corrected is not None:
        trace.corrected = corrected
    return trace_dict(trace)


def delete_trace(db: Session, project: Project, trace_id: str) -> None:
    db.delete(get_trace(db, project, trace_id))
    db.flush()


def promote(db: Session, project: Project, dataset: str, min_score: float | None = None, ids: list[str] | None = None, prefer_corrected: bool = True, preference: bool = False) -> int:
    """Turn traces into datapoints. With ``preference`` only corrected traces are used, each as a DPO pair."""
    query = select(Trace).where(Trace.project_id == project.id).order_by(Trace.time)
    if ids:
        query = query.where(Trace.id.in_(ids))
    chosen = []
    for row in db.scalars(query):
        if min_score is not None and (row.score is None or row.score < min_score):
            continue
        if not ids and min_score is None and not preference:
            continue
        response = row.response or ""
        if preference:
            corrected = row.corrected or ""
            if not corrected.strip() or not response.strip() or corrected.strip() == response.strip():
                continue
            chosen.append({"messages": [*row.messages, {"role": "assistant", "content": corrected}], "rejected": response, "labels": ["trace", "preference"], "source": f"trace:{row.model}"})
            continue
        answer = row.corrected if prefer_corrected and row.corrected else response
        if not answer:
            continue
        chosen.append({"messages": [*row.messages, {"role": "assistant", "content": answer}], "labels": ["trace"], "source": f"trace:{row.model}"})
    if not chosen:
        raise ProjectError("no corrected traces to pair" if preference else "no traces matched; pass min_score or ids")
    datasets_svc.add_rows(db, project, dataset, chosen)
    return len(chosen)


def stats(db: Session, project: Project) -> dict[str, Any]:
    total = int(db.scalar(select(func.count()).select_from(Trace).where(Trace.project_id == project.id)) or 0)
    tokens = db.execute(select(func.coalesce(func.sum(Trace.prompt_tokens), 0), func.coalesce(func.sum(Trace.completion_tokens), 0), func.count(Trace.score)).where(Trace.project_id == project.id)).one()
    per_day = Counter()
    per_model: Counter[str] = Counter()
    day = cast(Trace.time, Date)
    for d, model, n in db.execute(select(day, Trace.model, func.count()).where(Trace.project_id == project.id).group_by(day, Trace.model)).all():
        per_day[str(d)] += int(n)
        per_model[model] += int(n)
    return {"requests": total, "prompt_tokens": int(tokens[0]), "completion_tokens": int(tokens[1]), "scored_traces": int(tokens[2]), "requests_per_day": sorted(per_day.items()), "requests_per_model": per_model.most_common(10)}
