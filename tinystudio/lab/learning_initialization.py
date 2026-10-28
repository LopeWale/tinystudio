"""Validated immutable initialization boundary for the local sequence trainer."""
from __future__ import annotations

from .learning_axes import architecture as requested_architecture, selector

FAMILY_ALIASES = {"tiny_transformer": "transformer", "decoder_transformer": "transformer", "rnn": "gru"}
REPRESENTATION_ALIASES = {"bytes": "byte", "byte_level": "byte", "byte_bpe": "bpe",
                          "domain": "engineering", "engineering_vocabulary": "engineering"}


def validate_architecture(architecture):
    if not isinstance(architecture, dict) or not isinstance(architecture.get("family"), str) or architecture.get("family") not in {"transformer", "gru"}:
        raise ValueError("unsupported checkpoint architecture family")
    fields = {"hidden_size": (32, 4, 512), "layers": (1, 1, 8), "context": (32, 2, 512)}
    if architecture["family"] == "transformer":
        fields["heads"] = (2, 1, 16)
    if set(architecture) != {"family", *fields}:
        raise ValueError("invalid checkpoint architecture fields")
    if any(type(architecture[name]) is not int for name in fields):
        raise ValueError("checkpoint architecture dimensions must be integers")
    requested_architecture({"architecture": {k: v for k, v in architecture.items() if k != "family"}}, {}, fields)
    if architecture["family"] == "transformer" and architecture["hidden_size"] % architecture["heads"]:
        raise ValueError("hidden_size must be divisible by heads")


def validate_initialization_bindings(definition, initialization, objective=None):
    """Validate authored checkpoint roles without opening artifacts or importing torch."""
    if not isinstance(initialization, str) or initialization not in {"scratch", "random", "from_scratch", "weights", "resume"}:
        raise ValueError(f"unsupported initialization {initialization!r}")
    params = definition.get("parameters", {})
    if any(key in params for key in {"resume_from", "teacher_checkpoint", "initial_weights", "initial_weights_path"}):
        raise ValueError("checkpoint sources must use immutable input artifact roles")
    objective = objective or selector((definition.get("learning") or {}).get("objective", "causal_lm"), "objective")
    inputs = definition.get("inputs", [])
    expected = {"weights": "initial_weights", "resume": "resume"}.get(initialization)
    supplied = {item.get("role") for item in inputs} & {"resume", "initial_weights"}
    if supplied != ({expected} if expected else set()):
        raise ValueError("initialization requires exactly its matching immutable checkpoint role: "
                         "use initialization=resume with resume, weights with initial_weights, "
                         "or scratch/random without a checkpoint")
    for role in ("resume", "initial_weights", "teacher"):
        selected = [item for item in inputs if item.get("role") == role]
        if len(selected) > 1:
            raise ValueError(f"exactly one {role!r} input artifact is required")
        if selected and selected[0].get("visibility", "model") != "model":
            raise ValueError(f"{role} requires a model-visible artifact binding")
    teacher = any(item.get("role") == "teacher" for item in inputs)
    if teacher and objective != "distillation":
        raise ValueError("teacher input is unused without objective=distillation")
    if objective == "distillation" and not teacher:
        raise ValueError("distillation requires an immutable teacher input artifact")
    return expected


def checkpoint_binding(spec, initialization):
    """Resolve verified bindings only after rejecting contradictory/unused declarations."""
    from .compatibility import resolve_role
    expected = validate_initialization_bindings(spec["definition"], initialization)
    paths = spec.get("input_paths", {})
    for index, item in enumerate(spec["definition"].get("inputs", [])):
        if item.get("role") == "teacher" or expected is not None and item.get("role") == expected:
            raw = paths.get(item.get("artifact_id")) if isinstance(paths, dict) else (paths[index] if index < len(paths) else None)
            if not raw or isinstance(raw, dict) and not raw.get("path"):
                raise ValueError(f"server did not resolve {item.get('role')!r} artifact")
    binding = resolve_role(spec, expected) if expected else None
    teacher = resolve_role(spec, "teacher", required=False)
    return binding, teacher


def inherited_configuration(learning, params, architecture, tokenizer, tokenizer_configuration=None):
    """Compare authored semantic axes; omitted axes inherit checkpoint values."""
    validate_architecture(architecture)
    for container in (learning, params):
        if "family" in container:
            value = selector(container["family"], "family")
            if FAMILY_ALIASES.get(value, value) != architecture["family"]:
                raise ValueError("requested family does not match checkpoint architecture")
    requested = learning.get("architecture") or {}
    if not isinstance(requested, dict):
        raise ValueError("learning.architecture must be an object")
    explicit = dict(requested)
    for key in ("hidden_size", "layers", "heads", "context"):
        if key in params:
            if key in explicit and explicit[key] != params[key]:
                raise ValueError(f"conflicting architecture.{key} and parameters.{key}")
            explicit[key] = params[key]
    if any(key not in architecture or value != architecture[key] for key, value in explicit.items()):
        raise ValueError("requested architecture does not match checkpoint architecture")
    representation = learning.get("representation")
    options = representation if isinstance(representation, dict) else {}
    if set(options) - {"kind", "vocab_size", "vocabulary"}:
        raise ValueError("unsupported representation options")
    if representation is not None:
        kind = options.get("kind", "byte") if options else representation
        if not isinstance(kind, str) or REPRESENTATION_ALIASES.get(kind, kind) != tokenizer.kind:
            raise ValueError("requested representation does not match checkpoint tokenizer")
    for key in ("vocab_size", "vocabulary"):
        if key in options and key in params and options[key] != params[key]:
            raise ValueError(f"conflicting representation {key}")
    vocab_size = options.get("vocab_size", params.get("vocab_size"))
    if vocab_size is not None and (type(vocab_size) is not int or vocab_size != (tokenizer_configuration or {}).get("vocab_size", tokenizer.vocab_size)):
        raise ValueError("requested vocab_size does not match frozen checkpoint tokenizer")
    vocabulary = options.get("vocabulary", params.get("vocabulary"))
    if vocabulary is not None:
        if (tokenizer.kind != "engineering" or not isinstance(vocabulary, list)
            or any(not isinstance(word, str) or not word for word in vocabulary)
            or sorted(set(vocabulary), key=lambda word: (-len(word.encode()), word)) != tokenizer.vocabulary):
            raise ValueError("requested vocabulary does not match frozen checkpoint tokenizer")
