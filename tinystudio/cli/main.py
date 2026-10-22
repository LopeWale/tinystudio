"""tinystudio command line.

Studio's four tabs map to command groups:

    Datasets     tinystudio dataset ... / snapshot ...
    Fine-tuning  tinystudio recommend / finetune / jobs / export
    Evaluations  tinystudio metric ... / eval ...
    Deployment   tinystudio serve / chat / trace ...
"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from tinystudio.core import data as data_mod
from tinystudio.core.documents import DocumentError
from tinystudio.core.llm import LLMClient, LLMError
from tinystudio.core.project import Project, ProjectError

HELP = """Train, evaluate, and serve tiny domain-specific language models locally.

Datasets: dataset ..., snapshot ...   Fine-tuning: recommend, finetune, jobs, losses, export
Evaluations: metric ..., eval ...   Deployment: serve, chat, trace ..."""
app = typer.Typer(no_args_is_help=True, add_completion=False, help=HELP, rich_markup_mode=None)
dataset_app = typer.Typer(no_args_is_help=True, help="Create, extend, split, and inspect datasets.")
snapshot_app = typer.Typer(no_args_is_help=True, help="Freeze a dataset split for training.")
metric_app = typer.Typer(no_args_is_help=True, help="Define rubric, code, or webhook metrics.")
eval_app = typer.Typer(no_args_is_help=True, help="Run and inspect evaluations.")
trace_app = typer.Typer(no_args_is_help=True, help="Review, score, and promote served inferences.")
job_app = typer.Typer(no_args_is_help=True, help="Fine-tuning jobs: create, add experiments, run, analyze.")
hub_app = typer.Typer(no_args_is_help=True, help="Hugging Face Hub: search, download, cache, token, upload.")
autopilot_app = typer.Typer(no_args_is_help=True, help="Continuous fine-tuning cycles: traces -> dataset -> snapshot -> refinetune -> evaluate.")
interp_app = typer.Typer(no_args_is_help=True, help="Interpretability: per-neuron activations, explanations and tests, before/after comparisons, checkpoint drift.")
repo_app = typer.Typer(no_args_is_help=True, help="Repositories: document collections with embeddings for retrieval-augmented chat.")
for name, sub in (("dataset", dataset_app), ("snapshot", snapshot_app), ("job", job_app), ("metric", metric_app), ("eval", eval_app), ("trace", trace_app), ("hub", hub_app), ("autopilot", autopilot_app), ("interp", interp_app), ("repo", repo_app)):
    app.add_typer(sub, name=name)

console = Console()
err = Console(stderr=True)

ProjectOpt = Annotated[Path | None, typer.Option("--project", "-p", envvar="TINYSTUDIO_PROJECT", help="Project directory (default: current directory).")]
JudgeUrlOpt = Annotated[str | None, typer.Option("--llm-url", envvar="TINYSTUDIO_LLM_BASE_URL", help="OpenAI-compatible base URL of the helper/judge model, e.g. http://127.0.0.1:8080/v1")]
JudgeModelOpt = Annotated[str | None, typer.Option("--llm-model", envvar="TINYSTUDIO_LLM_MODEL", help="Model name at --llm-url (optional if the server lists exactly one).")]


def open_project(path: Path | None) -> Project:
    try:
        return Project.open(path or Path.cwd())
    except ProjectError as cause:
        fail(str(cause))


def fail(message: str, code: int = 1) -> Any:
    err.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code)


def emit(value: Any, as_json: bool) -> None:
    if as_json:
        console.print_json(json.dumps(value, ensure_ascii=False, default=str))
    else:
        console.print(value)


def llm_client(url: str | None, model: str | None) -> LLMClient:
    return LLMClient.from_env(url, model)


def run(action: Any) -> Any:
    try:
        return action()
    except (ProjectError, data_mod.DatapointError, LLMError, DocumentError) as cause:
        fail(str(cause))
    except FileNotFoundError as cause:
        fail(f"file not found: {cause.filename}")


# ----------------------------------------------------------------- project


@app.command()
def init(name: str, path: Annotated[Path | None, typer.Argument(help="Directory to create (default: ./NAME)")] = None, goal: Annotated[str, typer.Option(help="What the model should become good at; used by recommend, synth, and rule generation.")] = "") -> None:
    """Create a project directory."""
    project = run(lambda: Project.create(path or Path(name), name, goal))
    console.print(f"created project [bold]{name}[/bold] at {project.root}")


@app.command()
def tree(project: ProjectOpt = None, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show datasets, snapshots, jobs, and experiments."""
    p = open_project(project)
    value = p.tree()
    if as_json:
        emit(value, True)
        return
    console.print(f"[bold]{value['project']['name']}[/bold]  goal: {value['project'].get('goal') or '-'}")
    for dataset in value["datasets"]:
        console.print(f"  dataset {dataset['name']}: {dataset['datapoints']} datapoints {dataset['buckets']}")
        for snapshot in dataset["snapshots"]:
            console.print(f"    snapshot {snapshot['name']}: train {snapshot['training_count']} / val {snapshot['validation_count']}")
            for job in snapshot["jobs"]:
                console.print(f"      job {job['name']}: experiments {job['experiments']}")
    for metric in p.list_names("metrics"):
        console.print(f"  metric {metric}")
    for evaluation in p.list_names("evals"):
        console.print(f"  eval {evaluation}")


# ----------------------------------------------------------------- datasets


@dataset_app.command("create")
def dataset_create(name: str, from_jsonl: Annotated[Path, typer.Option("--from-jsonl", help="JSONL of {\"messages\": [...]} records")], project: ProjectOpt = None) -> None:
    """Create a dataset from a JSONL file of chat records."""
    p = open_project(project)
    meta = run(lambda: data_mod.create_dataset_from_jsonl(p, name, from_jsonl))
    console.print(f"dataset {name}: {meta['datapoints']} datapoints")


@dataset_app.command("add")
def dataset_add(name: str, jsonl: Path, bucket: Annotated[str | None, typer.Option(help="Force every added record into this bucket: uncategorized, training, or validation.")] = None, project: ProjectOpt = None) -> None:
    """Append records from a JSONL file."""
    p = open_project(project)
    meta = run(lambda: data_mod.add_datapoints(p, name, data_mod.read_jsonl(jsonl), bucket))
    console.print(f"dataset {name}: {meta['datapoints']} datapoints {meta['buckets']}")


