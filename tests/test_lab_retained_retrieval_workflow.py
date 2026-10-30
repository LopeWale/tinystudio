"""Build and reuse frozen indexes through authenticated routes and native children.

Disposable local PostgreSQL/Redis only; run with --noconftest.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_lab_b_workflow import database, integrated
from tests.test_lab_compatibility import local_pretrained_spec
from tinystudio.lab.runner import Runner


def _run(client, prefix, runner, target, definition, key):
    checked = client.post(prefix + "/preflight", json={"definition": definition, "target_id": target["id"]})
    assert checked.status_code == 200 and checked.json()["compatible"], checked.text
    saved = client.post(prefix + "/definitions", json={"definition": definition})
    assert saved.status_code == 201, saved.text
    submitted = client.post(prefix + "/runs", json={"definition_id": saved.json()["id"],
        "revision": saved.json()["revision"], "target_id": target["id"], "idempotency_key": key})
    assert submitted.status_code == 202, submitted.text
    assert runner.run_once(run_id=submitted.json()["id"])
    run = client.get(prefix + "/runs/" + submitted.json()["id"]).json()
    assert run["state"] == "succeeded", run.get("error")
    artifacts = {row["role"]: row for row in run["result"]["artifacts"]}
    content = {}
    for role in ("retrieval_index", "retrieval_trace"):
        response = client.get(prefix + "/artifacts/" + artifacts[role]["id"])
        assert response.status_code == 200, response.text
        assert hashlib.sha256(response.content).hexdigest() == artifacts[role]["sha256"]
        content[role] = response.content
    return run, artifacts, content


@pytest.mark.parametrize("mode", ["lexical", "structured", "episode", "semantic", "hybrid"])
def test_retained_index_queries_publish_identical_bytes_and_original_evidence(integrated, local_pretrained_spec, mode):
    client, prefix, other_prefix, app, connection, root, factory = integrated
    target_response = client.post(prefix + "/targets", json={"name": "retrieval-native"})
    assert target_response.status_code == 201, target_response.text
    target = target_response.json()
    model_inputs = []
    if mode in {"semantic", "hybrid"}:
        model = Path(local_pretrained_spec["input_paths"]["model-1"]["path"])
        uploaded = client.post(prefix + "/inputs", files={"file": ("embedding.tar", model.read_bytes(), "application/x-tar")})
        assert uploaded.status_code == 201, uploaded.text
        model_inputs = [{"artifact_id": uploaded.json()["id"], "sha256": uploaded.json()["sha256"],
                         "role": "embedding_model", "visibility": "model"}]
    build = {"name": "Frozen corpus fixture", "engine": "retrieval.index", "inputs": model_inputs,
        "parameters": {"operation": "build", "mode": mode, "queries": ["pressure normal"], "top_k": 2,
            "records": [{"text": "pressure normal", "unit": "A", "source_record_id": "pressure-record"},
                        {"text": "sensor ready", "unit": "B", "source_record_id": "sensor-record"}]},
        "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 60}}
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        original, original_artifacts, original_bytes = _run(client, prefix, runner, target, build, "build-" + mode)
        index = original_artifacts["retrieval_index"]
        original_payload = json.loads(original_bytes["retrieval_index"])
        expected_sensor = next(row for row in original_payload["documents"] if row["content"]["source_record_id"] == "sensor-record")
        edited = deepcopy(build)
        edited["parameters"]["records"] = [{"text": "replacement corpus"}]
        changed = client.put(prefix + "/definitions/" + original["definition_id"], json={"definition": edited, "expected_revision": 1})
        assert changed.status_code == 200, changed.text

        query = {"name": "Retained query fixture", "engine": "retrieval.index",
            "inputs": [*model_inputs, {"artifact_id": index["id"], "sha256": index["sha256"], "role": "retrieval_index", "visibility": "model"}],
            "parameters": {"operation": "query", "queries": ["sensor ready", "missing words"], "top_k": 1, "filters": {"unit": "B"}},
            "execution": build["execution"]}
        reused, _, reused_bytes = _run(client, prefix, runner, target, query, "query-" + mode)
        assert reused_bytes["retrieval_index"] == original_bytes["retrieval_index"]
        assert reused["result"]["metrics"]["corpus_sha256"] == original["result"]["metrics"]["corpus_sha256"]
        trace = json.loads(reused_bytes["retrieval_trace"])[0]
        assert trace["mode"] == mode
        assert trace["hits"][0]["id"] == expected_sensor["id"]
        assert trace["hits"][0]["content"] == expected_sensor["content"]
        assert trace["trace"]["origin_index"] == {"artifact_id": index["id"], "sha256": index["sha256"]}
        assert trace["trace"]["operation"] == "query"
        assert reused["result"]["qualification"]["generation"] is False
        assert client.get(other_prefix + "/artifacts/" + index["id"]).status_code == 404
