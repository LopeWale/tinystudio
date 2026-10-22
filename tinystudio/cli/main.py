"""The ``tinystudio`` command: processes (serve, worker, inference), operations (db, users, projects, import), and the
data pipeline against the database. Long operations run inline here (useful on a laptop without a worker)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from tinystudio.core.data import DatapointError
from tinystudio.core.project import ProjectError

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Train, evaluate, serve, and inspect tiny and domain-specific language models.")
db_app = typer.Typer(no_args_is_help=True, help="Database migrations.")
users_app = typer.Typer(no_args_is_help=True, help="Accounts and API keys.")
projects_app = typer.Typer(no_args_is_help=True, help="Projects.")
dataset_app = typer.Typer(no_args_is_help=True, help="Create, extend, split, and inspect datasets.")
snapshot_app = typer.Typer(no_args_is_help=True, help="Freeze a dataset split for training.")
job_app = typer.Typer(no_args_is_help=True, help="Fine-tuning jobs: create, add experiments, run, analyze.")
metric_app = typer.Typer(no_args_is_help=True, help="Define rubric, code, or webhook metrics.")
eval_app = typer.Typer(no_args_is_help=True, help="Run and inspect evaluations.")
trace_app = typer.Typer(no_args_is_help=True, help="Review, score, and promote served inferences.")
hub_app = typer.Typer(no_args_is_help=True, help="Hugging Face Hub: search, download, cache, token, upload.")
repo_app = typer.Typer(no_args_is_help=True, help="Repositories: document collections with embeddings for retrieval-augmented chat.")
interp_app = typer.Typer(no_args_is_help=True, help="Interpretability: per-neuron activations, explanations and tests, comparisons, drift.")
autopilot_app = typer.Typer(no_args_is_help=True, help="Continuous fine-tuning cycles.")
for name, sub in (("db", db_app), ("users", users_app), ("projects", projects_app), ("dataset", dataset_app), ("snapshot", snapshot_app), ("job", job_app), ("metric", metric_app), ("eval", eval_app), ("trace", trace_app), ("hub", hub_app), ("repo", repo_app), ("interp", interp_app), ("autopilot", autopilot_app)):
    app.add_typer(sub, name=name)

console = Console()
err = Console(stderr=True)
ProjectOpt = Annotated[str | None, typer.Option("--project", "-p", envvar="TINYSTUDIO_PROJECT", help="Project slug.")]
JsonOpt = Annotated[bool, typer.Option("--json")]


def fail(message: str, code: int = 1) -> Any:
    err.print(f"[red]error:[/red] {escape(message)}")
    raise typer.Exit(code)


def emit(value: Any, as_json: bool) -> None:
    if as_json:
        console.print_json(json.dumps(value, default=str))
    else:
        console.print(escape(str(value)) if isinstance(value, str) else value)


def run(action: Any) -> Any:
    try:
        return action()
    except (ProjectError, DatapointError, ValueError) as cause:
        return fail(str(cause))


def project_slug(value: str | None) -> str:
    if not value:
        fail("pass --project SLUG (or set TINYSTUDIO_PROJECT); list projects with `tinystudio projects list`")
    return str(value)


def with_project(slug: str | None, action: Any) -> Any:
    """Run ``action(db, project)`` in one transaction."""
    from tinystudio.db import session
    from tinystudio.services.projects import get_project

    name = project_slug(slug)
    with session() as db:
        return run(lambda: action(db, get_project(db, name)))


def inline(kind: str, args: dict[str, Any], slug: str | None) -> dict[str, Any]:
    """Run a task in this process, echoing progress; exits non-zero when it fails."""
    from tinystudio.db import session
    from tinystudio.services.projects import get_project
    from tinystudio.worker import run_inline

    project = None
    if slug:
        with session() as db:
            project = run(lambda: get_project(db, slug))
    task = run_inline(kind, args, project, None, echo=err.print)
    if task["state"] != "succeeded":
        fail(task.get("error") or f"task {task['id']} {task['state']}")
    return task


# ---------------------------------------------------------------- processes
@app.command()
def serve(host: Annotated[str | None, typer.Option(help="Bind address; default TINYSTUDIO_HOST or 127.0.0.1.")] = None, port: Annotated[int | None, typer.Option(help="Port; default TINYSTUDIO_PORT or 8000.")] = None, base: Annotated[list[str] | None, typer.Option("--base", help="Base model ids to list as deployable baselines. Repeatable.")] = None, no_auto_up: Annotated[bool, typer.Option("--no-auto-up", help="Require POST /v1/models/{id}/up before chat.")] = False, no_migrate: Annotated[bool, typer.Option("--no-migrate", help="Do not apply database migrations at start.")] = False) -> None:
    """Run the API and the web interface (applies migrations first)."""
    import uvicorn

    from tinystudio.api.app import create_app
    from tinystudio.config import config

    cfg = config()
    application = create_app(run_migrations=not no_migrate, base_models=base or [], auto_up=not no_auto_up)
    err.print(f"web interface: http://{host or cfg.host}:{port or cfg.port}/   API: /api   OpenAI-compatible: /v1   inference: {cfg.inference_url or 'in this process'}")
    uvicorn.run(application, host=host or cfg.host, port=port or cfg.port, log_level="warning")


@app.command()
def worker(queue: Annotated[list[str] | None, typer.Option("--queue", help="Queue names to listen on. Repeatable.")] = None, burst: Annotated[bool, typer.Option(help="Exit when the queue is empty.")] = False) -> None:
    """Run a worker that executes queued tasks (training, evaluation, indexing, captures)."""
    from tinystudio.worker import work

    err.print(f"worker listening on {', '.join(queue or ['tinystudio'])}")
    work(queue, burst)


@app.command()
def inference(host: Annotated[str | None, typer.Option(help="Bind address; default TINYSTUDIO_INFERENCE_HOST or 0.0.0.0.")] = None, port: Annotated[int | None, typer.Option(help="Port; default TINYSTUDIO_INFERENCE_PORT or 8001.")] = None, base: Annotated[list[str] | None, typer.Option("--base")] = None) -> None:
    """Run the inference service (models in memory, OpenAI-compatible routes; the API proxies to it)."""
    import uvicorn

    from tinystudio.config import config
    from tinystudio.inference.app import create_inference_app

    cfg = config()
    uvicorn.run(create_inference_app(base or []), host=host or cfg.inference_host, port=port or cfg.inference_port, log_level="warning")


@app.command()
def dev(port: Annotated[int | None, typer.Option()] = None) -> None:
    """Developer convenience: the API (models in-process) plus one worker, in this terminal, until Ctrl-C."""
    from tinystudio.config import config

    cfg = config()
    env = {**os.environ, "TINYSTUDIO_PORT": str(port or cfg.port)}
    env.pop("TINYSTUDIO_INFERENCE_URL", None)
    procs = [subprocess.Popen([sys.executable, "-m", "tinystudio.cli", "serve"], env=env), subprocess.Popen([sys.executable, "-m", "tinystudio.cli", "worker"], env=env)]
    try:
        for proc in procs:
            proc.wait()
    except KeyboardInterrupt:
        for proc in procs:
            proc.terminate()


@app.command("config")
def config_show(as_json: JsonOpt = False) -> None:
    """Show the effective configuration (environment plus .env); secrets as set/unset."""
    from tinystudio.settings import load_dotenv, settings

    files = load_dotenv()
    values = settings().as_dict()
    if as_json:
        emit({"env_files": [str(f) for f in files], **values}, True)
        return
    console.print(f".env files read: {', '.join(str(f) for f in files) or 'none'}")
    for key, value in values.items():
        console.print(f"{key:22} {escape(str(value))}")


# ---------------------------------------------------------------- db, users, projects
@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply pending migrations."""
    from tinystudio.db.migrate import upgrade

    upgrade()
    console.print("database is up to date")


