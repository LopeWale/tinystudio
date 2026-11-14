"""Shared routes, definition envelopes and opaque identifiers for Lab clients."""
from copy import deepcopy
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class LabApiPaths:
    base: str = "/api/lab"


@dataclass(frozen=True)
class RuntimeApiPaths(LabApiPaths):
    @property
    def methods(self) -> str:
        return self.base + "/runtime/methods"

    @property
    def definitions(self) -> str:
        return self.base + "/definitions"


@dataclass(frozen=True)
class HardwareApiPaths(LabApiPaths):
    @property
    def discovery_methods(self) -> str:
        return self.base + "/hardware/discovery-methods"


@dataclass(frozen=True)
class RecoveredEvidencePaths(LabApiPaths):
    @property
    def references_route(self):
        return self.base + '/runs/{id_}/recovered-evidence'

    @property
    def preview_route(self):
        return self.references_route + '/preview'

    @property
    def attach_route(self):
        return self.references_route

    def references(self,run_id):
        return self.references_route.replace('{id_}',path_identifier(run_id))

    def preview(self,run_id):
        return self.preview_route.replace('{id_}',path_identifier(run_id))

    def attach(self,run_id):
        return self.attach_route.replace('{id_}',path_identifier(run_id))


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

