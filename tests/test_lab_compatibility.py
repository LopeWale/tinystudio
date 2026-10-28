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


@pytest.mark.parametrize("objective", ["sft", "causal_lm", "dpo"])
@pytest.mark.parametrize("strategy", ["full", "lora"])
def test_independent_objective_update_placement_and_reload(tmp_path, local_pretrained_spec, monkeypatch, objective, strategy):
    from trl import DPOTrainer, SFTTrainer
    experiment = local_pretrained_spec
    experiment["definition"]["learning"].update(objective=objective, update_strategy=strategy, device="cpu", dtype="float32")
    # A per-run CPU selection must override defaults without changing the environment.
    monkeypatch.setenv("TINYSTUDIO_DEVICE", "cuda:7")
    monkeypatch.setenv("TINYSTUDIO_DTYPE", "bf16")
    snapshot = Path(experiment["input_paths"]["snapshot-1"])
    texts = ["  pressure -1.25 V\nμA\t", "sensor ready 0.5 A", "pressure high", "pressure low", "FINAL_TEST_ONLY"]
    rows = [json.loads(line) for line in snapshot.read_text().splitlines()]
    for index, row in enumerate(rows):
        row.update(partition="train" if index < 3 else "validation" if index == 3 else "test", source_record_id=f"record:{index}")
        if objective == "causal_lm":
            row.pop("messages")
            row["text"] = texts[index]
    snapshot.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    experiment["definition"]["inputs"][1]["sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    observed = {}
    trainer_type = DPOTrainer if objective == "dpo" else SFTTrainer
    original = trainer_type.train

    def observe_training(trainer, *args, **kwargs):
        before = {name: parameter.detach().clone() for name, parameter in trainer.model.named_parameters()}
        trainable = {name for name, parameter in trainer.model.named_parameters() if parameter.requires_grad}
        assert trainable
        assert all("lora_" in name for name in trainable) if strategy == "lora" else all("lora_" not in name for name in trainable)
        reference_before, ref_logps_before = {}, None
        if objective == "dpo":
            reference = trainer.ref_model if trainer.ref_model is not None else trainer.model
            reference_before = {name: parameter.detach().clone() for name, parameter in reference.named_parameters() if "lora_" not in name}
            batch = trainer._prepare_inputs(next(iter(trainer.get_train_dataloader())))
            if trainer.ref_model is not None:
                assert trainer.ref_model is not trainer.model
                assert not any(parameter.requires_grad for parameter in trainer.ref_model.parameters())
            ref_logps_before = tuple(value.detach().clone() for value in trainer.compute_ref_log_probs(reference, batch))
        result = original(trainer, *args, **kwargs)
        changed = {name for name, parameter in trainer.model.named_parameters() if not torch.equal(before[name], parameter)}
        assert changed and changed <= trainable
        optimizer = {id(parameter) for group in trainer.optimizer.param_groups for parameter in group["params"]}
        assert optimizer == {id(parameter) for parameter in trainer.model.parameters() if parameter.requires_grad}
        if objective == "dpo":
            assert all(torch.equal(reference_before[name], parameter) for name, parameter in reference.named_parameters() if name in reference_before)
            after = trainer.compute_ref_log_probs(reference, batch)
            assert all(torch.equal(left, right) for left, right in zip(ref_logps_before, after))
            if trainer.ref_model is not None:
                assert optimizer.isdisjoint({id(parameter) for parameter in trainer.ref_model.parameters()})
        observed.update(changed=sorted(changed), trainable=sorted(trainable))
        return result

    monkeypatch.setattr(trainer_type, "train", observe_training)
    result = execute_pretrained(experiment, tmp_path / "independent", lambda row: None, lambda: False)
    model, tokenizer, manifest = load_pretrained_output(tmp_path / "independent")
    assert manifest["objective"] == objective and manifest["update_strategy"] == strategy
    assert manifest["output_kind"] == ("model" if strategy == "full" else "adapter")
    placement = manifest["metrics"]["placement"]
    assert placement["model_devices"] == ["cpu"] and placement["batch_devices"] == ["cpu"]
    assert placement["model_dtypes"] == ["float32"] and placement["prepared_batches"] > 0
    assert result["qualification"]["excluded_final_test_records"] == 1
    assert observed["changed"]
    if objective == "causal_lm":
        contract = manifest["metrics"]["data_contract"]
        assert contract["chat_template_applied"] is False
        assert contract["train"][0]["text_sha256"] == hashlib.sha256(texts[0].encode()).hexdigest()
        assert [row["source_record_id"] for row in contract["train"]] == ["record:0", "record:1", "record:2"]
    probe = tokenizer("pressure high", return_tensors="pt")
    with torch.no_grad():
        expected = model(**probe).logits.clone()
    del model
    restored, _, _ = load_pretrained_output(tmp_path / "independent")
    with torch.no_grad():
        assert torch.equal(expected, restored(**probe).logits)
    import os
    assert os.environ["TINYSTUDIO_DEVICE"] == "cuda:7" and os.environ["TINYSTUDIO_DTYPE"] == "bf16"


@pytest.mark.parametrize("boundary", ["step", "saved", "bundled"])
def test_cancellation_never_publishes_partial_pretrained_model(tmp_path, local_pretrained_spec, monkeypatch, boundary):
    import tinystudio.lab.compatibility as compatibility
    experiment = local_pretrained_spec
    output = tmp_path / boundary
    state = {"cancel": False}
    original_train = compatibility.execute_pretrained.__globals__["load_pretrained_output"]
    original_bundle = compatibility._bundle
    def emit(row):
        if boundary == "step" and row.get("step") == 1:
            state["cancel"] = True
    def load(path, **kwargs):
        result = original_train(path, **kwargs)
        if boundary == "saved":
            state["cancel"] = True
        return result
    def bundle(*args):
        original_bundle(*args)
        if boundary == "bundled":
            state["cancel"] = True
    monkeypatch.setattr(compatibility, "load_pretrained_output", load)
    monkeypatch.setattr(compatibility, "_bundle", bundle)
    result = execute_pretrained(experiment, output, emit, lambda: state["cancel"])
    assert state["cancel"]
    assert result["qualification"]["status"] == "cancelled" and not result["artifacts"]
    assert result["qualification"]["partial_model_published"] is False
    assert not any((output / name).exists() for name in ("pretrained.json", "pretrained-model.tar", "model", "adapter", "checkpoints"))


def test_malformed_dpo_pair_is_not_silently_dropped(tmp_path, local_pretrained_spec):
    from tinystudio.core.project import ProjectError
    experiment = local_pretrained_spec
    experiment["definition"]["learning"].update(objective="dpo", update_strategy="full")
    source = Path(experiment["input_paths"]["snapshot-1"])
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    for index, row in enumerate(rows):
        row["partition"] = "train" if index < 3 else "validation"
    rows[1].pop("rejected")
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    experiment["definition"]["inputs"][1]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ProjectError, match="DPO row 1"):
        execute_pretrained(experiment, tmp_path / "invalid-pair", lambda row: None, lambda: False)
    assert not (tmp_path / "invalid-pair/pretrained.json").exists()


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


def test_cpu_qlora_is_rejected_before_artifact_access_on_cuda_host(tmp_path, monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr("tinystudio.training.quantization.cpu_pack_compatible", lambda: False)
    experiment = {"definition": {"learning": {"update_strategy": "qlora", "device": "cpu", "dtype": "float32"}}}
    with pytest.raises(ValueError, match="requires a CUDA target"):
        execute_pretrained(experiment, tmp_path / "must-stay-absent", lambda row: None, lambda: False)
    assert not (tmp_path / "must-stay-absent").exists()


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
    ({"update_strategy": "lora", "objective": "unknown"}, {}, "unsupported objective"),
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