@db_app.command("current")
def db_current() -> None:
    from tinystudio.db.migrate import current

    current()


@db_app.command("revision")
def db_revision(message: str) -> None:
    """Generate a migration from model changes (development)."""
    from tinystudio.db.migrate import revision

    revision(message)


@users_app.command("create")
def users_create(email: str, name: str = "", admin: bool = False, password: Annotated[str | None, typer.Option(prompt=True, hide_input=True, confirmation_prompt=True)] = None) -> None:
    """Create an account."""
    from tinystudio.db import session
    from tinystudio.services.users import create_user, user_dict

    with session() as db:
        user = run(lambda: create_user(db, email, password or "", name, "admin" if admin else "member"))
        console.print(user_dict(user))


@users_app.command("list")
def users_list(as_json: JsonOpt = False) -> None:
    from tinystudio.db import session
    from tinystudio.services.users import list_users

    with session() as db:
        rows = list_users(db)
    if as_json:
        return emit(rows, True)
    table = Table("email", "name", "role", "disabled", "last login")
    for r in rows:
        table.add_row(r["email"], r["name"], r["role"], str(r["disabled"]), str(r["last_login"] or "-"))
    console.print(table)


@users_app.command("passwd")
def users_passwd(email: str, password: Annotated[str, typer.Option(prompt=True, hide_input=True, confirmation_prompt=True)]) -> None:
    from sqlalchemy import select

    from tinystudio.db import session
    from tinystudio.db.models import User
    from tinystudio.services.users import set_password

    with session() as db:
        user = db.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            fail("no such user")
        run(lambda: set_password(db, user, password))
    console.print("password changed")


@users_app.command("key")
def users_key(email: str, name: str = "cli") -> None:
    """Create an API key for an account (the plaintext is printed once)."""
    from sqlalchemy import select

    from tinystudio.db import session
    from tinystudio.db.models import User
    from tinystudio.services.users import create_key

    with session() as db:
        user = db.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            fail("no such user")
        record, plaintext = run(lambda: create_key(db, user, name))
    console.print(f"{record['prefix']}...  {plaintext}")


