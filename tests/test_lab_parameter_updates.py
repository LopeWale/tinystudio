"""Qualify objective-owned updates using actual learners, not change flags.

Forecast/fusion losses must train every encoder/core and prediction head. PPO
also trains its value estimator; DQN and imitation have no critic objective.
Token GRPO uses group-relative advantages and has no learned value objective.
References are inference-only, except DQN's explicitly scheduled target copy.
"""
import gc
import weakref

import pytest
import torch

from tinystudio.lab import language_rl, rl, timeseries
from test_lab_language_rl import spec as language_spec
from test_lab_rl import spec as control_spec
from test_lab_timeseries import multimodal_spec, spec as forecast_spec


def snapshot(model):
    return {name: value.detach().clone() for name, value in model.named_parameters()}


def assert_updated(before, after, groups):
    for group in groups:
        names = [name for name in before if name.startswith(group + ".")]
        assert names, f"missing intended parameter group: {group}"
        assert any(not torch.equal(before[name], after[name]) for name in names), f"no update in intended group: {group}"


def assert_fixed(before, after):
    assert before.keys() == after.keys()
    assert all(torch.equal(value, after[name]) for name, value in before.items())


class TrainingObserver:
    """Observe real factory/Adam calls without substituting training behavior."""

    def __init__(self, monkeypatch, module, factory):
        self.models = []
        self.optimizers = []
        original_factory = getattr(module, factory)
        original_adam = torch.optim.Adam
        self.adam_class = original_adam

        def build(*args, **kwargs):
            model = original_factory(*args, **kwargs)
            self.models.append((model, snapshot(model)))
            return model

        def adam(*args, **kwargs):
            optimizer = original_adam(*args, **kwargs)
            self.optimizers.append(optimizer)
            return optimizer

        monkeypatch.setattr(module, factory, build)
        monkeypatch.setattr(torch.optim, "Adam", adam)

    def assert_optimizer(self, intended, excluded=(), unused=()):
        owned = [parameter for group in self.optimizers[0].param_groups for parameter in group["params"]]
        ids = [id(parameter) for parameter in owned]
        assert len(ids) == len(set(ids)), "duplicate optimizer ownership"
        required = {id(parameter) for parameter in intended}
        permitted = required | {id(parameter) for parameter in unused}
        assert required <= set(ids), "optimizer omitted objective-trained parameters"
        assert set(ids) <= permitted, "optimizer owns parameters outside policy and allowed unused heads"
        assert set(ids).isdisjoint({id(parameter) for parameter in excluded})


@pytest.mark.parametrize("family", ["mlp", "gru", "transformer"])
def test_forecast_updates_every_intended_group_and_reconstructs_inference(tmp_path, monkeypatch, family):
    observed = TrainingObserver(monkeypatch, timeseries, "_checkpoint_model")
    timeseries.execute_timeseries(forecast_spec(family, steps=3), tmp_path, lambda row: None, lambda: False)
    trained, before = observed.models[0]
    groups = ["core", "head"] + (["embedding", "position"] if family == "transformer" else [])
    assert_updated(before, snapshot(trained), groups)
    observed.assert_optimizer(trained.parameters())
    checkpoint = tmp_path / "model.pt"
    restored, state = timeseries.load_research_checkpoint(checkpoint)
    assert restored is not trained
    assert_fixed(snapshot(trained), snapshot(restored))
    probe = torch.tensor([[[0.0], [0.1], [0.2], [0.3]]])
    trained.eval()
    with torch.no_grad():
        expected = trained((probe - state["normalization"]["mean"]) / state["normalization"]["std"]).tolist()
    observed.models.clear()
    observed.optimizers.clear()
    del trained, restored
    gc.collect()
    assert timeseries.predict_from_checkpoint(checkpoint, probe.tolist()) == expected


