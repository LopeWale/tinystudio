"""Runtime prerequisites are observed candidates, never implicit qualification."""
from __future__ import annotations

from contextlib import contextmanager
import importlib
import json
import subprocess
from types import SimpleNamespace

import pytest


def compatibility(monkeypatch):
    module = importlib.import_module("tinystudio.training.runtime_compatibility")
    monkeypatch.setenv("TINYSTUDIO_DEVICE", "cpu")
    monkeypatch.setenv("TINYSTUDIO_DTYPE", "fp32")
    monkeypatch.setattr(module.metadata, "version", lambda name: "different-installed-version")
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: SimpleNamespace(name=name))
    return module


@pytest.mark.parametrize("system, machine, python", [
    ("Linux", "x86_64", "3.12.99"),
    ("Darwin", "arm64", "3.12.1"),
    ("FixtureOS", "unseen-architecture", "unseen-python"),
])
def test_changed_platform_and_package_metadata_remains_an_unprobed_candidate(monkeypatch, system, machine, python):
    module = compatibility(monkeypatch)
    monkeypatch.setattr(module.platform, "system", lambda: system)
    monkeypatch.setattr(module.platform, "machine", lambda: machine)
    monkeypatch.setattr(module.platform, "python_version", lambda: python)
    result = module.RuntimeCompatibility.inspect(device="cpu", dtype="float32")
    assert result.status == "unprobed" and result.eligible and result.qualified is False
    assert (result.selection.device, result.selection.dtype) == ("cpu", "float32")
    assert (result.inventory.os, result.inventory.machine, result.inventory.python) == (system, machine, python)
    assert result.inventory.dependencies["bitsandbytes"] == "different-installed-version"
    assert "unprobed" in result.reason.lower()
    report = result.to_dict()
    assert report["qualified"] is False and report["status"] == "unprobed"
    assert report["selection"] == {"device": "cpu", "dtype": "float32"}


def test_explicit_cpu_selection_is_honored_on_a_cuda_capable_host_without_running_a_probe(monkeypatch):
    module = compatibility(monkeypatch)
    import torch
    from tinystudio.training import train
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 4)
    def forbidden(*args, **kwargs):
        pytest.fail("prerequisite inspection must not execute kernels, models, subprocesses or training")
    monkeypatch.setattr(torch, "empty", forbidden)
    monkeypatch.setattr(torch.cuda, "init", forbidden)
    monkeypatch.setattr(train, "_load_4bit", forbidden)
    monkeypatch.setattr(train, "train_experiment", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    result = module.RuntimeCompatibility.inspect(device="cpu", dtype="float32")
    assert result.eligible and result.selection.device == "cpu" and not result.qualified


def test_missing_bitsandbytes_is_an_actionable_prerequisite_failure(monkeypatch):
    module = compatibility(monkeypatch)
    monkeypatch.setattr(module.importlib.util, "find_spec",
                        lambda name: None if name == "bitsandbytes" else SimpleNamespace(name=name))
    result = module.RuntimeCompatibility.inspect(device="cpu", dtype="float32")
    assert result.status == "unavailable" and not result.eligible and not result.qualified
    assert result.missing_dependencies == ("bitsandbytes",)
    assert "bitsandbytes" in result.reason
    assert result.selection.device == "cpu"


def test_missing_torch_is_reported_before_attempting_device_resolution(monkeypatch):
    module = compatibility(monkeypatch)
    from tinystudio.training.runtime import TrainingPlacement
    monkeypatch.setattr(module.importlib.util, "find_spec",
                        lambda name: None if name == "torch" else SimpleNamespace(name=name))
    def forbidden(*args, **kwargs):
        pytest.fail("cannot resolve placement without its required torch module")
    monkeypatch.setattr(TrainingPlacement, "resolve", forbidden)
    result = module.RuntimeCompatibility.inspect(device="cpu", dtype="float32")
    assert not result.eligible and "torch" in result.missing_dependencies
    assert "torch" in result.reason


@pytest.mark.parametrize("device, available, count", [("cuda:0", False, 0), ("cuda:3", True, 2)])
def test_unavailable_selected_cuda_device_is_not_an_eligible_candidate(monkeypatch, device, available, count):
    module = compatibility(monkeypatch)
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: available)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: count)
    result = module.RuntimeCompatibility.inspect(device=device, dtype="float32")
    assert result.status == "unavailable" and not result.eligible
    assert device in result.reason and "unavailable" in result.reason


@pytest.mark.parametrize("bf16_available", [False, True])
def test_selected_cuda_precision_uses_actual_device_capability_checks(monkeypatch, bf16_available):
    module = compatibility(monkeypatch)
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 2)
    active, events = [], []
    @contextmanager
    def selected_device(device):
        active.append(device)
        events.append(("enter", device))
        try:
            yield
        finally:
            events.append(("exit", active.pop()))
    def supported():
        assert active == ["cuda:1"]
        events.append(("query", active[-1]))
        return bf16_available
    monkeypatch.setattr(torch.cuda, "device", selected_device)
    monkeypatch.setattr(torch.cuda, "is_bf16_supported", supported)
    result = module.RuntimeCompatibility.inspect(device="cuda:1", dtype="bfloat16")
    assert result.eligible is bf16_available and not result.qualified
    if bf16_available:
        assert result.selection.to_dict() == {"device": "cuda:1", "dtype": "bfloat16"}
    else:
        assert "bfloat16 is unavailable" in result.reason
    assert events == [("enter", "cuda:1"), ("query", "cuda:1"), ("exit", "cuda:1")]
    assert not active


