"""Retained, immutable greedy comparisons on bounded local arithmetic tasks."""
from __future__ import annotations

import hashlib
import math
from numbers import Real

OPERATIONS = ("add", "subtract", "multiply", "divide")
DEFAULT_HELDOUT_TASKS = [
    {"operation": "add", "left": 5, "right": 2},
    {"operation": "subtract", "left": 6, "right": 1},
    {"operation": "multiply", "left": 2, "right": 5},
    {"operation": "divide", "left": 9, "right": 3},
]


def task_partitions(parameters, *, language=False):
    """Resolve the same authored/default tasks at admission and execution."""
    training = [
        {"operation": "add", "left": 2, "right": 3},
        {"operation": "subtract", "left": 7, "right": 4},
        {"operation": "multiply", "left": 3, "right": 4},
        {"operation": "divide", "left": 8, "right": 2},
    ]
    if not language:  # Preserve the categorical trainer's existing task order.
        training = [training[index] for index in (0, 3, 1, 2)]
    training = parameters.get("tasks", training)
    heldout = parameters.get("heldout_tasks", [dict(task) for task in DEFAULT_HELDOUT_TASKS])
    validate_tool_tasks(training, heldout)
    if language:
        tools = parameters.get("tools", list(OPERATIONS))
        validate_language_tasks(training, tools)
        validate_language_tasks(heldout, tools)
    return training, heldout


def validate_language_tasks(tasks, tools):
    """Token vocabulary and tool whitelist shared by preflight and inference."""
    if (not isinstance(tools, list) or not tools or
            any(not isinstance(tool, str) or tool not in OPERATIONS for tool in tools) or
            len(set(tools)) != len(tools)):
        raise ValueError("tool whitelist must select distinct local arithmetic tools")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 1000:
        raise ValueError("training and held-out task lists must contain 1..1000 tasks")
    for task in tasks:
        if not isinstance(task, dict) or task.get("operation") not in tools:
            raise ValueError("task operation must be in the tool whitelist")
        if any(type(task.get(name)) is not int or not -16 <= task[name] <= 16 for name in ("left", "right")):
            raise ValueError("tool tasks require integer operands in [-16, 16]")
        if task["operation"] == "divide" and task["right"] == 0:
            raise ValueError("requested division task cannot have zero divisor")


def validate_tool_tasks(training, heldout):
    """Validate authored inputs without changing their representation or ordering."""
    identities = []
    for tasks in (training, heldout):
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 1000:
            raise ValueError("training and held-out task lists must contain 1..1000 tasks")
        keys = []
        for task in tasks:
            if not isinstance(task, dict) or task.get("operation") not in OPERATIONS:
                raise ValueError("unknown task operation")
            for name in ("left", "right"):
                value = task.get(name)
                if isinstance(value, bool) or not isinstance(value, Real) or abs(value) > 1e6 or not math.isfinite(value):
                    raise ValueError("tool tasks require finite numeric nonboolean operands bounded by 1e6")
            if task["operation"] == "divide" and task["right"] == 0:
                raise ValueError("requested division task cannot have zero divisor")
            if not math.isfinite(expected_result(task)):
                raise ValueError("requested tool arithmetic must have a finite outcome")
            keys.append((task["operation"], float(task["left"]), float(task["right"])))
        identities.append(keys)
    if len(set(identities[1])) != len(identities[1]):
        raise ValueError("duplicate held-out tool tasks")
    if set(identities[0]) & set(identities[1]):
        raise ValueError("held-out tool tasks must not overlap training tasks")


def expected_result(task):
    left, right = task["left"], task["right"]
    return {"add": lambda: left + right, "subtract": lambda: left - right,
            "multiply": lambda: left * right, "divide": lambda: left / right}[task["operation"]]()