@pytest.mark.parametrize("objective", ["regression", "classification"])
def test_fusion_updates_each_modality_encoder_and_head(tmp_path, monkeypatch, objective):
    observed = TrainingObserver(monkeypatch, timeseries, "_checkpoint_model")
    experiment = multimodal_spec(objective)
    experiment["definition"]["parameters"]["steps"] = 3
    timeseries.execute_multimodal(experiment, tmp_path, lambda row: None, lambda: False)
    trained, before = observed.models[0]
    assert_updated(before, snapshot(trained), ["encoders.numeric", "encoders.text", "encoders.image", "encoders.audio", "head"])
    observed.assert_optimizer(trained.parameters())
    checkpoint = tmp_path / "model.pt"
    restored, state = timeseries.load_research_checkpoint(checkpoint)
    assert_fixed(snapshot(trained), snapshot(restored))
    rows = experiment["definition"]["parameters"]["records"][:2]
    features = [timeseries._modality_features(row, ["numeric", "text", "image", "audio"]) for row in rows]
    inputs = {name: (torch.tensor([row[name] for row in features]) - values["mean"]) / values["std"]
              for name, values in state["normalization"].items()}
    trained.eval()
    with torch.no_grad():
        logits = trained(inputs)
    expected = [state["classes"][index] for index in logits.argmax(-1).tolist()] if objective == "classification" else logits.tolist()
    observed.models.clear()
    observed.optimizers.clear()
    del trained, restored
    gc.collect()
    assert timeseries.predict_from_checkpoint(checkpoint, rows) == expected


def offline_transitions():
    return [{"observation": [0.5, 0, 0], "action": 0, "reward": -0.4,
             "next_observation": [0.4, -0.1, 0], "done": False},
            {"observation": [-0.5, 0, 0], "action": 2, "reward": -0.4,
             "next_observation": [-0.4, 0.1, 0], "done": True}]


@pytest.mark.parametrize("objective", ["ppo", "dqn", "imitation"])
def test_control_updates_objective_groups_and_leaves_unused_critic_fixed(tmp_path, monkeypatch, objective):
    observed = TrainingObserver(monkeypatch, rl, "_policy")
    options = {"steps": 1, "update_epochs": 2, "horizon": 4,
               "evaluation": {"seeds": [1009]}, "target_update_every": 2}
    if objective != "ppo":
        options["transitions"] = offline_transitions()
    rl.execute_rl(control_spec(objective, **options), tmp_path, lambda row: None, lambda: False)
    trained, before = observed.models[0]
    reference = observed.models[1][0]
    after = snapshot(trained)
    assert_updated(before, after, ["body", "actor"] + (["value"] if objective == "ppo" else []))
    # The target/reference begins as an exact policy copy, and one update must
    # not cross DQN's two-update target synchronization boundary.
    assert_fixed(before, snapshot(reference))
    assert all(parameter.grad is None for parameter in reference.parameters())
    if objective != "ppo":
        assert_fixed({name: value for name, value in before.items() if name.startswith("value.")},
                     {name: value for name, value in after.items() if name.startswith("value.")})
    intended = [parameter for name, parameter in trained.named_parameters() if objective == "ppo" or not name.startswith("value.")]
    unused = [] if objective == "ppo" else list(trained.value.parameters())
    observed.assert_optimizer(intended, reference.parameters(), unused)
    for parameter in unused:
        assert parameter.grad is None
        assert parameter not in observed.optimizers[0].state
    restored, _ = rl.load_policy(tmp_path / "policy.pt")
    assert_fixed(after, snapshot(restored))
    probe = torch.tensor([[0.3, -0.1, 0.0]])
    with torch.no_grad():
        expected = tuple(tensor.clone() for tensor in trained(probe))
    observed.models.clear()
    observed.optimizers.clear()
    del trained, reference, restored
    gc.collect()
    restored, _ = rl.load_policy(tmp_path / "policy.pt")
    with torch.no_grad():
        assert all(torch.equal(value, actual) for value, actual in zip(expected, restored(probe)))


def test_dqn_target_changes_only_at_scheduled_copy(tmp_path, monkeypatch):
    observed = TrainingObserver(monkeypatch, rl, "_policy")
    per_update = []

    def emit(row):
        trained, initial = observed.models[0]
        reference = observed.models[1][0]
        per_update.append((snapshot(trained), snapshot(reference), initial))

    rl.execute_rl(control_spec("dqn", transitions=offline_transitions(), steps=3,
                  target_update_every=2, evaluation={"seeds": [1009]}), tmp_path, emit, lambda: False)
    assert len(per_update) == 3
    assert_fixed(per_update[0][2], per_update[0][1])
    assert_fixed(per_update[1][0], per_update[1][1])
    assert_updated(per_update[0][1], per_update[1][1], ["body", "actor"])
    assert_fixed(per_update[1][1], per_update[2][1])
    assert_updated(per_update[1][0], per_update[2][0], ["body", "actor"])


