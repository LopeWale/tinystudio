"""Continuous fine-tuning cycles: traces -> dataset -> clean -> split -> snapshot -> refinetune -> evaluate -> winner.

One call runs one complete cycle; records live in ``autopilot_runs``. Nothing here is automatic: a cycle runs
when a person (or an assistant through the API/MCP) asks for it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tinystudio.core.llm import LLMClient
from tinystudio.core.project import ProjectError, iso, now
from tinystudio.core.quality import CleanSpec
from tinystudio.db import session
from tinystudio.db.models import AutopilotRun, Job, Project, Snapshot
from tinystudio.services import datasets as datasets_svc
from tinystudio.services import evals as evals_svc
from tinystudio.services import jobs as jobs_svc
from tinystudio.services import snapshots as snapshots_svc
from tinystudio.services import traces as traces_svc
from tinystudio.training.train import TrainConfig


@dataclass
class CycleSpec:
    name: str
    dataset: str
    base_model: str | None = None  # required for the first cycle; later cycles refine the previous winner
    training_type: str = "lora"  # lora | full | dpo
    epochs: float = 2.0
    learning_rate: float | None = None
    min_trace_score: float | None = None
    preference: bool = False
    clean: bool = True
    train_pct: int = 85
    metrics: list[str] = field(default_factory=list)
    eval_limit: int | None = None
    max_steps: int = -1


def history(db: Session, project: Project, name: str) -> list[dict[str, Any]]:
    return [{**r.payload, "recorded": iso(r.created)} for r in db.scalars(select(AutopilotRun).where(AutopilotRun.project_id == project.id, AutopilotRun.name == name).order_by(AutopilotRun.cycle))]


def list_histories(db: Session, project: Project) -> list[dict[str, Any]]:
    out = []
    for (name,) in db.execute(select(AutopilotRun.name).where(AutopilotRun.project_id == project.id).group_by(AutopilotRun.name).order_by(AutopilotRun.name)).all():
        cycles = history(db, project, name)
        last = cycles[-1] if cycles else {}
        out.append({"name": name, "cycles": len(cycles), "dataset": last.get("dataset"), "winner": last.get("winner"), "last": last.get("finished")})
    return out


def next_cycle(db: Session, project: Project, name: str) -> int:
    n = int(db.scalar(select(func.coalesce(func.max(AutopilotRun.cycle), 0)).where(AutopilotRun.project_id == project.id, AutopilotRun.name == name))) + 1
    while db.scalar(select(Job.id).where(Job.project_id == project.id, Job.name == f"{name}-c{n}")) is not None or db.scalar(select(Snapshot.id).where(Snapshot.project_id == project.id, Snapshot.name == f"{name}-c{n}")) is not None:
        n += 1
    return n


def last_winner(cycles: list[dict[str, Any]]) -> str | None:
    for record in reversed(cycles):
        if record.get("winner"):
            return str(record["winner"])
    return None


def _mean_score(evaluation: dict[str, Any], model_id: str, metric: str | None) -> float | None:
    for entry in evaluation.get("leaderboard") or []:
        if entry["model"] == model_id:
            return entry["scores"].get(metric) if metric else entry.get("overall")
    return None


def _record(project_id: str, name: str, cycle: int, payload: dict[str, Any]) -> None:
    with session() as db:
        db.add(AutopilotRun(project_id=project_id, name=name, cycle=cycle, payload=payload))


def run_cycle(project_id: str, spec: CycleSpec, client: LLMClient | None = None, progress: Any = print, task_id: str | None = None) -> dict[str, Any]:
    """Run one cycle (worker job; opens its own sessions around each step). Returns the record."""
    with session() as db:
        project = db.get(Project, project_id)
        if project is None:
            raise ProjectError("project does not exist")
        cycles = history(db, project, spec.name)
        n = next_cycle(db, project, spec.name)
        parent = last_winner(cycles)
        if parent is None and not spec.base_model:
            raise ProjectError(f"autopilot {spec.name!r} has no previous winner; pass base_model for the first cycle")
        base_model = spec.base_model or jobs_svc.find_experiment(db, project, parent or "").config["base_model"]
        record: dict[str, Any] = {"cycle": n, "name": spec.name, "dataset": spec.dataset, "started": now(), "parent": parent, "spec": asdict(spec)}
        promoted = 0
        if spec.min_trace_score is not None or spec.preference:
            try:
                promoted = traces_svc.promote(db, project, spec.dataset, spec.min_trace_score, preference=spec.preference)
                progress(f"cycle {n}: promoted {promoted} traces into {spec.dataset}")
            except ProjectError as cause:
                progress(f"cycle {n}: traces: {cause}")
        record["promoted_traces"] = promoted
        if spec.clean:
            cleaned = datasets_svc.apply_cleaning(db, project, spec.dataset, CleanSpec(dedupe_user=True, drop_empty=True, trim_whitespace=True))
            record["cleaning"] = {"before": cleaned["before"], "after": cleaned["after"], "removed": cleaned["removed"]}
            progress(f"cycle {n}: cleaned {cleaned['before']} -> {cleaned['after']} datapoints")
        datasets_svc.autosplit(db, project, spec.dataset, spec.train_pct)
        snapshot = f"{spec.name}-c{n}"
        snap = snapshots_svc.create_snapshot(db, project, spec.dataset, snapshot)
        record["snapshot"] = {"name": snapshot, "training": snap["training_count"], "validation": snap["validation_count"]}
        job_name = f"{spec.name}-c{n}"
        jobs_svc.create_job(db, project, job_name, snapshot, goal=f"autopilot {spec.name}, cycle {n}" + (f", refined from {parent}" if parent else ""))
        config_kwargs: dict[str, Any] = {"base_model": base_model, "training_type": spec.training_type, "epochs": spec.epochs, "refinetune_from": parent, "max_steps": spec.max_steps}
        if spec.learning_rate is not None:
            config_kwargs["learning_rate"] = spec.learning_rate
        elif spec.training_type == "dpo":
            config_kwargs["learning_rate"] = 5e-6
        exp = jobs_svc.add_experiment(db, project, job_name, TrainConfig(**config_kwargs))
        exp_id, number = exp.id, exp.number
    candidate = f"{job_name}/{number}"
    progress(f"cycle {n}: training {candidate} (base {base_model}, from {parent or 'base weights'})")
    status = jobs_svc.run_experiment(exp_id, progress, task_id)
    record["experiment"] = {"model": candidate, "state": status["state"], "train_loss": status.get("train_loss"), "eval_loss": (status.get("metrics") or {}).get("eval_loss"), "error": status.get("error"), "seconds": status.get("seconds")}
    if status["state"] != "succeeded":
        record.update({"winner": parent, "decision": f"training {status['state']}; keeping {parent or 'nothing'}", "finished": now()})
        _record(project_id, spec.name, n, record)
        return record
    winner, decision = candidate, "first cycle" if parent is None else "no metrics given; the newest succeeded experiment wins"
    if spec.metrics:
        baseline = parent or base_model
        with session() as db:
            project = db.get(Project, project_id)
            assert project is not None
            evaluation = evals_svc.prepare_eval(db, project, f"{spec.name}-c{n}", snapshot, [candidate, baseline], spec.metrics, spec.eval_limit, task_id)
            eval_id = evaluation.id
        try:
            summary = evals_svc.run_eval(eval_id, client, spec.eval_limit, progress=progress)
        except BaseException as cause:
            record.update({"winner": parent, "decision": f"evaluation failed: {type(cause).__name__}: {cause}; keeping {parent or 'nothing'}", "finished": now()})
            _record(project_id, spec.name, n, record)
            raise
        primary = spec.metrics[0]
        new_score, old_score = _mean_score(summary, candidate, primary), _mean_score(summary, baseline, primary)
        record["evaluation"] = {"name": f"{spec.name}-c{n}", "primary_metric": primary, "candidate": new_score, "baseline": old_score, "leaderboard": summary.get("leaderboard") or []}
        if new_score is not None and old_score is not None and new_score < old_score and parent is not None:
            winner, decision = parent, f"{primary}: {new_score:.3f} < {old_score:.3f}; keeping {parent}"
        else:
            decision = f"{primary}: {new_score} vs {old_score} ({baseline}); {candidate} wins"
    elif parent is not None:
        with session() as db:
            project = db.get(Project, project_id)
            assert project is not None
            parent_eval = (jobs_svc.find_experiment(db, project, parent).status or {}).get("metrics", {}).get("eval_loss")
        new_eval = record["experiment"]["eval_loss"]
        if parent_eval is not None and new_eval is not None:
            decision = f"eval_loss {new_eval:.4f} vs parent {parent_eval:.4f} (different snapshots; the newest experiment wins)"
    record.update({"winner": winner, "decision": decision, "finished": now()})
    progress(f"cycle {n}: {decision}")
    _record(project_id, spec.name, n, record)
    return record
