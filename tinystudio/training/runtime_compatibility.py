"""Selected runtime prerequisites and provenance, without executing a probe.

An eligible candidate remains unprobed. Package discovery and device availability
do not establish working NF4 kernels, training, retention, or cross-runtime replay.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from importlib import metadata
import platform
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Literal, Mapping


@dataclass(frozen=True)
class RuntimeEvidencePaths:
    root: Path

    @property
    def verification_manifest(self):
        return self.root / "runtime-verification.json"


@dataclass(frozen=True)
class RuntimeSelection:
    """Canonical placement supported by the current trainer implementation."""

    device: str
    dtype: str

    @classmethod
    def resolve(cls, device: str | None = None, dtype: str | None = None):
        from .runtime import TrainingPlacement
        placement = TrainingPlacement.resolve(device=device, dtype=dtype)
        return cls(placement.device, placement.dtype_name)

    def to_dict(self):
        return {"device": self.device, "dtype": self.dtype}


@dataclass(frozen=True)
class RuntimeInventory:
    """Observed metadata. Versions describe the candidate, not its eligibility."""

    os: str
    machine: str
    python: str
    dependencies: Mapping[str, str | None]
    metadata_errors: Mapping[str, str]

    packages: ClassVar[tuple[str, ...]] = (
        "torch", "transformers", "peft", "trl", "accelerate", "tokenizers",
        "safetensors", "bitsandbytes", "datasets")

    def __post_init__(self):
        object.__setattr__(self, "dependencies", MappingProxyType(dict(self.dependencies)))
        object.__setattr__(self, "metadata_errors", MappingProxyType(dict(self.metadata_errors)))

    @classmethod
    def capture(cls):
        versions, errors = {}, {}
        for name in cls.packages:
            try:
                versions[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                versions[name] = None
            except Exception as error:
                # Broken distribution metadata is observable provenance, not
                # proof that an otherwise discoverable module cannot execute.
                versions[name] = None
                errors[name] = type(error).__name__ + ": " + str(error)
        return cls(platform.system(), platform.machine(), platform.python_version(), versions, errors)

    def to_dict(self):
        return {"os": self.os, "machine": self.machine, "python": self.python,
                "dependencies": dict(self.dependencies), "metadata_errors": dict(self.metadata_errors)}


@dataclass(frozen=True)
class RuntimePrerequisites:
    """A prerequisite result; this type can never confer qualification."""

    status: Literal["unavailable", "unprobed"]
    reason: str
    inventory: RuntimeInventory
    selection: RuntimeSelection | None = None
    missing_dependencies: tuple[str, ...] = ()

    @property
    def eligible(self):
        return self.status == "unprobed"

    @property
    def qualified(self):
        return False

    def to_dict(self):
        return {"format": "tinystudio.runtime-prerequisites.v1", "status": self.status,
                "eligible": self.eligible, "qualified": self.qualified, "reason": self.reason,
                "selection": self.selection.to_dict() if self.selection else None,
                "inventory": self.inventory.to_dict(), "missing_dependencies": list(self.missing_dependencies)}


class RuntimeCompatibility:
    """Inspect prerequisites only; explicit target-side measurements are separate."""

    @classmethod
    def inspect(cls, device: str | None = None, dtype: str | None = None):
        inventory = RuntimeInventory.capture()
        missing, discovery_errors = [], []
        for name in inventory.packages:
            try:
                found = importlib.util.find_spec(name)
            except (ImportError, ValueError, AttributeError) as error:
                found = None
                discovery_errors.append(name + ": " + type(error).__name__ + ": " + str(error))
            if found is None:
                missing.append(name)
        if "torch" in missing:
            return cls._missing(inventory, None, missing, discovery_errors)
        try:
            selection = RuntimeSelection.resolve(device=device, dtype=dtype)
        except (RuntimeError, TypeError, ValueError, ImportError) as error:
            return RuntimePrerequisites("unavailable",
                f"Invalid training device or precision for {device!r}: {error}; select a compatible target supported by this trainer",
                inventory, missing_dependencies=tuple(missing))
        if missing:
            return cls._missing(inventory, selection, missing, discovery_errors)
        return RuntimePrerequisites("unprobed",
            f"QLoRA prerequisites found for {selection.device}/{selection.dtype}; this runtime is unprobed. "
            "Kernel, training, retention and reload qualification have not run.", inventory, selection)

    @staticmethod
    def _missing(inventory, selection, missing, errors):
        reason = "QLoRA requires discoverable modules in the selected execution environment: " + ", ".join(missing)
        if errors:
            reason += "; module discovery failed: " + "; ".join(errors)
        return RuntimePrerequisites("unavailable", reason, inventory, selection, tuple(missing))
