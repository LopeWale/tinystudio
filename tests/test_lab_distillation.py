"""Distillation objectives: scalar oracle, immutable teacher, and held-out custody.

The scalar oracle uses Python math independently of torch loss operators. The
T-squared KL and frozen eval/no-grad teacher follow the PyTorch distillation
tutorial; flattening token rows makes the normalization explicit:
https://docs.pytorch.org/tutorials/beginner/knowledge_distillation_tutorial.html
https://docs.pytorch.org/docs/2.14/generated/torch.nn.functional.kl_div.html
"""
from __future__ import annotations

import hashlib
import json
import math
import random

import pytest
import torch

from tinystudio.lab.distillation import FrozenTeacher, audit_teacher_exposure, distillation_loss
from tinystudio.lab.learning import ByteTokenizer, build_model, execute_learning, load_checkpoint


def _oracle(student, teacher, targets, temperature, alpha):
    def probabilities(row, scale):
        values = [value / scale for value in row]
        pivot = max(values)
        values = [math.exp(value - pivot) for value in values]
        return [value / sum(values) for value in values]

    ce = sum(-math.log(probabilities(row, 1)[target]) for row, target in zip(student, targets)) / len(targets)
    kl = 0.0
    for left, right in zip(student, teacher):
        p, q = probabilities(right, temperature), probabilities(left, temperature)
        kl += sum(a * math.log(a / b) for a, b in zip(p, q))
    kl *= temperature ** 2 / len(targets)
    return {"cross_entropy": ce, "distillation_kl": kl, "loss": (1 - alpha) * ce + alpha * kl}


@pytest.mark.parametrize("temperature", [0.7, 1.0, 3.0])
@pytest.mark.parametrize("alpha", [0.0, 0.35, 1.0])
def test_loss_matches_independent_scalar_probability_oracle(temperature, alpha):
    student = [[0.2, -0.7, 1.3], [2.1, 0.4, -1.2]]
    teacher = [[1.4, 0.2, -0.5], [-0.3, 1.8, 0.7]]
    expected = _oracle(student, teacher, [2, 0], temperature, alpha)
    actual = distillation_loss(torch.tensor(student, dtype=torch.float64),
        torch.tensor(teacher, dtype=torch.float64), torch.tensor([2, 0]), temperature, alpha)
    for key, value in expected.items():
        assert actual[key].item() == pytest.approx(value, rel=1e-12, abs=1e-12)


def test_loss_is_mean_per_token_and_teacher_changes_only_student_gradient():
    left = torch.tensor([[0.2, -0.7, 1.3], [2.1, 0.4, -1.2]], requires_grad=True)
    right = torch.tensor([[1.4, 0.2, -0.5], [-0.3, 1.8, 0.7]], requires_grad=True)
    targets = torch.tensor([2, 0])
    original = distillation_loss(left, right, targets, 2, 0.75)
    repeated = distillation_loss(left.repeat(3, 1), right.repeat(3, 1), targets.repeat(3), 2, 0.75)
    for key in ("loss", "cross_entropy", "distillation_kl"):
        assert repeated[key].item() == pytest.approx(original[key].item())
    original["loss"].backward()
    before = left.grad.clone()
    assert right.grad is None
    changed_student = left.detach().clone().requires_grad_()
    changed_teacher = right.detach().roll(1, dims=-1).requires_grad_()
    distillation_loss(changed_student, changed_teacher, targets, 2, 0.75)["loss"].backward()
    assert not torch.allclose(before, changed_student.grad)
    assert changed_teacher.grad is None


@pytest.mark.parametrize("temperature,alpha", [(0, .5), (-1, .5), (math.inf, .5), (math.nan, .5),
    (2, -.01), (2, 1.01), (2, math.inf), (2, math.nan)])
def test_loss_rejects_invalid_configuration(temperature, alpha):
    with pytest.raises(ValueError):
        distillation_loss(torch.zeros(2, 3), torch.zeros(2, 3), torch.tensor([0, 1]), temperature, alpha)


