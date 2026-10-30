"""Offline real-model checks for the attempt-owned inference boundary."""
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

import pytest
import torch

from tinystudio.core.file_manifest import directory_manifest
from tinystudio.lab.engine import ExecutionCancelled
from tinystudio.lab.learning import ByteTokenizer, build_model
from tinystudio.lab.model_session import ModelSession


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive(root, target):
    with tarfile.open(target, "w") as bundle:
        for item in sorted(root.iterdir()):
            bundle.add(item, arcname=item.name)
    return target


def scratch(tmp_path, family="gru", context=12):
    tokenizer = ByteTokenizer()
    architecture = {"family": family, "hidden_size": 8, "layers": 1, "context": context}
    if family == "transformer":
        architecture["heads"] = 2
    model = build_model(architecture, tokenizer.vocab_size)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.head.bias[ord("x")] = 1
    path = tmp_path / f"{family}.pt"
    torch.save({"format": "tinystudio.learning.v1", "architecture": architecture,
                "tokenizer": tokenizer.state(), "model": model.state_dict()}, path)
    return path


@pytest.fixture
def hf_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    from tokenizers import Tokenizer, models, pre_tokenizers, processors
    from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast
    root = tmp_path / "hf"
    root.mkdir()
    backend = Tokenizer(models.WordLevel({"<pad>": 0, "<unk>": 1, "<bos>": 2, "<eos>": 3, "pressure": 4},
                                         unk_token="<unk>"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    backend.post_processor = processors.TemplateProcessing(single="<bos> $A", special_tokens=[("<bos>", 2)])
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, pad_token="<pad>", unk_token="<unk>",
                                       bos_token="<bos>", eos_token="<eos>")
    tokenizer.save_pretrained(root)
    model = GPT2LMHeadModel(GPT2Config(vocab_size=5, n_positions=8, n_embd=8, n_layer=1, n_head=2,
                                     eos_token_id=3, bos_token_id=2, pad_token_id=0))
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
    model.save_pretrained(root)
    return root


def retained(tmp_path, hf_root, strategy):
    root = tmp_path / f"retained-{strategy}"
    root.mkdir()
    shutil.copytree(hf_root, root / "source-model")
    target = root / ("model" if strategy == "full" else "adapter")
    if strategy == "full":
        shutil.copytree(hf_root, target)
    else:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import LoraConfig, get_peft_model
        model = get_peft_model(AutoModelForCausalLM.from_pretrained(hf_root, local_files_only=True),
                              LoraConfig(r=2, lora_alpha=4, target_modules=["c_attn"], task_type="CAUSAL_LM"))
        model.save_pretrained(target)
        AutoTokenizer.from_pretrained(hf_root, local_files_only=True).save_pretrained(target)
    manifest = {"format": "tinystudio.pretrained.v1", "training_type": strategy, "update_strategy": strategy,
                "output_kind": "model" if strategy == "full" else "adapter", "trained_directory": target.name,
                "source_files": directory_manifest(root / "source-model"), "trained_files": directory_manifest(target)}
    (root / "pretrained.json").write_text(json.dumps(manifest))
    return root


@pytest.mark.parametrize("family", ["gru", "transformer"])
def test_scratch_exact_prompt_greedy_boundary_and_reload(tmp_path, family):
    path = scratch(tmp_path, family)
    work = tmp_path / "work"
    with ModelSession(path, sha(path), work) as session:
        result = session.generate(" \nμ", 8)  # Four UTF-8 bytes, plus eight reserved tokens.
        assert result == {"prompt": " \nμ", "input_token_ids": [32, 10, 206, 188], "prompt_tokens": 4,
                          "token_ids": [120] * 8, "completion": "x" * 8, "generated_tokens": 8,
                          "context_capacity": 12, "max_new_tokens": 8}
        assert session.generate(" \nμ", 8) == result
        assert session.metadata["device"] == "cpu"
        assert session.metadata["provenance"]["architecture"]["family"] == family
        receipt = session.metadata
        receipt["device"] = "cuda"
        assert session.metadata["device"] == "cpu"
    assert not list(work.iterdir())
    session.close()
    with pytest.raises(RuntimeError, match="closed"):
        session.generate("x", 1)
    with pytest.raises(RuntimeError, match="closed"):
        session.__enter__()
    with ModelSession(path, sha(path), work) as reloaded:
        assert reloaded.generate(" \nμ", 8) == result


@pytest.mark.parametrize("kind", ["base", "full", "lora"])
def test_hf_plain_full_lora_special_tokens_boundary_reload(tmp_path, hf_root, kind):
    root = hf_root if kind == "base" else retained(tmp_path, hf_root, kind)
    path = archive(root, tmp_path / "model.tar")
    work = tmp_path / "work"
    with ModelSession(path, sha(path), work) as session:
        result = session.generate("pressure pressure", 5)
        assert result["input_token_ids"] == [2, 4, 4]
        assert result["prompt"] == "pressure pressure"
        assert result["prompt_tokens"] == 3
        assert result["context_capacity"] == 8
        assert result["token_ids"] == [0] * 5
        assert result["generated_tokens"] == 5
        assert result["max_new_tokens"] == 5
        assert result["completion"] == ""
        assert session.generate("pressure pressure", 5) == result
        assert session.metadata["model_devices"] == ["cpu"]
        assert (work / Path(session._temporary.name).name / "unpacked").is_dir()
    assert not list(work.iterdir())
    with ModelSession(path, sha(path), work) as session:
        assert session.generate("pressure pressure", 5) == result


@pytest.mark.parametrize("kind", ["scratch", "hf"])
def test_context_overflow_counts_real_special_tokens_and_closes(tmp_path, hf_root, kind):
    path = scratch(tmp_path) if kind == "scratch" else archive(hf_root, tmp_path / "model.tar")
    work = tmp_path / "work"
    with pytest.raises(ValueError, match="context capacity"):
        with ModelSession(path, sha(path), work) as session:
            session.generate("pressure", 5 if kind == "scratch" else 7)
    assert not list(work.iterdir())


def test_zero_generation_empty_scratch_seed(tmp_path):
    path = scratch(tmp_path)
    with ModelSession(path, sha(path), tmp_path / "work") as session:
        result = session.generate("", 0)
        assert result["prompt"] == ""
        assert result["input_token_ids"] == [256]
        assert result["prompt_tokens"] == 1
        assert result["generated_tokens"] == 0
        assert result["completion"] == ""


@pytest.mark.parametrize("kind", ["scratch", "hf"])
@pytest.mark.parametrize("during", [False, True])
def test_cancel_before_or_during_generation_cleans_up(tmp_path, hf_root, kind, during):
    path = scratch(tmp_path) if kind == "scratch" else archive(hf_root, tmp_path / "model.tar")
    work = tmp_path / "work"
    session = ModelSession(path, sha(path), work)
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= (4 if during else 1)
    with pytest.raises(ExecutionCancelled):
        session.generate("pressure", 4, cancelled=cancelled)
    assert calls >= (4 if during else 1)
    assert not list(work.iterdir())
    assert session._model is None
    with pytest.raises(RuntimeError, match="closed"):
        session.generate("pressure", 1)


def test_outer_hash_tamper_rejected_before_any_loader(tmp_path, monkeypatch):
    path = scratch(tmp_path)
    digest = sha(path)
    path.write_bytes(path.read_bytes() + b"tampered")
    def forbidden(*args, **kwargs):
        pytest.fail("loader ran before checksum validation")
    monkeypatch.setattr(torch, "load", forbidden)
    work = tmp_path / "work"
    with pytest.raises(ValueError, match="checksum"):
        ModelSession(path, digest, work)
    assert not list(work.iterdir())


@pytest.mark.parametrize("strategy,target", [("full", "model"), ("lora", "source-model"), ("lora", "adapter")])
def test_retained_internal_hash_tamper(tmp_path, hf_root, strategy, target):
    root = retained(tmp_path, hf_root, strategy)
    (root / target / "tokenizer_config.json").write_text("{}")
    path = archive(root, tmp_path / "model.tar")
    work = tmp_path / "work"
    with pytest.raises(ValueError, match="checksum mismatch"):
        ModelSession(path, sha(path), work)
    assert not list(work.iterdir())


def test_requested_unsupported_device_fails_without_fallback(tmp_path):
    path = scratch(tmp_path)
    with pytest.raises(ValueError, match="unavailable"):
        ModelSession(path, sha(path), tmp_path / "work", device="meta")


def test_unknown_capacity_is_not_inferred_from_tokenizer(tmp_path, hf_root, monkeypatch):
    from transformers import AutoModelForCausalLM
    original = AutoModelForCausalLM.from_pretrained
    def missing_capacity(*args, **kwargs):
        model = original(*args, **kwargs)
        from types import SimpleNamespace
        model.config = SimpleNamespace()
        return model
    monkeypatch.setattr(AutoModelForCausalLM, "from_pretrained", missing_capacity)
    path = archive(hf_root, tmp_path / "model.tar")
    work = tmp_path / "work"
    with pytest.raises(ValueError, match="capacity is unknown"):
        ModelSession(path, sha(path), work)
    assert not list(work.iterdir())


def test_loading_failure_and_body_exception_preserve_other_attempts(tmp_path):
    path = scratch(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    marker = work / "foreign"
    marker.write_text("preserve")
    with pytest.raises(LookupError):
        with ModelSession(path, sha(path), work):
            raise LookupError("attempt failed")
    assert list(work.iterdir()) == [marker]
    path.write_text("not a model")
    with pytest.raises((ValueError, tarfile.ReadError)):
        ModelSession(path, sha(path), work)
    assert list(work.iterdir()) == [marker]


@pytest.mark.parametrize("device", [None, ""])
def test_device_cannot_fall_back_to_environment(tmp_path, monkeypatch, device):
    monkeypatch.setenv("TINYSTUDIO_DEVICE", "cpu")
    path = scratch(tmp_path)
    with pytest.raises(ValueError, match="device must be explicit"):
        ModelSession(path, sha(path), tmp_path / "work", device=device)


def test_scratch_eos_is_retained_as_generated_token(tmp_path):
    path = scratch(tmp_path)
    state = torch.load(path, weights_only=True)
    state["model"]["head.bias"].zero_()
    state["model"]["head.bias"][256] = 1
    torch.save(state, path)
    with ModelSession(path, sha(path), tmp_path / "work") as session:
        result = session.generate("x", 4)
        assert result["token_ids"] == [256]
        assert result["completion"] == ""
        assert result["generated_tokens"] == 1
        assert result["max_new_tokens"] == 4


def test_quantized_runtime_mismatch_never_loads_float_fallback(tmp_path, hf_root, monkeypatch):
    # Controlled incompatible runtime contract: no claimed live NF4 acceptance.
    root = retained(tmp_path, hf_root, "lora")
    (root / "source-model").rename(root / "quantized-base")
    probe = root / "reload-probes.safetensors"
    probe.write_bytes(b"runtime rejection must occur before reading probes")
    manifest_path = root / "pretrained.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(format="tinystudio.pretrained.v2", output_kind="quantized_adapter",
                    training_type="qlora", update_strategy="qlora")
    manifest["quantization"] = {"format": "tinystudio.nf4-base.v1", "directory": "quantized-base",
        "files": directory_manifest(root / "quantized-base"), "runtime": {"os": "incompatible-runtime"}}
    manifest["reload_probes"] = {"file": probe.name, "sha256": sha(probe), "inputs": [{}, {}, {}]}
    manifest_path.write_text(json.dumps(manifest))
    path = archive(root, tmp_path / "qlora.tar")
    from transformers import AutoModelForCausalLM
    def forbidden(*args, **kwargs):
        pytest.fail("incompatible QLoRA must not load any float fallback")
    monkeypatch.setattr(AutoModelForCausalLM, "from_pretrained", forbidden)
    work = tmp_path / "work"
    with pytest.raises(ValueError, match="compatible retained runtime"):
        ModelSession(path, sha(path), work)
    assert not list(work.iterdir())