def _finite_trace(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("tool evaluation contains non-finite trace values")
    if isinstance(value, dict):
        for child in value.values():
            _finite_trace(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _finite_trace(child)


def evaluate_pass(modules, tasks, invoke, cancelled):
    """Execute actual calls; restore modes/RNG and reject mutation even on failure."""
    import torch
    from .rl import _policy_version
    versions = [_policy_version(module) for module in modules]
    modes = [(child, child.training) for module in modules for child in module.modules()]
    rows, interrupted = [], False
    try:
        with torch.random.fork_rng(devices=[]), torch.no_grad():
            for module in modules:
                module.eval()
                if any(not torch.isfinite(value).all() for value in module.state_dict().values()):
                    raise ValueError("tool evaluation model contains non-finite weights")
            for task in tasks:
                if cancelled():
                    interrupted = True
                    break
                row = invoke(dict(task))
                _finite_trace(row)
                if row.get("task") != task:
                    raise ValueError("tool evaluation trace task mismatch")
                tool, evaluation = row["tool"], row["evaluation"]
                if tool["name"] not in OPERATIONS:
                    raise ValueError("tool evaluation selected an unsupported tool")
                arguments = tool.get("arguments", {})
                if set(arguments) != {"left", "right"} or any(isinstance(value, bool) or not isinstance(value, Real)
                        or abs(value) > 1e6 or not math.isfinite(value) for value in arguments.values()):
                    raise ValueError("tool evaluation arguments are inconsistent")
                expected = expected_result(task)
                error, result = tool["error"], tool["result"]
                if error is None:
                    if isinstance(result, bool) or not isinstance(result, Real) or not math.isfinite(result):
                        raise ValueError("tool evaluation result must be finite")
                elif result is not None or not isinstance(error, dict) or not all(isinstance(error.get(k), str) for k in ("type", "message")):
                    raise ValueError("tool evaluation error trace is inconsistent")
                if error is None:
                    try:
                        tool_expected = expected_result({"operation": tool["name"], **arguments})
                    except ZeroDivisionError as cause:
                        raise ValueError("tool evaluation result is inconsistent with invocation") from cause
                    if not math.isclose(result, tool_expected, abs_tol=1e-6):
                        raise ValueError("tool evaluation result is inconsistent with invocation")
                correct = error is None and math.isclose(result, expected, abs_tol=1e-6)
                if (evaluation.get("expected") != expected or evaluation.get("actual") != result
                        or evaluation.get("correct") is not correct or evaluation.get("tool_error") is not (error is not None)):
                    raise ValueError("tool evaluation arithmetic or outcome is inconsistent")
                rows.append({**row, "policy_version": versions[0], "partition": "heldout"})
                if cancelled():
                    interrupted = True
                    break
    finally:
        for child, mode in modes:
            child.training = mode
        if versions != [_policy_version(module) for module in modules]:
            raise ValueError("tool evaluation mutated model weights")
    return rows, interrupted


def categorical_call(model, task):
    import torch
    from .rl import LocalTools, evaluate_tool
    feature = [float(task["left"]) / 10, float(task["right"]) / 10] + [float(task["operation"] == name) for name in OPERATIONS]
    logits, values = model(torch.tensor([feature]))
    if tuple(logits.shape) != (1, 4) or tuple(values.shape) != (1,):
        raise ValueError("tool evaluation policy logits or values have invalid shape")
    if not torch.isfinite(logits).all() or not torch.isfinite(values).all():
        raise ValueError("tool evaluation model produced non-finite logits or values")
    tool = LocalTools().execute(OPERATIONS[int(logits[0].argmax())], {"left": task["left"], "right": task["right"]})
    return {"task": dict(task), "tool": tool, "evaluation": evaluate_tool(task, tool)}


def comparison_report(kind, checkpoint, tasks, initial, final, *, completed, interrupted, reload_equal):
    if not reload_equal:
        raise ValueError("retained tool policy differs from live policy")
    def metrics(rows):
        correct = sum(row["evaluation"]["correct"] for row in rows)
        return {"evaluated": len(rows), "correct": correct,
                "errors": sum(row["evaluation"]["tool_error"] for row in rows),
                "accuracy": correct / len(rows) if rows else None}
    complete = completed and not interrupted and len(initial) == len(final) == len(tasks)
    return {"format": "tinystudio.tool-evaluation.v1", "policy_kind": kind,
            "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            "baseline": "Incoming policy before this attempt's updates (greedy)",
            "records": [{"case_id": f"heldout:{index}", "task": dict(task), "expected": expected_result(task),
                         "initial": initial[index] if index < len(initial) else None,
                         "final": final[index] if index < len(final) else None} for index, task in enumerate(tasks)],
            "metrics": {"cases": len(tasks), "initial": metrics(initial), "final": metrics(final)},
            "qualification": {"status": "completed" if complete else "cancelled", "comparison_complete": complete,
                              "heldout_tasks_used_for_updates": False, "weights_unchanged": True,
                              "scope": "bounded local arithmetic tool calls; greedy incoming versus retained final policy"}}
