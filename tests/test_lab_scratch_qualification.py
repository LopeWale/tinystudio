"""Local CPU qualification of each scratch family/representation combination.

Run --noconftest: registered authenticated routes, owned PostgreSQL/Redis and
actual native children are used. These fixtures make no model-quality claim.
"""
import hashlib
import json

import pytest
import torch
from fastapi.testclient import TestClient

from tests.test_lab_b_workflow import database, integrated
from tests.test_lab_d_initialization_workflow import _execute
from tinystudio.lab.learning import ByteTokenizer, build_model, load_checkpoint
from tinystudio.lab.runner import Runner


def _assert_updates(before, after):
    # Independently inspect saved tensors; executor status flags cannot pass this.
    assert not torch.equal(before["embedding.weight"], after["embedding.weight"])
    assert not torch.equal(before["head.weight"], after["head.weight"])
    assert any(not torch.equal(value, after[name]) for name, value in before.items()
               if name.startswith("core.")), "core parameters were not updated"


@pytest.mark.parametrize("family", ["transformer", "gru"])
@pytest.mark.parametrize("representation", ["byte", "bpe", "engineering"])
def test_registered_scratch_family_representation_matrix(integrated, family, representation):
    client, prefix, _, app, _, root, _ = integrated
    response = client.post(prefix + "/targets", json={"name": "scratch-matrix-native"})
    assert response.status_code == 201, response.text
    target = response.json()
    codec = {"kind": representation}
    if representation == "bpe":
        codec["vocab_size"] = 280
    if representation == "engineering":
        codec["vocabulary"] = ["valve", "pressure", "kPa"]
    body = {"name": "scratch matrix", "engine": "learning.language", "seed": 31,
        "learning": {"initialization": "scratch", "family": family, "representation": codec,
            "objective": "causal_lm", "update_strategy": "full",
            "architecture": {"hidden_size": 8, "layers": 1, "context": 8,
                             **({"heads": 2} if family == "transformer" else {})}},
        "parameters": {"steps": 4, "prompt": "pressure", "max_new_tokens": 2, "documents": [
            {"text": "pressure -1.25 kPa " * 3, "source_record_id": "train-1", "partition": "train"},
            {"text": "valve open valve closed " * 3, "source_record_id": "train-2", "partition": "train"},
            {"text": "pressure stable pressure stable " * 3, "source_record_id": "train-3", "partition": "train"},
            {"text": "VALIDATION_ZZZZZ", "source_record_id": "validation", "partition": "validation"},
            {"text": "FINAL_YYYYY", "source_record_id": "test", "partition": "test"}]},
        "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 60}}
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as machine:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine)
        runner.advertise()
        run, artifact, state = _execute(client, prefix, runner, target, body, f"scratch-{family}-{representation}")
    tokenizer = ByteTokenizer(**state["tokenizer"])
    assert state["architecture"] == {"family": family, **body["learning"]["architecture"]}
    assert tokenizer.kind == representation
    assert state["split"] == {"train": [0, 1, 2], "validation": [3]}
    assert run["result"]["metrics"]["excluded_test_documents"] == 1
    probe = "pressure -1.25 kPa ∆"
    assert tokenizer.decode(tokenizer.encode(probe)) == probe
    if representation == "byte":
        assert tokenizer.vocab_size == 257
        assert tokenizer.encode("-1.25") == [45, 49, 46, 50, 53]
    elif representation == "engineering":
        assert tokenizer.vocab_size == 260
        assert tokenizer.encode("pressure -1.25 kPa") == [256, 32, 45, 49, 46, 50, 53, 32, 258]
    else:
        assert 257 < tokenizer.vocab_size <= 280
        assert not any(b"Z" in part or b"Y" in part for part in tokenizer.pieces[256:])

    previous_threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(body["seed"])
            initial = build_model(state["architecture"], tokenizer.vocab_size).state_dict()
    finally:
        torch.set_num_threads(previous_threads)
    _assert_updates(initial, state["model"])
    with pytest.raises(AssertionError):
        _assert_updates(initial, initial)  # A no-op optimizer must fail the oracle.
    frozen_core = {**state["model"], **{name: value for name, value in initial.items() if name.startswith("core.")}}
    with pytest.raises(AssertionError, match="core parameters"):
        _assert_updates(initial, frozen_core)
    # Embedding + output projection; GRU gates or attention/FFN/norms + positions.
    expected_parameters = 17 * tokenizer.vocab_size + (936 if family == "transformer" else 432)
    assert run["result"]["metrics"]["parameters"] == expected_parameters
    assert torch.isfinite(torch.tensor(run["result"]["metrics"]["validation_loss"]))
    qualification = run["result"]["qualification"]
    assert qualification["status"] == "completed" and qualification["backend"] == "cpu"
    assert qualification["initialization"] == "scratch" and qualification["save_unload_reload"]

    # The native child has exited. Reload its transported artifact independently.
    checkpoint = root / "transported-checkpoint.pt"
    content = client.get(prefix + "/artifacts/" + artifact["id"])
    assert content.status_code == 200 and hashlib.sha256(content.content).hexdigest() == artifact["sha256"]
    checkpoint.write_bytes(content.content)
    model, restored, _ = load_checkpoint(checkpoint)
    assert isinstance(model.core, torch.nn.TransformerEncoder if family == "transformer" else torch.nn.GRU)
    assert model.embedding.weight.dtype == state["model"]["embedding.weight"].dtype == torch.float32
    assert restored.state() == tokenizer.state()
    assert all(torch.equal(value, model.state_dict()[name]) for name, value in state["model"].items())
    generation = next(a for a in run["result"]["artifacts"] if a["role"] == "generation")
    response = client.get(prefix + "/artifacts/" + generation["id"])
    assert response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == generation["sha256"]
    result = json.loads(response.content)
    assert result["checkpoint_sha256"] == artifact["sha256"] and result["prompt"] == "pressure"
    assert all(type(token) is int and 0 <= token < tokenizer.vocab_size for token in result["token_ids"])
