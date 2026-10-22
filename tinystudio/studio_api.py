"""Studio-replica API: the endpoints the web interface needs beyond the basics in ``ui.py``.

Datasets (list, upload, synthetic wizard, labels, delete, download),
snapshots, jobs with pending experiments and analysis, metrics editing,
evaluations list, models, playground chat, traces, stats, and API keys.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import sys

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import data as data_mod
from .analysis import load_analysis
from .quality import CleanSpec, apply_cleaning, profile
from . import hub
from .evaluate import create_metric, delete_eval, delete_metric, ensure_builtin_metrics, load_eval, load_metric, update_metric
from .keys import create_key, list_keys, revoke_key
from .project import Project, ProjectError, now, read_json
from .recommend import catalog_as_dicts, catalog_entry
from .train import TrainConfig, create_job, create_pending_experiment, delete_experiment, delete_job, experiment_dir, job_meta, list_experiments, read_loss_curve
from .tools import Studio, StudioError, tool_specs
from .traces import delete_trace, read_traces


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


class HubDownloadBody(BaseModel):
    repo_id: str
    revision: str | None = None


class HubTokenBody(BaseModel):
    token: str


class HubUploadBody(BaseModel):
    kind: str  # "experiment" | "dataset" | "snapshot"
    name: str  # job/number, dataset name, or snapshot name
    repo_id: str
    private: bool = True
    merge: bool = False


class AssistantBody(BaseModel):
    provider: str = "anthropic"
    messages: list[dict[str, str]]
    allow_destructive: bool = False


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


class JobBody(BaseModel):
    name: str
    snapshot: str
    goal: str = ""
    reasoning: bool = False


class ExperimentSpec(BaseModel):
    base_model: str
    training_type: str = "lora"
    epochs: float = Field(3, ge=0.1, le=50)
    learning_rate: float = Field(2e-4, gt=0, le=1)
    batch_size: int = Field(8, ge=1, le=64)
    gradient_accumulation_steps: int = Field(2, ge=1, le=64)
    max_length: int = Field(512, ge=64, le=8192)
    lora_r: int = Field(16, ge=1, le=256)
    lora_alpha: int = Field(32, ge=1, le=512)
    assistant_only_loss: bool = False


class ExperimentsBody(BaseModel):
    experiments: list[ExperimentSpec]


class MetricUpdateBody(BaseModel):
    should: list[str] | None = None
    should_not: list[str] | None = None
    description: str | None = None


class ByoeBody(BaseModel):
    name: str
    snapshot: str
    models: list[str]
    url: str
    token: str | None = None
    limit: int | None = None


class KeyBody(BaseModel):
    name: str


class PlaygroundBody(BaseModel):
    model: str
    messages: list[dict[str, str]]
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(512, ge=1, le=4096)


def _zip_dir(project: Project, source: Path, stem: str) -> Path:
    out_dir = project.root / ".downloads"
    out_dir.mkdir(exist_ok=True)
    for stale in out_dir.glob(f"{stem}*.zip"):
        stale.unlink(missing_ok=True)
    archive = shutil.make_archive(str(out_dir / f"{stem}-{uuid.uuid4().hex[:6]}"), "zip", root_dir=source)
    return Path(archive)


def build_studio_router(project: Project, loaded: dict[str, Any], load: Any, unload: Any, start_task: Any, trace: Any) -> APIRouter:
    router = APIRouter()
    uploads = project.root / ".uploads"
    task_dir = project.root / ".tasks"
    ensure_builtin_metrics(project)

    def wrap(action: Any) -> Any:
        try:
            return action()
        except (ProjectError, data_mod.DatapointError) as cause:
            raise HTTPException(status_code=400, detail=str(cause)) from cause

    # datasets -----------------------------------------------------------

    @router.get("/api/datasets")
    def datasets() -> list[dict[str, Any]]:
        tree = project.tree()
        return [
            {**d, "snapshot_count": len(d.get("snapshots", [])), "labels": d.get("labels", []), "origin": d.get("origin")}
            for d in tree["datasets"]
            if not d.get("missing")
        ]

    @router.post("/api/datasets/upload")
    async def upload_dataset(name: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
        uploads.mkdir(exist_ok=True)
        target = uploads / f"{uuid.uuid4().hex[:8]}-{Path(file.filename or 'dataset.jsonl').name}"
        target.write_bytes(await file.read())
        return wrap(lambda: data_mod.create_dataset_from_jsonl(project, name, target))

    @router.post("/api/datasets/synthetic")
    async def synthetic_dataset(
        name: str = Form(...),
        pairs_per_source: int = Form(25),
        temperature: float = Form(0.3),
        goal: str = Form(""),
        rules: str = Form(""),
        question_format: str = Form(""),
        answer_format: str = Form(""),
        example_questions: str = Form(""),
        example_answers: str = Form(""),
        system_prompt: str = Form(""),
        curate_threshold: float = Form(0.0),
        urls: str = Form(""),
        files: list[UploadFile] = File(default=[]),
    ) -> dict[str, Any]:
        url_list = [u.strip() for u in urls.splitlines() if u.strip()]
        if not files and not url_list:
            raise HTTPException(status_code=400, detail="add at least one file or web page URL")
        folder = uploads / uuid.uuid4().hex[:8]
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for upload in files:
            target = folder / Path(upload.filename or "document").name
            target.write_bytes(await upload.read())
            paths.append(str(target))
        lines = lambda text: [line.strip() for line in text.splitlines() if line.strip()]  # noqa: E731
        spec = {
            "files": paths,
            "urls": url_list,
            "pairs_per_source": max(1, min(500, pairs_per_source)),
            "temperature": max(0.0, min(1.0, temperature)),
            "goal": goal or project.manifest.get("goal", ""),
            "rules": lines(rules),
            "question_format": question_format,
            "answer_format": answer_format,
            "example_questions": lines(example_questions),
            "example_answers": lines(example_answers),
            "system_prompt": system_prompt or None,
            "curate_threshold": max(0.0, min(10.0, curate_threshold)),
        }
        task_dir.mkdir(exist_ok=True)
        spec_path = task_dir / f"synth-{uuid.uuid4().hex[:6]}.json"
        spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return start_task("synth", "dataset", "synth", name, "--spec", str(spec_path))

    @router.post("/api/datasets/from-path")
    def dataset_from_path(body: FromPathBody) -> dict[str, Any]:
        source = Path(body.path).expanduser()
        if not source.is_file():
            raise HTTPException(status_code=400, detail=f"{body.path} is not a file")
        return wrap(lambda: data_mod.create_dataset_from_jsonl(project, body.name, source))

    @router.post("/api/datasets/synthetic-spec")
    def synthetic_from_spec(body: SyntheticSpecBody) -> dict[str, Any]:
        missing = [f for f in body.files if not Path(f).expanduser().is_file()]
        if missing:
            raise HTTPException(status_code=400, detail=f"files not found: {missing}")
        if not body.files and not body.urls:
            raise HTTPException(status_code=400, detail="add at least one file or web page URL")
        spec = body.model_dump()
        spec["files"] = [str(Path(f).expanduser()) for f in body.files]
        spec["goal"] = body.goal or project.manifest.get("goal", "")
        name = spec.pop("name")
        task_dir.mkdir(exist_ok=True)
        spec_path = task_dir / f"synth-{uuid.uuid4().hex[:6]}.json"
        spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return start_task("synth", "dataset", "synth", name, "--spec", str(spec_path))

    @router.get("/api/datasets/{name}/quality")
    def dataset_quality(name: str) -> dict[str, Any]:
        rows = wrap(lambda: data_mod.load_dataset(project, name))
        return profile(rows)

    @router.post("/api/datasets/{name}/clean")
    def dataset_clean(name: str, body: CleanBody) -> dict[str, Any]:
        return wrap(lambda: apply_cleaning(project, name, CleanSpec(**body.model_dump())))

    @router.get("/api/datasets/{name}/cleaning-history")
    def cleaning_history(name: str) -> list[dict[str, Any]]:
        wrap(lambda: data_mod.dataset_meta(project, name))
        path = project.dataset_dir(name) / "cleaning.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []

    @router.put("/api/datasets/{name}/labels")
    def set_labels(name: str, body: LabelsBody) -> dict[str, Any]:
        return wrap(lambda: data_mod.set_labels(project, name, body.labels))

    @router.post("/api/datasets/{name}/auto-label")
    def auto_label(name: str) -> dict[str, Any]:
        wrap(lambda: data_mod.dataset_meta(project, name))
        return start_task("label", "dataset", "auto-label", name)

    @router.delete("/api/datasets/{name}")
    def remove_dataset(name: str) -> dict[str, Any]:
        wrap(lambda: data_mod.delete_dataset(project, name))
        return {"deleted": name}

    @router.get("/api/datasets/{name}/download")
    def download_dataset(name: str) -> FileResponse:
        wrap(lambda: data_mod.dataset_meta(project, name))
        return FileResponse(project.dataset_dir(name) / "datapoints.jsonl", filename=f"{name}.jsonl", media_type="application/jsonl")

    @router.get("/api/snapshots")
    def snapshots() -> list[dict[str, Any]]:
        return [data_mod.snapshot_meta(project, s) for s in project.list_names("snapshots")]

    @router.get("/api/snapshots/{name}/download")
    def download_snapshot(name: str) -> FileResponse:
        wrap(lambda: data_mod.snapshot_meta(project, name))
        archive = _zip_dir(project, project.snapshot_dir(name), f"snapshot-{name}")
        return FileResponse(archive, filename=f"{name}.zip", media_type="application/zip")

    @router.delete("/api/snapshots/{name}")
    def remove_snapshot(name: str) -> dict[str, Any]:
        wrap(lambda: data_mod.delete_snapshot(project, name))
        return {"deleted": name}

    # fine-tuning --------------------------------------------------------

    @router.post("/api/jobs")
    def new_job(body: JobBody) -> dict[str, Any]:
        return wrap(lambda: create_job(project, body.name, body.snapshot, body.goal or project.manifest.get("goal", ""), body.reasoning))

    @router.get("/api/jobs/{job}")
    def job_detail(job: str) -> dict[str, Any]:
        meta = wrap(lambda: job_meta(project, job))
        experiments = list_experiments(project, job)
        for exp in experiments:
            exp["loss"] = read_loss_curve(project, job, exp["number"])
            exp["up"] = exp["alias"] in loaded or f"{job}/{exp['number']}" in loaded
        return {"job": meta, "experiments": experiments, "analysis": load_analysis(project, job), "snapshot": data_mod.snapshot_meta(project, meta["snapshot"]) if (project.snapshot_dir(meta["snapshot"]) / "meta.json").exists() else None}

    @router.post("/api/jobs/{job}/experiments")
    def add_experiments(job: str, body: ExperimentsBody) -> dict[str, Any]:
        if not body.experiments:
            raise HTTPException(status_code=400, detail="add at least one experiment")
        numbers = []
        for spec in body.experiments:
            config = wrap(lambda spec=spec: TrainConfig(**spec.model_dump()))
            numbers.append(wrap(lambda config=config: create_pending_experiment(project, job, config)))
        return {"job": job, "experiments": numbers}

    @router.post("/api/jobs/{job}/start")
    def start_job(job: str) -> dict[str, Any]:
        pending = [e for e in list_experiments(project, job) if e.get("state") == "pending"]
        if not pending:
            raise HTTPException(status_code=400, detail="no pending experiments; add one first")
        return start_task("finetune", "job", "run", job)

    @router.post("/api/jobs/{job}/analyze")
    def analyze(job: str) -> dict[str, Any]:
        wrap(lambda: job_meta(project, job))
        return start_task("analysis", "job", "analyze", job)

    @router.delete("/api/jobs/{job}")
    def remove_job(job: str) -> dict[str, Any]:
        wrap(lambda: delete_job(project, job))
        return {"deleted": job}

    @router.delete("/api/jobs/{job}/experiments/{number}")
    def remove_experiment(job: str, number: int) -> dict[str, Any]:
        wrap(lambda: delete_experiment(project, job, number))
        return {"deleted": f"{job}/{number}"}

    @router.get("/api/jobs/{job}/experiments/{number}/download")
    def download_experiment(job: str, number: int) -> FileResponse:
        exp_dir = experiment_dir(project, job, number)
        source = exp_dir / "adapter" if (exp_dir / "adapter").exists() else exp_dir / "model"
        if not source.exists():
            raise HTTPException(status_code=404, detail="experiment has no weights yet")
        archive = _zip_dir(project, source, f"ft-{job}-{number}")
        return FileResponse(archive, filename=f"ft-{job}-{number}.zip", media_type="application/zip")

    # metrics and evaluations --------------------------------------------

    @router.get("/api/metrics")
    def metrics() -> list[dict[str, Any]]:
        return [load_metric(project, m) for m in project.list_names("metrics")]

    @router.put("/api/metrics/{name}")
    def edit_metric(name: str, body: MetricUpdateBody) -> dict[str, Any]:
        return wrap(lambda: update_metric(project, name, body.should, body.should_not, body.description))

    @router.delete("/api/metrics/{name}")
    def remove_metric(name: str) -> dict[str, Any]:
        wrap(lambda: delete_metric(project, name))
        return {"deleted": name}

    @router.get("/api/evals")
    def evals() -> list[dict[str, Any]]:
        return [load_eval(project, e) for e in project.list_names("evals")]

    @router.delete("/api/evals/{name}")
    def remove_eval(name: str) -> dict[str, Any]:
        wrap(lambda: delete_eval(project, name))
        return {"deleted": name}

    @router.post("/api/evals/byoe")
    def byoe(body: ByoeBody) -> dict[str, Any]:
        metric_name = f"byoe-{body.name}"
        if not (project.metric_path(metric_name)).exists():
            wrap(lambda: create_metric(project, metric_name, "webhook", webhook_url=body.url, description=f"BYOE evaluator for {body.name}", token=body.token))
        args = ["eval", "run", body.name, "--snapshot", body.snapshot, "--metric", metric_name]
        for model in body.models:
            args += ["--model", model]
        if body.limit:
            args += ["--limit", str(body.limit)]
        return start_task("eval", *args)

    # deployment ---------------------------------------------------------

    @router.get("/api/models")
    def models() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for job in project.list_names("jobs"):
            for exp in list_experiments(project, job):
                if exp.get("state") != "succeeded":
                    continue
                model_id = f"{job}/{exp['number']}"
                rows.append(
                    {
                        "id": model_id,
                        "alias": exp["alias"],
                        "kind": "fine-tuned",
                        "base_model": exp["config"]["base_model"],
                        "training_type": exp["config"]["training_type"],
                        "parameters": exp.get("total_parameters"),
                        "created": exp.get("created"),
                        "status": "up" if (model_id in loaded or exp["alias"] in loaded) else "down",
                    }
                )
        for entry in catalog_as_dicts():
            rows.append({"id": entry["id"], "alias": entry["id"], "kind": "base", "base_model": entry["id"], "training_type": None, "parameters": int(entry["params_b"] * 1e9), "created": None, "status": "up" if entry["id"] in loaded else "down", "license": entry["license"], "notes": entry["notes"]})
        return rows

    @router.post("/api/models/{slug}/up")
    def model_up(slug: str) -> dict[str, Any]:
        model_id = slug.replace("~", "/")
        load(model_id)
        return {"id": model_id, "status": "up"}

    @router.post("/api/models/{slug}/down")
    def model_down(slug: str) -> dict[str, Any]:
        return unload(slug.replace("~", "/"))

    @router.post("/api/playground/chat")
    def playground_chat(body: PlaygroundBody) -> StreamingResponse:
        runner = load(body.model)

        def events() -> Iterator[str]:
            started = time.perf_counter()
            pieces: list[str] = []
            try:
                for delta in runner.stream(body.messages, body.max_tokens, body.temperature):
                    pieces.append(delta)
                    yield f"data: {json.dumps({'delta': delta})}\n\n"
            except Exception as cause:  # noqa: BLE001 - surfaced to the page instead of a dropped stream
                yield f"data: {json.dumps({'error': f'{type(cause).__name__}: {cause}'})}\n\n"
                return
            seconds = round(time.perf_counter() - started, 3)
            trace({"id": f"play-{uuid.uuid4().hex[:12]}", "time": now(), "model": body.model, "messages": body.messages, "response": "".join(pieces).strip(), "seconds": seconds, "score": None, "origin": "playground"})
            yield f"data: {json.dumps({'done': True, 'seconds': seconds})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @router.delete("/api/traces/{trace_id}")
    def remove_trace(trace_id: str) -> dict[str, Any]:
        wrap(lambda: delete_trace(project, trace_id))
        return {"deleted": trace_id}

    @router.get("/api/stats")
    def stats() -> dict[str, Any]:
        tree = project.tree()
        traces = read_traces(project)
        per_day: Counter[str] = Counter(t.get("time", "")[:10] for t in traces)
        per_model: Counter[str] = Counter(t.get("model", "") for t in traces)
        experiments = [e for job in project.list_names("jobs") for e in list_experiments(project, job)]
        return {
            "datasets": len([d for d in tree["datasets"] if not d.get("missing")]),
            "datapoints": sum(d.get("datapoints", 0) for d in tree["datasets"]),
            "snapshots": len(project.list_names("snapshots")),
            "jobs": len(project.list_names("jobs")),
            "experiments": dict(Counter(e.get("state") for e in experiments)),
            "training_seconds": round(sum(e.get("seconds", 0) or 0 for e in experiments), 1),
            "evaluations": len(project.list_names("evals")),
            "metrics": len(project.list_names("metrics")),
            "requests": len(traces),
            "prompt_tokens": sum(t.get("prompt_tokens", 0) or 0 for t in traces),
            "completion_tokens": sum(t.get("completion_tokens", 0) or 0 for t in traces),
            "scored_traces": sum(1 for t in traces if t.get("score") is not None),
            "requests_per_day": sorted(per_day.items()),
            "requests_per_model": per_model.most_common(10),
            "models_loaded": sorted(loaded),
        }

    @router.get("/api/keys")
    def keys() -> list[dict[str, Any]]:
        return [{k: v for k, v in key.items() if k != "sha256"} for key in list_keys(project)]

    @router.post("/api/keys")
    def new_key(body: KeyBody) -> dict[str, Any]:
        record, plaintext = wrap(lambda: create_key(project, body.name))
        return {**{k: v for k, v in record.items() if k != "sha256"}, "key": plaintext}

    @router.delete("/api/keys/{key_id}")
    def drop_key(key_id: str) -> dict[str, Any]:
        wrap(lambda: revoke_key(project, key_id))
        return {"revoked": key_id}

    # hugging face hub --------------------------------------------------

    @router.get("/api/hub/search")
    def hub_search(q: str = "", limit: int = 15, max_params_b: float | None = 2.0, author: str | None = None) -> list[dict[str, Any]]:
        return wrap(lambda: hub.search(q, limit, max_params_b, author))

    @router.get("/api/hub/models")
    def hub_models() -> dict[str, Any]:
        return {"models": hub.cached_models(), "free_disk_gb": hub.free_disk_gb(), "token": hub.token_status()}

    @router.get("/api/hub/models/{repo_id:path}/info")
    def hub_model_info(repo_id: str) -> dict[str, Any]:
        return wrap(lambda: hub.model(repo_id))

    @router.post("/api/hub/download")
    def hub_download(body: HubDownloadBody) -> dict[str, Any]:
        args = ["hub", "download", body.repo_id]
        if body.revision:
            args += ["--revision", body.revision]
        return start_task("download", *args)

    @router.delete("/api/hub/models/{repo_id:path}")
    def hub_delete(repo_id: str) -> dict[str, Any]:
        return wrap(lambda: hub.delete_cached(repo_id))

    @router.get("/api/hub/token")
    def hub_token() -> dict[str, Any]:
        return hub.token_status()

    @router.post("/api/hub/token")
    def hub_set_token(body: HubTokenBody) -> dict[str, Any]:
        return wrap(lambda: hub.save_token(body.token))

    @router.post("/api/hub/upload")
    def hub_upload(body: HubUploadBody) -> dict[str, Any]:
        if body.kind not in ("experiment", "dataset", "snapshot"):
            raise HTTPException(status_code=400, detail="kind must be experiment, dataset, or snapshot")
        args = ["hub", "upload", body.kind, body.name, body.repo_id]
        if not body.private:
            args.append("--public")
        if body.merge:
            args.append("--merge")
        return start_task("upload", *args)

    # assistant ---------------------------------------------------------

    @router.get("/api/assistant/status")
    def assistant_status() -> dict[str, Any]:
        from .assistant import status as assistant_status_fn

        return {**assistant_status_fn(), "mcp": {"command": f"{Path(sys.executable).parent / 'tinystudio'} mcp --project {project.root}", "tools": len(tool_specs(Studio('http://127.0.0.1', None)))}}

    @router.post("/api/assistant/chat")
    def assistant_chat(body: AssistantBody, request: Request) -> StreamingResponse:
        from .assistant import run as run_assistant

        if not body.messages or body.messages[-1].get("role") != "user":
            raise HTTPException(status_code=400, detail="the last message must come from the user")
        studio = Studio(str(request.base_url).rstrip("/"), project.root)

        def events() -> Iterator[str]:
            try:
                for event in run_assistant(body.provider, studio, body.messages, body.allow_destructive):
                    yield f"data: {json.dumps(event, default=str)}\n\n"
            except StudioError as cause:
                yield f"data: {json.dumps({'type': 'error', 'message': str(cause)})}\n\n"
            except Exception as cause:  # noqa: BLE001 - the page must see why the stream ended
                yield f"data: {json.dumps({'type': 'error', 'message': f'{type(cause).__name__}: {cause}'})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @router.get("/api/catalog")
    def catalog() -> list[dict[str, Any]]:
        return catalog_as_dicts()

    @router.get("/api/catalog/{model_id:path}")
    def catalog_item(model_id: str) -> dict[str, Any]:
        entry = catalog_entry(model_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="not in the catalog")
        return {"id": entry.id, "params_b": entry.params_b, "family": entry.family, "license": entry.license, "notes": entry.notes}

    return router