@pytest.mark.parametrize("student,teacher,targets", [
    (torch.zeros(2, 3), torch.zeros(1, 3), torch.tensor([0, 1])),
    (torch.zeros(2, 3), torch.zeros(2, 4), torch.tensor([0, 1])),
    (torch.zeros(1, 2, 3), torch.zeros(1, 2, 3), torch.tensor([0, 1])),
    (torch.zeros(2, 3), torch.zeros(2, 3), torch.tensor([[0, 1]])),
    (torch.zeros(2, 3), torch.zeros(2, 3), torch.tensor([0])),
    (torch.zeros(0, 3), torch.zeros(0, 3), torch.tensor([], dtype=torch.long)),
    (torch.zeros(2, 3), torch.zeros(2, 3), torch.tensor([0., 1.])),
    (torch.zeros(2, 3), torch.zeros(2, 3), torch.tensor([0, 3])),
    (torch.zeros(2, 3), torch.zeros(2, 3), torch.tensor([-1, 0])),
    (torch.zeros(2, 3), torch.zeros(2, 3), torch.tensor([-100, 0])),
    (torch.full((2, 3), math.nan), torch.zeros(2, 3), torch.tensor([0, 1])),
    (torch.zeros(2, 3), torch.full((2, 3), math.inf), torch.tensor([0, 1])),
])
def test_loss_rejects_misaligned_nonfinite_and_invalid_token_rows(student, teacher, targets):
    with pytest.raises(ValueError):
        distillation_loss(student, teacher, targets, 2, .5)


def _documents(prefix):
    return [{"text": f"{prefix} {partition} pressure stable valve ready " * 2,
             "source_record_id": f"{prefix}:{partition}", "partition": partition}
            for partition in ("train", "validation", "final_test")]


def _spec(documents, *, family="gru", representation="byte", **parameters):
    return {"definition": {"seed": 17, "engine": "learning.language", "learning": {
        "family": family, "initialization": "scratch", "representation": representation, "objective": "causal_lm"},
        "parameters": {"documents": documents, "steps": 2, "hidden_size": 8, "layers": 1, "context": 12,
                       **({"heads": 2} if family == "transformer" else {}), "max_new_tokens": 0, **parameters}},
        "input_paths": {}}


def _binding(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "artifact_id": "teacher"}


def _student(path, **parameters):
    spec = _spec(_documents("student"), **parameters)
    spec["definition"]["learning"]["objective"] = "distillation"
    spec["definition"]["inputs"] = [{key: value for key, value in _binding(path).items() if key != "path"} | {"role": "teacher"}]
    spec["input_paths"] = {"teacher": str(path)}
    return spec


@pytest.fixture
def teacher_checkpoint(tmp_path):
    execute_learning(_spec(_documents("teacher")), tmp_path / "teacher", lambda row: None, lambda: False)
    return tmp_path / "teacher/checkpoint.pt"


def test_frozen_teacher_eval_no_grad_defensive_metadata_and_optimizer_custody(teacher_checkpoint, monkeypatch):
    import tinystudio.lab.learning as learning
    original = learning.load_checkpoint
    captured = {}

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        captured["model"] = result[0]
        return result

    monkeypatch.setattr(learning, "load_checkpoint", capture)
    teacher = FrozenTeacher.load(_binding(teacher_checkpoint))
    observed = []
    handle = captured["model"].register_forward_pre_hook(
        lambda model, inputs: observed.append((model.training, torch.is_grad_enabled())))
    try:
        tokenizer = teacher.tokenizer
        tokenizer_state = tokenizer.state()
        tokenizer_state["vocabulary"].append("foreign")
        assert teacher.tokenizer.state() != tokenizer_state
        architecture = teacher.architecture
        architecture["context"] = 999
        assert teacher.architecture["context"] == 12
        configuration = teacher.configuration
        configuration["vocab_size"] = 999
        assert teacher.configuration["vocab_size"] != 999
        student = build_model({"family": "gru", "hidden_size": 4, "layers": 1, "context": 8}, tokenizer.vocab_size)
        teacher.validate_student(tokenizer, 8)
        optimizer = torch.optim.AdamW(student.parameters())
        teacher.bind_optimizer(optimizer, student)
        before = {key: value.clone() for key, value in captured["model"].state_dict().items()}
        tokens = torch.tensor([[10, 20, 30]])
        output = teacher.logits(tokens)
        assert output.shape == (1, 3, tokenizer.vocab_size)
        assert not output.requires_grad
        distillation_loss(student(tokens).reshape(-1, tokenizer.vocab_size),
            output.reshape(-1, tokenizer.vocab_size), torch.tensor([20, 30, 40]), 2, .75)["loss"].backward()
        optimizer.step()
        assert observed == [(False, False)]
        assert all(parameter.grad is None and not parameter.requires_grad for parameter in captured["model"].parameters())
        assert all(torch.equal(before[key], value) for key, value in captured["model"].state_dict().items())
        receipt = teacher.finish()
        assert receipt["format"] == "tinystudio.distillation.teacher.v1"
        assert receipt["artifact_id"] == "teacher"
        assert receipt["sha256"] == _binding(teacher_checkpoint)["sha256"]
        assert receipt["state_before_sha256"] == receipt["state_after_sha256"]
        assert receipt["optimizer_excluded"] is True
        assert receipt["forward_calls"] == 1
        assert len(receipt["tokenizer_sha256"]) == 64
        receipt["architecture"]["context"] = 999
        assert teacher.finish()["architecture"]["context"] == 12
        with pytest.raises(ValueError, match="finished"):
            teacher.logits(tokens)
    finally:
        handle.remove()


