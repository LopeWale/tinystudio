"""Projects: rows that scope datasets, jobs, evaluations, traces, repositories, and captures."""

from __future__ import annotations

import shutil
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from tinystudio.config import config
from tinystudio.core.project import ProjectError, check_name, iso, slugify
from tinystudio.db.models import Dataset, Evaluation, Job, Metric, Project, Repository, Snapshot, Task, User


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
    """Delete idle project rows before removing their external artifacts."""
    from tinystudio import vectors
    from tinystudio.lab.models import LabAttempt, LabOperation, LabRun
    from tinystudio.storage import project_key, storage

    # The project lock fences new project-scoped inserts through their FK;
    # work-row locks also prevent existing work from starting during deletion.
    project_id = project.id
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise ProjectError("project no longer exists")
    run_scope = select(LabRun.id).where(LabRun.project_id == project_id)
    terminal_lab = {"succeeded", "failed", "cancelled", "lost", "recovery_required"}
    work = (
        ("task", Task, Task.project_id == project_id, {"succeeded", "failed", "stopped"}),
        ("laboratory run", LabRun, LabRun.project_id == project_id, terminal_lab),
        ("laboratory attempt", LabAttempt, LabAttempt.run_id.in_(run_scope), terminal_lab),
        ("laboratory operation", LabOperation, LabOperation.project_id == project_id, terminal_lab),
    )
    identities: dict[type[Any], list[str]] = {}
    for label, model, scope, terminal in work:
        rows = list(db.execute(select(model.id, model.state).where(scope).with_for_update()))
        if any(row.state not in terminal for row in rows):
            raise ProjectError(f"project has active {label} work; stop or finish it before deleting the project")
        identities[model] = [row.id for row in rows]

    collections = list(db.scalars(select(Repository.collection).where(Repository.project_id == project_id)))
    prefixes = [project_key(project_id, category) for category in
                ("snapshots", "models", "captures", "retained-captures", "sources", "uploads", "lab")]
    prefixes.extend(f"tasks/{task_id}" for task_id in identities[Task])
    artifact_dir = config().project_dir(project_id)

    # Attempts restrict target deletion, and runs restrict definitions/targets.
    # A single run DELETE also handles same-project parent_run_id references at
    # PostgreSQL's statement boundary; cross-project references must still fail.
    db.execute(delete(LabAttempt).where(LabAttempt.run_id.in_(identities[LabRun])))
    db.execute(delete(LabRun).where(LabRun.id.in_(identities[LabRun])))
    db.delete(project)
    db.flush()

    # Never destroy remote/vector artifacts before relational FK checks pass.
    for collection in collections:
        try:
            vectors.drop_collection(collection)
        except Exception:  # noqa: BLE001 - the vector store may be down; rows still go
            pass
    backend = storage()
    if backend.remote:
        for prefix in prefixes:
            backend.delete_prefix(prefix)
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
