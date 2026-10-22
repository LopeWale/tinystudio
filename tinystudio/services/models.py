"""Model ids -> weights: base models from the Hub cache, fine-tuned experiments and their checkpoints from the data directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.core.project import SAFE_NAME, ProjectError
from tinystudio.db import session
from tinystudio.db.models import Experiment, Job, Project
from tinystudio.serving.runner import ModelRunner, ResolvedModel
from tinystudio.services.jobs import exp_path
from tinystudio.training.train import parse_alias
from tinystudio.training.recommend import catalog_as_dicts


def resolve(db: Session, project: Project | None, model_id: str) -> ResolvedModel:
    """A model directory, ``job/number[@step]`` or ``ft-job-number`` inside the project, or a Hub id."""
    local = Path(model_id).expanduser()
    if local.is_dir() and (local / "config.json").exists():
        return ResolvedModel(model_id, str(local), None, local, "full")
    if project is not None:
        alias = parse_alias(model_id)
        reference = f"{alias[0]}/{alias[1]}" if alias else model_id
        job_name, _, rest = reference.partition("/")
        number, _, step = rest.partition("@")
        if number.isdigit() and SAFE_NAME.match(job_name):
            exp = db.scalar(select(Experiment).join(Job).where(Job.project_id == project.id, Job.name == job_name, Experiment.number == int(number)))
            if exp is not None:
                return _resolve_experiment(exp, model_id, step)
            if alias is not None:
                raise ProjectError(f"experiment {model_id} does not exist")
    return ResolvedModel(model_id, model_id, None, None, "base")


def _resolve_experiment(exp: Experiment, model_id: str, step: str) -> ResolvedModel:
    path = exp_path(exp)
    base = (exp.status or {}).get("weights_source") or exp.config["base_model"]
    if step:
        if not step.isdigit():
            raise ProjectError(f"{model_id}: a checkpoint reference is job/number@step")
        ck = path / "checkpoints" / f"checkpoint-{step}"
        if not ck.is_dir():
            raise ProjectError(f"experiment {exp.alias} has no checkpoint at step {step}; train with checkpoint_every_steps")
        if (ck / "adapter_config.json").exists():
            return ResolvedModel(model_id, base, ck, None, "lora")
        return ResolvedModel(model_id, base, None, ck, "full")
    if exp.state != "succeeded":
        raise ProjectError(f"experiment {model_id} is in state {exp.state!r}; only succeeded experiments can be loaded")
    if (path / "adapter").exists():
        return ResolvedModel(model_id, base, path / "adapter", None, "lora")
    if (path / "model").exists():
        return ResolvedModel(model_id, base, None, path / "model", "full")
    raise ProjectError(f"experiment {model_id} has no adapter/ or model/ directory")


def deployable_ids(db: Session, project: Project) -> list[str]:
    return [f"{exp.job.name}/{exp.number}" for exp in db.scalars(select(Experiment).join(Job).where(Job.project_id == project.id, Experiment.state == "succeeded").order_by(Job.created, Experiment.number))]


def model_rows(db: Session, project: Project, loaded: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for exp in db.scalars(select(Experiment).join(Job).where(Job.project_id == project.id, Experiment.state == "succeeded").order_by(Job.created, Experiment.number)):
        model_id = f"{exp.job.name}/{exp.number}"
        status = exp.status or {}
        rows.append({"id": model_id, "alias": exp.alias, "kind": "fine-tuned", "base_model": exp.config["base_model"], "training_type": exp.config["training_type"], "parameters": status.get("total_parameters"), "created": status.get("created"), "status": "up" if (model_id in loaded or exp.alias in loaded) else "down"})
    for entry in catalog_as_dicts():
        rows.append({"id": entry["id"], "alias": entry["id"], "kind": "base", "base_model": entry["id"], "training_type": None, "parameters": int(entry["params_b"] * 1e9), "created": None, "status": "up" if entry["id"] in loaded else "down", "license": entry["license"], "notes": entry["notes"]})
    return rows


def load_runner_factory(project_id: str | None) -> Any:
    """``load(model_id) -> ModelRunner`` that resolves ids through a fresh session (for long worker jobs)."""

    def load(model_id: str) -> ModelRunner:
        with session() as db:
            project = db.get(Project, project_id) if project_id else None
            resolved = resolve(db, project, model_id)
        return ModelRunner(resolved)

    return load