@dataset_app.command("synth")
def dataset_synth(
    name: str,
    documents: Annotated[list[Path] | None, typer.Option("--from", help="Text, Markdown, HTML, DOCX-as-text, or PDF files to ground the questions in.")] = None,
    urls: Annotated[list[str] | None, typer.Option("--url", help="Web pages to ground the questions in.")] = None,
    spec: Annotated[Path | None, typer.Option(help="JSON spec written by the web wizard (files, urls, pairs_per_source, temperature, rules, formats, curate_threshold).")] = None,
    count: Annotated[int | None, typer.Option(help="Total pairs wanted; split evenly across sources.")] = None,
    pairs_per_source: int = 25,
    temperature: Annotated[float, typer.Option(help="Creativity level 0-1.")] = 0.3,
    goal: str = "",
    system: Annotated[str | None, typer.Option(help="System prompt to prepend to every generated datapoint.")] = None,
    rule: Annotated[list[str] | None, typer.Option("--rule", help="Rules and constraints for the generator. Repeatable.")] = None,
    question_format: str = "",
    answer_format: str = "",
    curate_threshold: Annotated[float, typer.Option(help="Keep only pairs the helper rates at or above this 1-10 score; 0 disables curation.")] = 0.0,
    llm_url: JudgeUrlOpt = None,
    llm_model: JudgeModelOpt = None,
    project: ProjectOpt = None,
) -> None:
    """Generate question/answer datapoints from documents or web pages with a local helper model. Creates the dataset if needed."""
    import math

    from tinystudio.core.synth import SynthSpec, load_sources, synthesize

    p = open_project(project)
    client = llm_client(llm_url, llm_model)
    if spec is not None:
        loaded = json.loads(spec.read_text(encoding="utf-8"))
        paths = [Path(f) for f in loaded.get("files", [])]
        url_list = list(loaded.get("urls", []))
        config = SynthSpec(**{k: v for k, v in loaded.items() if k in SynthSpec.__dataclass_fields__})
    else:
        paths = list(documents or [])
        url_list = list(urls or [])
        if not paths and not url_list:
            fail("pass --from FILE, --url URL, or --spec SPEC.json")
        per_source = max(1, math.ceil(count / (len(paths) + len(url_list)))) if count else pairs_per_source
        config = SynthSpec(pairs_per_source=per_source, temperature=temperature, goal=goal or p.manifest.get("goal", ""), rules=list(rule or []), question_format=question_format, answer_format=answer_format, system_prompt=system, curate_threshold=curate_threshold)
    sources = run(lambda: load_sources(paths, url_list))
    rows = run(lambda: synthesize(client, sources, config, progress=err.print))
    origin = {"kind": "synthetic", "documents": [s.name for s in sources], "pairs_per_source": config.pairs_per_source, "temperature": config.temperature, "curate_threshold": config.curate_threshold}
    if (p.dataset_dir(name) / "meta.json").exists():
        meta = run(lambda: data_mod.add_datapoints(p, name, rows))
    else:
        meta = run(lambda: data_mod.save_dataset(p, name, rows, {"origin": origin}))
    console.print(f"dataset {name}: {meta['datapoints']} datapoints")


@dataset_app.command("labels")
def dataset_labels(name: str, label: Annotated[list[str], typer.Option("--label", help="name:description. Repeatable.")], project: ProjectOpt = None) -> None:
    """Define the label vocabulary of a dataset."""
    p = open_project(project)
    labels = [{"name": item.partition(":")[0].strip(), "description": item.partition(":")[2].strip()} for item in label]
    meta = run(lambda: data_mod.set_labels(p, name, labels))
    console.print(f"dataset {name}: labels {[l['name'] for l in meta['labels']]}")


@dataset_app.command("auto-label")
def dataset_auto_label(name: str, llm_url: JudgeUrlOpt = None, llm_model: JudgeModelOpt = None, project: ProjectOpt = None) -> None:
    """Assign one of the defined labels to every datapoint with the helper model."""
    from tinystudio.core.synth import auto_label

    p = open_project(project)
    meta = run(lambda: data_mod.dataset_meta(p, name))
    rows = run(lambda: data_mod.load_dataset(p, name))
    client = llm_client(llm_url, llm_model)
    labeled = run(lambda: auto_label(client, rows, meta.get("labels", []), progress=err.print))
    data_mod.save_dataset(p, name, labeled)
    console.print(f"dataset {name}: labeled {len(labeled)} datapoints")


@dataset_app.command("augment")
def dataset_augment(
    name: str,
    count: int = 50,
    variants: int = 3,
    more: Annotated[str, typer.Option("--more", help="Positive instructions: what to generate more of.")] = "",
    avoid: Annotated[str, typer.Option("--avoid", help="Negative instructions: what to avoid.")] = "",
    temperature: Annotated[float, typer.Option(help="Creativity level for the paraphrases, 0.0-1.5.", min=0.0, max=1.5)] = 0.8,
    llm_url: JudgeUrlOpt = None,
    llm_model: JudgeModelOpt = None,
    project: ProjectOpt = None,
) -> None:
    """Paraphrase user turns of existing datapoints to add variety (Studio's enrichment with creativity and positive/negative instructions)."""
    from tinystudio.core.synth import augment

    p = open_project(project)
    client = llm_client(llm_url, llm_model)
    rows = run(lambda: data_mod.load_dataset(p, name))
    produced = run(lambda: augment(client, rows, count, variants, progress=err.print, instructions=more, avoid=avoid, temperature=temperature))
    meta = run(lambda: data_mod.add_datapoints(p, name, produced))
    console.print(f"dataset {name}: {meta['datapoints']} datapoints")


@dataset_app.command("split")
def dataset_split(name: str, train: Annotated[int, typer.Option(help="Percent of uncategorized datapoints to assign to training.")] = 80, seed: int = 0, synthetic_in_validation: bool = False, project: ProjectOpt = None) -> None:
    """Assign uncategorized datapoints to training/validation buckets."""
    p = open_project(project)
    meta = run(lambda: data_mod.autosplit(p, name, train, seed, synthetic_in_validation))
    console.print(f"dataset {name}: {meta['buckets']}")


