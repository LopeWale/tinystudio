"""Actual learner/evaluator TorchRL selection, checkpoint replay, and admission."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import torch

from tinystudio.lab.rl import execute_rl, load_policy

AVAILABLE = bool(importlib.util.find_spec("torchrl") and importlib.util.find_spec("tensordict"))


def experiment(objective="ppo", agents=1, steps=2, resume=None):
    parameters = {"steps": steps, "batch_size": 4, "hidden_size": 8, "horizon": 4, "agents": agents,
        "environment": {"kind": "control", "runtime": "torchrl"}, "evaluation": {"seeds": [1009]}}
    if objective in {"dqn", "imitation"}:
        parameters["transitions"] = [
            {"observation": [0.5, 0, 0], "action": 0, "reward": -0.4, "next_observation": [0.4, -0.1, 0], "done": False},
            {"observation": [-0.5, 0, 0], "action": 2, "reward": -0.4, "next_observation": [-0.4, 0.1, 0], "done": True}]
    value = {"definition": {"seed": 18, "learning": {"objective": objective}, "parameters": parameters}}
    if resume:
        value["definition"]["learning"]["initialization"] = "resume"
        value["definition"]["inputs"] = [{"artifact_id": "resume", "role": "resume", "sha256": hashlib.sha256(Path(resume).read_bytes()).hexdigest()}]
        value["input_paths"] = {"resume": str(resume)}
    return value


@pytest.mark.skipif(not AVAILABLE, reason="optional TorchRL runtime unavailable")
@pytest.mark.parametrize("objective,agents", [("ppo", 1), ("ppo", 2), ("dqn", 1), ("imitation", 1)])
def test_actual_learner_and_heldout_evaluation_use_public_envbase_steps(tmp_path, monkeypatch, objective, agents):
    from torchrl.envs import EnvBase
    original = EnvBase.step
    calls = []

    def step(environment, td):
        assert "next" not in td.keys()
        actions = td["agents", "action"] if agents > 1 else td["action"]
        assert tuple(actions.shape) == ((agents, 1) if agents > 1 else (1,))
        calls.append(id(environment))
        return original(environment, td)

    monkeypatch.setattr(EnvBase, "step", step)
    result = execute_rl(experiment(objective, agents), tmp_path, lambda row: None, lambda: False)
    assert calls, "selected TorchRL must execute real public EnvBase.step"
    assert result["qualification"]["environment_runtime"] == "torchrl"
    assert result["metrics"]["parameters_changed"]
    evaluation = json.loads((tmp_path / "control-evaluation.json").read_text())
    assert evaluation["format"] == "tinystudio.control-evaluation.v1"
    assert evaluation["qualification"]["comparison_complete"]
    assert evaluation["qualification"]["weights_unchanged"]
    assert evaluation["metrics"]["paired_initial_states"]
    assert result["baseline_comparison"]["metrics"] == evaluation["metrics"]
    assert {row["arm"] for row in evaluation["records"]} == {"policy", "baseline"}
    assert {row["seed"] for row in evaluation["records"]} == {1009}
    assert all(row["absolute_tracking_error"] == abs(row["next_observation"][0] - row["next_observation"][2]) for row in evaluation["records"])
    state = load_policy(tmp_path / "policy.pt")[1]
    assert set(state["environment"]) == {"configuration", "core", "observation"}
    assert state["environment"]["configuration"]["agents"] == agents
    assert state["environment"]["observation"] == state["observation"]
    if objective == "ppo":
        rows = [json.loads(line) for line in (tmp_path / "transitions.jsonl").read_text().splitlines()]
        assert all(len(row["joint_action"]) == agents for row in rows)
        assert len(calls) > 8, "held-out arms must add public steps beyond eight learner ticks"
    else:
        assert len(calls) >= 2, "offline/imitation must actually evaluate both held-out arms"


@pytest.mark.skipif(not AVAILABLE, reason="optional TorchRL runtime unavailable")
@pytest.mark.parametrize("objective,agents", [("ppo", 1), ("ppo", 2), ("dqn", 1), ("imitation", 1)])
def test_torchrl_selected_resume_matches_uninterrupted_tensors_and_environment(tmp_path, objective, agents):
    execute_rl(experiment(objective, agents, steps=3), tmp_path / "whole", lambda row: None, lambda: False)
    execute_rl(experiment(objective, agents, steps=1), tmp_path / "first", lambda row: None, lambda: False)
    execute_rl(experiment(objective, agents, steps=3, resume=tmp_path / "first/policy.pt"), tmp_path / "resumed", lambda row: None, lambda: False)
    expected = load_policy(tmp_path / "whole/policy.pt")[1]
    actual = load_policy(tmp_path / "resumed/policy.pt")[1]
    assert expected["environment"] == actual["environment"]
    assert expected["observation"] == actual["observation"]
    assert all(torch.equal(expected["model"][key], tensor) for key, tensor in actual["model"].items())
    assert torch.equal(expected["torch_rng"], actual["torch_rng"])
    assert torch.equal(expected["sampler_rng"], actual["sampler_rng"])


def test_preflight_missing_selected_optional_target_dependencies_is_explicit():
    from tinystudio.lab.contracts import ExperimentDefinition
    from tinystudio.lab.preflight import execution_checks
    definition = ExperimentDefinition(name="TorchRL selection", engine="learning.control_rl", execution={"profile": "trusted_native"}, **experiment()["definition"])
    checks = execution_checks(definition, {"runtime": {"torch": str(torch.__version__)}})
    selection = next(row for row in checks if row["code"] == "policy_selection")
    assert not selection["passed"]
    assert "requires torchrl and tensordict" in selection["message"]
    available = execution_checks(definition, {"runtime": {"torch": str(torch.__version__), "torchrl": "0.14", "tensordict": "0.14"}})
    assert next(row for row in available if row["code"] == "policy_selection")["passed"]


@pytest.mark.parametrize("runtime", ["native", "torchrl"])
def test_resume_rejects_outer_observation_conflicting_with_environment(tmp_path, runtime):
    if runtime == "torchrl" and not AVAILABLE:
        pytest.skip("optional TorchRL runtime unavailable")
    initial = experiment(steps=1)
    initial["definition"]["parameters"]["environment"]["runtime"] = runtime
    execute_rl(initial, tmp_path / "first", lambda row: None, lambda: False)
    checkpoint = tmp_path / "first/policy.pt"
    state = torch.load(checkpoint, weights_only=True)
    state["observation"] = [state["observation"][0] + 0.25, *state["observation"][1:]]
    torch.save(state, checkpoint)
    resumed = experiment(steps=2, resume=checkpoint)
    resumed["definition"]["parameters"]["environment"]["runtime"] = runtime
    with pytest.raises(ValueError, match="observation.*environment"):
        execute_rl(resumed, tmp_path / "resumed", lambda row: None, lambda: False)
    assert not (tmp_path / "resumed/policy.pt").exists()
