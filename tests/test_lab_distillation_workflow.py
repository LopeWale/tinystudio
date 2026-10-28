"""D: retained teacher to smaller student through app routes and native children.

Run --noconftest. These UUID-owned PostgreSQL/Redis fixtures qualify software,
not transfer quality. Every student uses a distinct, explicitly partitioned corpus.
"""
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
import torch

from tests.test_lab_b_workflow import database, integrated
from tests.test_lab_d_initialization_workflow import _execute
from tests.test_lab_scratch_qualification import _assert_updates
from tinystudio.lab.learning import build_model, load_checkpoint, generate_from_checkpoint
from tinystudio.lab.runner import Runner


def definition(family, representation, prefix, *, hidden=16, seed=31):
    codec = {"kind": representation}
    if representation == "bpe":
        codec["vocab_size"] = 280
    if representation == "engineering":
        codec["vocabulary"] = ["pressure", "valve", "kPa"]
    return {"name": prefix, "engine": "learning.language", "seed": seed,
        "learning": {"initialization": "scratch", "family": family, "representation": codec,
            "objective": "causal_lm", "update_strategy": "full", "architecture": {
                "hidden_size": hidden, "layers": 1, "context": 8,
                **({"heads": 2} if family == "transformer" else {})}},
        "parameters": {"steps": 3, "prompt": "pressure -1.25 kPa", "max_new_tokens": 2,
            "documents": [{"text": f"{prefix} pressure valve -1.25 kPa {index} " * 3,
                "source_record_id": f"{prefix}:{index}", "partition": partition}
                for index, partition in enumerate(("train", "train", "train", "validation", "test"))]},
        "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 60}}


@pytest.mark.parametrize("student_family", ["gru", "transformer"])
@pytest.mark.parametrize("representation", ["byte", "bpe", "engineering"])
def test_registered_retained_teacher_to_smaller_cross_family_student(integrated, monkeypatch, student_family, representation):
    client, prefix, other_prefix, app, _, root, _ = integrated
    response = client.post(prefix + "/targets", json={"name": "distillation-native"})
    assert response.status_code == 201, response.text
    target = response.json()
    teacher_family = "transformer" if student_family == "gru" else "gru"
    teacher_definition = definition(teacher_family, representation, "teacher-source")
    student_definition = definition(student_family, representation, "student-source", hidden=4, seed=71)
    student_definition["learning"].update(objective="distillation")
    # The actual editor's From teacher choice removes the representation field.
    # Model family, dimensions and source data remain independently authored.
    student_definition["learning"].pop("representation")
    student_definition["parameters"].update(temperature=2.5, distillation_alpha=.7)
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        teacher_run, teacher_artifact, teacher_state = _execute(client, prefix, runner, target,
            teacher_definition, f"teacher-{student_family}-{representation}")
        original_bytes = client.get(prefix + "/artifacts/" + teacher_artifact["id"]).content
        student_definition["inputs"] = [{"artifact_id": teacher_artifact["id"], "role": "teacher",
            "sha256": teacher_artifact["sha256"], "visibility": "model"}]
        library = client.get(prefix + "/inputs").json()
        retained = next(item for item in library if item["id"] == teacher_artifact["id"])
        assert retained["origin"]["run_id"] == teacher_run["id"]
        assert client.get(other_prefix + "/artifacts/" + teacher_artifact["id"]).status_code == 404
        student_run, student_artifact, state = _execute(client, prefix, runner, target,
            student_definition, f"student-{student_family}-{representation}")
    assert state["architecture"] == {"family": student_family, **student_definition["learning"]["architecture"]}
    assert state["tokenizer"] == teacher_state["tokenizer"]
    assert state["tokenizer_configuration"] == teacher_state["tokenizer_configuration"]
    assert state["tokenizer_source"] == "teacher"
    assert state["split"] == {"train": [0, 1, 2], "validation": [3]}
    assert state["partition_manifest"]["excluded_test"] == [4]
    receipt = state["teacher_receipt"]
    assert receipt["sha256"] == teacher_artifact["sha256"]
    assert receipt["artifact_id"] == teacher_artifact["id"]
    assert receipt["state_before_sha256"] == receipt["state_after_sha256"]
    assert receipt["optimizer_excluded"] and receipt["forward_calls"] == 3
    assert receipt["teacher_mode"] == "eval_no_grad" and receipt["tokens_queried"] > 0
    timeline = client.get(prefix + f"/runs/{student_run['id']}/records", params={"artifact": "processed.jsonl"})
    assert timeline.status_code == 200
    metric_rows = timeline.json()["records"]
    assert [row["time"] for row in metric_rows] == [1, 2, 3]
    assert all(row["clock_domain"] == "optimizer_step" and row["clock_units"] == "step" for row in metric_rows)
    assert all(row["field_units"]["distillation_kl"] == "nats/token" for row in metric_rows)
    assert state["teacher_exposure"]["coverage"] == "unknown"
    assert not state["teacher_exposure"]["matches"]
    assert state["teacher_exposure"]["qualification"]["status"] == "unverified"
    qualification = student_run["result"]["qualification"]
    assert qualification["held_out_validation_scope"] == qualification["final_test_exclusion_scope"] == "current_student_optimizer_only"
    assert student_run["result"]["metrics"]["parameters"] < teacher_run["result"]["metrics"]["parameters"]
    # Independently construct the initialized student, rather than trusting a
    # status flag or comparing two copies of the final checkpoint.
    previous_threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(student_definition["seed"])
            from tinystudio.lab.learning import ByteTokenizer
            vocabulary = ByteTokenizer(**teacher_state["tokenizer"]).vocab_size
            initial = build_model(state["architecture"], vocabulary).state_dict()
    finally:
        torch.set_num_threads(previous_threads)
    _assert_updates(initial, state["model"])
    unchanged = client.get(prefix + "/artifacts/" + teacher_artifact["id"])
    assert unchanged.content == original_bytes
    assert hashlib.sha256(unchanged.content).hexdigest() == teacher_artifact["sha256"]
    downloaded = root / "independent-student.pt"
    downloaded.write_bytes(client.get(prefix + "/artifacts/" + student_artifact["id"]).content)
    assert hashlib.sha256(downloaded.read_bytes()).hexdigest() == student_artifact["sha256"]
    read_bytes = Path.read_bytes
    def only_student(path):
        assert path.resolve() == downloaded.resolve(), "student inference attempted to read an unretained dependency"
        return read_bytes(path)
    with monkeypatch.context() as guard:
        guard.setattr(Path, "read_bytes", only_student)
        model, tokenizer, restored = load_checkpoint(downloaded)
        generation = generate_from_checkpoint(downloaded, "pressure -1.25 kPa", 2)
    assert all(torch.equal(value, model.state_dict()[name]) for name, value in state["model"].items())
    assert restored["teacher_receipt"] == receipt and tokenizer.state() == state["tokenizer"]
    assert generation["checkpoint_sha256"] == student_artifact["sha256"]
    # The independently downloaded student contains all inference dependencies;
    # its teacher is a provenance reference, not an inference-time filesystem path.
    manifest_artifact = next(item for item in student_run["result"]["artifacts"] if item["role"] == "training_manifest")
    response = client.get(prefix + "/artifacts/" + manifest_artifact["id"])
    assert hashlib.sha256(response.content).hexdigest() == manifest_artifact["sha256"]
    manifest = json.loads(response.content)
    assert manifest["teacher_receipt"] == receipt
    assert manifest["integrity_scope"] == "current_student_optimizer_only"
