"""Small shared contract for explicitly supported learning axes."""
from __future__ import annotations

from .signals import integer


ARCHITECTURE_FIELDS = {"hidden_size", "layers", "heads", "context", "input_size", "output_size", "dropout"}
LEARNING_FIELDS = {"family", "initialization", "architecture", "representation", "objective", "update_strategy",
                   "device", "dtype", "training_dtype", "leakage_policy"}


def selector(value, name):
    if isinstance(value, dict):
        if set(value) != {"kind"}:
            raise ValueError(f"{name} accepts only a kind; other settings must use supported parameters/artifact roles")
        value = value["kind"]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must name an explicit supported value")
    return value


def architecture(learning, parameters, fields):
    requested = learning.get("architecture", {})
    if requested is None:
        requested = {}
    if not isinstance(requested, dict):
        raise ValueError("learning.architecture must be an object")
    unknown = set(requested) - set(fields)
    unsupported_legacy = set(parameters) & (ARCHITECTURE_FIELDS - set(fields))
    if unknown or unsupported_legacy:
        raise ValueError(f"unsupported architecture axes for this method: {sorted(unknown | unsupported_legacy)}")
    result = {}
    for name, (default, low, high) in fields.items():
        nested = requested.get(name, parameters.get(name, default))
        value = integer(nested, f"architecture.{name}", low, high)
        if name in requested and name in parameters:
            legacy = integer(parameters[name], name, low, high)
            if value != legacy:
                raise ValueError(f"conflicting architecture.{name} and parameters.{name}")
        result[name] = value
    return result


def runtime_axes(learning, parameters, *, strategies, default_strategy, initializations,
                 default_initialization="scratch", actual_device="cpu", actual_dtype="float32"):
    unknown = set(learning) - LEARNING_FIELDS
    if unknown:
        raise ValueError(f"unsupported learning axes: {sorted(unknown)}")
    chosen = {}
    for name, default, supported in (("update_strategy", default_strategy, strategies),
                                     ("initialization", default_initialization, initializations)):
        value = selector(learning.get(name, parameters.get(name, default)), name)
        if name in learning and name in parameters and value != selector(parameters[name], name):
            raise ValueError(f"conflicting learning.{name} and parameters.{name}")
        if value not in supported:
            raise ValueError(f"unsupported {name} {value!r}; supported: {sorted(supported)}")
        chosen[name] = value
    def normalized(name, value):
        value = selector(value, name).lower()
        return {"fp32": "float32", "fp16": "float16", "bf16": "bfloat16"}.get(value, value) if name == "dtype" else value
    devices = [normalized("device", container["device"]) for container in (learning, parameters) if "device" in container]
    dtypes = [normalized("dtype", container[name]) for container in (learning, parameters) for name in ("dtype", "training_dtype") if name in container]
    if len(set(devices)) > 1 or len(set(dtypes)) > 1:
        raise ValueError("conflicting requested training device/dtype axes")
    if devices and devices[0] != actual_device or dtypes and dtypes[0] != actual_dtype:
        raise ValueError(f"requested device/dtype unavailable on this path; actual runtime is {actual_device}/{actual_dtype}")
    chosen.update(device=actual_device, dtype=actual_dtype)
    return chosen


def learning_rate(parameters, default):
    from .signals import number
    value = number(parameters.get("lr", parameters.get("learning_rate", default)), "learning_rate")
    if "lr" in parameters and "learning_rate" in parameters and value != number(parameters["learning_rate"], "learning_rate"):
        raise ValueError("conflicting lr and learning_rate parameters")
    if not 0 < value <= 1:
        raise ValueError("learning_rate must be between zero and one")
    return value
