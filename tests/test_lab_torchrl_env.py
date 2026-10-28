"""Optional native EnvBase contract checks; executable in an isolated runtime."""
import importlib.util

import pytest

from tinystudio.lab.torchrl_env import make_control_env


AVAILABLE = bool(importlib.util.find_spec("torchrl") and importlib.util.find_spec("tensordict"))


def test_optional_dependency_boundary_is_explicit():
    if not AVAILABLE:
        with pytest.raises(RuntimeError, match="optional torchrl"):
            make_control_env()
    else:
        environment = make_control_env()
        assert environment.device.type == "cpu"
        environment.close()


@pytest.mark.skipif(not AVAILABLE, reason="optional TorchRL/TensorDict not installed in this runtime")
@pytest.mark.parametrize("agents", [1, 2])
def test_native_envbase_specs_rollout_and_real_dynamics(agents):
    import torch
    from torchrl.envs import EnvBase
    from torchrl.envs.utils import check_env_specs
    environment = make_control_env(seed=5, horizon=4, agents=agents)
    assert isinstance(environment, EnvBase)
    check_env_specs(environment)
    observation = environment.reset()
    initial_position = float(observation["observation"][0])
    if agents == 1:
        observation.set("action", torch.tensor([2]))
    else:
        observation.set(("agents", "action"), torch.full((agents, 1), 2, dtype=torch.int64))
    next_state = environment.step(observation)["next"]
    assert float(next_state["observation"][1]) == pytest.approx(0.1)
    assert float(next_state["observation"][0]) == pytest.approx(initial_position + 0.1)
    assert next_state["reward"].shape == (1,)
    assert next_state["done"].dtype == torch.bool
    if agents > 1:
        assert next_state["agents", "observation"].shape == (agents, 3)
        assert next_state["agents", "reward"].shape == (agents, 1)
    environment.set_seed(14)
    first = environment.reset()["observation"].clone()
    environment.set_seed(14)
    second = environment.reset()["observation"].clone()
    assert torch.equal(first, second)
    rollout = environment.rollout(3)
    assert rollout.batch_size == torch.Size([3])
    assert rollout["next", "observation"].shape == (3, 3)
    environment.close()
