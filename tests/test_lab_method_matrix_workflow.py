"""Matched methods through authenticated admission, real runners and retained downloads."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import torch
from fastapi.testclient import TestClient

from tests.test_lab_b_workflow import database, integrated
from tests.test_lab_compatibility import local_pretrained_spec
from tinystudio.lab.runner import Runner

EXECUTION = {"profile": "trusted_native", "backend": "native", "max_seconds": 120}


def upload(client, prefix, path, role, visibility="model"):
    response = client.post(prefix + "/inputs", files={"file": (path.name, path.read_bytes(), "application/octet-stream")})
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    return binding(item, role, visibility)


def binding(item, role, visibility="model"):
    return {"artifact_id": item["id"], "sha256": item["sha256"], "role": role, "visibility": visibility}


def submit(client, prefix, target, definition):
    preflight = client.post(prefix + "/preflight", json={"definition": definition, "target_id": target["id"]})
    assert preflight.status_code == 200 and preflight.json()["compatible"], preflight.text
    saved = client.post(prefix + "/definitions", json={"definition": definition})
    assert saved.status_code == 201, saved.text
    admitted = client.post(prefix + "/runs", json={"definition_id": saved.json()["id"], "revision": 1,
        "target_id": target["id"], "idempotency_key": definition["name"]})
    assert admitted.status_code == 202, admitted.text
    return admitted.json()["id"], saved.json()["id"]


def execute(client, prefix, runner, target, definition):
    run_id, _ = submit(client, prefix, target, definition)
    assert runner.run_once(run_id=run_id)
    run = client.get(prefix + "/runs/" + run_id).json()
    assert run["state"] == "succeeded", run.get("error")
    return run


def cases(root):
    path = root / "matrix-cases.json"
    path.write_text(json.dumps({"format": "tinystudio.evaluation-cases.v1", "cases": [
        {"source_id": "first", "episode_id": "heldout", "partition": "test", "prompt": "pressure", "query": "pressure",
         "features": {"reading": "1.25"}, "expected": "-2.25", "unit": "Pa"},
        {"source_id": "second", "episode_id": "heldout", "partition": "test", "prompt": "sensor", "query": "sensor",
         "features": {"reading": "-1.5"}, "expected": "3.25", "unit": "Pa"}]}))
    return path


def conventional(id_="conventional", intercept="0.25"):
    return {"id": id_, "label": id_, "kind": "affine", "coefficients": {"reading": "-2"}, "intercept": intercept}


def test_real_base_full_lora_rag_and_conventional_share_frozen_cases(integrated, local_pretrained_spec):
    from transformers import AutoModelForCausalLM
    from tinystudio.lab.compatibility import extract_model_archive, load_pretrained_output
    client, prefix, other_prefix, app, _, root, _ = integrated
    target = client.post(prefix + "/targets", json={"name": "method-matrix"}).json()
    source = Path(local_pretrained_spec["input_paths"]["model-1"]["path"])
    base = upload(client, prefix, source, "model")
    snapshot = upload(client, prefix, Path(local_pretrained_spec["input_paths"]["snapshot-1"]), "snapshot")
    case_ref = upload(client, prefix, cases(root), "cases", "evaluation")
    trained = {}
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        for strategy in ("full", "lora"):
            definition = deepcopy(local_pretrained_spec["definition"])
            definition.update(name="matrix training " + strategy, engine="learning.pretrained", execution=EXECUTION)
            definition["learning"].update(update_strategy=strategy, device="cpu", dtype="float32")
            definition["inputs"] = [{**base, "role": "pretrained_model"}, snapshot]
            training = execute(client, prefix, runner, target, definition)
            artifact = next(row for row in training["result"]["artifacts"] if row["role"] == "pretrained_model_bundle")
            trained[strategy] = binding(artifact, "model")
            content = client.get(prefix + "/artifacts/" + artifact["id"]).content
            assert hashlib.sha256(content).hexdigest() == artifact["sha256"]
            archive = root / (strategy + ".tar")
            archive.write_bytes(content)
            restored_dir = extract_model_archive(archive, root / strategy)
            model, tokenizer, manifest = load_pretrained_output(restored_dir)
            assert manifest["update_strategy"] == strategy
            probe = tokenizer("pressure high", return_tensors="pt")
            with torch.no_grad():
                actual = model(**probe).logits.clone()
            del model
            base_model = AutoModelForCausalLM.from_pretrained(local_pretrained_spec["_fixture_model_dir"], local_files_only=True).eval()
            with torch.no_grad():
                assert not torch.equal(actual, base_model(**probe).logits)
            del base_model
            reloaded, _, _ = load_pretrained_output(restored_dir)
            with torch.no_grad():
                assert torch.equal(actual, reloaded(**probe).logits)
            del reloaded
        index_run = execute(client, prefix, runner, target, {"name": "matrix reference", "engine": "retrieval.index",
            "execution": EXECUTION, "parameters": {"mode": "lexical", "queries": ["pressure"], "records": [
                {"text": "pressure reference", "source_record_id": "training-reference-1"},
                {"text": "sensor reference", "source_record_id": "training-reference-2"}]}})
        index = next(row for row in index_run["result"]["artifacts"] if row["role"] == "retrieval_index")
        methods = [conventional(),
            {"id": "base", "label": "Base", "kind": "model", "artifact_id": base["artifact_id"], "retrieval": False},
            {"id": "specialized", "label": "Full fine-tuning", "kind": "model", "artifact_id": trained["full"]["artifact_id"], "retrieval": False},
            {"id": "lora", "label": "LoRA", "kind": "model", "artifact_id": trained["lora"]["artifact_id"], "retrieval": False},
            {"id": "rag", "label": "Base with retrieval", "kind": "model", "artifact_id": base["artifact_id"], "retrieval": True},
            {"id": "combined", "label": "LoRA with retrieval", "kind": "model", "artifact_id": trained["lora"]["artifact_id"], "retrieval": True}]
        definition = {"name": "matched methods", "engine": "evaluation.compare", "execution": EXECUTION,
            "parameters": {"operation": "matrix", "methods": methods, "baseline_method": "conventional", "max_new_tokens": 1, "device": "cpu"},
            "retrieval": {"top_k": 1, "fields": ["text"]},
            "inputs": [base, trained["full"], trained["lora"], case_ref, binding(index, "retrieval_index")]}
        run_id, definition_id = submit(client, prefix, target, definition)
        changed = deepcopy(definition)
        changed["parameters"]["methods"][0]["intercept"] = "999"
        assert client.put(prefix + "/definitions/" + definition_id,
                          json={"definition": changed, "expected_revision": 1}).status_code == 200
        assert runner.run_once(run_id=run_id)
        run = client.get(prefix + "/runs/" + run_id).json()
    assert run["state"] == "succeeded", run.get("error")
    assert run["spec"]["definition"]["parameters"]["methods"] == methods
    assert client.get(other_prefix + "/runs/" + run_id).status_code == 404
    artifacts = run["result"]["artifacts"]
    aggregates = [row for row in artifacts if row["role"] == "independent_evaluation"]
    assert len(aggregates) == 1
    assert artifacts[-1]["id"] == aggregates[0]["id"]
    for artifact in artifacts:
        response = client.get(prefix + "/artifacts/" + artifact["id"])
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
        assert client.get(other_prefix + "/artifacts/" + artifact["id"]).status_code == 404
    report = client.get(prefix + "/artifacts/" + aggregates[0]["id"]).json()
    assert report["format"] == "tinystudio.method-matrix.v1"
    assert report["baseline_method"] == "conventional"
    assert report["qualification"]["coverage_complete"]
    assert len(report["records"]) == 12
    assert set(report["comparisons"]) == {"base", "specialized", "lora", "rag", "combined"}
    by_method = {method["id"]: [row for row in report["records"] if row["method_id"] == method["id"]] for method in methods}
    for method in methods:
        rows = by_method[method["id"]]
        assert {(row["source_id"], row["episode_id"]) for row in rows} == {("first", "heldout"), ("second", "heldout")}
        assert all(row["cases"]["sha256"] == case_ref["sha256"] for row in rows)
        if method["kind"] == "model":
            assert all(row["model"]["artifact_id"] == method["artifact_id"] for row in rows)
            for row in rows:
                assert row["output"]["token_ids"]
                assert "-2.25" not in row["output"]["prompt"] and "3.25" not in row["output"]["prompt"]
                assert ('"reading"' in row["output"]["prompt"])
                assert bool(row.get("retrieval")) is method["retrieval"]
                if method["retrieval"]:
                    assert row["retrieval"]["index"]["sha256"] == index["sha256"]
    assert [row["prediction"] for row in by_method["conventional"]] == ["-2.25", "3.25"]
    # No requirement that a neural method beats this independently computed baseline.
    assert report["evaluations"]["conventional"]["passed"]


def test_matrix_cancelled_during_upload_never_publishes_success_result(integrated, monkeypatch):
    client, prefix, _, app, _, root, _ = integrated
    target = client.post(prefix + "/targets", json={"name": "cancel matrix"}).json()
    case_ref = upload(client, prefix, cases(root), "cases", "evaluation")
    definition = {"name": "cancel matrix", "engine": "evaluation.compare", "execution": EXECUTION,
        "inputs": [case_ref], "parameters": {"operation": "matrix", "methods": [conventional(), conventional("candidate", "1")],
            "baseline_method": "conventional"}}
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        run_id, _ = submit(client, prefix, target, definition)
        original = machine.post
        uploaded = []
        def post(url, *args, **kwargs):
            response = original(url, *args, **kwargs)
            if str(url).endswith("/artifacts") and response.status_code < 300 and not uploaded:
                uploaded.append(response.json()["id"])
                cancellation = client.post(prefix + "/runs/" + run_id + "/cancel")
                assert cancellation.status_code == 200
            return response
        monkeypatch.setattr(machine, "post", post)
        assert runner.run_once(run_id=run_id)
    run = client.get(prefix + "/runs/" + run_id).json()
    assert uploaded, "the fixture must cancel during actual artifact publication"
    assert run["state"] == "cancelled"
    assert run.get("result") is None
