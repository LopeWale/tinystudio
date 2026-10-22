"""Projects: rows that scope datasets, jobs, evaluations, traces, repositories, and captures."""

from __future__ import annotations

import shutil
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tinystudio.config import config
from tinystudio.core.project import ProjectError, check_name, iso, slugify
from tinystudio.db.models import Dataset, Evaluation, Job, Metric, Project, Repository, Snapshot, User


def project_dict(project: Project) -> dict[str, Any]:
    return {"id": project.id, "slug": project.slug, "name": project.name, "goal": project.goal, "created": iso(project.created)}


def list_projects(db: Session) -> list[dict[str, Any]]:
    rows = []
    for project in db.scalars(select(Project).order_by(Project.created)):
        rows.append({**project_dict(project), "datasets": db.scalar(select(func.count()).select_from(Dataset).where(Dataset.project_id == project.id)) or 0, "jobs": db.scalar(select(func.count()).select_from(Job).where(Job.project_id == project.id)) or 0})
    return rows


def get_project(db: Session, slug: str) -> Project:
    project = db.scalar(select(Project).where(Project.slug == slug))
    if project is None:
        raise ProjectError(f"project {slug!r} does not exist")
    return project


def create_project(db: Session, name: str, goal: str = "", user: User | None = None) -> Project:
    check_name(name, "project")
    slug = slugify(name)
    if db.scalar(select(Project.id).where(Project.slug == slug)) is not None:
        raise ProjectError(f"a project with slug {slug!r} already exists")
    project = Project(slug=slug, name=name.strip(), goal=goal.strip(), created_by=user.id if user else None)
    db.add(project)
    db.flush()
    from tinystudio.services.metrics import ensure_builtin_metrics

    ensure_builtin_metrics(db, project)
    return project


def update_project(db: Session, project: Project, name: str | None = None, goal: str | None = None) -> Project:
    if name is not None:
        project.name = check_name(name, "project").strip()
    if goal is not None:
        project.goal = goal.strip()
    return project


def delete_project(db: Session, project: Project) -> None:
    """Delete the rows (cascade), the repositories' vector collections, and the artifact directory."""
    from tinystudio import vectors

    for repository in db.scalars(select(Repository).where(Repository.project_id == project.id)):
        try:
            vectors.drop_collection(repository.collection)
        except Exception:  # noqa: BLE001 - the vector store may be down; rows still go
            pass
    artifact_dir = config().project_dir(project.id)
    db.delete(project)
    db.flush()
    shutil.rmtree(artifact_dir, ignore_errors=True)


def tree(db: Session, project: Project) -> dict[str, Any]:
    """Datasets with their snapshots and the jobs on each snapshot (Studio's project tree)."""
    from tinystudio.services.datasets import list_datasets
    from tinystudio.services.jobs import job_dict
    from tinystudio.services.snapshots import snapshot_dict

    jobs = [job_dict(job) for job in db.scalars(select(Job).where(Job.project_id == project.id).order_by(Job.created))]
    snapshots = [snapshot_dict(s) for s in db.scalars(select(Snapshot).where(Snapshot.project_id == project.id).order_by(Snapshot.created))]
    by_dataset: dict[str, list[dict[str, Any]]] = {}
    for snap in snapshots:
        by_dataset.setdefault(snap["dataset"], []).append({**snap, "jobs": [job for job in jobs if job["snapshot"] == snap["name"]]})
    datasets = list_datasets(db, project)
    known = {d["name"] for d in datasets}
    out = [{**d, "snapshots": by_dataset.get(d["name"], [])} for d in datasets]
    for name, snaps in by_dataset.items():
        if name not in known:
            out.append({"name": name, "missing": True, "datapoints": 0, "buckets": {"uncategorized": 0, "training": 0, "validation": 0}, "snapshots": snaps})
    return {"project": project_dict(project), "datasets": out}


def overview(db: Session, project: Project) -> dict[str, Any]:
    from tinystudio.services.evals import eval_dict
    from tinystudio.services.metrics import metric_dict
    from tinystudio.training.recommend import catalog_as_dicts

    return {
        "project": project_dict(project),
        "tree": tree(db, project),
        "metrics": [metric_dict(m) for m in db.scalars(select(Metric).where(Metric.project_id == project.id).order_by(Metric.created))],
        "evals": [eval_dict(e) for e in db.scalars(select(Evaluation).where(Evaluation.project_id == project.id).order_by(Evaluation.created))],
        "catalog": catalog_as_dicts(),
        "llm": {"base_url": __import__("os").environ.get("TINYSTUDIO_LLM_BASE_URL"), "model": __import__("os").environ.get("TINYSTUDIO_LLM_MODEL")},
    }
