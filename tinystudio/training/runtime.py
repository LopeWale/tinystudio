"""Placement checks and serialized ownership of the trainer's process state."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import random
from threading import RLock

_TRAINING_LOCK = RLock()


@dataclass
class TrainingPlacement:
    device: str
    dtype: object
    model_devices: set[str] = field(default_factory=set, init=False)
    model_dtypes: set[str] = field(default_factory=set, init=False)
    batch_devices: set[str] = field(default_factory=set, init=False)
    batches: int = field(default=0, init=False)

    @classmethod
    def resolve(cls, device=None, dtype=None):
        import torch
        from tinystudio.serving.runner import pick_device, pick_dtype
        device = device or pick_device()
        try:
            target = torch.device(device)
        except (RuntimeError, TypeError) as exc:
            raise ValueError(f"requested training device {device!r} is unavailable") from exc
        if target.type not in {"cpu", "cuda", "mps"}:
            raise ValueError(f"requested training device {device!r} is unavailable on this trainer")
        if target.type == "cuda" and (not torch.cuda.is_available() or
                (target.index or 0) >= torch.cuda.device_count()):
            raise ValueError(f"requested training device {device!r} is unavailable")
        if target.type == "mps" and not torch.backends.mps.is_available():
            raise ValueError("requested training device mps is unavailable")
        if target.type != "cuda" and target.index not in {None, 0}:
            raise ValueError("CPU/MPS training does not support a device index")
        table = {"fp32": torch.float32, "float32": torch.float32,
                 "bf16": torch.bfloat16, "bfloat16": torch.bfloat16}
        if dtype is not None and dtype not in table:
            raise ValueError("requested training dtype is unavailable; supported: float32 and CUDA bfloat16")
        chosen = table[dtype] if dtype is not None else pick_dtype(device)
        if chosen not in ({torch.float32, torch.bfloat16} if target.type == "cuda" else {torch.float32}):
            raise ValueError(f"requested dtype is unavailable on {target.type}; use float32")
        if chosen == torch.bfloat16 and not torch.cuda.is_bf16_supported():
            raise ValueError("requested CUDA bfloat16 is unavailable on this device")
        canonical = f"cuda:{target.index or 0}" if target.type == "cuda" else target.type
        return cls(canonical, chosen)

    @property
    def dtype_name(self):
        return str(self.dtype).replace("torch.", "")

    def check_device(self, value):
        if str(value) != self.device:
            raise ValueError(f"trainer placed data/model on {value}, requested {self.device}; select a compatible execution target")

    def check_model(self, model):
        for parameter in model.parameters():
            self.check_device(parameter.device)
            self.model_devices.add(str(parameter.device))
            if parameter.is_floating_point():
                self.model_dtypes.add(str(parameter.dtype).replace("torch.", ""))

    def check_batch(self, value):
        import torch
        if isinstance(value, torch.Tensor):
            self.check_device(value.device)
            self.batch_devices.add(str(value.device))
        elif isinstance(value, dict):
            for child in value.values():
                self.check_batch(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                self.check_batch(child)

    def report(self):
        return {"requested_device": self.device, "requested_dtype": self.dtype_name,
                "model_devices": sorted(self.model_devices), "model_dtypes": sorted(self.model_dtypes),
                "batch_devices": sorted(self.batch_devices), "prepared_batches": self.batches}


class CheckedPlacementMixin:
    """Validate the tensors actually handed to the installed HF/TRL trainer."""
    def _prepare_inputs(self, inputs):
        prepared = super()._prepare_inputs(inputs)
        placement = getattr(self, "tinystudio_placement", None)
        if placement is not None:
            placement.check_batch(prepared)
            placement.batches += 1
        return prepared


@contextmanager
def training_session():
    """Serialize in-process callers; runner processes remain the isolation unit."""
    import numpy as np
    import torch
    with _TRAINING_LOCK:
        threads, python_state, numpy_state = torch.get_num_threads(), random.getstate(), np.random.get_state()
        mps_state = torch.mps.get_rng_state() if torch.backends.mps.is_available() else None
        try:
            torch.set_num_threads(1)
            with torch.random.fork_rng(devices=list(range(torch.cuda.device_count()))):
                yield
        finally:
            if mps_state is not None:
                torch.mps.set_rng_state(mps_state)
            np.random.set_state(numpy_state)
            random.setstate(python_state)
            torch.set_num_threads(threads)