@pytest.mark.parametrize("objective", ["ppo_tool_bandit", "grpo_tool_bandit"])
def test_categorical_tool_policy_updates_objective_groups_and_reconstructs_inference(tmp_path, monkeypatch, objective):
    observed = TrainingObserver(monkeypatch, rl, "_policy")
    rl.execute_rl(control_spec(objective, steps=3, batch_size=32, update_epochs=2,
                  evaluation={"seeds": [1009]}), tmp_path, lambda row: None, lambda: False)
    trained, before = observed.models[0]
    reference = observed.models[1][0]
    after = snapshot(trained)
    # PPO fits tool rewards with a learned baseline. Group-relative GRPO trains
    # its categorical policy without a value loss or reference-policy updates.
    uses_value = objective == "ppo_tool_bandit"
    assert_updated(before, after, ["body", "actor"] + (["value"] if uses_value else []))
    assert_fixed(before, snapshot(reference))
    assert all(parameter.grad is None for parameter in reference.parameters())
    intended = [parameter for name, parameter in trained.named_parameters()
                if uses_value or not name.startswith("value.")]
    unused = [] if uses_value else list(trained.value.parameters())
    observed.assert_optimizer(intended, reference.parameters(), unused)
    optimizer = observed.optimizers[0]
    # Membership alone cannot establish that Adam actually trained the groups.
    assert all(parameter in optimizer.state for parameter in intended)
    assert all(parameter not in optimizer.state for parameter in reference.parameters())
    if not uses_value:
        assert_fixed({name: value for name, value in before.items() if name.startswith("value.")},
                     {name: value for name, value in after.items() if name.startswith("value.")})
        for parameter in unused:
            assert parameter.grad is None
            assert parameter not in optimizer.state

    checkpoint = tmp_path / "policy.pt"
    restored, state = rl.load_policy(checkpoint)
    assert restored is not trained
    assert state["algorithm"] == objective
    assert state["architecture"]["observation_size"] == 6
    assert state["architecture"]["actions"] == 4
    assert_fixed(after, snapshot(restored))
    # Numeric operands followed by add/subtract/multiply/divide indicators:
    # cover every categorical operation, including an unseen operand pair.
    probe = torch.tensor([[0.2, 0.3, 1, 0, 0, 0], [0.7, 0.4, 0, 1, 0, 0],
                          [0.3, 0.4, 0, 0, 1, 0], [-0.6, 0.2, 0, 0, 0, 1]])
    trained.eval()
    with torch.no_grad():
        expected_logits, expected_values = (value.clone() for value in trained(probe))
    assert expected_logits.shape == (4, 4)
    assert expected_values.shape == (4,)
    live_models = [weakref.ref(model) for model in (trained, reference, restored)]
    observed.models.clear()
    observed.optimizers.clear()
    del trained, reference, restored, optimizer, intended, unused
    gc.collect()
    assert all(model() is None for model in live_models), "live policy survived unload"
    restored, _ = rl.load_policy(checkpoint)
    with torch.no_grad():
        actual_logits, actual_values = restored(probe)
    assert torch.equal(expected_logits, actual_logits)
    assert torch.equal(expected_values, actual_values)
    assert torch.equal(expected_logits.argmax(-1), actual_logits.argmax(-1))


