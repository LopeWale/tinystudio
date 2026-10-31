"""D qualification through registered routes and a real native Runner child.

Run with --noconftest and PostgreSQL CREATE DATABASE access. The inherited
integrated fixture owns its disposable database and private Redis process.
Run the TorchRL case with an interpreter containing torchrl and tensordict.
"""
from copy import deepcopy
import hashlib
import json

import pytest
import torch
from fastapi.testclient import TestClient

from tests.test_lab_b_workflow import database, integrated
from tinystudio.lab.runner import Runner


def upload(client, prefix, rows, role="source", visibility="model"):
    content = json.dumps({"records": rows}).encode()
    response = client.post(prefix + "/inputs", files={"file": (role + ".json", content, "application/json")})
    assert response.status_code == 201, response.text
    artifact = response.json()
    assert artifact["sha256"] == hashlib.sha256(content).hexdigest()
    return {"artifact_id": artifact["id"], "sha256": artifact["sha256"], "role": role, "visibility": visibility}


def run_and_download(client, prefix, other_prefix, runner, target, definition, root):
    # Same route lifecycle as retained_retrieval_workflow._run; that helper's
    # fixed retrieval artifact roles cannot serve numeric/policy engines.
    checked = client.post(prefix + "/preflight", json={"definition": definition, "target_id": target["id"]})
    assert checked.status_code == 200 and checked.json()["compatible"], checked.text
    saved = client.post(prefix + "/definitions", json={"definition": definition})
    assert saved.status_code == 201, saved.text
    submitted = client.post(prefix + "/runs", json={"definition_id": saved.json()["id"],
        "revision": saved.json()["revision"], "target_id": target["id"], "idempotency_key": definition["name"]})
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["id"]
    # Editing after submission must never change the frozen run revision.
    edited = deepcopy(definition)
    edited["parameters"]["steps"] = 3
    changed = client.put(prefix + "/definitions/" + saved.json()["id"],
                         json={"definition": edited, "expected_revision": saved.json()["revision"]})
    assert changed.status_code == 200, changed.text
    assert runner.run_once(run_id=run_id)
    run = client.get(prefix + "/runs/" + run_id).json()
    assert run["state"] == "succeeded", run.get("error")
    assert run["spec"]["definition"]["parameters"]["steps"] == definition["parameters"]["steps"]
    assert run["spec"]["definition"].get("inputs", []) == definition.get("inputs", [])
    assert client.get(other_prefix + "/runs/" + run_id).status_code == 404
    paths = {}
    for artifact in run["result"]["artifacts"]:
        response = client.get(prefix + "/artifacts/" + artifact["id"])
        assert response.status_code == 200, response.text
        assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
        assert client.get(other_prefix + "/artifacts/" + artifact["id"]).status_code == 404
        path = root / artifact["name"]
        path.write_bytes(response.content)
        paths[artifact["role"]] = path
    profile = run["result"]["execution_receipt"]["profile_manifest"]
    assert profile["backend"] == "native" and profile["profile"] == "trusted_native"
    return run, paths


