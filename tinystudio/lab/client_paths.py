"""Shared routes, definition envelopes and opaque identifiers for Lab clients."""
from copy import deepcopy
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class RuntimeApiPaths:
    base: str = "/api/lab"

    @property
    def methods(self) -> str:
        return self.base + "/runtime/methods"

    @property
    def definitions(self) -> str:
        return self.base + "/definitions"


def runtime_definition(name: str, parameters: dict, execution: dict, inputs: list[dict] | None = None) -> dict:
    """Wrap authored settings and retain an explicit seed across generic normalization."""
    definition = {"name": name, "engine": "runtime.qualify", "domain": "runtime", "mode": "offline",
                  "operating_mode": "replay", "parameters": parameters, "execution": execution}
    if isinstance(parameters, dict) and "seed" in parameters:
        definition["seed"] = parameters["seed"]
    if inputs is not None:
        definition["inputs"] = inputs
    return deepcopy(definition)


def path_identifier(value: str, *, maximum: int = 64) -> str:
    """Accept one opaque Lab URL segment; never permit path or query syntax."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,"+str(maximum)+r"}", value):
        raise ValueError("invalid laboratory identifier")
    return value

