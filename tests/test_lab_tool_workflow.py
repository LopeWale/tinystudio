"""D tool outcomes through registered routes, native execution, retained reload.

Use --noconftest and the existing disposable PostgreSQL/private Redis fixture.
These are software qualification cases, not user research experiments.
"""
import hashlib
import json

import pytest
import torch
from fastapi.testclient import TestClient

from tests.test_lab_b_workflow import database, integrated
from tests.test_lab_numeric_policy_workflow import definition, run_and_download
from tinystudio.lab.runner import Runner


@pytest.mark.parametrize("engine,objective", [
    ("learning.tool_rl", "ppo_tool_bandit"),
    ("learning.tool_rl", "grpo_tool_bandit"),
    ("learning.language_rl", "ppo"),
    ("learning.language_rl", "grpo"),
])
def test_actual_tool_policy_retains_paired_heldout_outcomes_and_reloaded_calls(integrated, engine, objective):
    client, prefix, other_prefix, app, _, root, _ = integrated
    target = client.post(prefix + "/targets", json={"name": "d-tool-outcomes"}).json()
    training = [{"operation": "add", "left": 2, "right": 3},
                {"operation": "subtract", "left": 7, "right": 4}]
    heldout = [{"operation": "subtract", "left": 2, "right": 5},
               {"operation": "divide", "left": 3, "right": 2},
               {"operation": "add", "left": 4, "right": 0}]
    body = definition(engine, tasks=training, heldout_tasks=heldout, update_epochs=1, batch_size=4)
    body["learning"] = {"objective": objective, "family": "gru" if engine.endswith("language_rl") else "mlp"}
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        run, paths = run_and_download(client, prefix, other_prefix, runner, target, body, root)
    report = json.loads(paths["heldout_tool_evaluation"].read_text())
    assert report["format"] == "tinystudio.tool-evaluation.v1"
    assert run["result"]["artifacts"][-1]["role"] == "heldout_tool_evaluation"
    checkpoint = paths["language_policy_checkpoint" if engine.endswith("language_rl") else "policy_checkpoint"]
    assert report["checkpoint_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert [row["task"] for row in report["records"]] == heldout
    # Signed/fractional expectations are independent of the production evaluator.
    assert [row["expected"] for row in report["records"]] == [-3, 1.5, 4]
    assert len({row["case_id"] for row in report["records"]}) == 3
    assert report["qualification"]["status"] == "completed"
    assert report["qualification"]["comparison_complete"] is True
    assert report["qualification"]["heldout_tasks_used_for_updates"] is False
    assert report["qualification"]["weights_unchanged"] is True
    for arm in ("initial", "final"):
        traces = [row[arm] for row in report["records"]]
        assert all(trace is not None for trace in traces)
        assert [trace["task"] for trace in traces] == heldout
        correct = errors = 0
        for row, trace in zip(report["records"], traces):
            tool = trace["tool"]
            args = tool["arguments"]
            left, right = args["left"], args["right"]
            if tool["error"]:
                assert tool["name"] == "divide" and right == 0
                assert tool["result"] is None
                errors += 1
            else:
                actual = {"add": lambda: left + right, "subtract": lambda: left - right,
                          "multiply": lambda: left * right, "divide": lambda: left / right}[tool["name"]]()
                assert tool["result"] == actual
            expected_correct = tool["error"] is None and tool["result"] == row["expected"]
            assert trace["evaluation"]["correct"] == expected_correct
            assert trace["evaluation"]["expected"] == row["expected"]
            assert trace["evaluation"]["tool_error"] == bool(tool["error"])
            correct += expected_correct
        assert report["metrics"][arm] == {"evaluated": 3, "correct": correct, "errors": errors, "accuracy": correct / 3}
    if engine.endswith("language_rl"):
        from tinystudio.lab.language_rl import generate_tool_call
        for row in report["records"]:
            replay = generate_tool_call(checkpoint, row["task"])
            assert replay["generated_token_ids"] == row["final"]["generated_token_ids"]
            assert replay["tool"] == row["final"]["tool"]
        legacy = json.loads(paths["heldout_tool_evaluation"].read_text())
        assert legacy["initial"] and legacy["final"] and legacy["evaluation_policy"]
    else:
        from tinystudio.lab.rl import load_policy
        model, _ = load_policy(checkpoint)
        names = ("add", "subtract", "multiply", "divide")
        with torch.no_grad():
            for row in report["records"]:
                task = row["task"]
                features = [task["left"] / 10, task["right"] / 10] + [float(task["operation"] == name) for name in names]
                action = int(model(torch.tensor([features]))[0].argmax(-1))
                assert row["final"]["tool"]["name"] == names[action]
    assert run["result"]["baseline_comparison"]["metrics"] == report["metrics"]
    assert "records" not in run["result"]["baseline_comparison"]