@projects_app.command("list")
def projects_list(as_json: JsonOpt = False) -> None:
    from tinystudio.db import session
    from tinystudio.services.projects import list_projects

    with session() as db:
        rows = list_projects(db)
    if as_json:
        return emit(rows, True)
    table = Table("slug", "name", "datasets", "jobs", "created")
    for r in rows:
        table.add_row(r["slug"], r["name"], str(r["datasets"]), str(r["jobs"]), str(r["created"]))
    console.print(table)


@projects_app.command("create")
def projects_create(name: str, goal: str = "") -> None:
    from tinystudio.db import session
    from tinystudio.services.projects import create_project, project_dict

    with session() as db:
        console.print(project_dict(run(lambda: create_project(db, name, goal))))


@projects_app.command("delete")
def projects_delete(slug: str, yes: Annotated[bool, typer.Option("--yes", help="Do not ask.")] = False) -> None:
    from tinystudio.services.projects import delete_project

    if not yes and not typer.confirm(f"delete project {slug} and everything in it?"):
        raise typer.Exit(1)
    with_project(slug, lambda db, p: delete_project(db, p))
    console.print(f"deleted {slug}")


@app.command("import-project")
def import_project(directory: Path, name: Annotated[str | None, typer.Option(help="Project name (default: the directory's manifest name).")] = None, copy: Annotated[bool, typer.Option("--copy", help="Copy weights instead of hard-linking them into the data directory.")] = False) -> None:
    """Import a file-based tinystudio project (the pre-database layout) into the database."""
    from tinystudio.services.importer import import_directory

    summary = run(lambda: import_directory(directory, name, link=not copy, progress=err.print))
    console.print(summary)


@app.command()
def mcp(url: Annotated[str, typer.Option(envvar="TINYSTUDIO_URL", help="Base URL of the tinystudio API.")] = "http://127.0.0.1:8000", project: ProjectOpt = None, key: Annotated[str | None, typer.Option(envvar="TINYSTUDIO_API_KEY", help="API key (API Keys page).")] = None) -> None:
    """Run the MCP server on stdio so Claude Code, Codex, or Claude Desktop can drive tinystudio."""
    from tinystudio.integrations.mcp_server import main as mcp_main

    mcp_main(url, project, key)


# ---------------------------------------------------------------- datasets and snapshots
@dataset_app.command("create")
def dataset_create(name: str, from_jsonl: Annotated[Path, typer.Option("--from-jsonl")], project: ProjectOpt = None) -> None:
    from tinystudio.services.datasets import create_from_jsonl

    meta = with_project(project, lambda db, p: create_from_jsonl(db, p, name, from_jsonl))
    console.print(f"dataset {name}: {meta['datapoints']} datapoints")


@dataset_app.command("add")
def dataset_add(name: str, jsonl: Path, bucket: Annotated[str | None, typer.Option()] = None, project: ProjectOpt = None) -> None:
    from tinystudio.core.data import read_jsonl
    from tinystudio.services.datasets import add_rows

    meta = with_project(project, lambda db, p: add_rows(db, p, name, list(read_jsonl(jsonl)), bucket))
    console.print(f"dataset {name}: {meta['datapoints']} datapoints")


