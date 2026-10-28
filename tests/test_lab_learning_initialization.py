"""Restart/resume contracts using independent local CPU corpora and immutable bindings."""
from __future__ import annotations

import copy
import hashlib

import pytest
import torch

from tinystudio.lab import learning
from test_lab_learning import specification


def test_inline_message_documents_are_not_silently_dropped():
    spec = specification(texts=[], documents=[
        {"source_record_id": "train-message", "partition": "train",
         "messages": [{"role": "user", "content": "voltage"}, {"role": "assistant", "content": "-1.25 V"}]},
        {"source_record_id": "validation-message", "partition": "validation",
         "messages": [{"role": "user", "content": "current"}, {"role": "assistant", "content": "0.5 A"}]},
    ])
    documents = learning.read_documents(spec)
    assert [row["text"] for row in documents] == ["voltage\n-1.25 V", "current\n0.5 A"]
    assert [row["source_record_id"] for row in documents] == ["train-message", "validation-message"]
    assert [row["partition"] for row in documents] == ["train", "validation"]


@pytest.mark.parametrize("messages", [False, True])
@pytest.mark.parametrize("partition", ["train", None])
def test_inline_evaluation_visibility_cannot_be_reclassified_for_training(messages, partition):
    protected = {"visibility": "evaluation", "source_record_id": "held-out"}
    protected.update({"messages": [{"role": "assistant", "content": "-1.25 V"}]} if messages else {"text": "-1.25 V"})
    if partition is not None:
        protected["partition"] = partition
    spec = specification(texts=[], documents=[protected, {"text": "validation", "partition": "validation"}])
    with pytest.raises(ValueError, match="evaluation-visible.*held-out"):
        learning.read_documents(spec)


@pytest.mark.parametrize("messages", [False, True])
def test_inline_evaluation_visibility_preserves_valid_held_out_documents(messages):
    protected = {"visibility": "evaluation", "partition": "validation", "source_record_id": "held-out"}
    protected.update({"messages": [{"role": "assistant", "content": "-1.25 V"}]} if messages else {"text": "-1.25 V"})
    spec = specification(texts=[], documents=[{"text": "train", "partition": "train"}, protected])
    rows = learning.read_documents(spec)
    assert rows[1]["text"] == "-1.25 V"
    assert rows[1]["visibility"] == "evaluation" and rows[1]["partition"] == "validation"


