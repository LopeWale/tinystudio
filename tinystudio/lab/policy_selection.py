"""Pure authored selections shared by numeric/policy admission and execution."""
from __future__ import annotations

from .learning_axes import architecture, learning_rate, runtime_axes, selected_axis, selector
from .learning_initialization import validate_initialization_bindings

POLICY_ENGINES = frozenset({"learning.language_rl", "learning.control_rl", "learning.tool_rl",
                            "learning.timeseries", "learning.multimodal"})


def validate_policy_selection(definition, engine=None):
    engine = engine or definition.get("engine")
    if engine not in POLICY_ENGINES:
        raise ValueError("unsupported numeric/policy engine")
    learning, params = definition.get("learning") or {}, definition.get("parameters", {})
    axes = runtime_axes(learning, params, strategies={"full", "adam", "full_parameter", "full_finetune"},
        default_strategy="full", initializations={"scratch", "from_scratch", "random", "resume"})
    language = engine == "learning.language_rl"
    numeric = engine in {"learning.timeseries", "learning.multimodal"}
    multimodal = engine == "learning.multimodal"
    if any(key in params for key in {"model_path", "pretrained_model", "embedding_model", "embedding_model_path"}):
        raise ValueError("model sources must use supported immutable input artifact roles")
    validate_initialization_bindings(definition, axes["initialization"], "policy")
    unused = {item.get("role") for item in definition.get("inputs", [])} & {
        "pretrained_model", "embedding_model", "retrieval_index"}
    if unused:
        raise ValueError(f"unused input artifact roles for this learner: {sorted(unused)}")
    if definition.get("retrieval"):
        raise ValueError("retrieval selection is unsupported on numeric/policy learners")
    if "leakage_policy" in learning:
        raise ValueError("learning.leakage_policy is unsupported on numeric/policy learners")
    objective = selected_axis(learning, params, "objective")
    if numeric:
        if "algorithm" in params:
            raise ValueError("parameters.algorithm is unsupported on numeric/fusion learners")
        objective = objective or ("regression" if multimodal else "forecasting")
        supported = {"regression", "classification"} if multimodal else {"forecasting", "regression"}
    else:
        aliases = {} if language else {"reinforcement_learning": "ppo", "imitation": "behavioral_cloning",
            "bc": "behavioral_cloning", "grpo": "grpo_tool_bandit", "tool_grpo": "grpo_tool_bandit", "offline_rl": "dqn"}
        algorithm = selector(params["algorithm"], "algorithm") if "algorithm" in params else None
        normalize = lambda value: aliases.get(value, value)
        if objective is not None and algorithm is not None and normalize(objective) != normalize(algorithm):
            raise ValueError("conflicting objective and algorithm axes")
        objective = normalize(objective or algorithm or ("grpo" if language else "ppo"))
        supported = {"ppo", "grpo"} if language else {"ppo", "dqn", "behavioral_cloning", "grpo_tool_bandit", "ppo_tool_bandit"}
    if objective not in supported:
        raise ValueError(f"unsupported learning objective {objective!r}")
    environment = None
    if language or numeric or objective in {"grpo_tool_bandit", "ppo_tool_bandit"}:
        if "environment" in params:
            raise ValueError("environment selection is unused by this learning objective")
    else:
        from .policy_environment import environment_runtime
        from .control_evaluation import evaluation_seeds
        environment = environment_runtime(params)
        evaluation_seeds(params, definition.get("seed", 42))
    if language or not numeric and objective not in {"dqn", "behavioral_cloning"}:
        if any(item.get("visibility", "model") == "model" and item.get("role") != "resume"
               for item in definition.get("inputs", [])):
            raise ValueError("unused corpus input: this policy learns from configured tasks or environment interactions")
    if numeric and "batch_size" in params:
        raise ValueError("numeric/fusion backend uses full-batch updates; batch_size selection is unsupported")
    families = [selector(container["family"], "family") for container in (learning, params) if "family" in container]
    if language:
        families = [{"tiny_transformer": "transformer"}.get(value, value) for value in families]
    if len(set(families)) > 1:
        raise ValueError("conflicting family axes")
    family = families[0] if families else "multimodal" if multimodal else "gru" if numeric or language else "mlp"
    if family not in ({"multimodal"} if multimodal else {"gru", "transformer", "mlp"} if numeric else {"gru", "transformer"} if language else {"mlp"}):
        raise ValueError("unsupported family for selected numeric/policy engine")
    representations = {"multimodal", "raw_arrays", "byte_histogram_fusion"} if multimodal else {"numeric"} if numeric else {"tool_dsl", "engineering_vocabulary", "state_tokens"} if language else {"numeric", "state_vector"}
    representation = selected_axis(learning, params, "representation")
    if representation is not None and representation not in representations:
        raise ValueError("unsupported representation for selected numeric/policy engine")
    fields = {"hidden_size": (16, 4, 256 if language else 512), "layers": (1, 1, 4 if language else 1 if multimodal else 8)}
    if language or numeric and not multimodal:
        fields["context"] = (32, 16, 512) if language else (8, 1, 512)
        if family == "transformer":
            fields["heads"] = (2, 1, 16)
    dimensions = architecture(learning, params, fields)
    if family == "transformer" and dimensions["hidden_size"] % dimensions["heads"]:
        raise ValueError("hidden_size must be divisible by heads")
    learning_rate(params, 0.01 if numeric else 0.003)
    return {**axes, "objective": objective, "family": family, "dimensions": dimensions, "environment_runtime": environment}


def resume_configuration(configuration):
    """Initialization chooses how to enter a run, not its continued update rule."""
    return {**configuration,
        "learning": {key: value for key, value in configuration["learning"].items() if key != "initialization"},
        "parameters": {key: value for key, value in configuration["parameters"].items() if key != "initialization"}}


def matching_resume_configuration(saved, current):
    """Validate the recorded digest before normalizing legacy initialization syntax."""
    import hashlib
    import json
    recorded = saved.get("configuration")
    if not isinstance(recorded, dict):
        return False
    digest = hashlib.sha256(json.dumps(recorded, sort_keys=True).encode()).hexdigest()
    if digest != saved.get("config_sha256"):
        return False
    return resume_configuration(recorded) == resume_configuration(current)
