"""Laboratory HTTP contracts. Target credentials never grant general project access."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from tinystudio.api.deps import current_user, get_db, project_dep, require_admin
from tinystudio.auth import Principal
from tinystudio.db.models import Project
from . import service as svc
from .client_paths import RuntimeApiPaths, HardwareApiPaths, RecoveredEvidencePaths
from .models import LabArtifact, LabAttempt, LabDefinition, LabEvent, LabRun, LabTarget
from .study_import_paths import StudyImportPaths
from .study_import import PreviewRequest as ObservationImportPreview, ImportRequest as ObservationImport
from .recovered_evidence import RecoveryPreviewRequest, RecoveryAttachRequest, RecoveryParserLimits

router = APIRouter()
project_routes = APIRouter(prefix="/api/p/{slug}/lab", tags=["laboratory"])
runner_routes = APIRouter(prefix="/api/lab/runner/{target_id}", tags=["laboratory runner"])
data_bridge_routes = APIRouter(prefix="/api/p/{slug}/lab", tags=["laboratory data bridges"])
DB = Annotated[Session, Depends(get_db)]
ProjectDep = Annotated[Project, Depends(project_dep)]
AuthorDep = Annotated[Principal, Depends(current_user)]


def target_auth(target_id: str, db: DB, authorization: str = Header(default="")) -> LabTarget:
    if not authorization.lower().startswith("bearer "):
        raise svc.LabError("runner bearer credential required", 401)
    return svc.authenticate_target(db, target_id, authorization[7:])


TargetDep = Annotated[LabTarget, Depends(target_auth)]


class SaveDefinition(BaseModel):
    name: str | None = None
    definition: dict
    expected_revision: int | None = None


class Submission(BaseModel):
    definition_id: str
    revision: int = Field(ge=1)
    target_id: str = "local"
    idempotency_key: str = Field(min_length=1, max_length=120)
    parent_run_id: str | None = None
    not_before: AwareDatetime | None = None


class TargetEnrollment(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class Preflight(BaseModel):
    definition: dict
    target_id: str = "local"


class CaptureAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictInt | None = Field(default=None, ge=1, le=2**53-1)
    through_seq: StrictInt | None = Field(default=None, ge=0, le=2**53-1)
    idempotency_key: str = Field(min_length=1, max_length=120)


class SaveAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    snapshot_artifact_id: str
    parameters: dict
    execution: dict
    custom_code_artifact_id: str | None = None
    extra_artifact_ids: list[str] = Field(default_factory=list)


class RecordAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    idempotency_key: str = Field(min_length=1, max_length=120)
    notes: str = ""


class SnapshotInput(BaseModel):
    snapshot: str = Field(min_length=1, max_length=200)


class ModelInput(BaseModel):
    job_name: str = Field(min_length=1, max_length=200)
    experiment_number: int = Field(ge=1)


class DatasetPromotion(BaseModel):
    run_id: str
    artifact_id: str
    name: str = Field(min_length=1, max_length=200)
    snapshot_name: str | None = Field(default=None, min_length=1, max_length=200)
    min_training: int = Field(default=10, ge=1, le=10000, strict=True)
    idempotency_key: str = Field(min_length=1, max_length=120)


class RepositoryCapture(BaseModel):
    repository: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=120)


class Claim(BaseModel):
    capabilities: dict
    run_id: str | None = None


class OperationRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=120)


class BundleImport(OperationRequest):
    artifact_id: str


class Heartbeat(BaseModel):
    events: list[dict] = Field(default_factory=list, max_length=100)


class Completion(BaseModel):
    state: str
    result: dict | None = None
    error: str | None = None


class SaveStudy(BaseModel):
    study: dict
    expected_revision: int | None = Field(default=None, ge=1, strict=True)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)


@project_routes.get('/studies')
def studies(db: DB, project: ProjectDep, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    from .studies import list_studies
    return list_studies(db, project.id, offset=offset, limit=limit)


@project_routes.post('/studies', status_code=201)
def create_study(body: SaveStudy, db: DB, project: ProjectDep, author: AuthorDep):
    from .studies import save_study
    return save_study(db, project.id, body.study, author.user.id, key=body.idempotency_key)


@project_routes.get('/studies/{id_}')
def study(id_: str, db: DB, project: ProjectDep, revision: int | None = Query(None, ge=1, le=2**53-1)):
    from .studies import get_study
    return get_study(db, project.id, id_, revision=revision)


@project_routes.put('/studies/{id_}')
def update_study(id_: str, body: SaveStudy, db: DB, project: ProjectDep, author: AuthorDep):
    from .studies import save_study
    return save_study(db, project.id, body.study, author.user.id, id_=id_, expected=body.expected_revision)


@project_routes.get('/studies/{id_}/revisions')
def study_revisions(id_: str, db: DB, project: ProjectDep):
    from .studies import revisions
    return revisions(db, project.id, id_)


@project_routes.get('/studies/{id_}/records')
def study_records(id_: str, db: DB, project: ProjectDep, after: int = Query(0, ge=0, le=2**53-1),
                  limit: int = Query(100, ge=1, le=500)):
    from .studies import records
    return records(db, project.id, id_, after=after, limit=limit)


@project_routes.post('/studies/{id_}/records', status_code=201)
def create_study_record(id_: str, body: dict, db: DB, project: ProjectDep, author: AuthorDep):
    from .studies import append_record
    return append_record(db, project.id, id_, body, author.user.id)


@project_routes.post(StudyImportPaths.preview_route)
def preview_study_observations(id_: str, body: ObservationImportPreview, db: DB, project: ProjectDep):
    from .study_import import preview_import
    return preview_import(db, project.id, id_, body.model_dump())


@project_routes.post(StudyImportPaths.apply_route, status_code=201)
def import_study_observations(id_: str, body: ObservationImport, db: DB, project: ProjectDep, author: AuthorDep):
    from .study_import import apply_import
    result = apply_import(db, project.id, id_, body.model_dump(), author.user.id)
    # Request-scoped dependency finalizers may run after the response is sent.
    # A successful import receipt must follow the outer transaction commit.
    db.commit()
    return result


@project_routes.get('/studies/{id_}/manifest')
def study_manifest(id_: str, db: DB, project: ProjectDep):
    from .studies import tracking_manifest
    return tracking_manifest(db, project.id, id_)


@project_routes.get('/studies/{id_}/export')
def export_study(id_: str, db: DB, project: ProjectDep,
                 revision: int | None = Query(None, ge=1, le=2**53-1),
                 through_seq: int | None = Query(None, ge=0, le=2**53-1)):
    from .studies import export_tracking
    from .engine import digest
    directory = Path(tempfile.mkdtemp(prefix='ts-study-export-'))
    try:
        path = export_tracking(db, project.id, id_, directory, revision=revision, through_seq=through_seq)
        return FileResponse(path, filename=path.name, media_type='application/x-ndjson',
                            headers={'X-Content-SHA256': digest(path)},
                            background=BackgroundTask(shutil.rmtree, directory))
    except BaseException:
        shutil.rmtree(directory)
        raise


@project_routes.get("/catalog")
def catalog(project: ProjectDep):
    return svc.catalog()


@project_routes.get("/statistics/methods")
def statistics_methods(project: ProjectDep):
    from .statistics_selection import method_catalog
    return method_catalog()


@project_routes.get("/statistics/analysis-methods")
def analysis_methods(project: ProjectDep):
    from .analysis import method_catalog
    return method_catalog()


@project_routes.get(RuntimeApiPaths(base="").methods)
def runtime_methods(project: ProjectDep):
    from .runtime_probe import method_catalog
    return method_catalog()


@project_routes.get(HardwareApiPaths(base="").discovery_methods)
def hardware_discovery_methods(project: ProjectDep):
    from .hardware_discovery_engine import method_catalog
    return method_catalog()


@project_routes.post('/studies/{id_}/analysis-inputs', status_code=202)
def capture_analysis_input(id_: str, body: CaptureAnalysisInput, db: DB, project: ProjectDep, author: AuthorDep):
    from .study_analysis import request_input
    from .operations import operation_dict
    return operation_dict(request_input(db, project.id, id_, body.idempotency_key,
                                       revision=body.revision, through_seq=body.through_seq))


@project_routes.post('/statistics/analyses', status_code=201)
def save_analysis(body: SaveAnalysis, db: DB, project: ProjectDep, author: AuthorDep):
    from .study_analysis import save_analysis_definition
    return save_analysis_definition(db, project.id, body.model_dump())


@project_routes.post('/studies/{id_}/analysis-results', status_code=201)
def record_analysis_result(id_: str, body: RecordAnalysis, db: DB, project: ProjectDep, author: AuthorDep):
    from .study_analysis import record_result
    return record_result(db, project.id, id_, body.run_id, author.user.id, body.idempotency_key, notes=body.notes)


@project_routes.post("/preflight")
def preflight(body: Preflight, db: DB, project: ProjectDep):
    return svc.preflight(db, project.id, body.definition, body.target_id)


@project_routes.get("/definitions")
def definitions(db: DB, project: ProjectDep):
    return [svc.definition_dict(db, row) for row in db.scalars(select(LabDefinition).where(
        LabDefinition.project_id == project.id).order_by(LabDefinition.created.desc()).limit(500))]


@project_routes.get("/pipelines")
def pipelines(db: DB, project: ProjectDep):
    return [row for row in definitions(db, project) if row["definition"].get("pipeline")]


@project_routes.post("/pipelines", status_code=201)
def create_pipeline(body: SaveDefinition, db: DB, project: ProjectDep):
    if not body.definition.get("pipeline"):
        raise svc.LabError("a pipeline definition requires explicit transformation stages")
    return svc.save_definition(db, project.id, body.definition)


@project_routes.post("/definitions", status_code=201)
def create_definition(body: SaveDefinition, db: DB, project: ProjectDep):
    return svc.save_definition(db, project.id, body.definition)


@project_routes.put("/definitions/{id_}")
def update_definition(id_: str, body: SaveDefinition, db: DB, project: ProjectDep):
    return svc.save_definition(db, project.id, body.definition, id_=id_, expected=body.expected_revision)


@project_routes.get("/targets")
def targets(db: DB, project: ProjectDep):
    svc.resolve_target(db, project.id, "local")
    return [svc.target_dict(row) for row in db.scalars(select(LabTarget).where(LabTarget.project_id == project.id))]


@project_routes.post("/targets", dependencies=[Depends(require_admin)], status_code=201)
def enroll_target(body: TargetEnrollment, db: DB, project: ProjectDep):
    return svc.enroll(db, project.id, body.name)


@project_routes.post("/targets/{id_}/recover")
def recover_target(id_: str, db: DB, project: ProjectDep):
    result = {"recovered": svc.recover(db, svc.resolve_target(db, project.id, id_))}
    db.commit()
    return result


@project_routes.get("/runs")
def runs(db: DB, project: ProjectDep, limit: int = Query(100, ge=1, le=500)):
    return [svc.run_dict(db, row, detailed=False) for row in db.scalars(select(LabRun).where(
        LabRun.project_id == project.id).order_by(LabRun.created.desc()).limit(limit))]


@project_routes.post("/runs", status_code=202)
def submit(body: Submission, db: DB, project: ProjectDep):
    return svc.run_dict(db, svc.submit(db, project.id, body.definition_id, body.revision,
                        body.target_id, body.idempotency_key, body.parent_run_id, not_before=body.not_before))


@project_routes.get("/runs/{id_}")
def run(id_: str, db: DB, project: ProjectDep):
    return svc.run_dict(db, svc.get_run(db, project.id, id_))


@project_routes.get(RecoveredEvidencePaths(base='').references_route)
def recovered_evidence(id_:str,db:DB,project:ProjectDep):
    from .recovered_evidence import recovery_references, CLASSIFICATION
    svc.get_run(db,project.id,id_)
    return {'classification':CLASSIFICATION,'references':recovery_references(db,id_)}


@project_routes.post(RecoveredEvidencePaths(base='').preview_route)
def preview_recovered_evidence(id_:str,body:RecoveryPreviewRequest,db:DB,project:ProjectDep):
    from .recovered_evidence import preview_recovered_evidence as preview
    return preview(db,project.id,id_,body,limits=RecoveryParserLimits.from_environment())


@project_routes.post(RecoveredEvidencePaths(base='').attach_route,status_code=201)
def attach_recovered_evidence(id_:str,body:RecoveryAttachRequest,db:DB,project:ProjectDep,author:AuthorDep):
    from .recovered_evidence import attach_recovered_evidence as attach
    result=attach(db,project.id,id_,body,author.user.id,limits=RecoveryParserLimits.from_environment())
    db.commit()
    return result


@project_routes.get("/runs/{id_}/events")
def events(id_: str, db: DB, project: ProjectDep, after: int = Query(0, ge=0)):
    run = svc.get_run(db, project.id, id_)
    rows = list(db.scalars(select(LabEvent).where(LabEvent.run_id == id_, LabEvent.seq > after).order_by(LabEvent.seq).limit(500)))
    return {"events": [{"seq": row.seq, "kind": row.kind, "payload": row.payload, "time": row.time.isoformat()} for row in rows],
            "next_after": rows[-1].seq if rows else after,
            "latest_seq": run.next_seq,
            "has_more": bool(rows and rows[-1].seq < run.next_seq)}


@project_routes.post("/runs/{id_}/cancel")
def cancel(id_: str, db: DB, project: ProjectDep):
    return svc.run_dict(db, svc.cancel(db, project.id, id_))


@project_routes.post("/runs/{id_}/retry", status_code=202)
def retry(id_: str, db: DB, project: ProjectDep):
    return svc.run_dict(db, svc.retry(db, project.id, id_))


@project_routes.get("/runs/{id_}/records")
def records(id_: str, db: DB, project: ProjectDep, artifact: str = "processed.jsonl",
            offset: int = Query(0, ge=0, le=1000000), limit: int = Query(1000, ge=1, le=1000)):
    return svc.records(db, project.id, id_, artifact, offset, limit)


@project_routes.get("/episodes")
def episodes(db: DB, project: ProjectDep, run_id: str, offset: int = Query(0, ge=0, le=1000000),
             limit: int = Query(1000, ge=1, le=1000)):
    window = svc.records(db, project.id, run_id, "processed.jsonl", offset, limit)
    groups = {}
    for row in window["records"]:
        identity = row.get("episode_id")
        if not isinstance(identity, str):
            continue
        group = groups.setdefault(identity, {"id": identity, "run_id": run_id, "window_record_count": 0,
                                             "clock_domains": [], "units": []})
        group["window_record_count"] += 1
        for key, value in (("clock_domains", row.get("clock_domain", "unspecified")), ("units", row.get("units"))):
            if value is not None and value not in group[key]:
                group[key].append(value)
    return {"episodes": list(groups.values()), "record_offset": offset, "record_limit": limit,
            "next_offset": window["next_offset"], "evidence": window.get("artifact"),
            "counts_scope": "returned_record_window"}


@project_routes.get("/episodes/{id_}")
def episode(id_: str, db: DB, project: ProjectDep, run_id: str,
            offset: int = Query(0, ge=0, le=1000000), limit: int = Query(1000, ge=1, le=1000)):
    window = svc.records(db, project.id, run_id, "processed.jsonl", offset, limit)
    rows = [row for row in window["records"] if row.get("episode_id") == id_]
    return {"schema_version": 1, "id": id_, "run_id": run_id, "observations": rows,
            "actions": [{"record_id": row.get("record_id"), "time": row.get("time"), key: row[key]}
                        for row in rows for key in ("action", "command", "control") if key in row and row[key] is not None],
            "messages": [{"record_id": row.get("record_id"), "time": row.get("time"), "message": row["message"]}
                         for row in rows if "message" in row],
            "record_offset": offset, "next_offset": window["next_offset"], "evidence": window.get("artifact"),
            "counts_scope": "returned_record_window"}


def _resource_runs(db, project, engine):
    return [svc.run_dict(db, row, detailed=False) for row in db.scalars(select(LabRun).where(
        LabRun.project_id == project.id, LabRun.spec["definition"]["engine"].as_string() == engine)
        .order_by(LabRun.created.desc()).limit(100))]


def _submit_resource(body, db, project, engine):
    from .models import LabRevision
    svc.get_definition(db, project.id, body.definition_id)
    revision = db.get(LabRevision, (body.definition_id, body.revision))
    if revision is None or revision.definition.get("engine") != engine:
        raise svc.LabError("definition revision does not match this resource kind", 409)
    return submit(body, db, project)


@project_routes.get("/comparisons")
def comparisons(db: DB, project: ProjectDep):
    return _resource_runs(db, project, "evaluation.compare")


@project_routes.post("/comparisons", status_code=202)
def create_comparison(body: Submission, db: DB, project: ProjectDep):
    return _submit_resource(body, db, project, "evaluation.compare")


@project_routes.get("/deployments")
def deployments(db: DB, project: ProjectDep):
    return _resource_runs(db, project, "deployment.benchmark")


@project_routes.post("/deployments", status_code=202)
def create_deployment(body: Submission, db: DB, project: ProjectDep):
    return _submit_resource(body, db, project, "deployment.benchmark")


@project_routes.get("/runs/{id_}/export")
def export_run(id_: str, db: DB, project: ProjectDep):
    from .portable import export_bundle
    directory = Path(tempfile.mkdtemp(prefix="ts-lab-export-"))
    try:
        path = export_bundle(db, project.id, id_, directory)
        return FileResponse(path, filename=f"tinystudio-{id_}.zip", media_type="application/zip",
                            background=BackgroundTask(shutil.rmtree, directory))
    except BaseException:
        shutil.rmtree(directory)
        raise


@project_routes.get("/inputs")
def inputs(db: DB, project: ProjectDep):
    uploaded = [svc.artifact_dict(row) for row in db.scalars(select(LabArtifact).where(
        LabArtifact.project_id == project.id, LabArtifact.attempt_id.is_(None)).limit(1000))]
    for artifact, run in db.execute(select(LabArtifact, LabRun).join(LabAttempt, LabArtifact.attempt_id == LabAttempt.id)
            .join(LabRun, LabAttempt.run_id == LabRun.id).where(LabArtifact.project_id == project.id,
                LabAttempt.state == "succeeded").order_by(LabRun.created.desc()).limit(1000)):
        if artifact.id in {item["id"] for item in (run.result or {}).get("artifacts", [])}:
            uploaded.append({**svc.artifact_dict(artifact), "origin": {
                "run_id": run.id, "revision": run.revision,
                "definition_name": run.spec.get("definition", {}).get("name") or run.definition_id,
            }})
        if len(uploaded) >= 1000:
            break
    return uploaded


@project_routes.post("/imports", status_code=201)
def import_run(db: DB, project: ProjectDep, file: UploadFile = File(...)):
    from .portable import import_bundle
    with tempfile.TemporaryDirectory(prefix="ts-lab-import-") as root:
        path = copy_upload(file, Path(root), 512 * 1024**2)
        return import_bundle(db, project.id, path, Path(root))


def copy_upload(file: UploadFile, root: Path, maximum: int) -> Path:
    path = root / "upload"
    size = 0
    with path.open("wb") as stream:
        while block := file.file.read(1024**2):
            size += len(block)
            if size > maximum:
                raise svc.LabError(f"upload exceeds {maximum} byte limit", 413)
            stream.write(block)
    return path


@project_routes.post("/inputs", status_code=201)
def upload_input(db: DB, project: ProjectDep, file: UploadFile = File(...)):
    with tempfile.TemporaryDirectory(prefix="ts-lab-upload-") as root:
        path = copy_upload(file, Path(root), 512 * 1024**2)
        result = svc.artifact_dict(svc.store_artifact(db, project.id, path, file.filename or "input.bin", file.content_type or "application/octet-stream"))
        # A recovery client persists this identity before its attach request.
        # Commit before acknowledging it, so that identity is already visible
        # to independent verification and a failed commit cannot return 201.
        db.commit()
        return result


@data_bridge_routes.post("/inputs/from-snapshot", status_code=201)
def snapshot_input(body: SnapshotInput, db: DB, project: ProjectDep):
    from .bridge import snapshot_input as export_snapshot
    return export_snapshot(db, project, body.snapshot)


@data_bridge_routes.post("/inputs/from-model", status_code=201)
def model_input(body: ModelInput, db: DB, project: ProjectDep):
    from .bridge import model_input as export_model
    return export_model(db, project, body.job_name, body.experiment_number)


@project_routes.post("/datasets/from-run", status_code=202)
def promote_dataset(body: DatasetPromotion, db: DB, project: ProjectDep):
    from .operations import request_dataset_promotion, operation_dict
    return operation_dict(request_dataset_promotion(db, project.id, body.run_id, body.artifact_id, body.name,
        body.idempotency_key, snapshot_name=body.snapshot_name, min_training=body.min_training))


@project_routes.post("/inputs/from-repository", status_code=202)
def capture_repository(body: RepositoryCapture, db: DB, project: ProjectDep):
    from .repository_capture import request_capture
    from .operations import operation_dict
    return operation_dict(request_capture(db, project.id, body.repository, body.idempotency_key))


@project_routes.get("/runs/{id_}/representations")
def inspect_representations(id_: str, db: DB, project: ProjectDep, artifact_id: str, row: int = Query(ge=0)):
    from .inspection import representations
    return representations(db, project.id, id_, artifact_id, row)


@project_routes.get("/runs/{id_}/coverage")
def inspect_coverage(id_: str, db: DB, project: ProjectDep, row_axis: str = "scenario_id", column_axis: str = "partition"):
    from .inspection import coverage
    return coverage(db, project.id, id_, row_axis, column_axis)


def download_artifact(row: LabArtifact):
    directory = Path(tempfile.mkdtemp(prefix="ts-lab-download-"))
    try:
        path = svc.materialize(row, directory)
        return FileResponse(path, filename=Path(row.name).name, media_type=row.media_type,
                            background=BackgroundTask(shutil.rmtree, directory))
    except BaseException:
        shutil.rmtree(directory)
        raise


@project_routes.get("/artifacts/{id_}")
def artifact(id_: str, db: DB, project: ProjectDep):
    return download_artifact(svc.get_artifact(db, project.id, id_))


@project_routes.post("/runs/{id_}/exports", status_code=202)
def request_export(id_: str, body: OperationRequest, db: DB, project: ProjectDep):
    from .operations import operation_dict, request_export
    return operation_dict(request_export(db, project.id, id_, body.idempotency_key))


@project_routes.post("/bundle-imports", status_code=202)
def request_bundle_import(body: BundleImport, db: DB, project: ProjectDep):
    from .operations import operation_dict, request_import
    return operation_dict(request_import(db, project.id, body.artifact_id, body.idempotency_key))


@project_routes.get("/operations/{id_}")
def operation(id_: str, db: DB, project: ProjectDep):
    from .operations import get_operation, operation_dict
    return operation_dict(get_operation(db, project.id, id_))


@project_routes.get("/operations")
def operation_list(db: DB, project: ProjectDep, limit: int = Query(20, ge=1, le=100)):
    from .models import LabOperation
    # Lists deliberately omit potentially large private result bodies. Inspect
    # one scoped operation when its complete result is needed.
    rows = db.execute(select(LabOperation.id, LabOperation.kind, LabOperation.state, LabOperation.created,
        LabOperation.finished, LabOperation.generation, LabOperation.error, LabOperation.error_detail,
        func.coalesce(LabOperation.payload["run_id"].as_string(), LabOperation.payload["intent"]["run_id"].as_string()).label("run_id"),
        func.coalesce(LabOperation.payload["artifact_id"].as_string(), LabOperation.payload["intent"]["artifact_id"].as_string()).label("artifact_id"))
        .where(LabOperation.project_id == project.id).order_by(LabOperation.created.desc()).limit(limit))
    return [{"id": row.id, "kind": row.kind, "state": row.state, "created": row.created.isoformat(),
             "finished": row.finished.isoformat() if row.finished else None, "attempt": row.generation,
             "error": row.error, "error_detail": row.error_detail, "resource": {"run_id": row.run_id, "artifact_id": row.artifact_id}}
            for row in rows]


@project_routes.get("/operations/{id_}/download")
def operation_download(id_: str, db: DB, project: ProjectDep):
    from .operations import get_operation
    row = get_operation(db, project.id, id_)
    if row.kind != "export" or row.state != "succeeded" or not row.result:
        raise svc.LabError("export is not complete", 409)
    return download_artifact(svc.get_artifact(db, project.id, row.result["artifact"]["id"]))


@project_routes.post("/operations/{id_}/cancel")
def operation_cancel(id_: str, db: DB, project: ProjectDep):
    from .operations import cancel_operation, operation_dict
    return operation_dict(cancel_operation(db, project.id, id_))


@project_routes.post("/operations/{id_}/retry", status_code=202)
def operation_retry(id_: str, db: DB, project: ProjectDep):
    from .operations import retry_operation, operation_dict
    return operation_dict(retry_operation(db, project.id, id_))


@runner_routes.post("/claim")
def claim(body: Claim, db: DB, target: TargetDep):
    result = {"attempt": svc.claim(db, target, body.capabilities, run_id=body.run_id)}
    # The response conveys execution authority. Commit before transmitting it.
    db.commit()
    return result


@runner_routes.post("/advertise")
def advertise(body: Claim, db: DB, target: TargetDep):
    target = svc.lock_target(db, target)
    if len(svc.canonical(body.capabilities)) > 65536:
        raise svc.LabError("capability manifest too large")
    target.capabilities, target.last_seen = body.capabilities, svc.now()
    db.commit()
    return {"accepted": True, "target_id": target.id}


@runner_routes.post("/attempts/{id_}/heartbeat")
def heartbeat(id_: str, body: Heartbeat, db: DB, target: TargetDep, x_lab_lease: str = Header(default="")):
    result = svc.heartbeat(db, target, id_, x_lab_lease, body.events)
    db.commit()
    return result


@runner_routes.post("/attempts/{id_}/finish")
def finish(id_: str, body: Completion, db: DB, target: TargetDep, x_lab_lease: str = Header(default="")):
    # Credential authentication takes a target lock. Release it before storage
    # readback; the service reacquires attempt authority to publish completion.
    db.commit()
    result = svc.finish(db, target, id_, x_lab_lease, body.state, body.result, body.error)
    db.commit()
    return result


@runner_routes.post("/attempts/{id_}/artifacts", status_code=201)
def upload_output(id_: str, db: DB, target: TargetDep, file: UploadFile = File(...),
                  role: str = Query("evidence", max_length=64), visibility: str = Query("model", pattern=r"^(model|evaluation|truth)$"),
                  sha256: str = Query(pattern=r"^[0-9a-f]{64}$"), x_lab_lease: str = Header(default="")):
    from .engine import digest
    name = svc.validate_artifact_name(file.filename or "output.bin")
    run, attempt = svc.attempt_authority(db, target, id_, x_lab_lease)
    maximum = run.spec["definition"]["execution"]["max_output_bytes"]
    existing = db.scalar(select(LabArtifact).where(LabArtifact.attempt_id == id_, LabArtifact.name == name))
    if existing and (existing.sha256, existing.role, existing.visibility) != (sha256, role, visibility):
        raise svc.LabError("artifact name already published with different content or role", 409)
    prior = sum(db.scalars(select(LabArtifact.size).where(LabArtifact.attempt_id == attempt.id)))
    # A lost response may be redelivered after this artifact consumed the entire
    # budget. Bound its body by the retained size without charging it twice.
    allowance = existing.size if existing else maximum - prior
    # Release target/run locks while transferring large objects. The publication
    # transaction below reacquires authority and rechecks both lease and budget.
    db.commit()
    with tempfile.TemporaryDirectory(prefix="ts-lab-output-") as root:
        path = copy_upload(file, Path(root), allowance)
        if digest(path) != sha256:
            raise svc.LabError("uploaded output checksum mismatch", 409)
        verified = svc.persist_verified(path, target.project_id, file.content_type or "application/octet-stream")
        svc.attempt_authority(db, target, id_, x_lab_lease)
        existing = db.scalar(select(LabArtifact).where(LabArtifact.attempt_id == id_, LabArtifact.name == name))
        if existing:
            if (existing.sha256, existing.role, existing.visibility, existing.size) == (sha256, role, visibility, path.stat().st_size):
                result = svc.artifact_dict(existing)
                db.commit()
                return result
            raise svc.LabError("artifact name already published with different content or role", 409)
        prior = sum(db.scalars(select(LabArtifact.size).where(LabArtifact.attempt_id == attempt.id)))
        if prior + path.stat().st_size > maximum:
            raise svc.LabError("attempt output budget exceeded", 413)
        result = svc.artifact_dict(svc.store_artifact(db, target.project_id, path, name,
                   file.content_type or "application/octet-stream", role, id_, verified=verified, visibility=visibility))
        db.commit()
        return result


@runner_routes.get("/attempts/{id_}/inputs/{artifact_id}")
def runner_input(id_: str, artifact_id: str, db: DB, target: TargetDep, x_lab_lease: str = Header(default="")):
    run, _ = svc.attempt_authority(db, target, id_, x_lab_lease)
    if artifact_id not in {item["artifact_id"] for item in run.spec["definition"]["inputs"]}:
        raise svc.LabError("artifact is outside this attempt's input manifest", 403)
    artifact = svc.get_artifact(db, target.project_id, artifact_id)
    db.commit()
    return download_artifact(artifact)


router.include_router(project_routes)
router.include_router(runner_routes)


def install(app, *, include_data_bridges: bool = False):
    app.include_router(router)
    if include_data_bridges:
        app.include_router(data_bridge_routes)

    @app.exception_handler(svc.LabError)
    async def lab_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})
