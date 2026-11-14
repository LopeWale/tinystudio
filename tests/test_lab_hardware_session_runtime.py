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
