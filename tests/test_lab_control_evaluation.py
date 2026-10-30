"""Independent held-out control measurements, pairing, and immutable inference."""
import random

import pytest
import torch

from tinystudio.lab import control_evaluation as evaluation


class ConstantPolicy(torch.nn.Module):
    def __init__(self, action):
        super().__init__()
        logits = torch.zeros(3)
        logits[action] = 1
        self.register_buffer("logits", logits)

    def forward(self, observation):
        assert not torch.is_grad_enabled()
        return self.logits.repeat(observation.shape[0], 1), torch.zeros(observation.shape[0])


def test_policy_and_baseline_have_paired_heldout_initial_states_and_actual_dynamics():
    model = ConstantPolicy(2)
    result = evaluation.evaluate_control_policy(model, {"horizon": 2, "evaluation": {"seeds": [91]}}, seed=7, agents=2)
    assert model.training
    assert result["qualification"]["comparison_complete"]
    assert result["qualification"]["weights_unchanged"]
    assert result["metrics"]["paired_initial_states"]
    rows = result["records"]
    policy = [row for row in rows if row["arm"] == "policy"]
    baseline = [row for row in rows if row["arm"] == "baseline"]
    initial = random.Random(91).uniform(-1, 1)
    assert policy[0]["observation"] == baseline[0]["observation"] == [initial, 0, 0]
    assert policy[0]["next_observation"] == [initial + 0.1, 0.1, 0]
    assert policy[1]["next_observation"] == pytest.approx([initial + 0.1 + 0.18, 0.18, 0], abs=1e-15)
    assert all(row["joint_action"] == [2, 2] for row in policy)
    assert policy[-1]["done"] and policy[-1]["truncated"] and not policy[-1]["terminated"]
    expected = (abs(initial + 0.1) + abs(initial + 0.1 + 0.18)) / 2
    assert result["metrics"]["policy"]["mean_absolute_tracking_error"] == pytest.approx(expected)
    assert result["metrics"]["policy"]["constraint_violations"] == 0
    assert {row["seed"] for row in rows} == {91}


@pytest.mark.parametrize("observation,action", [([1, 0, 0], 0), ([-1, 0, 0], 2), ([0, 0, 0], 1),
    ([0.1, -0.2, 0], 2), ([-0.1, 0.2, 0], 0), ([0.03, 0, 0], 1)])
def test_conventional_baseline_has_explicit_proportional_damping_and_deadband(observation, action):
    assert evaluation.baseline_action(observation) == action


def test_changed_training_reward_does_not_change_independent_evaluation_metrics():
    model = ConstantPolicy(2)
    params = {"horizon": 3, "evaluation": {"seeds": [88]}}
    normal = evaluation.evaluate_control_policy(model, params, seed=7, agents=1)
    changed = evaluation.evaluate_control_policy(model, {**params, "reward": {"distance_weight": 0, "action_cost": 20}}, seed=7, agents=1)
    for arm in ("policy", "baseline"):
        assert normal["metrics"][arm]["mean_absolute_tracking_error"] == changed["metrics"][arm]["mean_absolute_tracking_error"]
        assert normal["metrics"][arm]["constraint_violations"] == changed["metrics"][arm]["constraint_violations"]
        assert normal["metrics"][arm]["mean_reward"] != changed["metrics"][arm]["mean_reward"]
    assert [row["next_observation"] for row in normal["records"]] == [row["next_observation"] for row in changed["records"]]


def test_independent_metrics_ignore_environment_reward_evaluation_annotation(monkeypatch):
    factory = evaluation.create_policy_environment

    def poisoned(*args, **kwargs):
        environment = factory(*args, **kwargs)
        step = environment.step

        def annotated(actions):
            outcome = step(actions)
            outcome["evaluation"] = {"absolute_tracking_error": 9000, "within_tolerance": True}
            return outcome

        environment.step = annotated
        return environment

    monkeypatch.setattr(evaluation, "create_policy_environment", poisoned)
    result = evaluation.evaluate_control_policy(ConstantPolicy(1), {"horizon": 1, "evaluation": {"seeds": [91]}}, seed=7, agents=1)
    assert result["metrics"]["policy"]["mean_absolute_tracking_error"] == abs(random.Random(91).uniform(-1, 1))