@pytest.mark.parametrize("device", ["cpu", "mps"])
def test_explicit_cpu_and_mps_bfloat16_remain_unprobed_without_cuda_queries(monkeypatch, device):
    module = compatibility(monkeypatch)
    import torch
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    def forbidden(*args, **kwargs):
        pytest.fail("CPU/MPS precision selection must not query or select a CUDA device")
    monkeypatch.setattr(torch.cuda, "device", forbidden)
    monkeypatch.setattr(torch.cuda, "is_bf16_supported", forbidden)
    result = module.RuntimeCompatibility.inspect(device=device, dtype="bfloat16")
    assert result.eligible and result.status == "unprobed" and not result.qualified
    assert result.selection.to_dict() == {"device": device, "dtype": "bfloat16"}


@pytest.mark.parametrize("available", [False, True])
def test_mps_is_selected_by_actual_device_availability_without_an_architecture_ban(monkeypatch, available):
    module = compatibility(monkeypatch)
    import torch
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: available)
    result = module.RuntimeCompatibility.inspect(device="mps", dtype="float32")
    assert result.eligible is available
    assert result.status == ("unprobed" if available else "unavailable")
    assert result.qualified is False


def test_device_families_unimplemented_by_the_trainer_are_reported_accurately(monkeypatch):
    module = compatibility(monkeypatch)
    result = module.RuntimeCompatibility.inspect(device="meta", dtype="float32")
    assert not result.eligible
    assert "on this trainer" in result.reason


def test_invalid_device_and_unsupported_precision_preserve_placement_errors(monkeypatch):
    module = compatibility(monkeypatch)
    invalid = module.RuntimeCompatibility.inspect(device="not-a-device", dtype="float32")
    assert not invalid.eligible and "training device" in invalid.reason
    precision = module.RuntimeCompatibility.inspect(device="cpu", dtype="float16")
    assert not precision.eligible and "dtype is unavailable" in precision.reason


def test_unknown_version_metadata_does_not_become_a_module_availability_gate(monkeypatch):
    module = compatibility(monkeypatch)
    def unknown(name):
        raise module.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(module.metadata, "version", unknown)
    result = module.RuntimeCompatibility.inspect(device="cpu", dtype="float32")
    assert result.eligible and not result.qualified
    assert result.inventory.dependencies["bitsandbytes"] is None
    with pytest.raises(TypeError):
        result.inventory.dependencies["bitsandbytes"] = "edited"


def test_broken_distribution_metadata_is_retained_without_claiming_execution_failure(monkeypatch):
    module = compatibility(monkeypatch)
    def broken(name):
        raise ValueError("controlled unreadable distribution metadata")
    monkeypatch.setattr(module.metadata, "version", broken)
    result = module.RuntimeCompatibility.inspect(device="cpu", dtype="float32")
    assert result.eligible and not result.qualified
    assert "ValueError" in result.inventory.metadata_errors["bitsandbytes"]
    assert json.loads(json.dumps(result.to_dict()))["inventory"]["dependencies"]["bitsandbytes"] is None


def test_module_discovery_failure_is_retained_as_an_unavailable_prerequisite(monkeypatch):
    module = compatibility(monkeypatch)
    def discovery(name):
        if name == "bitsandbytes":
            raise ValueError("controlled invalid module specification")
        return SimpleNamespace(name=name)
    monkeypatch.setattr(module.importlib.util, "find_spec", discovery)
    result = module.RuntimeCompatibility.inspect(device="cpu", dtype="float32")
    assert not result.eligible and result.missing_dependencies == ("bitsandbytes",)
    assert "module discovery failed" in result.reason and "invalid module specification" in result.reason


def test_module_import_does_not_inspect_runtime_or_run_training(monkeypatch):
    module = compatibility(monkeypatch)
    from tinystudio.training.runtime import TrainingPlacement
    def forbidden(*args, **kwargs):
        pytest.fail("importing compatibility must not inspect or probe a runtime")
    monkeypatch.setattr(module.metadata, "version", forbidden)
    monkeypatch.setattr(TrainingPlacement, "resolve", forbidden)
    importlib.reload(module)


def test_legacy_support_wrapper_does_not_call_the_fixed_pack_whitelist(monkeypatch):
    compatibility(monkeypatch)
    from tinystudio.training import quantization, train
    def forbidden():
        pytest.fail("fixed pack metadata cannot decide runtime eligibility")
    monkeypatch.setattr(quantization, "cpu_pack_compatible", forbidden, raising=False)
    eligible, reason = train.qlora_support("cpu")
    assert eligible
    assert "unprobed" in reason.lower() and "qualification" in reason.lower()


def test_device_only_support_wrapper_does_not_override_already_selected_precision(monkeypatch):
    compatibility(monkeypatch)
    from tinystudio.training.train import qlora_support
    # The calling trainer may already have explicitly resolved CPU/float32.
    monkeypatch.setenv("TINYSTUDIO_DTYPE", "bf16")
    eligible, reason = qlora_support("cpu")
    assert eligible and "unprobed" in reason.lower()


@pytest.mark.parametrize("device", ["cuda:invalid", "not-a-device"])
def test_legacy_support_wrapper_keeps_invalid_device_errors_actionable(monkeypatch, device):
    compatibility(monkeypatch)
    from tinystudio.training.train import qlora_support
    eligible, reason = qlora_support(device)
    assert not eligible
    assert "Invalid training device" in reason and "compatible target" in reason
