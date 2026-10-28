"""Project-scoped routes: jobs, experiments, analysis, recommendations, metrics, evaluations."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from tinystudio import worker
from tinystudio.api.deps import current_user, get_db, project_dep, wrap
from tinystudio.auth import Principal
from tinystudio.db.models import Project
from tinystudio.services import evals as evals_svc
from tinystudio.services import jobs as jobs_svc
from tinystudio.services import metrics as metrics_svc
from tinystudio.services import snapshots as snapshots_svc
from tinystudio.training.recommend import recommend
from tinystudio.training.train import TrainConfig, qlora_support

router = APIRouter(prefix="/api/p/{slug}", tags=["training"])


class JobBody(BaseModel):
    name: str
    snapshot: str
    goal: str = ""
    reasoning: bool = False


class ExperimentSpec(BaseModel):
    base_model: str
    training_type: str | None = None
    objective: str | None = None
    update_strategy: str | None = None
    data_format: str | None = None
    device: str | None = None
    dtype: str | None = None
    epochs: float = Field(3, ge=0.1, le=50)
    learning_rate: float = Field(2e-4, gt=0, le=1)
    batch_size: int = Field(8, ge=1, le=64)
    gradient_accumulation_steps: int = Field(2, ge=1, le=64)
    max_length: int = Field(512, ge=64, le=8192)
    lora_r: int = Field(16, ge=1, le=256)
    lora_alpha: int = Field(32, ge=1, le=512)
    assistant_only_loss: bool = False
    refinetune_from: str | None = None
    checkpoint_every_steps: int = Field(0, ge=0, le=100000)
    keep_checkpoints: int = Field(0, ge=0, le=1000)
    max_steps: int = -1


class ExperimentsBody(BaseModel):
    experiments: list[ExperimentSpec]


class MetricBody(BaseModel):
    name: str
    kind: str = "rubric"
    should: list[str] = []
    should_not: list[str] = []
    check: str | None = None
    url: str | None = None
    token: str | None = None
    description: str = ""


class MetricUpdateBody(BaseModel):
    should: list[str] | None = None
    should_not: list[str] | None = None
    description: str | None = None


class GenerateRulesBody(BaseModel):
    name: str
    snapshot: str
    goal: str = ""
    description: str = ""


class EvalBody(BaseModel):
    name: str
    snapshot: str
    models: list[str]
    metrics: list[str]
    limit: int | None = None
    max_new_tokens: int = 256


class ByoeBody(BaseModel):
    name: str
    snapshot: str
    models: list[str]
    url: str
    token: str | None = None
    limit: int | None = None


# ---------------------------------------------------------------- jobs
@router.get("/jobs")
def jobs(project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return jobs_svc.list_jobs(db, project)


@router.post("/jobs")
def new_job(body: JobBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: jobs_svc.create_job(db, project, body.name, body.snapshot, body.goal, body.reasoning))


@router.get("/jobs/{job}")
def job_detail(job: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    from tinystudio.inference.engine import engine

    row = wrap(lambda: jobs_svc.get_job(db, project, job))
    loaded = engine().loaded_ids(project.slug)
    experiments = []
    for exp in row.experiments:
        item = jobs_svc.experiment_dict(exp, row.name)
        item["loss"] = jobs_svc.loss_curve(db, exp)
        item["up"] = item["model_id"] in loaded or exp.alias in loaded
        experiments.append(item)
    snapshot = None
    try:
        snapshot = snapshots_svc.snapshot_dict(snapshots_svc.get_snapshot(db, project, row.snapshot_name))
    except Exception:  # noqa: BLE001 - the snapshot may have been deleted
        pass
    return {"job": jobs_svc.job_dict(row), "experiments": experiments, "analysis": jobs_svc.get_analysis(db, row), "snapshot": snapshot}


@router.post("/jobs/{job}/experiments")
def add_experiments(job: str, body: ExperimentsBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not body.experiments:
        raise HTTPException(status_code=400, detail="add at least one experiment")
    numbers = []
    for spec in body.experiments:
        cfg = wrap(lambda spec=spec: TrainConfig(**spec.model_dump()))
        if cfg.update_strategy == "qlora":
            ok, reason = qlora_support(cfg.device)
            if not ok:
                raise HTTPException(status_code=400, detail=reason)
        numbers.append(wrap(lambda cfg=cfg: jobs_svc.add_experiment(db, project, job, cfg)).number)
    return {"job": job, "experiments": numbers}


@router.post("/jobs/{job}/start")
def start_job(job: str, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    row = wrap(lambda: jobs_svc.get_job(db, project, job))
    if not any(e.state == "pending" for e in row.experiments):
        raise HTTPException(status_code=400, detail="no pending experiments; add one first")
    task = wrap(lambda: worker.enqueue(db, "job.run", {"job_id": row.id, "job": row.name}, project, principal.user))
    for exp in row.experiments:
        if exp.state == "pending":
            exp.task_id = task["id"]
    return task


@router.post("/jobs/{job}/analyze")
def analyze(job: str, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    wrap(lambda: jobs_svc.get_job(db, project, job))
    return wrap(lambda: worker.enqueue(db, "job.analyze", {"job": job}, project, principal.user))


@router.delete("/jobs/{job}")
def remove_job(job: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    wrap(lambda: jobs_svc.delete_job(db, project, job))
    return {"deleted": job}


@router.get("/jobs/{job}/experiments/{number}")
def experiment(job: str, number: int, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    exp = wrap(lambda: jobs_svc.get_experiment(db, project, job, number))
    item = jobs_svc.experiment_dict(exp, job)
    return {"status": item, "config": exp.config, "loss": jobs_svc.loss_curve(db, exp)}


@router.delete("/jobs/{job}/experiments/{number}")
def remove_experiment(job: str, number: int, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    wrap(lambda: jobs_svc.delete_experiment(db, project, job, number))
    return {"deleted": f"{job}/{number}"}


@router.get("/jobs/{job}/experiments/{number}/checkpoints")
def experiment_checkpoints(job: str, number: int, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return wrap(lambda: jobs_svc.checkpoints(db, project, job, number))


@router.get("/jobs/{job}/experiments/{number}/download")
def download_experiment(job: str, number: int, background: BackgroundTasks, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> Response:
    exp = wrap(lambda: jobs_svc.get_experiment(db, project, job, number))
    path = jobs_svc.ensure_experiment_artifacts(exp)
    source = path / "adapter" if (path / "adapter").exists() else path / "model"
    if not source.exists():
        raise HTTPException(status_code=404, detail="experiment has no weights yet")
    tmp = Path(tempfile.mkdtemp(prefix="tinystudio-zip-"))
    try:
        archive = Path(shutil.make_archive(str(tmp / f"ft-{job}-{number}-{uuid.uuid4().hex[:6]}"), "zip", root_dir=source))
        from tinystudio.storage import project_key, storage

        store = storage()
        if store.remote:
            key = project_key(project.id, "models", exp.id, "downloads", "weights.zip")
            store.upload_file(archive, key, content_type="application/zip")
            response = RedirectResponse(store.presign(key), status_code=303)
            shutil.rmtree(tmp, ignore_errors=True)
            return response
        background.add_task(shutil.rmtree, tmp, True)
        return FileResponse(archive, filename=f"ft-{job}-{number}.zip", media_type="application/zip")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


@router.get("/recommend/{snapshot}")
def recommendation(snapshot: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    train_rows, _ = wrap(lambda: snapshots_svc.load_snapshot(db, project, snapshot))
    return recommend(train_rows, project.goal)


# ---------------------------------------------------------------- metrics
@router.get("/metrics")
def metrics(project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return metrics_svc.list_metrics(db, project)


@router.post("/metrics")
def metric(body: MetricBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: metrics_svc.create_metric(db, project, body.name, body.kind, body.should, body.should_not, body.check, body.url, body.description, body.token))


@router.post("/metrics/generate")
def metric_generate(body: GenerateRulesBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    wrap(lambda: snapshots_svc.get_snapshot(db, project, body.snapshot))
    return wrap(lambda: worker.enqueue(db, "metric.generate", body.model_dump(), project, principal.user))


@router.put("/metrics/{name}")
def edit_metric(name: str, body: MetricUpdateBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: metrics_svc.update_metric(db, project, name, body.should, body.should_not, body.description))


@router.delete("/metrics/{name}")
def remove_metric(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    wrap(lambda: metrics_svc.delete_metric(db, project, name))
    return {"deleted": name}


# ---------------------------------------------------------------- evaluations
@router.get("/evals")
def evals(project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return evals_svc.list_evals(db, project)


@router.post("/evals")
def run_eval(body: EvalBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    evaluation = wrap(lambda: evals_svc.prepare_eval(db, project, body.name, body.snapshot, body.models, body.metrics, body.limit))
    task = wrap(lambda: worker.enqueue(db, "eval.run", {"evaluation_id": evaluation.id, "name": body.name, "limit": body.limit, "max_new_tokens": body.max_new_tokens}, project, principal.user))
    evaluation.task_id = task["id"]
    return task


@router.post("/evals/byoe")
def byoe(body: ByoeBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    metric_name = f"byoe-{body.name}"
    try:
        metrics_svc.get_metric(db, project, metric_name)
    except Exception:  # noqa: BLE001 - create on first use
        wrap(lambda: metrics_svc.create_metric(db, project, metric_name, "webhook", webhook_url=body.url, description=f"BYOE evaluator for {body.name}", token=body.token))
    evaluation = wrap(lambda: evals_svc.prepare_eval(db, project, body.name, body.snapshot, body.models, [metric_name], body.limit))
    task = wrap(lambda: worker.enqueue(db, "eval.run", {"evaluation_id": evaluation.id, "name": body.name, "limit": body.limit, "max_new_tokens": 256}, project, principal.user))
    evaluation.task_id = task["id"]
    return task


@router.get("/evals/{name}")
def eval_detail(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    evaluation = wrap(lambda: evals_svc.get_eval(db, project, name))
    return {"summary": evals_svc.eval_dict(evaluation), "rows": evals_svc.eval_rows(db, evaluation)}


@router.delete("/evals/{name}")
def remove_eval(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    wrap(lambda: evals_svc.delete_eval(db, project, name))
    return {"deleted": name}
