"""Pure CPU tests of actual interactions and parameter updates."""
import json
import hashlib
from pathlib import Path

import pytest
import torch

from tinystudio.lab.rl import ControlEnvironment, ControlReward, LocalTools, clipped_policy_loss, evaluate_tool, execute_rl, load_policy, transition_tensordict


def spec(objective="ppo", **parameters):
    value = {"definition": {"seed": 18, "learning": {"objective": objective},
        "parameters": {"steps": 3, "batch_size": 8, "hidden_size": 8, **parameters}}}
    path = value["definition"]["parameters"].pop("resume_from", None)
    if path:
        value["definition"]["inputs"] = [{"artifact_id": "resume", "role": "resume",
            "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}]
        value["input_paths"] = {"resume": str(path)}
    return value


def test_environment_dynamics_reward_and_termination_contract():
    environment = ControlEnvironment(seed=4, horizon=2, agents=2)
    observation = environment.reset()
    result = environment.step([2, 2])
    assert result["observation"][1] == pytest.approx(0.1)
    assert result["observation"][0] == pytest.approx(observation[0] + 0.1)
    assert result["reward"] == pytest.approx(-abs(observation[0] + 0.1) - 0.02)
    assert result["evaluation"]["absolute_tracking_error"] == pytest.approx(abs(observation[0] + 0.1))
    result = environment.step([1, 1])
    assert result["truncated"] and result["done"] and not result["terminated"]
    with pytest.raises(ValueError, match="reset"):
        environment.step([1, 1])


def test_reward_and_evaluator_are_distinct():
    no_cost = ControlReward(action_cost=0)
    high_cost = ControlReward(action_cost=2)
    assert no_cost(0.1, 0, [2]) == pytest.approx(-0.1)
    assert high_cost(0.1, 0, [2]) == pytest.approx(-2.1)


def test_ppo_clipping_matches_independent_scalar_example():
    # Positive advantage clips excessive policy growth at 1.2; negative advantage
    # penalizes that same growth without clipping it away.
    loss = clipped_policy_loss(torch.log(torch.tensor([2.0, 2.0])), torch.zeros(2), torch.tensor([1.0, -1.0]), 0.2)
    assert float(loss) == pytest.approx(0.4)


@pytest.mark.parametrize("agents", [1, 2])
def test_ppo_collects_environment_and_updates_versioned_policy(tmp_path, agents):
    result = execute_rl(spec(agents=agents), tmp_path, lambda row: None, lambda: False)
    assert result["metrics"]["parameters_changed"]
    assert result["metrics"]["reload_logits_equal"]
    rows = [json.loads(line) for line in (tmp_path / "transitions.jsonl").read_text().splitlines()]
    assert len(rows) == 3 * 8 * agents
    assert all(len(row["joint_action"]) == agents for row in rows)
    assert {row["agent_id"] for row in rows} == {str(index) for index in range(agents)}
    assert all(row["policy_version"] and "evaluation" in row for row in rows)
    assert len({row["policy_version"] for row in rows}) > 1
    assert result["records"][-1]["policy_version_after"] == result["metrics"]["policy_version"]


@pytest.mark.parametrize("objective", ["dqn", "imitation"])
def test_offline_learning_uses_actual_retained_transitions(tmp_path, objective):
    transitions = [{"observation": [0.5, 0.0, 0.0], "action": 0, "reward": -0.4,
        "next_observation": [0.4, -0.1, 0.0], "done": False, "policy_version": "demonstrator-v1"},
        {"observation": [-0.5, 0.0, 0.0], "action": 2, "reward": -0.4,
        "next_observation": [-0.4, 0.1, 0.0], "done": True, "policy_version": "demonstrator-v1"}]
    result = execute_rl(spec(objective, transitions=transitions), tmp_path, lambda row: None, lambda: False)
    assert result["metrics"]["parameters_changed"]
    rows = [json.loads(line) for line in (tmp_path / "transitions.jsonl").read_text().splitlines()]
    assert all(row["behavior_policy_version"] == "demonstrator-v1" and row["source"] == "offline" for row in rows)
    assert result["qualification"]["offline"] is True


@pytest.mark.parametrize("objective", ["grpo", "ppo_tool_bandit"])
def test_tool_policy_learning_executes_tools_and_evaluates_results(tmp_path, objective):
    result = execute_rl(spec(objective, batch_size=32), tmp_path, lambda row: None, lambda: False)
    assert result["metrics"]["tool_calls"] == 96
    assert result["metrics"]["parameters_changed"]
    rows = [json.loads(line) for line in (tmp_path / "transitions.jsonl").read_text().splitlines()]
    assert all(row["tool"]["arguments"] == {"left": row["task"]["left"], "right": row["task"]["right"]} for row in rows)
    assert any(row["evaluation"]["correct"] for row in rows)
    assert any(not row["evaluation"]["correct"] for row in rows)
    assert all(row["evaluation"] == evaluate_tool(row["task"], row["tool"]) for row in rows)
    if objective == "grpo":
        first_group = [row["advantage"] for row in rows if row["group"] == 0]
        assert sum(first_group) == pytest.approx(0, abs=1e-5)


def test_actual_tool_errors_are_traced():
    trace = LocalTools().execute("divide", {"left": 2, "right": 0})
    assert trace["result"] is None
    assert trace["error"]["type"] == "ZeroDivisionError"
    assert trace["arguments"]["right"] == 0


def test_cancellation_retains_checkpoint(tmp_path):
    rows = []
    result = execute_rl(spec(), tmp_path, rows.append, lambda: len(rows) == 1)
    assert result["qualification"]["status"] == "cancelled"
    assert load_policy(tmp_path / "policy.pt")[1]["step"] == 1


def test_missing_offline_transitions_and_optional_torchrl_are_explicit(tmp_path):
    with pytest.raises(ValueError, match="require retained"):
        execute_rl(spec("dqn"), tmp_path, lambda row: None, lambda: False)
    try:
        import tensordict  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="tensordict"):
            transition_tensordict([])


@pytest.mark.parametrize("algorithm", ["ppo", "grpo", "ppo_tool_bandit", "dqn", "behavioral_cloning"])
def test_policy_resume_matches_uninterrupted_updates(tmp_path, algorithm):
    extra = {}
    if algorithm in {"dqn", "behavioral_cloning"}:
        extra["transitions"] = [{"observation": [0.5, 0, 0], "action": 0, "reward": -0.4,
            "next_observation": [0.4, -0.1, 0], "done": False},
            {"observation": [-0.5, 0, 0], "action": 2, "reward": -0.4,
            "next_observation": [-0.4, 0.1, 0], "done": True}]
    execute_rl(spec(algorithm, steps=4, **extra), tmp_path / "whole", lambda row: None, lambda: False)
    execute_rl(spec(algorithm, steps=2, **extra), tmp_path / "first", lambda row: None, lambda: False)
    result = execute_rl(spec(algorithm, steps=4, resume_from=str(tmp_path / "first/policy.pt"), **extra),
        tmp_path / "resumed", lambda row: None, lambda: False)
    expected = load_policy(tmp_path / "whole/policy.pt")[1]["model"]
    actual = load_policy(tmp_path / "resumed/policy.pt")[1]["model"]
    assert all(torch.equal(expected[key], value) for key, value in actual.items())
    assert [row["step"] for row in result["records"]] == [3, 4]


def test_policy_resume_rejects_changed_reward(tmp_path):
    execute_rl(spec(steps=1), tmp_path / "first", lambda row: None, lambda: False)
    with pytest.raises(ValueError, match="does not match"):
        execute_rl(spec(steps=3, reward={"distance_weight": 2}, resume_from=str(tmp_path / "first/policy.pt")),
            tmp_path / "bad", lambda row: None, lambda: False)


def test_tool_errors_reach_retained_training_trace(tmp_path):
    result = execute_rl(spec("grpo", steps=1, batch_size=64,
        tasks=[{"left": 2, "right": 0, "operation": "add"}]), tmp_path, lambda row: None, lambda: False)
    assert result["metrics"]["tool_errors"] > 0
    rows = [json.loads(line) for line in (tmp_path / "transitions.jsonl").read_text().splitlines()]
    errors = [row for row in rows if row["tool"]["error"]]
    assert all(row["tool"]["name"] == "divide" for row in errors)
    assert all(row["evaluation"]["tool_error"] and row["reward"] < 0 for row in errors)


def test_nested_policy_dimensions_are_honored_and_strategy_conflicts_reject(tmp_path):
    experiment = spec(steps=1)
    experiment["definition"]["parameters"].pop("hidden_size")
    experiment["definition"]["learning"]["architecture"] = {"hidden_size": 12, "layers": 3}
    execute_rl(experiment, tmp_path, lambda row: None, lambda: False)
    model, state = load_policy(tmp_path / "policy.pt")
    assert model.body[0].out_features == 12
    assert sum(isinstance(layer, torch.nn.Linear) for layer in model.body) == 3
    assert state["architecture"]["layers"] == 3
    experiment["definition"]["parameters"]["hidden_size"] = 8
    with pytest.raises(ValueError, match="conflicting"):
        execute_rl(experiment, tmp_path / "conflict", lambda row: None, lambda: False)
    experiment["definition"]["learning"]["update_strategy"] = "lora"
    with pytest.raises(ValueError, match="unsupported update_strategy"):
        execute_rl(experiment, tmp_path / "strategy", lambda row: None, lambda: False)
