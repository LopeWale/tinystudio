"""Cancelled audit publication uses real children and an in-process lease server."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import httpx
import pytest

from tinystudio.lab import runner as module
from tinystudio.lab.contracts import checksum
from tinystudio.lab.runner import ProcessGroup, Runner
from tinystudio.lab.runner_publication import PublicationUnconfirmed, RunnerPublicationPaths


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def source(tmp_path):
    package = tmp_path / "source" / "tinystudio"
    lab = package / "lab"
    lab.mkdir(parents=True)
    for path in (package / "__init__.py", package / "settings.py", lab / "__init__.py"):
        path.write_text("")
    for name in ("execution.py", "execution_outcome.py"):
        original = Path(module.__file__).with_name(name)
        if original.exists():
            (lab / name).write_bytes(original.read_bytes())
    (lab / "contracts.py").write_text('''import hashlib,json
def checksum(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
''')
    (lab / "service.py").write_text('def runtime_versions():\n    return {"fixture-runtime":"1"}\n')
    (lab / "engine.py").write_text('''import hashlib,json,os,signal,subprocess,sys,time
from pathlib import Path
class ExecutionCancelled(RuntimeError):
    pass
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def execute(spec,output,emit,cancelled):
    params=spec["definition"]["parameters"]
    partial=output/"discovery.json"
    partial.write_text(json.dumps({"status":"cancelled","models":[{"model_id":65001,"length":5}]}))
    emit({"event":"fixture_started"})
    if params.get("descendant"):
        child="import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
        process=subprocess.Popen([sys.executable,"-c",child])
        (output/"descendant.pid").write_text(str(process.pid))
    if params.get("mode") == "success":
        return {"records":1,"artifacts":[{"path":str(partial),"name":partial.name,"sha256":digest(partial)}]}
    if params.get("mode") in {"failed_evidence","ordinary_failure","arbitrary_attached_error"}:
        partial.write_text(json.dumps({"status":"failed","reason":"controlled initialization failure"}))
        retained={"records":0,"artifacts":[{"path":str(partial),"name":partial.name,"sha256":digest(partial),"role":"source_binding"}]}
        if params["mode"] == "failed_evidence":
            from .execution_outcome import RetainedExecutionError
            raise RetainedExecutionError("controlled initialization failure",evidence_result=retained)
        exc=RuntimeError("ordinary engine failure")
        if params["mode"] == "arbitrary_attached_error":
            exc.evidence_result=retained
        raise exc
    while not cancelled():
        time.sleep(.005)
    if params.get("mode") == "uncooperative":
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        time.sleep(30)
    if params.get("delay"):
        time.sleep(params["delay"])
    if params.get("mutate_input"):
        path=Path(next(iter(spec["input_paths"].values())));path.chmod(0o644);path.write_text("changed")
    if params.get("mutate_context"):
        path=output.parent.parent/"context.json";path.chmod(0o644);path.write_text("{}")
    if params.get("mutate_source"):
        path=Path(__file__);path.chmod(0o644);path.write_text("# changed")
    exc=ExecutionCancelled("fixture cancellation")
    if params.get("mode") != "no_evidence":
        exc.evidence_result={"records":1,"artifacts":[{"path":str(partial),"name":partial.name,"sha256":digest(partial),"role":"device_discovery"}],"metrics":{"discovery_status":"cancelled"}}
    raise exc
''')
    return package


def attempt(source, **parameters):
    files = {path.relative_to(source).as_posix(): sha(path.read_bytes()) for path in source.rglob("*.py")}
    spec = {"definition": {"engine": "synthetic.signal", "parameters": parameters, "inputs": [],
                           "execution": {"backend": "native", "profile": "trusted_native",
                                         "max_seconds": 5, "max_output_bytes": 1024 ** 2}},
            "implementation": {"version": 1, "files": files}, "runtime": {"fixture-runtime": "1"},
            "input_manifest": []}
    return {"run_id": "cancelled-audit-run", "attempt_id": "cancelled-evidence", "lease_token": "not-child-visible",
            "spec": spec, "spec_digest": checksum(spec)}


class Transport:
    def __init__(self, *, cancel=True, lose_on_upload=False, upload_delay=0, payload=b"fixture input",
                 cancel_on_upload=False):
        self.cancel = cancel
        self.lose_on_upload = lose_on_upload
        self.upload_delay = upload_delay
        self.started = False
        self.uploading = False
        self.upload_started = False
        self.finish = None
        self.uploads = []
        self.beats = 0
        self.payload = payload
        self.cancel_on_upload = cancel_on_upload

    @contextmanager
    def stream(self, *args, **kwargs):
        yield SimpleNamespace(raise_for_status=lambda: None, iter_bytes=lambda: iter([self.payload]))

    def get(self, path, **kwargs):
        return httpx.Response(404, request=httpx.Request("GET", "http://localhost" + path))

    def post(self, path, *, json=None, files=None, params=None, headers=None):
        if path.endswith("/heartbeat"):
            self.beats += 1
            self.started |= any(event.get("event") == "fixture_started" for event in json["events"])
            if self.lose_on_upload and self.uploading:
                raise httpx.ConnectError("controlled lost audit lease")
            body = {"cancel_requested": self.cancel and self.started or self.cancel_on_upload and self.uploading,
                    "lease_seconds": 30}
        elif path.endswith("/artifacts"):
            self.uploading = True
            self.upload_started = True
            try:
                body_bytes = bytearray()
                while True:
                    time.sleep(self.upload_delay)
                    block = files["file"][1].read(8)
                    if not block:
                        break
                    body_bytes.extend(block)
                self.uploads.append(bytes(body_bytes))
                body = {"id": "audit-" + str(len(self.uploads)), "name": files["file"][0],
                        "sha256": sha(body_bytes), "size": len(body_bytes),
                        "media_type": files["file"][2], "role": params.get("role", "evidence"),
                        "visibility": params.get("visibility", "model")}
            finally:
                self.uploading = False
        elif path.endswith("/finish"):
            self.finish = copy.deepcopy(json)
            body = {"accepted": True, "state": json["state"]}
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=body, request=httpx.Request("POST", "http://localhost" + path))


def run(source, tmp_path, monkeypatch, *, transport=None, claim=None, grace=.3):
    monkeypatch.setattr(module, "runtime_versions", lambda: {"fixture-runtime": "1"})
    transport = transport or Transport()
    root = tmp_path / "attempt"
    root.mkdir()
    Runner("http://localhost", "fixture", "token", client=transport, source_root=source,
           heartbeat_interval=.01, process_grace=grace).execute_attempt(claim or attempt(source), root)
    return transport, root


def test_actual_child_runner_preserves_explicit_cancelled_audit_result(source, tmp_path, monkeypatch):
    transport, root = run(source, tmp_path, monkeypatch, claim=attempt(source, delay=.04))
    assert transport.finish["state"] == "cancelled", transport.finish
    assert transport.finish["result"] is not None, transport.finish
    result = transport.finish["result"]
    assert result["records"] == 1 and len(result["artifacts"]) == 1
    assert json.loads(transport.uploads[0])["status"] == "cancelled"
    assert result["execution_outcome"] == {"format": "tinystudio.cancelled-evidence.v1", "state": "cancelled", "audit_only": True}
    receipt = json.loads((root / "work/runtime-receipt.json").read_text())
    local_result = json.loads((root / "work/result.json").read_text())
    assert receipt["result_digest"] == checksum(local_result)
    assert receipt["outcome"] == result["execution_outcome"]


@pytest.mark.parametrize("mode", ["uncooperative", "no_evidence"])
def test_forced_or_ordinary_cancellation_does_not_promote_partial_files(source, tmp_path, monkeypatch, mode):
    transport, root = run(source, tmp_path, monkeypatch, claim=attempt(source, mode=mode), grace=.08)
    assert transport.finish["state"] == "cancelled", transport.finish
    assert transport.finish["result"] is None and not transport.uploads
    assert (root / "work/evidence/discovery.json").exists()
    assert not (root / "work/result.json").exists()


def test_cancelled_descendants_are_gone_before_artifact_publication(source, tmp_path, monkeypatch):
    transport, root = run(source, tmp_path, monkeypatch, claim=attempt(source, descendant=True))
    assert transport.finish["state"] == "cancelled" and transport.finish["result"] is not None
    pid = int((root / "work/evidence/descendant.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("tamper", ["receipt", "result", "escape", "hash", "exit_status", "marker"])
def test_cancelled_evidence_must_match_exit_receipt_digest_and_output_bounds(source, tmp_path, monkeypatch, tamper):
    original = ProcessGroup.close
    changed = False
    def close(group):
        nonlocal changed
        original(group)
        result_path = tmp_path / "attempt/work/result.json"
        if changed or not result_path.exists():
            return
        changed = True
        receipt_path = result_path.with_name("runtime-receipt.json")
        receipt = json.loads(receipt_path.read_text())
        result = json.loads(result_path.read_text())
        if tamper == "receipt":
            receipt["context_digest"] = "f" * 64
        elif tamper == "result":
            result["records"] = 777
        elif tamper == "exit_status":
            # A successful child may not opt into audit publication by leaving
            # an otherwise consistent marker/receipt; exit 85 is also required.
            result["execution_outcome"] = {"format": "tinystudio.cancelled-evidence.v1", "state": "cancelled", "audit_only": True}
            receipt["outcome"] = result["execution_outcome"]
            receipt["result_digest"] = checksum(result)
        else:
            if tamper == "escape":
                result["artifacts"][0]["path"] = str(tmp_path / "outside.txt")
                (tmp_path / "outside.txt").write_text("outside audit")
            elif tamper == "hash":
                result["artifacts"][0]["sha256"] = "a" * 64
            elif tamper == "marker":
                result["execution_outcome"]["state"] = "succeeded"
            receipt["result_digest"] = checksum(result)
        receipt_path.write_text(json.dumps(receipt))
        result_path.write_text(json.dumps(result))
    monkeypatch.setattr(ProcessGroup, "close", close)
    transport, _ = run(source, tmp_path, monkeypatch,
                       transport=Transport(cancel=tamper != "exit_status"),
                       claim=attempt(source, **({"mode": "success"} if tamper == "exit_status" else {})))
    assert changed, "tamper check must reach an actual child receipt"
    assert transport.finish["result"] is None, transport.finish
    assert transport.finish["state"] != "succeeded"
    assert not transport.uploads


def test_lost_lease_during_cancelled_audit_upload_prevents_authoritative_finish(source, tmp_path, monkeypatch):
    transport = Transport(lose_on_upload=True, upload_delay=.025)
    with pytest.raises(PublicationUnconfirmed, match="publication is unconfirmed"):
        run(source, tmp_path, monkeypatch, transport=transport)
    assert transport.finish is None and not transport.uploads
    paths = RunnerPublicationPaths(tmp_path / "attempt")
    assert json.loads(json.loads(paths.cleanup.read_text())["payload_json"]) == {"confirmed": True}
    assert paths.execution.is_file() and not paths.finalization.is_file()
    assert (tmp_path / "attempt/work/evidence/discovery.json").is_file()


def test_normal_success_cancellation_race_does_not_publish_as_audit(source, tmp_path, monkeypatch):
    transport, _ = run(source, tmp_path, monkeypatch, claim=attempt(source, mode="success"))
    assert transport.finish["state"] == "cancelled", transport.finish
    assert transport.finish["result"] is None


@pytest.mark.parametrize("mutation", ["mutate_source", "mutate_context"])
def test_cancelled_evidence_rechecks_frozen_execution_identity(source, tmp_path, monkeypatch, mutation):
    transport, _ = run(source, tmp_path, monkeypatch, claim=attempt(source, **{mutation: True}))
    assert transport.finish["result"] is None and not transport.uploads
    assert transport.finish["state"] != "succeeded"


@pytest.mark.parametrize("mode,expected_status,expected_state", [(None, 85, "cancelled"), ("failed_evidence", 86, "failed")])
def test_child_bootstrap_exits_with_explicit_audit_status_and_bound_receipt(source, tmp_path, mode, expected_status, expected_state):
    claim = attempt(source, **({"mode": mode} if mode else {}))
    work = tmp_path / "child-work"
    work.mkdir()
    (work / "cancel").touch()
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(claim["spec"]))
    context = {"spec_digest": claim["spec_digest"], "source_root": str(source),
               "input_paths": {}, "profile_manifest": {"fixture": True}}
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context))
    process = subprocess.run([sys.executable, "-m", "tinystudio.lab.execution", str(spec_path),
                              str(work / "evidence"), str(context_path)],
                             env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source.parent),
                                  "PYTHONDONTWRITEBYTECODE": "1", "TINYSTUDIO_SKIP_DOTENV": "1"},
                             capture_output=True, text=True, timeout=5, cwd=work)
    assert process.returncode == expected_status, process.stderr
    result = json.loads((work / "result.json").read_text())
    receipt = json.loads((work / "runtime-receipt.json").read_text())
    assert receipt["result_digest"] == checksum(result)
    assert receipt["outcome"] == result["execution_outcome"]
    assert result["execution_outcome"]["state"] == expected_state


def test_cancelled_evidence_rechecks_downloaded_input_hash(source, tmp_path, monkeypatch):
    transport = Transport()
    claim = attempt(source, mutate_input=True)
    claim["spec"]["definition"]["inputs"] = [{"artifact_id": "input-one", "sha256": sha(transport.payload)}]
    claim["spec"]["input_manifest"] = [{"id": "input-one", "name": "input.txt", "size": len(transport.payload),
                                          "sha256": sha(transport.payload)}]
    claim["spec_digest"] = checksum(claim["spec"])
    transport, _ = run(source, tmp_path, monkeypatch, transport=transport, claim=claim)
    assert transport.finish["state"] == "failed" and transport.finish["result"] is None
    assert "frozen input" in transport.finish["error"] and not transport.uploads


def test_stopped_cancelled_audit_upload_uses_live_lease_after_execution_deadline(source, tmp_path, monkeypatch):
    original_close = ProcessGroup.close
    closed = []
    def close(group):
        original_close(group)
        assert group.process.poll() is not None
        if group not in closed:
            closed.append(group)
    monkeypatch.setattr(ProcessGroup, "close", close)
    monotonic = time.monotonic
    advance = [0]
    monkeypatch.setattr(module.time, "monotonic", lambda: monotonic() + advance[0])
    crossed = []
    class CrossDeadline(Transport):
        def post(self, path, **kwargs):
            if path.endswith("/artifacts"):
                assert len(closed) == 1 and closed[0].process.poll() is not None
                filename, guarded, media_type = kwargs["files"]["file"]
                class DelayedRead:
                    def read(self, size=-1):
                        block = guarded.read(size)
                        if block and not crossed:
                            crossed.append(True)
                            # Cross the selected one-second execution deadline
                            # only after the real child stopped and upload began.
                            # The fixture's finite 30-second Lab lease stays live.
                            advance[0] = 2
                        return block
                kwargs["files"] = {"file": (filename, DelayedRead(), media_type)}
            return super().post(path, **kwargs)
    transport = CrossDeadline(upload_delay=.025)
    claim = attempt(source)
    claim["spec"]["definition"]["execution"]["max_seconds"] = 1
    claim["spec_digest"] = checksum(claim["spec"])
    transport, root = run(source, tmp_path, monkeypatch, transport=transport, claim=claim)
    assert crossed and len(closed) == 1 and transport.upload_started
    assert transport.finish["state"] == "cancelled" and transport.finish["result"] is not None
    assert len(transport.uploads) == 1 and json.loads(transport.uploads[0])["status"] == "cancelled"
    result = transport.finish["result"]
    assert result["execution_outcome"] == {"format": "tinystudio.cancelled-evidence.v1", "state": "cancelled", "audit_only": True}
    assert result["execution_receipt"]["result_digest"] == checksum(json.loads((root / "work/result.json").read_text()))
    assert "hardware_session" not in json.loads((root / "context.json").read_text())


def test_cancelled_audit_still_observes_output_budget(source, tmp_path, monkeypatch):
    claim = attempt(source)
    claim["spec"]["definition"]["execution"]["max_output_bytes"] = 32
    claim["spec_digest"] = checksum(claim["spec"])
    transport, _ = run(source, tmp_path, monkeypatch, claim=claim)
    assert transport.finish["state"] == "failed" and transport.finish["result"] is None and not transport.uploads
    assert "output_bytes" in transport.finish["error"] and "budget" in transport.finish["error"]


def test_cancellation_exception_only_qualifies_explicit_attached_result():
    from tinystudio.lab.engine import ExecutionCancelled
    assert ExecutionCancelled().evidence_result is None
    payload = {"artifacts": []}
    assert ExecutionCancelled("audit", evidence_result=payload).evidence_result is payload


def test_cancelled_audit_preserves_explicit_unbounded_output_budget(source, tmp_path, monkeypatch):
    claim = attempt(source)
    claim["spec"]["definition"]["execution"]["max_output_bytes"] = None
    claim["spec_digest"] = checksum(claim["spec"])
    transport, _ = run(source, tmp_path, monkeypatch, claim=claim)
    assert transport.finish["state"] == "cancelled" and transport.finish["result"] is not None
    assert len(transport.uploads) == 1 and json.loads(transport.uploads[0])["status"] == "cancelled"
    assert transport.finish["result"]["execution_outcome"] == {"format": "tinystudio.cancelled-evidence.v1", "state": "cancelled", "audit_only": True}
    assert claim["spec"]["definition"]["execution"]["max_output_bytes"] is None
    assert checksum(claim["spec"]) == claim["spec_digest"]


def test_cancelled_audit_rechecks_parent_runtime_identity(source, tmp_path, monkeypatch):
    original = ProcessGroup.close
    def close(group):
        original(group)
        monkeypatch.setattr(module, "runtime_versions", lambda: {"fixture-runtime": "changed"})
    monkeypatch.setattr(ProcessGroup, "close", close)
    transport, _ = run(source, tmp_path, monkeypatch)
    assert transport.finish["state"] == "failed" and transport.finish["result"] is None
    assert "identity receipt mismatch" in transport.finish["error"] and not transport.uploads


def test_explicit_failed_engine_result_has_verified_failure_receipt(source, tmp_path, monkeypatch):
    transport, root = run(source, tmp_path, monkeypatch, transport=Transport(cancel=False),
                          claim=attempt(source, mode="failed_evidence"))
    assert transport.finish["state"] == "failed", transport.finish
    result = transport.finish["result"]
    assert result is not None and len(result["artifacts"]) == 1
    assert "controlled initialization failure" in transport.finish["error"]
    assert result["execution_outcome"] == {"format": "tinystudio.failed-evidence.v1", "state": "failed", "audit_only": True}
    assert json.loads(transport.uploads[0])["status"] == "failed"
    receipt = json.loads((root / "work/runtime-receipt.json").read_text())
    assert receipt["outcome"] == result["execution_outcome"]
    assert receipt["result_digest"] == checksum(json.loads((root / "work/result.json").read_text()))


@pytest.mark.parametrize("mode", ["ordinary_failure", "arbitrary_attached_error"])
def test_failed_engine_files_or_arbitrary_exception_attributes_are_ineligible(source, tmp_path, monkeypatch, mode):
    transport, root = run(source, tmp_path, monkeypatch, transport=Transport(cancel=False), claim=attempt(source, mode=mode))
    assert transport.finish["state"] == "failed" and transport.finish["result"] is None
    assert not transport.uploads and (root / "work/evidence/discovery.json").exists()
    assert not (root / "work/result.json").exists()


@pytest.mark.parametrize("tamper", ["cancelled_marker", "successful_exit"])
def test_failed_evidence_cannot_forge_a_different_child_outcome(source, tmp_path, monkeypatch, tamper):
    original = ProcessGroup.close
    changed = False
    def close(group):
        nonlocal changed
        original(group)
        path = tmp_path / "attempt/work/result.json"
        if changed or not path.exists():
            return
        changed = True
        result = json.loads(path.read_text())
        receipt_path = path.with_name("runtime-receipt.json")
        receipt = json.loads(receipt_path.read_text())
        result["execution_outcome"] = {"format": "tinystudio.cancelled-evidence.v1" if tamper == "cancelled_marker"
                                        else "tinystudio.failed-evidence.v1",
                                        "state": "cancelled" if tamper == "cancelled_marker" else "failed", "audit_only": True}
        result["execution_error"] = "forged failure"
        receipt.update(outcome=result["execution_outcome"], result_digest=checksum(result))
        path.write_text(json.dumps(result))
        receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(ProcessGroup, "close", close)
    transport, _ = run(source, tmp_path, monkeypatch, transport=Transport(cancel=False),
                       claim=attempt(source, mode="success" if tamper == "successful_exit" else "failed_evidence"))
    assert changed and transport.finish["result"] is None and not transport.uploads
    assert transport.finish["state"] == "failed"


def test_cancellation_during_failed_audit_retains_actual_failure_evidence(source, tmp_path, monkeypatch):
    transport = Transport(cancel=False, cancel_on_upload=True, upload_delay=.025)
    transport, _ = run(source, tmp_path, monkeypatch, transport=transport,
                       claim=attempt(source, mode="failed_evidence"))
    assert transport.finish["state"] == "cancelled", transport.finish
    result = transport.finish["result"]
    assert result is not None and len(transport.uploads) == 1
    assert result["execution_outcome"]["state"] == "failed"
    assert "controlled initialization failure" in transport.finish["error"]


def test_late_cleanup_uncertainty_remains_failed_even_after_cancelled_audit_upload(source, tmp_path, monkeypatch):
    original = ProcessGroup.close
    closes = 0
    def close(group):
        nonlocal closes
        original(group)
        closes += 1
        if closes > 1:
            raise RuntimeError("controlled late cleanup uncertainty")
    monkeypatch.setattr(ProcessGroup, "close", close)
    transport, _ = run(source, tmp_path, monkeypatch)
    assert transport.finish["state"] == "failed", transport.finish
    assert transport.finish["result"] is None
    assert "cleanup unconfirmed" in transport.finish["error"]