def definition(engine, **parameters):
    return {"name": "D " + engine, "engine": engine, "seed": 13,
            "parameters": {"steps": 2, "hidden_size": 8, **parameters},
            "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 60}}


@pytest.mark.parametrize("engine,objective", [("learning.timeseries", "forecasting"),
    ("learning.multimodal", "regression"), ("learning.multimodal", "classification")])
def test_retained_numeric_validation_custody_and_fresh_checkpoint_prediction(integrated, engine, objective):
    from tinystudio.lab.timeseries import load_research_checkpoint, predict_from_checkpoint
    client, prefix, other_prefix, app, _, root, _ = integrated
    target = client.post(prefix + "/targets", json={"name": "d-numeric"}).json()
    rows = lambda episode, offset: [{"source_record_id": f"{episode}:{index}", "episode_id": episode,
        "time": index, "value": offset + index, "numeric": [offset + index], "text": "signal", "target": offset + index}
        for index in range(6)]
    training = rows("train", 0)
    training.extend({"source_record_id": label, "partition": label, "episode_id": label,
                     "value": "invalid excluded value", "numeric": None, "target": None}
                    for label in ("test", "truth"))
    for row in training[:6]:
        row["partition"] = "train"
    validation = rows("validation", 10)
    if objective == "classification":
        for group in (training[:6], validation):
            for index, row in enumerate(group):
                row["target"] = "odd" if index % 2 else "even"
    body = definition(engine, **({"context": 2} if engine.endswith("timeseries") else {"modalities": ["numeric", "text"]}))
    body["learning"] = {"family": "mlp"} if engine.endswith("timeseries") else {"objective": objective}
    body["inputs"] = [upload(client, prefix, training), upload(client, prefix, validation, "validation", "evaluation")]
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        run, paths = run_and_download(client, prefix, other_prefix, runner, target, body, root)
    model, state = load_research_checkpoint(paths["model_checkpoint"])
    assert run["result"]["artifacts"][-1]["role"] == "independent_evaluation"
    assert not model.training
    forecasting = engine.endswith("timeseries")
    normalization = state["normalization"] if forecasting else state["normalization"]["numeric"]
    assert normalization["mean"].tolist() == pytest.approx([2.5])
    retained = json.loads(paths["held_out_predictions"].read_text())
    expected = range(2, 6) if forecasting else range(6)
    assert [row["source_record_ids"] for row in retained["records"]] == [[f"validation:{i}"] for i in expected]
    assert all(row["partition"] == "validation" for row in retained["records"])
    if objective == "classification":
        labels = [row["target"] for row in validation]
        assert retained["target_encoding"] == "class_index"
        assert retained["target_labels"] == labels
        assert [retained["classes"][index] for index in retained["targets"]] == labels
        assert [row["target"] for row in retained["records"]] == labels
    probes = [[[10 + i], [11 + i]] for i in range(4)] if forecasting else validation
    assert predict_from_checkpoint(paths["model_checkpoint"], probes) == retained["predictions"]
    assert run["result"]["metrics"]["reload_predictions_equal"]
    evaluation = json.loads(paths["independent_evaluation"].read_text())
    assert evaluation["format"] == "tinystudio.numeric-evaluation.v1"
    assert evaluation["task"] == objective and evaluation["partition"] == "validation"
    assert evaluation["model_sha256"] == hashlib.sha256(paths["model_checkpoint"].read_bytes()).hexdigest()
    assert evaluation["corpus_sha256"] == state["corpus_sha256"]
    assert evaluation["qualification"]["comparison_complete"]
    assert evaluation["qualification"]["evaluation_valid"] and evaluation["qualification"]["validation_only"]
    assert run["result"]["baseline_comparison"] == {key: evaluation[key] for key in ("format", "metrics", "qualification")}
    assert [row["source_record_ids"] for row in evaluation["records"]] == [row["source_record_ids"] for row in retained["records"]]
    assert [row["prediction"] for row in evaluation["records"]] == retained["predictions"]
    assert evaluation["metrics"]["cases"] == len(retained["records"])
    baseline = evaluation["baseline"]
    if forecasting:
        assert baseline["kind"] == "persistence" and baseline["fit_source_record_ids"] == []
        assert baseline["fit_case_count"] == 0
        assert evaluation["metrics"]["baseline"] == {"mae": 1, "mse": 1}
        assert [row["baseline"] for row in evaluation["records"]] == [[11 + i] for i in range(4)]
        assert evaluation["records"][0]["context_source_record_ids"] == ["validation:0", "validation:1"]
    else:
        assert baseline["fit_source_record_ids"] == [f"train:{i}" for i in range(6)]
        assert baseline["fit_case_count"] == 6
        assert all(row["context_source_record_ids"] == [] for row in evaluation["records"])
        assert baseline["kind"] == ("training_majority" if objective == "classification" else "training_mean")
        assert baseline["constant"] == ("even" if objective == "classification" else [2.5])
    assert [json.loads(row["raw_target_json"]) for row in evaluation["records"]] == (
        [[10 + i] for i in expected] if forecasting else [row["target"] for row in validation])


@pytest.mark.parametrize("objective,runtime,agents", [("ppo", "native", 1), ("ppo", "torchrl", 2),
                                                      ("dqn", "native", 1), ("imitation", "native", 1)])
def test_control_policy_retained_sources_and_frozen_independent_evaluation(integrated, objective, runtime, agents):
    from tinystudio.lab.rl import load_policy
    client, prefix, other_prefix, app, _, root, _ = integrated
    target = client.post(prefix + "/targets", json={"name": "d-control"}).json()
    body = definition("learning.control_rl", batch_size=8, horizon=4, agents=agents,
                      environment={"kind": "control", "runtime": runtime}, evaluation={"seeds": [113, 114]})
    body["learning"] = {"objective": objective}
    if objective != "ppo":
        rows = [{"source_record_id": f"train:{i}", "partition": "train", "observation": [i / 10, 0, 0],
                 "action": i % 3, "next_observation": [i / 10 + .1, 0, 0], "reward": -.1, "done": False}
                for i in range(4)]
        rows.extend({"source_record_id": label, "partition": label, "observation": "excluded", "action": "excluded"}
                    for label in ("validation", "test", "truth"))
        body["inputs"] = [upload(client, prefix, rows)]
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        run, paths = run_and_download(client, prefix, other_prefix, runner, target, body, root)
    model, state = load_policy(paths["policy_checkpoint"])
    with torch.no_grad():
        logits, value = model(torch.tensor([[.1, 0, 0]]))
    assert logits.shape == (1, 3) and value.numel() == 1
    assert torch.isfinite(logits).all()
    sampled = [json.loads(line) for line in paths["transitions"].read_text().splitlines()]
    if objective != "ppo":
        assert {row["source_record_id"] for row in sampled} <= {f"train:{i}" for i in range(4)}
    else:
        assert all(len(row["joint_action"]) == agents for row in sampled)
    report = json.loads(paths["independent_evaluation"].read_text())
    qualification = report["qualification"]
    assert qualification["weights_unchanged"]
    assert qualification["weights_sha256_before"] == qualification["weights_sha256_after"]
    assert qualification["training_seed"] not in qualification["seeds"]
    assert qualification["seeds"] == [113, 114]
    assert report["metrics"]["paired_initial_states"]
    assert all(pair["policy"] == pair["baseline"] for pair in qualification["initial_states"])
    assert qualification["environment_runtime"] == runtime
    assert {row["arm"] for row in report["records"]} == {"policy", "baseline"}
    for row in report["records"]:
        assert row["absolute_tracking_error"] == pytest.approx(abs(row["next_observation"][0] - row["next_observation"][2]))
        if row["arm"] == "policy":
            with torch.no_grad():
                expected_action = int(model(torch.tensor([row["observation"]], dtype=torch.float32))[0].argmax(-1))
        else:
            position, velocity, target_position = row["observation"]
            demand = target_position - position - 2 * velocity
            expected_action = 2 if demand > .05 else 0 if demand < -.05 else 1
        assert row["action"] == expected_action
    assert run["result"]["metrics"]["reload_logits_equal"]


@pytest.mark.parametrize("objective", ["ppo", "grpo"])
def test_language_policy_frozen_tasks_reload_and_independent_tool_correctness(integrated, objective):
    from tinystudio.lab.language_rl import CallLanguage, generate_tool_call, load_language_rl_checkpoint
    client, prefix, other_prefix, app, _, root, _ = integrated
    target = client.post(prefix + "/targets", json={"name": "d-language"}).json()
    tasks = [{"operation": "add", "left": 2, "right": 3}, {"operation": "subtract", "left": 7, "right": 4}]
    body = definition("learning.language_rl", group_size=4, update_epochs=1, tasks=tasks,
                      heldout_tasks=[{"operation": "multiply", "left": 3, "right": 2}])
    body["learning"] = {"family": "gru", "objective": objective}
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        run, paths = run_and_download(client, prefix, other_prefix, runner, target, body, root)
    load_language_rl_checkpoint(paths["language_policy_checkpoint"])
    generation = generate_tool_call(paths["language_policy_checkpoint"], tasks[0])
    name, arguments = CallLanguage().parse(generation["generated_text"])
    assert name in {"add", "subtract", "multiply", "divide"}
    assert set(arguments) == {"left", "right"}
    traces = [json.loads(line) for line in paths["token_tool_trajectories"].read_text().splitlines()]
    assert {tuple(sorted(row["task"].items())) for row in traces} <= {tuple(sorted(task.items())) for task in tasks}
    heldout = json.loads(paths["heldout_tool_evaluation"].read_text())
    assert all(row["task"] == {"operation": "multiply", "left": 3, "right": 2} for row in heldout["final"])
    assert all(row["evaluation"]["expected"] == 6 for row in heldout["final"])
    for row in heldout["final"]:
        expected_correct = row["tool"]["error"] is None and row["tool"]["result"] == 6
        assert row["evaluation"]["correct"] == expected_correct
    assert run["result"]["qualification"]["heldout_tasks_used_for_updates"] is False
