"""Continuous fine-tuning cycles.

Prem's guides describe the loop as evaluate -> find weaknesses -> expand the dataset -> fine-tune again ->
re-evaluate, and Studio's "continuous fine-tuning" starts a new experiment from an earlier experiment's weights.
Here one call runs one complete cycle on this machine:

    scored traces -> dataset  (promote; optionally as DPO preference pairs)
    clean (dedupe, drop empty, trim)
    autosplit -> immutable snapshot  <name>-c<N>
    job <name>-c<N> with one experiment refined from the previous cycle's winner (or the base model)
    evaluation of the new experiment against the previous winner on the cycle's snapshot
    winner recorded in autopilot/<name>.jsonl, so the next cycle knows where to start

Nothing here is automatic: a cycle runs when you (or an assistant through the API/MCP) ask for it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tinystudio.core.data import autosplit, create_snapshot
from tinystudio.evaluation.evaluate import run_eval
from tinystudio.core.llm import LLMClient
from tinystudio.core.project import Project, ProjectError, now, read_json
from tinystudio.core.quality import CleanSpec, apply_cleaning
from tinystudio.training.train import TrainConfig, create_job, create_pending_experiment, experiment_dir, run_experiment_number
from tinystudio.core.traces import promote


@dataclass
class CycleSpec:
    name: str
    dataset: str
    base_model: str | None = None  # required for the first cycle; later cycles refine the previous winner
    training_type: str = "lora"  # lora | full | dpo
    epochs: float = 2.0
    learning_rate: float | None = None
    min_trace_score: float | None = None  # promote traces scored at or above this before training
    preference: bool = False  # promote corrected traces as DPO pairs (use with training_type="dpo")
    clean: bool = True
    train_pct: int = 85
    metrics: list[str] = field(default_factory=list)
    eval_limit: int | None = None
    max_steps: int = -1


def history_path(project: Project, name: str) -> Path:
    return project.root / "autopilot" / f"{name}.jsonl"


def read_history(project: Project, name: str) -> list[dict[str, Any]]:
    path = history_path(project, name)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def list_histories(project: Project) -> list[dict[str, Any]]:
    directory = project.root / "autopilot"
    out = []
    for path in sorted(directory.glob("*.jsonl")) if directory.exists() else []:
        cycles = read_history(project, path.stem)
        out.append({"name": path.stem, "cycles": len(cycles), "dataset": cycles[-1]["dataset"] if cycles else None, "winner": cycles[-1].get("winner") if cycles else None, "last": cycles[-1].get("finished") if cycles else None})
    return out


def next_cycle(project: Project, name: str) -> int:
    n = len(read_history(project, name)) + 1
    while (project.job_dir(f"{name}-c{n}") / "job.json").exists() or (project.snapshot_dir(f"{name}-c{n}") / "meta.json").exists():
        n += 1
    return n


def last_winner(history: list[dict[str, Any]]) -> str | None:
    for record in reversed(history):
        if record.get("winner"):
            return str(record["winner"])
    return None


def base_model_of(project: Project, model_id: str) -> str:
    job, _, number = model_id.partition("/")
    path = experiment_dir(project, job, int(number or 1)) / "config.json"
    if not path.exists():
        raise ProjectError(f"experiment {model_id} has no config.json")
    return str(read_json(path)["base_model"])


def _mean_score(evaluation: dict[str, Any], model_id: str, metric: str | None) -> float | None:
    for entry in evaluation.get("leaderboard", []):
        if entry["model"] == model_id:
            return entry["scores"].get(metric) if metric else entry.get("overall")
    return None


def run_cycle(project: Project, spec: CycleSpec, client: LLMClient | None = None, progress: Any = print) -> dict[str, Any]:
    """Run one cycle and append its record to autopilot/<name>.jsonl. Returns the record."""
    history = read_history(project, spec.name)
    n = next_cycle(project, spec.name)
    parent = last_winner(history)
    if parent is None and not spec.base_model:
        raise ProjectError(f"autopilot {spec.name!r} has no previous winner; pass base_model for the first cycle")
    base_model = spec.base_model or base_model_of(project, parent or "")
    record: dict[str, Any] = {"cycle": n, "name": spec.name, "dataset": spec.dataset, "started": now(), "parent": parent, "spec": asdict(spec)}

    promoted = 0
    if spec.min_trace_score is not None or spec.preference:
        try:
            promoted = promote(project, spec.dataset, spec.min_trace_score, preference=spec.preference)
            progress(f"cycle {n}: promoted {promoted} traces into {spec.dataset}")
        except ProjectError as cause:
            progress(f"cycle {n}: traces: {cause}")
    record["promoted_traces"] = promoted

    if spec.clean:
        cleaned = apply_cleaning(project, spec.dataset, CleanSpec(dedupe_user=True, drop_empty=True, trim_whitespace=True))
        record["cleaning"] = {"before": cleaned["before"], "after": cleaned["after"], "removed": cleaned["removed"]}
        progress(f"cycle {n}: cleaned {cleaned['before']} -> {cleaned['after']} datapoints")

    autosplit(project, spec.dataset, spec.train_pct)
    snapshot = f"{spec.name}-c{n}"
    snap = create_snapshot(project, spec.dataset, snapshot)
    record["snapshot"] = {"name": snapshot, "training": snap.get("training_count"), "validation": snap.get("validation_count")}

    job = f"{spec.name}-c{n}"
    create_job(project, job, snapshot, goal=f"autopilot {spec.name}, cycle {n}" + (f", refined from {parent}" if parent else ""))
    config_kwargs: dict[str, Any] = {"base_model": base_model, "training_type": spec.training_type, "epochs": spec.epochs, "refinetune_from": parent, "max_steps": spec.max_steps}
    if spec.learning_rate is not None:
        config_kwargs["learning_rate"] = spec.learning_rate
    elif spec.training_type == "dpo":
        config_kwargs["learning_rate"] = 5e-6
    number = create_pending_experiment(project, job, TrainConfig(**config_kwargs))
    candidate = f"{job}/{number}"
    progress(f"cycle {n}: training {candidate} (base {base_model}, from {parent or 'base weights'})")
    status = run_experiment_number(project, job, number, progress)
    record["experiment"] = {"model": candidate, "state": status["state"], "train_loss": status.get("train_loss"), "eval_loss": status.get("metrics", {}).get("eval_loss"), "error": status.get("error"), "seconds": status.get("seconds")}
    if status["state"] != "succeeded":
        record.update({"winner": parent, "decision": f"training {status['state']}; keeping {parent or 'nothing'}", "finished": now()})
        _append(project, spec.name, record)
        return record

    winner, decision = candidate, "first cycle" if parent is None else "no metrics given; the newest succeeded experiment wins"
    if spec.metrics:
        baseline = parent or base_model
        try:
            evaluation = run_eval(project, f"{spec.name}-c{n}", snapshot, [candidate, baseline], spec.metrics, client, spec.eval_limit, progress=progress)
        except BaseException as cause:  # record the trained experiment before the failure propagates
            record.update({"winner": parent, "decision": f"evaluation failed: {type(cause).__name__}: {cause}; keeping {parent or 'nothing'}", "finished": now()})
            _append(project, spec.name, record)
            raise
        primary = spec.metrics[0]
        new_score, old_score = _mean_score(evaluation, candidate, primary), _mean_score(evaluation, baseline, primary)
        record["evaluation"] = {"name": f"{spec.name}-c{n}", "primary_metric": primary, "candidate": new_score, "baseline": old_score, "leaderboard": evaluation.get("leaderboard", [])}
        if new_score is not None and old_score is not None and new_score < old_score and parent is not None:
            winner, decision = parent, f"{primary}: {new_score:.3f} < {old_score:.3f}; keeping {parent}"
        else:
            decision = f"{primary}: {new_score} vs {old_score} ({baseline}); {candidate} wins"
    elif parent is not None:
        parent_job, _, parent_number = parent.partition("/")
        parent_status_path = experiment_dir(project, parent_job, int(parent_number or 1)) / "status.json"
        parent_eval = read_json(parent_status_path).get("metrics", {}).get("eval_loss") if parent_status_path.exists() else None
        new_eval = record["experiment"]["eval_loss"]
        if parent_eval is not None and new_eval is not None:
            decision = f"eval_loss {new_eval:.4f} vs parent {parent_eval:.4f} (different snapshots; the newest experiment wins)"
    record.update({"winner": winner, "decision": decision, "finished": now()})
    progress(f"cycle {n}: {decision}")
    _append(project, spec.name, record)
    return record


def _append(project: Project, name: str, record: dict[str, Any]) -> None:
    path = history_path(project, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")
