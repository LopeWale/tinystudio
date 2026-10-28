"""Bounded, optimistic capture of mutable legacy repository evidence.

Two matching observations detect ordinary concurrent changes; PostgreSQL locks
cannot fence Qdrant writers. This exports dense points, never an HNSW snapshot
or pinned encoder weights. The operation lease owns the final publication fence.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import select

from tinystudio import vectors
from tinystudio.core.project import ProjectError, check_name
from tinystudio.db.models import Document, Project, Repository
from . import service as svc
from .contracts import checksum
from .models import LabOperation
from .partitioning import source_protection

MAX_POINTS = 10_000
MAX_BYTES = 64 * 1024 * 1024
MAX_POINT_BYTES = 1024 * 1024
PAGE_SIZE = 100


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _metadata(db, project, repository_id=None, name=None, *, lock=False):
    query = select(Repository).where(Repository.project_id == project)
    query = query.where(Repository.id == repository_id) if repository_id else query.where(Repository.name == name)
    if lock:
        query = query.with_for_update()
    repository = db.scalar(query.execution_options(populate_existing=True))
    if repository is None:
        raise svc.LabError("repository not found or changed since admission", 404)
    query = select(Document).where(Document.repository_id == repository.id).order_by(Document.id).limit(MAX_POINTS + 1)
    if lock:
        query = query.with_for_update()
    documents = list(db.scalars(query.execution_options(populate_existing=True)))
    if len(documents) > MAX_POINTS:
        raise svc.LabError("repository capture exceeds 10000 documents", 413)
    if not documents:
        raise svc.LabError("repository is empty; ingest documents before capture", 409)
    if any(doc.chunks <= 0 for doc in documents):
        raise svc.LabError("repository has incomplete documents; finish ingestion before capture", 409)
    if sum(doc.chunks for doc in documents) > MAX_POINTS:
        raise svc.LabError("repository capture exceeds 10000 points; use a smaller repository", 413)
    if not repository.dimension or repository.dimension <= 0 or not repository.collection:
        raise svc.LabError("repository embedding metadata is incomplete", 409)
    return {"id": repository.id, "name": repository.name, "model_identifier": repository.embedding_model,
            "dimension": repository.dimension, "collection": repository.collection,
            "documents": [{"id": doc.id, "name": doc.name, "kind": doc.kind, "chars": doc.chars,
                           "chunks": doc.chunks, "added": doc.added.isoformat()} for doc in documents]}


def request_capture(db, project_id, repository_name, idempotency_key):
    """Replay normalized intent before resolving a mutable source name."""
    from .operations import _request
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 120:
        raise svc.LabError("idempotency key must contain 1–120 characters")
    # Normalizing a string does not consult mutable source state.
    name = repository_name.strip() if isinstance(repository_name, str) else repository_name
    intent = {"action": "capture_repository", "repository": name}
    if db.scalar(select(Project.id).where(Project.id == project_id).with_for_update()) is None:
        raise svc.LabError("project not found", 404)
    previous = db.scalar(select(LabOperation).where(LabOperation.project_id == project_id,
                                                     LabOperation.idempotency_key == idempotency_key))
    if previous:
        if previous.kind != "data_bridge" or previous.payload.get("intent") != intent:
            raise svc.LabError("idempotency key belongs to a different operation", 409)
        return previous
    if not isinstance(name, str):
        raise svc.LabError("repository name must be a string")
    try:
        check_name(name, "repository")
    except (ProjectError, TypeError) as exc:
        raise svc.LabError(str(exc)) from exc
    metadata = _metadata(db, project_id, name=name)
    return _request(db, project_id, "data_bridge", {"intent": intent, "source": metadata,
                     "metadata_fingerprint": checksum(metadata)}, idempotency_key)


def _validate_source(db, project, payload, *, lock=False):
    metadata = _metadata(db, project, repository_id=payload["source"]["id"], lock=lock)
    if metadata != payload["source"] or checksum(metadata) != payload["metadata_fingerprint"]:
        raise svc.LabError("repository metadata changed since capture admission", 409)
    return metadata


def _configuration(client, source):
    info = client.get_collection(source["collection"])
    params = info.config.params
    layout = params.vectors
    if (isinstance(layout, dict) or getattr(layout, "size", None) != source["dimension"]
            or str(getattr(layout, "distance", "")).casefold() != "cosine"
            or getattr(params, "sparse_vectors", None)):
        raise svc.LabError("capture requires one unnamed dense cosine vector matching repository dimension", 409)
    return info.config.model_dump(mode="json")


def _validate_point(point, source, documents, seen):
    payload, vector = point.payload, point.vector
    if not isinstance(payload, dict):
        raise svc.LabError("repository point payload must be an object", 409)
    document = documents.get(payload.get("document_id"))
    index = payload.get("index")
    if document is None or isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < document["chunks"]:
        raise svc.LabError("repository point has unknown document or invalid chunk index", 409)
    identity = (document["id"], index)
    expected_id = vectors.point_id(source["collection"], *identity)
    if str(point.id) != expected_id or identity in seen:
        raise svc.LabError("repository point identity or duplicate chunk coverage is invalid", 409)
    if payload.get("document") != document["name"] or payload.get("kind") != document["kind"] or not isinstance(payload.get("text"), str):
        raise svc.LabError("repository point document metadata or text is invalid", 409)
    if (not isinstance(vector, list) or len(vector) != source["dimension"] or
            any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector)):
        raise svc.LabError("repository point must contain a finite flat dense vector of the declared dimension", 409)
    # Use the consumer's envelope rules, including nested visibility and labels.
    from .retrieval import _evidence_protection
    source_protection(payload)
    _evidence_protection({"metadata": payload})
    seen.add(identity)
    return {"id": str(point.id), "vector": vector, "payload": payload}


def _scan(client, source, check_authority):
    start = datetime.now(timezone.utc).isoformat()
    check_authority()
    configuration = _configuration(client, source)
    documents = {doc["id"]: doc for doc in source["documents"]}
    seen, offsets, rows, offset, byte_count = set(), set(), [], None, 0
    # A page must contain a new point; the hard page ceiling also bounds hostile offsets.
    for _ in range(MAX_POINTS + 1):
        check_authority()
        points, next_offset = client.scroll(collection_name=source["collection"], limit=PAGE_SIZE,
            offset=offset, with_payload=True, with_vectors=True, timeout=10)
        check_authority()
        if len(points) > PAGE_SIZE:
            raise svc.LabError("repository scan returned an oversized page", 413)
        for point in points:
            row = _validate_point(point, source, documents, seen)
            encoded = _canonical(row)
            byte_count += len(encoded) + 1
            if len(encoded) > MAX_POINT_BYTES or byte_count > MAX_BYTES or len(rows) >= MAX_POINTS:
                raise svc.LabError("repository capture evidence limit exceeded (10000 points, 64 MiB, 1 MiB per point)", 413)
            rows.append(row)
        if next_offset is None:
            break
        marker = str(next_offset)
        if not points or marker in offsets:
            raise svc.LabError("repository scan made no progress or repeated an offset", 409)
        offsets.add(marker)
        offset = next_offset
    else:
        raise svc.LabError("repository scan page limit exceeded", 413)
    expected = {(doc["id"], index) for doc in source["documents"] for index in range(doc["chunks"])}
    if seen != expected:
        raise svc.LabError("repository chunk coverage is incomplete; finish ingestion before capture", 409)
    check_authority()
    if _configuration(client, source) != configuration:
        raise svc.LabError("repository collection configuration changed during scan", 409)
    rows.sort(key=lambda row: row["id"])
    digest = hashlib.sha256(b"".join(_canonical(row) + b"\n" for row in rows)).hexdigest()
    return rows, configuration, {"started": start, "finished": datetime.now(timezone.utc).isoformat(),
                                 "points": len(rows), "serialized_bytes": byte_count, "sha256": digest}


def execute_capture(db, project_id, payload, directory: Path, check_authority: Callable):
    check_authority()
    source = _validate_source(db, project_id, payload)
    client = vectors.client()
    rows, configuration, first = _scan(client, source, check_authority)
    _validate_source(db, project_id, payload)
    check_authority()
    second_rows, second_config, second = _scan(client, source, check_authority)
    if first["sha256"] != second["sha256"] or configuration != second_config:
        raise svc.LabError("repository changed between capture scans; retry when ingestion is stable", 409)
    del second_rows
    _validate_source(db, project_id, payload)
    qualification = {"status": "optimistic_observed_stability", "atomic_snapshot": False,
        "admission_time_bytes": False, "dense_vector_export": True, "hnsw_snapshot": False,
        "encoder_weights_pinned": False, "semantic_replay_qualified": False,
        "limitation": "Two matching scans and database fingerprints do not fence concurrent Qdrant writes."}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    corpus, dense = directory / "corpus.jsonl", directory / "repository-vectors.jsonl"
    total_bytes = 0
    with corpus.open("wb") as text_file, dense.open("wb") as vector_file:
        for row in rows:
            check_authority()
            raw = row["payload"]
            identity = {"repository_id": source["id"], "document_id": raw["document_id"],
                        "index": raw["index"], "point_id": row["id"]}
            record_id = (f"repository:{source['id']}:document:{raw['document_id']}:"
                         f"chunk:{raw['index']}:point:{row['id']}")
            record = {"source_record_id": record_id, "source_record_ids": [record_id],
                      "source_identity": identity, "text": raw["text"], "metadata": raw}
            corpus_bytes, vector_bytes = _canonical(record) + b"\n", _canonical(row) + b"\n"
            total_bytes += len(corpus_bytes) + len(vector_bytes)
            if total_bytes > MAX_BYTES:
                raise svc.LabError("repository capture total serialized evidence exceeds 64 MiB", 413)
            text_file.write(corpus_bytes)
            vector_file.write(vector_bytes)
    def publish(path, role, media_type):
        check_authority()
        artifact = svc.store_artifact(db, project_id, path, path.name, media_type, role)
        check_authority()
        verify = directory / ("readback-" + role)
        verify.mkdir()
        retained = svc.materialize(artifact, verify)
        if retained.stat().st_size != artifact.size or hashlib.sha256(retained.read_bytes()).hexdigest() != artifact.sha256:
            raise svc.LabError("persisted repository capture output mismatch", 409)
        check_authority()
        return svc.artifact_dict(artifact)
    corpus_ref = publish(corpus, "corpus", "application/x-ndjson")
    vectors_ref = publish(dense, "repository_vectors", "application/x-ndjson")
    input_ref = {"artifact_id": corpus_ref["id"], "sha256": corpus_ref["sha256"], "role": "corpus", "visibility": "model"}
    manifest_body = {"format": "tinystudio.repository_capture.v1", "source": source,
        "metadata_fingerprint": payload["metadata_fingerprint"], "collection_config": configuration,
        "scan_windows": [first, second], "point_count": len(rows), "qualification": qualification,
        "inputs": [input_ref], "corpus": corpus_ref, "vectors": vectors_ref}
    manifest = directory / "repository-capture-manifest.json"
    manifest_bytes = _canonical(manifest_body)
    if total_bytes + len(manifest_bytes) > MAX_BYTES:
        raise svc.LabError("repository capture total serialized evidence exceeds 64 MiB", 413)
    manifest.write_bytes(manifest_bytes)
    manifest_ref = publish(manifest, "repository_capture_manifest", "application/json")
    # Publication readback must retain the exact linkage, not merely valid JSON.
    row = svc.get_artifact(db, project_id, manifest_ref["id"])
    check_authority()
    retained = svc.materialize(row, directory / "readback-repository_capture_manifest")
    if json.loads(retained.read_bytes()) != manifest_body:
        raise svc.LabError("repository capture manifest linkage mismatch", 409)
    check_authority()
    _validate_source(db, project_id, payload, lock=True)
    check_authority()
    return {"corpus": corpus_ref, "vectors": vectors_ref, "manifest": manifest_ref,
            "inputs": [input_ref], "qualification": qualification}