@pytest.mark.parametrize("options", [None, {"seeds": [7]}, {"seeds": [8, 8]}, {"seeds": []},
    {"seeds": [True]}, {"seeds": [-7]}, {"seeds": [1.5]}, {"seeds": "8"}, {"episodes": 2}])
def test_invalid_heldout_protocol_rejected(options):
    with pytest.raises(ValueError, match="evaluation"):
        evaluation.evaluate_control_policy(ConstantPolicy(1), {"evaluation": options}, seed=7, agents=1)


def test_default_seeds_are_distinct_from_training_seed():
    assert evaluation.evaluation_seeds({}, 7) == [1000010, 1000011]


def test_cancellation_returns_no_success_and_closes_owned_environment(monkeypatch):
    factory = evaluation.create_policy_environment
    sessions = []

    def tracked(*args, **kwargs):
        environment = factory(*args, **kwargs)
        sessions.append(environment)
        return environment

    monkeypatch.setattr(evaluation, "create_policy_environment", tracked)
    calls = []

    def cancelled():
        calls.append(True)
        return len(calls) >= 3

    result = evaluation.evaluate_control_policy(ConstantPolicy(1), {"horizon": 4}, seed=7, agents=1, cancelled=cancelled)
    assert result["qualification"]["status"] == "cancelled"
    assert not result["qualification"]["comparison_complete"]
    assert len(result["records"]) == 1
    assert len(sessions) == 1 and sessions[0].closed


def test_model_error_closes_session_and_restores_mode(monkeypatch):
    factory, sessions = evaluation.create_policy_environment, []

    def tracked(*args, **kwargs):
        environment = factory(*args, **kwargs)
        sessions.append(environment)
        return environment

    monkeypatch.setattr(evaluation, "create_policy_environment", tracked)

    class Broken(ConstantPolicy):
        def forward(self, observation):
            raise ValueError("controlled inference failure")

    model = Broken(1)
    with pytest.raises(ValueError, match="controlled inference failure"):
        evaluation.evaluate_control_policy(model, {}, seed=7, agents=1)
    assert model.training
    assert sessions[0].closed


def test_constraint_violation_counts_from_position_boundary():
    result = evaluation.evaluate_control_policy(ConstantPolicy(2), {"horizon": 24, "evaluation": {"seeds": [91]}}, seed=7, agents=1)
    rows = [row for row in result["records"] if row["arm"] == "policy"]
    assert rows[-1]["next_observation"][0] > 2
    assert rows[-1]["terminated"] and not rows[-1]["truncated"]
    assert result["metrics"]["policy"]["constraint_violations"] == 1


def test_mutating_policy_is_detected_and_mode_restored():
    class Mutating(ConstantPolicy):
        def forward(self, observation):
            self.logits.add_(1)
            return super().forward(observation)

    model = Mutating(1)
    with pytest.raises(RuntimeError, match="mutated"):
        evaluation.evaluate_control_policy(model, {"horizon": 1}, seed=7, agents=1)
    assert model.training


def test_paired_arms_own_distinct_sessions_and_close_all_on_success(monkeypatch):
    factory, sessions = evaluation.create_policy_environment, []

    def tracked(*args, **kwargs):
        environment = factory(*args, **kwargs)
        sessions.append(environment)
        return environment

    monkeypatch.setattr(evaluation, "create_policy_environment", tracked)
    result = evaluation.evaluate_control_policy(ConstantPolicy(1), {"horizon": 1}, seed=7, agents=1)
    assert result["qualification"]["comparison_complete"]
    assert len(sessions) == 4
    assert len({id(environment) for environment in sessions}) == 4
    assert all(environment.closed for environment in sessions)
    for pair in result["qualification"]["initial_states"]:
        assert pair["policy"] == pair["baseline"]
