"""D: authenticated application routes to real isolated Docker NF4 training.

Run with --noconftest and disposable PostgreSQL CREATE DATABASE credentials.
The pinned, already-local image is required: unavailable infrastructure fails
qualification. Three-step fixtures establish software behavior, not research value.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.test_lab_b_workflow import integrated, database
from tests import test_lab_qlora as qlora_fixture
from tinystudio.lab.contracts import checksum
from tinystudio.lab.runner import Runner


IMAGE = "tinystudio@sha256:2c8af1da323514b5fee5b56c724424b18fe0aeb11873de1dbc59ccd33c478443"


def docker_python(code, *, mounts=(), environment=None, timeout=180):
    """Run only the pinned local pack, without network, device or secret grants."""
    container_name = "tinystudio-d-proof-" + uuid.uuid4().hex
    command = ["docker", "run", "--rm", "--name", container_name, "--pull=never", "--network", "none",
               "--read-only", "--workdir", "/", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
               "--cpus", "1", "--memory", "2g", "--pids-limit", "128",
               "--user", f"{max(1, os.getuid())}:{max(1, os.getgid())}"]
    for source, target, writable in mounts:
        command += ["--mount", f"type=bind,src={source},dst={target}" + ("" if writable else ",readonly")]
    for name, value in {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", **(environment or {})}.items():
        command += ["--env", f"{name}={value}"]
    try:
        result = subprocess.run([*command, IMAGE, "python", "-c", code], capture_output=True,
                                text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        # Killing the Docker client alone can leave its container alive.
        cleanup = subprocess.run(["docker", "rm", "--force", container_name], capture_output=True,
                                 text=True, timeout=30, check=False)
        assert cleanup.returncode == 0 or "No such container" in cleanup.stderr, cleanup.stderr
        raise
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    return result.stdout


def test_reload_timeout_removes_only_its_own_container(monkeypatch):
    commands = []
    def execute(command, **kwargs):
        commands.append(command)
        if len(commands) == 1:
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', execute)
    with pytest.raises(subprocess.TimeoutExpired):
        docker_python('pass', environment={'HOME': '/proof'}, timeout=1)
    owned = commands[0][commands[0].index('--name') + 1]
    assert owned.startswith('tinystudio-d-proof-')
    assert commands[1] == ['docker', 'rm', '--force', owned]


@pytest.fixture(scope="module")
def image_runtime():
    # Inspect and measure, rather than asserting the macOS host's versions apply.
    identity = subprocess.run(["docker", "image", "inspect", IMAGE, "--format", "{{json .RepoDigests}}"],
                              capture_output=True, text=True, check=True)
    assert IMAGE in json.loads(identity.stdout), "Local image must retain the selected immutable repository digest"
    measured = docker_python('''
import importlib.metadata as metadata, json, platform
runtime = {"os": platform.system(), "machine": platform.machine(), "python": platform.python_version()}
for name in ("numpy", "torch", "transformers", "pyarrow", "tokenizers", "peft", "trl", "accelerate", "bitsandbytes", "safetensors", "datasets", "sentence-transformers", "scipy", "pint", "simpy", "ortools", "hypothesis", "fmpy", "pybamm", "pandapower", "torchrl", "tensordict", "pyserial", "pymodbus", "paho-mqtt"):
    try: runtime[name] = metadata.version(name)
    except metadata.PackageNotFoundError: pass
print(json.dumps(runtime))
''')
    runtime = json.loads(measured)
    assert "bitsandbytes" in runtime, "Pinned image hides bitsandbytes under the isolated runner environment: " + measured
    assert (runtime["os"], runtime["machine"], runtime["python"]) == ("Linux", "aarch64", "3.12.16")
    assert {key: runtime[key] for key in ("torch", "transformers", "peft", "trl", "bitsandbytes", "accelerate", "tokenizers", "safetensors", "datasets")} == {
        "torch": "2.15.0+cpu", "transformers": "5.21.0", "peft": "0.21.2", "trl": "1.16.0", "bitsandbytes": "0.50.2",
        "accelerate": "1.15.0", "tokenizers": "0.23.3", "safetensors": "0.8.0", "datasets": "5.1.0"}
    return runtime


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
        integrated, pretrained_fixture, image_runtime, objective):
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
    runtime_evidence = {"image": IMAGE, "runtime": image_runtime,
                        "sha256": checksum({"image": IMAGE, "runtime": image_runtime})}
    profile = {"schema_version": 1, "backends": {"container": {
        "enabled": True, "profiles": ["isolated"], "image": IMAGE,
        "engines": ["learning.pretrained"], "runtime_evidence": runtime_evidence,
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
    assert manifest["backend"] == "container" and manifest["image"] == IMAGE
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
weights = next((extracted / "quantized-base").glob("*.safetensors"))
weights.write_bytes(weights.read_bytes() + b"corrupt")
try: load_pretrained_output(extracted, device="cpu")
except ValueError as error: assert "checksum mismatch" in str(error), str(error)
else: raise AssertionError("corrupt retained base was accepted")
print(json.dumps({"reload": "equal", "corruption": "rejected", "objective": manifest["objective"]}))
'''.replace("EXPECTED", repr(hashlib.sha256(bundle.read_bytes()).hexdigest()))
    reload = docker_python(script, mounts=[(source, "/source", False), (transport_root, "/proof", True)],
        environment={"PYTHONPATH": "/source", "HOME": "/proof", "TMPDIR": "/proof"})
    assert json.loads(reload) == {"reload": "equal", "corruption": "rejected", "objective": objective}
