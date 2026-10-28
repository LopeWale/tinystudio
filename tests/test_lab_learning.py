"""Pure CPU learning/retrieval checks; no shared database fixtures."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest
import torch

from tinystudio.lab.learning import ByteTokenizer, build_model, execute_learning, generate_from_checkpoint, load_checkpoint
from tinystudio.lab.retrieval import build_index, execute_retrieval, query_index


def specification(family="gru", steps=8, **parameters):
    spec = {"definition": {"seed": 17, "engine": "learning.language",
        "learning": {"family": family, "initialization": "scratch", "representation": "byte", "objective": "causal_lm"},
        "parameters": {"texts": ["valve open valve closed " * 2, "pressure stable pressure stable " * 2,
            "pump ready pump running " * 2, "sensor calibrated sensor calibrated " * 2],
            "steps": steps, "hidden_size": 8, **({"heads": 2} if family == "transformer" else {}), "context": 12,
            "max_new_tokens": 3, **parameters}}, "input_paths": {}}
    for key, role in (("resume_from", "resume"), ("teacher_checkpoint", "teacher")):
        path = spec["definition"]["parameters"].pop(key, None)
        if path:
            spec["definition"].setdefault("inputs", []).append({"artifact_id": role, "role": role,
                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()})
            spec["input_paths"][role] = str(path)
    return spec


@pytest.mark.parametrize("family", ["gru", "transformer"])
def test_scratch_training_changes_weights_and_reloads(tmp_path, family):
    rows = []
    result = execute_learning(specification(family), tmp_path, rows.append, lambda: False)
    assert result["metrics"]["parameters_changed"] is True
    assert result["metrics"]["reload_logits_equal"] is True
    assert result["metrics"]["validation_documents"] == 1
    assert result["metrics"]["training_documents"] == 3
    assert len(rows) == 8
    assert result["metrics"]["validation_loss"] > 0
    checkpoint = tmp_path / "checkpoint.pt"
    before = generate_from_checkpoint(checkpoint, "pump", 5)
    after = generate_from_checkpoint(checkpoint, "pump", 5)
    assert before == after
    _, _, state = load_checkpoint(checkpoint)
    assert state["optimizer"]["state"]
    assert state["torch_rng"].numel() > 0


@pytest.mark.parametrize("family", ["gru", "transformer"])
def test_resume_matches_uninterrupted_parameter_updates(tmp_path, family):
    execute_learning(specification(family, 6), tmp_path / "whole", lambda row: None, lambda: False)
    execute_learning(specification(family, 3), tmp_path / "first", lambda row: None, lambda: False)
    resumed = execute_learning(specification(family, 6, resume_from=str(tmp_path / "first/checkpoint.pt")),
        tmp_path / "resumed", lambda row: None, lambda: False)
    _, _, expected = load_checkpoint(tmp_path / "whole/checkpoint.pt")
    _, _, actual = load_checkpoint(tmp_path / "resumed/checkpoint.pt")
    assert all(torch.equal(expected["model"][key], value) for key, value in actual["model"].items())
    assert [row["step"] for row in resumed["records"]] == [4, 5, 6]


def test_transformer_cannot_read_future_tokens():
    torch.manual_seed(4)
    model = build_model({"family": "transformer", "hidden_size": 8, "heads": 2, "context": 8, "layers": 1}, 257)
    model.eval()
    with torch.no_grad():
        left = model(torch.tensor([[10, 20, 30, 40]]))
        right = model(torch.tensor([[10, 20, 99, 99]]))
    assert torch.equal(left[:, :2], right[:, :2])
    assert not torch.equal(left[:, 2:], right[:, 2:])


@pytest.mark.parametrize("kind,options", [("byte", {}), ("bpe", {}), ("engineering", {"vocabulary": ["pressure", "kPa"]})])
def test_tokenizer_preserves_unseen_utf8(kind, options):
    tokenizer = ByteTokenizer.fit(["pressure kPa " * 10], kind, **options)
    text = "pressure 120 kPa ∆未知"
    assert tokenizer.decode(tokenizer.encode(text)) == text
    restored = ByteTokenizer(**tokenizer.state())
    assert restored.encode(text) == tokenizer.encode(text)
    if kind != "byte":
        assert len(tokenizer.encode("pressure kPa")) < len("pressure kPa".encode())


def test_training_has_actual_learning_signal(tmp_path):
    spec = specification(steps=60, texts=["aaaaa " * 8] * 4, lr=0.03)
    result = execute_learning(spec, tmp_path, lambda row: None, lambda: False)
    assert result["metrics"]["validation_loss"] < result["metrics"]["initial_validation_loss"] * 0.5


def test_resume_rejects_changed_corpus(tmp_path):
    execute_learning(specification(steps=2), tmp_path / "first", lambda row: None, lambda: False)
    with pytest.raises(ValueError, match="does not match"):
        execute_learning(specification(steps=4, texts=["different", "corpus"], resume_from=str(tmp_path / "first/checkpoint.pt")),
            tmp_path / "bad", lambda row: None, lambda: False)


def test_cancellation_preserves_resumable_state(tmp_path):
    rows = []
    result = execute_learning(specification(steps=8), tmp_path, rows.append, lambda: len(rows) == 2)
    assert result["qualification"]["status"] == "cancelled"
    assert load_checkpoint(tmp_path / "checkpoint.pt")[2]["step"] == 2


def test_distillation_updates_student_from_local_teacher(tmp_path):
    execute_learning(specification(steps=2), tmp_path / "teacher", lambda row: None, lambda: False)
    spec = specification(steps=3, teacher_checkpoint=str(tmp_path / "teacher/checkpoint.pt"))
    spec["definition"]["learning"]["objective"] = "distillation"
    spec["definition"]["seed"] = 99
    result = execute_learning(spec, tmp_path / "student", lambda row: None, lambda: False)
    assert result["metrics"]["parameters_changed"]
    state = load_checkpoint(tmp_path / "student/checkpoint.pt")[2]
    assert state["teacher_sha256"]
    assert state["objective"] == "distillation"
    assert result["records"][0]["distillation_kl"] > 0
    row = result["records"][0]
    assert row["loss"] == pytest.approx(0.5 * row["cross_entropy"] + 0.5 * row["distillation_kl"])


def test_unsupported_objective_fails_explicitly(tmp_path):
    spec = specification()
    spec["definition"]["learning"]["objective"] = "reinforcement_learning"
    with pytest.raises(ValueError, match="unsupported learning objective"):
        execute_learning(spec, tmp_path, lambda row: None, lambda: False)


def test_retrieval_ranks_evidence_and_binds_exact_corpus(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text('\n'.join(json.dumps(row) for row in [
        {"text": "pump pressure trip alarm", "unit": "A", "episode": "trip-7"},
        {"text": "cooling temperature normal", "unit": "B", "episode": "steady-4"}]))
    index = build_index([source], tmp_path / "index", "episode")
    trace = query_index(index["path"], "pressure alarm", filters={"unit": "A"})
    assert len(trace["hits"]) == 1
    assert trace["hits"][0]["content"]["episode"] == "trip-7"
    assert trace["hits"][0]["source_sha256"]
    assert query_index(index["path"], "pressure alarm", filters={"unit": "B"})["hits"] == []
    source.write_text('{"text": "modified source"}')
    assert query_index(index["path"], "pressure alarm")["hits"][0]["content"]["unit"] == "A"
    newer = build_index([source], tmp_path / "index", "episode")
    assert newer["corpus_sha256"] != index["corpus_sha256"]
    assert trace["corpus_sha256"] == index["corpus_sha256"]


def test_retrieval_rejects_tampering_and_traces_no_hits(tmp_path):
    index = build_index([], tmp_path, records=[{"text": "pressure"}])
    assert query_index(index["path"], "unrelated")["hits"] == []
    Path(index["path"]).write_text('{}')
    with pytest.raises(ValueError, match="digest"):
        query_index(index["path"], "pressure")


def test_execute_retrieval_returns_saved_trace(tmp_path):
    spec = {"definition": {"parameters": {"records": [{"text": "pressure trip", "unit": "A"}],
        "queries": ["pressure", "missing"], "mode": "structured"}}}
    result = execute_retrieval(spec, tmp_path, lambda row: None, lambda: False)
    assert result["metrics"]["queries"] == 2
    assert len(result["artifacts"]) == 2
    assert result["records"][1]["hits"] == []


def test_distillation_resume_binds_teacher_and_loss_configuration(tmp_path):
    execute_learning(specification(steps=2), tmp_path / "teacher", lambda row: None, lambda: False)
    def student(steps, **parameters):
        spec = specification(steps=steps, teacher_checkpoint=str(tmp_path / "teacher/checkpoint.pt"), **parameters)
        spec["definition"]["learning"]["objective"] = "distillation"
        spec["definition"]["seed"] = 99
        return spec
    execute_learning(student(4), tmp_path / "whole", lambda row: None, lambda: False)
    execute_learning(student(2), tmp_path / "first", lambda row: None, lambda: False)
    checkpoint = str(tmp_path / "first/checkpoint.pt")
    execute_learning(student(4, resume_from=checkpoint), tmp_path / "resumed", lambda row: None, lambda: False)
    expected = load_checkpoint(tmp_path / "whole/checkpoint.pt")[2]["model"]
    actual = load_checkpoint(tmp_path / "resumed/checkpoint.pt")[2]["model"]
    assert all(torch.equal(expected[key], value) for key, value in actual.items())
    with pytest.raises(ValueError, match="does not match"):
        execute_learning(student(4, resume_from=checkpoint, temperature=3), tmp_path / "bad", lambda row: None, lambda: False)


def test_bpe_vocabulary_never_fits_validation_only_text(tmp_path):
    spec = specification(steps=1, texts=["UNSEEN-ZZZZZZZZZZ", "train-AAAAAAAA", "train-AAAAAAAA", "train-AAAAAAAA"])
    spec["definition"]["learning"]["representation"] = "bpe"
    execute_learning(spec, tmp_path, lambda row: None, lambda: False)
    _, tokenizer, state = load_checkpoint(tmp_path / "checkpoint.pt")
    assert state["split"]["validation"] == [0]  # Independently checked seeded shuffle.
    assert all(b"Z" not in piece for piece in tokenizer.pieces[256:])
    assert len(tokenizer.merges) > 0
    assert set(state["split"]["train"]).isdisjoint(state["split"]["validation"])


@pytest.mark.parametrize("executor", [execute_learning, execute_retrieval])
def test_learning_and_retrieval_refuse_altered_revision_inputs(tmp_path, executor):
    source = tmp_path / "source.jsonl"
    source.write_text('{"text":"pressure"}\n{"text":"temperature"}\n')
    spec = specification(steps=1)
    spec["definition"]["inputs"] = [{"artifact_id": "source-1", "sha256": "0" * 64}]
    spec["input_paths"] = {"source-1": str(source)}
    with pytest.raises(ValueError, match="checksum mismatch"):
        executor(spec, tmp_path / "out", lambda row: None, lambda: False)


def test_scratch_training_preserves_explicit_groups_and_excludes_final_test(tmp_path):
    experiment = specification(steps=1, texts=[], documents=[
        {"text": "train AAAA" * 4, "source_record_id": "s:train:0", "partition": "train", "episode_id": "A"},
        {"text": "train AAAA" * 4, "source_record_id": "s:train:1", "partition": "train", "episode_id": "A"},
        {"text": "validation BBBB" * 4, "source_record_id": "s:val:0", "partition": "validation", "episode_id": "B"},
        {"text": "TEST-ZZZZZZ" * 4, "source_record_id": "s:test:0", "partition": "final_test", "episode_id": "C"}])
    experiment["definition"]["learning"]["representation"] = "bpe"
    result = execute_learning(experiment, tmp_path, lambda row: None, lambda: False)
    _, tokenizer, state = load_checkpoint(tmp_path / "checkpoint.pt")
    assert state["split"] == {"train": [0, 1], "validation": [2]}
    assert state["partition_manifest"]["excluded_test"] == [3]
    assert result["metrics"]["excluded_test_documents"] == 1
    assert all(b"Z" not in piece and b"B" not in piece for piece in tokenizer.pieces[256:])
    experiment["definition"]["parameters"]["documents"][2]["episode_id"] = "A"
    with pytest.raises(ValueError, match="overlaps"):
        execute_learning(experiment, tmp_path / "leak", lambda row: None, lambda: False)


@pytest.mark.parametrize("row", [{"text": "pressure", "partition": "test"},
    {"text": "pressure", "visibility": "truth"}, {"text": "pressure", "true_value": 99},
    {"text": "pressure", "visibility": "evaluation"}])
def test_retrieval_rejects_restricted_content_rows(tmp_path, row):
    with pytest.raises(ValueError, match="research override"):
        build_index([], tmp_path, records=[row])


def test_retrieval_research_override_is_recorded_and_unqualified(tmp_path):
    experiment = {"definition": {"parameters": {"records": [{"text": "pressure", "partition": "test"}], "query": "pressure"},
        "research_overrides": [{"kind": "retrieval.include_restricted_evidence", "reason": "explicit leakage sensitivity experiment"}]}}
    result = execute_retrieval(experiment, tmp_path, lambda row: None, lambda: False)
    assert result["qualification"]["research_only"] is True
    assert result["qualification"]["qualified"] is False
    assert result["records"][0]["trace"]["research_override"]["reason"]


def test_nested_scratch_architecture_is_actual_and_conflicts_reject(tmp_path):
    experiment = specification("transformer", steps=1)
    for field in ("hidden_size", "layers", "heads", "context"):
        experiment["definition"]["parameters"].pop(field, None)
    experiment["definition"]["learning"]["architecture"] = {"hidden_size": 12, "layers": 2, "heads": 3, "context": 10}
    execute_learning(experiment, tmp_path, lambda row: None, lambda: False)
    model, _, checkpoint = load_checkpoint(tmp_path / "checkpoint.pt")
    assert model.embedding.embedding_dim == 12
    assert model.position.num_embeddings == 10
    assert len(model.core.layers) == 2 and model.core.layers[0].self_attn.num_heads == 3
    assert checkpoint["architecture"]["hidden_size"] == 12
    experiment["definition"]["parameters"]["hidden_size"] = 8
    with pytest.raises(ValueError, match="conflicting"):
        execute_learning(experiment, tmp_path / "conflict", lambda row: None, lambda: False)


@pytest.mark.parametrize("field,value", [("update_strategy", "lora"), ("device", "cuda"), ("dtype", "bf16"), ("initialization", "pretrained")])
def test_scratch_rejects_unimplemented_axes(tmp_path, field, value):
    experiment = specification(steps=1)
    experiment["definition"]["learning"][field] = value
    with pytest.raises(ValueError, match="unsupported|unavailable"):
        execute_learning(experiment, tmp_path, lambda row: None, lambda: False)


def test_model_and_evaluator_validation_bindings_are_separate(tmp_path):
    from tinystudio.lab.learning import resolved_inputs
    train = tmp_path / "train.jsonl"
    validation = tmp_path / "validation.jsonl"
    train.write_text('\n'.join(json.dumps({"text": "train-AAAA" * 4, "episode_id": "train", "source_record_id": f"train:{index}"}) for index in range(2)))
    validation.write_text(json.dumps({"text": "validation-ZZZZZZ" * 4, "episode_id": "validation", "source_record_id": "val:0"}))
    experiment = specification(steps=1, texts=[])
    experiment["definition"]["learning"]["representation"] = "bpe"
    experiment["definition"]["inputs"] = [
        {"artifact_id": "train", "role": "source", "visibility": "model", "sha256": hashlib.sha256(train.read_bytes()).hexdigest()},
        {"artifact_id": "val", "role": "validation", "visibility": "evaluation", "sha256": hashlib.sha256(validation.read_bytes()).hexdigest()},
        {"artifact_id": "test", "role": "final_test", "visibility": "evaluation", "sha256": "0" * 64},
        {"artifact_id": "truth", "role": "source", "visibility": "truth", "sha256": "0" * 64}]
    experiment["input_paths"] = {"train": str(train)}
    experiment["evaluation_input_paths"] = {"val": str(validation), "test": "/does/not/exist", "truth": "/does/not/exist"}
    assert [binding["path"] for binding in resolved_inputs(experiment)] == [str(train)]
    result = execute_learning(experiment, tmp_path / "run", lambda row: None, lambda: False)
    _, tokenizer, saved = load_checkpoint(tmp_path / "run/checkpoint.pt")
    assert saved["split"] == {"train": [0, 1], "validation": [2]}
    assert result["metrics"]["training_documents"] == 2 and result["metrics"]["validation_documents"] == 1
    assert all(b"Z" not in piece for piece in tokenizer.pieces[256:])
    experiment.pop("evaluation_input_paths")
    experiment["input_paths"]["val"] = str(validation)  # Raw model map cannot confer evaluator access.
    with pytest.raises(ValueError, match="evaluator-only"):
        execute_learning(experiment, tmp_path / "bad", lambda row: None, lambda: False)
