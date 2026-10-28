"""Training intent stays independent and old serialized experiments retain meaning."""
import hashlib

import pytest

from tinystudio.core.project import ProjectError
from tinystudio.training.methods import resolve_method
from tinystudio.training.train import TrainConfig, preference_rows, raw_sequence_rows


@pytest.mark.parametrize("objective", ["sft", "causal_lm", "dpo"])
@pytest.mark.parametrize("strategy", ["full", "lora", "qlora"])
def test_objective_and_update_are_independent(objective, strategy):
    config = TrainConfig(base_model="fixture", objective=objective, update_strategy=strategy)
    restored = TrainConfig.from_dict(config.as_dict())
    assert (restored.objective, restored.update_strategy) == (objective, strategy)
    assert restored.training_type == strategy
    assert restored.data_format == ("raw_text" if objective == "causal_lm" else "conversational")
    assert resolve_method(objective=objective, update_strategy=strategy).output_kind == ("model" if strategy == "full" else "adapter")


@pytest.mark.parametrize("legacy,objective,strategy", [("full", "sft", "full"), ("lora", "sft", "lora"),
    ("qlora", "sft", "qlora"), ("dpo", "dpo", "lora")])
def test_legacy_config_round_trip(legacy, objective, strategy):
    config = TrainConfig.from_dict({"base_model": "fixture", "training_type": legacy})
    assert config.training_type == legacy
    assert (config.objective, config.update_strategy) == (objective, strategy)
    assert TrainConfig.from_dict(config.as_dict()).as_dict() == config.as_dict()


@pytest.mark.parametrize("settings,objective,strategy", [
    ({"training_type": "dpo"}, "dpo", "lora"),
    ({"objective": "dpo", "update_strategy": "full"}, "dpo", "full"),
])
def test_existing_training_api_retains_explicit_axes(settings, objective, strategy):
    from tinystudio.api.routes.training import ExperimentSpec
    decoded = ExperimentSpec(base_model="fixture", **settings)
    config = TrainConfig(**decoded.model_dump())
    assert (config.objective, config.update_strategy) == (objective, strategy)


@pytest.mark.parametrize("device", ["cuda:invalid", "not-a-device"])
def test_invalid_qlora_device_returns_actionable_4xx_without_creating_experiment(device, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from unittest.mock import Mock
    from tinystudio.api.routes import training
    from tinystudio.api.deps import get_db, project_dep
    # Isolate this controller boundary; authenticated app acceptance is separate.
    app = FastAPI()
    app.include_router(training.router)
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[project_dep] = lambda: SimpleNamespace(id="fixture", slug="fixture")
    creation = Mock(side_effect=AssertionError("invalid device reached experiment creation"))
    monkeypatch.setattr(training.jobs_svc, "add_experiment", creation)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/p/fixture/jobs/fixture/experiments", json={"experiments": [
            {"base_model": "must-not-open", "update_strategy": "qlora", "device": device}]})
    assert response.status_code == 400
    assert "Invalid training device" in response.json()["detail"]
    assert "compatible target" in response.json()["detail"]
    creation.assert_not_called()


@pytest.mark.parametrize("settings", [
    {"training_type": "dpo", "objective": "sft"},
    {"training_type": "dpo", "update_strategy": "full"},
    {"training_type": "full", "update_strategy": "lora"},
    {"objective": "causal_lm", "data_format": "conversational"},
    {"objective": "causal_lm", "assistant_only_loss": True},
])
def test_explicit_conflicts_do_not_silently_change_intent(settings):
    with pytest.raises(ProjectError):
        TrainConfig(base_model="fixture", **settings)


def test_strict_preference_conversion_rejects_every_invalid_pair():
    valid = {"messages": [{"role": "user", "content": "voltage?"}, {"role": "assistant", "content": "-1.25 V"}], "rejected": "+1.25 V"}
    assert preference_rows([valid], strict=True)[0]["chosen"][0]["content"] == "-1.25 V"
    for invalid in ({**valid, "rejected": "-1.25 V"}, {**valid, "rejected": None},
                    {**valid, "messages": [{"role": "user", "content": "not an answer"}]},
                    {**valid, "messages": [None, valid["messages"][-1]]}):
        with pytest.raises(ProjectError, match="DPO row 1"):
            preference_rows([valid, invalid], strict=True)


