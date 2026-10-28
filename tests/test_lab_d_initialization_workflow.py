"""D initialization through authenticated app routes and actual native children.

Disposable PostgreSQL/Redis fixtures qualify software wiring, not model quality
or user research. Run with --noconftest to avoid shared test stores.
"""
from copy import deepcopy
import hashlib
import io

import pytest
import torch
from fastapi.testclient import TestClient

from tests.test_lab_b_workflow import database, integrated
from tinystudio.lab.runner import Runner


def _execute(client, prefix, runner, target, definition, key):
    definition = {**deepcopy(definition), "name": key}
    preflight = client.post(prefix + "/preflight", json={"definition": definition, "target_id": target["id"]})
    assert preflight.status_code == 200 and preflight.json()["compatible"], preflight.text
    saved = client.post(prefix + "/definitions", json={"definition": definition})
    assert saved.status_code == 201, saved.text
    admitted = client.post(prefix + "/runs", json={"definition_id": saved.json()["id"], "revision": 1,
        "target_id": target["id"], "idempotency_key": key})
    assert admitted.status_code == 202, admitted.text
    identity = admitted.json()["id"]
    assert runner.run_once(run_id=identity)
    result = client.get(prefix + "/runs/" + identity)
    assert result.status_code == 200, result.text
    run = result.json()
    assert run["state"] == "succeeded", run.get("error")
    checkpoint = next(row for row in run["result"]["artifacts"] if row["role"] == "checkpoint")
    content = client.get(prefix + "/artifacts/" + checkpoint["id"])
    assert content.status_code == 200, content.text
    assert hashlib.sha256(content.content).hexdigest() == checkpoint["sha256"]
    return run, checkpoint, torch.load(io.BytesIO(content.content), weights_only=True, map_location="cpu")


@pytest.mark.parametrize("family", ["gru", "transformer"])
def test_frozen_language_restart_and_exact_resume_use_retained_artifacts(integrated, family):
    client, prefix, other_prefix, app, connection, root, factory = integrated
    response = client.post(prefix + "/targets", json={"name": "d-language-native"})
    assert response.status_code == 201, response.text
    target = response.json()
    body = {"name": "D software fixture", "engine": "learning.language", "seed": 23,
        "learning": {"family": family, "initialization": "scratch", "objective": "causal_lm",
            "representation": {"kind": "bpe", "vocab_size": 280}, "update_strategy": "adamw",
            "architecture": {"hidden_size": 8, "layers": 1, "context": 8, **({"heads": 2} if family == "transformer" else {})}},
        "parameters": {"steps": 2, "max_new_tokens": 2, "documents": [
            {"text": "valve open " * 4, "source_record_id": "train-1", "partition": "train"},
            {"text": "valve closed " * 4, "source_record_id": "train-2", "partition": "train"},
            {"text": "pressure stable " * 4, "source_record_id": "train-3", "partition": "train"},
            {"text": "validation evidence", "source_record_id": "validation-1", "partition": "validation"},
            {"text": "FINAL_ZZZZZZ", "source_record_id": "test-1", "partition": "test"}]},
        "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 60}}
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        original, parent, original_state = _execute(client, prefix, runner, target, body, "d-scratch-" + family)
        assert original["result"]["qualification"]["from_scratch"] is True
        assert original["result"]["metrics"]["excluded_test_documents"] == 1
        assert original_state["split"] == {"train": [0, 1, 2], "validation": [3]}
        changed = {**deepcopy(body), "name": "edited after scratch " + family}
        revision = client.put(prefix + "/definitions/" + original["definition_id"],
            json={"expected_revision": 1, "definition": changed})
        assert revision.status_code == 200 and revision.json()["revision"] == 2, revision.text
        library = client.get(prefix + "/inputs").json()
        assert any(row["id"] == parent["id"] and row["sha256"] == parent["sha256"] for row in library)
        origin = next(row for row in library if row["id"] == parent["id"])["origin"]
        assert origin == {"run_id": original["id"], "revision": 1,
                          "definition_name": "d-scratch-" + family}
        assert client.get(other_prefix + "/artifacts/" + parent["id"]).status_code == 404

        restart = deepcopy(body)
        restart["seed"] = 71
        restart["learning"]["initialization"] = "weights"
        restart["inputs"] = [{"artifact_id": parent["id"], "sha256": parent["sha256"],
                              "role": "initial_weights", "visibility": "model"}]
        restart["parameters"]["documents"][0]["text"] = "temperature rises " * 4
        restarted, intermediate, intermediate_state = _execute(client, prefix, runner, target, restart, "d-weights-" + family)
        assert intermediate_state["tokenizer"] == original_state["tokenizer"]
        assert intermediate_state["corpus_sha256"] != original_state["corpus_sha256"]
        assert intermediate_state["step"] == 2
        assert {int(row["step"]) for row in intermediate_state["optimizer"]["state"].values()} == {2}
        assert restarted["result"]["qualification"]["parent_checkpoint_sha256"] == parent["sha256"]
        assert restarted["result"]["qualification"]["optimizer_state_restored"] is False

        resume = deepcopy(restart)
        resume["learning"]["initialization"] = "resume"
        resume["parameters"]["steps"] = 4
        resume["inputs"] = [{"artifact_id": intermediate["id"], "sha256": intermediate["sha256"],
                             "role": "resume", "visibility": "model"}]
        resumed, _, resumed_state = _execute(client, prefix, runner, target, resume, "d-resume-" + family)
        restart["parameters"]["steps"] = 4
        whole, _, whole_state = _execute(client, prefix, runner, target, restart, "d-uninterrupted-" + family)
        assert all(torch.equal(value, whole_state["model"][key]) for key, value in resumed_state["model"].items())
        assert resumed["result"]["qualification"]["optimizer_state_restored"] is True
        assert resumed["result"]["qualification"]["parent_checkpoint_sha256"] == parent["sha256"]
        for run in (restarted, resumed, whole):
            assert run["result"]["qualification"]["from_scratch"] is False
            assert run["result"]["metrics"]["parameters_changed"] is True
            assert run["result"]["metrics"]["reload_logits_equal"] is True
        unchanged = client.get(prefix + "/artifacts/" + parent["id"])
        assert hashlib.sha256(unchanged.content).hexdigest() == parent["sha256"]
