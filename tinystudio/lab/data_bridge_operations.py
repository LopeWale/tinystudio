"""Frozen promotion intent; the shared operation engine owns execution authority."""
import hashlib
import json
from sqlalchemy import select

from tinystudio.core.project import ProjectError, check_name
from tinystudio.db.models import Dataset, Project, Snapshot
from tinystudio.services import snapshots, datasets
from . import bridge, service as svc
from .contracts import canonical, checksum
from .models import LabArtifact, LabOperation


def source(db, project, run_id, artifact_id):
    run = svc.get_run(db, project, run_id)
    if run.state not in {"succeeded", "completed"}:
        raise svc.LabError("dataset promotion requires a completed run", 409)
    if not any(ref.get("id") == artifact_id for ref in (run.result or {}).get("artifacts", [])):
        raise svc.LabError("artifact is not retained by this run", 404)
    artifact = svc.get_artifact(db, project, artifact_id)
    if artifact.size > bridge.MAX_JSON_BYTES:
        raise bridge.BridgeLimit("chat artifact exceeds 16 MiB")
    return {key: getattr(artifact, key) for key in ("id", "sha256", "size", "role", "visibility")}


def request(db, project, run_id, artifact_id, name, key, *, snapshot_name=None, min_training=10):
    from .operations import _request, _snapshot
    try:
        name = check_name(name, "dataset").strip()
        snapshot_name = check_name(snapshot_name, "snapshot").strip() if snapshot_name is not None else None
    except ProjectError as exc:
        raise svc.LabError(str(exc)) from exc
    if isinstance(min_training, bool) or not isinstance(min_training, int) or not 1 <= min_training <= bridge.MAX_ROWS:
        raise svc.LabError("min_training must be an integer between 1 and 10000")
    if not isinstance(key, str) or not key.strip() or len(key) > 120:
        raise svc.LabError("idempotency key must contain 1–120 characters")
    intent = {"action": "promote_dataset", "run_id": run_id, "artifact_id": artifact_id, "name": name,
              "snapshot_name": snapshot_name, "min_training": min_training}
    # Replay intent before checking names or refreshing source fingerprints. A
    # successful operation must remain inspectable under its original request.
    if db.scalar(select(Project.id).where(Project.id == project).with_for_update()) is None:
        raise svc.LabError("project not found", 404)
    previous = db.scalar(select(LabOperation).where(LabOperation.project_id == project, LabOperation.idempotency_key == key))
    if previous:
        if previous.kind != "data_bridge" or previous.payload.get("intent") != intent:
            raise svc.LabError("idempotency key belongs to a different operation", 409)
        return previous
    frozen = source(db, project, run_id, artifact_id)
    if db.scalar(select(Dataset.id).where(Dataset.project_id == project, Dataset.name == name)) is not None:
        raise svc.LabError("dataset name already exists", 409)
    if snapshot_name is not None and db.scalar(select(Snapshot.id).where(Snapshot.project_id == project, Snapshot.name == snapshot_name)) is not None:
        raise svc.LabError("snapshot name already exists", 409)
    return _request(db, project, "data_bridge", {"intent": intent, "source": frozen,
                    "snapshot_digest": _snapshot(db, project, run_id)}, key)


def validate_source(db, project, payload, *, lock=False):
    from .operations import _snapshot
    intent = payload["intent"]
    if lock:
        # Final short locks prevent service-mediated source mutation between
        # revalidation and commit; long materialization holds neither lock.
        db.flush()
        svc.get_run(db, project, intent["run_id"], lock=True)
        db.scalar(select(LabArtifact).where(LabArtifact.id == intent["artifact_id"], LabArtifact.project_id == project)
                  .with_for_update().execution_options(populate_existing=True))
        db.expire_all()
    if source(db, project, intent["run_id"], intent["artifact_id"]) != payload["source"]:
        raise svc.LabError("promotion artifact changed after admission", 409)
    if _snapshot(db, project, intent["run_id"]) != payload["snapshot_digest"]:
        raise svc.LabError("run changed after promotion admission", 409)


def _rows_digest(rows):
    digest, count = hashlib.sha256(), 0
    for row in rows:
        encoded = canonical(row)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


def _verify_snapshot_rows(db, project, dataset_name, directory):
    """Anchor stored content to the promoted rows, not only its own manifest."""
    import pyarrow.parquet as pq
    dataset = datasets.get_dataset(db, db.get(Project, project), dataset_name)
    verified = {}
    for split, bucket in (("train", "training"), ("val", "validation")):
        expected = _rows_digest(snapshots._strip(row) for row in datasets.iter_rows(db, dataset, bucket=bucket))

        def persisted():
            for path in sorted(directory.glob(f"{split}-*.parquet")):
                for batch in pq.ParquetFile(path).iter_batches(columns=["row_json"]):
                    for value in batch.column(0).to_pylist():
                        yield json.loads(value)

        if _rows_digest(persisted()) != expected:
            raise svc.LabError("persisted snapshot rows differ from promoted dataset", 409)
        verified[split] = expected
    return verified


def execute(db, project, payload, directory):
    validate_source(db, project, payload)
    intent = payload["intent"]
    if intent["action"] != "promote_dataset":
        raise svc.LabError("unsupported data bridge action")
    output = bridge.promote_chat_dataset(db, project, intent["run_id"], intent["artifact_id"], intent["name"],
                                         budget=bridge._Budget(timeout_seconds=None))
    if intent["snapshot_name"] is not None:
        try:
            output["snapshot"] = snapshots.create_snapshot(db, db.get(Project, project), intent["name"],
                                                           intent["snapshot_name"], intent["min_training"])
        except ProjectError as exc:
            raise svc.LabError(str(exc), 409) from exc
        snapshot = db.get(Snapshot, output["snapshot"]["id"])
        store = snapshots.storage()
        manifest = store.read_manifest(snapshot.storage_prefix)
        store.hydrate_directory(snapshot.storage_prefix, directory / "snapshot-readback", refresh=True)
        if checksum(store.read_manifest(snapshot.storage_prefix)) != checksum(manifest):
            raise svc.LabError("snapshot manifest changed during verification", 409)
        output["snapshot_manifest_sha256"] = checksum(manifest)
        output["snapshot_rows"] = _verify_snapshot_rows(db, project, intent["name"], directory / "snapshot-readback")
    # Every referenced lab output is read back before publication, including the
    # provenance manifest published after the excluded final-test/truth objects.
    for ref in [output["provenance_manifest"], *output["excluded_artifacts"]]:
        row = svc.get_artifact(db, project, ref["id"])
        path = svc.materialize(row, directory / "bridge-readback")
        if path.stat().st_size != row.size:
            raise svc.LabError("persisted bridge output size mismatch", 409)
    validate_source(db, project, payload, lock=True)
    return output
