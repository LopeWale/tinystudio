"""Local web interface: JSON API under ``/api`` plus the single-page app at ``/``.

The page is the four Studio tabs over the same files the CLI writes. Long
actions (fine-tuning, evaluation, synthesis) run as subprocesses of the CLI so
the server stays responsive and every run is reproducible from the command
the page shows you.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from tinystudio.core import data as data_mod
from tinystudio.evaluation.evaluate import create_metric, load_eval, load_eval_rows, load_metric
from tinystudio.core.project import Project, ProjectError, now, read_json
from tinystudio.training.recommend import catalog_as_dicts, recommend
from tinystudio.training.train import job_meta, list_experiments, read_loss_curve
from tinystudio.core.traces import promote, read_traces, score_trace

UI_DIR = Path(__file__).resolve().parents[1] / "ui"


class Task:
    def __init__(self, kind: str, argv: list[str], log_path: Path, cwd: Path) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.argv = argv
        self.log_path = log_path
        self.started = now()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = log_path.open("w", encoding="utf-8")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTORCH_ENABLE_MPS_FALLBACK": "1", "TOKENIZERS_PARALLELISM": "false"}
        self.process = subprocess.Popen(argv, cwd=str(cwd), stdout=self.log, stderr=subprocess.STDOUT, env=env)

    def snapshot(self, tail_lines: int = 12) -> dict[str, Any]:
        code = self.process.poll()
        try:
            lines = [line for line in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip() and "redirects.py" not in line]
        except OSError:
            lines = []
        return {
            "id": self.id,
            "kind": self.kind,
            "command": shlex.join(self.argv[3:]),  # argv = [python, -m, tinystudio.cli, ...]
            "started": self.started,
            "state": "running" if code is None else ("succeeded" if code == 0 else "failed"),
            "exit_code": code,
            "log_tail": lines[-tail_lines:],
        }


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


class SynthBody(BaseModel):
    dataset: str
    documents: list[str]
    count: int = Field(20, ge=1, le=2000)
    goal: str = ""
    system: str | None = None


class AugmentBody(BaseModel):
    dataset: str
    count: int = Field(20, ge=1, le=2000)
    instructions: str = ""  # positive: what to generate more of
    avoid: str = ""  # negative: what to avoid
    temperature: float = Field(0.8, ge=0.0, le=1.5)  # creativity level


class FinetuneBody(BaseModel):
    snapshot: str
    job: str
    model: str
    training_type: str = "lora"
    epochs: float = 3
    learning_rate: float = 2e-4
    batch_size: int = 8
    gradient_accumulation_steps: int = 2
    max_length: int = 512
    lora_r: int = 16
    lora_alpha: int = 32
    assistant_only_loss: bool = False


class MetricBody(BaseModel):
    name: str
    kind: str = "rubric"
    should: list[str] = []
    should_not: list[str] = []
    check: str | None = None
    url: str | None = None
    description: str = ""


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


class ScoreBody(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    note: str = ""
    corrected: str | None = None


class PromoteBody(BaseModel):
    dataset: str
    min_score: float | None = None
    ids: list[str] | None = None
    preference: bool = False  # corrected traces only, stored as DPO pairs


def _cli(*args: str) -> list[str]:
    return [sys.executable, "-m", "tinystudio.cli", *args]


class TaskRunner:
    """Registry of CLI subprocesses started from the page."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.tasks: dict[str, Task] = {}
        self.task_dir = project.root / ".tasks"

    def start(self, kind: str, *args: str) -> dict[str, Any]:
        task = Task(kind, _cli(*args), self.task_dir / f"{kind}-{uuid.uuid4().hex[:6]}.log", self.project.root)
        self.tasks[task.id] = task
        return task.snapshot()

    def list(self) -> list[dict[str, Any]]:
        return [task.snapshot() for task in sorted(self.tasks.values(), key=lambda t: t.started, reverse=True)]

    def stop(self, task_id: str) -> dict[str, Any] | None:
        task = self.tasks.get(task_id)
        if task is None:
            return None
        if task.process.poll() is None:
            task.process.terminate()
        return task.snapshot()


