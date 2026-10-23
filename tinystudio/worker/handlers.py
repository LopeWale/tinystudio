"""What each task kind does. Handlers run inside the worker (or inline from the CLI) and talk to the database
through short sessions; long model work happens between them."""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tinystudio.core.llm import LLMClient
from tinystudio.core.project import ProjectError
from tinystudio.db import session
from tinystudio.db.models import Project


@dataclass
class Context:
    task_id: str
    project_id: str | None
    user_id: str | None
    args: dict[str, Any]
    progress: Any

    def project(self, db: Any) -> Project:
        project = db.get(Project, self.project_id) if self.project_id else None
        if project is None:
            raise ProjectError("this task needs a project")
        return project

    def client(self) -> LLMClient:
        return LLMClient.from_env(self.args.get("llm_url"), self.args.get("llm_model"))


def _paths(values: list[str] | None) -> list[Path]:
    return [Path(v) for v in values or []]


# ---------------------------------------------------------------- datasets
def dataset_synthetic(ctx: Context) -> dict[str, Any]:
    from tinystudio.core.synth import SynthSpec, load_sources, synthesize
    from tinystudio.services import datasets as datasets_svc

    a = ctx.args
    paths, urls = _paths(a.get("files")), list(a.get("urls") or [])
    if not paths and not urls:
        raise ProjectError("pass at least one file or URL")
    count = a.get("count")
    per_source = max(1, math.ceil(int(count) / (len(paths) + len(urls)))) if count else int(a.get("pairs_per_source", 25))
    spec = SynthSpec(**{k: v for k, v in a.items() if k in SynthSpec.__dataclass_fields__ and k != "pairs_per_source"}, pairs_per_source=per_source)
    sources = load_sources(paths, urls)
    rows = synthesize(ctx.client(), sources, spec, progress=ctx.progress)
    origin = {"kind": "synthetic", "documents": [s.name for s in sources], "pairs_per_source": spec.pairs_per_source, "temperature": spec.temperature, "curate_threshold": spec.curate_threshold}
    with session() as db:
        project = ctx.project(db)
        try:
            datasets_svc.get_dataset(db, project, a["name"])
            meta = datasets_svc.add_rows(db, project, a["name"], rows)
        except ProjectError:
            meta = datasets_svc.create_dataset(db, project, a["name"], rows, origin)
    ctx.progress(f"dataset {a['name']}: {meta['datapoints']} datapoints")
    return meta


def dataset_augment(ctx: Context) -> dict[str, Any]:
    from tinystudio.core.synth import augment
    from tinystudio.services import datasets as datasets_svc

    a = ctx.args
    with session() as db:
        project = ctx.project(db)
        rows = [{k: v for k, v in r.items() if k != "id"} for r in datasets_svc.rows(db, datasets_svc.get_dataset(db, project, a["dataset"]))]
    produced = augment(ctx.client(), rows, int(a.get("count", 50)), int(a.get("variants", 3)), progress=ctx.progress, instructions=a.get("instructions", ""), avoid=a.get("avoid", ""), temperature=float(a.get("temperature", 0.8)))
    with session() as db:
        project = ctx.project(db)
        return datasets_svc.add_rows(db, project, a["dataset"], produced)


def dataset_auto_label(ctx: Context) -> dict[str, Any]:
    from tinystudio.core.synth import auto_label
    from tinystudio.services import datasets as datasets_svc

    a = ctx.args
    with session() as db:
        project = ctx.project(db)
        dataset = datasets_svc.get_dataset(db, project, a["dataset"])
        labels = list(dataset.labels or [])
        rows = [{k: v for k, v in r.items() if k != "id"} for r in datasets_svc.rows(db, dataset)]
    labeled = auto_label(ctx.client(), rows, labels, progress=ctx.progress)
    with session() as db:
        project = ctx.project(db)
        return datasets_svc.replace_rows(db, datasets_svc.get_dataset(db, project, a["dataset"]), labeled)


def dataset_clean(ctx: Context) -> dict[str, Any]:
    from tinystudio.core.quality import CleanSpec
    from tinystudio.services import datasets as datasets_svc

    a = ctx.args
    with session() as db:
        result = datasets_svc.apply_cleaning(db, ctx.project(db), a["dataset"], CleanSpec(**a.get("spec", {})))
    ctx.progress(f"cleaned {a['dataset']}: {result['before']} -> {result['after']} datapoints")
    return result


