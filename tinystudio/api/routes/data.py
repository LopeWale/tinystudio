"""Project-scoped routes: datasets, datapoints, snapshots, cleaning, synthetic data."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from tinystudio import worker
from tinystudio.api.deps import current_user, get_db, project_dep, wrap
from tinystudio.auth import Principal
from tinystudio.config import config
from tinystudio.core.quality import CleanSpec
from tinystudio.db.models import Project
from tinystudio.services import datasets as datasets_svc
from tinystudio.services import snapshots as snapshots_svc
from tinystudio.services import traces as traces_svc

router = APIRouter(prefix="/api/p/{slug}", tags=["data"])


class SplitBody(BaseModel):
    train_pct: int = Field(80, ge=1, le=99)
    seed: int = 0
    synthetic_in_validation: bool = False


class SnapshotBody(BaseModel):
    dataset: str
    name: str


class DatapointBody(BaseModel):
    dataset: str
    messages: list[dict[str, str]]
    bucket: str = "uncategorized"


class RowsBody(BaseModel):
    rows: list[dict[str, Any]]
    bucket: str | None = None


class DeleteRowsBody(BaseModel):
    ids: list[int]


class BucketBody(BaseModel):
    ids: list[int]
    bucket: str


class AugmentBody(BaseModel):
    dataset: str
    count: int = Field(20, ge=1, le=2000)
    instructions: str = ""
    avoid: str = ""
    temperature: float = Field(0.8, ge=0.0, le=1.5)


class LabelsBody(BaseModel):
    labels: list[dict[str, str]]


class FromPathBody(BaseModel):
    name: str
    path: str


class SyntheticSpecBody(BaseModel):
    name: str
    files: list[str] = []
    urls: list[str] = []
    pairs_per_source: int = Field(25, ge=1, le=500)
    temperature: float = Field(0.3, ge=0.0, le=1.0)
    goal: str = ""
    rules: list[str] = []
    question_format: str = ""
    answer_format: str = ""
    example_questions: list[str] = []
    example_answers: list[str] = []
    system_prompt: str | None = None
    curate_threshold: float = Field(0.0, ge=0.0, le=10.0)


class CleanBody(BaseModel):
    dedupe_user: bool = False
    dedupe_conversation: bool = False
    drop_near_duplicates: bool = False
    near_duplicate_threshold: float = Field(0.7, ge=0.3, le=1.0)
    drop_empty: bool = False
    trim_whitespace: bool = False
    min_total_chars: int | None = None
    max_total_chars: int | None = None
    min_rating: float | None = None
    require_json_answer: bool = False
    drop_labels: list[str] = []
    keep_labels: list[str] = []
    drop_indices: list[int] = []
    redact_pii: bool = False


class ScoreBody(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    note: str = ""
    corrected: str | None = None


class PromoteBody(BaseModel):
    dataset: str
    min_score: float | None = None
    ids: list[str] | None = None
    preference: bool = False


def _save_upload(upload: UploadFile, content: bytes) -> Path:
    folder = config().uploads_dir
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{uuid.uuid4().hex[:8]}-{Path(upload.filename or 'upload').name}"
    target.write_bytes(content)
    return target


# ---------------------------------------------------------------- datasets
@router.get("/datasets")
def datasets(project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return datasets_svc.list_datasets(db, project)


@router.post("/datasets/upload")
async def upload_dataset(name: str = Form(...), file: UploadFile = File(...), project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    target = _save_upload(file, await file.read())
    return wrap(lambda: datasets_svc.create_from_jsonl(db, project, name, target, file.filename))


@router.post("/datasets/from-path")
def dataset_from_path(body: FromPathBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    source = Path(body.path).expanduser()
    if not source.is_file():
        raise HTTPException(status_code=400, detail=f"{body.path} is not a file on the API host")
    return wrap(lambda: datasets_svc.create_from_jsonl(db, project, body.name, source))


@router.post("/datasets/rows")
def create_from_rows(name: str, body: RowsBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: datasets_svc.create_dataset(db, project, name, body.rows, {"kind": "api"}))


@router.post("/datasets/synthetic")
async def synthetic_dataset(name: str = Form(...), pairs_per_source: int = Form(25), temperature: float = Form(0.3), goal: str = Form(""), rules: str = Form(""), question_format: str = Form(""), answer_format: str = Form(""), example_questions: str = Form(""), example_answers: str = Form(""), system_prompt: str = Form(""), curate_threshold: float = Form(0.0), urls: str = Form(""), files: list[UploadFile] = File(default=[]), project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    url_list = [u.strip() for u in urls.splitlines() if u.strip()]
    if not files and not url_list:
        raise HTTPException(status_code=400, detail="add at least one file or web page URL")
    paths = [str(_save_upload(upload, await upload.read())) for upload in files if upload.filename]
    lines = lambda text: [line.strip() for line in text.splitlines() if line.strip()]  # noqa: E731
    args = {"name": name, "files": paths, "urls": url_list, "pairs_per_source": max(1, min(500, pairs_per_source)), "temperature": max(0.0, min(1.0, temperature)), "goal": goal or project.goal, "rules": lines(rules), "question_format": question_format, "answer_format": answer_format, "example_questions": lines(example_questions), "example_answers": lines(example_answers), "system_prompt": system_prompt or None, "curate_threshold": max(0.0, min(10.0, curate_threshold))}
    return wrap(lambda: worker.enqueue(db, "dataset.synthetic", args, project, principal.user))


@router.post("/datasets/synthetic-spec")
def synthetic_from_spec(body: SyntheticSpecBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    missing = [f for f in body.files if not Path(f).expanduser().is_file()]
    if missing:
        raise HTTPException(status_code=400, detail=f"files not found on the API host: {missing}")
    if not body.files and not body.urls:
        raise HTTPException(status_code=400, detail="add at least one file or web page URL")
    args = body.model_dump()
    args["files"] = [str(Path(f).expanduser()) for f in body.files]
    args["goal"] = body.goal or project.goal
    return wrap(lambda: worker.enqueue(db, "dataset.synthetic", args, project, principal.user))


@router.post("/augment")
def augment(body: AugmentBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    wrap(lambda: datasets_svc.get_dataset(db, project, body.dataset))
    return wrap(lambda: worker.enqueue(db, "dataset.augment", body.model_dump(), project, principal.user))


@router.get("/datasets/{name}")
def dataset(name: str, offset: int = 0, limit: int = 25, after: int | None = None, bucket: str | None = None, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Rows of a dataset. Pass ``after`` (the previous page's ``next_after``) for keyset paging; ``offset`` serves numbered pages."""
    ds = wrap(lambda: datasets_svc.get_dataset(db, project, name))
    rows, next_after = datasets_svc.page(db, ds, max(1, min(limit, 1000)), after, max(0, offset), bucket)
    return {"meta": datasets_svc.meta(db, ds), "total": datasets_svc.count(db, ds), "rows": rows, "next_after": next_after}


