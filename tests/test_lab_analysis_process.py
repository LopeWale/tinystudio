"""Owned real children and forced POSIX branches; no stores or macOS claim."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from tinystudio.lab import analysis_process
from tests.test_lab_runner import source  # noqa: F401 -- shared minimal snapshot fixture


def _ready(path):
    deadline = time.monotonic() + 5
    while not path.exists():
        assert time.monotonic() < deadline, "owned custom child did not become ready"
        time.sleep(.01)


@pytest.mark.skipif(os.name != "posix", reason="POSIX inherited attempt group")
@pytest.mark.parametrize("ignore_term", [False, True])
def test_direct_child_inherits_attempt_and_close_only_stops_exact_leader(tmp_path, monkeypatch, ignore_term):
    monkeypatch.setattr(analysis_process, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setenv("TINYSTUDIO_PROCESS_POLL_SECONDS", ".1")
    def reject_group_signal(*args):
        pytest.fail("custom child cleanup attempted to signal an inherited process group")
    monkeypatch.setattr(analysis_process.os, "killpg", reject_group_signal)
    ready = tmp_path / "ready"
    code = ("import os,pathlib,signal,time; "
            + ("signal.signal(signal.SIGTERM,signal.SIG_IGN); " if ignore_term else "")
            + f"pathlib.Path({str(ready)!r}).write_text(str(os.getpgrp())); time.sleep(30)")
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    child = None
    try:
        child = analysis_process.AnalysisProcess([sys.executable, "-c", code], grace=0)
        _ready(ready)
        assert int(ready.read_text()) == os.getpgrp()
        child.close()
        child.close()
        assert child.closed
        assert child.process.returncode == -(signal.SIGKILL if ignore_term else signal.SIGTERM)
        assert unrelated.poll() is None
        with pytest.raises(ChildProcessError):
            os.waitpid(child.process.pid, os.WNOHANG)
    finally:
        try:
            if child is not None:
                try:
                    child.close()
                finally:
                    if child.process.poll() is None:
                        child.process.kill()
                    child.process.wait(timeout=5)
        finally:
            unrelated.kill()
            unrelated.wait(timeout=5)


@pytest.mark.parametrize("grace", [-1, float("inf"), float("nan")])
def test_custom_process_rejects_unbounded_or_negative_termination_grace(grace):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        analysis_process.AnalysisProcess([sys.executable, "-c", "pass"], grace=grace)


@pytest.mark.skipif(os.name != "posix", reason="POSIX inherited attempt group")
def test_direct_completed_child_preserves_exit_status_environment_and_streams(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis_process, "sys", SimpleNamespace(platform="darwin"))
    code = "import os,sys; print(os.environ['ANALYSIS_VALUE']); print('diagnostic',file=sys.stderr); raise SystemExit(7)"
    child = analysis_process.AnalysisProcess([sys.executable, "-c", code], cwd=tmp_path,
        env={"ANALYSIS_VALUE": "retained"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert child.process.wait(timeout=5) == 7
        child.close()
        assert child.closed
        assert child.process.stdout.read() == b"retained\n"
        assert child.process.stderr.read() == b"diagnostic\n"
    finally:
        child.close()
        child.process.stdout.close()
        child.process.stderr.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux NativeSupervisor delegation")
def test_linux_custom_child_keeps_existing_descendant_supervisor():
    from tinystudio.lab.native_process import NativeSupervisor

    child = analysis_process.AnalysisProcess([sys.executable, "-c", "raise SystemExit(7)"], grace=.15)
    try:
        assert isinstance(child.process, NativeSupervisor)
        assert child.process.wait(timeout=5) == 7
        child.close()
        assert child.closed
    finally:
        child.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Isolated Linux subreaper tests forced non-Linux POSIX behavior")
@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_outer_runner_stops_custom_child_on_forced_nonlinux_posix(tmp_path, source, stop):
    # The framework child is deliberately uncooperative: only the outer
    # runner's group SIGKILL can stop it after its engine is killed. A private
    # harness subreaper collects the exact orphan PID instead of relying on PID1.
    (source / "config.py").write_bytes(Path(analysis_process.__file__).parents[1].joinpath("config.py").read_bytes())
    (source / "lab" / "analysis_process.py").write_bytes(Path(analysis_process.__file__).read_bytes())
    (source / "lab" / "engine.py").write_text('''import json, os, signal, subprocess, sys, time, types
from . import analysis_process
def execute(spec, output, emit, cancelled):
    analysis_process.sys = types.SimpleNamespace(platform="darwin")
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    path = spec["definition"]["parameters"]["ready"]
    code = "import json,os,pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path("+repr(path)+").write_text(json.dumps({'pid':os.getpid(),'parent':os.getppid(),'group':os.getpgrp(),'session':os.getsid(0)})); time.sleep(30)"
    child = analysis_process.AnalysisProcess([sys.executable, "-c", code], grace=.1,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while True: time.sleep(.01)
''')
    harness = r'''
import ctypes, json, os, pathlib, signal, subprocess, sys, threading, time, types
from tinystudio.lab import runner as module
from tinystudio.lab.contracts import checksum
from tests.test_lab_runner import Transport, attempt
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
source, root, stop = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
root.mkdir()
ready = root / "custom-ready"
claimed = attempt(source)
claimed["spec"]["definition"]["parameters"]["ready"] = str(ready)
claimed["spec"]["definition"]["execution"]["max_seconds"] = 1 if stop == "timeout" else 10
claimed["spec_digest"] = checksum(claimed["spec"])
module.runtime_versions = lambda: {"fixture-runtime": "1"}
module.sys = types.SimpleNamespace(platform="darwin", executable=sys.executable)
class Controller(Transport):
    def post(self, path, **kwargs):
        if path.endswith("/heartbeat") and stop == "cancel" and ready.exists():
            import httpx
            return httpx.Response(200, json={"cancel_requested": True}, request=httpx.Request("POST", "http://localhost"+path))
        return super().post(path, **kwargs)
transport = Controller()
agent = module.Runner("http://localhost", "fixture", "token", client=transport,
    source_root=source, heartbeat_interval=.01, process_grace=.15)
reaped = []
finished = threading.Event()
def collect_custom():
    while not finished.is_set():
        if ready.exists():
            try:
                pid = json.loads(ready.read_text())["pid"]
                waited, status = os.waitpid(pid, os.WNOHANG)
                if waited:
                    reaped.append(os.waitstatus_to_exitcode(status))
                    return
            except (ChildProcessError, json.JSONDecodeError): pass
        time.sleep(.005)
collector = threading.Thread(target=collect_custom, daemon=True)
unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
try:
    collector.start()
    agent.execute_attempt(claimed, root)
    collector.join(timeout=2)
    assert ready.exists(), transport.finish
    identity = json.loads(ready.read_text())
    assert identity["group"] == identity["parent"] == identity["session"], identity
    assert reaped == [-signal.SIGKILL], (reaped, transport.finish)
    assert transport.finish["state"] == ("cancelled" if stop == "cancel" else "failed"), transport.finish
    if stop == "timeout": assert "wall-time budget" in transport.finish["error"], transport.finish
    assert not transport.uploads
    assert unrelated.poll() is None
    try: os.kill(identity["pid"], 0)
    except ProcessLookupError: pass
    else: raise AssertionError("custom process survived outer runner teardown")
    print("outer "+stop+" stopped and reaped inherited custom child")
finally:
    finished.set()
    collector.join(timeout=2)
    if ready.exists() and not reaped:
        pid = json.loads(ready.read_text())["pid"]
        try: os.kill(pid, signal.SIGKILL)
        except ProcessLookupError: pass
        try: os.waitpid(pid, 0)
        except ChildProcessError: pass
    unrelated.kill()
    unrelated.wait(timeout=5)
'''
    result = subprocess.run([sys.executable, "-c", harness, str(source), str(tmp_path / "attempt"), stop],
        capture_output=True, text=True, timeout=18)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"outer {stop} stopped and reaped inherited custom child"
