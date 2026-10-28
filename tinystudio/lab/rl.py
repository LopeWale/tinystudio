"""Bounded CPU reinforcement/imitation research with retained real transitions.

PPO is a clipped categorical actor/critic update. Offline RL uses discrete DQN;
GRPO is a one-tool categorical bandit analogue, not a language-model GRPO trainer.
No external network, executable code, or hardware tool is invoked.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from typing import Callable

from .learning import artifact, resolved_inputs


def read_records(spec: dict, key: str = "records") -> list[dict]:
    params = spec["definition"].get("parameters", {})
    rows = list(params.get(key, []))
    for item in resolved_inputs(spec, {"resume", "teacher", "pretrained_model", "embedding_model"}):
        path = Path(item["path"])
        data = path.read_bytes()
        if item.get("sha256") and hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("input checksum mismatch")
        if data.startswith(b"PAR1"):
            import pyarrow as pa
            import pyarrow.parquet as pq
            values = pq.read_table(pa.BufferReader(data)).to_pylist()
        elif path.suffix in {".jsonl", ".ndjson"}:
            values = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
        else:
            try:
                values = json.loads(data)
            except json.JSONDecodeError:
                values = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
            if isinstance(values, dict):
                values = values.get(key, values.get("records", [values]))
        rows.extend(values)
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("records must be JSON objects")
    return rows


def _finite(value, name):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


class ControlReward:
    def __init__(self, distance_weight: float = 1, action_cost: float = 0.01):
        self.distance_weight = _finite(distance_weight, "distance_weight")
        self.action_cost = _finite(action_cost, "action_cost")
        if self.distance_weight < 0 or self.action_cost < 0:
            raise ValueError("reward weights must be nonnegative")

    def __call__(self, position: float, target: float, actions: list[int]) -> float:
        return -self.distance_weight * abs(position - target) - self.action_cost * sum(abs(action - 1) for action in actions)


class ControlEnvironment:
    """Seeded discrete control fixture, including a shared-actuator multiagent mode."""
    action_count = 3
    observation_size = 3

    def __init__(self, seed=42, target=0.0, horizon=24, agents=1, reward=None):
        self._random = random.Random(seed)
        self.target = _finite(target, "target")
        self.horizon, self.agents = int(horizon), int(agents)
        if not 1 <= self.horizon <= 10000 or not 1 <= self.agents <= 8 or abs(self.target) > 2:
            raise ValueError("invalid control horizon, agent count, or target")
        self.reward = reward or ControlReward()
        self._position, self._velocity, self._steps, self._done = 0.0, 0.0, 0, True

    def reset(self):
        self._position = self._random.uniform(-1, 1)
        self._velocity, self._steps, self._done = 0.0, 0, False
        return self.observation()

    def observation(self):
        return [self._position, self._velocity, self.target]

    def step(self, actions):
        if self._done:
            raise ValueError("reset is required before stepping a finished episode")
        if isinstance(actions, int):
            actions = [actions]
        if len(actions) != self.agents or any(type(action) is not int or action not in range(3) for action in actions):
            raise ValueError("one discrete action in [0, 2] is required for every agent")
        self._velocity = 0.8 * self._velocity + 0.1 * sum(action - 1 for action in actions) / self.agents
        self._position += self._velocity
        self._steps += 1
        terminated = abs(self._position) > 2
        truncated = self._steps >= self.horizon and not terminated
        self._done = terminated or truncated
        reward = self.reward(self._position, self.target, actions)
        return {"observation": self.observation(), "reward": reward, "terminated": terminated,
                "truncated": truncated, "done": self._done,
                "evaluation": {"absolute_tracking_error": abs(self._position - self.target),
                               "within_tolerance": abs(self._position - self.target) < 0.1},
                "agents": {str(index): {"action": action, "reward": reward} for index, action in enumerate(actions)}}

    def restore(self, state):
        self._position, self._velocity = state["position"], state["velocity"]
        self._steps, self._done = state["steps"], state["done"]
        self._random.setstate(state["random"])

    def state(self):
        return {"position": self._position, "velocity": self._velocity, "steps": self._steps,
                "done": self._done, "random": self._random.getstate()}


def transition_tensordict(records: list[dict]):
    """Export TorchRL's observation/action/next convention when installed.

    This is a data bridge; the native fixture is not an EnvBase implementation.
    """
    import torch
    try:
        from tensordict import TensorDict
    except ImportError as cause:
        raise RuntimeError("tensordict is required for the optional TorchRL transition bridge") from cause
    return TensorDict({"observation": torch.tensor([row["observation"] for row in records]),
        "action": torch.tensor([row["action"] for row in records]),
        "next": {"observation": torch.tensor([row["next_observation"] for row in records]),
                 "reward": torch.tensor([[row["reward"]] for row in records]),
                 "done": torch.tensor([[row["done"]] for row in records]),
                 "terminated": torch.tensor([[row.get("terminated", row["done"])] for row in records]),
                 "truncated": torch.tensor([[row.get("truncated", False)] for row in records])}}, batch_size=[len(records)])


class LocalTools:
    """Small closed tool registry; results and exceptions are always observable."""
    names = ("add", "subtract", "multiply", "divide")

    def execute(self, name: str, arguments: dict) -> dict:
        try:
            left = _finite(arguments["left"], "left")
            right = _finite(arguments["right"], "right")
            if max(abs(left), abs(right)) > 1e6:
                raise ValueError("tool operands exceed bounds")
            if name == "add":
                result = left + right
            elif name == "subtract":
                result = left - right
            elif name == "multiply":
                result = left * right
            elif name == "divide":
                result = left / right
            else:
                raise ValueError("unknown local tool")
            return {"name": name, "arguments": dict(arguments), "result": result, "error": None}
        except (KeyError, ValueError, ZeroDivisionError, TypeError) as cause:
            return {"name": name, "arguments": dict(arguments), "result": None,
                    "error": {"type": type(cause).__name__, "message": str(cause)}}


def evaluate_tool(task: dict, trace: dict) -> dict:
    """Independent outcome evaluator; does not execute or trust the selected tool."""
    left, right = float(task["left"]), float(task["right"])
    operation = task["operation"]
    expected = {"add": lambda: left + right, "subtract": lambda: left - right,
                "multiply": lambda: left * right, "divide": lambda: left / right}[operation]()
    correct = trace["error"] is None and math.isclose(float(trace["result"]), expected, abs_tol=1e-6)
    return {"correct": correct, "expected": expected, "actual": trace["result"], "tool_error": trace["error"] is not None}


def _policy(architecture):
    from torch import nn
    class Policy(nn.Module):
        def __init__(self):
            super().__init__()
            blocks = [nn.Linear(architecture["observation_size"], architecture["hidden_size"]), nn.Tanh()]
            for _ in range(architecture.get("layers", 1) - 1):
                blocks.extend([nn.Linear(architecture["hidden_size"], architecture["hidden_size"]), nn.Tanh()])
            self.body = nn.Sequential(*blocks)
            self.actor = nn.Linear(architecture["hidden_size"], architecture["actions"])
            self.value = nn.Linear(architecture["hidden_size"], 1)

        def forward(self, observation):
            hidden = self.body(observation)
            return self.actor(hidden), self.value(hidden).squeeze(-1)
    return Policy().float()


def _policy_version(model) -> str:
    checksum = hashlib.sha256()
    for key, value in sorted(model.state_dict().items()):
        checksum.update(key.encode())
        checksum.update(value.detach().cpu().numpy().tobytes())
    return checksum.hexdigest()


def load_policy(path):
    import torch
    state = torch.load(path, map_location="cpu", weights_only=True)
    if state.get("format") != "tinystudio.rl.v1":
        raise ValueError("unsupported policy checkpoint")
    model = _policy(state["architecture"])
    model.load_state_dict(state["model"])
    model.eval()
    return model, state


def clipped_policy_loss(log_prob, old_log_prob, advantages, epsilon=0.2):
    import torch
    ratio = (log_prob - old_log_prob).exp()
    return -torch.minimum(ratio * advantages, ratio.clamp(1 - epsilon, 1 + epsilon) * advantages).mean()


def execute_rl(spec: dict, output_dir: Path, emit: Callable[[dict], None], cancelled: Callable[[], bool]) -> dict:
    import torch
    from torch.nn import functional as F
    from torch.distributions import Categorical

    definition = spec["definition"]
    params = dict(definition.get("parameters", {}))
    if "resume_from" in params:
        raise ValueError("checkpoint sources must use immutable input artifact roles")
    from .compatibility import resolve_role
    resume_binding = resolve_role(spec, "resume", required=False)
    if resume_binding:
        params["resume_from"] = resume_binding["path"]
    learning = definition.get("learning") or {}
    from .learning_axes import architecture as requested_architecture, runtime_axes, selector, learning_rate
    axes = runtime_axes(learning, params, strategies={"full", "adam", "full_parameter", "full_finetune"},
        default_strategy="full", initializations={"scratch", "from_scratch", "random", "resume"})
    objective = selector(learning.get("objective", params.get("algorithm", "ppo")), "objective")
    aliases = {"reinforcement_learning": "ppo", "imitation": "behavioral_cloning", "bc": "behavioral_cloning",
               "grpo": "grpo_tool_bandit", "tool_grpo": "grpo_tool_bandit", "offline_rl": "dqn"}
    algorithm = aliases.get(objective, objective)
    if "objective" in learning and "algorithm" in params and aliases.get(params["algorithm"], params["algorithm"]) != algorithm:
        raise ValueError("conflicting objective and algorithm axes")
    requested_family = learning.get("family", params.get("family", "mlp"))
    if selector(requested_family, "family") != "mlp":
        raise ValueError("control/tool policies currently support family=mlp only")
    if "family" in learning and "family" in params and learning["family"] != params["family"]:
        raise ValueError("conflicting family axes")
    if learning.get("representation") is not None and selector(learning["representation"], "representation") not in {"numeric", "state_vector"}:
        raise ValueError("control/tool policies require numeric state vectors")
    if algorithm not in {"ppo", "dqn", "behavioral_cloning", "grpo_tool_bandit", "ppo_tool_bandit"}:
        raise ValueError(f"unsupported RL objective {objective!r}")
    tools_mode = algorithm in {"grpo_tool_bandit", "ppo_tool_bandit"}
    offline = algorithm in {"dqn", "behavioral_cloning"}
    iterations, batch_size = int(params.get("steps", 8)), int(params.get("batch_size", 16))
    dimensions = requested_architecture(learning, params, {"hidden_size": (16, 4, 512), "layers": (1, 1, 8)})
    hidden = dimensions["hidden_size"]
    agents = int(params.get("agents", 1))
    epochs = int(params.get("update_epochs", 2))
    if not (1 <= iterations <= 10000 and 2 <= batch_size <= 4096 and 4 <= hidden <= 512 and 1 <= epochs <= 20):
        raise ValueError("RL steps, batch size, hidden size or update epochs exceed bounds")
    if not 1 <= agents <= 8:
        raise ValueError("agents must be between 1 and 8")
    target_interval = int(params.get("target_update_every", 2))
    if target_interval < 1:
        raise ValueError("target_update_every must be positive")
    kl_weight = _finite(params.get("kl_weight", 0.01), "kl_weight")
    if kl_weight < 0:
        raise ValueError("kl_weight must be nonnegative")
    if tools_mode and agents != 1:
        raise ValueError("tool bandit supports one agent per independent task")
    gamma, epsilon = float(params.get("gamma", 0.95)), float(params.get("clip_epsilon", 0.2))
    lr = learning_rate(params, 0.003)
    if not 0 <= gamma <= 1 or not 0 < epsilon < 1 or not 0 < lr <= 1:
        raise ValueError("invalid discount, clipping epsilon or learning rate")
    initialization = axes["initialization"]
    if initialization == "resume" and not params.get("resume_from"):
        raise ValueError("resume initialization requires an immutable resume artifact")
    from .learning import evaluator_validation_inputs
    if evaluator_validation_inputs(spec):
        raise ValueError("control/tool RL does not consume external validation artifacts; use the documented independent environment evaluator")
    architecture = {"observation_size": 6 if tools_mode else 3, "hidden_size": hidden,
                    "layers": dimensions["layers"], "actions": 4 if tools_mode else 3}
    seed = int(definition.get("seed", 42))
    data = read_records(spec, "transitions") if offline else []
    if offline and not data:
        raise ValueError("offline RL and imitation require retained transitions")
    for row in data:
        if len(row.get("observation", [])) != 3 or row.get("action") not in range(3):
            raise ValueError("offline rows require three observation features and action in [0, 2]")
        if not all(math.isfinite(float(value)) for value in row["observation"]):
            raise ValueError("offline observation must be finite")
        if algorithm == "dqn" and (len(row.get("next_observation", [])) != 3 or "reward" not in row or type(row.get("done")) is not bool):
            raise ValueError("DQN requires next_observation, reward and boolean done")
        if algorithm == "dqn" and (not math.isfinite(float(row["reward"])) or not all(math.isfinite(float(value)) for value in row["next_observation"])):
            raise ValueError("offline next observations and rewards must be finite")
    tasks = params.get("tasks", [{"left": 2, "right": 3, "operation": "add"},
        {"left": 8, "right": 2, "operation": "divide"}, {"left": 7, "right": 4, "operation": "subtract"},
        {"left": 3, "right": 4, "operation": "multiply"}])
    if tools_mode:
        if not tasks:
            raise ValueError("tool learning requires tasks")
        for task in tasks:
            if task.get("operation") not in LocalTools.names:
                raise ValueError("unknown task operation")
            _finite(task["left"], "left")
            _finite(task["right"], "right")
            if task["operation"] == "divide" and float(task["right"]) == 0:
                raise ValueError("expected division task must have nonzero divisor")
    configuration = {"learning": {key: value for key, value in learning.items() if key != "initialization"},
        "parameters": {key: value for key, value in params.items() if key not in {"steps", "resume_from"}}, "seed": seed}
    config_digest = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    corpus_digest = hashlib.sha256(json.dumps(data if offline else tasks if tools_mode else [], sort_keys=True).encode()).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_threads = torch.get_num_threads()
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        torch.set_num_threads(1)
        try:
            model = _policy(architecture)
            initial_version = _policy_version(model)
            reference = _policy(architecture)
            reference.load_state_dict(model.state_dict())
            reference.eval()
            optimizer = torch.optim.Adam(model.parameters(), lr=lr)
            generator = torch.Generator().manual_seed(seed + 3)
            reward_options = params.get("reward", {})
            environment = ControlEnvironment(seed, params.get("target", 0), params.get("horizon", 24), agents,
                ControlReward(reward_options.get("distance_weight", 1), reward_options.get("action_cost", 0.01)))
            error_cost = _finite(reward_options.get("error_cost", 0.1), "error_cost")
            if error_cost < 0:
                raise ValueError("error_cost must be nonnegative")
            observation = environment.reset()
            start = 0
            if params.get("resume_from"):
                model, saved = load_policy(params["resume_from"])
                if (saved["algorithm"] != algorithm or saved["architecture"] != architecture
                    or saved["backend"] != "cpu" or saved["torch_version"] != str(torch.__version__)
                    or saved["config_sha256"] != config_digest or saved["corpus_sha256"] != corpus_digest):
                    raise ValueError("resume policy does not match configuration, corpus or backend")
                optimizer = torch.optim.Adam(model.parameters(), lr=lr)
                optimizer.load_state_dict(saved["optimizer"])
                reference.load_state_dict(saved["reference_model"])
                generator.set_state(saved["sampler_rng"])
                environment.restore(saved["environment"])
                observation = saved["observation"]
                torch.set_rng_state(saved["torch_rng"])
                start = saved["step"]
                if iterations <= start:
                    raise ValueError("resume steps must exceed checkpoint step")
                initial_version = _policy_version(model)
            model.train()
            transitions, updates = [], []
            completed = start
            registry = LocalTools()
            for update in range(start, iterations):
                if cancelled():
                    break
                version = _policy_version(model)
                batch = []
                if offline:
                    indices = torch.randint(len(data), (batch_size,), generator=generator).tolist()
                    batch = [{**data[index], "policy_version": version,
                              "behavior_policy_version": data[index].get("policy_version", "unknown"),
                              "source_index": index, "update": update, "source": "offline"} for index in indices]
                elif tools_mode:
                    task = tasks[update % len(tasks)]
                    feature = [float(task["left"]) / 10, float(task["right"]) / 10] + [float(task["operation"] == name) for name in registry.names]
                    with torch.no_grad():
                        logits, values = model(torch.tensor([feature]))
                        probabilities = logits.softmax(-1)[0]
                    for group in range(batch_size):
                        action = int(torch.multinomial(probabilities, 1, generator=generator))
                        trace = registry.execute(registry.names[action], {"left": task["left"], "right": task["right"]})
                        evaluation = evaluate_tool(task, trace)
                        reward = float(evaluation["correct"]) - error_cost * float(evaluation["tool_error"])
                        batch.append({"observation": feature, "action": action, "next_observation": feature,
                            "reward": reward, "done": True, "terminated": True, "truncated": False,
                            "old_log_prob": float(probabilities[action].log()), "old_value": float(values[0]),
                            "policy_version": version, "update": update, "group": update, "member": group,
                            "task": task, "tool": trace, "evaluation": evaluation, "source": "tool_interaction"})
                else:
                    for tick in range(batch_size):
                        with torch.no_grad():
                            logits, value = model(torch.tensor([observation]))
                            probabilities = logits.softmax(-1)[0]
                        actions = torch.multinomial(probabilities, agents, replacement=True, generator=generator).tolist()
                        outcome = environment.step(actions)
                        for agent, action in enumerate(actions):
                            batch.append({"observation": list(observation), "action": action,
                                "joint_action": actions, "agent_id": str(agent), "next_observation": outcome["observation"],
                                "reward": outcome["reward"], "done": outcome["done"], "terminated": outcome["terminated"],
                                "truncated": outcome["truncated"], "evaluation": outcome["evaluation"],
                                "old_log_prob": float(probabilities[action].log()), "old_value": float(value[0]),
                                "policy_version": version, "update": update, "tick": tick, "source": "environment"})
                        observation = environment.reset() if outcome["done"] else outcome["observation"]
                observations = torch.tensor([row["observation"] for row in batch], dtype=torch.float32)
                actions = torch.tensor([row["action"] for row in batch])
                if algorithm == "behavioral_cloning":
                    for _ in range(epochs):
                        optimizer.zero_grad(set_to_none=True)
                        logits, _ = model(observations)
                        loss = F.cross_entropy(logits, actions)
                        loss.backward()
                        optimizer.step()
                elif algorithm == "dqn":
                    rewards = torch.tensor([row["reward"] for row in batch])
                    next_observations = torch.tensor([row["next_observation"] for row in batch], dtype=torch.float32)
                    terminated = torch.tensor([row.get("terminated", row["done"]) for row in batch], dtype=torch.float32)
                    with torch.no_grad():
                        target = rewards + gamma * (1 - terminated) * reference(next_observations)[0].max(-1).values
                    for _ in range(epochs):
                        optimizer.zero_grad(set_to_none=True)
                        predicted = model(observations)[0].gather(1, actions[:, None]).squeeze(1)
                        loss = F.smooth_l1_loss(predicted, target)
                        loss.backward()
                        optimizer.step()
                    if (update + 1) % target_interval == 0:
                        reference.load_state_dict(model.state_dict())
                else:
                    old_log = torch.tensor([row["old_log_prob"] for row in batch])
                    rewards = torch.tensor([row["reward"] for row in batch])
                    old_values = torch.tensor([row["old_value"] for row in batch])
                    # One-step bootstrapped advantages handle shared multiagent transitions without mixing agents.
                    with torch.no_grad():
                        next_observations = torch.tensor([row["next_observation"] for row in batch], dtype=torch.float32)
                        terminated = torch.tensor([row["terminated"] for row in batch], dtype=torch.float32)
                        targets = rewards + gamma * (1 - terminated) * model(next_observations)[1]
                    if algorithm == "grpo_tool_bandit":
                        advantages = (rewards - rewards.mean()) / rewards.std(unbiased=False).clamp_min(1e-6)
                    else:
                        advantages = targets - old_values
                    for row, advantage in zip(batch, advantages.tolist()):
                        row["advantage"] = advantage
                    for _ in range(epochs):
                        optimizer.zero_grad(set_to_none=True)
                        logits, values = model(observations)
                        distribution = Categorical(logits=logits)
                        loss = clipped_policy_loss(distribution.log_prob(actions), old_log, advantages, epsilon)
                        if algorithm == "grpo_tool_bandit":
                            with torch.no_grad():
                                reference_logits = reference(observations)[0]
                            kl = F.kl_div(F.log_softmax(reference_logits, -1), F.softmax(logits, -1), reduction="batchmean")
                            loss = loss + kl_weight * kl
                        else:
                            loss = loss + 0.5 * F.mse_loss(values, targets) - 0.01 * distribution.entropy().mean()
                        if not torch.isfinite(loss):
                            raise ValueError("non-finite RL loss")
                        loss.backward()
                        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                        optimizer.step()
                transitions.extend(batch)
                completed = update + 1
                row = {"step": completed, "loss": float(loss.detach()), "policy_version_before": version,
                    "policy_version_after": _policy_version(model), "transitions": len(batch),
                    "mean_reward": sum(float(item.get("reward", 0)) for item in batch) / len(batch)}
                updates.append(row)
                emit({"type": "metric", **row})
            final_version = _policy_version(model)
            state = {"format": "tinystudio.rl.v1", "algorithm": algorithm, "architecture": architecture,
                "model": model.state_dict(), "optimizer": optimizer.state_dict(), "reference_model": reference.state_dict(),
                "torch_rng": torch.get_rng_state(), "sampler_rng": generator.get_state(), "seed": seed,
                "step": completed, "environment": environment.state(), "observation": observation, "policy_version": final_version,
                "backend": "cpu", "torch_version": str(torch.__version__),
                "configuration": configuration, "config_sha256": config_digest,
                "corpus_sha256": corpus_digest,
                "reward_evaluator_separate": True}
            checkpoint = output_dir / "policy.pt"
            torch.save(state, checkpoint)
            probe = torch.tensor([[0.1] * architecture["observation_size"]])
            model.eval()
            with torch.no_grad():
                expected = model(probe)[0].clone()
            del model, optimizer
            reloaded, _ = load_policy(checkpoint)
            with torch.no_grad():
                reload_equal = torch.equal(expected, reloaded(probe)[0])
            trace_path = output_dir / "transitions.jsonl"
            trace_path.write_text("".join(json.dumps(row) + "\n" for row in transitions))
            update_path = output_dir / "updates.json"
            update_path.write_text(json.dumps(updates, indent=2))
            return {"records": updates, "artifacts": [artifact(checkpoint, "policy_checkpoint", "application/octet-stream"),
                artifact(trace_path, "transitions", "application/x-ndjson"), artifact(update_path, "policy_updates")],
                "metrics": {"steps": completed, "transitions": len(transitions), "parameters_changed": initial_version != final_version,
                    "reload_logits_equal": reload_equal, "policy_version": final_version,
                    "tool_calls": sum("tool" in row for row in transitions),
                    "tool_errors": sum(bool(row.get("tool", {}).get("error")) for row in transitions)},
                "qualification": {"status": "completed" if completed == iterations else "cancelled", "algorithm": algorithm,
                    "backend": "cpu", "dtype": "float32", "offline": offline, "agents": agents, "save_unload_reload": reload_equal,
                    "torchrl": "optional TensorDict data bridge; EnvBase collector integration unqualified",
                    "scope": "one-tool categorical bandit analogue; not token-level LLM GRPO" if tools_mode else "seeded local control fixture; no physical or benchmark performance qualification",
                    "resume": "exact same CPU backend, corpus and configuration at update boundaries"}}
        finally:
            torch.set_num_threads(previous_threads)