def test_frozen_teacher_rejects_incomplete_or_foreign_optimizer(teacher_checkpoint):
    teacher = FrozenTeacher.load(_binding(teacher_checkpoint))
    student = build_model({"family": "gru", "hidden_size": 4, "layers": 1, "context": 8}, teacher.tokenizer.vocab_size)
    parameters = list(student.parameters())
    for owned in (parameters[:-1], [*parameters, torch.nn.Parameter(torch.zeros(1))]):
        with pytest.raises(ValueError):
            teacher.bind_optimizer(torch.optim.AdamW(owned), student)
    with pytest.raises(ValueError):
        teacher.validate_student(ByteTokenizer("engineering", vocabulary=["pressure"]), 8)
    with pytest.raises(ValueError):
        teacher.validate_student(teacher.tokenizer, 13)


def test_nonfinite_teacher_state_is_rejected_before_output_or_optimizer(teacher_checkpoint, tmp_path, monkeypatch):
    state = torch.load(teacher_checkpoint, weights_only=True)
    next(iter(state["model"].values())).fill_(math.nan)
    invalid = tmp_path / "invalid-teacher.pt"
    torch.save(state, invalid)
    def unexpected_optimizer(*args, **kwargs):
        raise AssertionError("optimizer constructed before teacher validation")
    monkeypatch.setattr(torch.optim, "AdamW", unexpected_optimizer)
    destination = tmp_path / "invalid-output"
    with pytest.raises(ValueError, match="teacher state.*finite"):
        execute_learning(_student(invalid), destination, lambda row: None, lambda: False)
    assert not destination.exists()


@pytest.mark.parametrize("configuration", [[320], [], {}, False, {"vocab_size": True}, {"vocab_size": 3}])
def test_invalid_teacher_tokenizer_metadata_fails_before_output(teacher_checkpoint, tmp_path, configuration):
    state = torch.load(teacher_checkpoint, weights_only=True)
    state["tokenizer_configuration"] = configuration
    invalid = tmp_path / "invalid-metadata.pt"
    torch.save(state, invalid)
    destination = tmp_path / "invalid-output"
    with pytest.raises(ValueError, match="teacher tokenizer configuration"):
        execute_learning(_student(invalid), destination, lambda row: None, lambda: False)
    assert not destination.exists()


