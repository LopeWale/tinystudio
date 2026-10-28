"""Local generated HF fixtures exercise TinyStudio's real existing trainer."""
import hashlib
import json
import io
import tarfile
import zipfile
from pathlib import Path

import pytest
import torch

from tinystudio.lab.compatibility import directory_manifest, execute_pretrained, extract_model_archive, load_pretrained_output, resolve_role


@pytest.fixture
def local_pretrained_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYSTUDIO_DEVICE", "cpu")
    monkeypatch.setenv("TINYSTUDIO_DTYPE", "fp32")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast
    directory = tmp_path / "local-model"
    directory.mkdir()
    vocabulary = {token: index for index, token in enumerate(["<pad>", "<eos>", "<unk>", "user", "assistant", ":", "pressure", "normal", "high", "low", "yes", "no", "sensor", "ready"])}
    backend = Tokenizer(models.WordLevel(vocabulary, unk_token="<unk>"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, eos_token="<eos>", pad_token="<pad>", unk_token="<unk>")
    tokenizer.chat_template = "{% for message in messages %}{{ message['role'] + ': ' + message['content'] + ' ' }}{% endfor %}{{ eos_token }}"
    tokenizer.save_pretrained(directory)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(23)
        model = GPT2LMHeadModel(GPT2Config(vocab_size=len(vocabulary), n_positions=64, n_embd=8,
            n_layer=1, n_head=2, resid_pdrop=0, embd_pdrop=0, attn_pdrop=0, eos_token_id=1, pad_token_id=0))
        model.save_pretrained(directory)
    archive = tmp_path / "model.blob"
    with tarfile.open(archive, "w") as bundle:
        for source in directory.iterdir():
            bundle.add(source, arcname=source.name)
    snapshot = tmp_path / ("a" * 64)  # Artifact storage uses opaque checksum paths.
    rows = [{"messages": [{"role": "user", "content": "pressure high"},
              {"role": "assistant", "content": "yes"}], "rejected": "no"} for _ in range(5)]
    snapshot.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return {"definition": {"seed": 2, "learning": {"initialization": "pretrained", "update_strategy": "full", "objective": "sft"},
        "parameters": {"steps": 2, "context": 32, "learning_rate": 0.01, "lora_r": 2, "lora_alpha": 4,
                       "lora_dropout": 0, "lora_targets": ["c_attn"]},
        "inputs": [{"artifact_id": "model-1", "role": "pretrained_model", "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
                   {"artifact_id": "snapshot-1", "role": "snapshot", "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest()}]},
        "_fixture_model_dir": str(directory),
        "input_paths": {"model-1": {"path": str(archive)}, "snapshot-1": str(snapshot)},
        "compatibility": {"job_id": "existing-job", "experiment_number": 3, "snapshot_id": "snapshot-1", "base_model_revision": "immutable-test"}}


@pytest.mark.parametrize("strategy", ["full", "lora", "dpo"])
def test_real_existing_trainer_from_immutable_local_model(tmp_path, local_pretrained_spec, strategy):
    from transformers import AutoModelForCausalLM, AutoTokenizer
    experiment = local_pretrained_spec
    experiment["definition"]["learning"]["update_strategy"] = strategy
    if strategy == "dpo":
        experiment["definition"]["learning"]["objective"] = "dpo"
    directory = Path(experiment["_fixture_model_dir"])
    base = AutoModelForCausalLM.from_pretrained(directory, local_files_only=True)
    tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True)
    base.eval()
    inputs = tokenizer("user: pressure high assistant:", return_tensors="pt")
    with torch.no_grad():
        expected = base(**inputs).logits.clone()
    del base
    result = execute_pretrained(experiment, tmp_path / "trained", lambda row: None, lambda: False)
    assert result["metrics"]["steps"] == 2
    assert result["qualification"]["saved_model_reloaded"]
    assert (tmp_path / "trained/pretrained-model.tar").is_file()
    reloaded, _, manifest = load_pretrained_output(tmp_path / "trained")
    with torch.no_grad():
        actual = reloaded(**inputs).logits
    assert not torch.equal(expected, actual), "training must alter observable model predictions"
    assert torch.isfinite(actual).all()
    assert manifest["compatibility"]["job_id"] == "existing-job"
    assert manifest["snapshot_input"]["sha256"] == experiment["definition"]["inputs"][1]["sha256"]
    del reloaded
    again, _, _ = load_pretrained_output(tmp_path / "trained")
    with torch.no_grad():
        assert torch.equal(actual, again(**inputs).logits)
    if strategy != "full":
        assert result["metrics"]["trainable_parameters"] < result["metrics"]["total_parameters"]


def test_directory_binding_rejects_source_mutation(tmp_path, local_pretrained_spec):
    archive = Path(local_pretrained_spec["input_paths"]["model-1"]["path"])
    archive.write_bytes(b"altered archive")
    with pytest.raises(ValueError, match="checksum mismatch"):
        resolve_role(local_pretrained_spec, "pretrained_model", directory=True, extract_dir=tmp_path / "extracted")


def test_directory_binding_rejects_links(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "data").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="symbolic"):
        directory_manifest(model)


def test_authored_model_path_cannot_bypass_artifact_binding(tmp_path, local_pretrained_spec):
    local_pretrained_spec["definition"]["parameters"]["base_model"] = "/arbitrary/model"
    with pytest.raises(ValueError, match="immutable input"):
        execute_pretrained(local_pretrained_spec, tmp_path / "out", lambda row: None, lambda: False)


@pytest.mark.parametrize("format", ["tar", "zip"])
def test_archive_rejects_traversal(tmp_path, format):
    source = tmp_path / "bad.blob"
    if format == "tar":
        with tarfile.open(source, "w") as bundle:
            info = tarfile.TarInfo("../escaped.txt")
            info.size = 4
            bundle.addfile(info, io.BytesIO(b"evil"))
    else:
        with zipfile.ZipFile(source, "w") as bundle:
            bundle.writestr("../escaped.txt", b"evil")
    with pytest.raises(ValueError, match="unsafe"):
        extract_model_archive(source, tmp_path / "extracted")
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "extracted").exists()


def test_archive_rejects_links_and_expansion_bounds(tmp_path):
    source = tmp_path / "bad.tar"
    with tarfile.open(source, "w") as bundle:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/outside"
        bundle.addfile(info)
    with pytest.raises(ValueError, match="regular"):
        extract_model_archive(source, tmp_path / "links")
    with zipfile.ZipFile(source, "w") as bundle:
        bundle.writestr("huge", b"1234567890")
    with pytest.raises(ValueError, match="bounds"):
        extract_model_archive(source, tmp_path / "huge", max_bytes=4)


def test_archive_binding_retains_outer_and_inner_identity(tmp_path, local_pretrained_spec):
    binding = resolve_role(local_pretrained_spec, "pretrained_model", directory=True, extract_dir=tmp_path / "resolved")
    assert binding["sha256"] == local_pretrained_spec["definition"]["inputs"][0]["sha256"]
    assert binding["directory_sha256"] == directory_manifest(local_pretrained_spec["_fixture_model_dir"])["sha256"]
    assert binding["sha256"] != binding["directory_sha256"]


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_real_local_embedding_backend_retains_vectors_and_query_trace(tmp_path, local_pretrained_spec, mode):
    from tinystudio.lab.retrieval import execute_retrieval
    experiment = local_pretrained_spec
    model = experiment["definition"]["inputs"][0]
    model["role"] = "embedding_model"
    experiment["definition"]["inputs"] = [model]
    experiment["definition"]["parameters"] = {"mode": mode, "records": [
        {"text": "pressure normal", "unit": "A"}, {"text": "sensor ready", "unit": "B"}],
        "queries": ["pressure normal"], "top_k": 2}
    result = execute_retrieval(experiment, tmp_path / "retrieval", lambda row: None, lambda: False)
    row = result["records"][0]
    assert row["hits"][0]["content"]["text"] == "pressure normal"
    assert row["hits"][0]["semantic_score"] == pytest.approx(1.0, abs=1e-5)
    assert row["trace"]["embedding_model"]["artifact_sha256"] == model["sha256"]
    assert len(row["trace"]["query_embedding"]) == 8
    index = json.loads(Path(result["artifacts"][0]["path"]).read_text())
    assert all(len(document["embedding"]) == 8 for document in index["documents"])
    if mode == "hybrid":
        assert row["scoring"] == "reciprocal-rank-fusion-k=60"
        assert row["hits"][0]["lexical_score"] > 0


def test_role_model_source_rejects_raw_directory(tmp_path, local_pretrained_spec):
    local_pretrained_spec["input_paths"]["model-1"] = local_pretrained_spec["_fixture_model_dir"]
    with pytest.raises(ValueError, match="artifact file"):
        resolve_role(local_pretrained_spec, "pretrained_model", directory=True, extract_dir=tmp_path / "model")


def test_semantic_mode_does_not_silently_fall_back_without_model(tmp_path):
    from tinystudio.lab.retrieval import execute_retrieval
    experiment = {"definition": {"parameters": {"mode": "semantic", "records": [{"text": "pressure"}], "query": "pressure"}}}
    with pytest.raises(ValueError, match="embedding_model"):
        execute_retrieval(experiment, tmp_path, lambda row: None, lambda: False)


def test_snapshot_partitions_preserve_final_test_and_reject_episode_leakage():
    from tinystudio.lab.compatibility import partition_snapshot
    rows = [{"source_record_id": "snap:train:0", "partition": "train", "episode_id": "train-episode"},
            {"source_record_id": "snap:train:1", "partition": "train", "episode_id": "train-episode"},
            {"source_record_id": "snap:validation:0", "partition": "validation", "episode_id": "validation-episode"},
            {"source_record_id": "snap:test:0", "partition": "test", "episode_id": "final-episode"}]
    split = partition_snapshot(rows, 3, 0.2)
    assert split["train"] == [0, 1]
    assert split["validation"] == [2]
    assert split["excluded_test"] == [3]
    assert split["partitions"]["test"]["records"][0]["source_record_id"] == "snap:test:0"
    assert split["partitions"]["train"]["sha256"] != split["partitions"]["test"]["sha256"]
    rows[2]["episode_id"] = "train-episode"
    with pytest.raises(ValueError, match="overlaps"):
        partition_snapshot(rows, 3, 0.2)


def test_unpartitioned_windows_are_split_as_whole_episode_groups():
    from tinystudio.lab.compatibility import partition_snapshot
    rows = [{"episode_id": episode, "window": offset} for episode in ("A", "B", "C") for offset in range(3)]
    split = partition_snapshot(rows, 3, 0.3)
    train_groups = {rows[index]["episode_id"] for index in split["train"]}
    validation_groups = {rows[index]["episode_id"] for index in split["validation"]}
    assert train_groups.isdisjoint(validation_groups)
    assert len(split["validation"]) == 3
    assert split["tokenizer_fitted"] is False


def test_device_policy_rejects_cross_partition_overlap_even_with_different_episodes():
    from tinystudio.lab.compatibility import partition_snapshot
    rows = [{"partition": "train", "episode_id": "A", "device_id": "same-device"},
            {"partition": "train", "episode_id": "B", "device_id": "same-device"},
            {"partition": "validation", "episode_id": "C", "device_id": "same-device"}]
    with pytest.raises(ValueError, match="overlaps"):
        partition_snapshot(rows, 2, 0.2, {"group_by": ["episode_id", "device_id"]})


def test_real_pretrained_wrapper_honors_imported_snapshot_partitions(tmp_path, local_pretrained_spec):
    experiment = local_pretrained_spec
    path = Path(experiment["input_paths"]["snapshot-1"])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for index, row in enumerate(rows):
        row["partition"] = "train" if index < 3 else "validation" if index == 3 else "test"
        row["source_record_id"] = f"snapshot:{row['partition']}:{index}"
        row["episode_id"] = row["partition"]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    experiment["definition"]["inputs"][1]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = execute_pretrained(experiment, tmp_path / "partitioned", lambda row: None, lambda: False)
    manifest = json.loads((tmp_path / "partitioned/partitions.json").read_text())
    assert manifest["train"] == [0, 1, 2]
    assert manifest["validation"] == [3]
    assert manifest["excluded_test"] == [4]
    assert result["qualification"]["excluded_final_test_records"] == 1
    assert any(artifact["role"] == "partition_manifest" for artifact in result["artifacts"])


@pytest.mark.parametrize("learning,parameters,match", [
    ({"initialization": "scratch"}, {}, "unsupported initialization"),
    ({"update_strategy": "lora", "objective": "dpo"}, {}, "incompatible objective"),
    ({"update_strategy": "dpo", "objective": "sft"}, {}, "incompatible objective"),
    ({"update_strategy": "sgd"}, {}, "unsupported update_strategy"),
    ({"architecture": {"hidden_size": 32}}, {}, "unsupported architecture"),
    ({"architecture": {"context": 16}}, {"context": 32}, "conflicting"),
    ({"architecture": {"context": 16}}, {"max_length": 32}, "conflicting"),
    ({"architecture": []}, {}, "must be an object"),
    ({"device": "cuda"}, {}, "unavailable"),
    ({"training_dtype": "bf16"}, {}, "unavailable"),
    ({"representation": "byte"}, {}, "immutable model tokenizer"),
    ({"family": "gru"}, {}, "immutable artifact"),
    ({"update_strategy": "lora"}, {"training_type": "full"}, "conflicting"),
])
def test_pretrained_rejects_incompatible_axes_before_artifact_access(tmp_path, monkeypatch, learning, parameters, match):
    monkeypatch.setenv("TINYSTUDIO_DEVICE", "cpu")
    monkeypatch.setenv("TINYSTUDIO_DTYPE", "fp32")
    # No input refs or paths exist: the intended contract error must precede any resolution.
    experiment = {"definition": {"learning": learning, "parameters": parameters}}
    with pytest.raises(ValueError, match=match):
        execute_pretrained(experiment, tmp_path / "must-stay-absent", lambda row: None, lambda: False)
    assert not (tmp_path / "must-stay-absent").exists()


def test_nested_pretrained_context_and_runtime_match_actual_training(tmp_path, local_pretrained_spec):
    experiment = local_pretrained_spec
    experiment["definition"]["parameters"].pop("context")
    experiment["definition"]["learning"].update(architecture={"context": 24}, device="cpu", training_dtype="fp32")
    result = execute_pretrained(experiment, tmp_path / "nested", lambda row: None, lambda: False)
    manifest = json.loads((tmp_path / "nested/pretrained.json").read_text())
    assert manifest["config"]["max_length"] == 24
    assert manifest["axes"]["architecture"] == {"context": 24}
    assert manifest["axes"]["initialization"] == "pretrained"
    assert manifest["axes"]["device"] == "cpu" and manifest["axes"]["dtype"] == "float32"
    assert result["qualification"]["requested_axes_honored"]
    assert result["qualification"]["training_type"] == "full"


def test_pretrained_external_validation_is_evaluator_only_and_excludes_test_refs(tmp_path, local_pretrained_spec):
    experiment = local_pretrained_spec
    source = Path(experiment["input_paths"]["snapshot-1"])
    examples = [json.loads(line) for line in source.read_text().splitlines()]
    for index, row in enumerate(examples):
        row.update(source_record_id=f"train:{index}", episode_id="train")
    source.write_text("".join(json.dumps(row) + "\n" for row in examples))
    experiment["definition"]["inputs"][1]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    validation = tmp_path / "validation.blob"
    held_out = {**examples[0], "source_record_id": "validation:0", "episode_id": "validation", "visibility": "evaluation"}
    validation.write_text(json.dumps(held_out))
    experiment["definition"]["inputs"].extend([
        {"artifact_id": "validation-1", "role": "validation", "visibility": "evaluation", "sha256": hashlib.sha256(validation.read_bytes()).hexdigest()},
        {"artifact_id": "final-test", "role": "final_test", "visibility": "evaluation", "sha256": "0" * 64},
        {"artifact_id": "truth", "role": "source", "visibility": "truth", "sha256": "0" * 64}])
    experiment["evaluation_input_paths"] = {"validation-1": str(validation), "final-test": "/does/not/exist", "truth": "/does/not/exist"}
    execute_pretrained(experiment, tmp_path / "validation-run", lambda row: None, lambda: False)
    manifest = json.loads((tmp_path / "validation-run/pretrained.json").read_text())
    assert manifest["split"]["train"] == [0, 1, 2, 3, 4]
    assert manifest["split"]["validation"] == [5]
    assert manifest["split"]["partitions"]["validation"]["records"][0]["source_record_id"] == "validation:0"
    assert {entry["artifact_id"] for entry in manifest["validation_inputs"]} == {"validation-1"}
    assert "final-test" not in json.dumps(manifest["split"])
    experiment.pop("evaluation_input_paths")
    experiment["input_paths"]["validation-1"] = str(validation)
    with pytest.raises(ValueError, match="evaluator-only"):
        execute_pretrained(experiment, tmp_path / "wrong-map", lambda row: None, lambda: False)


@pytest.mark.parametrize("field,value", [("partition", "test"), ("visibility", "truth"), ("true_value", 42)])
def test_pretrained_validation_cannot_relabel_test_or_truth(tmp_path, local_pretrained_spec, field, value):
    experiment = local_pretrained_spec
    validation = tmp_path / "bad-validation.blob"
    row = {"messages": [{"role": "user", "content": "pressure"}, {"role": "assistant", "content": "yes"}], field: value}
    validation.write_text(json.dumps(row))
    experiment["definition"]["inputs"].append({"artifact_id": "validation", "role": "validation", "visibility": "evaluation",
        "sha256": hashlib.sha256(validation.read_bytes()).hexdigest()})
    experiment["evaluation_input_paths"] = {"validation": str(validation)}
    with pytest.raises(ValueError, match="truth|non-validation"):
        execute_pretrained(experiment, tmp_path / "bad-validation", lambda row: None, lambda: False)


def test_pretrained_context_cannot_exceed_immutable_model_capacity(tmp_path, local_pretrained_spec):
    experiment = local_pretrained_spec
    experiment["definition"]["parameters"].pop("context")
    experiment["definition"]["learning"]["architecture"] = {"context": 65}
    with pytest.raises(ValueError, match="exceeds.*capacity"):
        execute_pretrained(experiment, tmp_path / "too-long", lambda row: None, lambda: False)
    assert not (tmp_path / "too-long/source-model").exists()
