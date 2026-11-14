"""Real owned subprocesses; no stores, hardware, or process-wide test reaping changes."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

import pytest

from tinystudio.lab.runner import ProcessGroup


def await_file(path, timeout=5):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"owned child did not become ready: {path}")
        time.sleep(.01)


def kill_owned(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@pytest.mark.skipif(sys.platform != "linux", reason="Linux child-subreaper lifecycle")
def test_escaped_descendant_is_reaped_before_cleanup_is_confirmed(tmp_path):
    pidfile = tmp_path / "pid"
    ready = tmp_path / "ready"
    child = f"import pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path({str(ready)!r}).touch(); time.sleep(30)"
    leader = f"import subprocess,sys,pathlib,time; p=subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True); pathlib.Path({str(pidfile)!r}).write_text(str(p.pid));\nwhile not pathlib.Path({str(ready)!r}).exists(): time.sleep(.01)"
    group = ProcessGroup([sys.executable, "-c", leader], grace=.15,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    descendant = None
    try:
        group.process.wait(timeout=5)
        descendant = int(pidfile.read_text())
        os.kill(descendant, 0)
        group.close()
        assert group.closed
        with pytest.raises(ProcessLookupError):
            os.kill(descendant, 0)
    finally:
        if descendant is not None:
            kill_owned(descendant)
        group.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux child-subreaper lifecycle")
def test_attempt_does_not_change_callers_child_reaping_mode():
    def mode():
        value = ctypes.c_int()
        assert ctypes.CDLL(None, use_errno=True).prctl(37, ctypes.byref(value), 0, 0, 0) == 0
        return value.value
    before = mode()
    group = ProcessGroup([sys.executable, "-c", "pass"], grace=.15)
    try:
        assert group.process.wait(timeout=5) == 0
        group.close()
        assert mode() == before
    finally:
        group.close()


@pytest.mark.parametrize("exit_code", [0, 7])
def test_engine_exit_status_environment_and_streams_are_preserved(tmp_path, exit_code):
    output = tmp_path / "stdout"
    error = tmp_path / "stderr"
    code = "import json,os,sys; print(json.dumps([os.getcwd(),os.environ['FIXTURE_VALUE'],sys.argv[1]]),flush=True); print('engine diagnostic',file=sys.stderr,flush=True); sys.exit(int(sys.argv[2]))"
    with output.open("wb") as stdout, error.open("wb") as stderr:
        group = ProcessGroup([sys.executable, "-c", code, "value with spaces", str(exit_code)],
                             grace=.15, env={"FIXTURE_VALUE": "retained-value"}, cwd=tmp_path,
                             stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        try:
            assert group.process.wait(timeout=5) == exit_code
            assert group.process.poll() == exit_code
            group.close()
        finally:
            group.close()
    assert json.loads(output.read_text()) == [str(tmp_path), "retained-value", "value with spaces"]
    assert error.read_text() == "engine diagnostic\n"


def test_cleanup_leaves_unrelated_process_and_another_attempt_running(tmp_path):
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    other = ProcessGroup([sys.executable, "-c", "import time; time.sleep(30)"], grace=.15)
    selected = ProcessGroup([sys.executable, "-c", "import time; time.sleep(30)"], grace=.15)
    try:
        selected.close()
        assert selected.closed
        assert unrelated.poll() is None
        assert other.process.poll() is None
    finally:
        selected.close()
        other.close()
        unrelated.kill()
        unrelated.wait(timeout=5)


def test_wait_timeout_does_not_lose_execution_ownership():
    group = ProcessGroup([sys.executable, "-c", "import time; time.sleep(30)"], grace=.15)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            group.process.wait(timeout=.02)
        assert group.process.poll() is None
        group.close()
        assert group.closed
        assert group.process.poll() == -signal.SIGTERM
    finally:
        group.close()


def test_missing_engine_reports_the_original_startup_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        ProcessGroup([str(tmp_path / "missing-engine")], grace=.15,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_declared_inherited_descriptor_reaches_only_the_engine():
    read_fd, write_fd = os.pipe()
    group = None
    try:
        command = [sys.executable, "-c", "import os,sys; os.write(int(sys.argv[1]),b'engine-output')", str(write_fd)]
        group = ProcessGroup(command, grace=.15, pass_fds=(write_fd,),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert group.process.wait(timeout=5) == 0
        group.close()
        assert os.read(read_fd, 13) == b"engine-output"
    finally:
        if group is not None:
            group.close()
        os.close(read_fd)
        os.close(write_fd)


def test_inherited_pipe_reports_eof_when_engine_exits_before_group_close():
    read_fd, write_fd = os.pipe()
    group = None
    try:
        command = [sys.executable, "-c", "import os,sys; os.write(int(sys.argv[1]),b'done')", str(write_fd)]
        group = ProcessGroup(command, grace=.15, pass_fds=(write_fd,),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(write_fd)
        write_fd = None
        assert group.process.wait(timeout=5) == 0
        assert os.read(read_fd, 4) == b"done"
        assert select.select([read_fd], [], [], .2)[0], "supervisor retained an engine-only pipe writer"
        assert os.read(read_fd, 1) == b""
    finally:
        if group is not None:
            group.close()
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


def test_engine_standard_pipes_preserve_data_and_eof_before_cleanup():
    command = [sys.executable, "-c", "import sys; print(sys.stdin.readline().strip(),flush=True); print('diagnostic',file=sys.stderr,flush=True)"]
    group = ProcessGroup(command, grace=.15, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        group.process.stdin.write(b"request\n")
        group.process.stdin.close()
        assert group.process.wait(timeout=5) == 0
        for stream, expected in ((group.process.stdout, b"request\n"), (group.process.stderr, b"diagnostic\n")):
            assert os.read(stream.fileno(), len(expected)) == expected
            assert select.select([stream], [], [], .2)[0], "supervisor retained an engine standard stream"
            assert os.read(stream.fileno(), 1) == b""
    finally:
        group.close()
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(group.process, name, None)
            if stream is not None:
                stream.close()


def test_invalid_inherited_descriptor_is_rejected_before_private_socket_allocation(tmp_path):
    descriptor = os.open(os.devnull, os.O_RDONLY)
    os.close(descriptor)
    group = None
    try:
        with pytest.raises(OSError):
            group = ProcessGroup([sys.executable, "-c", "pass"], grace=.15, pass_fds=(descriptor,))
    finally:
        if group is not None:
            group.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux private supervisor descriptor handling")
def test_native_attempt_starts_when_controller_standard_descriptors_are_closed(tmp_path):
    result_file = tmp_path / "result"
    code = r'''
import os, pathlib, subprocess, sys
from tinystudio.lab.runner import ProcessGroup
for fd in (0, 1, 2): os.close(fd)
group = ProcessGroup([sys.executable, '-c', 'raise SystemExit(7)'], grace=.15,
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    code = group.process.wait(timeout=5)
    group.close()
    pathlib.Path(sys.argv[1]).write_text(str(code))
finally:
    group.close()
'''
    result = subprocess.run([sys.executable, "-c", code, str(result_file)], capture_output=True, text=True, timeout=12)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result_file.read_text() == "7"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux supervisor fault cleanup")
def test_lost_supervisor_does_not_prevent_sigkill_escalation(tmp_path):
    harness = r'''
import ctypes, os, pathlib, signal, subprocess, sys, time
from tinystudio.lab.runner import ProcessGroup
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
ready = pathlib.Path(sys.argv[1])
code = f'import pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path({str(ready)!r}).touch(); time.sleep(30)'
group = ProcessGroup([sys.executable, '-c', code], grace=.15)
pid = group.process.pid
reaped = False
try:
    deadline = time.monotonic() + 5
    while not ready.exists():
        assert time.monotonic() < deadline
        time.sleep(.01)
    group.process.owner.kill()
    group.process.owner.wait(timeout=5)
    try: group.close()
    except RuntimeError: pass
    else: raise AssertionError('lost supervisor cleanup was falsely qualified')
    assert not group.closed
    status = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
    assert status is not None, 'engine survived failed cleanup without SIGKILL escalation'
    waited, code = os.waitpid(pid, 0)
    reaped = True
    assert waited == pid and os.waitstatus_to_exitcode(code) == -signal.SIGKILL
finally:
    if not reaped:
        try: os.kill(pid, signal.SIGKILL)
        except ProcessLookupError: pass
        try: os.waitpid(pid, 0)
        except ChildProcessError: pass
'''
    result = subprocess.run([sys.executable, "-c", harness, str(tmp_path / "ready")], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="Linux supervisor descriptor capacity")
def test_native_attempt_supports_control_descriptors_above_select_limit():
    code = r'''
import ctypes, os, resource, subprocess, sys, time
from tinystudio.lab.runner import ProcessGroup
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
if resource.getrlimit(resource.RLIMIT_NOFILE)[0] < 1100: raise SystemExit(77)
fds = []
group = None
try:
    for _ in range(1030): fds.append(os.open(os.devnull, os.O_RDONLY))
    group = ProcessGroup([sys.executable, '-c', 'pass'], grace=.15,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert group.process.wait(timeout=5) == 0
    group.close()
finally:
    if group is not None: group.close()
    for fd in fds: os.close(fd)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
            if not pid: time.sleep(.01)
        except ChildProcessError: break
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=15)
    if result.returncode == 77:
        pytest.skip("host descriptor limit cannot exercise a descriptor above 1024")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="Linux supervisor protocol fault")
def test_supervisor_error_still_reaps_helper_and_cannot_be_retried_into_success():
    group = ProcessGroup([sys.executable, "-c", "pass"], grace=.15,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert group.process.wait(timeout=5) == 0
        group.process._socket.sendall(b"invalid-control")
        with pytest.raises(RuntimeError):
            group.close()
        assert group.process.owner.returncode == 1, "failed helper was not waited/reaped"
        assert not group.closed
        with pytest.raises(RuntimeError):
            group.close()
        assert not group.closed
    finally:
        group.process.owner.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux orphan-adoption lifecycle")
def test_controller_loss_stops_and_reaps_the_owned_attempt(tmp_path):
    # The outer test subprocess is a temporary subreaper only to collect the
    # now-orphaned supervisor itself. It never changes pytest's process state.
    harness = r'''
import ctypes, json, os, pathlib, signal, subprocess, sys, time
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
root = pathlib.Path(sys.argv[1])
child_ready = root / 'child-ready'
pidfile = root / 'child-pid'
controller_ready = root / 'controller-ready'
child = f'import pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path({str(child_ready)!r}).touch(); time.sleep(30)'
leader = f'import pathlib,subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c",{child!r}]); pathlib.Path({str(pidfile)!r}).write_text(str(p.pid));\nwhile not pathlib.Path({str(child_ready)!r}).exists(): time.sleep(.01)'
controller = f'from tinystudio.lab.runner import ProcessGroup; import pathlib,sys,time; g=ProcessGroup([sys.executable,"-c",{leader!r}],grace=.15); g.process.wait(timeout=5); pathlib.Path({str(controller_ready)!r}).touch(); time.sleep(30)'
parent = subprocess.Popen([sys.executable, '-c', controller])
descendant = None
try:
    deadline = time.monotonic() + 8
    while not controller_ready.exists():
        assert parent.poll() is None, 'controller exited before readiness'
        assert time.monotonic() < deadline, 'controller readiness timed out'
        time.sleep(.01)
    descendant = int(pidfile.read_text())
    os.kill(descendant, 0)
    parent.kill()
    assert parent.wait(timeout=5) == -signal.SIGKILL
    adopted = []
    deadline = time.monotonic() + 5
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid:
            adopted.append(os.waitstatus_to_exitcode(status))
        else:
            assert time.monotonic() < deadline, 'owned process survived controller loss'
            time.sleep(.01)
    assert adopted and all(code == 0 for code in adopted), adopted
    try:
        os.kill(descendant, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError('owned descendant was not reaped')
    print('controller loss cleanup confirmed')
finally:
    if parent.poll() is None:
        parent.kill()
    parent.wait(timeout=5)
    if descendant is not None:
        try: os.kill(descendant, signal.SIGKILL)
        except ProcessLookupError: pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
            if not pid: time.sleep(.01)
        except ChildProcessError:
            break
'''
    result = subprocess.run([sys.executable, "-c", harness, str(tmp_path)], capture_output=True,
                            text=True, timeout=22)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "controller loss cleanup confirmed"
