"""Arithmetic comparison evidence comes from actual calls on disjoint cases."""
import hashlib
import json

import pytest
import torch

from tinystudio.lab.tool_evaluation import (
    DEFAULT_HELDOUT_TASKS, categorical_call, comparison_report, evaluate_pass, validate_tool_tasks,
)
from tinystudio.lab.rl import _policy, execute_rl, load_policy
from tinystudio.lab.language_rl import execute_language_rl, generate_tool_call


def experiment(token=False, **parameters):
    return {"definition": {"seed": 29, "plugin_id": "learning.language_rl" if token else "learning.tool_rl",
        "learning": {"objective": "grpo", **({"family": "gru"} if token else {})},
        "parameters": {"steps": 1, "batch_size": 4, "hidden_size": 8, "update_epochs": 1, **parameters}}}


@pytest.mark.parametrize("token", [False, True])
def test_actual_retained_tool_comparison_and_independent_arithmetic(tmp_path, token):
    runner = execute_language_rl if token else execute_rl
    spec = experiment(token)
    result = runner(spec, tmp_path, lambda row: None, lambda: False)
    report = json.loads((tmp_path / "heldout-evaluation.json").read_text())
    checkpoint = tmp_path / ("language-policy.pt" if token else "policy.pt")
    assert report["checkpoint_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert report["policy_kind"] == ("autoregressive" if token else "categorical")
    assert report["qualification"]["comparison_complete"]
    assert report["qualification"]["weights_unchanged"]
    assert not report["qualification"]["heldout_tasks_used_for_updates"]
    assert report["format"] == "tinystudio.tool-evaluation.v1"
    # Explicit independent expected answers, not the producer's evaluator.
    for index, (record, expected) in enumerate(zip(report["records"], [7, 5, 10, 3])):
        assert record["case_id"] == f"heldout:{index}"
        assert record["expected"] == expected
        final = (generate_tool_call(checkpoint, record["task"]) if token else
                 categorical_call(load_policy(checkpoint)[0], record["task"]))
        assert record["final"]["tool"] == final["tool"]
        for phase in ("initial", "final"):
            row = record[phase]
            assert row["partition"] == "heldout"
            assert row["evaluation"]["expected"] == expected
            assert row["evaluation"]["correct"] == (row["tool"]["error"] is None and row["tool"]["result"] == expected)
    assert report["metrics"]["final"]["evaluated"] == 4
    assert any(item["role"] == "heldout_tool_evaluation" for item in result["artifacts"])
    if token:
        assert report["initial"] == [record["initial"] for record in report["records"]]
        assert report["final"] == [record["final"] for record in report["records"]]
        assert report["evaluation_policy"].startswith("independent")
        assert report["final"][0]["generated_token_ids"]


@pytest.mark.parametrize("heldout,match", [
    ([{"operation": "add", "left": 2.0, "right": 3.0}], "overlap"),
    ([DEFAULT_HELDOUT_TASKS[0], {"operation": "add", "left": 5.0, "right": 2.0}], "duplicate"),
    ([{"operation": "add", "left": True, "right": 1}], "finite"),
    ([{"operation": "add", "left": float("nan"), "right": 1}], "finite"),
    ([{"operation": "add", "left": 1000001, "right": 1}], "bounded"),
    ([{"operation": "divide", "left": 2, "right": 0}], "zero"),
])
def test_partition_boundaries(heldout, match):
    with pytest.raises(ValueError, match=match):
        validate_tool_tasks([{"operation": "add", "left": 2, "right": 3}], heldout)


def model():
    return _policy({"observation_size": 6, "hidden_size": 8, "actions": 4})


def test_partial_cancellation_keeps_call_and_uses_evaluated_denominator(tmp_path):
    policy = model()
    calls = []
    def invoke(task):
        calls.append(task)
        return categorical_call(policy, task)
    rows, interrupted = evaluate_pass([policy], DEFAULT_HELDOUT_TASKS, invoke, lambda: bool(calls))
    assert interrupted and len(rows) == 1
    checkpoint = tmp_path / "policy.pt"
    checkpoint.write_bytes(b"checkpoint")
    report = comparison_report("categorical", checkpoint, DEFAULT_HELDOUT_TASKS, rows, [],
        completed=True, interrupted=interrupted, reload_equal=True)
    assert report["qualification"]["status"] == "cancelled"
    assert not report["qualification"]["comparison_complete"]
    assert report["records"][0]["initial"] and report["records"][1]["initial"] is None
    assert report["metrics"]["initial"]["accuracy"] in (0, 1)
    assert report["metrics"]["final"]["accuracy"] is None


@pytest.mark.parametrize("fault", ["mutation", "nan_logits", "nan_trace", "bad_evaluation"])
def test_refuses_mutation_nonfinite_or_inconsistent_evidence(fault):
    policy = model()
    policy.train()
    policy.body[0].eval()  # Mixed modes must also be preserved.
    modes = [module.training for module in policy.modules()]
    def invoke(task):
        row = categorical_call(policy, task)
        if fault == "mutation":
            next(policy.parameters()).add_(1)
        elif fault == "nan_logits":
            policy.actor.register_forward_hook(lambda module, args, output: output * float("nan"))
            return categorical_call(policy, task)
        elif fault == "nan_trace":
            row["tool"]["result"] = float("nan")
        else:
            row["evaluation"]["expected"] = 999
        return row
    with pytest.raises(ValueError, match="mutated|non-finite|inconsistent"):
        evaluate_pass([policy], DEFAULT_HELDOUT_TASKS, invoke, lambda: False)
    assert modes == [module.training for module in policy.modules()]


def test_wrong_calls_and_tool_errors_are_retained():
    policy = model()
    with torch.no_grad():
        policy.actor.weight.zero_()
        policy.actor.bias.copy_(torch.tensor([0., 0., 0., 10.]))
    tasks = [{"operation": "add", "left": 2, "right": 0}, {"operation": "multiply", "left": 3, "right": 2}]
    rows, interrupted = evaluate_pass([policy], tasks, lambda task: categorical_call(policy, task), lambda: False)
    assert not interrupted
    assert rows[0]["tool"]["error"]["type"] == "ZeroDivisionError"
    assert rows[0]["tool"]["result"] is None
    assert rows[1]["tool"]["result"] == 1.5
    assert all(not row["evaluation"]["correct"] for row in rows)


@pytest.mark.parametrize("token", [False, True])
def test_real_partial_run_retains_complete_initial_and_missing_final(tmp_path, token):
    events = []
    runner = execute_language_rl if token else execute_rl
    result = runner(experiment(token, steps=3), tmp_path, events.append, lambda: bool(events))
    report = json.loads((tmp_path / "heldout-evaluation.json").read_text())
    assert result["qualification"]["status"] == "cancelled"
    assert report["qualification"]["status"] == "cancelled"
    assert not report["qualification"]["comparison_complete"]
    assert report["metrics"]["initial"]["evaluated"] == 4
    assert report["metrics"]["final"]["evaluated"] == 0
    assert all(record["initial"] and record["final"] is None for record in report["records"])


def test_evaluation_preserves_rng_and_restores_modes():
    policy = model()
    policy.train()
    policy.actor.eval()
    modes = [module.training for module in policy.modules()]
    rng = torch.get_rng_state().clone()
    def invoke(task):
        torch.rand(4)  # Controlled violation of RNG neutrality inside a callback.
        return categorical_call(policy, task)
    evaluate_pass([policy], DEFAULT_HELDOUT_TASKS, invoke, lambda: False)
    assert torch.equal(rng, torch.get_rng_state())
    assert modes == [module.training for module in policy.modules()]


def test_nonfinite_weights_and_forged_tool_result_refused():
    policy = model()
    with torch.no_grad():
        policy.actor.bias[0] = float("inf")
    with pytest.raises(ValueError, match="non-finite weights"):
        evaluate_pass([policy], DEFAULT_HELDOUT_TASKS, lambda task: categorical_call(policy, task), lambda: False)
    policy = model()
    def forged(task):
        return {"task": task, "tool": {"name": "subtract", "arguments": {"left": 5, "right": 2}, "result": 7, "error": None},
                "evaluation": {"expected": 7, "actual": 7, "correct": True, "tool_error": False}}
    with pytest.raises(ValueError, match="inconsistent with invocation"):
        evaluate_pass([policy], DEFAULT_HELDOUT_TASKS, forged, lambda: False)


def test_selected_tool_overflow_is_retained_error_for_valid_requested_task():
    policy = model()
    with torch.no_grad():
        policy.actor.weight.zero_()
        policy.actor.bias.copy_(torch.tensor([0., 0., 0., 10.]))
    task = {"operation": "add", "left": 1, "right": 1e-310}
    rows, interrupted = evaluate_pass([policy], [task], lambda task: categorical_call(policy, task), lambda: False)
    assert not interrupted and len(rows) == 1
    assert rows[0]["tool"]["result"] is None
    assert rows[0]["tool"]["error"]["type"] == "ValueError"
    assert rows[0]["evaluation"] == {"expected": 1.0, "actual": None, "correct": False, "tool_error": True}