@dataset_app.command("list")
def dataset_list(project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    from tinystudio.services.datasets import list_datasets

    rows = with_project(project, lambda db, p: list_datasets(db, p))
    if as_json:
        return emit(rows, True)
    table = Table("name", "datapoints", "uncategorized", "training", "validation", "snapshots")
    for r in rows:
        table.add_row(r["name"], str(r["datapoints"]), *(str(r["buckets"][b]) for b in ("uncategorized", "training", "validation")), str(r["snapshot_count"]))
    console.print(table)


@dataset_app.command("show")
def dataset_show(name: str, limit: int = 5, project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    from tinystudio.services.datasets import get_dataset, meta, rows

    out = with_project(project, lambda db, p: {"meta": meta(db, (ds := get_dataset(db, p, name))), "rows": rows(db, ds, 0, limit)})
    if as_json:
        return emit(out, True)
    console.print(out["meta"])
    for row in out["rows"]:
        console.print(escape(json.dumps(row, ensure_ascii=False)[:400]))


@dataset_app.command("split")
def dataset_split(name: str, train: int = 80, seed: int = 0, synthetic_in_validation: bool = False, project: ProjectOpt = None) -> None:
    from tinystudio.services.datasets import autosplit

    console.print(with_project(project, lambda db, p: autosplit(db, p, name, train, seed, synthetic_in_validation))["buckets"])


@dataset_app.command("labels")
def dataset_labels(name: str, label: Annotated[list[str], typer.Option("--label", help="name:description. Repeatable.")], project: ProjectOpt = None) -> None:
    from tinystudio.services.datasets import set_labels

    labels = [{"name": item.partition(":")[0].strip(), "description": item.partition(":")[2].strip()} for item in label]
    console.print(with_project(project, lambda db, p: set_labels(db, p, name, labels))["labels"])


@dataset_app.command("quality")
def dataset_quality(name: str, project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    from tinystudio.services.datasets import get_dataset, profile

    emit(with_project(project, lambda db, p: profile(db, get_dataset(db, p, name))), True)


@dataset_app.command("clean")
def dataset_clean(name: str, dedupe_user: bool = False, dedupe_conversation: bool = False, drop_near_duplicates: bool = False, drop_empty: bool = False, trim_whitespace: bool = False, min_chars: Annotated[int | None, typer.Option()] = None, max_chars: Annotated[int | None, typer.Option()] = None, min_rating: Annotated[float | None, typer.Option()] = None, require_json_answer: bool = False, redact_pii: bool = False, project: ProjectOpt = None) -> None:
    from tinystudio.core.quality import CleanSpec
    from tinystudio.services.datasets import apply_cleaning

    spec = CleanSpec(dedupe_user=dedupe_user, dedupe_conversation=dedupe_conversation, drop_near_duplicates=drop_near_duplicates, drop_empty=drop_empty, trim_whitespace=trim_whitespace, min_total_chars=min_chars, max_total_chars=max_chars, min_rating=min_rating, require_json_answer=require_json_answer, redact_pii=redact_pii)
    out = with_project(project, lambda db, p: apply_cleaning(db, p, name, spec))
    console.print(f"{out['before']} -> {out['after']} datapoints; removed {out['removed']}; redacted rows {out['redacted_rows']}")


@dataset_app.command("synth")
def dataset_synth(name: str, documents: Annotated[list[Path] | None, typer.Option("--from")] = None, urls: Annotated[list[str] | None, typer.Option("--url")] = None, count: Annotated[int | None, typer.Option()] = None, pairs_per_source: int = 25, temperature: float = 0.3, goal: str = "", system: Annotated[str | None, typer.Option()] = None, rule: Annotated[list[str] | None, typer.Option("--rule")] = None, curate_threshold: float = 0.0, project: ProjectOpt = None) -> None:
    """Generate question/answer datapoints from documents or web pages with the helper LLM."""
    if not documents and not urls:
        fail("pass --from FILE or --url URL")
    args = {"name": name, "files": [str(d.resolve()) for d in documents or []], "urls": list(urls or []), "count": count, "pairs_per_source": pairs_per_source, "temperature": temperature, "goal": goal, "system_prompt": system, "rules": list(rule or []), "curate_threshold": curate_threshold}
    task = inline("dataset.synthetic", args, project)
    console.print(f"dataset {name}: {(task.get('result') or {}).get('datapoints')} datapoints")


@dataset_app.command("augment")
def dataset_augment(name: str, count: int = 50, variants: int = 3, more: str = "", avoid: str = "", temperature: float = 0.8, project: ProjectOpt = None) -> None:
    task = inline("dataset.augment", {"dataset": name, "count": count, "variants": variants, "instructions": more, "avoid": avoid, "temperature": temperature}, project)
    console.print(f"dataset {name}: {(task.get('result') or {}).get('datapoints')} datapoints")


@dataset_app.command("auto-label")
def dataset_auto_label(name: str, project: ProjectOpt = None) -> None:
    inline("dataset.auto_label", {"dataset": name}, project)
    console.print(f"dataset {name}: labeled")


@dataset_app.command("delete")
def dataset_delete(name: str, project: ProjectOpt = None) -> None:
    from tinystudio.services.datasets import delete_dataset

    with_project(project, lambda db, p: delete_dataset(db, p, name))
    console.print(f"deleted {name}")


@snapshot_app.command("create")
def snapshot_create(dataset: str, name: str, min_training: int = 10, project: ProjectOpt = None) -> None:
    from tinystudio.services.snapshots import create_snapshot

    console.print(with_project(project, lambda db, p: create_snapshot(db, p, dataset, name, min_training)))


@snapshot_app.command("list")
def snapshot_list(project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    from tinystudio.services.snapshots import list_snapshots

    emit(with_project(project, lambda db, p: list_snapshots(db, p)), True)


# ---------------------------------------------------------------- jobs
@app.command()
def recommend(snapshot: str, goal: str = "", project: ProjectOpt = None) -> None:
    from tinystudio.services.snapshots import load_snapshot
    from tinystudio.training.recommend import recommend as recommend_fn

    emit(with_project(project, lambda db, p: recommend_fn(load_snapshot(db, p, snapshot)[0], goal or p.goal)), True)


@job_app.command("create")
def job_create(name: str, snapshot: Annotated[str, typer.Option()], goal: str = "", reasoning: bool = False, project: ProjectOpt = None) -> None:
    from tinystudio.services.jobs import create_job

    console.print(with_project(project, lambda db, p: create_job(db, p, name, snapshot, goal, reasoning)))


@job_app.command("add")
def job_add(job: str, model: Annotated[str, typer.Option()], type_: Annotated[str, typer.Option("--type")] = "lora", epochs: float = 3, lr: float = 2e-4, batch_size: int = 8, grad_accum: int = 2, max_length: int = 512, lora_r: int = 16, lora_alpha: int = 32, assistant_only_loss: bool = False, refinetune_from: Annotated[str | None, typer.Option()] = None, checkpoint_every: int = 0, keep_checkpoints: int = 0, max_steps: int = -1, project: ProjectOpt = None) -> None:
    """Add a pending experiment to a job."""
    from tinystudio.services.jobs import add_experiment, experiment_dict
    from tinystudio.training.train import TrainConfig

    cfg = run(lambda: TrainConfig(base_model=model, training_type=type_, epochs=epochs, learning_rate=lr, batch_size=batch_size, gradient_accumulation_steps=grad_accum, max_length=max_length, lora_r=lora_r, lora_alpha=lora_alpha, assistant_only_loss=assistant_only_loss, refinetune_from=refinetune_from, checkpoint_every_steps=checkpoint_every, keep_checkpoints=keep_checkpoints, max_steps=max_steps))
    exp = with_project(project, lambda db, p: experiment_dict(add_experiment(db, p, job, cfg), job))
    console.print(f"experiment {job}/{exp['number']} pending ({exp['alias']})")


@job_app.command("run")
def job_run(job: str, project: ProjectOpt = None) -> None:
    """Train every pending experiment of a job, in this process."""
    from tinystudio.services.jobs import get_job

    job_id = with_project(project, lambda db, p: get_job(db, p, job).id)
    task = inline("job.run", {"job_id": job_id, "job": job}, project)
    for status in (task.get("result") or {}).get("experiments", []):
        console.print(f"{job}/{status['number']}: {status['state']} train_loss={status.get('train_loss')} seconds={status.get('seconds')} {status.get('error') or ''}")


@job_app.command("analyze")
def job_analyze(job: str, project: ProjectOpt = None) -> None:
    emit(inline("job.analyze", {"job": job}, project).get("result"), True)


@job_app.command("list")
def job_list(project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    from tinystudio.services.jobs import list_jobs

    rows = with_project(project, lambda db, p: list_jobs(db, p))
    if as_json:
        return emit(rows, True)
    table = Table("job", "snapshot", "experiment", "model", "type", "state", "train loss", "eval loss")
    for item in rows:
        for e in item["experiments"]:
            table.add_row(item["job"]["name"], item["job"]["snapshot"], str(e["number"]), e["config"]["base_model"], e["config"]["training_type"], e["state"], str(e.get("train_loss")), str((e.get("metrics") or {}).get("eval_loss")))
    console.print(table)


@app.command()
def finetune(snapshot: str, job: Annotated[str, typer.Option()], model: Annotated[str, typer.Option()], type_: Annotated[str, typer.Option("--type")] = "lora", epochs: float = 3, lr: float = 2e-4, max_steps: int = -1, project: ProjectOpt = None) -> None:
    """Create a job (if needed), add one experiment, and train it now."""
    from tinystudio.services.jobs import add_experiment, create_job, get_job
    from tinystudio.training.train import TrainConfig

    def prepare(db: Any, p: Any) -> str:
        try:
            get_job(db, p, job)
        except ProjectError:
            create_job(db, p, job, snapshot)
        add_experiment(db, p, job, TrainConfig(base_model=model, training_type=type_, epochs=epochs, learning_rate=lr, max_steps=max_steps))
        return get_job(db, p, job).id

    job_id = with_project(project, prepare)
    task = inline("job.run", {"job_id": job_id, "job": job}, project)
    for status in (task.get("result") or {}).get("experiments", []):
        console.print(f"{job}/{status['number']}: {status['state']} train_loss={status.get('train_loss')} {status.get('error') or ''}")


@app.command()
def export(model: Annotated[str, typer.Argument(help="job/number")], destination: Path, ollama: bool = False, system: Annotated[str | None, typer.Option()] = None, project: ProjectOpt = None) -> None:
    """Merge a LoRA experiment into a standalone model directory (optionally with an Ollama Modelfile)."""
    from tinystudio.services.jobs import exp_path, find_experiment
    from tinystudio.training.train import export_merged, write_ollama_modelfile

    exp = with_project(project, lambda db, p: find_experiment(db, p, model))
    out = run(lambda: export_merged(exp_path(exp), destination, exp.config["base_model"], (exp.status or {}).get("weights_source")))
    if ollama:
        write_ollama_modelfile(out, out / "Modelfile", system)
    console.print(f"exported to {out}")


# ---------------------------------------------------------------- metrics and evaluations
@metric_app.command("create")
def metric_create(name: str, kind: str = "rubric", should: Annotated[list[str] | None, typer.Option("--should")] = None, should_not: Annotated[list[str] | None, typer.Option("--should-not")] = None, check: Annotated[str | None, typer.Option()] = None, url: Annotated[str | None, typer.Option()] = None, description: str = "", project: ProjectOpt = None) -> None:
    from tinystudio.services.metrics import create_metric

    console.print(with_project(project, lambda db, p: create_metric(db, p, name, kind, should, should_not, check, url, description)))


@metric_app.command("generate")
def metric_generate(name: str, snapshot: str, goal: str = "", description: str = "", project: ProjectOpt = None) -> None:
    emit(inline("metric.generate", {"name": name, "snapshot": snapshot, "goal": goal, "description": description}, project).get("result"), True)


@metric_app.command("list")
def metric_list(project: ProjectOpt = None) -> None:
    from tinystudio.services.metrics import list_metrics

    emit(with_project(project, lambda db, p: list_metrics(db, p)), True)


@eval_app.command("run")
def eval_run(name: str, snapshot: Annotated[str, typer.Option()], model: Annotated[list[str], typer.Option("--model")], metric: Annotated[list[str], typer.Option("--metric")], limit: Annotated[int | None, typer.Option()] = None, max_new_tokens: int = 256, project: ProjectOpt = None) -> None:
    """Evaluate models on a snapshot's validation split, in this process."""
    from tinystudio.services.evals import prepare_eval

    eval_id = with_project(project, lambda db, p: prepare_eval(db, p, name, snapshot, model, metric, limit).id)
    task = inline("eval.run", {"evaluation_id": eval_id, "name": name, "limit": limit, "max_new_tokens": max_new_tokens}, project)
    _print_leaderboard(task.get("result") or {})


@eval_app.command("show")
def eval_show(name: str, failures: bool = False, limit: int = 10, project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    from tinystudio.services.evals import eval_dict, eval_rows, get_eval

    out = with_project(project, lambda db, p: {"summary": eval_dict((e := get_eval(db, p, name))), "rows": eval_rows(db, e)})
    if as_json:
        return emit(out, True)
    _print_leaderboard(out["summary"])
    if failures:
        for row in sorted(out["rows"], key=lambda r: r["score"])[:limit]:
            console.print(f"[{row['score']:.2f}] {row['model']} / {row['metric']}: {escape(row['reason'][:160])}")


def _print_leaderboard(summary: dict[str, Any]) -> None:
    table = Table("model", "overall", *list((summary.get("leaderboard") or [{}])[0].get("scores", {}).keys()))
    for entry in summary.get("leaderboard") or []:
        table.add_row(entry["model"], str(entry.get("overall")), *(str(v) for v in entry["scores"].values()))
    console.print(table)


# ---------------------------------------------------------------- traces
@trace_app.command("list")
def trace_list(limit: int = 20, project: ProjectOpt = None, as_json: JsonOpt = False) -> None:
    from tinystudio.services.traces import list_traces

    rows = with_project(project, lambda db, p: list_traces(db, p, limit))
    if as_json:
        return emit(rows, True)
    for t in rows:
        console.print(f"{t['id']}  {t['time']}  {t['model']}  score={t.get('score')}  {escape((t.get('response') or '')[:80])}")


@trace_app.command("score")
def trace_score(trace_id: str, score: float, note: str = "", corrected: Annotated[str | None, typer.Option()] = None, project: ProjectOpt = None) -> None:
    from tinystudio.services.traces import score_trace

    console.print(with_project(project, lambda db, p: score_trace(db, p, trace_id, score, note, corrected)))


@trace_app.command("promote")
def trace_promote(dataset: str, min_score: Annotated[float | None, typer.Option()] = None, trace_id: Annotated[list[str] | None, typer.Option("--id")] = None, preference: bool = False, project: ProjectOpt = None) -> None:
    from tinystudio.services.traces import promote

    count = with_project(project, lambda db, p: promote(db, p, dataset, min_score, trace_id, preference=preference))
    console.print(f"promoted {count} traces into {dataset}")


# ---------------------------------------------------------------- hub
@hub_app.command("search")
def hub_search(query: str, limit: int = 15, max_params_b: Annotated[float | None, typer.Option()] = 2.0, task: str = "text-generation", as_json: JsonOpt = False) -> None:
    from tinystudio.integrations import hub

    rows = run(lambda: hub.search(query, limit, max_params_b, None, task))
    if as_json:
        return emit(rows, True)
    table = Table("model", "params", "downloads", "license", "gated", "loadable")
    for r in rows:
        table.add_row(r["id"], str(r.get("params_b")), str(r.get("downloads")), str(r.get("license")), str(r.get("gated")), str(r.get("transformers_loadable")))
    console.print(table)


@hub_app.command("download")
def hub_download(repo_id: str, revision: Annotated[str | None, typer.Option()] = None) -> None:
    emit(inline("hub.download", {"repo_id": repo_id, "revision": revision}, None).get("result"), True)


@hub_app.command("list")
def hub_list() -> None:
    from tinystudio.integrations import hub

    for m in hub.cached_models():
        console.print(f"{m['id']}  {m['size_gb']} GB  loadable={m['transformers_loadable']}")


@hub_app.command("upload")
def hub_upload(kind: str, name: str, repo_id: str, public: bool = False, merge: bool = False, project: ProjectOpt = None) -> None:
    emit(inline("hub.upload", {"kind": kind, "name": name, "repo_id": repo_id, "private": not public, "merge": merge}, project).get("result"), True)


# ---------------------------------------------------------------- repositories
@repo_app.command("create")
def repo_create(name: str, description: str = "", embedding_model: Annotated[str | None, typer.Option()] = None, project: ProjectOpt = None) -> None:
    from tinystudio.services.repositories import create_repository

    console.print(with_project(project, lambda db, p: create_repository(db, p, name, description, embedding_model)))


@repo_app.command("add")
def repo_add(name: str, source: Annotated[list[Path] | None, typer.Option("--from")] = None, url: Annotated[list[str] | None, typer.Option("--url")] = None, max_chars: int = 1200, overlap: int = 150, project: ProjectOpt = None) -> None:
    from tinystudio.services.repositories import get_repository

    repo_id = with_project(project, lambda db, p: get_repository(db, p, name).id)
    task = inline("repo.add", {"repository_id": repo_id, "repository": name, "paths": [str(s.resolve()) for s in source or []], "urls": list(url or []), "max_chars": max_chars, "overlap": overlap}, project)
    console.print(f"repository {name}: {(task.get('result') or {}).get('chunk_count')} chunks")


@repo_app.command("list")
def repo_list(project: ProjectOpt = None) -> None:
    from tinystudio.services.repositories import list_repositories

    emit(with_project(project, lambda db, p: list_repositories(db, p)), True)


@repo_app.command("query")
def repo_query(name: str, text: str, limit: int = 5, threshold: float = 0.0, project: ProjectOpt = None) -> None:
    from tinystudio.services.repositories import query

    emit(with_project(project, lambda db, p: query(db, p, name, text, limit, threshold)), True)


@repo_app.command("delete")
def repo_delete(name: str, document: Annotated[str | None, typer.Option()] = None, project: ProjectOpt = None) -> None:
    from tinystudio.services.repositories import delete_repository, remove_document

    if document:
        with_project(project, lambda db, p: remove_document(db, p, name, document))
    else:
        with_project(project, lambda db, p: delete_repository(db, p, name))
    console.print("deleted")


# ---------------------------------------------------------------- interpretability
def _layers(value: str | None) -> list[int]:
    return [int(x) for x in value.split(",") if x.strip()] if value else []


@interp_app.command("capture")
def interp_capture(model: str, dataset: Annotated[str | None, typer.Option()] = None, snapshot: Annotated[str | None, typer.Option()] = None, layers: Annotated[str | None, typer.Option(help="Comma-separated layer indices; default all.")] = None, topk: int = 40, name: Annotated[str | None, typer.Option()] = None, project: ProjectOpt = None) -> None:
    emit(inline("interp.capture", {"model": model, "dataset": dataset, "snapshot": snapshot, "layers": _layers(layers), "topk": topk, "name": name}, project).get("result"), True)


@interp_app.command("capture-checkpoints")
def interp_capture_checkpoints(experiment: str, dataset: Annotated[str | None, typer.Option()] = None, snapshot: Annotated[str | None, typer.Option()] = None, layers: Annotated[str | None, typer.Option()] = None, topk: int = 40, project: ProjectOpt = None) -> None:
    from tinystudio.training.train import parse_experiment_ref

    job, number = run(lambda: parse_experiment_ref(experiment))
    emit(inline("interp.capture_checkpoints", {"job": job, "number": number, "dataset": dataset, "snapshot": snapshot, "layers": _layers(layers), "topk": topk}, project).get("result"), True)


@interp_app.command("list")
def interp_list(project: ProjectOpt = None) -> None:
    from tinystudio.services.interp import overview

    emit(with_project(project, lambda db, p: overview(db, p)), True)


@interp_app.command("neurons")
def interp_neurons(capture: str, layer: Annotated[int, typer.Option("--layer")], sort: str = "max", limit: int = 30, query: str = "", project: ProjectOpt = None) -> None:
    from tinystudio.interp.neurons import neuron_table
    from tinystudio.services.interp import store_for

    emit(with_project(project, lambda db, p: neuron_table(store_for(p), capture, layer, sort=sort, limit=limit, query=query)), True)


@interp_app.command("show")
def interp_show(capture: str, layer: int, neuron: int, limit: int = 8, project: ProjectOpt = None) -> None:
    from tinystudio.interp.neurons import neuron_view
    from tinystudio.services.interp import store_for

    emit(with_project(project, lambda db, p: neuron_view(store_for(p), capture, layer, neuron, limit=limit)), True)


@interp_app.command("explain")
def interp_explain(capture: str, layer: int, neuron: Annotated[list[int] | None, typer.Option("--neuron")] = None, top: int = 0, sort: str = "max", project: ProjectOpt = None) -> None:
    emit(inline("interp.explain", {"capture": capture, "layer": layer, "neurons": list(neuron or []), "top": top, "sort": sort}, project).get("result"), True)


@interp_app.command("test")
def interp_test(capture: str, layer: int, neuron: Annotated[list[int], typer.Option("--neuron")], project: ProjectOpt = None) -> None:
    emit(inline("interp.test", {"capture": capture, "layer": layer, "neurons": list(neuron)}, project).get("result"), True)


@interp_app.command("diff")
def interp_diff(experiment: str, dataset: Annotated[str | None, typer.Option()] = None, snapshot: Annotated[str | None, typer.Option()] = None, base: Annotated[str | None, typer.Option()] = None, layers: Annotated[str | None, typer.Option()] = None, name: Annotated[str | None, typer.Option()] = None, project: ProjectOpt = None) -> None:
    emit(inline("interp.diff", {"experiment": experiment, "base": base, "dataset": dataset, "snapshot": snapshot, "layers": _layers(layers), "name": name}, project).get("result"), True)


@interp_app.command("diff-show")
def interp_diff_show(comparison: str, layer: int, neuron: Annotated[int | None, typer.Option("--neuron")] = None, limit: int = 20, sort: str = "change", project: ProjectOpt = None) -> None:
    from tinystudio.interp.neurons import diff_layer, diff_neuron
    from tinystudio.services.interp import store_for

    emit(with_project(project, lambda db, p: diff_neuron(store_for(p), comparison, layer, neuron) if neuron is not None else diff_layer(store_for(p), comparison, layer, limit=limit, sort=sort)), True)


@interp_app.command("drift")
def interp_drift(experiment: str, dataset: Annotated[str, typer.Option("--dataset")], layer: Annotated[int, typer.Option("--layer")], neuron: Annotated[int, typer.Option("--neuron")], project: ProjectOpt = None) -> None:
    from tinystudio.interp.neurons import drift
    from tinystudio.services.interp import store_for
    from tinystudio.training.train import parse_experiment_ref

    job, number = run(lambda: parse_experiment_ref(experiment))
    emit(with_project(project, lambda db, p: drift(store_for(p), job, number, dataset, layer, neuron)), True)


@interp_app.command("delete")
def interp_delete(kind: str, name: str, project: ProjectOpt = None) -> None:
    from tinystudio.interp.neurons import delete_capture, delete_diff
    from tinystudio.services.interp import store_for

    if kind not in ("capture", "comparison"):
        fail("kind must be capture or comparison")
    with_project(project, lambda db, p: (delete_capture if kind == "capture" else delete_diff)(store_for(p), name))
    console.print("deleted")


# ---------------------------------------------------------------- autopilot
@autopilot_app.command("run")
def autopilot_run(name: str, dataset: Annotated[str, typer.Option()], model: Annotated[str | None, typer.Option()] = None, type_: Annotated[str, typer.Option("--type")] = "lora", epochs: float = 2.0, lr: Annotated[float | None, typer.Option()] = None, min_trace_score: Annotated[float | None, typer.Option()] = None, preference: bool = False, clean: bool = True, train_pct: int = 85, metric: Annotated[list[str] | None, typer.Option("--metric")] = None, eval_limit: Annotated[int | None, typer.Option()] = None, max_steps: int = -1, project: ProjectOpt = None) -> None:
    args = {"name": name, "dataset": dataset, "base_model": model, "training_type": type_, "epochs": epochs, "learning_rate": lr, "min_trace_score": min_trace_score, "preference": preference, "clean": clean, "train_pct": train_pct, "metrics": list(metric or []), "eval_limit": eval_limit, "max_steps": max_steps}
    emit(inline("autopilot.run", args, project).get("result"), True)


@autopilot_app.command("history")
def autopilot_history(name: Annotated[str | None, typer.Argument()] = None, project: ProjectOpt = None) -> None:
    from tinystudio.services.autopilot import history, list_histories

    emit(with_project(project, lambda db, p: history(db, p, name) if name else list_histories(db, p)), True)


# ---------------------------------------------------------------- chat
@app.command()
def chat(model: str, system: Annotated[str | None, typer.Option()] = None, base_url: Annotated[str | None, typer.Option(help="Talk to a running server instead of loading the model here.")] = None, temperature: float = 0.0, max_tokens: int = 256, project: ProjectOpt = None) -> None:
    """Interactive chat with a model. /reset clears history, /quit leaves."""
    from tinystudio.core.llm import LLMClient

    history: list[dict[str, str]] = [{"role": "system", "content": system}] if system else []
    if base_url:
        client = LLMClient(base_url.rstrip("/"), model)

        def reply(messages: list[dict[str, str]]) -> str:
            return client.chat(messages, temperature=temperature, max_tokens=max_tokens)
    else:
        from tinystudio.db import session
        from tinystudio.serving.runner import ModelRunner
        from tinystudio.services.models import resolve
        from tinystudio.services.projects import get_project

        with session() as db:
            p = get_project(db, project) if project else None
            resolved = run(lambda: resolve(db, p, model))
        err.print(f"loading {model}")
        runner = run(lambda: ModelRunner(resolved))

        def reply(messages: list[dict[str, str]]) -> str:
            pieces = []
            for delta in runner.stream(messages, max_tokens, temperature):
                pieces.append(delta)
                sys.stdout.write(delta)
                sys.stdout.flush()
            sys.stdout.write("\n")
            return "".join(pieces).strip()

    while True:
        try:
            text = console.input("[bold cyan]you>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        if text == "/reset":
            history = history[:1] if system else []
            continue
        history.append({"role": "user", "content": text})
        answer = run(lambda: reply(history))
        if base_url:
            console.print(escape(answer))
        history.append({"role": "assistant", "content": answer})


def _terminate(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt


def main() -> None:
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    signal.signal(signal.SIGTERM, _terminate)
    app()


if __name__ == "__main__":
    main()
