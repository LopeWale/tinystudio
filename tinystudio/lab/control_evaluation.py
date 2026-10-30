"""Paired held-out control trajectories with reward-independent error measurements.

The conventional baseline is a quantized proportional/derivative controller:
error - 2 * velocity, with proportional gain 1 and a 0.05 neutral deadband.
This comparison qualifies a local fixture run, not scientific superiority.
"""
from __future__ import annotations

import hashlib
import math

from .policy_environment import create_policy_environment, environment_runtime


def evaluation_seeds(parameters, seed):
    """Validate the bounded held-out protocol without importing a tensor runtime."""
    environment_runtime(parameters)
    evaluation = parameters.get("evaluation", {})
    if not isinstance(evaluation, dict) or set(evaluation) - {"seeds"}:
        raise ValueError("control evaluation accepts only an object containing seeds")
    seeds = evaluation.get("seeds", [seed + 1000003, seed + 1000004])
    if (not isinstance(seeds, list) or not 1 <= len(seeds) <= 64 or
            any(type(value) is not int or value < 0 for value in seeds) or len(set(seeds)) != len(seeds)):
        raise ValueError("control evaluation seeds must be one to 64 unique nonnegative integers")
    if seed in seeds:
        raise ValueError("control evaluation seeds must exclude the training seed")
    return list(seeds)


def _weights_hash(model):
    import torch
    digest = hashlib.sha256()
    for name, tensor in sorted(model.state_dict().items()):
        value = tensor.detach().cpu().contiguous()
        digest.update(name.encode())
        digest.update(str(value.dtype).encode())
        digest.update(str(tuple(value.shape)).encode())
        digest.update(value.reshape(-1).view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def baseline_action(observation):
    signal = observation[2] - observation[0] - 2.0 * observation[1]
    return 2 if signal > 0.05 else 0 if signal < -0.05 else 1


def evaluate_control_policy(model, parameters, *, seed, agents, cancelled=lambda: False):
    """Evaluate immutable greedy weights and the PD baseline on paired reset seeds."""
    import torch
    seeds = evaluation_seeds(parameters, seed)
    if "agents" in parameters and parameters["agents"] != agents:
        raise ValueError("control evaluation agent selection differs from training")
    modes = [(module, module.training) for module in model.modules()]
    before = _weights_hash(model)
    records, initial_states = [], {}
    interrupted = False
    try:
        model.eval()
        with torch.no_grad():
            for episode, episode_seed in enumerate(seeds):
                for arm in ("policy", "baseline"):
                    if cancelled():
                        interrupted = True
                        break
                    # Each arm owns a distinct session; no training state or RNG is reused.
                    with create_policy_environment(parameters, seed=episode_seed, agents=agents) as environment:
                        observation = environment.reset()
                        initial_states.setdefault(episode_seed, {})[arm] = list(observation)
                        for step in range(environment.horizon):
                            if cancelled():
                                interrupted = True
                                break
                            if arm == "policy":
                                logits = model(torch.tensor([observation], dtype=torch.float32))[0]
                                if tuple(logits.shape) != (1, 3) or not torch.isfinite(logits).all():
                                    raise ValueError("control evaluation policy must produce three finite action logits")
                                action = int(logits.argmax(dim=-1).item())
                            else:
                                action = baseline_action(observation)
                            actions = [action] * agents
                            outcome = environment.step(actions)
                            next_observation = list(outcome["observation"])
                            position, target = next_observation[0], next_observation[2]
                            if not all(math.isfinite(value) for value in next_observation):
                                raise ValueError("control evaluation observations must be finite")
                            records.append({"episode": episode, "step": step, "arm": arm, "seed": episode_seed,
                                "source": "heldout_environment", "observation": list(observation),
                                "action": action, "joint_action": actions, "next_observation": next_observation,
                                "reward": float(outcome["reward"]), "done": outcome["done"],
                                "terminated": outcome["terminated"], "truncated": outcome["truncated"],
                                "absolute_tracking_error": abs(position - target), "constraint_violation": abs(position) > 2})
                            observation = next_observation
                            if outcome["done"]:
                                break
                    if interrupted:
                        break
                if interrupted:
                    break
        after = _weights_hash(model)
        if before != after:
            raise RuntimeError("control evaluation mutated model weights or buffers")
        paired = sum(set(value) == {"policy", "baseline"} and value["policy"] == value["baseline"]
                     for value in initial_states.values())
        metrics = {}
        for arm in ("policy", "baseline"):
            rows = [row for row in records if row["arm"] == arm]
            metrics[arm] = {"mean_absolute_tracking_error": sum(row["absolute_tracking_error"] for row in rows) / len(rows) if rows else None,
                            "constraint_violations": sum(row["constraint_violation"] for row in rows),
                            "mean_reward": sum(row["reward"] for row in rows) / len(rows) if rows else None,
                            "transitions": len(rows)}
        complete = not interrupted and paired == len(seeds)
        metrics.update(paired_initial_states=paired == len(seeds), paired_episodes=paired)
        return {"records": records, "metrics": metrics, "qualification": {
            "status": "completed" if complete else "cancelled", "comparison_complete": complete,
            "environment_runtime": environment_runtime(parameters), "seeds": seeds,
            "initial_states": [{"seed": value, **observations} for value, observations in initial_states.items()],
            "training_seed": seed, "agents": agents, "policy": "greedy argmax; shared action across agents",
            "baseline": "quantized PD: target - position - 2 * velocity; neutral deadband +/-0.05",
            "weights_sha256_before": before, "weights_sha256_after": after, "weights_unchanged": before == after,
            "scope": "paired seeded local control fixture; no physical, benchmark, or scientific superiority qualification"}}
    finally:
        for module, training in modes:
            module.training = training