def dataset_split(ctx: Context) -> dict[str, Any]:
    from tinystudio.services import datasets as datasets_svc

    a = ctx.args
    with session() as db:
        meta = datasets_svc.autosplit(db, ctx.project(db), a["dataset"], int(a.get("train_pct", 80)), int(a.get("seed", 0)), bool(a.get("synthetic_in_validation", False)))
    ctx.progress(f"split {a['dataset']}: {meta['buckets']}")
    return meta


def snapshot_create(ctx: Context) -> dict[str, Any]:
    from tinystudio.services import snapshots as snapshots_svc

    a = ctx.args
    with session() as db:
        snap = snapshots_svc.create_snapshot(db, ctx.project(db), a["dataset"], a["name"])
    ctx.progress(f"snapshot {snap['name']}: {snap['training_count']} training, {snap['validation_count']} validation")
    return snap


# ---------------------------------------------------------------- metrics, jobs, evaluations
def metric_generate(ctx: Context) -> dict[str, Any]:
    from tinystudio.evaluation.evaluate import generate_rules
    from tinystudio.services import metrics as metrics_svc
    from tinystudio.services import snapshots as snapshots_svc

    a = ctx.args
    with session() as db:
        project = ctx.project(db)
        train_rows, _ = snapshots_svc.load_snapshot(db, project, a["snapshot"])
        goal = a.get("goal") or project.goal
    rules = generate_rules(train_rows, ctx.client(), goal, name=a["name"], description=a.get("description", ""))
    with session() as db:
        project = ctx.project(db)
        return metrics_svc.create_metric(db, project, a["name"], "rubric", rules["should"], rules["should_not"], description=a.get("description") or f"generated from snapshot {a['snapshot']}")


def job_run(ctx: Context) -> dict[str, Any]:
    from tinystudio.services import jobs as jobs_svc

    results = jobs_svc.run_pending(ctx.args["job_id"], ctx.progress, ctx.task_id)
    return {"experiments": results}


def job_analyze(ctx: Context) -> dict[str, Any]:
    from tinystudio.services import jobs as jobs_svc

    with session() as db:
        project = ctx.project(db)
        return jobs_svc.analyze_job(db, project, ctx.args["job"], ctx.client())


def eval_run(ctx: Context) -> dict[str, Any]:
    from tinystudio.db.models import Evaluation
    from tinystudio.services import evals as evals_svc
    from tinystudio.services import metrics as metrics_svc

    a = ctx.args
    with session() as db:
        evaluation = db.get(Evaluation, a["evaluation_id"])
        if evaluation is None:
            raise ProjectError("evaluation does not exist")
        project = ctx.project(db)
        needs_judge = any(metrics_svc.get_metric(db, project, m).kind == "rubric" for m in evaluation.metrics)
    client = ctx.client() if needs_judge else None
    return evals_svc.run_eval(a["evaluation_id"], client, a.get("limit"), int(a.get("max_new_tokens", 256)), ctx.progress)


# ---------------------------------------------------------------- repositories
def repo_add(ctx: Context) -> dict[str, Any]:
    from tinystudio.services import repositories as repositories_svc

    a = ctx.args
    return repositories_svc.add_documents(a["repository_id"], _paths(a.get("paths")), list(a.get("urls") or []), int(a.get("max_chars", 1200)), int(a.get("overlap", 150)), ctx.progress)


# ---------------------------------------------------------------- interpretability
def _store(ctx: Context) -> Any:
    from tinystudio.services.interp import ProjectStore

    if not ctx.project_id:
        raise ProjectError("this task needs a project")
    return ProjectStore(ctx.project_id)


def interp_capture(ctx: Context) -> dict[str, Any]:
    from tinystudio.interp.neurons import CaptureSpec, capture

    a = ctx.args
    return capture(_store(ctx), CaptureSpec(model_id=a["model"], dataset=a.get("dataset"), snapshot=a.get("snapshot"), layers=list(a.get("layers") or []), topk=int(a.get("topk", 40)), name=a.get("name") or ""), ctx.progress)


def interp_capture_checkpoints(ctx: Context) -> dict[str, Any]:
    from tinystudio.interp.neurons import capture_checkpoints

    a = ctx.args
    metas = capture_checkpoints(_store(ctx), a["job"], int(a["number"]), a.get("dataset"), a.get("snapshot"), list(a.get("layers") or []), int(a.get("topk", 40)), ctx.progress)
    return {"captures": [m["name"] for m in metas]}


def interp_explain(ctx: Context) -> dict[str, Any]:
    from tinystudio.interp.neurons import explain, explain_top

    a = ctx.args
    store, client = _store(ctx), ctx.client()
    records = [explain(store, a["capture"], int(a["layer"]), int(n), client, ctx.progress) for n in a.get("neurons") or []]
    if a.get("top"):
        records += explain_top(store, a["capture"], int(a["layer"]), client, int(a["top"]), a.get("sort", "max"), ctx.progress)
    return {"explained": len(records)}


