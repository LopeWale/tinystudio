"""Serving-selection and retirement contracts; tensor qualification is separate."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading

import pytest
import torch

from tinystudio.core.project import ProjectError
from tinystudio.serving.runner import ModelRunner, ResolvedModel


class Source:
    def __init__(self, manifest):
        self.manifest, self.extracted = manifest, None

    @contextmanager
    def open(self):
        with tempfile.TemporaryDirectory() as directory:
            self.extracted = Path(directory)
            (self.extracted / "pretrained.json").write_text(json.dumps(self.manifest))
            yield self.extracted


class Tokenizer:
    pad_token_id, eos_token_id, eos_token = 0, 1, "<eos>"

    def get_chat_template(self):
        return "{{ messages }}"


class Policy(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.weight = torch.nn.Parameter(torch.ones(1))
        self.moves = []

    def to(self, *args, **kwargs):
        self.moves.append((args, kwargs))
        return super().to(*args, **kwargs)


@pytest.fixture
def loader(monkeypatch):
    monkeypatch.setenv("TINYSTUDIO_DEVICE", "auto")
    monkeypatch.setenv("TINYSTUDIO_DTYPE", "auto")
    monkeypatch.setattr("tinystudio.serving.runner.pick_device", lambda: "mps")
    calls = []
    def load(path, *, device):
        assert path.is_dir()
        model = Policy()
        calls.append((path, device, model))
        return model, Tokenizer(), {}
    monkeypatch.setattr("tinystudio.lab.compatibility.load_pretrained_output", load)
    return calls


def retained(*, quantized=True, runtime=None):
    source = Source({"format": "tinystudio.pretrained.v2" if quantized else "tinystudio.pretrained.v1",
        "quantization": {"runtime": runtime if runtime is not None else {"device": "cpu", "dtype": "float32"}}})
    return source, ResolvedModel("lab:" + "1" * 32, "unused-float-base", None, None, "retained", source)


def test_automatic_nf4_serving_adopts_contract_without_moving_or_merging(loader):
    source, resolved = retained()
    runner = ModelRunner(resolved)
    assert runner.device == "cpu"
    assert loader[0][1] == "cpu" and runner.model is loader[0][2]
    assert runner.model.moves == []
    assert not source.extracted.exists()
    assert runner.tokenizer.get_chat_template()
    runner.unload()
    runner.unload()  # Retirement is idempotent.
    with pytest.raises(ProjectError, match="unloaded"):
        runner.generate([{"role": "user", "content": "pressure"}])
    with pytest.raises(ProjectError, match="unloaded"):
        list(runner.stream([{"role": "user", "content": "pressure"}]))


@pytest.mark.parametrize("dtype", ["fp16", "bf16", "float16", "bfloat16", "invalid"])
def test_explicit_incompatible_precision_rejects_before_tensor_loading(loader, monkeypatch, dtype):
    monkeypatch.setenv("TINYSTUDIO_DTYPE", dtype)
    source, resolved = retained()
    with pytest.raises(ProjectError, match="dtype|precision"):
        ModelRunner(resolved)
    assert not loader and not source.extracted.exists()


@pytest.mark.parametrize("dtype", ["fp32", "float32"])
def test_explicit_matching_precision_is_honored(loader, monkeypatch, dtype):
    monkeypatch.setenv("TINYSTUDIO_DTYPE", dtype)
    _, resolved = retained()
    runner = ModelRunner(resolved, device="cpu")
    assert runner.device == "cpu" and not runner.model.moves


@pytest.mark.parametrize("runtime", [{}, {"device": "cpu"}, {"device": "auto", "dtype": "float32"},
    {"device": "cpu", "dtype": "float16"}])
def test_missing_concrete_runtime_is_not_filled_from_machine_defaults(loader, runtime):
    source, resolved = retained(runtime=runtime)
    with pytest.raises(ProjectError, match="concrete device and precision"):
        ModelRunner(resolved)
    assert not loader and not source.extracted.exists()


def test_explicit_device_cannot_silently_adopt_a_different_contract(loader, monkeypatch):
    monkeypatch.setenv("TINYSTUDIO_DEVICE", "cpu")
    source, resolved = retained()
    with pytest.raises((ProjectError, ValueError), match="unavailable|conflicts"):
        ModelRunner(resolved, device="cuda:0")
    assert not loader and not source.extracted.exists()


def test_missing_chat_template_has_actionable_error_and_removes_extraction(loader, monkeypatch):
    def missing(self):
        raise ValueError("chat_template is not set")
    monkeypatch.setattr(Tokenizer, "get_chat_template", missing)
    source, resolved = retained()
    with pytest.raises(ProjectError, match="no usable chat template.*raw-text inference"):
        ModelRunner(resolved)
    assert len(loader) == 1 and not source.extracted.exists()


def test_nonquantized_retained_model_uses_selected_serving_precision(loader, monkeypatch):
    monkeypatch.setenv("TINYSTUDIO_DTYPE", "fp16")
    source, resolved = retained(quantized=False)
    runner = ModelRunner(resolved, device="cpu")
    assert runner.model.weight.dtype == torch.float16
    assert len(runner.model.moves) == 1 and not source.extracted.exists()


def test_failure_after_allocation_removes_extraction(loader, monkeypatch):
    def fail(self, *args, **kwargs):
        raise RuntimeError("placement failed after allocation")
    monkeypatch.setattr(Policy, "to", fail)
    source, resolved = retained(quantized=False)
    with pytest.raises(RuntimeError, match="placement failed"):
        ModelRunner(resolved, device="cpu")
    assert len(loader) == 1 and not source.extracted.exists()


def test_unload_waits_for_active_generation_and_retired_runner_rejects_retry():
    entered, release, retiring, retired = (threading.Event() for _ in range(4))
    class ActivePolicy:
        def generate(self, **kwargs):
            entered.set()
            assert release.wait(3)
            return torch.tensor([[3, 4]])
    class ActiveTokenizer(Tokenizer):
        def decode(self, *args, **kwargs):
            return "answer"
    runner = ModelRunner.__new__(ModelRunner)
    runner.lock, runner.device = threading.Lock(), "cpu"
    runner.model, runner.tokenizer = ActivePolicy(), ActiveTokenizer()
    runner._encode = lambda *args: {"input_ids": torch.tensor([[3]])}
    result, failures = [], []
    def generate():
        try:
            result.append(runner.generate([]))
        except BaseException as cause:
            failures.append(cause)
    def retire():
        retiring.set()
        runner.unload()
        retired.set()
    generation = threading.Thread(target=generate)
    retirement = threading.Thread(target=retire)
    generation.start()
    assert entered.wait(3)
    retirement.start()
    assert retiring.wait(3)
    assert not retired.is_set() and runner.model is not None
    release.set()
    generation.join(3); retirement.join(3)
    assert not generation.is_alive() and not retirement.is_alive()
    assert not failures and result[0]["content"] == "answer"
    assert retired.is_set() and runner.model is None
    with pytest.raises(ProjectError, match="unloaded"):
        runner.generate([])
