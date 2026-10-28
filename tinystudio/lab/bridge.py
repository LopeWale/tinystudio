"""Bounded synchronous compatibility bridge into existing application services.

Caller owns the Session transaction. These APIs never commit, train, merge model
adapters, fetch new base models, or claim asynchronous/batch support. Admission
caps: 10k rows, 16 MiB JSON, 256 regular model files / 512 MiB. Deadline checks are
cooperative between service calls; existing storage calls retain their own I/O
timeouts. Larger transfers require a future durable batch bridge.
"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
import hashlib
import json
from pathlib import Path
import stat
import tarfile
import tempfile
import time

from sqlalchemy import select, func, cast, Text

from tinystudio.core.data import validate_datapoint
from tinystudio.core.project import ProjectError, check_name
from tinystudio.config import config
from tinystudio.db.models import Project, Snapshot, SnapshotRow
from tinystudio.services import datasets as datasets_svc, jobs as jobs_svc, snapshots as snapshots_svc
from tinystudio.storage import storage
from . import service
from .contracts import canonical, checksum
from .engine import digest

MAX_ROWS = 10_000
MAX_JSON_BYTES = 16 * 1024**2
MAX_MODEL_BYTES = 512 * 1024**2
MAX_MODEL_FILES = 256
MAX_SYNC_SECONDS = 30


class BridgeLimit(service.LabError):
    def __init__(self, detail):
        super().__init__(detail + "; split the evidence into bounded inputs; larger batch transfers are not qualified", 413)


class _Budget:
    def __init__(self, timeout_seconds=MAX_SYNC_SECONDS):
        self.started = time.monotonic()
        self.timeout_seconds = timeout_seconds
    def check(self):
        if self.timeout_seconds is not None and time.monotonic() - self.started > self.timeout_seconds:
            raise BridgeLimit("synchronous bridge deadline exceeded")
    def json(self, value):
        self.check()
        encoded = canonical(value)
        if len(encoded) > MAX_JSON_BYTES:
            raise BridgeLimit("bridge JSON exceeds 16 MiB")
        return encoded


def _project(db, project):
    value = db.get(Project, project) if isinstance(project, str) else project
    if value is None or not isinstance(value.id, str) or not value.id:
        raise service.LabError("project not found", 404)
    return value


def _project_errors(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ProjectError as exc:
            message = str(exc)
            status = 404 if any(text in message.casefold() for text in ("does not exist", "not found")) else 409 if any(text in message.casefold() for text in ("already exists", "changed", "do not match", "no longer")) else 400
            raise service.LabError(message, status) from exc
    return call


def _published_bound(prefix, maximum):
    if prefix:
        manifest = storage().read_manifest(prefix)
        if len(manifest["files"]) > MAX_MODEL_FILES or sum(item["size"] for item in manifest["files"]) > maximum:
            raise BridgeLimit("published artifact exceeds synchronous admission limits")
        return manifest
    return None


def _snapshot_preflight(db, snapshot, budget):
    prefix = getattr(snapshot, "storage_prefix", None)
    manifest = _published_bound(prefix, MAX_JSON_BYTES)
    expected = snapshot.training_count + snapshot.validation_count
    if prefix:
        import pyarrow.parquet as pq
        directory = config().project_dir(snapshot.project_id) / "snapshots" / snapshot.id
        storage().hydrate_directory(prefix, directory)
        logical_bytes = records = 0
        for path in sorted([*directory.glob("train-*.parquet"), *directory.glob("val-*.parquet")]):
            budget.check()
            parquet = pq.ParquetFile(path)
            if sum(parquet.metadata.row_group(i).total_byte_size for i in range(parquet.metadata.num_row_groups)) > MAX_JSON_BYTES:
                raise BridgeLimit("snapshot shard expands beyond synchronous JSON limit")
            for batch in parquet.iter_batches(batch_size=1, columns=["row_json"]):
                budget.check()
                for value in batch["row_json"].to_pylist():
                    if not isinstance(value, str):
                        raise service.LabError("snapshot row_json column must contain text", 409)
                    logical_bytes += len(value.encode())
                    records += 1
                    if logical_bytes > MAX_JSON_BYTES or records > MAX_ROWS:
                        raise BridgeLimit("snapshot logical rows exceed synchronous admission limits")
    else:
        records, logical_bytes = db.execute(select(func.count(SnapshotRow.id),
            func.coalesce(func.sum(func.octet_length(cast(SnapshotRow.row, Text))), 0)).where(SnapshotRow.snapshot_id == snapshot.id)).one()
        if logical_bytes > MAX_JSON_BYTES or records > MAX_ROWS:
            raise BridgeLimit("legacy snapshot logical rows exceed synchronous admission limits")
    if records != expected:
        raise service.LabError("snapshot stored row count differs from its frozen counts", 409)
    budget.check()
    return manifest


PARTITIONS = {"train": "train", "training": "train", "val": "validation", "validation": "validation",
              "test": "final_test", "final_test": "final_test", "final-test": "final_test", "finaltest": "final_test",
              "heldout": "final_test", "held_out": "final_test", "held-out": "final_test",
              "truth": "truth", "ground_truth": "truth", "ground-truth": "truth"}


def _markers(row):
    from .partitioning import partition_label
    try:
        value = partition_label(row, allow_truth=True, allow_unassigned=True)
    except ValueError as exc:
        raise service.LabError(str(exc), 409) from exc
    return {"final_test" if value == "test" else value} if value is not None else set()


def _stored(db, project, path, role, visibility="model"):
    row = service.store_artifact(db, project.id, path, path.name,
                                 "application/json" if path.suffix == ".json" else "application/x-ndjson" if path.suffix == ".jsonl" else "application/x-tar",
                                 role=role, visibility=visibility)
    return service.artifact_dict(row)


def _manifest(db, project, directory, payload, budget, visibility="evaluation"):
    path = directory / "bridge-manifest.json"
    path.write_bytes(budget.json({"format": "tinystudio.bridge.v1", **payload}))
    return _stored(db, project, path, "bridge_manifest", visibility)


@_project_errors
def snapshot_input(db, project, snapshot_id_or_name: str):
    """Freeze an existing scoped snapshot as role=snapshot and return its manifest."""
    project = _project(db, project)
    budget = _Budget()
    snapshot = db.scalar(select(Snapshot).where(Snapshot.project_id == project.id, Snapshot.id == snapshot_id_or_name))
    if snapshot is None:
        snapshot = snapshots_svc.get_snapshot(db, project, snapshot_id_or_name)
    if snapshot.project_id != project.id:
        raise service.LabError("snapshot not found", 404)
    expected = int(snapshot.training_count) + int(snapshot.validation_count)
    if expected < 1 or expected > MAX_ROWS or min(snapshot.training_count, snapshot.validation_count) < 0:
        raise BridgeLimit("snapshot row count is outside synchronous admission limits")
    source_manifest = _snapshot_preflight(db, snapshot, budget)
    train, validation = snapshots_svc.snapshot_rows(db, snapshot)
    if (len(train), len(validation)) != (snapshot.training_count, snapshot.validation_count):
        raise service.LabError("snapshot rows no longer match frozen counts", 409)
    output, lineage = [], []
    for split, rows in (("train", train), ("validation", validation)):
        for position, original in enumerate(rows):
            if not isinstance(original, dict):
                raise service.LabError("snapshot contains a non-object row")
            markers = _markers(original)
            if markers - {split} or original.get("visibility") == "truth" or original.get("role") in {"truth", "ground_truth", "final_test", "finaltest"}:
                raise service.LabError("snapshot contains a protected held-out/truth marker inconsistent with legacy train/validation buckets", 409)
            source_id = f"snapshot:{snapshot.id}:{split}:{position}"
            output.append({**deepcopy(original), "source_id": source_id, "source_record_id": source_id,
                           "partition": split, "split": split, "bucket": "training" if split == "train" else "validation"})
            lineage.append({"source_id": source_id, "split": split, "split_position": position, "original_row": original})
    with tempfile.TemporaryDirectory(prefix="tinystudio-bridge-snapshot-") as temporary:
        directory = Path(temporary)
        path = directory / "snapshot.jsonl"
        size = 0
        with path.open("wb") as stream:
            for row in output:
                encoded = budget.json(row) + b"\n"
                size += len(encoded)
                if size > MAX_JSON_BYTES:
                    raise BridgeLimit("snapshot JSONL exceeds 16 MiB")
                stream.write(encoded)
        # Validate the full provenance before publishing either object.
        provenance = {"kind": "existing_snapshot", "snapshot_id": snapshot.id, "snapshot_name": snapshot.name,
                      "dataset_id": snapshot.dataset_id, "dataset_name": snapshot.dataset_name,
                      "created": str(snapshot.created) if snapshot.created else None,
                      "source_storage_prefix": getattr(snapshot, "storage_prefix", None),
                      "source_manifest_sha256": checksum(source_manifest) if source_manifest else None,
                      "partition_counts": {"train": len(train), "validation": len(validation)},
                      "final_test_available": False, "position_basis": "frozen split iteration order", "records": lineage}
        budget.json(provenance)
        artifact = _stored(db, project, path, "snapshot")
        manifest = _manifest(db, project, directory, {**provenance, "export_artifact": artifact}, budget)
    return {**artifact, "input": {"artifact_id": artifact["id"], "sha256": artifact["sha256"], "role": "snapshot", "visibility": "model"},
            "provenance_manifest": manifest, "partition_counts": provenance["partition_counts"], "final_test_available": False}


def _model_files(root, budget):
    if root.is_symlink() or not root.is_dir():
        raise service.LabError("model export requires an existing regular directory")
    files = []
    total = 0
    for visited, path in enumerate(root.rglob("*")):
        budget.check()
        if visited > 4 * MAX_MODEL_FILES:
            raise BridgeLimit("model tree exceeds bounded synchronous traversal")
        if path.is_symlink():
            raise service.LabError("model export refuses symbolic links")
        if path.is_dir():
            continue
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise service.LabError("model export permits regular files only")
        total += info.st_size
        if len(files) >= MAX_MODEL_FILES or total > MAX_MODEL_BYTES:
            raise BridgeLimit("model directory exceeds 256 files or 512 MiB")
        files.append((path, {"path": path.relative_to(root).as_posix(), "size": info.st_size, "sha256": digest(path)}))
    return sorted(files, key=lambda item: item[1]["path"])


@_project_errors
def model_input(db, project, job_name: str, number: int):
    """Export only a succeeded model or an already materialized merged directory."""
    project = _project(db, project)
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise service.LabError("experiment number must be a positive integer")
    budget = _Budget()
    experiment = jobs_svc.get_experiment(db, project, job_name, number)
    job = experiment.job
    if job.project_id != project.id:
        raise service.LabError("experiment not found", 404)
    if experiment.state != "succeeded":
        raise service.LabError("model export requires a succeeded experiment", 409)
    status = experiment.status or {}
    _published_bound(status.get("artifact_prefix"), MAX_MODEL_BYTES)
    path = jobs_svc.ensure_experiment_artifacts(experiment)
    candidates = [path / "model", *jobs_svc._merged_cache_paths(experiment)]
    selected = next((candidate for candidate in candidates if candidate.is_dir() and (candidate / "config.json").is_file()), None)
    if selected is None:
        raise service.LabError("no completed full model or existing merged model; explicitly prepare/publish a merged LoRA model first (no implicit merge or base-model download)", 409)
    if (selected / "adapter_config.json").exists():
        raise service.LabError("an unmerged adapter is not a full-model input", 409)
    files = _model_files(selected, budget)
    names = {item["path"] for _, item in files}
    if not any(name.endswith(".safetensors") or name.endswith(".bin") for name in names):
        raise service.LabError("full-model weights are missing", 409)
    with tempfile.TemporaryDirectory(prefix="tinystudio-bridge-model-") as temporary:
        directory = Path(temporary)
        archive = directory / "model.tar"
        with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tar:
            for source, expected in files:
                budget.check()
                if source.stat().st_size != expected["size"] or digest(source) != expected["sha256"]:
                    raise service.LabError("model source changed before archiving", 409)
                info = tarfile.TarInfo(expected["path"])
                info.size, info.mode, info.mtime = expected["size"], 0o644, 0
                with source.open("rb") as stream:
                    tar.addfile(info, stream)
        with tarfile.open(archive, "r") as tar:
            for member, (_, expected) in zip(tar.getmembers(), files, strict=True):
                stream = tar.extractfile(member)
                if not member.isfile() or stream is None or hashlib.file_digest(stream, "sha256").hexdigest() != expected["sha256"]:
                    raise service.LabError("model archive verification failed", 409)
        provenance = {"kind": "existing_completed_model", "job": {"id": job.id, "name": job.name, "snapshot_id": job.snapshot_id, "snapshot_name": job.snapshot_name},
                      "experiment": {"id": experiment.id, "number": experiment.number, "alias": experiment.alias, "state": experiment.state,
                                     "config": experiment.config, "config_sha256": checksum(experiment.config), "status": status},
                      "model_layout": "full" if selected == path / "model" else "existing_merged", "files": [entry for _, entry in files]}
        budget.json(provenance)
        artifact = _stored(db, project, archive, "pretrained_model")
        manifest = _manifest(db, project, directory, {**provenance, "export_artifact": artifact}, budget)
    return {**artifact, "input": {"artifact_id": artifact["id"], "sha256": artifact["sha256"], "role": "pretrained_model", "visibility": "model"}, "provenance_manifest": manifest}


def _partition(row):
    markers = _markers(row)
    if not markers:
        raise service.LabError("chat promotion requires an explicit train/validation/final_test partition")
    if len(markers) != 1:
        raise service.LabError("contradictory partition/split/bucket markers cannot be relabeled", 409)
    return next(iter(markers))


def chat_rows(rows, *, artifact_visibility="model", partitioned_container=False):
    """Pure serializer. Preserve raw records in lineage; never synthesize messages."""
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise BridgeLimit("chat row count is outside synchronous admission limits")
    from .partitioning import source_protection

    dataset, lineage, final_test, truth = [], [], [], []
    partitions = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or "messages" not in row:
            raise service.LabError("promotion accepts explicit chat messages only; numeric records are not converted into Q&A")
        source_id = row.get("source_id", row.get("source_record_id"))
        group = row.get("group_id", row.get("episode_id"))
        if not isinstance(source_id, str) or not source_id or not isinstance(group, str) or not group:
            raise service.LabError("chat promotion requires known source_id and group_id/episode_id")
        partition = _partition(row)
        try:
            protection = source_protection(row, visibility="model" if partitioned_container and artifact_visibility == "evaluation" else artifact_visibility)
        except ValueError as exc:
            raise service.LabError(str(exc), 409) from exc
        is_truth = partition == "truth" or artifact_visibility == "truth" or protection == "truth"
        if not is_truth and partition == "train" and protection != "model":
            raise service.LabError("evaluation-bound evidence cannot enter training", 409)
        classification = "truth" if is_truth else partition
        for key in (("source", source_id), ("group", group)):
            if key in partitions and partitions[key] != classification:
                raise service.LabError("source or group overlaps promotion partitions", 409)
            partitions[key] = classification
        normalized = validate_datapoint({**row, "bucket": "training" if partition == "train" else "validation"})
        source_link = source_id if len(source_id) <= 200 else "lab-source:sha256:" + hashlib.sha256(source_id.encode()).hexdigest()
        mapping = {"source_id": source_id, "group_id": group, "partition": classification, "source_link": source_link,
                   "source_row": index, "raw_sha256": checksum(row), "raw_row": deepcopy(row), "dataset_position": None}
        if is_truth:
            truth.append(deepcopy(row))
        elif partition == "final_test":
            final_test.append(deepcopy(row))
        else:
            mapping["dataset_position"] = len(dataset)
            dataset.append({**normalized, "source": source_link})
        lineage.append(mapping)
    return dataset, lineage, final_test, truth


@_project_errors
def promote_chat_dataset(db, project, run_id: str, artifact_id: str, name: str, *, budget=None):
    """Promote explicit training/validation chats; retain final-test and truth separately."""
    project = _project(db, project)
    check_name(name, "dataset")
    budget = budget or _Budget()
    run = service.get_run(db, project.id, run_id)
    if run.project_id != project.id:
        raise service.LabError("run not found", 404)
    if run.state not in {"succeeded", "completed"}:
        raise service.LabError("chat promotion requires a completed run", 409)
    selected = next((item for item in (run.result or {}).get("artifacts", []) if item.get("id") == artifact_id), None)
    if selected is None:
        raise service.LabError("artifact is not retained by this run", 404)
    artifact = service.get_artifact(db, project.id, artifact_id)
    if artifact.project_id != project.id:
        raise service.LabError("artifact not found", 404)
    if artifact.size > MAX_JSON_BYTES:
        raise BridgeLimit("chat artifact exceeds 16 MiB")
    with tempfile.TemporaryDirectory(prefix="tinystudio-bridge-chat-") as temporary:
        directory = Path(temporary)
        path = service.materialize(artifact, directory)
        if path.stat().st_size != artifact.size or path.stat().st_size > MAX_JSON_BYTES or digest(path) != artifact.sha256:
            raise service.LabError("chat artifact checksum/size mismatch", 409)
        text = path.read_text()
        try:
            rows = json.loads(text)
        except json.JSONDecodeError:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        if isinstance(rows, dict):
            rows = rows.get("records", [rows])
        # The pipeline's supervised_dataset is an explicitly partitioned container.
        # Other evaluation-bound artifacts cannot acquire training authority merely
        # by carrying row-level train labels. Row/source restrictions still apply.
        dataset_rows, lineage, final_test, truth = chat_rows(rows, artifact_visibility=artifact.visibility,
                                                            partitioned_container=artifact.role == "supervised_dataset")
        if not dataset_rows:
            raise service.LabError("artifact contains no explicit training/validation chat rows")
        provenance = {"kind": "lab_chat_promotion", "source_run_id": run.id, "source_spec_digest": run.spec_digest,
                      "source_artifact": service.artifact_dict(artifact), "source_mapping": lineage,
                      "excluded_final_test_rows": len(final_test), "excluded_truth_rows": len(truth)}
        budget.json(provenance)
        excluded = []
        for filename, retained, role, visibility in (("final-test.jsonl", final_test, "final_test", "evaluation"), ("truth.jsonl", truth, "truth", "truth")):
            if retained:
                target = directory / filename
                target.write_bytes(b"".join(budget.json(row) + b"\n" for row in retained))
                excluded.append(_stored(db, project, target, role, visibility))
        manifest = _manifest(db, project, directory, {**provenance, "excluded_artifacts": excluded}, budget, "truth" if truth else "evaluation")
        dataset = datasets_svc.create_dataset(db, project, name, dataset_rows,
            {"kind": "lab_chat_promotion", "run_id": run.id, "artifact_id": artifact.id,
             "manifest_artifact_id": manifest["id"], "manifest_sha256": manifest["sha256"],
             "excluded_final_test_rows": len(final_test), "excluded_truth_rows": len(truth)})
        budget.check()
    return {"dataset": dataset, "provenance_manifest": manifest, "excluded_artifacts": excluded,
            "promoted_rows": len(dataset_rows), "excluded_final_test_rows": len(final_test), "excluded_truth_rows": len(truth)}
