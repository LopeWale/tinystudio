"""Import a project from the pre-database file layout (``tinystudio.json`` plus datasets/, snapshots/, jobs/, ...)."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select

from tinystudio.config import config
from tinystudio.core.data import read_jsonl
from tinystudio.core.project import ProjectError, read_json
from tinystudio.db import session
from tinystudio.db.models import Analysis, AutopilotRun, Document, EvalResult, Evaluation, Experiment, ExperimentLog, Metric, Project, Repository, Trace
from tinystudio.services import datasets as datasets_svc
from tinystudio.services import jobs as jobs_svc
from tinystudio.services import projects as projects_svc
from tinystudio.services import settings as settings_svc
from tinystudio.services import snapshots as snapshots_svc
from tinystudio.training.train import TrainConfig, alias_for


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def _when(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _copy_tree(source: Path, target: Path, link: bool, progress: Any) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    try:
        shutil.copytree(source, target, copy_function=os.link if link else shutil.copy2)
    except OSError:
        shutil.copytree(source, target, dirs_exist_ok=True)
    progress(f"  artifacts: {source} -> {target}")


def import_directory(root: Path, name: str | None = None, link: bool = True, progress: Any = print) -> dict[str, Any]:
    root = root.expanduser().resolve()
    manifest_path = root / "tinystudio.json"
    if not manifest_path.exists():
        raise ProjectError(f"{root} is not a file-based tinystudio project (no tinystudio.json)")
    manifest = read_json(manifest_path)
    summary: dict[str, Any] = {"datasets": 0, "datapoints": 0, "snapshots": 0, "jobs": 0, "experiments": 0, "metrics": 0, "evaluations": 0, "traces": 0, "repositories": 0, "captures": 0, "autopilot_cycles": 0, "settings": 0, "skipped": []}

    with session() as db:
        project = projects_svc.create_project(db, name or manifest.get("name") or root.name, manifest.get("goal", ""))
        project_id, slug = project.id, project.slug
    progress(f"project {slug} created")

    # datasets
    datasets_dir = root / "datasets"
    for child in sorted(datasets_dir.iterdir()) if datasets_dir.exists() else []:
        if not (child / "meta.json").exists():
            continue
        meta = read_json(child / "meta.json")
        rows = list(read_jsonl(child / "datapoints.jsonl")) if (child / "datapoints.jsonl").exists() else []
        with session() as db:
            project = db.get(Project, project_id)
            assert project is not None
            created = datasets_svc.create_dataset(db, project, child.name, rows, meta.get("origin"))
            if meta.get("labels"):
                datasets_svc.set_labels(db, project, child.name, meta["labels"])
            for record in _jsonl(child / "cleaning.jsonl"):
                from tinystudio.db.models import CleaningRun

                db.add(CleaningRun(dataset_id=created["id"], time=_when(record.get("time")), before=int(record.get("before", 0)), after=int(record.get("after", 0)), removed=record.get("removed") or {}, redacted_rows=int(record.get("redacted_rows", 0)), spec=record.get("spec") or {}))
        summary["datasets"] += 1
        summary["datapoints"] += len(rows)
        progress(f"dataset {child.name}: {len(rows)} datapoints")

    # snapshots (rows copied as they were frozen)
    snaps_dir = root / "snapshots"
    for child in sorted(snaps_dir.iterdir()) if snaps_dir.exists() else []:
        if not (child / "meta.json").exists():
            continue
        meta = read_json(child / "meta.json")
        train = list(read_jsonl(child / "train.jsonl")) if (child / "train.jsonl").exists() else []
        val = list(read_jsonl(child / "val.jsonl")) if (child / "val.jsonl").exists() else []
        with session() as db:
            from tinystudio.db.models import Dataset, Snapshot
            from tinystudio.services.snapshots import freeze

            dataset = db.scalar(select(Dataset).where(Dataset.project_id == project_id, Dataset.name == meta.get("dataset", "")))
            snapshot = Snapshot(project_id=project_id, name=child.name, dataset_id=dataset.id if dataset else None, dataset_name=meta.get("dataset", ""), training_count=len(train), validation_count=len(val), created=_when(meta.get("created")))
            db.add(snapshot)
            db.flush()
            snapshot.storage_prefix, _ = freeze(project_id, snapshot.id, {"train": train, "val": val})
        summary["snapshots"] += 1
        progress(f"snapshot {child.name}: {len(train)} train, {len(val)} val")

    # metrics
    metrics_dir = root / "metrics"
    for path in sorted(metrics_dir.glob("*.json")) if metrics_dir.exists() else []:
        m = read_json(path)
        spec = {k: m[k] for k in ("rules", "check", "webhook") if k in m}
        with session() as db:
            if db.scalar(select(Metric.id).where(Metric.project_id == project_id, Metric.name == m["name"])) is None:
                db.add(Metric(project_id=project_id, name=m["name"], kind=m["kind"], description=m.get("description", ""), spec=spec, created=_when(m.get("created"))))
                summary["metrics"] += 1
            else:
                from tinystudio.db.models import Metric as M

                row = db.scalar(select(M).where(M.project_id == project_id, M.name == m["name"]))
                if row is not None:
                    row.spec, row.description = spec, m.get("description", row.description)

    # jobs and experiments
    jobs_dir = root / "jobs"
    for child in sorted(jobs_dir.iterdir()) if jobs_dir.exists() else []:
        if not (child / "job.json").exists():
            continue
        job_meta = read_json(child / "job.json")
        with session() as db:
            project = db.get(Project, project_id)
            assert project is not None
            from tinystudio.db.models import Job, Snapshot

            snapshot = db.scalar(select(Snapshot).where(Snapshot.project_id == project_id, Snapshot.name == job_meta.get("snapshot", "")))
            job = Job(project_id=project_id, name=child.name, snapshot_id=snapshot.id if snapshot else None, snapshot_name=job_meta.get("snapshot", ""), goal=job_meta.get("goal", ""), reasoning=bool(job_meta.get("reasoning")), created=_when(job_meta.get("created")))
            db.add(job)
            db.flush()
            job_id = job.id
            if (child / "analysis.json").exists():
                payload = read_json(child / "analysis.json")
                db.add(Analysis(job_id=job_id, payload=payload, created=_when(payload.get("created"))))
        summary["jobs"] += 1
        exps_dir = child / "experiments"
        for exp_dir in sorted(exps_dir.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0) if exps_dir.exists() else []:
            if not exp_dir.name.isdigit() or not (exp_dir / "config.json").exists():
                continue
            number = int(exp_dir.name)
            cfg = TrainConfig.from_dict(read_json(exp_dir / "config.json")).as_dict()
            status = read_json(exp_dir / "status.json") if (exp_dir / "status.json").exists() else {}
            relative = f"projects/{project_id}/experiments/{job_id}/{number}"
            with session() as db:
                exp = Experiment(job_id=job_id, number=number, alias=alias_for(child.name, number), config=cfg, state=status.get("state", "pending") if status.get("state") != "running" else "failed", status={k: v for k, v in status.items() if k not in ("number",)}, artifact_dir=relative, created=_when(status.get("created")))
                db.add(exp)
                db.flush()
                exp_id = exp.id
                for row in _jsonl(exp_dir / "loss.jsonl"):
                    db.add(ExperimentLog(experiment_id=exp_id, kind="loss", step=int(row.get("step", 0)), payload=row, time=_when(row.get("time"))))
                for row in _jsonl(exp_dir / "checkpoints.jsonl"):
                    db.add(ExperimentLog(experiment_id=exp_id, kind="checkpoint", step=int(row.get("step", 0)), payload=row, time=_when(row.get("time"))))
                target = config().data_dir / relative
                _copy_tree(exp_dir, target, link, progress)
                # Publication errors roll back this experiment instead of
                # committing a row that promises unavailable remote artifacts.
                exp.status = jobs_svc.publish_existing_experiment(exp, target)
            summary["experiments"] += 1
            progress(f"experiment {child.name}/{number}: {status.get('state')}")

    # evaluations
    evals_dir = root / "evals"
    for child in sorted(evals_dir.iterdir()) if evals_dir.exists() else []:
        if not (child / "eval.json").exists():
            continue
        e = read_json(child / "eval.json")
        with session() as db:
            evaluation = Evaluation(project_id=project_id, name=child.name, snapshot_name=e.get("snapshot", ""), models=e.get("models", []), metrics=e.get("metrics", []), datapoints=int(e.get("datapoints", 0)), state=e.get("state", "failed"), created=_when(e.get("created")), finished=_when(e.get("finished")), error=e.get("error"), leaderboard=e.get("leaderboard"))
            db.add(evaluation)
            db.flush()
            rows = _jsonl(child / "results.jsonl")
            payload = [{"evaluation_id": evaluation.id, "datapoint": int(r.get("datapoint", 0)), "model": str(r.get("model", "")), "metric": str(r.get("metric", "")), "score": float(r.get("score", 0)), "reason": str(r.get("reason", "")), "prediction": str(r.get("prediction", "")), "reference": str(r.get("reference", "")), "user": str(r.get("user", "")), "seconds": r.get("seconds"), "detail": {k: v for k, v in r.items() if k not in ("datapoint", "model", "metric", "score", "reason", "prediction", "reference", "user", "seconds")}} for r in rows]
            for offset in range(0, len(payload), 2000):
                db.execute(insert(EvalResult), payload[offset : offset + 2000])
        summary["evaluations"] += 1

    # traces
    with session() as db:
        for record in _jsonl(root / "traces.jsonl"):
            if db.get(Trace, str(record.get("id"))) is None:
                chunks = record.get("document_chunks")
                db.add(Trace(id=str(record["id"]), project_id=project_id, time=_when(record.get("time")), model=str(record.get("model", "")), messages=record.get("messages", []), response=str(record.get("response", "")), seconds=record.get("seconds"), prompt_tokens=record.get("prompt_tokens"), completion_tokens=record.get("completion_tokens"), score=record.get("score"), note=record.get("note"), corrected=record.get("corrected"), origin=record.get("origin"), document_chunks=chunks if isinstance(chunks, list) else None))
                summary["traces"] += 1

    # repositories: documents to Postgres, chunks and vectors to Qdrant (vectors were saved normalized)
    repos_dir = root / "repositories"
    for child in sorted(repos_dir.iterdir()) if repos_dir.exists() else []:
        if not (child / "meta.json").exists():
            continue
        meta = read_json(child / "meta.json")
        chunks = _jsonl(child / "chunks.jsonl")
        matrix = None
        if (child / "embeddings.npy").exists():
            import numpy as np

            matrix = np.load(child / "embeddings.npy")
        with session() as db:
            repo = Repository(project_id=project_id, name=child.name, description=meta.get("description", ""), embedding_model=meta.get("embedding_model", ""), dimension=meta.get("dimension"), collection="", created=_when(meta.get("created")))
            db.add(repo)
            db.flush()
            repo.collection = f"ts_{repo.id}"
            docs: dict[str, str] = {}
            for d in meta.get("documents", []):
                doc = Document(repository_id=repo.id, name=d["name"], kind=d.get("kind", "file"), chars=int(d.get("chars", 0)), chunks=int(d.get("chunks", 0)), added=_when(d.get("added")))
                db.add(doc)
                db.flush()
                docs[d["name"]] = doc.id
            collection, dimension = repo.collection, repo.dimension
        if matrix is not None and chunks and dimension:
            from tinystudio import vectors

            vectors.ensure_collection(collection, int(dimension))
            per_doc: dict[str, int] = {}
            points = []
            for i, c in enumerate(chunks):
                doc_id = docs.get(c.get("document", ""))
                if doc_id is None or i >= matrix.shape[0]:
                    continue
                index = per_doc.get(doc_id, 0)
                per_doc[doc_id] = index + 1
                points.append({"id": vectors.point_id(collection, doc_id, index), "vector": matrix[i].tolist(), "payload": {"text": c.get("text", ""), "document_id": doc_id, "document": c.get("document", ""), "kind": c.get("kind", "file"), "index": index}})
            vectors.upsert(collection, points)
            progress(f"repository {child.name}: {len(points)} chunks into Qdrant")
        summary["repositories"] += 1

    # interpretability artifacts
    from tinystudio.services.interp import ProjectStore

    store = ProjectStore(project_id)
    for kind, base in (("capture", root / "interp" / "captures"), ("diff", root / "interp" / "diffs")):
        for child in sorted(base.iterdir()) if base.exists() else []:
            if (child / "meta.json").exists():
                target = (store.captures_dir if kind == "capture" else store.diffs_dir) / child.name
                _copy_tree(child, target, link, progress)
                store.register(kind, child.name, read_json(child / "meta.json"))
                summary["captures"] += 1

    # autopilot
    auto_dir = root / "autopilot"
    with session() as db:
        for path in sorted(auto_dir.glob("*.jsonl")) if auto_dir.exists() else []:
            for record in _jsonl(path):
                db.add(AutopilotRun(project_id=project_id, name=path.stem, cycle=int(record.get("cycle", 0)), payload=record, created=_when(record.get("finished") or record.get("started"))))
                summary["autopilot_cycles"] += 1

    # settings: seed global settings that are not stored yet
    if (root / "settings.json").exists():
        values = (read_json(root / "settings.json") or {}).get("values", {})
        with session() as db:
            stored = settings_svc.stored(db)
            changes = {k: v for k, v in values.items() if k in settings_svc.SPEC and k not in stored}
            if changes:
                settings_svc.save(db, changes)
                summary["settings"] = len(changes)
    if (root / ".api-keys.json").exists():
        summary["skipped"].append("API keys (only digests were stored; create new keys per user)")
    summary["project"] = slug
    return summary
