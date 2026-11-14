"""Parent coordinator tests use owned Python peers and bounded component doubles."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time

import pytest

from tinystudio.lab.attempt_control import AttemptControl, AttemptIdentity
from tinystudio.lab.hardware_emulator_selection import EmulatorProcessBinding, EmulatorSelection
from tinystudio.lab.hardware_session_client import HardwareSessionClient
from tinystudio.lab.hardware_session_configuration import HardwareSessionBinding
from tinystudio.lab.hardware_session_runtime import HardwareSessionRuntime, HardwareSessionRuntimeError
from tinystudio.lab.managed_process_configuration import ManagedProcessSelection, ReadinessSelection


def authority():
    control = AttemptControl(AttemptIdentity('http://127.0.0.1', 'run', 'attempt', 'target', 'a' * 64))
    control.renew(60, sent_at=time.monotonic())
    return control


@pytest.fixture
def default_sigint():
    import signal
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try: yield
    finally: signal.signal(signal.SIGINT, previous)


def binding(tmp_path, port=1):
    state = tmp_path / 'operator-state'
    state.mkdir(mode=0o700, exist_ok=True)
    stop = {'operation': 'tcp.query', 'data': {'encoding': 'utf8', 'value': 'HALT\n'}}
    return HardwareSessionBinding.from_grants({'native_execution': True, 'bindings': {'hardware': {
        'state_directory': str(state), 'lease_seconds': 10, 'renew_interval_seconds': 2,
        'gateway_binding': {'endpoint_id': 'owned-peer', 'operator_id': 'owned-operator',
            'transport': {'kind': 'tcp', 'host': '127.0.0.1', 'port': port, 'read': {'kind': 'fixed_bytes', 'count': 1}},
            'command_profile': {'format': 'tinystudio.native-command-profile.v1'},
            'local_stop': stop, 'send_timeout': 2}}}}, 'hardware')


def emulator_selection(**extra):
    return EmulatorSelection.from_definition({'engine': 'hardware.session', 'inputs': [], 'parameters': {
        'emulator': {'binding_id': 'emulator', 'assets': [], 'argv': [],
                     'readiness': {'kind': 'process_running'}, **extra}}})


def emulator_binding():
    return EmulatorProcessBinding.from_grants({'native_execution': True, 'emulator_bindings': {
        'emulator': {'executable': {'argv_prefix': [sys.executable]}}}}, 'emulator')


class Components:
    def __init__(self, tmp_path, control, *, fail_ready=False, fail_bridge_close=False,
                 fail_seal=False, fail_start=None, exit_code=None):
        self.events = []
        self.work = tmp_path / 'work'
        self.control = control
        self.fail_ready, self.fail_bridge_close = fail_ready, fail_bridge_close
        self.fail_seal, self.fail_start, self.exit_code = fail_seal, fail_start, exit_code
        self.emulator = self.bridge = None

    def workspace_factory(self, work, selection):
        owner = self
        class Workspace:
            def prepare(self, inputs, *, check_authority):
                check_authority(); owner.events.append('prepare')
                return type('Prepared', (), {'as_dict': lambda self: {'status': 'prepared'}})()
            def launch_selection(self, binding, **kwargs):
                kwargs['check_authority'](); owner.events.append('launch_selection')
                return ManagedProcessSelection((sys.executable,), Path(work), ReadinessSelection('process_running'))
            def seal(self, **kwargs):
                assert not kwargs, 'offline custody was coupled to live authority'
                owner.events.append('seal')
                if owner.fail_seal: raise OSError('private-seal-secret')
                return {'status': 'sealed', 'assets': {}, 'provenance': {'execution_success': 'not_inferred'}}
            def retain_artifacts(self, destination, **kwargs):
                owner.events.append('retain_workspace'); return []
        return Workspace()

    def emulator_factory(self, selection, control, paths, **kwargs):
        owner = self
        class Emulator:
            closed = False
            close_calls = 0
            state = 'created'
            launch_attempted = False
            closed_at = None
            def start(self):
                owner.events.append('emulator_start')
                if owner.fail_start == 'pure': raise ValueError('private-start-secret')
                self.launch_attempted = True; self.state = 'running'
                if owner.fail_start == 'auto_unknown':
                    self.close_calls += 1; self.state = 'cleanup_unconfirmed'; self.closed_at = {'monotonic': 1}
                    owner.events.append('emulator_internal_cleanup')
                    raise ValueError('private-start-secret')
            def wait_ready(self):
                owner.events.append('ready')
                if owner.fail_ready: raise ValueError('private-ready-secret')
                return {'ready': True, 'scope': 'owned_process_alive_only'}
            def close(self, reason):
                self.close_calls += 1; owner.events.append('emulator_close')
                self.closed = True; self.state = 'closed'; self.closed_at = {'monotonic': 2}
                return self.snapshot()
            def snapshot(self):
                return {'state': self.state, 'pid': None, 'exit_code': owner.exit_code,
                    'cleanup_confirmed': self.closed, 'closed_at': self.closed_at,
                    'launch': {'attempted': self.launch_attempted, 'ownership_handle_returned': self.launch_attempted},
                    'evidence': {}, 'logs': {}}
            def poll(self): owner.events.append('poll'); return self.snapshot()
            def retain_artifacts(self, destination, **kwargs): owner.events.append('retain_emulator'); return []
        self.emulator = Emulator()
        return self.emulator

    def bridge_factory(self, binding, control, evidence, **kwargs):
        owner = self
        class Bridge:
            cleanup_confirmed = True
            close_calls = 0
            descriptor = {'format': 'tinystudio.hardware-session-ipc.v1', 'path': str(owner.work / 'unused.sock'),
                          'capability': 'attempt-only', 'session_id': control.identity.owner, 'profile': {}}
            def start(self):
                owner.events.append('bridge_start'); self.cleanup_confirmed = False
            def close(self):
                self.close_calls += 1; owner.events.append('bridge_close')
                if owner.fail_bridge_close: raise OSError('private-bridge-secret')
                assert owner.emulator is None or not owner.emulator.closed
                control.revoke('parent_session_closed', termination='release')
                self.cleanup_confirmed = True
                return {'physical_outcome': 'unverified', 'termination': {'trigger': control.snapshot()['termination_trigger']}}
            def retain_artifacts(self, destination, **kwargs): owner.events.append('retain_bridge'); return []
        self.bridge = Bridge()
        return self.bridge

    def runtime(self, selected_binding, **kwargs):
        return HardwareSessionRuntime(selected_binding, self.control, self.work, input_paths={},
            emulator_selection=emulator_selection(), emulator_binding=emulator_binding(),
            bridge_factory=self.bridge_factory, workspace_factory=self.workspace_factory,
            emulator_factory=kwargs.pop('emulator_factory', self.emulator_factory), **kwargs)


def test_pure_constructor_and_strict_pairing_before_any_factory_or_path_io(tmp_path):
    from tinystudio.lab.hardware_session_configuration import HardwareSessionRuntimePaths
    from tinystudio.lab.hardware_session_runtime import HardwareSessionRuntimePaths as RuntimePathsAlias
    assert RuntimePathsAlias is HardwareSessionRuntimePaths
    control = authority(); owned = Components(tmp_path, control)
    runtime = owned.runtime(binding(tmp_path))
    assert owned.events == [] and not owned.work.exists()
    assert runtime.poll()['state'] == 'created'
    assert owned.events == []
    with pytest.raises(ValueError):
        HardwareSessionRuntime(binding(tmp_path), control, owned.work, input_paths={}, emulator_selection=emulator_selection())


def test_known_start_cleanup_order_descriptor_and_private_environment_scopes(tmp_path):
    control = authority(); components = Components(tmp_path, control)
    runtime = components.runtime(binding(tmp_path), runtime_secrets={'password': 'bridge-private'},
        emulator_runtime_secrets={'environment': {'TOKEN': 'emulator-private'}}, base_env={'BASE': 'base-private'})
    assert runtime.start() is runtime
    assert components.events == ['prepare', 'launch_selection', 'emulator_start', 'ready', 'bridge_start']
    descriptor = runtime.descriptor
    assert set(descriptor) == {'format', 'path', 'capability', 'session_id', 'profile'}
    descriptor['profile']['changed'] = True
    assert 'changed' not in runtime.descriptor['profile']
    control.revoke('server_cancelled', termination='cancel')
    result = runtime.close()
    assert components.events[-3:] == ['bridge_close', 'emulator_close', 'seal']
    assert result['cleanup_confirmed'] and result['workspace']['status'] == 'sealed'
    assert result['bridge']['status'] == 'cleanup_confirmed'
    assert result['bridge']['outcome']['termination']['trigger'] == 'cancel'
    assert runtime.close() == result
    assert components.bridge.close_calls == components.emulator.close_calls == 1
    artifacts = runtime.retain_artifacts(tmp_path / 'retained', visibility='truth')
    assert artifacts and all(item['visibility'] == 'truth' for item in artifacts)
    public = json.dumps(result) + ''.join(Path(item['path']).read_text() for item in artifacts)
    assert all(secret not in public for secret in ('bridge-private', 'emulator-private', 'base-private'))


@pytest.mark.parametrize('exit_code', [0, 9, -15])
def test_poll_observes_exit_without_changing_authority_or_scientific_policy(tmp_path, exit_code):
    control = authority(); components = Components(tmp_path, control, exit_code=exit_code)
    runtime = components.runtime(binding(tmp_path)).start()
    try:
        observed = runtime.poll()
        assert observed['emulator']['exit_code'] == exit_code
        assert observed['qualification']['scientific_outcome'] == 'not_inferred'
        assert control.snapshot()['authorized'] and components.bridge.close_calls == 0
        assert 'seal' not in components.events
    finally: runtime.close()


@pytest.mark.parametrize('interrupt', [KeyboardInterrupt, SystemExit])
def test_poll_does_not_swallow_caller_shutdown_signals(tmp_path, interrupt):
    components = Components(tmp_path, authority())
    runtime = components.runtime(binding(tmp_path)).start()
    def stop(): raise interrupt()
    components.emulator.poll = stop
    try:
        with pytest.raises(interrupt):
            runtime.poll()
    finally:
        runtime.close()
    assert runtime.cleanup_confirmed


def test_readiness_failure_closes_known_process_before_sealing_and_never_starts_bridge(tmp_path):
    components = Components(tmp_path, authority(), fail_ready=True)
    runtime = components.runtime(binding(tmp_path))
    with pytest.raises(HardwareSessionRuntimeError) as caught: runtime.start()
    assert caught.value.phase == 'emulator_readiness' and caught.value.primary_error_type == 'ValueError'
    assert 'private-ready-secret' not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert 'bridge_start' not in components.events
    assert components.events[-2:] == ['emulator_close', 'seal']
    assert runtime.cleanup_confirmed and components.emulator.close_calls == 1


@pytest.mark.parametrize('failure,confirmed', [('pure', True), ('auto_unknown', False)])
def test_failed_start_consumes_known_auto_cleanup_and_distinguishes_pure_validation(tmp_path, failure, confirmed):
    components = Components(tmp_path, authority(), fail_start=failure)
    runtime = components.runtime(binding(tmp_path))
    with pytest.raises(HardwareSessionRuntimeError): runtime.start()
    assert runtime.cleanup_confirmed is confirmed
    assert ('seal' in components.events) is confirmed
    assert components.emulator.close_calls == (1 if failure == 'auto_unknown' else 0)
    runtime.close()
    assert components.emulator.close_calls == (1 if failure == 'auto_unknown' else 0)


def test_unknown_bridge_cleanup_does_not_prevent_owned_emulator_close_or_seal_as_stable(tmp_path):
    components = Components(tmp_path, authority(), fail_bridge_close=True)
    runtime = components.runtime(binding(tmp_path)).start()
    result = runtime.close()
    assert not result['cleanup_confirmed'] and result['workspace']['status'] == 'not_sealed_cleanup_unconfirmed'
    assert components.events[-2:] == ['bridge_close', 'emulator_close']
    assert components.emulator.closed and 'seal' not in components.events
    assert runtime.close() == result and components.bridge.close_calls == 1
    artifacts = runtime.retain_artifacts(tmp_path / 'audit')
    assert any(item['role'] == 'hardware_session_runtime' for item in artifacts)
    assert 'retain_workspace' not in components.events and 'retain_bridge' not in components.events
    assert 'private-bridge-secret' not in json.dumps(result)


def test_seal_metadata_failure_is_separate_from_confirmed_process_cleanup(tmp_path):
    components = Components(tmp_path, authority(), fail_seal=True)
    runtime = components.runtime(binding(tmp_path)).start()
    result = runtime.close()
    assert result['cleanup_confirmed'] and not result['metadata_complete']
    assert result['workspace']['status'] == 'seal_failed'
    assert 'private-seal-secret' not in json.dumps(result)


def test_retained_metadata_digest_is_reverified_on_cached_retention(tmp_path):
    components = Components(tmp_path, authority())
    runtime = components.runtime(binding(tmp_path)).start()
    runtime.close()
    destination = tmp_path / 'retained'
    artifacts = runtime.retain_artifacts(destination)
    changed = Path(artifacts[0]['path']); changed.chmod(0o600); changed.write_bytes(b'changed metadata')
    with pytest.raises(HardwareSessionRuntimeError) as caught: runtime.retain_artifacts(destination)
    assert caught.value.phase == 'artifact_retention'
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_corrupted_new_copy_cannot_issue_a_verified_artifact_receipt(tmp_path, monkeypatch):
    import tinystudio.lab.hardware_session_runtime as module
    components = Components(tmp_path, authority())
    runtime = components.runtime(binding(tmp_path)).start()
    runtime.close()
    original = module._stable_file
    def corrupt_after_copy(path, *, destination=None, expected=None):
        result = original(path, destination=destination, expected=expected)
        if destination is not None: destination.write_bytes(b'owned simulated copy corruption')
        return result
    monkeypatch.setattr(module, '_stable_file', corrupt_after_copy)
    with pytest.raises(HardwareSessionRuntimeError) as caught: runtime.retain_artifacts(tmp_path / 'retained')
    assert caught.value.phase == 'artifact_retention' and caught.value.artifacts == []


def test_metadata_write_failure_keeps_primary_and_closes_the_known_writer(tmp_path, monkeypatch):
    from tinystudio.lab.evidence_io import JsonlEvidenceWriter
    def fail(*args): raise OSError('private-metadata-secret')
    monkeypatch.setattr(JsonlEvidenceWriter, 'write_record', fail)
    runtime = HardwareSessionRuntime(binding(tmp_path), authority(), tmp_path / 'work', input_paths={})
    with pytest.raises(HardwareSessionRuntimeError) as caught: runtime.start()
    assert caught.value.phase == 'metadata_open' and caught.value.primary_error_type == 'OSError'
    result = runtime.close()
    assert result['cleanup_confirmed'] and not result['metadata_complete']
    assert result['evidence']['closed'] and result['evidence']['written_records'] == 0
    assert 'private-metadata-secret' not in json.dumps(result)


def test_factory_failure_without_ownership_handle_remains_unknown(tmp_path):
    components = Components(tmp_path, authority())
    def unavailable(*args, **kwargs): raise OSError('private-factory-secret')
    runtime = components.runtime(binding(tmp_path), emulator_factory=unavailable)
    with pytest.raises(HardwareSessionRuntimeError) as caught: runtime.start()
    assert caught.value.phase == 'emulator_construct'
    assert not runtime.cleanup_confirmed and 'seal' not in components.events
    assert 'private-factory-secret' not in str(caught.value)


def test_real_core_validation_failure_before_launch_retains_only_available_parent_metadata(tmp_path):
    from tinystudio.lab.managed_emulator import ManagedEmulatorProcess
    control = authority()
    def revoked_before_start(*args, **kwargs):
        owned = ManagedEmulatorProcess(*args, **kwargs)
        control.revoke('server_cancelled', termination='cancel')
        return owned
    runtime = HardwareSessionRuntime(binding(tmp_path), control, tmp_path / 'work', input_paths={},
        emulator_selection=emulator_selection(), emulator_binding=emulator_binding(), emulator_factory=revoked_before_start)
    with pytest.raises(HardwareSessionRuntimeError) as caught: runtime.start()
    assert caught.value.primary_error_type == 'AttemptAuthorityLost'
    assert runtime.cleanup_confirmed
    assert runtime.emulator.snapshot()['launch']['attempted'] is False
    artifacts = runtime.retain_artifacts(tmp_path / 'retained')
    assert {item['role'] for item in artifacts} == {'hardware_session_runtime', 'emulator_asset_custody'}


def test_unknown_process_cleanup_exports_closed_metadata_but_never_raw_logs_or_workspace(tmp_path):
    from tinystudio.lab.managed_emulator import ManagedEmulatorProcess
    from tinystudio.lab.runner import ProcessGroup
    control = authority(); components = Components(tmp_path, control)
    groups = []
    class UnknownClose:
        closed = False
        close_calls = 0
        def __init__(self, *args, **kwargs):
            self.actual = ProcessGroup(*args, **kwargs); self.process = self.actual.process
        def close(self):
            self.close_calls += 1
            self.actual.close()  # The fixture still reaps every actually owned child.
            raise OSError('private-cleanup-secret')
    def launch(*args, **kwargs):
        owned = UnknownClose(*args, **kwargs); groups.append(owned); return owned
    def emulator(*args, **kwargs): return ManagedEmulatorProcess(*args, process_factory=launch, **kwargs)
    selected = emulator_selection(argv=['-c', 'import time; time.sleep(30)'], raw_log_visibility='truth')
    runtime = HardwareSessionRuntime(binding(tmp_path), control, components.work, input_paths={},
        emulator_selection=selected, emulator_binding=emulator_binding(), emulator_factory=emulator,
        bridge_factory=components.bridge_factory)
    try:
        runtime.start()
        result = runtime.close()
        assert not result['cleanup_confirmed'] and not runtime.workspace.report_path.exists()
        artifacts = runtime.retain_artifacts(tmp_path / 'audit')
        assert {'hardware_session_runtime', 'managed_emulator_parent'} <= {item['role'] for item in artifacts}
        assert all(item['role'] != 'emulator_asset_custody' and item['media_type'] != 'application/octet-stream' for item in artifacts)
        assert 'private-cleanup-secret' not in json.dumps(result)
        assert runtime.close() == result and all(group.close_calls == 1 for group in groups)
    finally:
        for group in groups: group.actual.close()


def test_bridge_only_runtime_uses_existing_descriptor_without_emulator_side_effects(tmp_path):
    control = authority(); components = Components(tmp_path, control)
    runtime = HardwareSessionRuntime(binding(tmp_path), control, components.work, input_paths={},
        bridge_factory=components.bridge_factory)
    runtime.start()
    assert runtime.poll()['emulator'] is None
    assert set(runtime.descriptor) == {'format', 'path', 'capability', 'session_id', 'profile'}
    result = runtime.close()
    assert result['cleanup_confirmed'] and result['workspace']['status'] == 'not_selected'
    assert components.events == ['bridge_start', 'bridge_close']


def test_close_during_preparation_waits_for_writers_and_prevents_late_launch(tmp_path):
    import threading
    control = authority(); components = Components(tmp_path, control)
    entered, resume, finished = threading.Event(), threading.Event(), threading.Event()
    original_factory = components.workspace_factory
    def workspace(*args):
        owned = original_factory(*args)
        original_prepare = owned.prepare
        def prepare(*values, **kwargs):
            entered.set(); assert resume.wait(3)
            return original_prepare(*values, **kwargs)
        owned.prepare = prepare
        return owned
    runtime = HardwareSessionRuntime(binding(tmp_path), control, components.work, input_paths={},
        emulator_selection=emulator_selection(), emulator_binding=emulator_binding(),
        workspace_factory=workspace, emulator_factory=components.emulator_factory, bridge_factory=components.bridge_factory)
    errors, results = [], []
    def start():
        try: runtime.start()
        except BaseException as error: errors.append(error)
    def close():
        try: results.append(runtime.close())
        finally: finished.set()
    starter = threading.Thread(target=start); closer = threading.Thread(target=close)
    try:
        starter.start(); assert entered.wait(2)
        closer.start()
        deadline = time.monotonic() + 2
        while not finished.is_set() and not control.snapshot()['revoked']:
            assert time.monotonic() < deadline
            time.sleep(.005)
        assert not finished.is_set(), 'close sealed while preparation was still writing'
        assert 'seal' not in components.events
        resume.set(); starter.join(3); closer.join(3)
        assert not starter.is_alive() and not closer.is_alive()
        assert errors and isinstance(errors[0], HardwareSessionRuntimeError)
        assert results[0]['cleanup_confirmed']
        assert 'emulator_start' not in components.events and 'bridge_start' not in components.events
    finally:
        resume.set(); starter.join(3)
        if closer.ident is not None: closer.join(3)
        if components.emulator is not None and not components.emulator.closed:
            components.emulator.close('fixture_cleanup')


def test_actual_bridge_close_blocks_retention_until_final_runtime_inventory(tmp_path):
    import threading
    from tinystudio.lab.hardware_session_bridge import HardwareSessionBridge
    entered, release = threading.Event(), threading.Event()
    retain_entered, retain_done = threading.Event(), threading.Event()
    selected = binding(tmp_path)
    gateway = selected.gateway_binding
    gateway.pop('local_stop')  # This metadata-only fixture never opens a native transport.
    selected = HardwareSessionBinding.from_grants({'native_execution': True, 'bindings': {'hardware': {
        'state_directory': str(selected.state_directory), 'gateway_binding': gateway}}}, 'hardware')
    class GatedBridge(HardwareSessionBridge):
        def close(self):
            entered.set(); assert release.wait(5)
            return super().close()
    class NoTransportIO:
        kind = 'tcp'
        def exchange(self, *args, **kwargs): raise AssertionError('metadata-only fixture attempted transport I/O')
    runtime = HardwareSessionRuntime(selected, authority(), tmp_path / 'work', input_paths={},
        bridge_factory=GatedBridge, transport_factory=lambda *args, **kwargs: NoTransportIO()).start()
    destination = tmp_path / 'retained'
    results, artifacts, errors = [], [], []
    def close():
        try: results.append(runtime.close())
        except BaseException as error: errors.append(error)
    def retain():
        retain_entered.set()
        try: artifacts.extend(runtime.retain_artifacts(destination))
        except BaseException as error: errors.append(error)
        finally: retain_done.set()
    closer, retainer = threading.Thread(target=close), threading.Thread(target=retain)
    try:
        closer.start(); assert entered.wait(2)
        early_cleanup = runtime.cleanup_confirmed
        assert not runtime.bridge.cleanup_confirmed
        retainer.start(); assert retain_entered.wait(2)
        early_retention = retain_done.wait(.1)
        release.set(); closer.join(3); retainer.join(3)
        assert not closer.is_alive() and not retainer.is_alive() and not errors
        assert not early_cleanup, 'cleanup was confirmed while the actual bridge was still live'
        assert not early_retention, 'retention exported and cached an unfinished close inventory'
        assert runtime.cleanup_confirmed and results[0]['cleanup_confirmed']
        names = {item['name'] for item in artifacts}
        assert {'hardware-session-runtime.jsonl', 'hardware-session-runtime.json'} <= names
        ledger = next(item for item in artifacts if item['role'] == 'hardware_session_parent' and item['media_type'] == 'application/x-ndjson')
        rows = [json.loads(line) for line in Path(ledger['path']).read_text().splitlines()]
        assert rows[-1]['kind'] == 'session_closed'
        assert runtime.retain_artifacts(destination) == artifacts
    finally:
        release.set()
        if closer.ident is not None: closer.join(3)
        if retainer.ident is not None: retainer.join(3)
        runtime.close()


def test_unknown_live_supervisor_blocks_real_custody_and_raw_exports_after_own_cleanup(tmp_path):
    from tinystudio.lab.runner import ProcessGroup
    control = authority(); components = Components(tmp_path, control)
    original = tmp_path / 'frozen.bin'; original.write_bytes(b'original')
    selected = EmulatorSelection.from_definition({'engine': 'hardware.session',
        'inputs': [{'artifact_id': 'asset', 'sha256': hashlib.sha256(b'original').hexdigest()}], 'parameters': {'emulator': {
            'binding_id': 'emulator', 'assets': [{'name': 'data', 'artifact_id': 'asset', 'destination': 'data.bin'}],
            'argv': ['-c', 'import time; time.sleep(30)'], 'readiness': {'kind': 'process_running'},
            'shutdown_grace_seconds': .1, 'raw_log_visibility': 'truth'}}})
    runtime = HardwareSessionRuntime(binding(tmp_path), control, components.work, input_paths={'asset': original},
        emulator_selection=selected, emulator_binding=emulator_binding(), bridge_factory=components.bridge_factory).start()
    marker = tmp_path / 'supervisor-running'
    code = f"from pathlib import Path; import time; target=Path({str(runtime.workspace.root / 'data.bin')!r}); Path({str(marker)!r}).touch(); i=0\nwhile True: target.write_bytes(str(i).encode()); i+=1; time.sleep(.01)"
    supervisor = ProcessGroup([sys.executable, '-c', code], grace=.1)
    try:
        deadline = time.monotonic() + 3
        while not marker.exists():
            assert time.monotonic() < deadline
            time.sleep(.005)
        assert supervisor.process.poll() is None
        result = runtime.close(supervisor_cleanup_confirmed=False)
        assert runtime.cleanup_confirmed and result['cleanup_confirmed']
        assert result['writer_quiescence']['supervisor_cleanup_confirmed'] is False
        assert result['writer_quiescence']['supervisor_scope'] == 'caller_assertion_not_process_observation'
        assert result['writer_quiescence']['all_writers_quiescent'] is False
        assert result['workspace']['status'] == 'not_sealed_supervisor_cleanup_unconfirmed'
        assert not runtime.workspace.report_path.exists() and supervisor.process.poll() is None
        assert not runtime.paths.assurance.exists()
        assert json.loads(runtime.paths.manifest.read_text())['writer_quiescence']['all_writers_quiescent'] is False
        artifacts = runtime.retain_artifacts(tmp_path / 'audit')
        assert {'hardware_session_runtime', 'managed_emulator_parent'} <= {item['role'] for item in artifacts}
        assert not any(item['role'] == 'emulator_asset_custody' or item['media_type'] == 'application/octet-stream' for item in artifacts)
        assert original.read_bytes() == b'original'
        assert runtime.close(supervisor_cleanup_confirmed=True)['writer_quiescence']['false_latched'] is True
    finally:
        supervisor.close()
        try: runtime.close(supervisor_cleanup_confirmed=False)
        except TypeError: runtime.close()  # RED against the prior API still reaps its owned model.


def test_concurrent_false_supervisor_assertion_wins_over_true_before_seal(tmp_path):
    import threading
    components = Components(tmp_path, authority())
    runtime = components.runtime(binding(tmp_path)).start()
    entered, release = threading.Event(), threading.Event()
    original_close = runtime.bridge.close
    def gated_close():
        entered.set(); assert release.wait(3)
        return original_close()
    runtime.bridge.close = gated_close
    results, errors = [], []
    def close(value):
        try: results.append(runtime.close(supervisor_cleanup_confirmed=value))
        except BaseException as error: errors.append(error)
    first, second = threading.Thread(target=close, args=(True,)), threading.Thread(target=close, args=(False,))
    try:
        first.start(); assert entered.wait(2)
        second.start()
        assert runtime._supervisor_uncertain.wait(2)  # Synchronize publication of the restrictive caller assertion.
        release.set(); first.join(3); second.join(3)
        assert not first.is_alive() and not second.is_alive() and not errors
        assert len(results) == 2 and all(not row['writer_quiescence']['all_writers_quiescent'] for row in results)
        assert 'seal' not in components.events
        assert components.bridge.close_calls == components.emulator.close_calls == 1
        assert runtime.close(supervisor_cleanup_confirmed=True) == results[-1]
    finally:
        release.set()
        if first.ident is not None: first.join(3)
        if second.ident is not None: second.join(3)
        runtime.close(supervisor_cleanup_confirmed=False)


def test_late_false_invalidates_prior_seal_and_cached_raw_inventory_without_rewriting_it(tmp_path):
    components = Components(tmp_path, authority())
    selected = emulator_selection(argv=['-c', 'import time; time.sleep(30)'], raw_log_visibility='truth')
    runtime = HardwareSessionRuntime(binding(tmp_path), components.control, components.work, input_paths={},
        emulator_selection=selected, emulator_binding=emulator_binding(), bridge_factory=components.bridge_factory).start()
    first = runtime.close()
    assert first['workspace']['status'] == 'sealed' and first['writer_quiescence']['all_writers_quiescent']
    original_manifest = runtime.paths.manifest.read_bytes()
    original_custody = runtime.workspace.report_path.read_bytes()
    destination = tmp_path / 'retained'
    prior_artifacts = runtime.retain_artifacts(destination)
    assert any(item['media_type'] == 'application/octet-stream' for item in prior_artifacts)
    late = runtime.close(supervisor_cleanup_confirmed=False)
    assert late['cleanup_confirmed'] and not late['writer_quiescence']['all_writers_quiescent']
    assert late['workspace']['status'] == 'prior_seal_not_attested' and late['workspace']['report'] is None
    assert late['workspace']['prior_report']['status'] == 'sealed'
    assert late['supervisor_assurance']['prior_exports_not_recalled'] is True
    assert runtime.paths.manifest.read_bytes() == original_manifest
    assert runtime.workspace.report_path.read_bytes() == original_custody
    current = runtime.retain_artifacts(destination)
    assert any(item['name'] == runtime.paths.assurance.name for item in current)
    assert not any(item['media_type'] == 'application/octet-stream' or item['role'] == 'emulator_asset_custody' for item in current)
    correction = json.loads(runtime.paths.assurance.read_text())
    assert correction['writer_quiescence']['false_latched'] is True
    assert correction['workspace']['status'] == 'prior_seal_not_attested'
    assert correction['supersedes_runtime_manifest_sha256'] == hashlib.sha256(original_manifest).hexdigest()
    assert runtime.close(supervisor_cleanup_confirmed=True) == late


def test_false_arriving_during_seal_keeps_that_report_historical_and_unexported(tmp_path):
    import threading
    components = Components(tmp_path, authority())
    entered, release = threading.Event(), threading.Event()
    def workspace(*args):
        owned = components.workspace_factory(*args)
        original = owned.seal
        def seal():
            entered.set(); assert release.wait(3)
            return original()
        owned.seal = seal
        return owned
    runtime = HardwareSessionRuntime(binding(tmp_path), components.control, components.work, input_paths={},
        emulator_selection=emulator_selection(), emulator_binding=emulator_binding(), workspace_factory=workspace,
        emulator_factory=components.emulator_factory, bridge_factory=components.bridge_factory).start()
    results, errors = [], []
    def close(value):
        try: results.append(runtime.close(supervisor_cleanup_confirmed=value))
        except BaseException as error: errors.append(error)
    first, second = threading.Thread(target=close, args=(True,)), threading.Thread(target=close, args=(False,))
    try:
        first.start(); assert entered.wait(2)
        second.start(); assert runtime._supervisor_uncertain.wait(2)
        release.set(); first.join(3); second.join(3)
        assert not first.is_alive() and not second.is_alive() and not errors
        assert components.events.count('seal') == 1
        assert all(row['workspace']['status'] == 'prior_seal_not_attested' for row in results)
        assert all(not row['writer_quiescence']['all_writers_quiescent'] for row in results)
        runtime.retain_artifacts(tmp_path / 'audit')
        assert 'retain_workspace' not in components.events
    finally:
        release.set()
        if first.ident is not None: first.join(3)
        if second.ident is not None: second.join(3)
        runtime.close(supervisor_cleanup_confirmed=False)


def test_actual_startup_sigint_propagates_after_owned_cleanup_and_metadata(tmp_path, default_sigint):
    import signal
    from tinystudio.lab.managed_emulator import ManagedEmulatorProcess
    class InterruptAfterReady(ManagedEmulatorProcess):
        def wait_ready(self):
            super().wait_ready()
            os.kill(os.getpid(), signal.SIGINT)
            raise AssertionError('SIGINT did not interrupt startup')
    runtime = HardwareSessionRuntime(binding(tmp_path), authority(), tmp_path / 'work', input_paths={},
        emulator_selection=emulator_selection(argv=['-c', 'import time; time.sleep(30)'], shutdown_grace_seconds=.1),
        emulator_binding=emulator_binding(), emulator_factory=InterruptAfterReady)
    with pytest.raises(KeyboardInterrupt) as caught: runtime.start()
    assert caught.value.args == () and caught.value.__context__ is None and caught.value.__cause__ is None
    assert runtime.cleanup_confirmed and runtime.emulator.snapshot()['cleanup_confirmed']
    summary = runtime.close()
    assert summary['primary_error']['type'] == 'KeyboardInterrupt'
    assert summary['evidence']['closed'] and runtime.paths.manifest.is_file()


@pytest.mark.parametrize('code,expected', [(None, None), (0, 0), (7, 7), ('private-system-exit-secret', 1)])
def test_startup_systemexit_keeps_safe_code_and_cleanup_without_private_context(tmp_path, code, expected):
    components = Components(tmp_path, authority())
    def emulator(*args, **kwargs):
        owned = components.emulator_factory(*args, **kwargs)
        def exit_ready(): raise SystemExit(code)
        owned.wait_ready = exit_ready
        return owned
    runtime = components.runtime(binding(tmp_path), emulator_factory=emulator)
    with pytest.raises(SystemExit) as caught: runtime.start()
    assert caught.value.code == expected and caught.value.__context__ is None and caught.value.__cause__ is None
    assert 'private-system-exit-secret' not in str(caught.value) + json.dumps(runtime.close())
    assert components.emulator.close_calls == 1 and runtime.cleanup_confirmed


def test_actual_retention_sigint_propagates_after_real_owned_process_cleanup(tmp_path, monkeypatch, default_sigint):
    import signal
    components = Components(tmp_path, authority())
    runtime = HardwareSessionRuntime(binding(tmp_path), components.control, components.work, input_paths={},
        emulator_selection=emulator_selection(argv=['-c', 'import time; time.sleep(30)'], shutdown_grace_seconds=.1),
        emulator_binding=emulator_binding(), bridge_factory=components.bridge_factory).start()
    result = runtime.close()
    pid = result['emulator']['observation']['pid']
    with pytest.raises(ProcessLookupError): os.kill(pid, 0)
    observed = []
    def interrupt(*args, **kwargs):
        observed.append(runtime.cleanup_confirmed)
        os.kill(os.getpid(), signal.SIGINT)
        raise AssertionError('SIGINT did not interrupt retention')
    monkeypatch.setattr(runtime, '_retain_known', interrupt)
    with pytest.raises(KeyboardInterrupt) as caught: runtime.retain_artifacts(tmp_path / 'retained')
    assert observed == [True]
    assert caught.value.args == () and caught.value.__context__ is None and caught.value.__cause__ is None
    assert runtime.close() == result and runtime.cleanup_confirmed


@pytest.mark.parametrize('code,expected', [(None, None), (0, 0), (7, 7), ('private-retention-exit-secret', 1)])
def test_retention_systemexit_preserves_safe_code_without_private_context(tmp_path, monkeypatch, code, expected):
    components = Components(tmp_path, authority())
    runtime = components.runtime(binding(tmp_path)).start()
    runtime.close()
    def exit_retention(*args, **kwargs): raise SystemExit(code)
    monkeypatch.setattr(runtime, '_retain_known', exit_retention)
    with pytest.raises(SystemExit) as caught: runtime.retain_artifacts(tmp_path / 'retained')
    assert caught.value.code == expected
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert 'private-retention-exit-secret' not in str(caught.value) + json.dumps(runtime.close())


def test_regular_retention_failure_keeps_already_verified_artifact_metadata(tmp_path, monkeypatch):
    components = Components(tmp_path, authority())
    runtime = components.runtime(binding(tmp_path)).start()
    runtime.close()
    original, verified = runtime._retain_known, []
    def fail_second(*args, **kwargs):
        if verified: raise OSError('private-retention-diagnostic')
        artifact = original(*args, **kwargs)
        verified.append(artifact)
        return artifact
    monkeypatch.setattr(runtime, '_retain_known', fail_second)
    with pytest.raises(HardwareSessionRuntimeError) as caught: runtime.retain_artifacts(tmp_path / 'retained')
    assert caught.value.phase == 'artifact_retention' and caught.value.primary_error_type == 'OSError'
    assert caught.value.artifacts == verified and len(verified) == 1
    assert hashlib.sha256(Path(verified[0]['path']).read_bytes()).hexdigest() == verified[0]['sha256']
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert 'private-retention-diagnostic' not in str(caught.value)


def test_actual_seal_sigint_finishes_metadata_then_propagates_once(tmp_path, monkeypatch, default_sigint):
    import signal
    components = Components(tmp_path, authority())
    runtime = HardwareSessionRuntime(binding(tmp_path), components.control, components.work, input_paths={},
        emulator_selection=emulator_selection(argv=['-c', 'import time; time.sleep(30)'], shutdown_grace_seconds=.1),
        emulator_binding=emulator_binding(), bridge_factory=components.bridge_factory).start()
    observed = []
    def interrupt_seal():
        snapshot = runtime.emulator.snapshot()
        assert snapshot['cleanup_confirmed']
        with pytest.raises(ProcessLookupError): os.kill(snapshot['pid'], 0)
        observed.append(True)
        os.kill(os.getpid(), signal.SIGINT)
    monkeypatch.setattr(runtime.workspace, 'seal', interrupt_seal)
    with pytest.raises(KeyboardInterrupt) as caught: runtime.close()
    assert observed == [True]
    assert caught.value.args == () and caught.value.__context__ is None and caught.value.__cause__ is None
    result = runtime.close()
    assert runtime.cleanup_confirmed and result['workspace']['status'] == 'seal_failed'
    assert result['evidence']['closed'] and runtime.paths.manifest.is_file()
    assert {'phase': 'workspace_seal', 'type': 'KeyboardInterrupt'} in result['errors']
    assert runtime.close() == result and observed == [True]


@pytest.mark.parametrize('primary,expected,code', [
    (KeyboardInterrupt('private-primary'), KeyboardInterrupt, None),
    (SystemExit(7), SystemExit, 7),
    (SystemExit(None), SystemExit, None),
    (ValueError('private-primary'), KeyboardInterrupt, None),
])
def test_startup_error_and_second_cleanup_signal_keep_first_shutdown_without_context(tmp_path, primary, expected, code):
    components = Components(tmp_path, authority())
    def emulator(*args, **kwargs):
        owned = components.emulator_factory(*args, **kwargs)
        original_close = owned.close
        def ready(): raise primary
        def close(reason):
            original_close(reason)
            raise KeyboardInterrupt('private-secondary')
        owned.wait_ready, owned.close = ready, close
        return owned
    runtime = components.runtime(binding(tmp_path), emulator_factory=emulator)
    with pytest.raises(expected) as caught: runtime.start()
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    if expected is SystemExit: assert caught.value.code == code
    else: assert caught.value.args == ()
    result = runtime.close()
    assert result['primary_error']['type'] == type(primary).__name__
    assert components.emulator.close_calls == 1 and runtime.cleanup_confirmed
    assert 'private-primary' not in str(caught.value) + json.dumps(result)
    assert 'private-secondary' not in str(caught.value) + json.dumps(result)


@pytest.mark.parametrize('boundary', [
    'bridge_cleanup', 'bridge_cleanup_observation', 'emulator_cleanup', 'emulator_cleanup_observation',
    'workspace_seal', 'metadata_write', 'metadata_close', 'metadata_identity',
    'metadata_manifest_write', 'metadata_manifest_hash', 'supervisor_assurance_metadata',
])
@pytest.mark.parametrize('shutdown,code', [('interrupt', None), ('exit', None), ('exit', 7), ('exit', 'private-close-code')])
def test_cleanup_and_metadata_boundaries_deliver_safe_shutdown_once_after_remaining_work(
        tmp_path, monkeypatch, boundary, shutdown, code):
    import tinystudio.lab.hardware_session_runtime as module
    components = Components(tmp_path, authority())
    runtime = components.runtime(binding(tmp_path)).start()
    fired = []
    def interrupt():
        if fired: return
        fired.append(True)
        if shutdown == 'interrupt': raise KeyboardInterrupt('private-close-args')
        raise SystemExit(code)
    phase = boundary
    if boundary == 'bridge_cleanup':
        original = runtime.bridge.close
        def close_bridge():
            result = original(); interrupt(); return result
        monkeypatch.setattr(runtime.bridge, 'close', close_bridge)
    elif boundary == 'bridge_cleanup_observation':
        value = {'closed': False}
        def get_closed(self): interrupt(); return value['closed']
        def set_closed(self, closed): value['closed'] = closed
        monkeypatch.setattr(type(runtime.bridge), 'cleanup_confirmed', property(get_closed, set_closed))
    elif boundary == 'emulator_cleanup':
        original = runtime.emulator.close
        def close_emulator(reason):
            result = original(reason); interrupt(); return result
        monkeypatch.setattr(runtime.emulator, 'close', close_emulator)
    elif boundary == 'emulator_cleanup_observation':
        original, calls = runtime.emulator.snapshot, []
        def snapshot():
            calls.append(True)
            if len(calls) == 2: interrupt()
            return original()
        monkeypatch.setattr(runtime.emulator, 'snapshot', snapshot)
    elif boundary == 'workspace_seal':
        def seal(): interrupt()
        monkeypatch.setattr(runtime.workspace, 'seal', seal)
    elif boundary == 'metadata_write':
        original = runtime._writer.write_record
        def write_record(row):
            if row['event'] == 'runtime_closed': interrupt()
            return original(row)
        monkeypatch.setattr(runtime._writer, 'write_record', write_record)
    elif boundary == 'metadata_close':
        original = runtime._writer.close
        def close_writer():
            original(); interrupt()
        monkeypatch.setattr(runtime._writer, 'close', close_writer)
    elif boundary in {'metadata_identity', 'metadata_manifest_hash'}:
        phase = 'metadata_identity' if boundary == 'metadata_identity' else 'metadata_manifest'
        selected = runtime.paths.lifecycle if boundary == 'metadata_identity' else runtime.paths.manifest
        original = module._stable_file
        def observe(path, **kwargs):
            if Path(path) == selected and kwargs.get('destination') is None: interrupt()
            return original(path, **kwargs)
        monkeypatch.setattr(module, '_stable_file', observe)
    elif boundary in {'metadata_manifest_write', 'supervisor_assurance_metadata'}:
        phase = 'metadata_manifest' if boundary == 'metadata_manifest_write' else boundary
        if boundary == 'supervisor_assurance_metadata': runtime.close()
        selected = runtime.paths.manifest if boundary == 'metadata_manifest_write' else runtime.paths.assurance
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path == selected and args and args[0] == 'xb': interrupt()
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', open_file)
    value = boundary != 'supervisor_assurance_metadata'
    expected = KeyboardInterrupt if shutdown == 'interrupt' else SystemExit
    with pytest.raises(expected) as caught: runtime.close(supervisor_cleanup_confirmed=value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    if shutdown == 'interrupt': assert caught.value.args == ()
    else: assert caught.value.code == (code if code is None or type(code) is int else 1)
    result = runtime.close(supervisor_cleanup_confirmed=value)
    assert runtime.close(supervisor_cleanup_confirmed=value) == result
    assert fired == [True] and components.bridge.close_calls == components.emulator.close_calls == 1
    assert result['evidence']['closed']
    assert {'phase': phase, 'type': expected.__name__} in result['errors']
    assert 'private-close' not in str(caught.value) + json.dumps(result)
    if boundary in {'bridge_cleanup_observation', 'emulator_cleanup_observation'}:
        assert not result['cleanup_confirmed'] and 'seal' not in components.events
    else: assert result['cleanup_confirmed']


def test_shutdown_from_clock_during_startup_does_not_continue_to_factories(tmp_path):
    class Clock:
        called = False
        def monotonic(self):
            if not self.called:
                self.called = True
                raise KeyboardInterrupt('private-clock-args')
            return time.monotonic()
        def time(self): return time.time()
    components = Components(tmp_path, authority())
    runtime = components.runtime(binding(tmp_path), clock=Clock())
    with pytest.raises(KeyboardInterrupt) as caught: runtime.start()
    assert caught.value.args == () and caught.value.__context__ is None and caught.value.__cause__ is None
    assert components.events == []
    result = runtime.close()
    assert runtime.cleanup_confirmed and result['evidence']['closed']
    assert {'phase': 'metadata_clock', 'type': 'KeyboardInterrupt'} in result['errors']


@pytest.mark.parametrize('raw_visibility', [None, 'truth'])
def test_actual_owned_tcp_emulator_real_ipc_stop_before_process_close_and_asset_custody(tmp_path, raw_visibility):
    with socket.socket() as port_owner:
        port_owner.bind(('127.0.0.1', 0)); port = port_owner.getsockname()[1]
    source = tmp_path / 'downloaded-peer.py'
    source.write_text('''import json,os,socket,sys
from pathlib import Path
Path(__file__).open('ab').write(b'\\n# runtime copy changed\\n')
os.write(1,b'\\x00\\xffowned-peer\\n')
with socket.socket() as server:
    server.bind(('127.0.0.1',int(sys.argv[1])));server.listen()
    while True:
        connection,_=server.accept()
        with connection:
            data=bytearray()
            while not data.endswith(b'\\n'):
                block=connection.recv(1)
                if not block:break
                data.extend(block)
            if data:
                with Path(sys.argv[2]).open('ab') as output:output.write((json.dumps({'hex':data.hex()})+'\\n').encode())
                connection.sendall(b'K')
''')
    original = source.read_bytes()
    selected = EmulatorSelection.from_definition({'engine': 'hardware.session',
        'inputs': [{'artifact_id': 'peer-source', 'sha256': hashlib.sha256(original).hexdigest()}], 'parameters': {'emulator': {
            'binding_id': 'emulator', 'assets': [{'name': 'peer', 'artifact_id': 'peer-source', 'destination': 'peer.py'}],
            'argv': [{'asset': 'peer'}, str(port), {'workspace': 'effects.jsonl'}],
            'readiness': {'kind': 'tcp', 'host': '127.0.0.1', 'port': port},
            'startup_timeout_seconds': 5, 'shutdown_grace_seconds': .1, 'raw_log_visibility': raw_visibility}}})
    control = authority()
    runtime = HardwareSessionRuntime(binding(tmp_path, port), control, tmp_path / 'work',
        input_paths={'peer-source': source}, emulator_selection=selected, emulator_binding=emulator_binding())
    try:
        runtime.start()
        (runtime.paths.bridge / 'unrelated-private.txt').write_text('private-unselected-file')
        client = HardwareSessionClient(runtime.descriptor, timeout=3)
        try:
            receipt = client.submit('binary', {'operation': 'tcp.query', 'data': {'encoding': 'hex', 'value': '00ff0a'}}, valid_for_seconds=3)
            assert receipt['status'] == 'response_received' and receipt['physical_outcome'] == 'unverified'
        finally: client.close()
        control.revoke('server_cancelled', termination='cancel')
    finally: result = runtime.close()
    assert result['cleanup_confirmed'] and runtime.cleanup_confirmed
    effects = [json.loads(line)['hex'] for line in (runtime.workspace.root / 'effects.jsonl').read_text().splitlines()]
    assert effects == ['00ff0a', b'HALT\n'.hex()]
    assert source.read_bytes() == original
    custody = result['workspace']['report']['assets']['peer']
    assert custody['original_status'] == 'matched' and custody['copy_status'] == 'changed'
    assert result['qualification']['scientific_outcome'] == 'not_inferred'
    artifacts = runtime.retain_artifacts(tmp_path / 'retained')
    assert not any(b'private-unselected-file' in Path(item['path']).read_bytes() for item in artifacts)
    assert {'hardware_session_parent', 'managed_emulator_parent', 'emulator_asset_custody', 'hardware_session_runtime'} <= {item['role'] for item in artifacts}
    raw = [item for item in artifacts if item['media_type'] == 'application/octet-stream']
    assert len(raw) == (0 if raw_visibility is None else 2)
    assert all(item['visibility'] == 'truth' for item in raw)
    if raw:
        assert any(Path(item['path']).read_bytes() == b'\x00\xffowned-peer\n' for item in raw)
    with socket.socket() as probe: assert probe.connect_ex(('127.0.0.1', port)) != 0
