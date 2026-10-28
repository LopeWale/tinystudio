import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
import torch

from tinystudio.lab.compatibility import extract_model_archive
from tinystudio.lab.deployment import execute_deployment, run_packaged_inference, target_compatibility
from tinystudio.lab.learning import execute_learning, generate_from_checkpoint


@pytest.fixture
def scratch_model(tmp_path):
    spec = {"definition": {"seed": 3, "learning": {"family": "gru"}, "parameters": {
        "texts": ["pressure normal", "sensor ready", "valve open"], "hidden_size": 8,
        "context": 8, "steps": 3, "max_new_tokens": 2}}}
    execute_learning(spec, tmp_path / "training", lambda row: None, lambda: False)
    return tmp_path / "training/checkpoint.pt"


def deployment_spec(path, **parameters):
    return {"definition": {"parameters": {"warmup": 1, "repetitions": 4, "max_new_tokens": 3,
        "prompt": "pressure", **parameters}, "inputs": [{"artifact_id": "model-1", "role": "model",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]}, "input_paths": {"model-1": str(path)}}


def test_scratch_benchmark_measures_real_outputs_and_replayable_package(tmp_path, scratch_model):
    result = execute_deployment(deployment_spec(scratch_model), tmp_path / "deployment", lambda row: None, lambda: False)
    metrics = result["metrics"]
    assert metrics["inference_repetitions"] == 4
    assert metrics["model_artifact_bytes"] == scratch_model.stat().st_size
    assert metrics["loaded_state_dict_tensor_bytes"] > 0
    latency = metrics["latency_ms"]
    assert 0 < latency["min"] <= latency["p50"] <= latency["p95"] <= latency["max"]
    expected = generate_from_checkpoint(scratch_model, "pressure", 3)
    assert all(row["output"]["token_ids"] == expected["token_ids"] for row in result["records"])
    assert all(metrics[name] is None for name in ("model_ram_bytes", "flash_bytes", "energy_joules"))
    manifest = json.loads((tmp_path / "deployment/target.json").read_text())
    assert manifest["runtime_sha256"] and manifest["hardware"]["system"]
    assert "whole process" in manifest["measurements"]["memory"]["after_benchmark"]["scope"]
    package = tmp_path / "deployment/deployment-package.tar"
    extracted = extract_model_archive(package, tmp_path / "extracted")
    replay = run_packaged_inference(extracted, "pressure", 3)
    assert replay == result["records"][0]["output"]
    (extracted / "model.artifact").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        run_packaged_inference(extracted, "pressure", 3)


def test_gateway_and_mcu_reports_preserve_qualification_boundary(tmp_path, scratch_model):
    result = execute_deployment(deployment_spec(scratch_model, target="mcu.cortex_m"), tmp_path / "mcu", lambda row: None, lambda: False)
    assert result["qualification"]["host_inference_measured"]
    assert result["qualification"]["target_package_supported"] is False
    assert result["qualification"]["mcu_ready"] is False
    report = json.loads((tmp_path / "mcu/compatibility.json").read_text())
    assert report["selected"]["unsupported_reason"]
    assert report["operator_requirements"]
    gateway = target_compatibility("gateway", "scratch_language", {"system": "Darwin"})
    assert gateway["selected"]["package_supported"]
    assert not gateway["selected"]["measured_on_this_target_class"]


def test_deployment_checks_inputs_and_cancellation(tmp_path, scratch_model):
    bad = deployment_spec(scratch_model)
    bad["definition"]["inputs"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        execute_deployment(bad, tmp_path / "bad", lambda row: None, lambda: False)
    rows = []
    result = execute_deployment(deployment_spec(scratch_model), tmp_path / "cancel", rows.append, lambda: len(rows) == 2)
    assert result["qualification"]["status"] == "cancelled"
    assert result["metrics"]["inference_repetitions"] == 2


def test_pretrained_archive_is_actually_loaded_and_generates(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast
    directory = tmp_path / "hf"
    directory.mkdir()
    backend = Tokenizer(models.WordLevel({"<pad>": 0, "<eos>": 1, "<unk>": 2, "pressure": 3}, unk_token="<unk>"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, eos_token="<eos>", pad_token="<pad>", unk_token="<unk>")
    tokenizer.save_pretrained(directory)
    torch.manual_seed(3)
    GPT2LMHeadModel(GPT2Config(vocab_size=4, n_positions=32, n_embd=8, n_layer=1, n_head=2,
        eos_token_id=1, pad_token_id=0)).save_pretrained(directory)
    archive = tmp_path / "hf.blob"
    with tarfile.open(archive, "w") as bundle:
        for path in directory.iterdir():
            bundle.add(path, arcname=path.name)
    result = execute_deployment(deployment_spec(archive, prompt="pressure"), tmp_path / "deployed-hf", lambda row: None, lambda: False)
    assert result["metrics"]["inference_repetitions"] == 4
    assert all("completion" in row["output"] for row in result["records"])
    manifest = json.loads((tmp_path / "deployed-hf/target.json").read_text())
    assert manifest["model"]["kind"] == "pretrained_language"
    assert len({tuple(row["output"]["token_ids"]) for row in result["records"]}) == 1