@dataset_app.command("show")
def dataset_show(name: str, limit: int = 5, project: ProjectOpt = None, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Print dataset metadata and the first datapoints."""
    p = open_project(project)
    meta = run(lambda: data_mod.dataset_meta(p, name))
    rows = run(lambda: data_mod.load_dataset(p, name))
    if as_json:
        emit({"meta": meta, "datapoints": rows[:limit]}, True)
        return
    console.print(meta)
    for row in rows[:limit]:
        console.rule()
        for message in row["messages"]:
            console.print(f"[bold]{message['role']}:[/bold] {escape(message['content'])}")
        console.print(f"[dim]bucket={row['bucket']} labels={escape(str(row['labels']))} source={escape(row.get('source', '-'))}[/dim]")


@dataset_app.command("list")
def dataset_list(project: ProjectOpt = None) -> None:
    p = open_project(project)
    for name in p.list_names("datasets"):
        meta = data_mod.dataset_meta(p, name)
        console.print(f"{name}: {meta['datapoints']} datapoints {meta['buckets']}")


# ----------------------------------------------------------------- snapshots


@snapshot_app.command("create")
def snapshot_create(dataset: str, name: str, min_training: int = 10, project: ProjectOpt = None) -> None:
    """Freeze the dataset's training and validation buckets."""
    p = open_project(project)
    meta = run(lambda: data_mod.create_snapshot(p, dataset, name, min_training))
    console.print(f"snapshot {name}: train {meta['training_count']} / val {meta['validation_count']}")


@snapshot_app.command("list")
def snapshot_list(project: ProjectOpt = None) -> None:
    p = open_project(project)
    for name in p.list_names("snapshots"):
        meta = data_mod.snapshot_meta(p, name)
        console.print(f"{name} (dataset {meta['dataset']}): train {meta['training_count']} / val {meta['validation_count']}")


# ----------------------------------------------------------------- fine-tuning


@app.command()
def recommend(snapshot: str, goal: str = "", project: ProjectOpt = None, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Recommend base models and hyperparameters for a snapshot on this machine."""
    from tinystudio.training.recommend import recommend as recommend_fn

    p = open_project(project)
    train_rows, _ = run(lambda: data_mod.load_snapshot(p, snapshot))
    result = recommend_fn(train_rows, goal or p.manifest.get("goal", ""))
    if as_json:
        emit(result, True)
        return
    console.print(f"training datapoints: {result['dataset']['training_count']}  avg {result['dataset']['avg_chars']} chars  max {result['dataset']['max_chars']} chars")
    console.print(f"host memory: {result['host']['memory_gib']} GiB  (budget {result['host']['budget_gib']} GiB)")
    table = Table("rank", "base model", "params", "type", "epochs", "lr", "batch", "max_len", show_lines=False)
    for index, option in enumerate(result["options"], start=1):
        h = option["hyperparameters"]
        table.add_row(str(index), option["base_model"], f"{option['params_b']}B", option["training_type"], str(h["epochs"]), f"{h['learning_rate']:g}", f"{h['batch_size']}x{h['gradient_accumulation_steps']}", str(h["max_length"]))
    console.print(table)
    for index, option in enumerate(result["options"], start=1):
        console.print(f"  {index}. {option['base_model']}: {option['why']}")
    for note in result["notes"]:
        console.print(f"[dim]- {note}[/dim]")
    if result["recommended"]:
        r = result["recommended"]
        console.print(f"\nrun: tinystudio finetune {snapshot} --job <name> --model {r['base_model']} --type {r['training_type']} --epochs {r['hyperparameters']['epochs']} --lr {r['hyperparameters']['learning_rate']:g}")


@app.command()
def finetune(
    snapshot: str,
    job: Annotated[str, typer.Option(help="Job name; created if new, otherwise a new experiment is added.")],
    model: Annotated[str | None, typer.Option("--model", help="Base model id (default: the recommendation).")] = None,
    training_type: Annotated[str | None, typer.Option("--type", help="lora, full, qlora (4-bit base, CUDA + bitsandbytes), or dpo (preference pairs; default lr 5e-6)")] = None,
    epochs: float | None = None,
    lr: float | None = None,
    batch_size: int | None = None,
    grad_accum: int | None = None,
    max_length: int | None = None,
    lora_r: int | None = None,
    lora_alpha: int | None = None,
    assistant_only_loss: bool = False,
    refinetune_from: Annotated[str | None, typer.Option(help="Continue from a finished experiment (job/number or ft-job-number).")] = None,
    max_steps: int = -1,
    seed: int = 42,
    goal: str = "",
    checkpoint_every: Annotated[int, typer.Option(help="Save weights-only checkpoints every N steps (for `interp drift`); 0 = off.")] = 0,
    keep_checkpoints: Annotated[int, typer.Option(help="Keep only the newest K checkpoints; 0 keeps all.")] = 0,
    project: ProjectOpt = None,
) -> None:
    """Train one experiment on a snapshot. Writes jobs/JOB/experiments/N/ with config, status, and loss log."""
    from tinystudio.training.recommend import recommend as recommend_fn
    from tinystudio.training.train import TrainConfig, create_job, job_meta, run_experiment

    p = open_project(project)
    train_rows, _ = run(lambda: data_mod.load_snapshot(p, snapshot))
    if not (p.job_dir(job) / "job.json").exists():
        run(lambda: create_job(p, job, snapshot, goal or p.manifest.get("goal", "")))
    elif job_meta(p, job)["snapshot"] != snapshot:
        fail(f"job {job!r} is pinned to snapshot {job_meta(p, job)['snapshot']!r}; use a new job name for {snapshot!r}")

    suggestion = recommend_fn(train_rows)["recommended"]
    if suggestion is None:
        fail("no base model fits this machine's memory budget")
    chosen_model = model or suggestion["base_model"]
    matching = next((o for o in recommend_fn(train_rows)["options"] if o["base_model"] == chosen_model), suggestion)
    h = matching["hyperparameters"]
    chosen_type = training_type or matching["training_type"]
    config = run(
        lambda: TrainConfig(
            base_model=chosen_model,
            training_type=chosen_type,
            epochs=epochs if epochs is not None else h["epochs"],
            learning_rate=lr if lr is not None else (5e-6 if chosen_type == "dpo" else h["learning_rate"] if chosen_type == matching["training_type"] else (5e-5 if chosen_type == "full" else 2e-4)),
            batch_size=batch_size or h["batch_size"],
            gradient_accumulation_steps=grad_accum or h["gradient_accumulation_steps"],
            max_length=max_length or h["max_length"],
            lora_r=lora_r or h["lora_r"],
            lora_alpha=lora_alpha or h["lora_alpha"],
            assistant_only_loss=assistant_only_loss,
            refinetune_from=refinetune_from,
            max_steps=max_steps,
            checkpoint_every_steps=checkpoint_every,
            keep_checkpoints=keep_checkpoints,
            seed=seed,
        )
    )
    console.print(f"job [bold]{job}[/bold] snapshot {snapshot}: {config.base_model} {config.training_type} epochs={config.epochs} lr={config.learning_rate:g} batch={config.batch_size}x{config.gradient_accumulation_steps} max_length={config.max_length}")
    status = run_experiment(p, job, config, progress=err.print)
    if status["state"] != "succeeded":
        fail(escape(f"experiment failed: {status.get('error')}\n{status.get('traceback', '')}"))
    console.print(f"experiment [bold]{job}/{status.get('number', len(job_meta(p, job)['experiments']))}[/bold] succeeded in {status['seconds']} s: train_loss={status['train_loss']:.4f} eval_loss={status['metrics'].get('eval_loss', float('nan')):.4f}")
    console.print(f"weights: {status['output']}")
    console.print(f"next: tinystudio chat {job}/{len(job_meta(p, job)['experiments'])}   or   tinystudio eval run <name> --snapshot {snapshot} --model {job}/{len(job_meta(p, job)['experiments'])} --model {config.base_model} --metric <metric>")


@job_app.command("create")
def job_create(name: str, snapshot: Annotated[str, typer.Option()], goal: str = "", reasoning: bool = False, project: ProjectOpt = None) -> None:
    """Create a job on a snapshot without experiments (Studio's New Finetuning Job)."""
    from tinystudio.training.train import create_job

    p = open_project(project)
    job = run(lambda: create_job(p, name, snapshot, goal or p.manifest.get("goal", ""), reasoning))
    console.print(f"job {job['name']} on snapshot {job['snapshot']}: ready to add experiments")


@job_app.command("add")
def job_add(
    job: str,
    model: Annotated[str, typer.Option("--model")],
    training_type: Annotated[str, typer.Option("--type")] = "lora",
    epochs: float = 3,
    lr: Annotated[float | None, typer.Option(help="Default 2e-4 for lora, 5e-5 for full, 5e-6 for dpo.")] = None,
    batch_size: int = 8,
    grad_accum: int = 2,
    max_length: int = 512,
    lora_r: int = 16,
    lora_alpha: int = 32,
    assistant_only_loss: bool = False,
    refinetune_from: Annotated[str | None, typer.Option(help="Continue from a finished experiment (job/number or ft-job-number).")] = None,
    checkpoint_every: Annotated[int, typer.Option(help="Save weights-only checkpoints every N steps; 0 = off.")] = 0,
    keep_checkpoints: Annotated[int, typer.Option(help="Keep only the newest K checkpoints; 0 keeps all.")] = 0,
    project: ProjectOpt = None,
) -> None:
    """Add a pending experiment to a job."""
    from tinystudio.training.train import TrainConfig, create_pending_experiment

    p = open_project(project)
    config = run(lambda: TrainConfig(base_model=model, training_type=training_type, epochs=epochs, learning_rate=lr if lr is not None else (5e-6 if training_type == "dpo" else 5e-5 if training_type == "full" else 2e-4), batch_size=batch_size, gradient_accumulation_steps=grad_accum, max_length=max_length, lora_r=lora_r, lora_alpha=lora_alpha, assistant_only_loss=assistant_only_loss, refinetune_from=refinetune_from, checkpoint_every_steps=checkpoint_every, keep_checkpoints=keep_checkpoints))
    number = run(lambda: create_pending_experiment(p, job, config))
    console.print(f"experiment {job}/{number} pending (alias ft-{job}-{number}); run with: tinystudio job run {job}")


@job_app.command("run")
def job_run(job: str, project: ProjectOpt = None) -> None:
    """Train every pending experiment of a job, one after another."""
    from tinystudio.training.train import run_pending_experiments

    p = open_project(project)
    results = run(lambda: run_pending_experiments(p, job, progress=err.print))
    if not results:
        fail(f"job {job!r} has no pending experiments")
    failed = [r for r in results if r["state"] != "succeeded"]
    for r in results:
        console.print(f"experiment {job}/{r['number']}: {r['state']}  train_loss={r.get('train_loss', float('nan')):.4f} eval_loss={r.get('metrics', {}).get('eval_loss', float('nan')):.4f}  {escape(str(r.get('error', '')))}")
    if failed:
        raise typer.Exit(1)


@job_app.command("analyze")
def job_analyze(job: str, llm_url: JudgeUrlOpt = None, llm_model: JudgeModelOpt = None, project: ProjectOpt = None) -> None:
    """Ask the helper model for the dataset analysis shown next to the experiments."""
    from tinystudio.evaluation.analysis import analyze_job

    p = open_project(project)
    client = llm_client(llm_url, llm_model)
    document = run(lambda: analyze_job(p, job, client))
    console.print_json(json.dumps(document["analysis"], ensure_ascii=False))


@app.command()
def jobs(project: ProjectOpt = None, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List jobs and their experiments."""
    from tinystudio.training.train import job_meta, list_experiments

    p = open_project(project)
    rows = [{"job": job_meta(p, name), "experiments": list_experiments(p, name)} for name in p.list_names("jobs")]
    if as_json:
        emit(rows, True)
        return
    for entry in rows:
        console.print(f"[bold]{entry['job']['name']}[/bold] snapshot={entry['job']['snapshot']}")
        for exp in entry["experiments"]:
            c = exp["config"]
            metrics = exp.get("metrics", {})
            console.print(f"  {exp['number']}: {exp['state']}  {escape(c['base_model'])} {c['training_type']} epochs={c['epochs']} lr={c['learning_rate']:g}  train_loss={exp.get('train_loss', float('nan')):.4f} eval_loss={metrics.get('eval_loss', float('nan')):.4f}  {escape(exp.get('error', ''))}")


@app.command()
def losses(model: Annotated[str, typer.Argument(help="job/experiment")], project: ProjectOpt = None) -> None:
    """Print the logged loss curve of an experiment."""
    from tinystudio.training.train import read_loss_curve

    p = open_project(project)
    job, _, number = model.partition("/")
    for row in read_loss_curve(p, job, int(number or 1)):
        shown = {k: row[k] for k in ("loss", "eval_loss", "learning_rate", "mean_token_accuracy", "eval_mean_token_accuracy") if k in row}
        if shown:
            console.print(f"step {row['step']:>5}  epoch {row['epoch']:<5.2f} " + "  ".join(f"{k}={v:.4g}" for k, v in shown.items()))


@app.command()
def export(model: Annotated[str, typer.Argument(help="job/experiment")], destination: Path, ollama: Annotated[bool, typer.Option(help="Also write an Ollama Modelfile next to the weights (then: ollama create NAME -f DIR/Modelfile).")] = False, system: Annotated[str | None, typer.Option(help="System prompt to bake into the Modelfile.")] = None, project: ProjectOpt = None) -> None:
    """Merge an adapter into its base model and save a standalone Hugging Face model directory."""
    from tinystudio.training.train import export_merged, write_ollama_modelfile

    p = open_project(project)
    job, _, number = model.partition("/")
    out = run(lambda: export_merged(p, job, int(number or 1), destination))
    console.print(f"saved merged model to {out}")
    if ollama:
        modelfile = write_ollama_modelfile(out, out / "Modelfile", system)
        console.print(f"wrote {modelfile}; import with: ollama create {job}-{number or 1} -f {modelfile}")


# ----------------------------------------------------------------- metrics and evaluation


@metric_app.command("create")
def metric_create(
    name: str,
    kind: Annotated[str, typer.Option(help="rubric | code | webhook")] = "rubric",
    should: Annotated[list[str] | None, typer.Option("--should")] = None,
    should_not: Annotated[list[str] | None, typer.Option("--should-not")] = None,
    check: Annotated[str | None, typer.Option(help="For kind=code: exact_match | token_f1 | similarity | json_valid | contains_reference")] = None,
    url: Annotated[str | None, typer.Option(help="For kind=webhook: evaluator URL")] = None,
    description: str = "",
    project: ProjectOpt = None,
) -> None:
    """Define a metric."""
    from tinystudio.evaluation.evaluate import create_metric

    p = open_project(project)
    metric = run(lambda: create_metric(p, name, kind, should, should_not, check, url, description))
    console.print(metric)


@metric_app.command("generate")
def metric_generate(name: str, snapshot: str, goal: str = "", description: Annotated[str, typer.Option(help="What the metric should measure; drives the generated rules.")] = "", llm_url: JudgeUrlOpt = None, llm_model: JudgeModelOpt = None, project: ProjectOpt = None) -> None:
    """Ask the helper model to propose should/should-not rules from a snapshot, then save them as a rubric metric."""
    from tinystudio.evaluation.evaluate import create_metric, generate_rules

    p = open_project(project)
    client = llm_client(llm_url, llm_model)
    rules = run(lambda: generate_rules(p, snapshot, client, goal, name=name, description=description))
    metric = run(lambda: create_metric(p, name, "rubric", rules["should"], rules["should_not"], description=description or f"generated from snapshot {snapshot}"))
    console.print(metric)


@metric_app.command("list")
def metric_list(project: ProjectOpt = None) -> None:
    from tinystudio.evaluation.evaluate import load_metric

    p = open_project(project)
    for name in p.list_names("metrics"):
        metric = load_metric(p, name)
        detail = metric.get("rules") or metric.get("check") or metric.get("webhook")
        console.print(f"{name} ({metric['kind']}): {detail}")


@eval_app.command("run")
def eval_run(
    name: str,
    snapshot: Annotated[str, typer.Option()],
    model: Annotated[list[str], typer.Option("--model", help="Model ids to compare: job/experiment or a base model id. Repeatable.")],
    metric: Annotated[list[str], typer.Option("--metric", help="Metric names. Repeatable.")],
    limit: Annotated[int | None, typer.Option(help="Only the first N validation datapoints.")] = None,
    max_new_tokens: int = 256,
    llm_url: JudgeUrlOpt = None,
    llm_model: JudgeModelOpt = None,
    project: ProjectOpt = None,
) -> None:
    """Generate answers for the validation split with each model and score them with each metric."""
    from tinystudio.evaluation.evaluate import load_metric, run_eval

    p = open_project(project)
    needs_judge = any(run(lambda m=m: load_metric(p, m))["kind"] == "rubric" for m in metric)
    client = llm_client(llm_url, llm_model) if needs_judge else None
    summary = run(lambda: run_eval(p, name, snapshot, model, metric, client, limit, max_new_tokens, progress=err.print))
    _print_leaderboard(summary)


@eval_app.command("show")
def eval_show(name: str, failures: Annotated[bool, typer.Option(help="Print the lowest-scoring rows.")] = False, limit: int = 10, project: ProjectOpt = None, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show an evaluation's leaderboard, optionally with its worst rows."""
    from tinystudio.evaluation.evaluate import load_eval, load_eval_rows

    p = open_project(project)
    summary = run(lambda: load_eval(p, name))
    if as_json:
        emit({**summary, "rows": load_eval_rows(p, name)}, True)
        return
    _print_leaderboard(summary)
    if failures:
        rows = sorted(load_eval_rows(p, name), key=lambda row: row["score"])[:limit]
        for row in rows:
            console.rule(escape(f"{row['model']} · {row['metric']} · score {row['score']}"))
            console.print(f"[bold]user:[/bold] {escape(row['user'])}")
            console.print(f"[bold]reference:[/bold] {escape(row['reference'])}")
            console.print(f"[bold]prediction:[/bold] {escape(row['prediction'])}")
            console.print(f"[dim]{escape(row['reason'])}[/dim]")


def _print_leaderboard(summary: dict[str, Any]) -> None:
    table = Table("model", *summary["metrics"], "overall", title=f"eval {summary['name']} · snapshot {summary['snapshot']} · {summary['datapoints']} datapoints")
    for entry in summary.get("leaderboard", []):
        table.add_row(entry["model"], *(f"{entry['scores'][m]:.3f}" if entry["scores"].get(m) is not None else "-" for m in summary["metrics"]), f"{entry['overall']:.3f}" if entry["overall"] is not None else "-")
    console.print(table)


# ----------------------------------------------------------------- deployment


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    up: Annotated[list[str] | None, typer.Option("--up", help="Model ids to load at start. Repeatable.")] = None,
    base: Annotated[list[str] | None, typer.Option("--base", help="Base model ids to list as deployable baselines. Repeatable.")] = None,
    no_auto_up: Annotated[bool, typer.Option("--no-auto-up", help="Require POST /v1/models/{id}/up before chat (Studio behaviour).")] = False,
    project: ProjectOpt = None,
) -> None:
    """Serve the web interface and an OpenAI-compatible API for experiments and base models on localhost."""
    import uvicorn

    from tinystudio.serving.serve import create_app

    p = None
    try:
        p = Project.open(project or Path.cwd())
    except ProjectError:
        err.print("[yellow]no project here; serving base models only[/yellow]")
    application = create_app(p, base or [], auto_up=not no_auto_up)
    for model_id in up or []:
        err.print(f"loading {model_id}")
        from fastapi.testclient import TestClient

        with TestClient(application) as client:
            response = client.post(f"/v1/models/{model_id.replace('/', '~')}/up")
            if response.status_code != 200:
                fail(response.text)
    if p is not None:
        err.print(f"web interface: http://{host}:{port}/")
    err.print(f"API: http://{host}:{port}/v1  (models: GET /v1/models)")
    uvicorn.run(application, host=host, port=port, log_level="warning")


@app.command()
def chat(
    model: Annotated[str, typer.Argument(help="job/experiment, a base model id, or a model served at --base-url")],
    system: Annotated[str | None, typer.Option(help="System prompt")] = None,
    base_url: Annotated[str | None, typer.Option(help="Talk to a running server instead of loading the model in this process.")] = None,
    temperature: float = 0.0,
    max_tokens: int = 256,
    project: ProjectOpt = None,
) -> None:
    """Interactive chat with a model. Type /reset to clear history, /quit to leave."""
    history: list[dict[str, str]] = [{"role": "system", "content": system}] if system else []
    if base_url:
        client = LLMClient(base_url.rstrip("/"), model)

        def reply(messages: list[dict[str, str]]) -> str:
            return client.chat(messages, temperature=temperature, max_tokens=max_tokens)
    else:
        from tinystudio.serving.runner import ModelRunner, resolve_model

        p = None
        try:
            p = Project.open(project or Path.cwd())
        except ProjectError:
            pass
        err.print(f"loading {model}")
        runner = run(lambda: ModelRunner(resolve_model(p, model)))

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


@trace_app.command("list")
def trace_list(limit: int = 20, project: ProjectOpt = None, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    from tinystudio.core.traces import read_traces

    p = open_project(project)
    rows = read_traces(p)[-limit:]
    if as_json:
        emit(rows, True)
        return
    for row in rows:
        user = next((m["content"] for m in reversed(row["messages"]) if m["role"] == "user"), "")
        console.print(f"[bold]{escape(row['id'])}[/bold] {row['time']} {escape(row['model'])} score={row.get('score')}\n  user: {escape(user[:120])}\n  model: {escape(row['response'][:120])}")


@trace_app.command("score")
def trace_score(trace_id: str, score: Annotated[float, typer.Argument(min=0.0, max=1.0)], note: str = "", corrected: Annotated[str | None, typer.Option(help="A corrected answer to use instead of the model's when promoting.")] = None, project: ProjectOpt = None) -> None:
    from tinystudio.core.traces import score_trace

    p = open_project(project)
    row = run(lambda: score_trace(p, trace_id, score, note, corrected))
    console.print(f"scored {row['id']} = {row['score']}")


@trace_app.command("promote")
def trace_promote(
    dataset: str,
    min_score: float | None = None,
    trace_id: Annotated[list[str] | None, typer.Option("--id")] = None,
    preference: Annotated[bool, typer.Option("--preference", help="Only corrected traces, stored as DPO pairs: correction = chosen, original response = rejected.")] = False,
    project: ProjectOpt = None,
) -> None:
    """Add traces (scored >= --min-score or named by --id) to a dataset as new uncategorized datapoints."""
    from tinystudio.core.traces import promote

    p = open_project(project)
    count = run(lambda: promote(p, dataset, min_score, trace_id, preference=preference))
    console.print(f"promoted {count} traces into dataset {dataset}" + (" as preference pairs" if preference else ""))


@hub_app.command("search")
def hub_search(query: str, limit: int = 15, max_params_b: Annotated[float | None, typer.Option(help="Only models up to this many billion parameters (needs safetensors metadata).")] = 2.0, task: Annotated[str, typer.Option(help="text-generation, embeddings, sentence-similarity, or any")] = "text-generation", as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Search text-generation models on the Hugging Face Hub."""
    from tinystudio.integrations import hub

    rows = run(lambda: hub.search(query, limit, max_params_b, task=task))
    if as_json:
        emit(rows, True)
        return
    table = Table("model", "params", "license", "gated", "downloads", "loadable")
    for r in rows:
        table.add_row(r["id"], f"{r['params_b']}B" if r["params_b"] else "?", str(r["license"] or "?"), "yes" if r["gated"] else "", str(r["downloads"] or ""), "yes" if r["transformers_loadable"] else ", ".join(r["format_tags"]) or "?")
    console.print(table)


@hub_app.command("download")
def hub_download(repo_id: str, revision: str | None = None) -> None:
    """Download a model repository into the local Hub cache (transformers files only)."""
    from tinystudio.integrations import hub

    result = run(lambda: hub.download(repo_id, revision, progress=err.print))
    console.print(f"{result['repo_id']}: {result['path']}" + ("" if result["transformers_loadable"] else "  (no config.json: not loadable by transformers)"))


@hub_app.command("list")
def hub_list() -> None:
    """Models in the local Hub cache."""
    from tinystudio.integrations import hub

    for r in hub.cached_models():
        console.print(f"{r['id']:55} {r['size_gb']:6.2f} GB  {'transformers' if r['transformers_loadable'] else ('mlx' if r['mlx'] else 'other')}")
    console.print(f"free disk: {hub.free_disk_gb()} GB · token: {hub.token_status()['masked'] or 'none'}")


@hub_app.command("login")
def hub_login(token: Annotated[str, typer.Argument(help="hf_... token from https://huggingface.co/settings/tokens")]) -> None:
    """Save a Hugging Face token (needed for gated models and uploads)."""
    from tinystudio.integrations import hub

    who = run(lambda: hub.save_token(token))
    console.print(f"logged in as {who['user']}")


@hub_app.command("upload")
def hub_upload(kind: Annotated[str, typer.Argument(help="experiment | dataset | snapshot")], name: Annotated[str, typer.Argument(help="job/number, dataset name, or snapshot name")], repo_id: str, public: bool = False, merge: Annotated[bool, typer.Option(help="For a LoRA experiment: merge into the base model before uploading.")] = False, project: ProjectOpt = None) -> None:
    """Upload an experiment's weights, a dataset, or a snapshot to the Hugging Face Hub."""
    import tempfile

    from tinystudio.integrations import hub
    from tinystudio.training.train import experiment_dir, export_merged

    p = open_project(project)
    if kind == "experiment":
        job, _, number = name.partition("/")
        exp_dir = experiment_dir(p, job, int(number or 1))
        source = exp_dir / "adapter" if (exp_dir / "adapter").exists() else exp_dir / "model"
        if merge and (exp_dir / "adapter").exists():
            tmp = Path(tempfile.mkdtemp(prefix="tinystudio-merged-"))
            err.print(f"merging adapter into the base model at {tmp}")
            source = run(lambda: export_merged(p, job, int(number or 1), tmp))
        result = run(lambda: hub.upload(source, repo_id, "model", not public, f"tinystudio experiment {name}"))
    elif kind == "dataset":
        run(lambda: data_mod.dataset_meta(p, name))
        result = run(lambda: hub.upload(p.dataset_dir(name), repo_id, "dataset", not public, f"tinystudio dataset {name}"))
    elif kind == "snapshot":
        run(lambda: data_mod.snapshot_meta(p, name))
        result = run(lambda: hub.upload(p.snapshot_dir(name), repo_id, "dataset", not public, f"tinystudio snapshot {name}"))
    else:
        fail("kind must be experiment, dataset, or snapshot")
    console.print(f"uploaded to {result['url']}")


@app.command()
def mcp(
    url: Annotated[str, typer.Option(envvar="TINYSTUDIO_URL", help="Base URL of the running tinystudio server; started automatically for --project when unreachable.")] = "http://127.0.0.1:8000",
    project: ProjectOpt = None,
) -> None:
    """Run the MCP server on stdio so Claude Code, Codex, or Claude Desktop can drive tinystudio."""
    from tinystudio.integrations.mcp_server import main as mcp_main

    mcp_main(url, str(project or Path.cwd()))


@dataset_app.command("quality")
def dataset_quality(name: str, project: ProjectOpt = None, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Profile a dataset: lengths, duplicates, outliers, labels, formats."""
    from tinystudio.core.quality import profile

    p = open_project(project)
    report = profile(run(lambda: data_mod.load_dataset(p, name)))
    if as_json:
        emit(report, True)
        return
    console.print(f"{name}: {report['count']} datapoints · buckets {report['buckets']} · ~{report['approx_tokens']['total']:,} tokens")
    console.print(f"user chars {report['user_chars']['summary']} · answer chars {report['answer_chars']['summary']}")
    d = report["duplicates"]
    console.print(f"duplicates: {d['exact_user_rows']} exact user turns in {d['exact_user_groups']} groups · {d['exact_conversation_rows']} exact conversations · {d['near_pair_count']} near-duplicate pairs")
    o = report["outliers"]
    console.print(f"outliers: {len(o['too_short'])} too short, {len(o['too_long'])} too long, {len(o['empty_like'])} empty-like · JSON answers {report['format']['answers_json_pct']}% · PII-like {report['pii']}")
    if report["labels"]:
        console.print(f"labels: {dict(report['labels'])}")


@dataset_app.command("clean")
def dataset_clean(
    name: str,
    dedupe_user: bool = False,
    dedupe_conversation: bool = False,
    drop_near_duplicates: bool = False,
    drop_empty: bool = False,
    trim_whitespace: bool = False,
    min_total_chars: int | None = None,
    max_total_chars: int | None = None,
    min_rating: float | None = None,
    require_json_answer: bool = False,
    drop_label: Annotated[list[str] | None, typer.Option("--drop-label")] = None,
    keep_label: Annotated[list[str] | None, typer.Option("--keep-label")] = None,
    redact_pii: Annotated[bool, typer.Option("--redact-pii", help="Replace emails, card numbers, national ids, and phone numbers with placeholders (rows are kept).")] = False,
    project: ProjectOpt = None,
) -> None:
    """Clean a dataset in place; every run is logged to cleaning.jsonl."""
    from tinystudio.core.quality import CleanSpec, apply_cleaning

    p = open_project(project)
    spec = CleanSpec(dedupe_user=dedupe_user, dedupe_conversation=dedupe_conversation, drop_near_duplicates=drop_near_duplicates, drop_empty=drop_empty, trim_whitespace=trim_whitespace, min_total_chars=min_total_chars, max_total_chars=max_total_chars, min_rating=min_rating, require_json_answer=require_json_answer, drop_labels=list(drop_label or []), keep_labels=list(keep_label or []), redact_pii=redact_pii)
    result = run(lambda: apply_cleaning(p, name, spec))
    console.print(f"dataset {name}: {result['before']} -> {result['after']} datapoints; removed {result['removed'] or 'nothing'}" + (f"; redacted PII in {result['redacted_rows']} rows" if result.get("redacted_rows") else ""))


def _layers_option(value: str | None) -> list[int]:
    if not value or value.strip().lower() in ("all", "*"):
        return []
    try:
        return sorted({int(part) for part in value.replace(" ", "").split(",") if part})
    except ValueError:
        fail(f"--layers must be a comma-separated list of layer indices or 'all', not {value!r}")
        return []


def _parse_experiment(ref: str) -> tuple[str, int]:
    from tinystudio.training.train import parse_alias

    alias = parse_alias(ref)
    if alias:
        return alias
    job, _, number = ref.partition("/")
    if not number.isdigit():
        fail(f"expected job/number or ft-job-number, got {ref!r}")
    return job, int(number)


@interp_app.command("capture")
def interp_capture(
    model: Annotated[str, typer.Argument(help="Model id: job/number, ft-job-number, job/number@step (checkpoint), a model directory, or a Hub id.")],
    dataset: Annotated[str | None, typer.Option("--dataset")] = None,
    snapshot: Annotated[str | None, typer.Option("--snapshot")] = None,
    layers: Annotated[str | None, typer.Option("--layers", help="Comma-separated MLP layer indices, or 'all'.")] = None,
    topk: int = 40,
    batch_size: Annotated[int, typer.Option(help="Rows per forward pass; 0 fits about 8k tokens per pass.")] = 0,
    name: Annotated[str | None, typer.Option("--name")] = None,
    project: ProjectOpt = None,
) -> None:
    """Record per-neuron MLP activations (poles, sign regimes, per-datapoint maxima) of a model over a dataset or snapshot."""
    from tinystudio.interp.neurons import CaptureSpec, capture

    p = open_project(project)
    spec = CaptureSpec(model_id=model, dataset=dataset, snapshot=snapshot, layers=_layers_option(layers), topk=topk, batch_size=batch_size, name=name or "")
    meta = run(lambda: capture(p, spec, progress=err.print))
    console.print(f"capture {meta['name']}: {meta['rows']} datapoints, {meta['tokens']} tokens, {len(meta['layers'])} layers x {meta['neurons_per_layer']} neurons in {meta['seconds']}s")


@interp_app.command("capture-checkpoints")
def interp_capture_checkpoints(
    experiment: Annotated[str, typer.Argument(help="job/number or ft-job-number")],
    dataset: Annotated[str | None, typer.Option("--dataset")] = None,
    snapshot: Annotated[str | None, typer.Option("--snapshot")] = None,
    layers: Annotated[str | None, typer.Option("--layers")] = None,
    topk: int = 40,
    project: ProjectOpt = None,
) -> None:
    """Capture every saved checkpoint of an experiment plus its final weights, for `interp drift`."""
    from tinystudio.interp.neurons import capture_checkpoints

    p = open_project(project)
    job, number = _parse_experiment(experiment)
    metas = run(lambda: capture_checkpoints(p, job, number, dataset, snapshot, _layers_option(layers), topk, progress=err.print))
    for meta in metas:
        console.print(f"{meta['model_id']}: capture {meta['name']}")


@interp_app.command("list")
def interp_list(as_json: Annotated[bool, typer.Option("--json")] = False, project: ProjectOpt = None) -> None:
    """List captures and comparisons."""
    from tinystudio.interp.neurons import list_captures, list_diffs

    p = open_project(project)
    captures, diffs = list_captures(p), list_diffs(p)
    if as_json:
        emit({"captures": captures, "comparisons": diffs}, True)
        return
    for m in captures:
        console.print(f"capture {m['name']}: {m['model_id']} on {m['source']} ({m['rows']} rows, layers {m['layers'] if len(m['layers']) < 8 else str(len(m['layers'])) + ' layers'}, explained {m['explained']}, tested {m['tested']})")
    for m in diffs:
        console.print(f"comparison {m['name']}: {m['experiment']} vs {m['base']} on {m['source']} ({m['mode']})")
    if not captures and not diffs:
        console.print("no captures yet: tinystudio interp capture MODEL --dataset DATASET --layers 8,12")


@interp_app.command("neurons")
def interp_neurons(capture: str, layer: Annotated[int, typer.Option("--layer")], sort: str = "max", limit: int = 30, query: str = "", as_json: Annotated[bool, typer.Option("--json")] = False, project: ProjectOpt = None) -> None:
    """Neuron table of one layer, sorted by max, min, open_max, echo_share, or density."""
    from tinystudio.interp.neurons import neuron_table

    p = open_project(project)
    table = run(lambda: neuron_table(p, capture, layer, sort=sort, limit=limit, query=query))
    if as_json:
        emit(table, True)
        return
    for r in table["rows"]:
        console.print(f"N{r['neuron']:>5}  max {r['max']:7.3f}  min {r['min']:8.3f}  open {r['open_max']:7.3f}  echo {r['echo_share']:.2f}{'*' if r['echo_top'] else ' '}  density {r['density']:.2f}  {escape(r['explanation'] or '')}")


@interp_app.command("show")
def interp_show(capture: str, layer: int, neuron: int, limit: int = 8, as_json: Annotated[bool, typer.Option("--json")] = False, project: ProjectOpt = None) -> None:
    """Poles, sign regimes, label shares, explanation, and tests of one neuron."""
    from tinystudio.interp.neurons import neuron_view

    p = open_project(project)
    view = run(lambda: neuron_view(p, capture, layer, neuron, limit=limit))
    if as_json:
        emit(view, True)
        return
    s = view["stats"]
    console.print(f"L{layer} N{neuron}: max {s['max']:.3f} (gate-open {s['open_max']:.3f}), min {s['min']:.3f}, echo share {s['echo_share']:.2f}, density {s['density']:.2f}; regimes {view['regimes']}")
    for pole in ("gate_open", "positives", "negatives"):
        console.print(f"[bold]{pole}[/bold]")
        for e in view[pole][:limit]:
            flat = lambda text: text.replace("\n", "⏎")  # noqa: E731
            console.print(f"  {e['value']:7.3f} {e['regime']} dp{e['datapoint']}:{e['position']}  {escape(flat(e['before']))}[bold]{escape('[' + flat(e['token']) + ']')}[/bold]{escape(flat(e['after']))}")
    console.print(f"labels: {view['label_share']}")
    if view["explanation"]:
        console.print(f"explanation: {escape(view['explanation']['explanation'])}  (negative pole: {escape(view['explanation'].get('negative_pole') or '—')})")
    if view["test"]:
        t = view["test"]
        console.print(f"test: blind best {t['blind_best']}x corpus max, {t['counts']}, verdict {t['verdict']}")


@interp_app.command("explain")
def interp_explain(capture: str, layer: int, neuron: Annotated[list[int] | None, typer.Option("--neuron")] = None, top: Annotated[int, typer.Option(help="Explain the top-N neurons of the layer by --sort that have no explanation yet.")] = 0, sort: str = "max", llm_url: JudgeUrlOpt = None, llm_model: JudgeModelOpt = None, project: ProjectOpt = None) -> None:
    """Ask the helper LLM for a one-line explanation of neurons from their strongest examples."""
    from tinystudio.interp.neurons import explain, explain_top

    p = open_project(project)
    client = llm_client(llm_url, llm_model)
    if not neuron and not top:
        fail("pass --neuron N (repeatable) or --top K")
    plain = lambda message: console.print(escape(str(message)))  # noqa: E731 - helper output may contain [brackets]
    for n in neuron or []:
        run(lambda: explain(p, capture, layer, n, client, progress=plain))
    if top:
        run(lambda: explain_top(p, capture, layer, client, top=top, sort=sort, progress=plain))


@interp_app.command("test")
def interp_test(capture: str, layer: int, neuron: Annotated[list[int], typer.Option("--neuron")], llm_url: JudgeUrlOpt = None, llm_model: JudgeModelOpt = None, project: ProjectOpt = None) -> None:
    """Blind-maximum and near-miss tests of the explanations (explains first when missing)."""
    from tinystudio.interp.neurons import test_many

    p = open_project(project)
    client = llm_client(llm_url, llm_model)
    run(lambda: test_many(p, capture, layer, list(neuron), client, progress=lambda message: console.print(escape(str(message)))))


@interp_app.command("diff")
def interp_diff(
    experiment: Annotated[str, typer.Argument(help="job/number, ft-job-number, or job/number@step")],
    base: Annotated[str | None, typer.Option("--base", help="Model to compare against (default: the experiment's own base weights).")] = None,
    dataset: Annotated[str | None, typer.Option("--dataset")] = None,
    snapshot: Annotated[str | None, typer.Option("--snapshot")] = None,
    layers: Annotated[str | None, typer.Option("--layers")] = None,
    name: Annotated[str | None, typer.Option("--name")] = None,
    project: ProjectOpt = None,
) -> None:
    """Compare per-neuron activations before and after fine-tuning on the same tokens."""
    from tinystudio.interp.neurons import DiffSpec, diff

    p = open_project(project)
    meta = run(lambda: diff(p, DiffSpec(experiment=experiment, base=base, dataset=dataset, snapshot=snapshot, layers=_layers_option(layers), name=name or ""), progress=err.print))
    for layer, s in meta["layer_summary"].items():
        console.print(f"layer {layer}: median r {s['median_r']:.3f}, {s['below_0_9']} neurons with r < 0.9, {s['below_0_5']} with r < 0.5")
    console.print(f"comparison {meta['name']} saved ({meta['seconds']}s)")


@interp_app.command("diff-show")
def interp_diff_show(comparison: str, layer: int, neuron: Annotated[int | None, typer.Option("--neuron")] = None, limit: int = 20, sort: Annotated[str, typer.Option(help="change (1 - r plus relative change), r, or rel_change")] = "change", as_json: Annotated[bool, typer.Option("--json")] = False, project: ProjectOpt = None) -> None:
    """Most-changed neurons of a layer, or the datapoints that drove one neuron's change."""
    from tinystudio.interp.neurons import diff_layer, diff_neuron

    p = open_project(project)
    if neuron is None:
        result = run(lambda: diff_layer(p, comparison, layer, limit=limit, sort=sort))
        if as_json:
            emit(result, True)
            return
        for r in result["rows"]:
            rs = "  n/a " if r["r"] is None else f"{r['r']:6.3f}"
            console.print(f"N{r['neuron']:>5}  change {r['change']:5.2f}  r {rs}  rel {r['rel_change']:5.2f}  max delta {r['max_delta']:6.3f} ({r['before']:.2f} -> {r['after']:.2f})  {escape(r['context'].replace(chr(10), '⏎'))}")
        return
    result = run(lambda: diff_neuron(p, comparison, layer, neuron, limit=limit))
    if as_json:
        emit(result, True)
        return
    console.print(f"N{neuron}: change {result['change']:.2f}, r {'n/a (constant)' if result['r'] is None else f'{result['r']:.3f}'}, rel change {result['rel_change']:.2f}")
    for d in result["datapoints"]:
        console.print(f"  dp{d['datapoint']} delta {d['delta_sum']:.2f} {d['labels']}: {escape(d['user'][:100])}")
    for t in result["tokens"]:
        console.print(f"  token delta {t['delta']:.3f} ({t['before']:.2f} -> {t['after']:.2f}): {escape(t['context'])}")


@interp_app.command("drift")
def interp_drift(experiment: str, dataset: Annotated[str, typer.Option("--dataset", help="Dataset or snapshot name the captures used.")], layer: Annotated[int, typer.Option("--layer")], neuron: Annotated[int, typer.Option("--neuron")], as_json: Annotated[bool, typer.Option("--json")] = False, project: ProjectOpt = None) -> None:
    """How one neuron's strongest examples move across an experiment's checkpoints."""
    from tinystudio.interp.neurons import drift

    p = open_project(project)
    job, number = _parse_experiment(experiment)
    result = run(lambda: drift(p, job, number, dataset, layer, neuron))
    if as_json:
        emit(result, True)
        return
    console.print(f"list: {result['list']} top-{result['topk']}; labels present at the first checkpoint: {result['core_labels']}")
    for s in result["steps"]:
        console.print(f"{'final' if s['final'] else 'step ' + str(s['step']):>10}: max {s['max']:.3f}  {s['datapoints']} datapoints  jaccard prev {s['jaccard_prev']}  vs final {s['jaccard_final']}  labels {s['label_share'][:4]}  new labels {s['new_labels']}  offshoots {len(s['offshoot_candidates'])}")


@interp_app.command("delete")
def interp_delete(kind: Annotated[str, typer.Argument(help="capture or comparison")], name: str, project: ProjectOpt = None) -> None:
    """Delete a capture or a comparison."""
    from tinystudio.interp.neurons import delete_capture, delete_diff

    p = open_project(project)
    if kind == "capture":
        run(lambda: delete_capture(p, name))
    elif kind == "comparison":
        run(lambda: delete_diff(p, name))
    else:
        fail("kind must be capture or comparison")
    console.print(f"deleted {kind} {name}")


@app.command("config")
def config_show(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Show the effective configuration (environment plus .env); secrets are shown as set/unset. See .env.example."""
    from tinystudio.settings import load_dotenv, settings

    files = load_dotenv()
    values = settings().as_dict()
    if as_json:
        emit({"env_files": [str(f) for f in files], **values}, True)
        return
    console.print(f".env files read: {', '.join(str(f) for f in files) or 'none'}")
    for key, value in values.items():
        console.print(f"{key:18} {escape(str(value))}")


@repo_app.command("create")
def repo_create(name: str, description: str = "", embedding_model: Annotated[str | None, typer.Option(help="Any Hugging Face feature-extraction model (default sentence-transformers/all-MiniLM-L6-v2).")] = None, project: ProjectOpt = None) -> None:
    """Create an empty repository."""
    from tinystudio.rag.repositories import create_repository

    p = open_project(project)
    meta = run(lambda: create_repository(p, name, description, embedding_model))
    console.print(f"repository {meta['name']} created (embeddings: {meta['embedding_model']})")


@repo_app.command("add")
def repo_add(name: str, source: Annotated[list[Path] | None, typer.Option("--from", help="Files: txt, md, html, pdf, docx, pptx. Repeatable.")] = None, url: Annotated[list[str] | None, typer.Option("--url", help="Web pages or YouTube videos. Repeatable.")] = None, max_chars: int = 1200, overlap: int = 150, project: ProjectOpt = None) -> None:
    """Extract, chunk, embed, and index documents into a repository."""
    from tinystudio.rag.repositories import add_documents

    p = open_project(project)
    meta = run(lambda: add_documents(p, name, list(source or []), list(url or []), max_chars, overlap, progress=err.print))
    console.print(f"repository {name}: {len(meta['documents'])} documents, {meta['chunk_count']} chunks")


@repo_app.command("list")
def repo_list(as_json: Annotated[bool, typer.Option("--json")] = False, project: ProjectOpt = None) -> None:
    """List repositories."""
    from tinystudio.rag.repositories import list_repositories

    p = open_project(project)
    rows = list_repositories(p)
    if as_json:
        emit(rows, True)
        return
    for m in rows:
        console.print(f"{m['name']}: {len(m['documents'])} documents, {m['chunk_count']} chunks, {m['embedding_model']}")
    if not rows:
        console.print("no repositories yet: tinystudio repo create NAME; tinystudio repo add NAME --from doc.pdf")


@repo_app.command("query")
def repo_query(name: str, text: str, limit: int = 5, threshold: float = 0.0, as_json: Annotated[bool, typer.Option("--json")] = False, project: ProjectOpt = None) -> None:
    """Retrieve the most similar chunks."""
    from tinystudio.rag.repositories import query

    p = open_project(project)
    rows = run(lambda: query(p, name, text, limit, threshold))
    if as_json:
        emit(rows, True)
        return
    for r in rows:
        console.print(f"{r['similarity_score']:.3f}  [{escape(r['document'])}]  {escape(r['text'][:200].replace(chr(10), ' '))}")
    if not rows:
        console.print("no chunks above the threshold")


@repo_app.command("delete")
def repo_delete(name: str, document: Annotated[str | None, typer.Option(help="Remove one document instead of the whole repository.")] = None, project: ProjectOpt = None) -> None:
    """Delete a repository, or one document from it."""
    from tinystudio.rag.repositories import delete_repository, remove_document

    p = open_project(project)
    if document:
        meta = run(lambda: remove_document(p, name, document))
        console.print(f"removed {document}; {meta['chunk_count']} chunks remain")
    else:
        run(lambda: delete_repository(p, name))
        console.print(f"deleted repository {name}")


@autopilot_app.command("run")
def autopilot_run(
    name: str,
    dataset: Annotated[str, typer.Option("--dataset", help="Dataset that collects traces and feeds every cycle.")],
    model: Annotated[str | None, typer.Option("--model", help="Base model for the first cycle; later cycles refine the previous winner.")] = None,
    training_type: Annotated[str, typer.Option("--type", help="lora, full, or dpo")] = "lora",
    epochs: float = 2.0,
    lr: float | None = None,
    min_trace_score: Annotated[float | None, typer.Option(help="Promote traces scored at or above this before training.")] = None,
    preference: Annotated[bool, typer.Option("--preference", help="Promote corrected traces as DPO pairs.")] = False,
    clean: Annotated[bool, typer.Option(help="Dedupe, drop empty rows, trim whitespace before splitting.")] = True,
    train_pct: int = 85,
    metric: Annotated[list[str] | None, typer.Option("--metric", help="Metrics for the candidate-vs-previous evaluation; the first decides the winner.")] = None,
    eval_limit: int | None = None,
    max_steps: int = -1,
    llm_url: JudgeUrlOpt = None,
    llm_model: JudgeModelOpt = None,
    project: ProjectOpt = None,
) -> None:
    """Run one continuous fine-tuning cycle: promote traces, clean, split, snapshot, refinetune, evaluate, pick a winner."""
    from tinystudio.training.autopilot import CycleSpec, run_cycle

    p = open_project(project)
    spec = CycleSpec(name=name, dataset=dataset, base_model=model, training_type=training_type, epochs=epochs, learning_rate=lr, min_trace_score=min_trace_score, preference=preference, clean=clean, train_pct=train_pct, metrics=list(metric or []), eval_limit=eval_limit, max_steps=max_steps)
    client = llm_client(llm_url, llm_model) if metric else None
    record = run(lambda: run_cycle(p, spec, client, progress=err.print))
    console.print(f"cycle {record['cycle']}: experiment {record['experiment']['model']} {record['experiment']['state']}; winner {record.get('winner')} ({escape(str(record.get('decision')))})")
    if record["experiment"]["state"] != "succeeded":
        raise typer.Exit(1)


@autopilot_app.command("history")
def autopilot_history(name: Annotated[str | None, typer.Argument()] = None, as_json: Annotated[bool, typer.Option("--json")] = False, project: ProjectOpt = None) -> None:
    """Show the cycles of one autopilot (or every autopilot's summary)."""
    from tinystudio.training.autopilot import list_histories, read_history

    p = open_project(project)
    if name is None:
        emit(list_histories(p), as_json)
        return
    rows = read_history(p, name)
    if as_json:
        emit(rows, True)
        return
    for r in rows:
        exp = r.get("experiment", {})
        console.print(f"cycle {r['cycle']}  snapshot {r.get('snapshot', {}).get('name')}  {exp.get('model')} {exp.get('state')}  eval_loss={exp.get('eval_loss')}  winner={r.get('winner')}  {escape(str(r.get('decision', '')))}")


def _terminate(signum: int, frame: Any) -> None:
    """Let `tinystudio serve`'s task stop button (SIGTERM) unwind like Ctrl-C so status files record the failure."""
    raise KeyboardInterrupt


def main() -> None:
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    signal.signal(signal.SIGTERM, _terminate)
    app()


if __name__ == "__main__":
    main()
