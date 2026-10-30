"""Pure authored-selection checks shared by preflight and the CPU language trainer.

Checkpoint dimensions and frozen vocabularies are deliberately not defaulted here:
only artifact inspection can establish omitted inherited values.
"""
from __future__ import annotations

from .learning_axes import architecture, runtime_axes, selector
from .learning_initialization import FAMILY_ALIASES, REPRESENTATION_ALIASES, validate_initialization_bindings


def validate_language_selection(definition):
    learning, params = definition.get("learning") or {}, definition.get("parameters", {})
    axes = runtime_axes(learning, params, strategies={"adamw", "full", "full_parameter", "full_finetune"},
        default_strategy="adamw", initializations={"scratch", "from_scratch", "random", "weights", "resume"})
    if "objective" in params:
        raise ValueError("parameters.objective is unsupported on the language path; use learning.objective")
    objective = selector(learning.get("objective", "causal_lm"), "objective")
    if objective not in {"causal_lm", "next_token", "cross_entropy", "distillation"}:
        raise ValueError(f"unsupported learning objective {objective!r}")
    validate_initialization_bindings(definition, axes["initialization"], objective)
    if definition.get("retrieval"):
        raise ValueError("language training requires explicitly composed frozen retrieval; a retrieval selection cannot be silently ignored")
    if "leakage_policy" in learning:
        raise ValueError("learning.leakage_policy is unsupported on the language path; use parameters.leakage_policy")
    if "batch_size" in params and params["batch_size"] != 1:
        raise ValueError("scratch language backend currently supports batch_size=1 only")

    inherited = axes["initialization"] in {"weights", "resume"}
    families = [FAMILY_ALIASES.get(value, value) for value in
                (selector(container["family"], "family") for container in (learning, params) if "family" in container)]
    if len(set(families)) > 1:
        raise ValueError("conflicting family axes")
    family = families[0] if families else None if inherited else "transformer"
    if family is not None and family not in {"transformer", "gru"}:
        raise ValueError(f"unsupported scratch language family {family!r}")
    fields = {"hidden_size": (32, 4, 512), "layers": (1, 1, 8), "context": (32, 2, 512)}
    if family != "gru":
        fields["heads"] = (2, 1, 16)
    try:
        requested = architecture(learning, params, fields)
    except ValueError as exc:
        if inherited:
            raise ValueError(f"requested architecture does not match selected family: {exc}") from exc
        raise
    explicit = {**{key: params[key] for key in fields if key in params}, **(learning.get("architecture") or {})}
    # For restored models, hidden_size=9 may inherit heads=3. Defaults are not
    # evidence about the checkpoint; compare actual inherited values at execution.
    if family != "gru" and (not inherited or {"hidden_size", "heads"} <= explicit.keys()):
        if requested["hidden_size"] % requested["heads"]:
            raise ValueError("hidden_size must be divisible by heads")

    if "representation" in params:
        raise ValueError("parameters.representation is unsupported on the language path; use learning.representation")
    representation = learning.get("representation")
    if "representation" in learning and representation is None:
        raise ValueError("representation must name a supported kind or be an object")
    options = representation if isinstance(representation, dict) else {}
    if set(options) - {"kind", "vocab_size", "vocabulary"}:
        raise ValueError("unsupported representation options")
    frozen = inherited or objective == "distillation"
    kind = options.get("kind") if isinstance(representation, dict) else representation
    if "kind" in options and kind is None:
        raise ValueError("representation kind must be a string")
    if kind is None and not frozen:
        kind = "byte"
    if kind is not None:
        if not isinstance(kind, str):
            raise ValueError("representation kind must be a string")
        kind = REPRESENTATION_ALIASES.get(kind, kind)
        if kind not in {"byte", "bpe", "engineering"}:
            raise ValueError(f"unsupported representation {kind!r}")
    elif representation is not None and not isinstance(representation, dict):
        raise ValueError("representation must name a supported kind or be an object")
    for key in ("vocab_size", "vocabulary"):
        if key in options and key in params and options[key] != params[key]:
            raise ValueError(f"conflicting representation {key}")
    vocabulary = options.get("vocabulary", params.get("vocabulary"))
    if vocabulary is not None:
        if kind not in {None, "engineering"}:
            raise ValueError("vocabulary does not match representation; vocabulary is supported only by the engineering representation")
        if not isinstance(vocabulary, list) or not vocabulary or any(not isinstance(word, str) or not word for word in vocabulary):
            raise ValueError("engineering representation requires a nonempty vocabulary")
    if kind == "engineering" and vocabulary is None and not frozen:
        raise ValueError("engineering representation requires a nonempty vocabulary")
    if "vocab_size" in options or "vocab_size" in params:
        value = options.get("vocab_size", params.get("vocab_size"))
        if kind not in {None, "bpe"} and not frozen:
            raise ValueError("vocab_size is supported only by the bpe representation")
        try:
            int(value)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("representation vocab_size must be convertible to an integer") from exc
    return {**axes, "objective": objective}