@pytest.mark.parametrize("state_kind", ["parameter", "buffer"])
def test_frozen_teacher_detects_file_and_in_memory_state_tampering(teacher_checkpoint, monkeypatch, state_kind):
    import tinystudio.lab.learning as learning
    original = learning.load_checkpoint
    captured = {}

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        captured["model"] = result[0]
        result[0].register_buffer("audit_probe", torch.zeros(1))
        return result

    monkeypatch.setattr(learning, "load_checkpoint", capture)
    teacher = FrozenTeacher.load(_binding(teacher_checkpoint))
    student = build_model({"family": "gru", "hidden_size": 4, "layers": 1, "context": 8}, teacher.tokenizer.vocab_size)
    teacher.bind_optimizer(torch.optim.AdamW(student.parameters()), student)
    with torch.no_grad():
        if state_kind == "parameter":
            next(captured["model"].parameters()).add_(1)
        else:
            captured["model"].audit_probe.add_(1)
    with pytest.raises(ValueError, match="parameters or buffers changed"):
        teacher.finish()
    fresh = FrozenTeacher.load(_binding(teacher_checkpoint))
    fresh.bind_optimizer(torch.optim.AdamW(student.parameters()), student)
    teacher_checkpoint.write_bytes(teacher_checkpoint.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="artifact changed"):
        fresh.finish()


@pytest.mark.parametrize("representation,options", [("bpe", {"vocab_size": 280}),
    ("engineering", {"vocabulary": ["pressure", "valve"]})])
@pytest.mark.parametrize("teacher_family,student_family", [("transformer", "gru"), ("gru", "transformer")])
def test_smaller_cross_family_student_inherits_tokenizer_without_refitting(tmp_path, monkeypatch,
        representation, options, teacher_family, student_family):
    execute_learning(_spec(_documents("teacher"), family=teacher_family, representation=representation,
        hidden_size=16, **options), tmp_path / "teacher", lambda row: None, lambda: False)
    path = tmp_path / "teacher/checkpoint.pt"
    _, tokenizer, teacher_state = load_checkpoint(path)
    teacher_bytes = path.read_bytes()

    def no_refit(*args, **kwargs):
        pytest.fail("student distillation must reuse the exact teacher tokenizer on its new corpus")

    monkeypatch.setattr(ByteTokenizer, "fit", no_refit)
    spec = _student(path, family=student_family, representation=representation, **options)
    result = execute_learning(spec, tmp_path / "student", lambda row: None, lambda: False)
    _, actual_tokenizer, state = load_checkpoint(tmp_path / "student/checkpoint.pt")
    assert actual_tokenizer.state() == tokenizer.state()
    assert state["architecture"]["family"] == student_family
    assert sum(value.numel() for value in state["model"].values()) < sum(value.numel() for value in teacher_state["model"].values())
    assert result["metrics"]["parameters_changed"]
    assert state["optimizer"]["state"]
    assert path.read_bytes() == teacher_bytes
    assert state["teacher_receipt"]["forward_calls"] == 2
    assert state["teacher_receipt"]["optimizer_excluded"] is True
    assert state["teacher_receipt"]["state_before_sha256"] == state["teacher_receipt"]["state_after_sha256"]
    assert result["qualification"]["held_out_validation_scope"] == "current_student_optimizer_only"
    assert state["teacher_exposure"]["coverage"] == "unknown"


@pytest.mark.parametrize("invalid", ["checksum", "context", "representation", "vocabulary"])
def test_teacher_validation_precedes_optimizer_and_publication(teacher_checkpoint, tmp_path, monkeypatch, invalid):
    spec = _student(teacher_checkpoint)
    if invalid == "checksum":
        spec["definition"]["inputs"][0]["sha256"] = "0" * 64
    elif invalid == "context":
        spec["definition"]["parameters"]["context"] = 13
    elif invalid == "representation":
        spec["definition"]["learning"]["representation"] = "bpe"
    else:
        spec["definition"]["parameters"]["vocab_size"] = 999

    def forbidden_optimizer(*args, **kwargs):
        pytest.fail("invalid teacher compatibility must fail before optimizer construction")

    monkeypatch.setattr(torch.optim, "AdamW", forbidden_optimizer)
    destination = tmp_path / "invalid-student"
    rng, threads = torch.get_rng_state().clone(), torch.get_num_threads()
    with pytest.raises(ValueError):
        execute_learning(spec, destination, lambda row: None, lambda: False)
    assert not destination.exists()
    assert torch.equal(torch.get_rng_state(), rng)
    assert torch.get_num_threads() == threads