@router.get("/datasets/{name}/download")
def download_dataset(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> StreamingResponse:
    """Stream the dataset as JSONL in keyset batches; the stream holds its own session, not the request's."""
    import json

    from tinystudio.db import session as open_session
    from tinystudio.db.models import Dataset

    dataset_id = wrap(lambda: datasets_svc.get_dataset(db, project, name)).id

    def lines() -> Any:
        with open_session() as stream_db:
            ds = stream_db.get(Dataset, dataset_id)
            if ds is None:
                return
            for row in datasets_svc.iter_rows(stream_db, ds):
                yield json.dumps({k: v for k, v in row.items() if k != "id"}, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/jsonl", headers={"content-disposition": f'attachment; filename="{name}.jsonl"'})


@router.get("/datasets/{name}/quality")
def dataset_quality(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return datasets_svc.profile(db, wrap(lambda: datasets_svc.get_dataset(db, project, name)))


def _queued_if_large(db: Session, project: Project, dataset_name: str, kind: str, args: dict[str, Any], principal: Principal) -> JSONResponse | None:
    """Datasets above TINYSTUDIO_SYNC_ROW_LIMIT rows are processed by a worker so the request returns at once
    (Cloudflare closes a proxied request that has not answered within its read timeout)."""
    ds = wrap(lambda: datasets_svc.get_dataset(db, project, dataset_name))
    if datasets_svc.count(db, ds) <= config().sync_row_limit:
        return None
    task = wrap(lambda: worker.enqueue(db, kind, args, project, principal.user))
    return JSONResponse(status_code=202, content={"queued": True, "task": jsonable_encoder(task)})


@router.post("/datasets/{name}/clean")
def dataset_clean(name: str, body: CleanBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> Any:
    """Clean in place. Returns the result, or 202 with a task for datasets above the synchronous row limit."""
    queued = _queued_if_large(db, project, name, "dataset.clean", {"dataset": name, "spec": body.model_dump()}, principal)
    return queued or wrap(lambda: datasets_svc.apply_cleaning(db, project, name, CleanSpec(**body.model_dump())))


@router.get("/datasets/{name}/cleaning-history")
def cleaning_history(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return datasets_svc.cleaning_history(db, wrap(lambda: datasets_svc.get_dataset(db, project, name)))


@router.put("/datasets/{name}/labels")
def set_labels(name: str, body: LabelsBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: datasets_svc.set_labels(db, project, name, body.labels))


@router.post("/datasets/{name}/auto-label")
def auto_label(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> dict[str, Any]:
    ds = wrap(lambda: datasets_svc.get_dataset(db, project, name))
    if not ds.labels:
        raise HTTPException(status_code=400, detail="define labels first")
    return wrap(lambda: worker.enqueue(db, "dataset.auto_label", {"dataset": name}, project, principal.user))


@router.post("/datasets/{name}/split")
def split(name: str, body: SplitBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> Any:
    """Autosplit. Returns the dataset meta, or 202 with a task for datasets above the synchronous row limit."""
    queued = _queued_if_large(db, project, name, "dataset.split", {"dataset": name, **body.model_dump()}, principal)
    return queued or wrap(lambda: datasets_svc.autosplit(db, project, name, body.train_pct, body.seed, body.synthetic_in_validation))


@router.post("/datasets/{name}/rows")
def add_rows(name: str, body: RowsBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: datasets_svc.add_rows(db, project, name, body.rows, body.bucket))


@router.post("/datasets/{name}/rows/delete")
def delete_rows(name: str, body: DeleteRowsBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: datasets_svc.delete_rows(db, datasets_svc.get_dataset(db, project, name), body.ids))


@router.post("/datasets/{name}/rows/bucket")
def set_bucket(name: str, body: BucketBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: datasets_svc.set_bucket(db, datasets_svc.get_dataset(db, project, name), body.ids, body.bucket))


@router.delete("/datasets/{name}")
def remove_dataset(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    wrap(lambda: datasets_svc.delete_dataset(db, project, name))
    return {"deleted": name}


@router.post("/datapoints")
def add_datapoint(body: DatapointBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: datasets_svc.add_rows(db, project, body.dataset, [{"messages": body.messages}], body.bucket))


# ---------------------------------------------------------------- snapshots
@router.get("/snapshots")
def snapshots(project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return snapshots_svc.list_snapshots(db, project)


@router.post("/snapshots")
def create_snapshot(body: SnapshotBody, project: Project = Depends(project_dep), db: Session = Depends(get_db), principal: Principal = Depends(current_user)) -> Any:
    """Freeze a snapshot. Returns it, or 202 with a task for datasets above the synchronous row limit."""
    queued = _queued_if_large(db, project, body.dataset, "snapshot.create", body.model_dump(), principal)
    return queued or wrap(lambda: snapshots_svc.create_snapshot(db, project, body.dataset, body.name))


@router.get("/snapshots/{name}")
def snapshot_detail(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    snap = wrap(lambda: snapshots_svc.get_snapshot(db, project, name))
    train, val = snapshots_svc.snapshot_rows(db, snap)
    return {**snapshots_svc.snapshot_dict(snap), "train": train, "val": val}


@router.get("/snapshots/{name}/download")
def download_snapshot(name: str, split: str = "train", project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> StreamingResponse:
    import json

    snap = wrap(lambda: snapshots_svc.get_snapshot(db, project, name))
    train, val = snapshots_svc.snapshot_rows(db, snap)
    rows = val if split == "val" else train
    return StreamingResponse((json.dumps(r, ensure_ascii=False) + "\n" for r in rows), media_type="application/jsonl", headers={"content-disposition": f'attachment; filename="{name}-{split}.jsonl"'})


@router.delete("/snapshots/{name}")
def remove_snapshot(name: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    wrap(lambda: snapshots_svc.delete_snapshot(db, project, name))
    return {"deleted": name}


# ---------------------------------------------------------------- traces and stats
@router.get("/traces")
def traces(limit: int = 100, offset: int = 0, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return traces_svc.list_traces(db, project, limit, offset)


@router.post("/traces/{trace_id}/score")
def score(trace_id: str, body: ScoreBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return wrap(lambda: traces_svc.score_trace(db, project, trace_id, body.score, body.note, body.corrected))


@router.post("/traces/promote")
def promote_traces(body: PromoteBody, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"promoted": wrap(lambda: traces_svc.promote(db, project, body.dataset, body.min_score, body.ids, preference=body.preference))}


@router.delete("/traces/{trace_id}")
def remove_trace(trace_id: str, project: Project = Depends(project_dep), db: Session = Depends(get_db)) -> dict[str, Any]:
    wrap(lambda: traces_svc.delete_trace(db, project, trace_id))
    return {"deleted": trace_id}