@pytest.mark.parametrize("objective,family", [("ppo", "gru"), ("grpo", "gru"), ("ppo", "transformer"), ("grpo", "transformer")])
def test_token_policy_updates_core_and_heads_with_immutable_reference(tmp_path, monkeypatch, objective, family):
    observed = TrainingObserver(monkeypatch, language_rl, "build_model")
    # Capture the separately constructed value head from the actual optimizer.
    initial_values = {}
    original_step = observed.adam_class.step

    def step(optimizer, *args, **kwargs):
        policy_ids = {id(parameter) for parameter in observed.models[0][0].parameters()}
        for group in optimizer.param_groups:
            for parameter in group["params"]:
                if id(parameter) not in policy_ids and id(parameter) not in initial_values:
                    initial_values[id(parameter)] = parameter.detach().clone()
        return original_step(optimizer, *args, **kwargs)

    monkeypatch.setattr(observed.adam_class, "step", step)
    language_rl.execute_language_rl(language_spec(objective, family), tmp_path, lambda row: None, lambda: False)
    trained, before = observed.models[0]
    reference = observed.models[1][0]
    assert_updated(before, snapshot(trained), ["embedding", "core", "head"] + (["position"] if family == "transformer" else []))
    assert_fixed(before, snapshot(reference))
    assert all(parameter.grad is None for parameter in reference.parameters())
    restored, values, language, _ = language_rl.load_language_rl_checkpoint(tmp_path / "language-policy.pt")
    assert_fixed(snapshot(trained), snapshot(restored))
    policy_ids = {id(parameter) for parameter in trained.parameters()}
    value_parameters = [parameter for group in observed.optimizers[0].param_groups for parameter in group["params"] if id(parameter) not in policy_ids]
    if objective == "ppo":
        assert len(value_parameters) == 2
        assert any(not torch.equal(initial_values[id(parameter)], parameter) for parameter in value_parameters)
        assert all(torch.equal(live, saved) for live, saved in zip(value_parameters, values.parameters()))
    else:
        assert all(torch.equal(initial_values[id(parameter)], parameter) for parameter in value_parameters)
    observed.assert_optimizer([*trained.parameters(), *(value_parameters if objective == "ppo" else [])],
                              reference.parameters(), value_parameters if objective == "grpo" else [])
    if objective == "grpo":
        for parameter in value_parameters:
            assert parameter.grad is None or torch.count_nonzero(parameter.grad) == 0
            moments = observed.optimizers[0].state.get(parameter, {})
            assert all(torch.count_nonzero(moments[key]) == 0 for key in ("exp_avg", "exp_avg_sq") if key in moments)
    task = {"operation": "add", "left": 5, "right": 2}
    trained.eval()
    expected = language_rl.sample_call(trained, values, language, task, greedy=True)
    observed.models.clear()
    observed.optimizers.clear()
    del trained, reference, restored, values
    gc.collect()
    actual = language_rl.generate_tool_call(tmp_path / "language-policy.pt", task)
    assert actual["generated_token_ids"] == expected["generated_token_ids"]
    assert actual["generation_log_probs"] == expected["generation_log_probs"]


def test_update_oracle_rejects_omitted_core_group(tmp_path, monkeypatch):
    observed = TrainingObserver(monkeypatch, timeseries, "_checkpoint_model")
    original_step = observed.adam_class.step

    def omit_core(optimizer, *args, **kwargs):
        # Controlled fault: the prediction head learns while the encoder/core
        # receives no optimizer step. An any-parameter-changed flag still passes.
        for parameter in observed.models[0][0].core.parameters():
            parameter.grad = None
        return original_step(optimizer, *args, **kwargs)

    monkeypatch.setattr(observed.adam_class, "step", omit_core)
    result = timeseries.execute_timeseries(forecast_spec("mlp", steps=3), tmp_path, lambda row: None, lambda: False)
    trained, before = observed.models[0]
    assert result["metrics"]["parameters_changed"]
    assert_updated(before, snapshot(trained), ["head"])
    with pytest.raises(AssertionError, match="no update in intended group: core"):
        assert_updated(before, snapshot(trained), ["core", "head"])


def test_optimizer_oracle_rejects_missing_core_ownership(tmp_path, monkeypatch):
    observed = TrainingObserver(monkeypatch, timeseries, "_checkpoint_model")
    original_adam = torch.optim.Adam

    def head_only(parameters, **kwargs):
        # A real optimizer that silently omits a whole intended group still
        # updates the prediction head and produces a loadable checkpoint.
        return original_adam(observed.models[0][0].head.parameters(), **kwargs)

    monkeypatch.setattr(torch.optim, "Adam", head_only)
    result = timeseries.execute_timeseries(forecast_spec("mlp", steps=3), tmp_path, lambda row: None, lambda: False)
    trained, _ = observed.models[0]
    assert result["metrics"]["parameters_changed"]
    with pytest.raises(AssertionError, match="optimizer omitted objective-trained parameters"):
        observed.assert_optimizer(trained.parameters())
