"""Owned learner environment sessions, with an explicitly selected optional runtime."""
from __future__ import annotations

from copy import deepcopy
import math
import random

from .rl import ControlEnvironment, ControlReward


def control_configuration(seed=42, target=0.0, horizon=24, agents=1, distance_weight=1.0, action_cost=0.01):
    if type(seed) is not int:
        raise ValueError("control seed must be an integer")
    for name, value in (("horizon", horizon), ("agents", agents)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
            raise ValueError(f"{name} must be an integer")
    reward = ControlReward(distance_weight, action_cost)
    core = ControlEnvironment(seed, target, horizon, agents, reward)
    return {"seed": seed, "target": core.target, "horizon": core.horizon, "agents": core.agents,
            "distance_weight": reward.distance_weight, "action_cost": reward.action_cost}


def validate_checkpoint(snapshot, configuration):
    """Validate a complete snapshot before touching the owned runtime."""
    if not isinstance(snapshot, dict) or set(snapshot) != {"configuration", "core", "observation"}:
        raise ValueError("invalid control environment checkpoint")
    if snapshot["configuration"] != configuration:
        raise ValueError("control environment checkpoint configuration mismatch")
    core = snapshot["core"]
    if not isinstance(core, dict) or set(core) != {"position", "velocity", "steps", "done", "random"}:
        raise ValueError("invalid control environment checkpoint state")
    for key, bound in (("position", 2.5), ("velocity", 0.5)):
        value = core[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > bound:
            raise ValueError(f"invalid control checkpoint {key}")
    steps, done = core["steps"], core["done"]
    if type(steps) is not int or not 0 <= steps <= configuration["horizon"] or type(done) is not bool:
        raise ValueError("invalid control checkpoint steps or done")
    if steps and done != (abs(core["position"]) > 2 or steps >= configuration["horizon"]):
        raise ValueError("inconsistent control checkpoint termination")
    if steps == 0 and (core["velocity"] != 0 or abs(core["position"]) > 1):
        raise ValueError("invalid initial control checkpoint state")
    observation = snapshot["observation"]
    if observation != [core["position"], core["velocity"], configuration["target"]]:
        raise ValueError("control checkpoint observation does not match state")
    rng = core["random"]
    if (not isinstance(rng, tuple) or len(rng) != 3 or
            (rng[2] is not None and (not isinstance(rng[2], (int, float)) or not math.isfinite(rng[2])))):
        raise ValueError("invalid control checkpoint random state")
    try:
        random.Random().setstate(rng)
    except (ValueError, TypeError, OverflowError) as cause:
        raise ValueError("invalid control checkpoint random state") from cause
    return deepcopy(snapshot)


class PolicyEnvironment:
    """Own a native core or genuine EnvBase for the lifetime of a learner/evaluator."""
    action_count = 3
    observation_size = 3

    def __init__(self, configuration, runtime="native"):
        self._configuration = dict(configuration)
        self._runtime = runtime
        self._closed = False
        self._current = None
        if runtime == "native":
            self._environment = ControlEnvironment(configuration["seed"], self.target, self.horizon, self.agents,
                ControlReward(configuration["distance_weight"], configuration["action_cost"]))
        elif runtime == "torchrl":
            from .torchrl_env import make_control_env
            self._environment = make_control_env(**configuration)
        else:
            raise ValueError("unsupported control environment runtime")

    @property
    def runtime(self):
        return self._runtime

    @property
    def agents(self):
        return self._configuration["agents"]

    @property
    def horizon(self):
        return self._configuration["horizon"]

    @property
    def target(self):
        return self._configuration["target"]

    @property
    def closed(self):
        return self._closed

    def _require_open(self):
        if self._closed:
            raise ValueError("control environment session is closed")

    def reset(self):
        self._require_open()
        try:
            result = self._environment.reset()
            if self.runtime == "native":
                return result
            self._current = result
            return result["observation"].tolist()
        except Exception:
            self.close()
            raise

    def step(self, actions):
        self._require_open()
        try:
            if isinstance(actions, int):
                actions = [actions]
            if len(actions) != self.agents or any(type(action) is not int or action not in range(3) for action in actions):
                raise ValueError("one discrete action in [0, 2] is required for every agent")
            if self.runtime == "native":
                return self._environment.step(actions)
            if self._current is None:
                raise ValueError("reset is required before stepping")
            import torch
            # EnvBase.step gives an existing next precedence over freshly computed values.
            current = self._current.exclude("next").clone()
            key = ("agents", "action") if self.agents > 1 else "action"
            shape = (self.agents, 1) if self.agents > 1 else (1,)
            current.set(key, torch.tensor(actions, dtype=torch.int64).reshape(shape))
            result = self._environment.step(current)["next"]
            self._current = result
            observation, reward = result["observation"].tolist(), result["reward"].item()
            error = result["tracking_error"].item()
            return {"observation": observation, "reward": reward, "done": result["done"].item(),
                    "terminated": result["terminated"].item(), "truncated": result["truncated"].item(),
                    "evaluation": {"absolute_tracking_error": error, "within_tolerance": error < 0.1},
                    "agents": {str(index): {"action": action, "reward": reward} for index, action in enumerate(actions)}}
        except Exception:
            self.close()
            raise

    def state(self):
        self._require_open()
        if self.runtime == "torchrl":
            return self._environment.checkpoint_state()
        return {"configuration": dict(self._configuration), "core": deepcopy(self._environment.state()),
                "observation": self._environment.observation()}

    def restore(self, snapshot):
        self._require_open()
        try:
            snapshot = validate_checkpoint(snapshot, self._configuration)
            if self.runtime == "torchrl":
                self._current = self._environment.restore_checkpoint(snapshot)
            else:
                self._environment.restore(snapshot["core"])
        except Exception:
            self.close()
            raise

    def close(self):
        if not self._closed:
            self._closed = True
            self._current = None
            if self.runtime == "torchrl":
                self._environment.close()

    def __enter__(self):
        self._require_open()
        return self

    def __exit__(self, *exc):
        self.close()


def environment_runtime(parameters=None):
    """Validate the selector without constructing or importing an optional runtime."""
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        raise ValueError("environment parameters must be an object")
    selector = parameters.get("environment", {"kind": "control", "runtime": "native"})
    if not isinstance(selector, dict) or set(selector) - {"kind", "runtime"}:
        raise ValueError("environment must select kind control and runtime native or torchrl")
    if selector.get("kind", "control") != "control" or selector.get("runtime", "native") not in ("native", "torchrl"):
        raise ValueError("unsupported environment kind or runtime")
    return selector.get("runtime", "native")


def create_policy_environment(parameters=None, *, seed=42, agents=1):
    runtime = environment_runtime(parameters)
    parameters = parameters or {}
    reward = parameters.get("reward", {})
    if not isinstance(reward, dict):
        raise ValueError("control reward must be an object")
    configuration = control_configuration(seed, parameters.get("target", 0), parameters.get("horizon", 24), agents,
                                          reward.get("distance_weight", 1), reward.get("action_cost", 0.01))
    return PolicyEnvironment(configuration, runtime)
