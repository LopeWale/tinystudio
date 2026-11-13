"""D: authenticated application routes to real isolated Docker NF4 training.

Run with --noconftest and disposable PostgreSQL CREATE DATABASE credentials.
TINYSTUDIO_TEST_CONTAINER_PROFILE selects an already-local immutable image and
its exact recorded runtime map. Unavailable infrastructure fails qualification.
Three-step fixtures establish software behavior, not research value.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.support.environment import ContainerQualification, TestEnvironment
from tests.test_lab_b_workflow import integrated, database
from tests import test_lab_qlora as qlora_fixture
from tinystudio.lab.contracts import checksum
from tinystudio.lab.runner import Runner


@pytest.fixture(scope="module")
def container_qualification():
    return TestEnvironment.from_environment().require_container()


def test_reload_timeout_removes_only_its_own_container(monkeypatch):
    # This lifecycle test has synthetic configuration and never needs a daemon.
    selected = ContainerQualification(SimpleNamespace(resolve=lambda name: "docker"),
        "fixture/qualification@sha256:" + "a" * 64,
        {name: "fixture" for name in ContainerQualification.required_runtime_keys})
    commands = []
    identifier = "b" * 64
    def execute(command, **kwargs):
        commands.append(command)
        if len(commands) == 1:
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        if command[1] == 'inspect':
            if command[-1] == identifier:
                return subprocess.CompletedProcess(command, 1, '', 'Error: No such object: ' + identifier)
            label = commands[0][commands[0].index('--label') + 1]
            key, owner = label.split('=', 1)
            return subprocess.CompletedProcess(command, 0, json.dumps([{'Id': identifier,
                'Config': {'Image': selected.image, 'Labels': {key: owner}}}]), '')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', execute)
    with pytest.raises(subprocess.TimeoutExpired):
        selected.python('pass', environment={'HOME': '/proof'}, timeout=1)
    owned = commands[0][commands[0].index('--name') + 1]
    assert owned.startswith('tinystudio-d-proof-')
    assert commands[1:] == [['docker', 'inspect', owned],
        ['docker', 'rm', '--force', identifier], ['docker', 'inspect', identifier]]


@pytest.fixture(scope="module")
def image_runtime(container_qualification):
    return container_qualification.measure_runtime()


@pytest.fixture
def pretrained_fixture():
    fixture = qlora_fixture.QLoRAQualification(methodName="test_sft_qlora")
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize("objective", ["sft", "causal_lm", "dpo"])
def test_authenticated_application_to_docker_qlora_and_retained_reload(
        integrated, pretrained_fixture, container_qualification, image_runtime, objective):
    image = container_qualification.image
    client, prefix, other_prefix, app, _, root, _ = integrated
    fixture = pretrained_fixture
    private = fixture.specification(objective)
    with TestClient(app) as anonymous:
        assert anonymous.get(prefix + "/definitions").status_code == 401
    references = []
    for original in private["definition"]["inputs"]:
        path = Path(private["input_paths"][original["artifact_id"]])
        uploaded = client.post(prefix + "/inputs", files={"file": (path.name, path.read_bytes(), "application/octet-stream")})
        assert uploaded.status_code == 201, uploaded.text
        artifact = uploaded.json()
        assert artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        references.append({"artifact_id": artifact["id"], "role": original["role"], "sha256": artifact["sha256"]})
        assert client.get(other_prefix + "/artifacts/" + artifact["id"]).status_code == 404
    runtime_evidence = {"image": image, "runtime": image_runtime,
                        "sha256": checksum({"image": image, "runtime": image_runtime})}
    profile = {"schema_version": 1, "backends": {"container": {
        "enabled": True, "profiles": ["isolated"], "image": image, "python": container_qualification.python_command,
        "engines": ["learning.pretrained", "evaluation.compare"], "runtime_evidence": runtime_evidence,
        "limits": {"cpu": 1, "memory_bytes": 2 * 1024**3, "pids": 128}}}}
    target_response = client.post(prefix + "/targets", json={"name": "d-qlora-" + objective})
    assert target_response.status_code == 201, target_response.text
    target = target_response.json()
    machine = TestClient(app, headers={"Authorization": "Bearer " + target["token"]})
    try:
        runner = Runner("http://127.0.0.1", target["id"], target["token"], client=machine, profile_config=profile)
        runner.advertise()
        definition = {**private["definition"], "name": "D deterministic " + objective,
                      "engine": "learning.pretrained", "inputs": references,
                      "execution": {"profile": "isolated", "backend": "container", "max_seconds": 180,
                                    "max_output_bytes": 64 * 1024**2}}
        preflight = client.post(prefix + "/preflight", json={"definition": definition, "target_id": target["id"]})
        assert preflight.status_code == 200, preflight.text
        assert preflight.json()["compatible"], preflight.json()
        saved = client.post(prefix + "/definitions", json={"definition": definition})
        assert saved.status_code == 201, saved.text
        submitted = client.post(prefix + "/runs", json={"definition_id": saved.json()["id"], "revision": 1,
            "target_id": target["id"], "idempotency_key": "d-qlora-" + objective})
        assert submitted.status_code == 202, submitted.text
        run_id = submitted.json()["id"]
        assert runner.run_once(run_id=run_id)
    finally:
        machine.close()
    response = client.get(prefix + "/runs/" + run_id)
    assert response.status_code == 200
    run = response.json()
    assert run["state"] == "succeeded", run
    assert run["spec"]["runtime"] == image_runtime
    assert run["spec"]["definition"]["learning"]["objective"] == objective
    result = run["result"]
    assert result["metrics"]["steps"] == 3
    assert result["metrics"]["model_sha256"] == fixture.model_hash
    assert result["qualification"]["live_policy_reload_equal"] is True
    assert result["qualification"]["excluded_final_test_records"] == 1
    receipt = result["execution_receipt"]
    assert receipt["runtime"] == image_runtime
    manifest = receipt["profile_manifest"]
    assert manifest["backend"] == "container" and manifest["image"] == image
    assert manifest["network_default"] == "deny"
    assert manifest["grants"]["secret_file_count"] == manifest["grants"]["device_count"] == 0
    downloaded = {}
    for artifact in result["artifacts"]:
        response = client.get(prefix + "/artifacts/" + artifact["id"])
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
        downloaded[artifact["role"]] = response.content
    retained = json.loads(downloaded["pretrained_manifest"])
    assert (retained["format"], retained["output_kind"], retained["training_type"]) == (
        "tinystudio.pretrained.v2", "quantized_adapter", "qlora")
    assert retained["objective"] == objective
    assert retained["quantization"]["precision"]["adapter_dtype"] == "float32"
    assert retained["reload_probes"]["comparison"] == "bitwise_equal_last_token_logits"
    assert retained["split"]["train"] == [0, 1, 2]
    assert retained["split"]["validation"] == [3]
    assert retained["split"]["excluded_test"] == [4]
    snapshot = Path(private["input_paths"]["data"])
    assert downloaded["training_snapshot"] == snapshot.read_bytes()
    assert hashlib.sha256(fixture.archive.read_bytes()).hexdigest() == fixture.model_hash
    transport_root = (root / "transport").resolve()
    transport_root.mkdir()
    bundle = transport_root / "downloaded.tar"
    bundle.write_bytes(downloaded["pretrained_model_bundle"])
    # Reuse the retained trained artifact through the registered generation
    # engine, with evaluator-only numeric truth and the same isolated Runner.
    trained_bundle = next(item for item in result["artifacts"] if item["role"] == "pretrained_model_bundle")
    model_ref = {"artifact_id": trained_bundle["id"], "sha256": trained_bundle["sha256"],
                 "role": "model", "visibility": "model"}
    assert model_ref["sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert client.get(other_prefix + "/artifacts/" + model_ref["artifact_id"]).status_code == 404
    cases_content = json.dumps({"format": "tinystudio.evaluation-cases.v1", "cases": [
        {"source_id": "d-qlora-heldout-" + objective, "episode_id": "d-independent-generation",
         "partition": "test", "prompt": "sensor ready", "expected": "-987654321", "unit": "Pa"}
    ]}).encode()
    uploaded_cases = client.post(prefix + "/inputs", files={"file": (
        "generation-cases.json", cases_content, "application/json")})
    assert uploaded_cases.status_code == 201, uploaded_cases.text
    cases_artifact = uploaded_cases.json()
    assert cases_artifact["sha256"] == hashlib.sha256(cases_content).hexdigest()
    cases_ref = {"artifact_id": cases_artifact["id"], "sha256": cases_artifact["sha256"],
                 "role": "cases", "visibility": "evaluation"}
    assert client.get(other_prefix + "/artifacts/" + cases_ref["artifact_id"]).status_code == 404
    generation_definition = {"name": "D retained QLoRA generation " + objective,
        "engine": "evaluation.compare", "execution": definition["execution"],
        "parameters": {"operation": "generate", "max_new_tokens": 4, "device": "cpu"},
        "inputs": [model_ref, cases_ref]}
    checked = client.post(prefix + "/preflight", json={"definition": generation_definition, "target_id": target["id"]})
    assert checked.status_code == 200 and checked.json()["compatible"], checked.text
    saved_generation = client.post(prefix + "/definitions", json={"definition": generation_definition})
    assert saved_generation.status_code == 201, saved_generation.text
    admitted_generation = client.post(prefix + "/runs", json={
        "definition_id": saved_generation.json()["id"], "revision": saved_generation.json()["revision"],
        "target_id": target["id"], "idempotency_key": "d-qlora-generate-" + objective})
    assert admitted_generation.status_code == 202, admitted_generation.text
    generation_id = admitted_generation.json()["id"]
    with TestClient(app, headers={"Authorization": "Bearer " + target["token"]}) as generation_machine:
        generation_runner = Runner("http://127.0.0.1", target["id"], target["token"],
            client=generation_machine, profile_config=profile)
        generation_runner.advertise()
        assert generation_runner.run_once(run_id=generation_id)
    generated_run = client.get(prefix + "/runs/" + generation_id).json()
    assert generated_run["state"] == "succeeded", generated_run
    assert generated_run["spec"]["runtime"] == image_runtime
    frozen_inputs = generated_run["spec"]["definition"]["inputs"]
    assert {item["role"]: (item["artifact_id"], item["sha256"], item["visibility"]) for item in frozen_inputs} == {
        item["role"]: (item["artifact_id"], item["sha256"], item["visibility"]) for item in (model_ref, cases_ref)}
    generated_result = generated_run["result"]
    assert generated_result["metrics"] == {"cases": 1, "predictions": 1}
    generation_receipt = generated_result["execution_receipt"]
    assert generation_receipt["runtime"] == image_runtime
    generation_manifest = generation_receipt["profile_manifest"]
    assert generation_manifest["backend"] == "container" and generation_manifest["image"] == image
    assert generation_manifest["network_default"] == "deny"
    assert generation_manifest["grants"]["secret_file_count"] == generation_manifest["grants"]["device_count"] == 0
    generation_artifacts = {}
    for item in generated_result["artifacts"]:
        fetched = client.get(prefix + "/artifacts/" + item["id"])
        assert fetched.status_code == 200, fetched.text
        assert hashlib.sha256(fetched.content).hexdigest() == item["sha256"]
        assert client.get(other_prefix + "/artifacts/" + item["id"]).status_code == 404
        generation_artifacts[item["role"]] = fetched.content
    generation_report_bytes = generation_artifacts["independent_evaluation"]
    generation_report = json.loads(generation_report_bytes)
    assert generation_report["format"] == "tinystudio.generation-evaluation.v1"
    assert generation_report["model"]["sha256"] == model_ref["sha256"]
    assert generation_report["model"]["provenance"] == retained
    assert generation_report["model"]["provenance"]["training_type"] == "qlora"
    assert generation_report["model"]["provenance"]["output_kind"] == "quantized_adapter"
    assert len(generation_report["records"]) == 1
    generation_row = generation_report["records"][0]
    assert generation_row["model"] == {key: model_ref[key] for key in ("artifact_id", "sha256")}
    assert generation_row["cases"] == {key: cases_ref[key] for key in ("artifact_id", "sha256")}
    assert generation_row["partition"] == "test" and generation_row["arm"] == "without_retrieval"
    assert generation_row["expected"] == "-987654321" and generation_row["unit"] == "Pa"
    assert "-987654321" not in generation_row["output"]["prompt"]
    assert generation_row["output"]["prompt"] == "Evidence:\n\n\nQuestion:\nsensor ready\nAnswer:\n"
    assert generation_row["output"]["max_new_tokens"] == 4
    # Success means execution completed; arbitrary neural text need not solve
    # this held-out numeric case. Preserve the real report for independent replay.
    (transport_root / "generation-report.json").write_bytes(generation_report_bytes)
    for ref, expected_bytes in ((model_ref, bundle.read_bytes()), (cases_ref, cases_content)):
        frozen = client.get(prefix + "/artifacts/" + ref["artifact_id"])
        assert frozen.status_code == 200 and frozen.content == expected_bytes
        assert hashlib.sha256(frozen.content).hexdigest() == ref["sha256"]
    # Snapshot current implementation into an owned mount; never expose repo,
    # credentials, shared artifact stores or other sessions to this process.
    package = Path(__file__).resolve().parents[1] / "tinystudio"
    source = (root / "reload-source").resolve()
    for path in package.rglob("*.py"):
        destination = source / "tinystudio" / path.relative_to(package)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    script = '''
import hashlib, json
from pathlib import Path
from tinystudio.lab.compatibility import extract_model_archive, load_pretrained_output
from tinystudio.training.quantization import state_digest
from safetensors.torch import load_file
from bitsandbytes.nn import Linear4bit
import torch
root = Path("/proof")
archive = root / "downloaded.tar"
assert hashlib.sha256(archive.read_bytes()).hexdigest() == EXPECTED
extracted = extract_model_archive(archive, root / "extracted")
model, tokenizer, manifest = load_pretrained_output(extracted, device="cpu")
quantized = [module for module in model.modules() if isinstance(module, Linear4bit)]
assert len(quantized) == 7
assert all(module.weight.dtype == torch.uint8 and module.weight.quant_state.nested for module in quantized)
probes = manifest["reload_probes"]
probe_file = extracted / probes["file"]
assert hashlib.sha256(probe_file.read_bytes()).hexdigest() == probes["sha256"]
expected = load_file(str(probe_file))
for index, inputs in enumerate(probes["inputs"]):
    encoded_probe = {name: torch.tensor(value, dtype=torch.long) for name, value in inputs.items()}
    with torch.no_grad(): actual = model(**encoded_probe).logits[0, -1].detach().cpu()
    assert torch.equal(actual, expected[f"probe_{index}"]), "transported live-policy logits differ"
assert state_digest(model, base_only=True) == manifest["quantization"]["state_sha256"]
encoded = {name: value for name, value in tokenizer("sensor ready", return_tensors="pt").items() if name in {"input_ids", "attention_mask"}}
with torch.no_grad(): assert torch.isfinite(model(**encoded).logits).all()
# Exercise the consumer against the transported archive, preserving packed NF4.
from tinystudio.lab.model_session import ModelSession
from tinystudio.lab.engine import ExecutionCancelled
from transformers import GenerationConfig
session_work = root / "session-work"
archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
prompt, max_new_tokens = "sensor ready", 4
prompt_batch = tokenizer(prompt, return_tensors="pt", add_special_tokens=True, truncation=False)
input_ids = prompt_batch["input_ids"][0].tolist()
# Independent direct retained-loader oracle, with the same explicit HF arguments.
configuration = GenerationConfig(do_sample=False, num_beams=1, max_new_tokens=max_new_tokens,
    eos_token_id=model.generation_config.eos_token_id,
    pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id)
model.eval()
with torch.inference_mode():
    direct = model.generate(**prompt_batch, generation_config=configuration)
expected_tokens = direct[0, len(input_ids):].tolist()
expected_completion = tokenizer.decode(expected_tokens, skip_special_tokens=True)
assert expected_tokens, "fixture must execute actual token generation"

def assert_quantized(session):
    modules = [module for module in session._model.modules() if isinstance(module, Linear4bit)]
    assert len(modules) == 7
    assert all(module.weight.dtype == torch.uint8 and module.weight.quant_state.nested for module in modules)
    assert state_digest(session._model, base_only=True) == manifest["quantization"]["state_sha256"]

def assert_closed(session):
    assert session._model is None and session._tokenizer is None
    assert not list(session_work.iterdir()), "session leaked owned extracted files"
    try: session.generate(prompt, 1)
    except RuntimeError as error: assert "closed" in str(error)
    else: raise AssertionError("closed quantized model session accepted inference")

session = ModelSession(archive, archive_sha, session_work, device="cpu")
with session:
    metadata = session.metadata
    assert metadata["kind"] == "pretrained_language"
    assert metadata["provenance"] == manifest
    assert metadata["sha256"] == archive_sha and metadata["device"] == "cpu"
    assert metadata["model_devices"] == ["cpu"] and metadata["network_downloads"] is False
    assert metadata["context_capacity"] == model.config.max_position_embeddings == 64
    # Metadata is an independent snapshot; consumers cannot mutate provenance.
    metadata["provenance"]["objective"] = "mutated consumer copy"
    assert session.metadata["provenance"]["objective"] == manifest["objective"]
    assert_quantized(session)
    generated = session.generate(prompt, max_new_tokens)
    assert generated["input_token_ids"] == input_ids
    assert generated["prompt_tokens"] == len(input_ids)
    assert generated["token_ids"] == expected_tokens
    assert generated["completion"] == expected_completion
    assert generated["generated_tokens"] == len(expected_tokens)
    assert generated["max_new_tokens"] == max_new_tokens
    assert generated["context_capacity"] == 64
    registered = json.loads((root / "generation-report.json").read_bytes())
    assert registered["model"]["sha256"] == archive_sha
    assert registered["model"]["provenance"] == manifest
    reported = registered["records"][0]["output"]
    replayed = session.generate(reported["prompt"], reported["max_new_tokens"])
    assert replayed == reported, "registered QLoRA generation differs from independent session replay"
    registered_batch = tokenizer(reported["prompt"], return_tensors="pt", add_special_tokens=True, truncation=False)
    with torch.inference_mode():
        registered_direct = model.generate(**registered_batch, generation_config=configuration)
    registered_ids = registered_batch["input_ids"][0].tolist()
    registered_tokens = registered_direct[0, len(registered_ids):].tolist()
    assert replayed["input_token_ids"] == registered_ids
    assert replayed["token_ids"] == registered_tokens
    assert replayed["completion"] == tokenizer.decode(registered_tokens, skip_special_tokens=True)
    assert_quantized(session)
assert_closed(session)

# Fresh extraction and load must reproduce deterministic tokens after unload.
reloaded = ModelSession(archive, archive_sha, session_work, device="cpu")
with reloaded:
    assert_quantized(reloaded)
    assert reloaded.generate(prompt, max_new_tokens) == generated
    # The exact capacity is accepted with no generation reservation.
    boundary_prompt = " ".join(["sensor"] * 64)
    assert len(tokenizer(boundary_prompt)["input_ids"]) == 64
    boundary = reloaded.generate(boundary_prompt, 0)
    assert boundary["prompt_tokens"] == 64 and boundary["token_ids"] == []
assert_closed(reloaded)

# Reservation is checked before generation, even if EOS might arrive early.
overflow = ModelSession(archive, archive_sha, session_work, device="cpu")
try: overflow.generate(boundary_prompt, 1)
except ValueError as error: assert "context capacity" in str(error)
else: raise AssertionError("quantized inference exceeded reserved context")
assert_closed(overflow)

# Observe the real HF call, distinguishing interruption inside generate from
# cancellation detected only after it returns. The controlled omission below
# must fail that oracle even though the later cancellation check still raises.
def assert_interrupted_in_generation(observed):
    assert observed["entered"] and not observed["returned"], "generation completed before cancellation"

for threshold, suppress_stopping in ((1, False), (3, False), (3, True)):
    cancelled_session = ModelSession(archive, archive_sha, session_work, device="cpu")
    checks = [0]
    observed = {"entered": False, "returned": False}
    original_generate = cancelled_session._model.generate
    def observe_generate(*args, **kwargs):
        observed["entered"] = True
        if suppress_stopping:
            kwargs.pop("stopping_criteria")
        result = original_generate(*args, **kwargs)
        observed["returned"] = True
        return result
    cancelled_session._model.generate = observe_generate
    def cancelled():
        checks[0] += 1
        return checks[0] >= threshold
    try: cancelled_session.generate(prompt, max_new_tokens, cancelled=cancelled)
    except ExecutionCancelled: pass
    else: raise AssertionError("quantized inference ignored cancellation")
    assert checks[0] >= threshold
    if threshold == 1:
        assert not observed["entered"]
    elif suppress_stopping:
        assert observed == {"entered": True, "returned": True}
        try: assert_interrupted_in_generation(observed)
        except AssertionError: pass
        else: raise AssertionError("cancellation oracle accepted a completed generation")
    else:
        assert_interrupted_in_generation(observed)
    assert_closed(cancelled_session)
    del original_generate
assert hashlib.sha256(archive.read_bytes()).hexdigest() == archive_sha
assert state_digest(model, base_only=True) == manifest["quantization"]["state_sha256"]
weights = next((extracted / "quantized-base").glob("*.safetensors"))
weights.write_bytes(weights.read_bytes() + b"corrupt")
try: load_pretrained_output(extracted, device="cpu")
except ValueError as error: assert "checksum mismatch" in str(error), str(error)
else: raise AssertionError("corrupt retained base was accepted")
print(json.dumps({"reload": "equal", "corruption": "rejected", "objective": manifest["objective"],
                  "model_session": "quantized_equal_reloaded_context_cancel_clean"}))
'''.replace("EXPECTED", repr(hashlib.sha256(bundle.read_bytes()).hexdigest()))
    reload = container_qualification.python(script, mounts=[(source, "/source", False), (transport_root, "/proof", True)],
        environment={"PYTHONPATH": "/source", "HOME": "/proof", "TMPDIR": "/proof"})
    assert json.loads(reload) == {"reload": "equal", "corruption": "rejected", "objective": objective,
                                  "model_session": "quantized_equal_reloaded_context_cancel_clean"}
