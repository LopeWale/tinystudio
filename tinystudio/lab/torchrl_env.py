"""Optional native EnvBase adapter for TinyStudio's real control fixture.

Importing this module does not require TorchRL. Construction imports the optional
runtime and returns a genuine EnvBase with checked TensorDict/spec contracts.
"""
from __future__ import annotations

from .rl import ControlEnvironment, ControlReward


def make_control_env(seed=42, target=0.0, horizon=24, agents=1, distance_weight=1.0, action_cost=0.01):
    from .policy_environment import control_configuration, validate_checkpoint
    configuration = control_configuration(seed, target, horizon, agents, distance_weight, action_cost)
    try:
        import torch
        from tensordict import TensorDict
        from torchrl.data import Categorical, Composite, Unbounded
        from torchrl.envs import EnvBase
    except ImportError as cause:
        raise RuntimeError("native control EnvBase requires optional torchrl and tensordict packages") from cause
    if not 1 <= int(agents) <= 8:
        raise ValueError("agents must be between one and eight")
    agent_count = int(agents)

    class TinyStudioControlEnv(EnvBase):
        def __init__(self):
            super().__init__(device="cpu", batch_size=torch.Size([]), run_type_checks=True)
            self._configuration = dict(configuration)
            self._core = ControlEnvironment(seed, target, horizon, agent_count, ControlReward(distance_weight, action_cost))
            self.observation_spec = Composite(observation=Unbounded(shape=(3,), dtype=torch.float64),
                                              tracking_error=Unbounded(shape=(1,), dtype=torch.float64))
            self.action_spec = Categorical(3, shape=(1,), dtype=torch.int64)
            self.reward_spec = Unbounded(shape=(1,), dtype=torch.float64)
            done = lambda shape: Categorical(2, shape=shape, dtype=torch.bool)
            self.done_spec = Composite(done=done((1,)), terminated=done((1,)), truncated=done((1,)))
            if agent_count > 1:
                self.observation_spec = Composite(observation=Unbounded(shape=(3,), dtype=torch.float64),
                    tracking_error=Unbounded(shape=(1,), dtype=torch.float64),
                    agents=Composite(observation=Unbounded(shape=(agent_count, 3), dtype=torch.float64), shape=(agent_count,)))
                self.action_spec = Composite(agents=Composite(action=Categorical(3, shape=(agent_count, 1), dtype=torch.int64), shape=(agent_count,)))
                self.reward_spec = Composite(reward=Unbounded(shape=(1,), dtype=torch.float64),
                    agents=Composite(reward=Unbounded(shape=(agent_count, 1), dtype=torch.float64), shape=(agent_count,)))
                self.done_spec = Composite(done=done((1,)), terminated=done((1,)), truncated=done((1,)),
                    agents=Composite(done=done((agent_count, 1)), terminated=done((agent_count, 1)),
                                     truncated=done((agent_count, 1)), shape=(agent_count,)))

        def _set_seed(self, value):
            self._configuration["seed"] = value
            self._core = ControlEnvironment(value, target, horizon, agent_count, ControlReward(distance_weight, action_cost))

        def _output(self, observation, outcome=None):
            value = torch.tensor(observation, dtype=torch.float64)
            done = bool(outcome and outcome["done"])
            terminated = bool(outcome and outcome["terminated"])
            truncated = bool(outcome and outcome["truncated"])
            result = {"observation": value, "tracking_error": torch.tensor([abs(observation[0] - observation[2])], dtype=torch.float64),
                      "done": torch.tensor([done]), "terminated": torch.tensor([terminated]), "truncated": torch.tensor([truncated])}
            if outcome is not None:
                result["reward"] = torch.tensor([outcome["reward"]], dtype=torch.float64)
            if agent_count > 1:
                nested = {"observation": value.repeat(agent_count, 1),
                          "done": torch.full((agent_count, 1), done),
                          "terminated": torch.full((agent_count, 1), terminated),
                          "truncated": torch.full((agent_count, 1), truncated)}
                if outcome is not None:
                    nested["reward"] = torch.full((agent_count, 1), outcome["reward"], dtype=torch.float64)
                result["agents"] = TensorDict(nested, batch_size=[agent_count])
            return TensorDict(result, batch_size=[])

        def checkpoint_state(self):
            from copy import deepcopy
            return {"configuration": dict(self._configuration), "core": deepcopy(self._core.state()),
                    "observation": self._core.observation()}

        def restore_checkpoint(self, snapshot):
            snapshot = validate_checkpoint(snapshot, self._configuration)
            self._core.restore(snapshot["core"])
            return self._output(snapshot["observation"], {"done": snapshot["core"]["done"],
                "terminated": abs(snapshot["core"]["position"]) > 2,
                "truncated": snapshot["core"]["steps"] >= horizon and abs(snapshot["core"]["position"]) <= 2, "reward": 0.0})

        def _reset(self, tensordict=None, **kwargs):
            if kwargs.get("set_state"):
                raise ValueError("arbitrary TensorDict state injection is not implemented by this stateful control adapter")
            return self._output(self._core.reset())

        def _step(self, tensordict):
            action = tensordict.get(("agents", "action") if agent_count > 1 else "action")
            if action.dtype != torch.int64 or tuple(action.shape) != ((agent_count, 1) if agent_count > 1 else (1,)):
                raise ValueError("control actions must match the declared int64 TensorSpec")
            outcome = self._core.step(action.reshape(-1).tolist())
            return self._output(outcome["observation"], outcome)

    return TinyStudioControlEnv()
