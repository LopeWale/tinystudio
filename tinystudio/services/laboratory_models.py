"""Project-scoped projections of accepted laboratory pretrained outputs."""
from sqlalchemy import select

from tinystudio.core.project import ProjectError, iso
from tinystudio.lab.models import LabArtifact, LabAttempt, LabEvent, LabRun, LabTarget
from tinystudio.serving.retained import RetainedModelSource, parse_laboratory_id


def _accepted(db, project):
    statement = (select(LabArtifact, LabAttempt, LabRun)
        .join(LabAttempt, LabArtifact.attempt_id == LabAttempt.id)
        .join(LabRun, LabAttempt.run_id == LabRun.id)
        .join(LabTarget, LabAttempt.target_id == LabTarget.id)
        .where(LabArtifact.project_id == project.id, LabRun.project_id == project.id,
               LabTarget.project_id == project.id, LabRun.target_id == LabAttempt.target_id,
               LabRun.state == "succeeded", LabAttempt.state == "succeeded",
               LabArtifact.role == "pretrained_model_bundle", LabArtifact.visibility == "model")
        .order_by(LabRun.created, LabRun.id, LabArtifact.id)
        .execution_options(populate_existing=True))
    for artifact, attempt, run in db.execute(statement):
        definition = (run.spec or {}).get("definition", {})
        if definition.get("engine") != "learning.pretrained":
            continue
        # Only server-reserved events establish which attempt's result won.
        outcomes = db.scalars(select(LabEvent).where(LabEvent.run_id == run.id,
            LabEvent.kind == "server.outcome").execution_options(populate_existing=True))
        if not any(event.payload.get("attempt_id") == attempt.id and event.payload.get("state") == "succeeded" for event in outcomes):
            continue
        refs = (run.result or {}).get("artifacts", [])
        if not isinstance(refs, list):
            continue
        matching = [ref for ref in refs if isinstance(ref, dict) and ref.get("id") == artifact.id]
        if len(matching) != 1 or any(matching[0].get(key) != getattr(artifact, key)
            for key in ("name", "sha256", "size", "role", "visibility")):
            continue
        try:
            retained = RetainedModelSource(project.id, artifact.id, artifact.sha256, artifact.size, artifact.storage_key)
        except ProjectError:
            continue
        yield artifact, run, retained


def source(db, project, model_id: str) -> RetainedModelSource:
    if project is None:
        raise ProjectError("a project is required to resolve a laboratory model")
    artifact_id = parse_laboratory_id(model_id)
    if artifact_id is None:
        raise ProjectError("not a laboratory model id")
    for artifact, _, retained in _accepted(db, project):
        if artifact.id == artifact_id:
            return retained
    raise ProjectError("accepted laboratory model not found in this project")


def rows(db, project, loaded: set[str]) -> list[dict]:
    if project is None:
        return []
    result = []
    for artifact, run, _ in _accepted(db, project):
        definition = run.spec["definition"]
        outcome = run.result or {}
        model_id = f"lab:{artifact.id}"
        result.append({"id": model_id,
            "alias": f"{definition.get('name', 'Laboratory')} r{run.revision} · {run.id[:8]}",
            "kind": "fine-tuned", "origin": "laboratory", "base_model": "immutable pretrained artifact",
            "training_type": outcome.get("qualification", {}).get("training_type") or definition.get("learning", {}).get("update_strategy"),
            "parameters": outcome.get("metrics", {}).get("total_parameters"), "created": iso(run.created),
            "status": "up" if model_id in loaded else "down",
            "notes": "Retained accepted output. Chat template and runtime capability are checked when loading."})
    return result
