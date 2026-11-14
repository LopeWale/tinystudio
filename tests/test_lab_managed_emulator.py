"""Owned Python processes only; readiness does not qualify a physical device."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

from tinystudio.lab.attempt_control import AttemptAuthorityLost, AttemptControl, AttemptIdentity
from tinystudio.lab.evidence_io import RecordWritePolicy
from tinystudio.lab.managed_process_configuration import ManagedProcessPaths, ManagedProcessSelection, ReadinessSelection
from tinystudio.lab.managed_emulator import ManagedEmulatorError, ManagedEmulatorProcess


def control():
    result = AttemptControl(AttemptIdentity('http://127.0.0.1', 'run', 'attempt', 'target', 'a' * 64))
    result.renew(30, sent_at=time.monotonic())
    return result


def selection(tmp_path, code='import time; time.sleep(30)', **kwargs):
    readiness = kwargs.pop('readiness', ReadinessSelection('process_running'))
    return ManagedProcessSelection((sys.executable, '-c', code), tmp_path, readiness,
                                   shutdown_grace_seconds=.03, **kwargs)


def process(tmp_path, code='import time; time.sleep(30)', **kwargs):
    return ManagedEmulatorProcess(selection(tmp_path, code, **kwargs), control(), ManagedProcessPaths(tmp_path / 'work'),
                                  evidence_policy=RecordWritePolicy(fsync=False))


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'owned process observation did not arrive'
        time.sleep(.005)


def test_constructor_performs_no_io_clock_or_launch(tmp_path):
    class ForbiddenClock:
        def __getattr__(self, name):
            raise AssertionError('constructor consulted clock')
    paths = ManagedProcessPaths(tmp_path / 'absent')
    owned = ManagedEmulatorProcess(selection(tmp_path), control(), paths, clock=ForbiddenClock(),
                                   process_factory=lambda *a, **k: pytest.fail('constructor launched'))
    assert not paths.work.exists()
    assert owned.snapshot()['state'] == 'created'


def test_real_pid_binary_streams_and_private_default(tmp_path):
    code = "import os,time; os.write(1,str(os.getpid()).encode()+b'\\x00\\xff'); os.write(2,b'err\\x00\\xfe'); time.sleep(30)"
    owned = process(tmp_path, code)
    try:
        started = owned.start()
        assert owned.wait_ready()['ready']
        until(lambda: owned.snapshot()['logs']['stderr']['observed_bytes'] == 5)
    finally:
        closed = owned.close('completed')
    assert closed['cleanup_confirmed']
    assert owned.paths.private_log('stdout').read_bytes() == str(started['pid']).encode() + b'\0\xff'
    assert owned.paths.private_log('stderr').read_bytes() == b'err\0\xfe'
    assert owned.paths.private_directory.stat().st_mode & 0o777 == 0o700
    artifacts = owned.retain_artifacts(tmp_path / 'retained')
    assert {a['role'] for a in artifacts} == {'managed_emulator_parent'}
    assert not any(a['media_type'] == 'application/octet-stream' for a in artifacts)
    manifest = json.loads(owned.paths.manifest.read_text())
    assert manifest['pid_role'] == 'owned_main_process'
    assert manifest['executable_identity']['scope'] == 'observed_file_not_execution_attestation'


@pytest.mark.parametrize('budget', [None, 0, 7])
def test_log_budget_is_per_stream_and_keeps_draining_and_matching(tmp_path, budget):
    payload = b'prefix\0\xffREADYtail' * 10000
    code = f"import os,time; data={payload[:18]!r}*10000; os.write(1,data); os.write(2,data); time.sleep(30)"
    expected = payload[:18] * 10000
    owned = process(tmp_path, code, max_log_bytes=budget, raw_log_visibility='truth',
                    readiness=ReadinessSelection('log_bytes', stream='stdout', marker=b'READY'))
    try:
        owned.start()
        assert owned.wait_ready()['ready']
        until(lambda: owned.snapshot()['logs']['stderr']['observed_bytes'] == len(expected))
    finally:
        owned.close('completed')
    snapshot = owned.snapshot()
    assert snapshot['raw_log_policy']['max_log_bytes_scope'] == 'per_stream'
    for stream in ('stdout', 'stderr'):
        retained = expected if budget is None else expected[:budget]
        receipt = snapshot['logs'][stream]
        assert receipt['observed_bytes'] == len(expected)
        assert receipt['observed_sha256'] == hashlib.sha256(expected).hexdigest()
        assert receipt['captured_bytes'] == len(retained)
        assert receipt['captured_sha256'] == hashlib.sha256(retained).hexdigest()
        assert receipt['capture_truncated'] == (retained != expected)
        assert receipt['observation_complete']
        assert owned.paths.private_log(stream).read_bytes() == retained
    artifacts = owned.retain_artifacts(tmp_path / 'retained')
    raw = [a for a in artifacts if a['role'] == 'managed_emulator_raw_log']
    assert len(raw) == 2 and all(a['visibility'] == 'truth' for a in raw)
    assert sorted(Path(a['path']).read_bytes() for a in raw) == [retained, retained]


def test_environment_is_explicit_overlay_and_public_metadata_omits_values(tmp_path, monkeypatch):
    monkeypatch.setenv('AMBIENT_APP_TOKEN', 'ambient-secret')
    values = {'BASE': 'base-private', 'OVERLAP': 'base'}
    plan = selection(tmp_path, "import os,json,time; print(json.dumps(dict(os.environ)),flush=True); time.sleep(30)",
                     public_env={'PUBLIC': 'public-value', 'OVERLAP': 'public'})
    owned = ManagedEmulatorProcess(plan, control(), ManagedProcessPaths(tmp_path / 'work'), base_env=values,
        runtime_secrets={'environment': {'SECRET': 'private-secret', 'OVERLAP': 'private-wins'}},
        evidence_policy=RecordWritePolicy(fsync=False))
    try:
        owned.start()
        owned.wait_ready()
        until(lambda: owned.snapshot()['logs']['stdout']['observed_bytes'] > 0)
    finally:
        owned.close('completed')
    actual = json.loads(owned.paths.private_log('stdout').read_bytes())
    assert actual['BASE'] == 'base-private' and actual['PUBLIC'] == 'public-value'
    assert actual['SECRET'] == 'private-secret' and actual['OVERLAP'] == 'private-wins'
    assert 'AMBIENT_APP_TOKEN' not in actual
    public = json.dumps(owned.snapshot()) + owned.paths.manifest.read_text() + owned.paths.lifecycle.read_text()
    assert all(value not in public for value in ['ambient-secret', 'base-private', 'private-secret', 'private-wins'])
    assert owned.snapshot()['environment']['precedence'] == ['base_env', 'public_env', 'runtime_secrets.environment']


def test_revoked_before_launch_and_after_owned_launch_are_both_fenced(tmp_path):
    authority = control()
    authority.revoke('server_cancelled')
    owned = ManagedEmulatorProcess(selection(tmp_path), authority, ManagedProcessPaths(tmp_path / 'before'),
                                   process_factory=lambda *a, **k: pytest.fail('revoked process launched'))
    with pytest.raises(AttemptAuthorityLost):
        owned.start()
    assert not owned.paths.work.exists()
    from tinystudio.lab.runner import ProcessGroup
    authority = control()
    groups = []
    def launch(*args, **kwargs):
        groups.append(ProcessGroup(*args, **kwargs))
        authority.revoke('server_cancelled')
        return groups[-1]
    owned = ManagedEmulatorProcess(selection(tmp_path), authority, ManagedProcessPaths(tmp_path / 'after'), process_factory=launch)
    with pytest.raises(AttemptAuthorityLost):
        owned.start()
    assert groups[0].closed and owned.snapshot()['cleanup_confirmed']


def test_startup_zero_local_process_observation_and_no_tcp_probe(tmp_path):
    owned = process(tmp_path / 'local', startup_timeout_seconds=0)
    owned.selection.cwd.mkdir(parents=True)
    try:
        owned.start()
        assert owned.wait_ready()['ready']
    finally:
        owned.close('completed')
    owned = process(tmp_path, startup_timeout_seconds=0, readiness=ReadinessSelection('tcp', host='localhost', port=1))
    try:
        owned.start()
        with pytest.raises(ManagedEmulatorError, match='readiness'):
            owned.wait_ready()
        assert owned.snapshot()['readiness']['status'] == 'not_observed'
        assert owned.snapshot()['helpers'] == []
    finally:
        owned.close('startup_failed')


def test_actual_hostname_tcp_probe_is_reachability_only(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    code = f"import socket,time; s=socket.socket(); s.bind(('127.0.0.1',{port})); s.listen(); time.sleep(30)"
    owned = process(tmp_path, code, startup_timeout_seconds=3, readiness=ReadinessSelection('tcp', host='localhost', port=port))
    try:
        owned.start()
        ready = owned.wait_ready()
        assert ready['ready'] and ready['scope'] == 'endpoint_reachability_only'
        assert ready['observed_at']['monotonic'] >= owned.snapshot()['started_at']['monotonic']
    finally:
        owned.close('completed')
    assert all(helper['cleanup_confirmed'] for helper in owned.snapshot()['helpers'])


def test_readiness_command_runs_once_and_actual_exit_predicate_is_retained(tmp_path):
    counter = tmp_path / 'counter'
    command = (sys.executable, '-c', f"from pathlib import Path; p=Path({str(counter)!r}); p.write_bytes(p.read_bytes()+b'x' if p.exists() else b'x'); raise SystemExit(7)")
    owned = process(tmp_path, readiness=ReadinessSelection('command', argv=command, expected_exit_code=0))
    try:
        owned.start()
        for _ in range(2):
            with pytest.raises(ManagedEmulatorError, match='readiness'):
                owned.wait_ready()
        assert counter.read_bytes() == b'x'
        assert owned.snapshot()['readiness']['exit_code'] == 7
    finally:
        owned.close('readiness_failed')


def test_authority_loss_terminates_blocking_owned_probe_and_main(tmp_path):
    marker = tmp_path / 'helper-pid'
    command = (sys.executable, '-c', f"import os,pathlib,time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)")
    authority = control()
    owned = ManagedEmulatorProcess(selection(tmp_path, readiness=ReadinessSelection('command', argv=command)), authority,
                                   ManagedProcessPaths(tmp_path / 'work'), evidence_policy=RecordWritePolicy(fsync=False))
    import threading
    def revoke():
        until(marker.exists)
        authority.revoke('server_cancelled')
    revoker = threading.Thread(target=revoke)
    try:
        owned.start()
        revoker.start()
        with pytest.raises(AttemptAuthorityLost):
            owned.wait_ready()
    finally:
        owned.close('cancelled')
        revoker.join(timeout=5)
    assert not revoker.is_alive()
    assert owned.snapshot()['cleanup_confirmed']
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


def test_poll_retains_exit_without_revoking_attempt(tmp_path):
    owned = process(tmp_path, 'raise SystemExit(9)')
    try:
        owned.start()
        until(lambda: owned.poll()['exit_code'] is not None)
        assert owned.poll()['exit_code'] == 9
        assert owned.control.snapshot()['authorized']
    finally:
        owned.close('process_exited')


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux per-owner subreaper')
def test_escaped_descendant_is_reaped_and_unrelated_process_survives(tmp_path):
    pidfile = tmp_path / 'descendant'
    code = f"import subprocess,sys,pathlib,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True); pathlib.Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(30)"
    other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
    owned = process(tmp_path, code)
    try:
        owned.start()
        until(pidfile.exists)
        owned.control.revoke('cancelled')
        assert owned.close('cancelled')['cleanup_confirmed']
        assert other.poll() is None
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
    finally:
        owned.close('cancelled')
        other.kill()
        other.wait(timeout=5)


def test_cleanup_failure_is_truthful_and_is_never_retried(tmp_path):
    class FakeProcess:
        pid = 123
        stdout = stderr = None
        def poll(self): return None
    class FailedGroup:
        process = FakeProcess()
        closed = False
        calls = 0
        def close(self):
            self.calls += 1
            raise OSError('secret-path-and-credential')
    group = FailedGroup()
    owned = ManagedEmulatorProcess(selection(tmp_path), control(), ManagedProcessPaths(tmp_path / 'work'),
                                   process_factory=lambda *a, **k: group, evidence_policy=RecordWritePolicy(fsync=False))
    owned.start()
    for _ in range(2):
        with pytest.raises(ManagedEmulatorError, match='cleanup'):
            owned.close('cancelled')
    assert group.calls == 1
    assert not owned.snapshot()['cleanup_confirmed']
    assert not owned.snapshot()['logs']['stdout']['observation_complete']
    assert 'secret-path-and-credential' not in json.dumps(owned.snapshot()) + owned.paths.manifest.read_text()
    with pytest.raises(ManagedEmulatorError):
        owned.retain_artifacts(tmp_path / 'retained')


def test_close_during_command_probe_joins_only_owned_processes(tmp_path):
    import threading
    marker = tmp_path / 'probe-pid'
    command = (sys.executable, '-c', f"import os,pathlib,time; pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)")
    owned = process(tmp_path, readiness=ReadinessSelection('command', argv=command))
    errors = []
    def ready():
        try: owned.wait_ready()
        except BaseException as error: errors.append(error)
    thread = threading.Thread(target=ready)
    try:
        owned.start()
        thread.start()
        until(marker.exists)
        owned.close('cancelled')
        thread.join(timeout=2)
        assert not thread.is_alive(), 'close left its readiness helper alive'
        assert owned.snapshot()['cleanup_confirmed']
        assert errors and isinstance(errors[0], ManagedEmulatorError)
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
    finally:
        # The controller is ours even in the RED case; revoke wakes the waiter,
        # whose finally closes its exact helper, before leaving the fixture.
        owned.control.revoke('fixture_cleanup')
        thread.join(timeout=5)
        try: owned.close('cancelled')
        except ManagedEmulatorError: pass


def test_positive_startup_budget_includes_time_spent_launching(tmp_path):
    class Clock:
        now = 100.
        def monotonic(self): return self.now
        def time(self): return self.now + 1000
        def sleep(self, seconds): self.now += seconds
    clock = Clock()
    from tinystudio.lab.runner import ProcessGroup
    def launch(*args, **kwargs):
        result = ProcessGroup(*args, **kwargs)
        clock.now += 2
        return result
    owned = ManagedEmulatorProcess(selection(tmp_path, startup_timeout_seconds=1), control(),
        ManagedProcessPaths(tmp_path / 'work'), process_factory=launch, clock=clock,
        evidence_policy=RecordWritePolicy(fsync=False))
    try:
        owned.start()
        with pytest.raises(ManagedEmulatorError, match='readiness'):
            owned.wait_ready()
        assert owned.snapshot()['readiness']['status'] == 'timed_out'
    finally:
        owned.close('startup_failed')


def test_owned_pty_program_can_select_log_readiness_without_serial_claims(tmp_path):
    code = "import os,pty,time; master,slave=pty.openpty(); os.write(master,b'owned-pty\\n'); print(os.ttyname(slave),flush=True); print('PTY_READY',flush=True); time.sleep(30)"
    owned = process(tmp_path, code, readiness=ReadinessSelection('log_bytes', stream='stdout', marker=b'PTY_READY'))
    try:
        owned.start()
        assert owned.wait_ready()['scope'] == 'owned_log_byte_sequence_observed'
    finally:
        owned.close('completed')
    assert owned.paths.private_log('stdout').read_bytes().startswith(b'/dev/')
    assert owned.snapshot()['qualification']['physical_outcome'] == 'unverified'


def test_successful_probe_observed_after_selected_deadline_is_not_ready(tmp_path):
    class Clock:
        now = 100.
        def monotonic(self): return self.now
        def time(self): return self.now + 1000
        def sleep(self, seconds): self.now += seconds
    clock = Clock()
    from tinystudio.lab.runner import ProcessGroup
    calls = []
    def launch(*args, **kwargs):
        result = ProcessGroup(*args, **kwargs)
        calls.append(result)
        if len(calls) == 2:
            assert result.process.wait(timeout=5) == 0
            clock.now += 2
        return result
    owned = ManagedEmulatorProcess(selection(tmp_path, startup_timeout_seconds=1,
        readiness=ReadinessSelection('command', argv=(sys.executable, '-c', 'pass'))), control(),
        ManagedProcessPaths(tmp_path / 'work'), process_factory=launch, clock=clock,
        evidence_policy=RecordWritePolicy(fsync=False))
    try:
        owned.start()
        with pytest.raises(ManagedEmulatorError, match='readiness'):
            owned.wait_ready()
        assert owned.snapshot()['readiness']['status'] == 'timed_out'
        assert owned.snapshot()['helpers'][0]['exit_code'] == 0
    finally:
        owned.close('startup_failed')


def test_launch_failure_without_returned_ownership_handle_does_not_claim_cleanup(tmp_path):
    def failed(*args, **kwargs):
        raise OSError('signed-url-and-private-environment-value')
    owned = ManagedEmulatorProcess(selection(tmp_path), control(), ManagedProcessPaths(tmp_path / 'work'),
        process_factory=failed, evidence_policy=RecordWritePolicy(fsync=False))
    with pytest.raises(ManagedEmulatorError, match='launch') as caught:
        owned.start()
    assert 'signed-url' not in str(caught.value)
    assert not owned.snapshot()['cleanup_confirmed']
    assert owned.snapshot()['launch']['ownership_handle_returned'] is False
    assert 'signed-url' not in owned.paths.manifest.read_text()


def test_empty_log_marker_and_repeated_close_are_explicit(tmp_path):
    owned = process(tmp_path, startup_timeout_seconds=0,
                    readiness=ReadinessSelection('log_bytes', stream='stdout', marker=b''))
    owned.start()
    assert owned.wait_ready()['ready']
    first = owned.close('completed')
    lifecycle = owned.paths.lifecycle.read_bytes()
    assert owned.close('cancelled') == first
    assert owned.paths.lifecycle.read_bytes() == lifecycle


def test_selected_command_exit_and_timeout_are_distinct(tmp_path):
    owned = process(tmp_path, readiness=ReadinessSelection('command', argv=(sys.executable, '-c', 'raise SystemExit(7)'), expected_exit_code=7))
    try:
        owned.start()
        assert owned.wait_ready()['exit_code'] == 7
    finally:
        owned.close('completed')
    owned = process(tmp_path / 'timeout', startup_timeout_seconds=.2,
                    readiness=ReadinessSelection('command', argv=(sys.executable, '-c', 'import time; time.sleep(30)')))
    owned.selection.cwd.mkdir(parents=True)
    try:
        owned.start()
        with pytest.raises(ManagedEmulatorError):
            owned.wait_ready()
        assert owned.snapshot()['readiness']['status'] == 'timed_out'
        assert all(helper['cleanup_confirmed'] for helper in owned.snapshot()['helpers'])
    finally:
        owned.close('startup_failed')


def test_helper_launch_failure_keeps_uncertainty_and_omits_private_error_text(tmp_path):
    from tinystudio.lab.runner import ProcessGroup
    calls = []
    def launch(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise OSError('private-URL-in-helper-diagnostic')
        return ProcessGroup(*args, **kwargs)
    owned = ManagedEmulatorProcess(selection(tmp_path,
        readiness=ReadinessSelection('command', argv=(sys.executable, '-c', 'pass'))), control(),
        ManagedProcessPaths(tmp_path / 'work'), process_factory=launch,
        evidence_policy=RecordWritePolicy(fsync=False))
    owned.start()
    try:
        with pytest.raises(ManagedEmulatorError, match='readiness') as caught:
            owned.wait_ready()
        assert 'private-URL' not in str(caught.value)
        assert owned.snapshot()['helpers'][0]['ownership_handle_returned'] is False
    finally:
        with pytest.raises(ManagedEmulatorError, match='cleanup'):
            owned.close('readiness_failed')
    assert len(calls) == 2
    assert not owned.snapshot()['cleanup_confirmed']
    assert 'private-URL' not in owned.paths.manifest.read_text()


@pytest.mark.parametrize('bad', [{'environment': {'BAD': 3}}, {'token': 'private'}, {'environment': {'A=B': 'x'}}])
def test_invalid_private_environment_is_rejected_before_io(tmp_path, bad):
    with pytest.raises(ValueError):
        ManagedEmulatorProcess(selection(tmp_path), control(), ManagedProcessPaths(tmp_path / 'work'), runtime_secrets=bad)
    assert not (tmp_path / 'work').exists()
