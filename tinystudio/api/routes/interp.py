"""Project-scoped routes: interpretability captures, comparisons, drift, and autopilot cycles."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from tinystudio import worker
from tinystudio.api.deps import current_user, get_db, project_dep, wrap
from tinystudio.auth import Principal
from tinystudio.core.project import SAFE_NAME
from tinystudio.db.models import Project
from tinystudio.interp import neurons as interp_mod
from tinystudio.services import autopilot as autopilot_svc
from tinystudio.services import datasets as datasets_svc
from tinystudio.services import jobs as jobs_svc
from tinystudio.services import snapshots as snapshots_svc
from tinystudio.services.interp import overview, store_for

router = APIRouter(prefix="/api/p/{slug}", tags=["interp"])


class InterpCaptureBody(BaseModel):
    model: str
    dataset: str | None = None
    snapshot: str | None = None
    layers: list[int] = []
    topk: int = Field(40, ge=5, le=500)
    name: str | None = None


class InterpCheckpointsBody(BaseModel):
    job: str
    number: int = Field(1, ge=1)
    dataset: str | None = None
    snapshot: str | None = None
    layers: list[int] = []
    topk: int = Field(40, ge=5, le=500)


class InterpExplainBody(BaseModel):
    neurons: list[int] = []
    top: int = Field(0, ge=0, le=500)
    sort: str = "max"


class InterpTestBody(BaseModel):
    neurons: list[int]


class InterpDiffBody(BaseModel):
    experiment: str
    base: str | None = None
    dataset: str | None = None
    snapshot: str | None = None
    layers: list[int] = []
    name: str | None = None


class AutopilotBody(BaseModel):
    name: str
    dataset: str
    base_model: str | None = None
    training_type: str = "lora"
    epochs: float = Field(2.0, ge=0.1, le=50)
    learning_rate: float | None = Field(None, gt=0, le=1)
    min_trace_score: float | None = Field(None, ge=0, le=1)
    preference: bool = False
    clean: bool = True
    train_pct: int = Field(85, ge=50, le=99)
    metrics: list[str] = []
    eval_limit: int | None = Field(None, ge=1)
    max_steps: int = -1


def _check_source(db: Session, project: Project, dataset: str | None, snapshot: str | None) -> None:
    if dataset:
        wrap(lambda: datasets_svc.get_dataset(db, project, dataset))
    elif snapshot:
        wrap(lambda: snapshots_svc.get_snapshot(db, project, snapshot))
    else:
        raise HTTPException(400, "pass a dataset or a snapshot")


def _check_name(name: str | None) -> None:
    if name and not SAFE_NAME.match(name):
        raise HTTPException(400, "name may contain letters, digits, spaces, dots, dashes, and underscores")


@router.get("/interp")
def interp_overview(project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return overview(db, project)


@router.post("/interp/captures")
def interp_capture(body: InterpCaptureBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    _check_source(db, project, body.dataset, body.snapshot)
    _check_name(body.name)
    return wrap(lambda: worker.enqueue(db, "interp.capture", body.model_dump(), project, principal.user))


@router.post("/interp/checkpoint-captures")
def interp_checkpoint_captures(body: InterpCheckpointsBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    wrap(lambda: jobs_svc.get_experiment(db, project, body.job, body.number))
    _check_source(db, project, body.dataset, body.snapshot)
    return wrap(lambda: worker.enqueue(db, "interp.capture_checkpoints", body.model_dump(), project, principal.user))


@router.get("/interp/captures/{name}")
def interp_capture_meta(name: str, project: Project = Depends(project_dep)) -> dict[str, Any]:
    return wrap(lambda: interp_mod.capture_meta(store_for(project), name))


@router.delete("/interp/captures/{name}")
def interp_capture_delete(name: str, project: Project = Depends(project_dep)) -> dict[str, Any]:
    wrap(lambda: interp_mod.delete_capture(store_for(project), name))
    return {"deleted": name}


@router.get("/interp/captures/{name}/layers/{layer}")
def interp_neurons(name: str, layer: int, sort: str = "max", limit: int = 100, offset: int = 0, q: str = "", project: Project = Depends(project_dep)) -> dict[str, Any]:
    return wrap(lambda: interp_mod.neuron_table(store_for(project), name, layer, sort=sort, limit=max(1, min(limit, 2000)), offset=max(0, offset), query=q))


@router.get("/interp/captures/{name}/layers/{layer}/neurons/{neuron}")
def interp_neuron(name: str, layer: int, neuron: int, limit: int = 20, project: Project = Depends(project_dep)) -> dict[str, Any]:
    return wrap(lambda: interp_mod.neuron_view(store_for(project), name, layer, neuron, limit=max(1, min(limit, 200))))


@router.post("/interp/captures/{name}/layers/{layer}/explain")
def interp_explain(name: str, layer: int, body: InterpExplainBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    wrap(lambda: interp_mod.capture_meta(store_for(project), name))
    if not body.neurons and not body.top:
        raise HTTPException(400, "pass neurons or top")
    return wrap(lambda: worker.enqueue(db, "interp.explain", {"capture": name, "layer": layer, **body.model_dump()}, project, principal.user))


@router.post("/interp/captures/{name}/layers/{layer}/test")
def interp_test(name: str, layer: int, body: InterpTestBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    wrap(lambda: interp_mod.capture_meta(store_for(project), name))
    if not body.neurons:
        raise HTTPException(400, "pass at least one neuron")
    return wrap(lambda: worker.enqueue(db, "interp.test", {"capture": name, "layer": layer, "neurons": body.neurons}, project, principal.user))


@router.post("/interp/diffs")
def interp_diff(body: InterpDiffBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    _check_source(db, project, body.dataset, body.snapshot)
    _check_name(body.name)
    return wrap(lambda: worker.enqueue(db, "interp.diff", body.model_dump(), project, principal.user))


@router.get("/interp/diffs/{name}")
def interp_diff_meta(name: str, project: Project = Depends(project_dep)) -> dict[str, Any]:
    return wrap(lambda: interp_mod.diff_meta(store_for(project), name))


@router.delete("/interp/diffs/{name}")
def interp_diff_delete(name: str, project: Project = Depends(project_dep)) -> dict[str, Any]:
    wrap(lambda: interp_mod.delete_diff(store_for(project), name))
    return {"deleted": name}


@router.get("/interp/diffs/{name}/layers/{layer}")
def interp_diff_layer(name: str, layer: int, limit: int = 50, sort: str = "change", project: Project = Depends(project_dep)) -> dict[str, Any]:
    return wrap(lambda: interp_mod.diff_layer(store_for(project), name, layer, limit=max(1, min(limit, 2000)), sort=sort))


@router.get("/interp/diffs/{name}/layers/{layer}/neurons/{neuron}")
def interp_diff_neuron(name: str, layer: int, neuron: int, project: Project = Depends(project_dep)) -> dict[str, Any]:
    return wrap(lambda: interp_mod.diff_neuron(store_for(project), name, layer, neuron))


@router.get("/interp/drift/{job}/{number}")
def interp_drift(job: str, number: int, source: str, layer: int, neuron: int, project: Project = Depends(project_dep)) -> dict[str, Any]:
    return wrap(lambda: interp_mod.drift(store_for(project), job, number, source, layer, neuron))


# ---------------------------------------------------------------- autopilot
@router.get("/autopilot")
def autopilot_list(project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return autopilot_svc.list_histories(db, project)


@router.get("/autopilot/{name}")
def autopilot_history(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return autopilot_svc.history(db, project, name)


@router.post("/autopilot")
def autopilot_run(body: AutopilotBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    _check_name(body.name)
    wrap(lambda: datasets_svc.get_dataset(db, project, body.dataset))
    if body.training_type not in ("lora", "full", "dpo"):
        raise HTTPException(400, "training_type must be lora, full, or dpo")
    return wrap(lambda: worker.enqueue(db, "autopilot.run", body.model_dump(), project, principal.user))
