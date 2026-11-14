"""Researcher-selected NF4 implementation choices, recorded independently of qualification."""
from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from typing import ClassVar


@dataclass(frozen=True)
class NF4ExecutionPolicy:
    cpu_inference: str = "library_default"

    modes: ClassVar[tuple[str, ...]] = ("library_default", "portable")
    cpu_setting: ClassVar[str] = "support_avx512bf16_for_cpu"

    @classmethod
    def resolve(cls, value=None):
        if value is None:
            return cls()
        if isinstance(value, cls):
            value = value.to_dict()
        if not isinstance(value, dict) or set(value) - {"cpu_inference"}:
            raise ValueError("NF4 execution policy must contain only cpu_inference")
        mode = value.get("cpu_inference", "library_default")
        if mode not in cls.modes:
            raise ValueError("select NF4 cpu_inference library_default or portable explicitly")
        return cls(mode)

    def to_dict(self):
        return {"cpu_inference": self.cpu_inference}

    @staticmethod
    def _flag(value):
        # bitsandbytes may expose the native CPU feature query as C int 0/1.
        return bool(value) if isinstance(value, Integral) and value in (0, 1) else None

    def apply(self, model, device):
        from bitsandbytes.nn import Linear4bit

        selected = self.resolve(self)
        modules = [(name, layer) for name, layer in model.named_modules() if isinstance(layer, Linear4bit)]
        if not modules:
            raise ValueError("NF4 execution policy requires actual Linear4bit modules")
        applicable = str(device).split(":")[0] == "cpu"
        if selected.cpu_inference == "portable" and applicable and any(
                self._flag(getattr(layer, self.cpu_setting, None)) is None for _, layer in modules):
            raise ValueError("selected portable NF4 CPU inference feature is unavailable in this runtime")
        observations = []
        for name, layer in modules:
            before = self._flag(getattr(layer, self.cpu_setting, None))
            if selected.cpu_inference == "portable" and applicable:
                setattr(layer, self.cpu_setting, False)
            after = self._flag(getattr(layer, self.cpu_setting, None))
            observations.append({"module": name, "before": before, "after": after, "changed": before != after})
        return {"policy": selected.to_dict(), "device": str(device), "applicable": applicable,
                "setting": self.cpu_setting, "modules": observations, "qualified": False}