def build_router(project: Project, loaded: dict[str, Any], load: Any, unload: Any, runner: TaskRunner | None = None) -> APIRouter:
    router = APIRouter()
    runner = runner or TaskRunner(project)

    def wrap(action: Any) -> Any:
        try:
            return action()
        except (ProjectError, data_mod.DatapointError) as cause:
            raise HTTPException(status_code=400, detail=str(cause)) from cause

    def start(kind: str, *args: str) -> dict[str, Any]:
        return runner.start(kind, *args)

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        return (UI_DIR / "index.html").read_text(encoding="utf-8")

    @router.get("/api/overview")
    def overview() -> dict[str, Any]:
        return {
            "project": project.manifest,
            "root": str(project.root),
            "tree": project.tree(),
            "metrics": [load_metric(project, m) for m in project.list_names("metrics")],
            "evals": [load_eval(project, e) for e in project.list_names("evals")],
            "catalog": catalog_as_dicts(),
            "llm": {"base_url": os.environ.get("TINYSTUDIO_LLM_BASE_URL"), "model": os.environ.get("TINYSTUDIO_LLM_MODEL")},
        }

    # datasets -------------------------------------------------------------

    @router.get("/api/datasets/{name}")
    def dataset(name: str, offset: int = 0, limit: int = 25) -> dict[str, Any]:
        rows = wrap(lambda: data_mod.load_dataset(project, name))
        return {"meta": data_mod.dataset_meta(project, name), "total": len(rows), "rows": rows[offset : offset + limit]}


    @router.post("/api/datasets/{name}/split")
    def split(name: str, body: SplitBody) -> dict[str, Any]:
        return wrap(lambda: data_mod.autosplit(project, name, body.train_pct, body.seed, body.synthetic_in_validation))


    @router.post("/api/snapshots")
    def snapshot(body: SnapshotBody) -> dict[str, Any]:
        return wrap(lambda: data_mod.create_snapshot(project, body.dataset, body.name))


    @router.post("/api/datapoints")
    def add_datapoint(body: DatapointBody) -> dict[str, Any]:
        return wrap(lambda: data_mod.add_datapoints(project, body.dataset, [{"messages": body.messages}], body.bucket))


    @router.post("/api/synth")
    def synth(body: SynthBody) -> dict[str, Any]:
        args = ["dataset", "synth", body.dataset, "--count", str(body.count)]
        for document in body.documents:
            if not Path(document).expanduser().exists():
                raise HTTPException(status_code=400, detail=f"{document} does not exist")
            args += ["--from", str(Path(document).expanduser())]
        if body.goal:
            args += ["--goal", body.goal]
        if body.system:
            args += ["--system", body.system]
        return start("synth", *args)


    @router.post("/api/augment")
    def augment(body: AugmentBody) -> dict[str, Any]:
        extra = ["--temperature", str(body.temperature)]
        if body.instructions.strip():
            extra += ["--more", body.instructions.strip()]
        if body.avoid.strip():
            extra += ["--avoid", body.avoid.strip()]
        return start("augment", "dataset", "augment", body.dataset, "--count", str(body.count), *extra)

    # fine-tuning ----------------------------------------------------------

    @router.get("/api/recommend/{snapshot}")
    def recommendation(snapshot: str) -> dict[str, Any]:
        train_rows, _ = wrap(lambda: data_mod.load_snapshot(project, snapshot))
        return recommend(train_rows, project.manifest.get("goal", ""))


    @router.post("/api/finetune")
    def finetune(body: FinetuneBody) -> dict[str, Any]:
        args = [
            "finetune", body.snapshot, "--job", body.job, "--model", body.model, "--type", body.training_type,
            "--epochs", str(body.epochs), "--lr", str(body.learning_rate), "--batch-size", str(body.batch_size),
            "--grad-accum", str(body.gradient_accumulation_steps), "--max-length", str(body.max_length),
            "--lora-r", str(body.lora_r), "--lora-alpha", str(body.lora_alpha),
        ]
        if body.assistant_only_loss:
            args.append("--assistant-only-loss")
        return start("finetune", *args)

    @router.get("/api/jobs")
    def jobs() -> list[dict[str, Any]]:
        return [{"job": job_meta(project, name), "experiments": list_experiments(project, name)} for name in project.list_names("jobs")]

    @router.get("/api/jobs/{job}/experiments/{number}")
    def experiment(job: str, number: int) -> dict[str, Any]:
        exp_dir = project.job_dir(job) / "experiments" / str(number)
        if not (exp_dir / "status.json").exists():
            raise HTTPException(status_code=404, detail=f"experiment {job}/{number} not found")
        return {"status": read_json(exp_dir / "status.json"), "config": read_json(exp_dir / "config.json"), "loss": read_loss_curve(project, job, number)}

    # evaluation -----------------------------------------------------------


    @router.post("/api/metrics")
    def metric(body: MetricBody) -> dict[str, Any]:
        return wrap(lambda: create_metric(project, body.name, body.kind, body.should, body.should_not, body.check, body.url, body.description))


    @router.post("/api/metrics/generate")
    def metric_generate(body: GenerateRulesBody) -> dict[str, Any]:
        args = ["metric", "generate", body.name, body.snapshot]
        if body.goal:
            args += ["--goal", body.goal]
        if body.description:
            args += ["--description", body.description]
        return start("rules", *args)


    @router.post("/api/evals")
    def run_eval(body: EvalBody) -> dict[str, Any]:
        if not body.models or not body.metrics:
            raise HTTPException(status_code=400, detail="pick at least one model and one metric")
        args = ["eval", "run", body.name, "--snapshot", body.snapshot, "--max-new-tokens", str(body.max_new_tokens)]
        for model in body.models:
            args += ["--model", model]
        for metric_name in body.metrics:
            args += ["--metric", metric_name]
        if body.limit:
            args += ["--limit", str(body.limit)]
        return start("eval", *args)

    @router.get("/api/evals/{name}")
    def eval_detail(name: str) -> dict[str, Any]:
        return {"summary": wrap(lambda: load_eval(project, name)), "rows": load_eval_rows(project, name)}

    # deployment -----------------------------------------------------------

    @router.get("/api/traces")
    def traces(limit: int = 100) -> list[dict[str, Any]]:
        return read_traces(project)[-limit:][::-1]


    @router.post("/api/traces/{trace_id}/score")
    def score(trace_id: str, body: ScoreBody) -> dict[str, Any]:
        return wrap(lambda: score_trace(project, trace_id, body.score, body.note, body.corrected))


    @router.post("/api/traces/promote")
    def promote_traces(body: PromoteBody) -> dict[str, Any]:
        count = wrap(lambda: promote(project, body.dataset, body.min_score, body.ids, preference=body.preference))
        return {"promoted": count}

    # tasks ----------------------------------------------------------------

    @router.get("/api/tasks")
    def list_tasks() -> list[dict[str, Any]]:
        return runner.list()

    @router.post("/api/tasks/{task_id}/stop")
    def stop_task(task_id: str) -> dict[str, Any]:
        snapshot = runner.stop(task_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="task not found")
        return snapshot

    return router