def test_raw_sequence_exact_text_and_documented_tokens():
    class RecordingTokenizer:
        eos_token_id = 99
        calls = []

        def __call__(self, text, **kwargs):
            self.calls.append((text, kwargs))
            return {"input_ids": [4, 7, 8, 9]}

    text = "  -1.25 V\nμA\t"
    tokenizer = RecordingTokenizer()
    rows, receipts = raw_sequence_rows([{"text": text, "source_record_id": "source:1"}], tokenizer, 4)
    assert tokenizer.calls == [(text, {"add_special_tokens": False})]
    assert rows == [{"input_ids": [4, 7, 8, 9], "attention_mask": [1, 1, 1, 1]}]
    assert receipts[0]["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert receipts[0]["tokens_before_truncation"] == 5
    assert receipts[0]["truncated_tokens"] == 1
    assert receipts[0]["eos_appended"] is True


def test_placement_checks_reject_wrong_model_and_batch_devices():
    import torch
    from tinystudio.training.runtime import TrainingPlacement
    placement = TrainingPlacement.resolve("cpu", "float32")
    with pytest.raises(ValueError, match="requested cpu"):
        placement.check_model(torch.nn.Linear(2, 2, device="meta"))
    with pytest.raises(ValueError, match="requested cpu"):
        placement.check_batch({"nested": [torch.zeros(2, device="meta")]})


def test_device_and_dtype_defaults_are_independent(monkeypatch):
    from tinystudio.training.runtime import TrainingPlacement
    monkeypatch.setenv("TINYSTUDIO_DEVICE", "cuda:7")
    monkeypatch.setenv("TINYSTUDIO_DTYPE", "bf16")
    # Omitting dtype retains the configured dtype; it must not silently fall back.
    with pytest.raises(ValueError, match="dtype is unavailable on cpu"):
        TrainingPlacement.resolve("cpu")
    assert TrainingPlacement.resolve("cpu", "float32").device == "cpu"
    monkeypatch.setenv("TINYSTUDIO_DTYPE", "fp32")
    assert TrainingPlacement.resolve("cpu").dtype_name == "float32"


def test_shared_trainer_rejects_cpu_qlora_on_a_cuda_capable_host(tmp_path, monkeypatch):
    import torch
    from tinystudio.training.train import train_experiment
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr("tinystudio.training.quantization.cpu_pack_compatible", lambda: False)
    config = TrainConfig(base_model="must-not-open", update_strategy="qlora", device="cpu", dtype="float32")
    with pytest.raises(ProjectError, match="requires a CUDA target"):
        train_experiment(tmp_path, config, [], [])


@pytest.mark.parametrize("exceptional", [False, True])
def test_training_session_restores_rng_and_threads(exceptional):
    import random
    import numpy as np
    import torch
    from tinystudio.training.runtime import training_session
    python_state, numpy_state = random.getstate(), np.random.get_state()
    torch_state, threads = torch.get_rng_state().clone(), torch.get_num_threads()
    try:
        with training_session():
            assert torch.get_num_threads() == 1
            random.seed(81)
            np.random.seed(81)
            torch.manual_seed(81)
            if exceptional:
                raise RuntimeError("controlled exit")
    except RuntimeError as exc:
        assert exceptional and str(exc) == "controlled exit"
    assert random.getstate() == python_state
    actual_numpy = np.random.get_state()
    assert actual_numpy[0] == numpy_state[0] and np.array_equal(actual_numpy[1], numpy_state[1])
    assert actual_numpy[2:] == numpy_state[2:]
    assert torch.equal(torch.get_rng_state(), torch_state) and torch.get_num_threads() == threads


def test_training_session_serializes_process_global_mutation():
    import threading
    from tinystudio.training.runtime import training_session
    requested, entered = threading.Event(), threading.Event()
    def contender():
        requested.set()
        with training_session():
            entered.set()
    with training_session():
        thread = threading.Thread(target=contender)
        thread.start()
        assert requested.wait(timeout=2)
        assert not entered.wait(timeout=0.05), "a second trainer must not enter while globals belong to the first"
    thread.join(timeout=2)
    assert not thread.is_alive() and entered.is_set()
