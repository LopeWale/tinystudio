"""Learner sessions must preserve real control transitions and checkpoint replay."""
from copy import deepcopy
import importlib.util
import subprocess
import sys

import pytest

from tinystudio.lab.policy_environment import create_policy_environment, environment_runtime

AVAILABLE = bool(importlib.util.find_spec("torchrl") and importlib.util.find_spec("tensordict"))


def session(runtime="native", agents=1, **parameters):
    return create_policy_environment({"environment": {"kind": "control", "runtime": runtime}, **parameters}, seed=5, agents=agents)


def test_native_import_does_not_import_optional_runtime():
    subprocess.run([sys.executable, "-c", "from tinystudio.lab.policy_environment import create_policy_environment; "
        "import sys; e=create_policy_environment(); e.reset(); e.step([2]); e.close(); "
        "assert 'torchrl' not in sys.modules and 'tensordict' not in sys.modules"], check=True)


@pytest.mark.parametrize("selector", [None, "custom", {"kind": "custom"}, {"runtime": "custom"}, {"path": "env.py"}, {"runtime": []}])
def test_selector_rejects_unsupported_environment(selector):
    with pytest.raises(ValueError, match="environment"):
        create_policy_environment({"environment": selector})


def test_native_transition_equation_and_lifecycle():
    with session(horizon=2) as environment:
        initial = environment.reset()
        outcome = environment.step([2])
        assert outcome["observation"] == [initial[0] + 0.1, 0.1, 0.0]
        assert outcome["reward"] == -abs(initial[0] + 0.1) - 0.01
        assert not outcome["done"]
        assert environment.step([1])["truncated"]
    assert environment.closed
    with pytest.raises(ValueError, match="closed"):
        environment.reset()


@pytest.mark.parametrize("runtime", ["native", pytest.param("torchrl", marks=pytest.mark.skipif(not AVAILABLE, reason="optional TorchRL runtime unavailable"))])
def test_checkpoint_exact_replay_and_next_reset_rng(runtime):
    with session(runtime, agents=2) as environment:
        environment.reset()
        environment.step([2, 1])
        checkpoint = environment.state()
        expected = [environment.step(actions) for actions in ([0, 1], [2, 2], [1, 0])]
        expected_reset = environment.reset()
        environment.restore(checkpoint)
        assert [environment.step(actions) for actions in ([0, 1], [2, 2], [1, 0])] == expected
        assert environment.reset() == expected_reset
        checkpoint["core"]["position"] = 900
        assert environment.state()["core"]["position"] != 900


@pytest.mark.parametrize("runtime", ["native", pytest.param("torchrl", marks=pytest.mark.skipif(not AVAILABLE, reason="optional TorchRL runtime unavailable"))])
@pytest.mark.parametrize("change", ["configuration", "nan", "position", "velocity", "steps", "done", "observation", "random"])
def test_invalid_restore_does_not_mutate_and_closes(change, runtime):
    environment = session(runtime)
    environment.reset()
    valid = environment.state()
    invalid = deepcopy(valid)
    if change == "configuration":
        invalid["configuration"]["horizon"] += 1
    elif change == "nan":
        invalid["core"]["position"] = float("nan")
    elif change in {"position", "velocity", "steps"}:
        invalid["core"][change] = 10001
    elif change == "done":
        invalid["core"]["done"] = 1
    elif change == "observation":
        invalid["observation"] = [0, 0, 0]
    else:
        invalid["core"]["random"] = (1, 2)
    core = environment._environment
    before = core.state() if runtime == "native" else core.checkpoint_state()
    with pytest.raises(ValueError):
        environment.restore(invalid)
    assert environment.closed
    assert (core.state() if runtime == "native" else core.checkpoint_state()) == before
    if runtime == "torchrl":
        assert core.is_closed


@pytest.mark.parametrize("runtime", ["native", pytest.param("torchrl", marks=pytest.mark.skipif(not AVAILABLE, reason="optional TorchRL runtime unavailable"))])
def test_invalid_step_closes_session(runtime):
    environment = session(runtime)
    environment.reset()
    with pytest.raises(ValueError, match="discrete action"):
        environment.step([9])
    assert environment.closed
    if runtime == "torchrl":
        assert environment._environment.is_closed


@pytest.mark.skipif(not AVAILABLE, reason="optional TorchRL runtime unavailable")
@pytest.mark.parametrize("agents", [1, 2, 8])
def test_real_envbase_runtime_exact_native_parity_and_checkpoint(agents):
    with session(agents=agents, horizon=4) as native, session("torchrl", agents=agents, horizon=4) as wrapped:
        assert native.reset() == wrapped.reset()
        for action in [2, 0, 1, 2]:
            assert native.step([action] * agents) == wrapped.step([action] * agents)
            assert native.state() == wrapped.state()
        native.restore(wrapped.state())
        assert native.reset() == wrapped.reset()


@pytest.mark.skipif(not AVAILABLE, reason="optional TorchRL runtime unavailable")
def test_envbase_public_methods_are_called_and_stale_next_is_discarded(monkeypatch):
    import torch
    from torchrl.envs import EnvBase
    calls = []
    public_step, public_reset = EnvBase.step, EnvBase.reset

    def step(self, td):
        calls.append("step")
        assert "next" not in td.keys()
        return public_step(self, td)

    def reset(self, *args, **kwargs):
        calls.append("reset")
        return public_reset(self, *args, **kwargs)

    monkeypatch.setattr(EnvBase, "step", step)
    monkeypatch.setattr(EnvBase, "reset", reset)
    with session("torchrl") as environment:
        initial = environment.reset()
        environment._current.set("next", environment._current.clone())
        outcome = environment.step([2])
        assert outcome["observation"][0] == initial[0] + 0.1
        assert calls == ["reset", "step"]
        assert environment._current["observation"].dtype == torch.float64


def test_selected_optional_dependency_failure_is_explicit(monkeypatch):
    import builtins
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "torchrl.data":
            raise ImportError("controlled missing optional runtime")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(RuntimeError, match="optional torchrl"):
        session("torchrl")


def test_preflight_selector_validation_is_pure(monkeypatch):
    import builtins
    original = builtins.__import__

    def no_optional(name, *args, **kwargs):
        if name in {"torch", "torchrl", "tensordict"}:
            raise AssertionError("selector validation must not load optional runtime")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_optional)
    assert environment_runtime({}) == "native"
    assert environment_runtime({"environment": {"kind": "control", "runtime": "torchrl"}}) == "torchrl"
    with pytest.raises(ValueError, match="environment"):
        environment_runtime({"environment": {"kind": "custom"}})


@pytest.mark.parametrize("seed", [True, "5", 5.5, None])
def test_invalid_seed_rejected_before_runtime_construction(seed):
    with pytest.raises(ValueError, match="seed"):
        create_policy_environment(seed=seed)


@pytest.mark.parametrize("reward", [None, [], "custom"])
def test_invalid_reward_object_rejected(reward):
    with pytest.raises(ValueError, match="reward"):
        create_policy_environment({"reward": reward})
