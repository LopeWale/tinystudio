"""C qualification through authenticated registered routes and actual native child.

These are disposable software fixtures, not user research results. No model,
training experiment, live acquisition, or public server is started.
Run --noconftest; imported fixtures own their PostgreSQL database and Redis.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json

from fastapi.testclient import TestClient
from rq import Queue, SimpleWorker

from tests.test_lab_b_workflow import database, integrated
from tinystudio.lab import dispatch
from tinystudio.lab.runner import Runner


def _recording():
    rows = []
    for episode in range(6):
        partition = "train" if episode < 3 else "validation" if episode == 3 else "test"
        for index, value in enumerate((-1.25, -1.0, None, -.5, -.25)):
            time = episode * 10 + index
            rows.append({"source_record_id": f"episode:{episode}:sample:{index}",
                "episode_id": f"episode:{episode}", "scenario_id": f"scenario:{episode}",
                "partition": partition, "time": time, "available_at": time,
                "clock_domain": "fixture-clock", "clock_units": "s",
                "clock_origin": "software-fixture-start", "clock_session": "software-fixture-session",
                "value": value, "units": "V", "wire_hex": "0080ff",
                "protocol": "software-fixture", "decoded_protocol": {"sample": index, "episode": episode}})
    return rows


def _codecs():
    return ["numeric_text", "raw_bytes", "decoded_protocol", "hybrid",
            {"kind": "engineering_state", "bins": {"boundaries": [-1., -.5],
             "labels": ["low", "middle", "high"], "units": "V"}}]


def _run(client, prefix, runner, target, body, key, *, succeeds=True):
    saved = client.post(prefix + "/definitions", json={"definition": body})
    assert saved.status_code == 201, saved.text
    submitted = client.post(prefix + "/runs", json={"definition_id": saved.json()["id"], "revision": 1,
        "target_id": target["id"], "idempotency_key": key})
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["id"]
    assert runner.run_once(run_id=run_id)
    response = client.get(prefix + "/runs/" + run_id)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["state"] == ("succeeded" if succeeds else "failed"), run.get("error")
    return run


def _records(client, prefix, run, name):
    response = client.get(prefix + f"/runs/{run['id']}/records", params={"artifact": name})
    assert response.status_code == 200, response.text
    assert response.json()["next_offset"] is None
    return response.json()["records"]


def test_prepare_review_annotate_promote_and_explicit_training_job(integrated):
    client, prefix, other_prefix, app, connection, root, _ = integrated
    legacy = prefix.removesuffix("/lab")
    target_response = client.post(prefix + "/targets", json={"name": "c-native-child"})
    assert target_response.status_code == 201, target_response.text
    target = target_response.json()
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        rows = _recording()
        uploaded = client.post(prefix + "/inputs", files={"file": ("c-recording.jsonl",
            "".join(json.dumps(row) + "\n" for row in rows).encode(), "application/x-ndjson")})
        assert uploaded.status_code == 201, uploaded.text
        artifact = uploaded.json()
        body = {"name": "C prepare explicit recording", "engine": "data.pipeline", "seed": 42,
            "inputs": [{"artifact_id": artifact["id"], "sha256": artifact["sha256"], "role": "source", "visibility": "model"}],
            "pipeline": [{"kind": "impute", "parameters": {"method": "forward_fill"}}],
            "parameters": {"partition_policy": {"strategy": "grouped", "group_by": ["episode_id"]},
                           "representations": _codecs()},
            "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 60}}
        prepared = _run(client, prefix, runner, target, body, "c-prepared")
        assert prepared["result"]["execution_receipt"]["profile_manifest"]["backend"] == "native"
        raw = _records(client, prefix, prepared, "raw-normalized.jsonl")
        transformed = _records(client, prefix, prepared, "transformed.jsonl")
        assert len(raw) == len(transformed) == 30
        for episode in range(6):
            raw_gap = next(row for row in raw if row["source_record_id"] == f"episode:{episode}:sample:2")
            filled = next(row for row in transformed if row["source_record_id"] == f"episode:{episode}:sample:2")
            assert raw_gap["value"] is None and raw_gap["observed"] is False
            assert filled["value"] == -1.0 and filled["units"] == "V"
            assert filled["estimated"] is True and filled["observed"] is False
            assert filled["source_missing_mask"] == {"value": True}
            assert filled["future_aware"] is False
        coverage = client.get(prefix + f"/runs/{prepared['id']}/coverage")
        assert coverage.status_code == 200, coverage.text
        assert coverage.json()["complete"]
        assert coverage.json()["counts"]["records"] == 30
        assert coverage.json()["counts"]["estimated"] == 6
        assert coverage.json()["partition_counts"] == {"train": 15, "validation": 5, "test": 10}
        transformed_artifact = next(item for item in prepared["result"]["artifacts"] if item["role"] == "transformed_dataset")
        selected_index = next(index for index, row in enumerate(transformed) if row["source_record_id"] == "episode:0:sample:2")
        matched = client.get(prefix + f"/runs/{prepared['id']}/representations",
            params={"artifact_id": transformed_artifact["id"], "row": selected_index})
        assert matched.status_code == 200, matched.text
        inspected = matched.json()
        assert inspected["identity"]["artifact_sha256"] == transformed_artifact["sha256"]
        assert inspected["identity"]["row_index"] == selected_index
        assert inspected["identity"]["observation_sha256"] == transformed[selected_index]["observation_sha256"]
        assert inspected["status"] == "matched" and len(inspected["matches"]) == 5
        assert client.get(other_prefix + f"/runs/{prepared['id']}/coverage").status_code == 404
        for match in inspected["matches"]:
            record = match["record"]
            actual = base64.b64decode(record["model_input_base64"], validate=True)
            assert record["input_sha256"] == hashlib.sha256(actual).hexdigest()
            assert record["token_ids"] == list(actual)
            assert record["token_count"] == len(actual)
            assert not record["loss"]["truncated"]
            assert record["loss"]["numeric_quantized"] == (record["representation_kind"] == "engineering_state")
            if record["representation_kind"] == "raw_bytes":
                assert actual == b"\x00\x80\xff" and record["model_input_text"] is None
            if record["representation_kind"] == "numeric_text":
                assert json.loads(actual)["value"] == -1.0
        # Authored fixture responses are bound to the exact reviewed observations.
        annotations = [{"record_id": row["record_id"], "observation_sha256": row["observation_sha256"],
            "messages": [{"role": "user", "content": f"Describe disposable fixture {row['record_id']}"},
                         {"role": "assistant", "content": f"Recorded or causally filled voltage: {row['value']} V"}],
            "label_source": {"kind": "human", "actor": "qualification fixture author",
                             "reference": "tests/test_lab_c_workflow.py"}} for row in transformed]
        chat_body = deepcopy(body)
        chat_body["name"] = "C explicitly reviewed supervision"
        chat_body["parameters"].update(representation="chat", annotations=annotations)
        supervised = _run(client, prefix, runner, target, chat_body, "c-authored-chat")
        chats = _records(client, prefix, supervised, "chat-dataset.jsonl")
        assert len(chats) == 30 and all(row["message_origin"] == "explicit_annotation" for row in chats)
        assert {row["source_record_id"] for row in chats} == {row["source_record_id"] for row in transformed}
        stale = deepcopy(chat_body)
        stale["name"] = "C stale annotation must fail"
        stale["parameters"]["annotations"][0]["observation_sha256"] = "0" * 64
        rejected = _run(client, prefix, runner, target, stale, "c-stale-annotation", succeeds=False)
        assert "checksum changed" in str(rejected.get("error"))
    supervised_artifact = next(item for item in supervised["result"]["artifacts"] if item["role"] == "supervised_dataset")
    assert client.get(legacy + "/jobs").json() == []
    promotion = client.post(prefix + "/datasets/from-run", json={"run_id": supervised["id"],
        "artifact_id": supervised_artifact["id"], "name": "c-prepared-dataset", "snapshot_name": "c-prepared-snapshot",
        "idempotency_key": "c-promote", "min_training": 10})
    assert promotion.status_code == 202, promotion.text
    assert dispatch.tick()["operations_dispatched"] == 1
    delivery = Queue("tinystudio", connection=connection)
    SimpleWorker([delivery], connection=connection).work(burst=True)
    completed = client.get(prefix + "/operations/" + promotion.json()["id"]).json()
    assert completed["state"] == "succeeded", completed.get("error")
    assert completed["result"]["excluded_final_test_rows"] == 10
    snapshot = completed["result"]["snapshot"]
    assert snapshot["training_count"] == 15 and snapshot["validation_count"] == 5
    datasets = client.get(legacy + "/datasets").json()
    dataset = next(item for item in datasets if item["name"] == "c-prepared-dataset")
    assert dataset["datapoints"] == 20
    assert dataset["buckets"]["training"] == 15 and dataset["buckets"]["validation"] == 5
    assert dataset["buckets"]["uncategorized"] == 0
    snapshots = client.get(legacy + "/snapshots").json()
    assert any(item["name"] == "c-prepared-snapshot" and item["training_count"] == 15 and item["validation_count"] == 5 for item in snapshots)
    assert client.get(legacy + "/jobs").json() == []
    admitted = client.post(legacy + "/jobs", json={"name": "c-explicit-training-handoff", "snapshot": "c-prepared-snapshot",
        "goal": "Software fixture qualification only; no training experiment authorized"})
    assert admitted.status_code == 200, admitted.text
    assert admitted.json()["snapshot"] == "c-prepared-snapshot" and admitted.json()["experiments"] == []
    assert delivery.count == 0


def test_http_repository_capture_rq_then_native_retrieval_after_source_deletion(integrated, monkeypatch):
    import uuid
    from qdrant_client import QdrantClient, models
    from sqlalchemy import select
    from tinystudio import vectors
    from tinystudio.db.models import Document, Project, Repository

    client, prefix, _, app, connection, _, factory = integrated
    collection = "ts_c_workflow_" + uuid.uuid4().hex
    qdrant = QdrantClient(url="http://localhost:6333", timeout=10)
    qdrant.create_collection(collection, vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    monkeypatch.setattr(vectors, "client", lambda: qdrant)
    try:
        with factory.begin() as db:
            project = db.scalar(select(Project).where(Project.slug == prefix.split("/")[3]))
            repository = Repository(project_id=project.id, name="c-owned-repository", collection=collection,
                                    embedding_model="fixture-known-2d", dimension=2)
            db.add(repository)
            db.flush()
            document = Document(repository_id=repository.id, name="owned.txt", kind="text", chunks=2, chars=50)
            db.add(document)
            db.flush()
            repository_id, document_id = repository.id, document.id
        qdrant.upsert(collection, points=[models.PointStruct(id=vectors.point_id(collection, document_id, index),
            vector=[[1., 0.], [0., 1.]][index], payload={"document_id": document_id, "document": "owned.txt",
                "kind": "text", "index": index, "text": text})
            for index, text in enumerate(("orchid retained immutable evidence", "cedar unrelated observation"))], wait=True)
        admitted = client.post(prefix + "/inputs/from-repository", json={"repository": "c-owned-repository",
                                                                 "idempotency_key": "c-repository-capture"})
        assert admitted.status_code == 202, admitted.text
        assert dispatch.tick()["operations_dispatched"] == 1
        SimpleWorker([Queue("tinystudio", connection=connection)], connection=connection).work(burst=True)
        operation = client.get(prefix + "/operations/" + admitted.json()["id"]).json()
        assert operation["state"] == "succeeded", operation.get("error")
        captured = operation["result"]
        assert len(captured["inputs"]) == 1 and captured["inputs"][0]["role"] == "corpus"
        assert captured["qualification"]["semantic_replay_qualified"] is False
        qdrant.delete_collection(collection)
        with factory.begin() as db:
            db.delete(db.get(Repository, repository_id))
        replay = client.post(prefix + "/inputs/from-repository", json={"repository": "c-owned-repository",
                                                                      "idempotency_key": "c-repository-capture"})
        assert replay.json()["id"] == operation["id"]
        target = client.post(prefix + "/targets", json={"name": "c-retained-retrieval"}).json()
        with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
            runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
            runner.advertise()
            run = _run(client, prefix, runner, target, {"name": "C retained lexical corpus consumer",
                "engine": "retrieval.index", "inputs": captured["inputs"],
                "parameters": {"mode": "lexical", "queries": ["orchid"]},
                "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 60}}, "c-retained-query")
        trace = next(item for item in run["result"]["artifacts"] if item["role"] == "retrieval_trace")
        downloaded = client.get(prefix + "/artifacts/" + trace["id"])
        assert downloaded.status_code == 200, downloaded.text
        query = downloaded.json()[0]
        hit = query["hits"][0]["content"]
        assert hit["text"] == "orchid retained immutable evidence"
        assert hit["source_record_id"] == (
            f"repository:{repository_id}:document:{document_id}:chunk:0:point:{vectors.point_id(collection, document_id, 0)}")
        assert hit["source_identity"]["repository_id"] == repository_id
        assert query["trace"]["research_only"] is False
    finally:
        if qdrant.collection_exists(collection):
            qdrant.delete_collection(collection)
        qdrant.close()