def interp_test(ctx: Context) -> dict[str, Any]:
    from tinystudio.interp.neurons import test_many

    a = ctx.args
    records = test_many(_store(ctx), a["capture"], int(a["layer"]), [int(n) for n in a["neurons"]], ctx.client(), ctx.progress)
    return {"tested": len(records), "verdicts": [r.get("verdict") for r in records]}


def interp_diff(ctx: Context) -> dict[str, Any]:
    from tinystudio.interp.neurons import DiffSpec, diff

    a = ctx.args
    return diff(_store(ctx), DiffSpec(experiment=a["experiment"], base=a.get("base"), dataset=a.get("dataset"), snapshot=a.get("snapshot"), layers=list(a.get("layers") or []), name=a.get("name") or ""), ctx.progress)


# ---------------------------------------------------------------- autopilot, hub
def autopilot_run(ctx: Context) -> dict[str, Any]:
    from tinystudio.services.autopilot import CycleSpec, run_cycle

    a = ctx.args
    spec = CycleSpec(**{k: v for k, v in a.items() if k in CycleSpec.__dataclass_fields__})
    client = ctx.client() if spec.metrics else None
    if not ctx.project_id:
        raise ProjectError("this task needs a project")
    return run_cycle(ctx.project_id, spec, client, ctx.progress, ctx.task_id)


def hub_download(ctx: Context) -> dict[str, Any]:
    from tinystudio.integrations import hub

    return hub.download(ctx.args["repo_id"], ctx.args.get("revision"), ctx.progress)


def hub_upload(ctx: Context) -> dict[str, Any]:
    from tinystudio.core.data import write_jsonl
    from tinystudio.integrations import hub
    from tinystudio.services import datasets as datasets_svc
    from tinystudio.services import jobs as jobs_svc
    from tinystudio.services import snapshots as snapshots_svc
    from tinystudio.training.train import export_merged

    a = ctx.args
    kind, name, repo_id, private = a["kind"], a["name"], a["repo_id"], bool(a.get("private", True))
    with session() as db:
        project = ctx.project(db)
        if kind == "experiment":
            exp = jobs_svc.find_experiment(db, project, name)
            exp_dir = jobs_svc.exp_path(exp)
            source = exp_dir / "adapter" if (exp_dir / "adapter").exists() else exp_dir / "model"
            if a.get("merge") and (exp_dir / "adapter").exists():
                tmp = Path(tempfile.mkdtemp(prefix="tinystudio-merged-"))
                ctx.progress(f"merging adapter into the base model at {tmp}")
                source = export_merged(exp_dir, tmp, exp.config["base_model"], (exp.status or {}).get("weights_source"))
            repo_type, message = "model", f"tinystudio experiment {name}"
        elif kind in ("dataset", "snapshot"):
            tmp = Path(tempfile.mkdtemp(prefix=f"tinystudio-{kind}-"))
            if kind == "dataset":
                rows = [{k: v for k, v in r.items() if k != "id"} for r in datasets_svc.rows(db, datasets_svc.get_dataset(db, project, name))]
                write_jsonl(tmp / "datapoints.jsonl", rows)
            else:
                train, val = snapshots_svc.load_snapshot(db, project, name)
                write_jsonl(tmp / "train.jsonl", train)
                write_jsonl(tmp / "val.jsonl", val)
            source, repo_type, message = tmp, "dataset", f"tinystudio {kind} {name}"
        else:
            raise ProjectError("kind must be experiment, dataset, or snapshot")
    result = hub.upload(source, repo_id, repo_type, private, message)
    ctx.progress(f"uploaded to {result['url']}")
    return result


HANDLERS: dict[str, Any] = {
    "dataset.synthetic": dataset_synthetic,
    "dataset.augment": dataset_augment,
    "dataset.auto_label": dataset_auto_label,
    "dataset.clean": dataset_clean,
    "dataset.split": dataset_split,
    "snapshot.create": snapshot_create,
    "metric.generate": metric_generate,
    "job.run": job_run,
    "job.analyze": job_analyze,
    "eval.run": eval_run,
    "repo.add": repo_add,
    "interp.capture": interp_capture,
    "interp.capture_checkpoints": interp_capture_checkpoints,
    "interp.explain": interp_explain,
    "interp.test": interp_test,
    "interp.diff": interp_diff,
    "autopilot.run": autopilot_run,
    "hub.download": hub_download,
    "hub.upload": hub_upload,
}
