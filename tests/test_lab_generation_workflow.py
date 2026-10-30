"""Authenticated generation/retrieval composition on real owned native attempts."""
import hashlib
import json
from pathlib import Path

import pytest
import torch
from fastapi.testclient import TestClient

from tests.test_lab_b_workflow import database, integrated
from tests.test_lab_compatibility import local_pretrained_spec
from tests.test_lab_model_session import scratch
from tests.test_lab_retained_retrieval_workflow import _run
from tinystudio.lab.runner import Runner


@pytest.mark.parametrize("mode", ["lexical", "structured", "episode", "semantic", "hybrid"])
def test_real_retained_generation_uses_frozen_index_and_matched_cases(integrated, local_pretrained_spec, mode):
    client, prefix, other_prefix, app, _, root, _ = integrated
    target = client.post(prefix + "/targets", json={"name": "generation-native"}).json()
    def upload(path, role, visibility="model"):
        result = client.post(prefix + "/inputs", files={"file": (path.name, path.read_bytes(), "application/octet-stream")})
        assert result.status_code == 201, result.text
        return {"artifact_id": result.json()["id"], "sha256": result.json()["sha256"], "role": role, "visibility": visibility}
    model = scratch(root, context=256)
    state = torch.load(model, weights_only=True)
    state["model"]["head.bias"].zero_(); state["model"]["head.bias"][ord("1")] = 1
    torch.save(state, model)
    model_ref = upload(model, "model")
    cases = root / "generation-cases.json"
    cases.write_text(json.dumps({"format": "tinystudio.evaluation-cases.v1", "cases": [
        {"source_id": "test-one", "episode_id": "held-out-episode", "partition": "test", "prompt": "pressure?",
         "query": "pressure", "expected": "-9000", "unit": "Pa"},
        {"source_id": "test-two", "episode_id": "held-out-episode", "partition": "test", "prompt": "sensor?",
         "query": "sensor", "expected": "1", "unit": "Pa"}]}))
    cases_ref = upload(cases, "cases", "evaluation")
    embeddings = []
    if mode in {"semantic", "hybrid"}:
        embeddings = [upload(Path(local_pretrained_spec["input_paths"]["model-1"]["path"]), "embedding_model")]
    execution = {"profile": "trusted_native", "backend": "native", "max_seconds": 60}
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        build = {"name": "Independent reference", "engine": "retrieval.index", "inputs": embeddings,
            "parameters": {"mode": mode, "queries": ["pressure"], "records": [{"text": "pressure reference", "source_record_id": "train-one"},
                {"text": "sensor reference", "source_record_id": "train-two"}]}, "execution": execution}
        _, artifacts, original = _run(client, prefix, runner, target, build, "build-" + mode)
        retained = artifacts["retrieval_index"]
        definition = {"name": "Matched generation", "engine": "evaluation.compare", "execution": execution,
            "parameters": {"operation": "generate", "compare_retrieval": True, "max_new_tokens": 1},
            "retrieval": {"top_k": 1, "fields": ["text"]}, "inputs": [model_ref, cases_ref, *embeddings,
                {"artifact_id": retained["id"], "sha256": retained["sha256"], "role": "retrieval_index", "visibility": "model"}]}
        checked = client.post(prefix + "/preflight", json={"definition": definition, "target_id": target["id"]})
        assert checked.status_code == 200 and checked.json()["compatible"], checked.text
        saved = client.post(prefix + "/definitions", json={"definition": definition})
        assert saved.status_code == 201, saved.text
        admitted = client.post(prefix + "/runs", json={"definition_id": saved.json()["id"], "revision": 1,
            "target_id": target["id"], "idempotency_key": "generate-" + mode})
        assert admitted.status_code == 202, admitted.text
        assert runner.run_once(run_id=admitted.json()["id"])
        run = client.get(prefix + "/runs/" + admitted.json()["id"]).json()
        assert run["state"] == "succeeded", run.get("error")
        output = {row["role"]: row for row in run["result"]["artifacts"]}
        retained_bytes = client.get(prefix + "/artifacts/" + output["retrieval_index"]["id"]).content
        assert retained_bytes == original["retrieval_index"]
        report_response = client.get(prefix + "/artifacts/" + output["independent_evaluation"]["id"])
        assert hashlib.sha256(report_response.content).hexdigest() == output["independent_evaluation"]["sha256"]
        report = report_response.json()
        assert len(report["records"]) == 4
        assert all(row["output"]["token_ids"] == [ord("1")] for row in report["records"])
        for row in report["records"]:
            assert "-9000" not in row["output"]["prompt"]
            assert row["model"]["sha256"] == model_ref["sha256"]
            assert bool(row["retrieval"]["included_ids"]) is (row["arm"] == "retrieval")
            assert row["output"]["input_token_ids"] == list(row["output"]["prompt"].encode())
        assert report["comparison"]["passed"]
        assert report["comparison"]["metrics"]["aggregate"]["candidate_mae"] == "4500.5"
        assert not run["result"]["qualification"]["evaluation_passed"]  # Wrong predictions cannot masquerade as success.
        assert client.get(other_prefix + "/artifacts/" + output["independent_evaluation"]["id"]).status_code == 404
