"""Autoregressive token-level PPO/GRPO for a constrained local tool-call language.

The scratch language model samples tool and argument tokens conditionally on all
preceding tokens. Grammar masks are explicit; tools execute only after a complete
call has been parsed. This is a small DSL language-RL fixture, not unrestricted
natural-language agent training or a TRL trainer wrapper.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Callable

from .compatibility import resolve_role
from .learning import artifact, build_model
from .rl import LocalTools, _policy_version, clipped_policy_loss


class CallLanguage:
    tokens = ["<pad>", "TASK", "CALL", "(", ",", ")", "=>", "END"] + list(LocalTools.names) + [str(value) for value in range(-16, 17)]

    def __init__(self, tools=None):
        self.tools = list(LocalTools.names if tools is None else tools)
        if not self.tools or len(set(self.tools)) != len(self.tools) or any(tool not in LocalTools.names for tool in self.tools):
            raise ValueError("tool whitelist must select distinct local arithmetic tools")
        self.ids = {token: index for index, token in enumerate(self.tokens)}

    def encode(self, tokens):
        return [self.ids[token] for token in tokens]

    def decode(self, tokens):
        return " ".join(self.tokens[index] for index in tokens)

    def prompt(self, task):
        return self.encode(["TASK", task["operation"], "(", str(task["left"]), ",", str(task["right"]), ")", "=>"])

    def grammar(self, task):
        arguments = sorted(set([0, task["left"], task["right"]]))
        return [self.encode(["CALL"]), self.encode(self.tools), self.encode(["("]), self.encode([str(value) for value in arguments]),
                self.encode([","]), self.encode([str(value) for value in arguments]), self.encode([")"]), self.encode(["END"])]

    def parse(self, text):
        match = re.fullmatch(r"CALL ([a-z]+) \( (-?\d+) , (-?\d+) \) END", text)
        if not match or match[1] not in self.tools:
            raise ValueError("call does not match the whitelisted tool grammar")
        left, right = int(match[2]), int(match[3])
        if not -16 <= left <= 16 or not -16 <= right <= 16:
            raise ValueError("tool arguments are outside the language vocabulary")
        return match[1], {"left": left, "right": right}


def evaluate_call(task: dict, trace: dict) -> dict:
    """Held-out evaluator computes the requested operation, independent of policy/reward."""
    left, right = task["left"], task["right"]
    if task["operation"] == "add":
        expected = left + right
    elif task["operation"] == "subtract":
        expected = left - right
    elif task["operation"] == "multiply":
        expected = left * right
    else:
        expected = left / right
    correct = trace["error"] is None and math.isclose(trace["result"], expected, abs_tol=1e-6)
    arguments_correct = trace["arguments"] == {"left": left, "right": right}
    return {"expected": expected, "actual": trace["result"], "correct": correct,
            "arguments_correct": arguments_correct, "tool_error": trace["error"] is not None}


def _reward(evaluation, weights):
    parts = {"outcome": weights["outcome"] * float(evaluation["correct"]),
             "argument_fidelity": weights["argument_fidelity"] * float(evaluation["arguments_correct"]),
             "tool_error": -weights["tool_error"] * float(evaluation["tool_error"])}
    return parts, sum(parts.values())


def _validate_tasks(tasks, language):
    if not isinstance(tasks, list) or not tasks or len(tasks) > 1000:
        raise ValueError("training and held-out task lists must contain 1..1000 tasks")
    for task in tasks:
        if task.get("operation") not in language.tools:
            raise ValueError("task operation must be in the tool whitelist")
        if any(type(task.get(name)) is not int or not -16 <= task[name] <= 16 for name in ("left", "right")):
            raise ValueError("tool tasks require integer operands in [-16, 16]")
        if task["operation"] == "divide" and task["right"] == 0:
            raise ValueError("requested division task cannot have zero divisor")


def _masked(logits, allowed):
    import torch
    mask = torch.ones_like(logits, dtype=torch.bool)
    mask[..., allowed] = False
    return logits.masked_fill(mask, torch.finfo(logits.dtype).min)


def sample_call(model, value_head, language: CallLanguage, task: dict, generator=None, greedy=False):
    import torch
    prompt = language.prompt(task)
    grammar = language.grammar(task)
    generated, logs, values = [], [], []
    with torch.no_grad():
        for allowed in grammar:
            logits = model(torch.tensor([prompt + generated]))[0, -1]
            log_probs = _masked(logits, allowed).log_softmax(-1)
            token = int(log_probs.argmax()) if greedy else int(torch.multinomial(log_probs.exp(), 1, generator=generator))
            generated.append(token)
            logs.append(float(log_probs[token]))
            values.append(float(value_head(logits).squeeze()))
    text = language.decode(generated)
    name, arguments = language.parse(text)
    tool = LocalTools().execute(name, arguments)
    return {"task": dict(task), "prompt_token_ids": prompt, "generated_token_ids": generated,
            "generated_text": text, "generation_log_probs": logs, "generation_values": values,
            "allowed_token_ids": grammar, "sampled_token_mask": [len(allowed) > 1 for allowed in grammar],
            "tool": tool, "evaluation": evaluate_call(task, tool)}


def token_statistics(model, value_head, row):
    """Teacher-force exactly the sampled prefix and its original grammar masks."""
    import torch
    ids = row["prompt_token_ids"] + row["generated_token_ids"][:-1]
    start = len(row["prompt_token_ids"]) - 1
    raw = model(torch.tensor([ids]))[0, start:]
    distributions = torch.stack([_masked(logits, allowed).log_softmax(-1)
                                 for logits, allowed in zip(raw, row["allowed_token_ids"])])
    token_ids = torch.tensor(row["generated_token_ids"])
    chosen = distributions.gather(-1, token_ids[:, None]).squeeze(-1)
    return chosen, distributions, value_head(raw).squeeze(-1)


def load_language_rl_checkpoint(path):
    import torch
    state = torch.load(path, map_location="cpu", weights_only=True)
    if state.get("format") != "tinystudio.language_rl.v1" or state["tokens"] != CallLanguage.tokens:
        raise ValueError("unsupported token-level language RL checkpoint")
    model = build_model(state["architecture"], len(CallLanguage.tokens))
    value_head = torch.nn.Linear(len(CallLanguage.tokens), 1).float()
    model.load_state_dict(state["model"])
    value_head.load_state_dict(state["value_head"])
    model.eval()
    value_head.eval()
    return model, value_head, CallLanguage(state["tools"]), state


def generate_tool_call(path, task):
    model, value, language, state = load_language_rl_checkpoint(path)
    _validate_tasks([task], language)
    row = sample_call(model, value, language, task, greedy=True)
    row["policy_version"] = _policy_version(model)
    return row


def execute_language_rl(spec: dict, output_dir: Path, emit: Callable[[dict], None], cancelled: Callable[[], bool]) -> dict:
    import torch
    from torch.nn import functional as F
    definition = spec["definition"]
    params = definition.get("parameters", {})
    learning = definition.get("learning") or {}
    if any(key in params for key in {"resume_from", "model_path", "teacher_checkpoint"}):
        raise ValueError("language RL checkpoints must use immutable role inputs")
    from .learning_axes import architecture as requested_architecture, runtime_axes, selector, learning_rate
    axes = runtime_axes(learning, params, strategies={"full", "adam", "full_parameter", "full_finetune"},
        default_strategy="full", initializations={"scratch", "from_scratch", "random", "resume"})
    algorithm = selector(learning.get("objective", params.get("algorithm", "grpo")), "objective")
    if "objective" in learning and "algorithm" in params and algorithm != params["algorithm"]:
        raise ValueError("conflicting objective and algorithm axes")
    if algorithm not in {"ppo", "grpo"}:
        raise ValueError("token-level language RL objective must be ppo or grpo")
    initialization = axes["initialization"]
    family = selector(learning.get("family", params.get("family", "gru")), "family")
    family = {"tiny_transformer": "transformer"}.get(family, family)
    if "family" in learning and "family" in params and family != params["family"]:
        raise ValueError("conflicting family axes")
    if family not in {"gru", "transformer"}:
        raise ValueError("language RL family must be gru or transformer")
    if learning.get("representation") is not None and selector(learning["representation"], "representation") not in {"tool_dsl", "engineering_vocabulary", "state_tokens"}:
        raise ValueError("language RL currently requires its explicit engineering tool-call vocabulary")
    from .learning import evaluator_validation_inputs
    if evaluator_validation_inputs(spec):
        raise ValueError("language RL heldout tasks currently use parameters.heldout_tasks; external validation artifacts are unsupported")
    language = CallLanguage(params.get("tools"))
    training_tasks = params.get("tasks", [{"operation": "add", "left": 2, "right": 3},
        {"operation": "subtract", "left": 7, "right": 4}, {"operation": "multiply", "left": 3, "right": 4},
        {"operation": "divide", "left": 8, "right": 2}])
    held_out = params.get("heldout_tasks", [{"operation": "add", "left": 5, "right": 2},
        {"operation": "subtract", "left": 6, "right": 1}, {"operation": "multiply", "left": 2, "right": 5},
        {"operation": "divide", "left": 9, "right": 3}])
    _validate_tasks(training_tasks, language)
    _validate_tasks(held_out, language)
    identities = lambda tasks: {(task["operation"], task["left"], task["right"]) for task in tasks}
    if identities(training_tasks) & identities(held_out):
        raise ValueError("held-out tool tasks must not overlap training tasks")
    steps, group_size = int(params.get("steps", 8)), int(params.get("group_size", params.get("batch_size", 8)))
    if "group_size" in params and "batch_size" in params and params["group_size"] != params["batch_size"]:
        raise ValueError("conflicting group_size and batch_size parameters")
    fields = {"hidden_size": (16, 4, 256), "layers": (1, 1, 4), "context": (32, 16, 512)}
    if family == "transformer":
        fields["heads"] = (2, 1, 16)
    dimensions = requested_architecture(learning, params, fields)
    epochs, hidden = int(params.get("update_epochs", 2)), dimensions["hidden_size"]
    layers, heads = dimensions["layers"], dimensions.get("heads", 1)
    lr, epsilon = learning_rate(params, 0.003), float(params.get("clip_epsilon", 0.2))
    gamma, kl_weight = float(params.get("gamma", 1)), float(params.get("kl_weight", 0.01))
    if not (1 <= steps <= 10000 and 2 <= group_size <= 128 and 1 <= epochs <= 20 and 4 <= hidden <= 256
            and 1 <= layers <= 4 and 1 <= heads <= 16 and 0 < lr <= 1 and 0 < epsilon < 1
            and 0 <= gamma <= 1 and math.isfinite(kl_weight) and kl_weight >= 0):
        raise ValueError("language RL dimensions or optimizer settings exceed bounds")
    if family == "transformer" and hidden % heads:
        raise ValueError("hidden_size must be divisible by heads")
    architecture = {"family": family, **dimensions}
    seed = int(definition.get("seed", 42))
    weights = {"outcome": 1.0, "argument_fidelity": 0.25, "tool_error": 0.1, **params.get("reward", {})}
    if set(weights) != {"outcome", "argument_fidelity", "tool_error"} or any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in weights.values()):
        raise ValueError("reward weights must be supported finite nonnegative values")
    config = {"learning": {key: value for key, value in learning.items() if key != "initialization"},
              "parameters": {key: value for key, value in params.items() if key != "steps"}, "seed": seed}
    config_digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    resume = resolve_role(spec, "resume", required=False)
    if initialization == "resume" and not resume:
        raise ValueError("resume initialization requires an immutable resume artifact")
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_threads = torch.get_num_threads()
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        torch.set_num_threads(1)
        try:
            model = build_model(architecture, len(language.tokens))
            value_head = torch.nn.Linear(len(language.tokens), 1).float()
            reference = build_model(architecture, len(language.tokens))
            reference.load_state_dict(model.state_dict())
            reference.eval()
            optimizer = torch.optim.Adam(list(model.parameters()) + list(value_head.parameters()), lr=lr)
            generator = torch.Generator().manual_seed(seed + 11)
            start = 0
            if resume:
                model, value_head, language, saved = load_language_rl_checkpoint(resume["path"])
                if (saved["config_sha256"] != config_digest or saved["architecture"] != architecture
                    or saved["algorithm"] != algorithm or saved["torch_version"] != str(torch.__version__) or saved["backend"] != "cpu"):
                    raise ValueError("resume language policy does not match configuration or backend")
                reference.load_state_dict(saved["reference_model"])
                optimizer = torch.optim.Adam(list(model.parameters()) + list(value_head.parameters()), lr=lr)
                optimizer.load_state_dict(saved["optimizer"])
                generator.set_state(saved["sampler_rng"])
                torch.set_rng_state(saved["torch_rng"])
                start = saved["step"]
                if steps <= start:
                    raise ValueError("resume steps must exceed checkpoint step")
            initial_version = _policy_version(model)
            def evaluate():
                model.eval()
                return [{**sample_call(model, value_head, language, task, greedy=True),
                         "policy_version": _policy_version(model), "partition": "heldout"} for task in held_out]
            initial_evaluation = evaluate()
            trajectories, updates, completed = [], [], start
            for update in range(start, steps):
                if cancelled():
                    break
                model.eval()  # No dropout: collection and update probabilities share exact semantics.
                task = training_tasks[update % len(training_tasks)]
                version = _policy_version(model)
                batch = []
                for member in range(group_size):
                    row = sample_call(model, value_head, language, task, generator)
                    components, reward = _reward(row["evaluation"], weights)
                    row.update(policy_version=version, group=update, member=member, reward=reward,
                               reward_components=components, partition="train")
                    batch.append(row)
                rewards = torch.tensor([row["reward"] for row in batch])
                group_advantages = (rewards - rewards.mean()) / rewards.std(unbiased=False).clamp_min(1e-6)
                losses, policy_losses, value_losses, kls = [], [], [], []
                for _ in range(epochs):
                    optimizer.zero_grad(set_to_none=True)
                    sequence_losses, policy_terms, value_terms, kl_terms = [], [], [], []
                    for index, row in enumerate(batch):
                        chosen, distribution, values = token_statistics(model, value_head, row)
                        with torch.no_grad():
                            _, ref_distribution, _ = token_statistics(reference, value_head, row)
                        old = torch.tensor(row["generation_log_probs"])
                        if algorithm == "grpo":
                            advantages = group_advantages[index].expand_as(chosen)
                            value_loss = values.sum() * 0
                        else:
                            returns = torch.tensor([gamma ** (len(chosen) - 1 - token) * row["reward"] for token in range(len(chosen))])
                            advantages = returns - torch.tensor(row["generation_values"])
                            value_loss = 0.5 * F.mse_loss(values, returns)
                        policy_loss = clipped_policy_loss(chosen, old, advantages, epsilon)
                        kl = (distribution.exp() * (distribution - ref_distribution)).sum(-1).mean()
                        sequence_losses.append(policy_loss + value_loss + kl_weight * kl)
                        policy_terms.append(policy_loss)
                        value_terms.append(value_loss)
                        kl_terms.append(kl)
                        row["advantages"] = advantages.tolist()
                    loss = torch.stack(sequence_losses).mean()
                    if not torch.isfinite(loss):
                        raise ValueError("token-level language RL produced a non-finite loss")
                    loss.backward()
                    gradient_norm = float(torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0))
                    torch.nn.utils.clip_grad_norm_(value_head.parameters(), 1.0)
                    optimizer.step()
                    losses.append(float(loss.detach()))
                    policy_losses.append(float(torch.stack(policy_terms).mean().detach()))
                    value_losses.append(float(torch.stack(value_terms).mean().detach()))
                    kls.append(float(torch.stack(kl_terms).mean().detach()))
                completed = update + 1
                trajectories.extend(batch)
                record = {"step": completed, "loss": losses[-1], "policy_loss": policy_losses[-1],
                    "value_loss": value_losses[-1], "reference_kl": kls[-1], "model_gradient_norm": gradient_norm,
                    "mean_reward": float(rewards.mean()), "reward_std": float(rewards.std(unbiased=False)),
                    "policy_version_before": version, "policy_version_after": _policy_version(model),
                    "generated_tokens": sum(len(row["generated_token_ids"]) for row in batch),
                    "sampled_decision_tokens": sum(sum(row["sampled_token_mask"]) for row in batch)}
                updates.append(record)
                emit({"type": "metric", **record})
            final_evaluation = evaluate()
            state = {"format": "tinystudio.language_rl.v1", "algorithm": algorithm, "architecture": architecture,
                "tokens": language.tokens, "tools": language.tools, "model": model.state_dict(), "value_head": value_head.state_dict(),
                "reference_model": reference.state_dict(), "optimizer": optimizer.state_dict(), "torch_rng": torch.get_rng_state(),
                "sampler_rng": generator.get_state(), "seed": seed, "step": completed, "configuration": config,
                "config_sha256": config_digest, "backend": "cpu", "torch_version": str(torch.__version__)}
            checkpoint = output_dir / "language-policy.pt"
            torch.save(state, checkpoint)
            expected = sample_call(model, value_head, language, held_out[0], greedy=True)
            final_version = _policy_version(model)
            del model, value_head, optimizer
            restored, restored_value, restored_language, _ = load_language_rl_checkpoint(checkpoint)
            actual = sample_call(restored, restored_value, restored_language, held_out[0], greedy=True)
            reload_equal = (expected["generated_token_ids"] == actual["generated_token_ids"]
                            and expected["generation_log_probs"] == actual["generation_log_probs"])
            trace_path = output_dir / "language-trajectories.jsonl"
            trace_path.write_text("".join(json.dumps(row) + "\n" for row in trajectories))
            evaluation_path = output_dir / "heldout-evaluation.json"
            evaluation_path.write_text(json.dumps({"initial": initial_evaluation, "final": final_evaluation,
                "evaluation_policy": "independent requested-operation correctness; rewards not used to optimize heldout tasks"}, indent=2))
            updates_path = output_dir / "updates.json"
            updates_path.write_text(json.dumps(updates, indent=2))
            return {"records": updates, "metrics": {"steps": completed, "parameters_changed": initial_version != final_version,
                "reload_generation_equal": reload_equal, "trajectories": len(trajectories),
                "sampled_decision_tokens": sum(sum(row["sampled_token_mask"]) for row in trajectories),
                "heldout_accuracy": sum(row["evaluation"]["correct"] for row in final_evaluation) / len(final_evaluation),
                "initial_heldout_accuracy": sum(row["evaluation"]["correct"] for row in initial_evaluation) / len(initial_evaluation),
                "training_tasks": len(training_tasks), "heldout_tasks": len(held_out),
                "tool_calls": len(trajectories), "tool_errors": sum(row["tool"]["error"] is not None for row in trajectories)},
                "artifacts": [artifact(checkpoint, "language_policy_checkpoint", "application/octet-stream"),
                    artifact(trace_path, "token_tool_trajectories", "application/x-ndjson"),
                    artifact(evaluation_path, "heldout_tool_evaluation"), artifact(updates_path, "policy_updates")],
                "qualification": {"status": "completed" if completed == steps else "cancelled", "algorithm": algorithm,
                    "token_level_autoregressive_policy": True, "grammar": "CALL <whitelisted-tool> ( <integer> , <integer> ) END",
                    "constraints": "tool and argument positions sampled when their allowed set has multiple values; arguments restricted to requested operands and zero; syntax forced",
                    "loss_variant": "clipped per-token ratios, mean per completion, exact masked-policy/reference KL",
                    "advantage": "group mean/std normalized completion reward" if algorithm == "grpo" else "discounted terminal reward minus learned token-state value",
                    "backend": "cpu", "dtype": "float32", "saved_policy_reloaded": reload_equal, "heldout_tasks_used_for_updates": False,
                    "scope": "scratch constrained tool-call DSL; one local arithmetic invocation per completion; no unrestricted natural-language or network tools",
                    "resume": "exact identical CPU backend/configuration at group-update boundaries"}}
        finally:
            torch.set_num_threads(previous_threads)