def bind(spec, path, role, mode):
    spec["definition"]["learning"]["initialization"] = mode
    spec["definition"].setdefault("inputs", []).append({"artifact_id": role, "role": role,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    spec["input_paths"][role] = str(path)
    return spec


def run(spec, output, cancelled=lambda: False):
    return learning.execute_learning(spec, output, lambda row: None, cancelled)


def checkpoint(output):
    return learning.load_checkpoint(output / "checkpoint.pt")[2]


@pytest.mark.parametrize("family", ["gru", "transformer"])
def test_weights_restart_inherits_model_but_resets_optimizer_rng_and_corpus(tmp_path, family):
    parent_dir = tmp_path / "parent"
    run(specification(family, steps=3), parent_dir)
    parent_path = parent_dir / "checkpoint.pt"
    parent_bytes = parent_path.read_bytes()
    parent = checkpoint(parent_dir)
    spec = bind(specification(family, steps=2, texts=["new corpus B zzz " * 3] * 4), parent_path,
                "initial_weights", "weights")
    spec["definition"]["seed"] = 73
    # A zero-update cancellation exposes the actual optimizer/model starting state.
    initial_dir = tmp_path / "initial"
    result = run(spec, initial_dir, cancelled=lambda: True)
    initial = checkpoint(initial_dir)
    assert initial["architecture"] == parent["architecture"]
    assert initial["tokenizer"] == parent["tokenizer"]
    assert all(torch.equal(initial["model"][key], value) for key, value in parent["model"].items())
    assert initial["optimizer"]["state"] == {} and initial["step"] == 0
    assert initial["corpus_sha256"] != parent["corpus_sha256"]
    assert torch.equal(initial["sampler_rng"], torch.Generator().manual_seed(74).get_state())
    assert result["qualification"]["initialization"] == "weights"
    assert result["qualification"]["from_scratch"] is False
    assert result["qualification"]["optimizer_state_restored"] is False
    assert result["qualification"]["parent_checkpoint_sha256"] == hashlib.sha256(parent_bytes).hexdigest()
    trained_dir = tmp_path / "trained"
    trained_result = run(spec, trained_dir)
    trained = checkpoint(trained_dir)
    assert trained["step"] == 2
    assert {int(row["step"]) for row in trained["optimizer"]["state"].values()} == {2}
    assert any(not torch.equal(trained["model"][key], value) for key, value in parent["model"].items())
    assert trained_result["metrics"]["reload_logits_equal"] is True
    assert learning.generate_from_checkpoint(trained_dir / "checkpoint.pt", "unseen B", 2)["prompt"] == "unseen B"
    assert parent_path.read_bytes() == parent_bytes


@pytest.mark.parametrize("family", ["gru", "transformer"])
def test_bpe_new_corpus_restart_then_exact_resume_matches_uninterrupted(tmp_path, monkeypatch, family):
    source = specification(family, steps=2, texts=["AAAAAAAAA valve AAAAAAAA " * 4] * 4)
    source["definition"]["learning"]["representation"] = {"kind": "bpe", "vocab_size": 280}
    run(source, tmp_path / "A")
    parent = tmp_path / "A/checkpoint.pt"
    old_state = checkpoint(tmp_path / "A")
    def corpus_b(steps):
        spec = specification(family, steps=steps, texts=["ZZZZZZZZ BBBBB new corpus " * 3] * 4)
        spec["definition"]["learning"]["representation"] = {"kind": "bpe", "vocab_size": 280}
        return spec
    # Frozen tokenizer must never be fitted again, including during exact resume.
    def forbidden_fit(*args, **kwargs):
        raise AssertionError("inherited tokenizer must remain frozen")
    monkeypatch.setattr(learning.ByteTokenizer, "fit", forbidden_fit)
    run(bind(corpus_b(5), parent, "initial_weights", "weights"), tmp_path / "whole")
    run(bind(corpus_b(2), parent, "initial_weights", "weights"), tmp_path / "first")
    result = run(bind(corpus_b(5), tmp_path / "first/checkpoint.pt", "resume", "resume"), tmp_path / "resumed")
    expected, actual = checkpoint(tmp_path / "whole"), checkpoint(tmp_path / "resumed")
    assert actual["tokenizer"] == expected["tokenizer"] == old_state["tokenizer"]
    assert actual["corpus_sha256"] == expected["corpus_sha256"] != old_state["corpus_sha256"]
    assert all(torch.equal(expected["model"][key], value) for key, value in actual["model"].items())
    assert torch.equal(expected["sampler_rng"], actual["sampler_rng"])
    assert torch.equal(expected["torch_rng"], actual["torch_rng"])
    assert [row["step"] for row in result["records"]] == [3, 4, 5]
    assert actual["lineage"] == expected["lineage"] == {"initialization": "weights",
        "parent_checkpoint_sha256": hashlib.sha256(parent.read_bytes()).hexdigest()}
    assert result["qualification"]["from_scratch"] is False
    assert result["qualification"]["optimizer_state_restored"] is True


@pytest.mark.parametrize("mode", ["scratch", "random", "from_scratch", None])
def test_legacy_ambiguous_checkpoint_never_silently_loads(tmp_path, monkeypatch, mode):
    path = tmp_path / "parent.pt"
    path.write_bytes(b"not even a checkpoint")
    spec = bind(specification(), path, "resume", mode or "scratch")
    if mode is None:
        spec["definition"]["learning"].pop("initialization")
    monkeypatch.setattr(learning, "read_documents", lambda *args, **kwargs: pytest.fail("must reject before corpus"))
    with pytest.raises(ValueError, match="initialization=resume"):
        run(spec, tmp_path / "bad")


@pytest.mark.parametrize("mode,roles", [("weights", []), ("resume", []), ("weights", ["resume"]),
    ("resume", ["initial_weights"]), ("weights", ["initial_weights", "resume"]),
    ("weights", ["initial_weights", "initial_weights"])])
def test_missing_contradictory_duplicate_binding_rejected_before_corpus(tmp_path, monkeypatch, mode, roles):
    spec = specification()
    spec["definition"]["learning"]["initialization"] = mode
    path = tmp_path / "not-a-checkpoint.pt"
    path.write_bytes(b"bad")
    for role in roles:
        bind(spec, path, role, mode)
    monkeypatch.setattr(learning, "read_documents", lambda *args, **kwargs: pytest.fail("must reject before corpus"))
    with pytest.raises(ValueError, match="matching|exactly one"):
        run(spec, tmp_path / "bad")


@pytest.mark.parametrize("problem", ["hash", "unresolved", "authored_path"])
def test_binding_failures_reject_before_training(tmp_path, monkeypatch, problem):
    path = tmp_path / "parent.pt"
    path.write_bytes(b"untrusted")
    spec = bind(specification(), path, "initial_weights", "weights")
    if problem == "hash":
        spec["definition"]["inputs"][0]["sha256"] = "0" * 64
    elif problem == "unresolved":
        spec["input_paths"] = {}
    else:
        spec["definition"]["parameters"]["initial_weights"] = str(path)
    monkeypatch.setattr(learning, "read_documents", lambda *args, **kwargs: pytest.fail("must reject before corpus"))
    with pytest.raises(ValueError, match="checksum|resolve|immutable input"):
        run(spec, tmp_path / "bad")


@pytest.mark.parametrize("change,match", [("family", "family"), ("kind", "kind"), ("merge", "earlier"),
    ("vocabulary", "vocabulary"), ("mixed", "kind"), ("dimensions", "integers")])
def test_malformed_checkpoint_metadata_rejected_even_when_tensors_still_fit(tmp_path, change, match):
    run(specification(steps=1), tmp_path / "parent")
    state = checkpoint(tmp_path / "parent")
    if change == "family":
        state["architecture"]["family"] = "typo-gru"
    elif change == "kind":
        state["tokenizer"]["kind"] = "typo-byte"
    elif change == "merge":
        state["tokenizer"] = {"kind": "bpe", "merges": [[256, 0]], "vocabulary": []}
    elif change == "vocabulary":
        state["tokenizer"] = {"kind": "engineering", "merges": [], "vocabulary": ["x", "x"]}
    elif change == "mixed":
        state["tokenizer"]["vocabulary"] = ["unexpected"]
    else:
        state["architecture"]["hidden_size"] = 8.0
    path = tmp_path / "malformed.pt"
    torch.save(state, path)
    with pytest.raises(ValueError, match=match):
        learning.load_checkpoint(path)


def test_tokenizer_defensively_owns_and_exports_state():
    merges = [[65, 65], [256, 65]]
    tokenizer = learning.ByteTokenizer("bpe", merges=merges)
    expected = tokenizer.encode("AAA")
    merges[0][0] = 66
    exported = tokenizer.state()
    exported["merges"][1][0] = 66
    tokenizer.merges[0][0] = 66
    assert tokenizer.encode("AAA") == expected == [257]
    assert tokenizer.state()["merges"] == [[65, 65], [256, 65]]
    words = ["pressure", "kPa"]
    engineering = learning.ByteTokenizer("engineering", vocabulary=words)
    words[0] = "changed"
    engineering.state()["vocabulary"].append("changed")
    assert engineering.vocabulary == ["pressure", "kPa"]


@pytest.mark.parametrize("conflict", ["family", "context", "heads", "representation", "vocab_size", "vocabulary"])
def test_explicit_axes_cannot_be_ignored_on_inheritance(tmp_path, monkeypatch, conflict):
    source = specification("transformer", steps=1)
    source["definition"]["learning"]["representation"] = {"kind": "bpe", "vocab_size": 280}
    run(source, tmp_path / "parent")
    spec = bind(copy.deepcopy(source), tmp_path / "parent/checkpoint.pt", "initial_weights", "weights")
    learn, params = spec["definition"]["learning"], spec["definition"]["parameters"]
    if conflict == "family":
        learn["family"] = "gru"
    elif conflict == "context":
        params["context"] = 10
    elif conflict == "heads":
        params["heads"] = 4  # Weight tensor shapes still fit; attention semantics differ.
    elif conflict == "representation":
        learn["representation"] = "byte"
    elif conflict == "vocab_size":
        learn["representation"]["vocab_size"] = 281
    else:
        learn["representation"]["vocabulary"] = ["unexpected"]
    monkeypatch.setattr(learning, "read_documents", lambda *args, **kwargs: pytest.fail("must reject before corpus"))
    with pytest.raises(ValueError, match="match"):
        run(spec, tmp_path / "bad")


def test_omitted_axes_inherit_and_additive_v1_checkpoint_still_loads(tmp_path):
    run(specification("gru", steps=1), tmp_path / "parent")
    legacy = checkpoint(tmp_path / "parent")
    for key in ("lineage", "initialization", "parent_checkpoint_sha256", "optimizer_state_restored", "tokenizer_configuration"):
        legacy.pop(key)
    path = tmp_path / "legacy.pt"
    torch.save(legacy, path)
    spec = {"definition": {"learning": {"initialization": "weights"},
        "parameters": {"texts": ["new A", "new B", "new C"], "steps": 1}}, "input_paths": {}}
    run(bind(spec, path, "initial_weights", "weights"), tmp_path / "out")
    assert checkpoint(tmp_path / "out")["architecture"] == legacy["architecture"]
    assert checkpoint(tmp_path / "out")["tokenizer"] == legacy["tokenizer"]


def test_unused_teacher_rejected_and_initial_weights_not_read_as_corpus(tmp_path):
    run(specification(steps=1), tmp_path / "parent")
    path = tmp_path / "parent/checkpoint.pt"
    spec = bind(specification(), path, "initial_weights", "weights")
    assert [row["text"] for row in learning.read_documents(spec)] == spec["definition"]["parameters"]["texts"]
    bind(spec, path, "teacher", "weights")
    with pytest.raises(ValueError, match="teacher input is unused"):
        run(spec, tmp_path / "bad")


@pytest.mark.parametrize("problem", ["missing", "duplicate", "evaluation"])
def test_distillation_teacher_declarations_reject_before_corpus(tmp_path, monkeypatch, problem):
    spec = specification()
    spec["definition"]["learning"]["objective"] = "distillation"
    if problem != "missing":
        teacher = {"artifact_id": "teacher", "role": "teacher", "sha256": "0" * 64}
        spec["definition"]["inputs"] = [teacher] * (2 if problem == "duplicate" else 1)
        if problem == "evaluation":
            teacher["visibility"] = "evaluation"
    monkeypatch.setattr(learning, "read_documents", lambda *args, **kwargs: pytest.fail("must reject before corpus"))
    with pytest.raises(ValueError, match="teacher|model-visible"):
        run(spec, tmp_path / "bad")


@pytest.mark.parametrize("representation", [{"kind": []}, {"kind": {"unexpected": "byte"}}, []])
def test_malformed_inherited_representation_rejects_with_actionable_error(tmp_path, representation):
    run(specification(steps=1), tmp_path / "parent")
    spec = bind(specification(), tmp_path / "parent/checkpoint.pt", "initial_weights", "weights")
    spec["definition"]["learning"]["representation"] = representation
    with pytest.raises(ValueError, match="representation"):
        run(spec, tmp_path / "bad")


def test_engineering_restart_accepts_same_vocabulary_and_rejects_conflict(tmp_path):
    spec = specification(steps=1)
    spec["definition"]["learning"]["representation"] = {"kind": "engineering", "vocabulary": ["valve", "pressure"]}
    run(spec, tmp_path / "parent")
    restart = bind(copy.deepcopy(spec), tmp_path / "parent/checkpoint.pt", "initial_weights", "weights")
    restart["definition"]["parameters"]["texts"] = ["new corpus A", "new corpus B", "new corpus C"]
    run(restart, tmp_path / "same")
    assert checkpoint(tmp_path / "same")["tokenizer"] == checkpoint(tmp_path / "parent")["tokenizer"]
    restart["definition"]["learning"]["representation"]["vocabulary"] = ["different"]
    with pytest.raises(ValueError, match="vocabulary"):
        run(restart, tmp_path / "bad")


def test_random_without_checkpoint_is_a_fresh_training_mode(tmp_path):
    spec = specification(steps=1)
    spec["definition"]["learning"]["initialization"] = "random"
    result = run(spec, tmp_path)
    assert result["qualification"]["initialization"] == "random"
    assert result["qualification"]["from_scratch"] is True
    assert result["qualification"]["parent_checkpoint_sha256"] is None
    assert result["qualification"]["optimizer_state_restored"] is False


def test_partial_explicit_architecture_uses_inherited_dimensions(tmp_path):
    source = specification("transformer", steps=1)
    source["definition"]["parameters"].update(hidden_size=12, heads=3)
    run(source, tmp_path / "parent")
    spec = {"definition": {"learning": {"initialization": "weights", "architecture": {"heads": 3}},
        "parameters": {"texts": ["new A", "new B", "new C"], "steps": 1}}, "input_paths": {}}
    run(bind(spec, tmp_path / "parent/checkpoint.pt", "initial_weights", "weights"), tmp_path / "out")
    assert checkpoint(tmp_path / "out")["architecture"] == checkpoint(tmp_path / "parent")["architecture"]


def test_restart_cannot_overwrite_immutable_parent_checkpoint(tmp_path):
    run(specification(steps=1), tmp_path)
    path = tmp_path / "checkpoint.pt"
    original = path.read_bytes()
    spec = bind(specification(steps=1), path, "initial_weights", "weights")
    with pytest.raises(ValueError, match="overwrite"):
        run(spec, tmp_path)
    assert path.read_bytes() == original


def test_unbound_truth_without_role_is_never_resolved_as_a_checkpoint(tmp_path):
    spec = specification(steps=1)
    spec["definition"]["inputs"] = [{"artifact_id": "truth", "visibility": "truth", "sha256": "0" * 64}]
    run(spec, tmp_path)
    assert checkpoint(tmp_path)["step"] == 1


def test_restart_releases_trained_model_before_checkpoint_reload(tmp_path, monkeypatch):
    import weakref
    run(specification(steps=1), tmp_path / "parent")
    original_loader = learning.load_checkpoint
    parent_path = tmp_path / "parent/checkpoint.pt"
    out_path = tmp_path / "out/checkpoint.pt"
    trained_model = None
    def tracked_loader(path, **kwargs):
        nonlocal trained_model
        if path == out_path:
            assert trained_model is not None and trained_model() is None
        result = original_loader(path, **kwargs)
        if str(path) == str(parent_path):
            trained_model = weakref.ref(result[0])
        return result
    monkeypatch.setattr(learning, "load_checkpoint", tracked_loader)
    result = run(bind(specification(steps=1), parent_path, "initial_weights", "weights"), tmp_path / "out")
    assert result["metrics"]["reload_logits_equal"]
