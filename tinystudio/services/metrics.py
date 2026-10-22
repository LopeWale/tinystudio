"""Metrics: rubric rules judged by the helper LLM, deterministic code checks, or webhooks."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.core.project import ProjectError, check_name, iso
from tinystudio.db.models import Metric, Project
from tinystudio.evaluation.evaluate import BUILTIN_METRICS, CODE_CHECKS


def metric_dict(metric: Metric) -> dict[str, Any]:
    out = {"id": metric.id, "name": metric.name, "kind": metric.kind, "description": metric.description, "created": iso(metric.created), "updated": iso(metric.updated), **metric.spec}
    if metric.kind == "webhook" and isinstance(out.get("webhook"), dict):
        out["webhook"] = {k: ("set" if k == "token" else v) for k, v in out["webhook"].items()}  # never return the token
    return out


def get_metric(db: Session, project: Project, name: str) -> Metric:
    metric = db.scalar(select(Metric).where(Metric.project_id == project.id, Metric.name == name))
    if metric is None:
        raise ProjectError(f"metric {name!r} does not exist")
    return metric


def list_metrics(db: Session, project: Project) -> list[dict[str, Any]]:
    return [metric_dict(m) for m in db.scalars(select(Metric).where(Metric.project_id == project.id).order_by(Metric.created))]


def create_metric(db: Session, project: Project, name: str, kind: str, should: list[str] | None = None, should_not: list[str] | None = None, check: str | None = None, webhook_url: str | None = None, description: str = "", token: str | None = None) -> dict[str, Any]:
    check_name(name, "metric")
    if db.scalar(select(Metric.id).where(Metric.project_id == project.id, Metric.name == name)) is not None:
        raise ProjectError(f"metric {name!r} already exists")
    spec: dict[str, Any]
    if kind == "rubric":
        rules = {"should": [r.strip() for r in (should or []) if r.strip()], "should_not": [r.strip() for r in (should_not or []) if r.strip()]}
        if not rules["should"] and not rules["should_not"]:
            raise ProjectError("a rubric metric needs at least one should or should-not rule")
        spec = {"rules": rules}
    elif kind == "code":
        if check not in CODE_CHECKS:
            raise ProjectError(f"check must be one of {CODE_CHECKS}")
        spec = {"check": check}
    elif kind == "webhook":
        if not webhook_url:
            raise ProjectError("a webhook metric needs a url")
        spec = {"webhook": {"url": webhook_url, **({"token": token} if token else {})}}
    else:
        raise ProjectError("kind must be rubric, code, or webhook")
    metric = Metric(project_id=project.id, name=name, kind=kind, description=description, spec=spec)
    db.add(metric)
    db.flush()
    return metric_dict(metric)


def update_metric(db: Session, project: Project, name: str, should: list[str] | None = None, should_not: list[str] | None = None, description: str | None = None) -> dict[str, Any]:
    metric = get_metric(db, project, name)
    if metric.kind == "rubric" and (should is not None or should_not is not None):
        current = metric.spec.get("rules", {"should": [], "should_not": []})
        rules = {"should": [r.strip() for r in (should if should is not None else current["should"]) if r.strip()], "should_not": [r.strip() for r in (should_not if should_not is not None else current["should_not"]) if r.strip()]}
        if not rules["should"] and not rules["should_not"]:
            raise ProjectError("a rubric metric needs at least one rule")
        metric.spec = {**metric.spec, "rules": rules}
    if description is not None:
        metric.description = description
    return metric_dict(metric)


def delete_metric(db: Session, project: Project, name: str) -> None:
    db.delete(get_metric(db, project, name))
    db.flush()


def ensure_builtin_metrics(db: Session, project: Project) -> None:
    """Studio's three default rubric metrics, once per project."""
    existing = {m.name for m in db.scalars(select(Metric).where(Metric.project_id == project.id))}
    for name, spec in BUILTIN_METRICS.items():
        if name not in existing:
            create_metric(db, project, name, "rubric", spec["should"], spec["should_not"], description=spec["description"])


def judge_spec(metric: Metric) -> dict[str, Any]:
    """The metric as the evaluator needs it (with the webhook token)."""
    return {"name": metric.name, "kind": metric.kind, **metric.spec}
