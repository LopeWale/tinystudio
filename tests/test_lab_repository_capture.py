"""Owned disposable PostgreSQL and Qdrant fixtures; no embedding downloads.

Run with --noconftest alongside test_lab_operations.py's database fixture.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient, models
from sqlalchemy import select

from test_lab_operations import database  # exclusively owned CREATE/DROP DATABASE fixture
from tinystudio import vectors
from tinystudio.db.models import Document, Project, Repository
from tinystudio.lab import repository_capture as capture, service as svc
from tinystudio.lab.models import LabArtifact
from tinystudio.lab.retrieval import build_index, query_index
from tinystudio.storage import ArtifactStorage


@pytest.fixture
def source(database, tmp_path, monkeypatch):
    client = QdrantClient(url="http://localhost:6333", timeout=10)
    collection = "ts_capture_test_" + uuid.uuid4().hex
    client.create_collection(collection, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    monkeypatch.setattr(vectors, "client", lambda: client)
    monkeypatch.setattr(svc, "storage", lambda: ArtifactStorage(root=tmp_path / "objects"))
    with database.begin() as db:
        project = Project(slug=uuid.uuid4().hex, name="Capture fixture")
        db.add(project)
        db.flush()
        repository = Repository(project_id=project.id, name="Evidence", collection=collection,
                                embedding_model="fixture-two-dimensional", dimension=2)
        db.add(repository)
        db.flush()
        document = Document(repository_id=repository.id, name="fixture.txt", kind="text", chars=36, chunks=2)
        db.add(document)
        db.flush()
        project_id, repository_id, document_id = project.id, repository.id, document.id
    raw = [{"text": "orchid retained evidence", "document_id": document_id, "document": "fixture.txt",
            "kind": "text", "index": 0, "partition": "heldout", "visibility": "evaluation",
            "scientific": {"temperature": 21.5}},
           {"text": "cedar unrelated evidence", "document_id": document_id, "document": "fixture.txt",
            "kind": "text", "index": 1}]
    points = [models.PointStruct(id=vectors.point_id(collection, document_id, i), vector=[[1., 0.], [0., 1.]][i],
                                payload=payload) for i, payload in enumerate(raw)]
    client.upsert(collection, points=points, wait=True)
    try:
        yield database, project_id, repository_id, document_id, client, collection, raw, tmp_path
    finally:
        if client.collection_exists(collection):
            client.delete_collection(collection)
        client.close()


def admit(source, key="capture"):
    factory, project, *_ = source
    with factory.begin() as db:
        return capture.request_capture(db, project, "Evidence", key).payload


def execute(source, payload=None, check=lambda: None):
    factory, project, *_, root = source
    payload = payload or admit(source)
    with factory.begin() as db:
        return capture.execute_capture(db, project, payload, root / uuid.uuid4().hex, check)


def test_retained_corpus_survives_original_deletion_and_preserves_restrictions(source):
    factory, project, repo_id, _, client, collection, raw, root = source
    payload = admit(source)
    result = execute(source, payload)
    assert len(result["inputs"]) == 1 and result["inputs"][0]["role"] == "corpus"
    assert not result["qualification"]["atomic_snapshot"]
    assert not result["qualification"]["semantic_replay_qualified"]
    client.delete_collection(collection)
    with factory.begin() as db:
        db.delete(db.get(Repository, repo_id))
    # Idempotency replays the admitted identity after source deletion.
    assert admit(source) == payload
    with factory.begin() as db:
        artifact = svc.get_artifact(db, project, result["corpus"]["id"])
        path = svc.materialize(artifact, root)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert sorted((row["metadata"] for row in rows), key=lambda row: row["index"]) == raw
        dense = svc.materialize(svc.get_artifact(db, project, result["vectors"]["id"]), root)
        assert len(dense.read_text().splitlines()) == 2
        manifest = json.loads(svc.materialize(svc.get_artifact(db, project, result["manifest"]["id"]), root).read_bytes())
        assert manifest["corpus"] == result["corpus"] and manifest["vectors"] == result["vectors"]
        assert manifest["scan_windows"][0]["sha256"] == manifest["scan_windows"][1]["sha256"]
    # Existing retrieval parser sniffs JSONL despite CAS filename having no suffix.
    with pytest.raises(ValueError, match="explicit recorded research override"):
        build_index([{**result["inputs"][0], "path": str(path)}], root / "denied")
    index = build_index([{**result["inputs"][0], "path": str(path)}], root / "index",
                        research_override={"kind": "retrieval.include_restricted_evidence", "reason": "Owned fixture proof"})
    query = query_index(index["path"], "orchid")
    assert query["trace"]["research_only"]
    hit = query["hits"][0]
    assert hit["content"]["text"] == "orchid retained evidence"
    assert hit["content"]["metadata"]["partition"] == "heldout"
    assert hit["content"]["source_identity"]["repository_id"] == repo_id
    assert hit["content"]["source_record_id"] == (
        f"repository:{repo_id}:document:{raw[0]['document_id']}:chunk:0:"
        f"point:{vectors.point_id(collection, raw[0]['document_id'], 0)}")
    assert hit["provenance"]["protection"] == "evaluation"


def test_cross_project_scope_and_changed_intent(source):
    factory, project, *_ = source
    payload = admit(source)
    with factory.begin() as db:
        other = Project(slug=uuid.uuid4().hex, name="Other")
        db.add(other)
        db.flush()
        with pytest.raises(svc.LabError, match="repository not found"):
            capture.request_capture(db, other.id, "Evidence", "other")
        with pytest.raises(svc.LabError, match="different operation"):
            capture.request_capture(db, project, "Changed", "capture")
    assert payload["source"]["id"] == source[2]


@pytest.mark.parametrize("change", ["partial", "unknown", "bool_index", "unknown_partition", "wrong_name", "wrong_id"])
def test_invalid_point_or_coverage_fails_without_refs(source, change):
    factory, project, _, doc_id, client, collection, raw, _ = source
    payload = admit(source)
    point_id = vectors.point_id(collection, doc_id, 0)
    if change == "partial":
        client.delete(collection, points_selector=models.PointIdsList(points=[point_id]), wait=True)
    else:
        altered = dict(raw[0])
        altered.update({"document_id": "unknown"} if change == "unknown" else
                       {"index": True} if change == "bool_index" else
                       {"partition": "mystery"} if change == "unknown_partition" else
                       {"document": "incorrect"} if change == "wrong_name" else {})
        if change == "wrong_id":
            client.delete(collection, points_selector=models.PointIdsList(points=[point_id]), wait=True)
            point_id = str(uuid.uuid4())
        client.upsert(collection, points=[models.PointStruct(id=point_id, vector=[1., 0.], payload=altered)], wait=True)
    with pytest.raises((svc.LabError, ValueError)):
        execute(source, payload)
    with factory() as db:
        assert not list(db.scalars(select(LabArtifact).where(LabArtifact.project_id == project)))


def test_metadata_and_partial_ingestion_rejected(source):
    factory, project, _, doc_id, *_ = source
    payload = admit(source)
    with factory.begin() as db:
        db.get(Document, doc_id).chars += 1
    with pytest.raises(svc.LabError, match="metadata changed"):
        execute(source, payload)
    with factory.begin() as db:
        db.get(Document, doc_id).chunks = 0
    with pytest.raises(svc.LabError, match="incomplete documents"):
        admit(source, "new")


def test_mutation_between_scans_detected(source, monkeypatch):
    _, _, _, doc_id, client, collection, raw, _ = source
    original = client.scroll
    calls = 0
    def mutate(**kwargs):
        nonlocal calls
        points, offset = original(**kwargs)
        calls += 1
        if calls == 1:
            client.set_payload(collection, payload={"text": "mutated after first observation"},
                               points=[vectors.point_id(collection, doc_id, 0)], wait=True)
        return points, offset
    monkeypatch.setattr(client, "scroll", mutate)
    with pytest.raises(svc.LabError, match="changed between"):
        execute(source)


def test_bound_and_repeated_offset_are_explicit(source, monkeypatch):
    client = source[4]
    original = client.scroll
    captured, _ = original(collection_name=source[5], with_vectors=True, with_payload=True)
    def repeated(**kwargs):
        return captured, captured[0].id
    monkeypatch.setattr(client, "scroll", repeated)
    with pytest.raises(svc.LabError, match="duplicate|offset"):
        execute(source)
    monkeypatch.setattr(client, "scroll", original)
    monkeypatch.setattr(capture, "MAX_BYTES", 10)
    with pytest.raises(svc.LabError) as failure:
        execute(source)
    assert failure.value.status == 413


@pytest.mark.parametrize("boundary", ["scan", "storage", "readback"])
def test_authority_loss_rolls_back_all_refs(source, monkeypatch, boundary):
    factory, project, *_ = source
    cancelled = False
    def check():
        if cancelled:
            raise svc.LabError("cancelled fixture", 409)
    owner, method = (source[4], "scroll") if boundary == "scan" else (svc, "store_artifact" if boundary == "storage" else "materialize")
    original = getattr(owner, method)
    def revoke(*args, **kwargs):
        nonlocal cancelled
        result = original(*args, **kwargs)
        cancelled = True
        return result
    monkeypatch.setattr(owner, method, revoke)
    with pytest.raises(svc.LabError, match="cancelled fixture"):
        execute(source, check=check)
    with factory() as db:
        assert not list(db.scalars(select(LabArtifact).where(LabArtifact.project_id == project)))


def test_corrupt_output_readback_rejected(source, monkeypatch):
    original = svc.materialize
    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        result.write_bytes(b"corrupt")
        return result
    monkeypatch.setattr(svc, "materialize", corrupt)
    with pytest.raises(svc.LabError, match="output mismatch"):
        execute(source)


@pytest.mark.parametrize("vector", [[float("nan"), 0.], [True, 0.], [[1.], [0.]], [1.], {"named": [1., 0.]}])
def test_invalid_dense_shape_rejected_at_raw_boundary(source, monkeypatch, vector):
    client = source[4]
    original = client.scroll
    def invalid(**kwargs):
        points, offset = original(**kwargs)
        first = points[0]
        return [SimpleNamespace(id=first.id, payload=first.payload, vector=vector), *points[1:]], offset
    monkeypatch.setattr(client, "scroll", invalid)
    with pytest.raises(svc.LabError, match="finite flat dense vector"):
        execute(source)


def test_real_multiple_pages_export_every_point_once(source, monkeypatch):
    factory, _, _, doc_id, client, collection, raw, _ = source
    with factory.begin() as db:
        db.get(Document, doc_id).chunks = 102
    points = [models.PointStruct(id=vectors.point_id(collection, doc_id, index), vector=[1., 0.],
               payload={**raw[1], "index": index, "text": f"page evidence {index}"}) for index in range(2, 102)]
    client.upsert(collection, points=points, wait=True)
    original, calls = client.scroll, []
    def observed(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(client, "scroll", observed)
    result = execute(source)
    assert len(calls) == 4
    assert all(call["limit"] == 100 and call["with_vectors"] is True and call["timeout"] <= 10 for call in calls)
    with factory() as db:
        path = svc.materialize(svc.get_artifact(db, source[1], result["vectors"]["id"]), source[-1])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 102 and {row["payload"]["index"] for row in rows} == set(range(102))


def test_duplicate_chunk_coverage_is_rejected(source, monkeypatch):
    client = source[4]
    original = client.scroll
    def duplicate(**kwargs):
        points, offset = original(**kwargs)
        return [points[0], points[0]], offset
    monkeypatch.setattr(client, "scroll", duplicate)
    with pytest.raises(svc.LabError, match="duplicate chunk coverage"):
        execute(source)


def test_layout_change_rejected(source, monkeypatch):
    client = source[4]
    original = client.get_collection
    def named(*args, **kwargs):
        result = original(*args, **kwargs)
        result.config.params.vectors = {"unexpected": models.VectorParams(size=2, distance=models.Distance.COSINE)}
        return result
    monkeypatch.setattr(client, "get_collection", named)
    with pytest.raises(svc.LabError, match="unnamed dense cosine"):
        execute(source)


def test_admitted_identity_does_not_follow_recreated_name(source):
    factory, project, repository_id, *_ = source
    payload = admit(source)
    with factory.begin() as db:
        db.delete(db.get(Repository, repository_id))
        db.flush()
        replacement = Repository(project_id=project, name="Evidence", collection="different_collection",
                                 embedding_model="different", dimension=2)
        db.add(replacement)
    assert admit(source) == payload
    with pytest.raises(svc.LabError, match="repository not found"):
        execute(source, payload)


def test_record_ids_are_unique_stable_strings_across_independent_captures(source):
    factory, project, repository_id, document_id, _, collection, raw, root = source
    first = execute(source, admit(source, "first-independent-capture"))
    second = execute(source, admit(source, "second-independent-capture"))
    captures = []
    with factory() as db:
        for result in (first, second):
            path = svc.materialize(svc.get_artifact(db, project, result["corpus"]["id"]), root)
            captures.append(sorted((json.loads(line) for line in path.read_text().splitlines()),
                                   key=lambda row: row["source_identity"]["index"]))
    assert captures[0] == captures[1]
    assert first["corpus"]["sha256"] == second["corpus"]["sha256"]
    expected_ids = []
    for index in range(2):
        point_id = vectors.point_id(collection, document_id, index)
        expected_id = f"repository:{repository_id}:document:{document_id}:chunk:{index}:point:{point_id}"
        expected_ids.append(expected_id)
        row = captures[0][index]
        assert isinstance(row["source_record_id"], str)
        assert row["source_record_id"] == expected_id
        assert row["source_record_ids"] == [expected_id]
        assert row["source_identity"] == {"repository_id": repository_id, "document_id": document_id,
                                           "index": index, "point_id": point_id}
        assert row["metadata"] == raw[index]
    assert len(set(expected_ids)) == 2
    assert len({str(row["source_record_id"]) for row in captures[0]}) == 2