def test_checkpoint_retains_exact_training_text_fingerprints_only(teacher_checkpoint):
    state = load_checkpoint(teacher_checkpoint)[2]
    evidence = state["training_evidence"]
    assert evidence["format"] == "tinystudio.training-evidence.v1"
    train, validation, test = _documents("teacher")
    assert len(evidence["records"]) == 1
    retained = evidence["records"][0]
    assert retained["source_record_id"] == train["source_record_id"]
    assert retained["text_sha256"] == hashlib.sha256(train["text"].encode()).hexdigest()
    assert retained["record_sha256"] == hashlib.sha256(json.dumps(train, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert retained["text_sha256"] not in {hashlib.sha256(row["text"].encode()).hexdigest() for row in (validation, test)}


@pytest.mark.parametrize("heldout_index", [1, 2])
def test_teacher_training_text_in_student_holdout_blocks_even_with_changed_metadata(teacher_checkpoint, tmp_path, heldout_index):
    state = load_checkpoint(teacher_checkpoint)[2]
    documents = _documents("student")
    documents[heldout_index]["text"] = _documents("teacher")[0]["text"]
    from tinystudio.lab.compatibility import partition_snapshot
    partitions = partition_snapshot(documents, 17, .2, min_training_rows=1)
    audit = audit_teacher_exposure(state, documents, partitions)
    assert audit["matches"]
    assert audit["qualification"]["status"] == "failed"
    assert "teacher:heldout_training_overlap" in audit["qualification"]["blocking_checks"]
    spec = _student(teacher_checkpoint)
    spec["definition"]["parameters"]["documents"] = documents
    destination = tmp_path / "contaminated"
    with pytest.raises(ValueError, match="teacher|held.out|overlap"):
        execute_learning(spec, destination, lambda row: None, lambda: False)
    assert not destination.exists()
    spec["definition"]["research_overrides"] = [{"check_id": "teacher:heldout_training_overlap",
        "reason": "Intentional contamination control", "approved_by": "test researcher"}]
    result = execute_learning(spec, tmp_path / "research", lambda row: None, lambda: False)
    assert result["qualification"]["status"] == "research_only"
    audited = load_checkpoint(tmp_path / "research/checkpoint.pt")[2]["teacher_exposure"]
    assert audited["matches"]
    assert audited["qualification"]["status"] == "research_only"


def test_legacy_teacher_cannot_claim_clean_from_inline_ids_or_changed_metadata(teacher_checkpoint):
    state = load_checkpoint(teacher_checkpoint)[2]
    state.pop("training_evidence")
    from tinystudio.lab.compatibility import partition_snapshot
    documents = _documents("student")
    # The text matches teacher train, but the legacy complete-row hash cannot see
    # through changed source identity/partition. Coverage must stay unknown.
    documents[1]["text"] = _documents("teacher")[0]["text"]
    audit = audit_teacher_exposure(state, documents, partition_snapshot(documents, 17, .2, min_training_rows=1))
    assert audit["coverage"] == "unknown"
    assert not audit["matches"]
    assert audit["qualification"]["status"] != "qualified"


@pytest.mark.parametrize("state", [
    {"partition_manifest": []},
    {"partition_manifest": {"partitions": []}},
    {"partition_manifest": {"partitions": {"train": []}}},
    {"partition_manifest": {"partitions": {"train": {"records": ["invalid"]}}}},
    {"training_evidence": {"format": "tinystudio.training-evidence.v1", "records": []}},
])
def test_claimed_malformed_teacher_evidence_cannot_be_treated_as_clean_or_missing(state):
    with pytest.raises(ValueError, match="teacher.*evidence"):
        audit_teacher_exposure(state, _documents("student"), {"validation": [1], "excluded_test": [2]})


def test_distillation_restores_rng_and_threads_after_training_exception(teacher_checkpoint, tmp_path):
    rng = torch.get_rng_state().clone()
    python_rng = random.getstate()
    threads = torch.get_num_threads()

    def fail_emit(row):
        raise RuntimeError("injected metric callback failure")

    with pytest.raises(RuntimeError, match="injected metric callback failure"):
        execute_learning(_student(teacher_checkpoint), tmp_path / "interrupted", fail_emit, lambda: False)
    assert torch.equal(torch.get_rng_state(), rng)
    assert random.getstate() == python_rng
    assert torch.get_num_threads() == threads
    assert not (tmp_path / "interrupted/checkpoint.pt").exists()


def test_metric_clock_records_optimizer_steps_and_survives_exact_resume(tmp_path):
    from tinystudio.lab.clocks import clock_reference
    initial = _spec(_documents("clock"), steps=2)
    result = execute_learning(initial, tmp_path / "first", lambda row: None, lambda: False)
    path = tmp_path / "first/checkpoint.pt"
    state = load_checkpoint(path)[2]
    assert [row["time"] for row in result["records"]] == [1, 2]
    assert all(row["available_at"] == row["event_time"] == row["step"] for row in result["records"])
    assert all(clock_reference(row)["qualified"] for row in result["records"])
    assert state["metric_clock"]["clock_domain"] == "optimizer_step"
    assert state["metric_clock"]["clock_units"] == "step"
    resumed = _spec(_documents("clock"), steps=4)
    resumed["definition"]["learning"]["initialization"] = "resume"
    resumed["definition"]["inputs"] = [{**{key: value for key, value in _binding(path).items() if key != "path"}, "role": "resume"}]
    resumed["input_paths"] = {"teacher": str(path)}
    continuation = execute_learning(resumed, tmp_path / "resumed", lambda row: None, lambda: False)
    assert [row["time"] for row in continuation["records"]] == [3, 4]
    assert all(clock_reference(row)["key"] == clock_reference(result["records"][0])["key"] for row in continuation["records"])
    state["metric_clock"]["clock_units"] = "s"
    invalid = tmp_path / "invalid-clock.pt"
    torch.save(state, invalid)
    resumed["definition"]["inputs"][0]["sha256"] = _binding(invalid)["sha256"]
    resumed["input_paths"] = {"teacher": str(invalid)}
    with pytest.raises(ValueError, match="optimizer-step clock"):
        execute_learning(resumed, tmp_path / "invalid-clock", lambda row: None, lambda: False)
    assert not (tmp_path / "invalid-clock").exists()


@pytest.mark.parametrize("partition", ["validation", "excluded_test"])
def test_legacy_complete_row_match_blocks_but_source_identity_alone_never_does(partition):
    row = {"source_record_id": "inline:0", "text": "known teacher optimization text"}
    fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    state = {"partition_manifest": {"partitions": {"train": {"records": [
        {"source_record_id": "inline:0", "sha256": fingerprint}]}}}}
    partitions = {"validation": [], "excluded_test": [], partition: [0]}
    positive = audit_teacher_exposure(state, [row], partitions)
    assert positive["matches"][0]["kind"] == "exact_training_record"
    assert positive["qualification"]["status"] == "failed"
    negative = audit_teacher_exposure(state, [{**row, "text": "independent unseen text"}], partitions)
    assert negative["matches"] == []
    assert negative["coverage"] == "unknown"
    assert negative["qualification"]["status"] != "qualified"


def test_teacher_overlap_cannot_be_waived_by_incomplete_research_override(teacher_checkpoint, tmp_path):
    spec = _student(teacher_checkpoint)
    spec["definition"]["parameters"]["documents"][1]["text"] = _documents("teacher")[0]["text"]
    spec["definition"]["research_overrides"] = [{"check_id": "teacher:heldout_training_overlap",
        "reason": "Missing accountable approver"}]
    destination = tmp_path / "invalid-override"
    with pytest.raises(ValueError, match="teacher|held.out|overlap"):
        execute_learning(spec, destination, lambda row: None, lambda: False)
    assert not destination.exists()


def test_bpe_inheritance_distinguishes_fitting_budget_from_actual_vocabulary(tmp_path, monkeypatch):
    documents = _documents("teacher")
    documents[0]["text"] = "aaaa " * 4
    execute_learning(_spec(documents, representation="bpe", vocab_size=320),
        tmp_path / "teacher", lambda row: None, lambda: False)
    path = tmp_path / "teacher/checkpoint.pt"
    _, teacher_tokenizer, teacher_state = load_checkpoint(path)
    assert teacher_tokenizer.vocab_size < 320
    assert teacher_state["tokenizer_configuration"]["vocab_size"] == 320

    def no_refit(*args, **kwargs):
        pytest.fail("BPE fitting budget does not authorize fitting a student tokenizer")

    monkeypatch.setattr(ByteTokenizer, "fit", no_refit)
    execute_learning(_student(path, representation="bpe", vocab_size=320),
        tmp_path / "student", lambda row: None, lambda: False)
    _, tokenizer, state = load_checkpoint(tmp_path / "student/checkpoint.pt")
    assert tokenizer.state() == teacher_tokenizer.state()
    assert state["tokenizer_configuration"] == teacher_state["tokenizer_configuration"]


@pytest.mark.parametrize("initialization", ["weights", "resume"])
def test_initialized_student_cannot_replace_its_frozen_vocabulary_with_teacher(tmp_path, monkeypatch, initialization):
    execute_learning(_spec(_documents("teacher"), representation="engineering", vocabulary=["pressure", "valve"]),
        tmp_path / "teacher", lambda row: None, lambda: False)
    execute_learning(_spec(_documents("student"), representation="engineering", vocabulary=["temperature", "sensor"]),
        tmp_path / "initial", lambda row: None, lambda: False)
    source = tmp_path / "initial/checkpoint.pt"
    original_bytes = source.read_bytes()
    # Omit authored vocabulary so the conflict can only arise from the two
    # frozen checkpoint tokenizers, rather than an explicit parameter mismatch.
    spec = _student(tmp_path / "teacher/checkpoint.pt", representation="engineering", steps=3)
    spec["definition"]["learning"]["initialization"] = initialization
    role = "initial_weights" if initialization == "weights" else "resume"
    spec["definition"]["inputs"].append({"artifact_id": "student-initial", "role": role,
        "sha256": hashlib.sha256(original_bytes).hexdigest()})
    spec["input_paths"]["student-initial"] = str(source)

    def forbidden_optimizer(*args, **kwargs):
        pytest.fail("incompatible frozen student vocabulary must reject before optimizer construction")

    monkeypatch.setattr(torch.optim, "AdamW", forbidden_optimizer)
    destination = tmp_path / "mismatched"
    with pytest.raises(ValueError, match="share tokenizer"):
        execute_learning(spec, destination, lambda row: None, lambda: False)
    assert not destination.exists()
    assert source.read_bytes() == original_bytes


@pytest.mark.parametrize("completed,mutate_teacher", [(0, False), (1, False), (1, True)])
def test_cancelled_distillation_seals_teacher_before_checkpoint_publication(
        teacher_checkpoint, tmp_path, monkeypatch, completed, mutate_teacher):
    original = teacher_checkpoint.read_bytes()
    emitted = []
    teacher_logits = FrozenTeacher.logits

    def queried_teacher(teacher, tokens):
        result = teacher_logits(teacher, tokens)
        if mutate_teacher:
            with torch.no_grad():
                next(teacher._model.parameters()).add_(1)
        return result

    monkeypatch.setattr(FrozenTeacher, "logits", queried_teacher)
    destination = tmp_path / "cancelled-student"
    spec = _student(teacher_checkpoint, steps=3)
    cancelled = lambda: len(emitted) >= completed
    if mutate_teacher:
        with pytest.raises(ValueError, match="teacher parameters or buffers changed"):
            execute_learning(spec, destination, emitted.append, cancelled)
        assert len(emitted) == 1
        assert not (destination / "checkpoint.pt").exists()
        assert not (destination / "learning.json").exists()
    else:
        result = execute_learning(spec, destination, emitted.append, cancelled)
        assert result["qualification"]["status"] == "cancelled"
        assert result["metrics"]["steps"] == completed
        state = load_checkpoint(destination / "checkpoint.pt")[2]
        receipt = state["teacher_receipt"]
        manifest = json.loads((destination / "learning.json").read_text())
        assert receipt == result["qualification"]["teacher_receipt"] == manifest["teacher_receipt"]
        assert receipt["forward_calls"] == completed
        assert receipt["optimizer_excluded"]
        assert receipt["sha256"] == hashlib.sha256(original).hexdigest()
        assert receipt["state_before_sha256"] == receipt["state_after_sha256"]
    assert teacher_checkpoint.read_bytes() == original
