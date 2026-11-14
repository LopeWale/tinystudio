"""Exact-attempt cleanup policies with owned journals and a software device."""
import json
import hashlib
from pathlib import Path
import shutil
import time

import pytest

from tinystudio.lab.attempt_control import AttemptControl, AttemptIdentity
from tinystudio.lab.runner import AttemptCancelled, LeaseHeartbeat
from tests.test_lab_hardware_session_bridge import setup
from tests.test_lab_native_gateway import binding, payload


class HeartbeatTransport:
    def __init__(self, *, cancelled=False, error=None):
        self.cancelled, self.error = cancelled, error

    def post(self, *args):
        if self.error is not None:
            raise self.error
        return {'cancel_requested': self.cancelled, 'lease_seconds': 60}


def heartbeat(tmp_path, control, **kwargs):
    root = tmp_path/'heartbeat'
    root.mkdir()
    return LeaseHeartbeat(HeartbeatTransport(**kwargs), '/attempts/owned', 'owned-lease', root,
                          interval=60, deadline=time.monotonic()+60, authority=control)


@pytest.mark.parametrize('submitted', [False, True])
def test_server_cancellation_between_submissions_uses_cancel_policy_once(tmp_path, submitted):
    bridge, control, device, evidence = setup(tmp_path)
    try:
        bridge.start()
        if submitted:
            bridge.submit('before-cancel', payload('SET 7\n'), valid_for_seconds=5)
        beat = heartbeat(tmp_path, control, cancelled=True)
        beat.renew()
        with pytest.raises(AttemptCancelled):
            beat.check()
        outcome = bridge.close()
        assert [row['kind'] for row in device.effects] == (['command'] if submitted else []) + ['local_stop']
        assert device.effects[-1]['payload'] == payload('OFF\n')
        assert outcome['release_receipt'] is None
        assert outcome['termination'] == {'trigger': 'cancel', 'reason': 'server_requested_cancellation'}
        receipt = outcome['termination_receipt']
        assert receipt['trigger'] == 'cancel' and receipt['action'] == 'local_stop'
        assert receipt['owner'] == control.identity.owner and receipt['lease_epoch'] == bridge._lease['epoch']
        assert receipt['physical_outcome'] == 'unverified' and receipt['host_state'] == 'recovery_required'
        assert bridge.close() == outcome
        assert sum(row['kind'] == 'local_stop' for row in device.effects) == 1
        retained = json.loads((evidence/'hardware-session-parent.json').read_text())
        assert retained['authority']['revocation_reason'] == 'server_requested_cancellation'
        assert retained['outcome']['termination_receipt'] == receipt
    finally:
        bridge.close()


def test_heartbeat_transport_loss_uses_expiry_policy_without_becoming_server_cancel(tmp_path):
    selected = binding(local_stop=payload('OFF\n'), lease_policy={
        'on_release': 'revoke_only', 'on_cancel': 'revoke_only', 'on_expiry': 'local_stop'})
    bridge, control, device, _ = setup(tmp_path, gateway_binding=selected)
    try:
        bridge.start()
        beat = heartbeat(tmp_path, control, error=ConnectionError('owned heartbeat outage'))
        with pytest.raises(ConnectionError):
            beat.renew()
        outcome = bridge.close()
        assert beat.connectivity_lost and not beat.cancelled.is_set()
        assert outcome['termination'] == {'trigger': 'expiry', 'reason': 'lab_heartbeat_failed'}
        assert outcome['termination_receipt']['trigger'] == 'expiry'
        assert [row['kind'] for row in device.effects] == ['local_stop']
    finally:
        bridge.close()


def test_cancel_revoke_only_does_not_run_a_different_release_stop_policy(tmp_path):
    selected = binding(local_stop=payload('OFF\n'), lease_policy={
        'on_release': 'local_stop', 'on_cancel': 'revoke_only'})
    bridge, control, device, _ = setup(tmp_path, gateway_binding=selected)
    try:
        bridge.start()
        heartbeat(tmp_path, control, cancelled=True).renew()
        outcome = bridge.close()
        assert outcome['termination_receipt']['trigger'] == 'cancel'
        assert outcome['termination_receipt']['action'] == 'revoke_only'
        assert outcome['termination_receipt']['host_state'] == 'recovery_required'
        assert device.effects == []
    finally:
        bridge.close()


@pytest.mark.parametrize('reason', ['wall_limit', 'manual_stop'])
def test_wall_limit_and_manual_heartbeat_stop_apply_cancel_policy(tmp_path, reason):
    bridge, control, device, _ = setup(tmp_path)
    try:
        bridge.start()
        beat = heartbeat(tmp_path, control)
        if reason == 'wall_limit':
            beat.deadline = time.monotonic()-1
            with pytest.raises(TimeoutError):
                beat.check()
            expected = 'selected_execution_deadline_exceeded'
        else:
            beat.stop()
            expected = 'lab_heartbeat_stopped'
        outcome = bridge.close()
        assert outcome['termination'] == {'trigger': 'cancel', 'reason': expected}
        assert outcome['termination_receipt']['trigger'] == 'cancel'
        assert [row['kind'] for row in device.effects] == ['local_stop']
    finally:
        bridge.close()


def test_normal_completion_keeps_release_and_first_termination_is_immutable(tmp_path):
    bridge, control, device, _ = setup(tmp_path)
    try:
        bridge.start()
        control.revoke('supervisor_process_exited', termination='release')
        control.revoke('late_server_cancellation', termination='cancel')
        outcome = bridge.close()
        assert outcome['termination'] == {'trigger': 'release', 'reason': 'supervisor_process_exited'}
        assert outcome['release_receipt']['trigger'] == 'release'
        assert outcome['release_receipt']['host_state'] == 'idle'
        assert outcome['termination_receipt'] == outcome['release_receipt']
        assert device.effects == []
    finally:
        bridge.close()


@pytest.mark.parametrize('attempt_deadline,observed_at,expected', [
    (None, 111., 'expiry'), (105., 111., 'cancel'), (125., 130., 'expiry'), (110., 111., 'cancel')])
def test_observed_attempt_and_authority_deadlines_keep_distinct_termination(attempt_deadline, observed_at, expected):
    now = [100.]
    identity = AttemptIdentity('http://127.0.0.1', 'run', 'attempt', 'target', 'a'*64)
    control = AttemptControl(identity, monotonic=lambda: now[0], deadline=attempt_deadline)
    control.renew(10, sent_at=100.)
    now[0] = observed_at
    state = control.snapshot()
    assert state['termination_trigger'] == expected
    first = state['revocation_reason']
    control.revoke('cleanup', termination='release')
    assert control.snapshot()['termination_reason'] == first
    assert control.snapshot()['termination_trigger'] == expected


@pytest.mark.parametrize('attempt_deadline,expected', [(None, 'expiry'), (105., 'cancel')])
def test_normal_child_exit_cannot_latch_release_after_a_known_deadline(tmp_path, attempt_deadline, expected):
    selected = binding(local_stop=payload('OFF\n'), lease_policy={
        'on_release': 'revoke_only', 'on_cancel': 'local_stop', 'on_expiry': 'local_stop'})
    bridge, original, device, _ = setup(tmp_path, gateway_binding=selected)
    now = [100.]
    control = AttemptControl(original.identity, monotonic=lambda: now[0], deadline=attempt_deadline)
    control.renew(10, sent_at=100.)
    bridge.control = control
    try:
        bridge.start()
        now[0] = 111.
        # No snapshot/check between the elapsed bound and Runner's exit latch.
        control.revoke('supervisor_process_exited', termination='release')
        outcome = bridge.close()
        assert outcome['termination']['trigger'] == expected
        assert outcome['termination_receipt']['trigger'] == expected
        assert [row['kind'] for row in device.effects] == ['local_stop']
    finally:
        bridge.close()


@pytest.mark.parametrize('clock_value,reason', [(99., 'monotonic_clock_regressed'),
                                              (float('nan'), 'monotonic_clock_invalid')])
def test_first_explicit_revocation_preserves_a_conservative_clock_failure(clock_value, reason):
    now = [100.]
    identity = AttemptIdentity('http://127.0.0.1', 'run', 'attempt', 'target', 'a'*64)
    control = AttemptControl(identity, monotonic=lambda: now[0])
    control.renew(10, sent_at=100.)
    now[0] = clock_value
    control.revoke('supervisor_process_exited', termination='release')
    assert control.snapshot()['revocation_reason'] == reason
    assert control.snapshot()['termination_trigger'] == 'expiry'
    control.revoke('server_cancelled', termination='cancel')
    assert control.snapshot()['termination_reason'] == reason


def test_inflight_cancellation_receipt_is_reused_without_another_cleanup_stop(tmp_path):
    bridge, control, device, _ = setup(tmp_path)
    try:
        bridge.start()
        device.before_write = lambda: control.revoke('server_requested_cancellation', termination='cancel')
        with pytest.raises(Exception):
            bridge.submit('cancel-during-setup', payload(), valid_for_seconds=5)
        prior = bridge.gateway.lease_events()[0]['receipt']
        outcome = bridge.close()
        assert outcome['termination_receipt'] == prior
        assert [row['kind'] for row in device.effects] == ['local_stop']
    finally:
        bridge.close()


def test_old_bridge_cancellation_never_stops_or_revokes_a_newer_owner(tmp_path, monkeypatch):
    bridge, control, device, _ = setup(tmp_path)
    try:
        bridge.start()
        old = bridge._lease
        bridge.gateway.release('already-released', owner=old['owner'], epoch=old['epoch'])
        newer = bridge.gateway.acquire('newer-owner', ttl=20)
        heartbeat(tmp_path, control, cancelled=True).renew()
        observed = []
        close = bridge.gateway.close

        def capture():
            observed.append(bridge.gateway.snapshot())
            close()

        monkeypatch.setattr(bridge.gateway, 'close', capture)
        outcome = bridge.close()
        assert outcome['termination_receipt']['trigger'] == 'release'
        assert observed[0]['state'] == 'owned' and observed[0]['owner'] == newer['owner']
        assert observed[0]['epoch'] == newer['epoch'] and device.effects == []
    finally:
        bridge.close()


def test_unknown_stop_is_retained_and_close_never_retries_it(tmp_path):
    bridge, control, device, _ = setup(tmp_path)
    try:
        bridge.start()
        device.failure = RuntimeError('owned reply loss after write')
        heartbeat(tmp_path, control, cancelled=True).renew()
        outcome = bridge.close()
        assert outcome['errors'] and outcome['termination_receipt']['state'] == 'unknown'
        assert [row['kind'] for row in device.effects] == ['local_stop']
        assert bridge.close() == outcome
        assert len(device.effects) == 1
    finally:
        bridge.close()


def test_normal_close_finishes_only_its_already_observed_gateway_expiry(tmp_path):
    selected = binding(local_stop=payload('OFF\n'), lease_policy={
        'on_release': 'revoke_only', 'on_expiry': 'local_stop'})
    bridge, control, device, _ = setup(tmp_path, gateway_binding=selected)
    try:
        bridge.start()
        # Gateway wall-clock expiry and parent monotonic authority are distinct.
        bridge.gateway._clock = lambda: bridge._lease['expires_at']+1
        control.revoke('supervisor_process_exited', termination='release')
        outcome = bridge.close()
        assert outcome['termination']['trigger'] == 'release'
        assert outcome['termination_receipt']['trigger'] == 'expiry'
        assert outcome['termination_receipt']['state'] == 'completed'
        assert [row['kind'] for row in device.effects] == ['local_stop']
    finally:
        bridge.close()


def test_actual_runner_child_cancelled_between_submissions_retains_selected_stop(tmp_path):
    """Real child and pySerial loop; the Lab server response is controlled."""
    pytest.importorskip('serial')
    import httpx
    from tinystudio.lab import runner as runner_module
    from tinystudio.lab.contracts import ExperimentDefinition, checksum
    from tinystudio.lab.hardware_session_configuration import SessionBridgePaths
    from tinystudio.lab.service import runtime_versions
    from tests.test_lab_runner import Transport, manifest

    state = tmp_path/'private-state'
    state.mkdir(mode=0o700)
    source = '''import time
from pathlib import Path
def program(session, context):
    session.submit('before-cancel', {'operation': 'serial.write', 'data': {'encoding': 'hex', 'value': '0001ff'}}, valid_for_seconds=5)
    deadline = time.monotonic()+10
    while not Path(context['supervisor_parameters']['cancel_marker']).exists():
        if time.monotonic()>deadline:
            raise RuntimeError('owned server cancellation was not delivered')
        time.sleep(.005)
    return {'metrics': {'submitted': 1}}
'''.encode()
    definition = ExperimentDefinition(name='Owned cancellation after receipt', engine='hardware.session',
        operating_mode='simulation', inputs=[{'artifact_id': 'supervisor', 'sha256': hashlib.sha256(source).hexdigest()}],
        parameters={'binding_id': 'owned-loop', 'supervisor': {'kind': 'python', 'artifact_id': 'supervisor', 'callable': 'program'},
            'supervisor_parameters': {'cancel_marker': str(tmp_path/'attempt'/'work'/'cancel')}},
        execution={'backend': 'native', 'profile': 'trusted_native', 'max_seconds': 30, 'max_output_bytes': 4*1024**2}).model_dump()
    # Freeze an owned package before measuring it; another review agent can then
    # work on the original engine without invalidating this attempt's manifest.
    package = tmp_path/'source'/'tinystudio'
    shutil.copytree(Path(runner_module.__file__).parents[1], package,
                    ignore=shutil.ignore_patterns('__pycache__', 'ui'))
    spec = {'schema_version': 1, 'definition': definition, 'implementation': manifest(package),
            'runtime': runtime_versions(), 'input_manifest': [{'id': 'supervisor', 'name': 'source-without-extension',
                'sha256': hashlib.sha256(source).hexdigest(), 'size': len(source)}]}
    attempt = {'run_id': 'owned-run', 'attempt_id': 'owned-attempt', 'lease_token': 'parent-only-lab-token',
               'spec': spec, 'spec_digest': checksum(spec)}
    selected = {'endpoint_id': 'owned-loop', 'operator_id': 'operator',
        'transport': {'kind': 'serial', 'port': 'loop://', 'baudrate': 9600, 'write_timeout': 1},
        'command_profile': {'format': 'tinystudio.native-command-profile.v1'}, 'send_timeout': 1,
        'local_stop': {'operation': 'serial.write', 'data': {'encoding': 'hex', 'value': '4f46460a'}}}
    profile = {'schema_version': 1, 'engine_grants': {'hardware.session': {'native_execution': True,
        'bindings': {'owned-loop': {'state_directory': str(state), 'gateway_binding': selected}}}}}
    root = tmp_path/'attempt'
    root.mkdir()

    class Server(Transport):
        def post(self, path, **kwargs):
            if path.endswith('/heartbeat'):
                ledger = SessionBridgePaths(SessionBridgePaths.evidence_in_work(root/'work'), root).decisions
                rows = [json.loads(line) for line in ledger.read_bytes().splitlines(keepends=True)
                        if line.endswith(b'\n')] if ledger.exists() else []
                cancel = any(row['kind'] == 'receipt' and row.get('decision_id') == 'before-cancel' for row in rows)
                return httpx.Response(200, json={'cancel_requested': cancel, 'lease_seconds': 60},
                    request=httpx.Request('POST', 'http://127.0.0.1'+path))
            return super().post(path, **kwargs)

    server = Server(blocks=[source])
    runner_module.Runner('http://127.0.0.1', 'owned-target', 'parent-only-target-token', client=server,
        source_root=package, profile_config=profile, heartbeat_interval=.02, process_grace=1).execute_attempt(attempt, root)
    assert server.finish['state'] == 'cancelled', server.finish
    result = server.finish['result']
    assert result['parent_session']['termination'] == {'trigger': 'cancel', 'reason': 'server_requested_cancellation'}
    stop = result['parent_session']['termination_receipt']
    assert stop['trigger'] == 'cancel' and stop['action'] == 'local_stop'
    assert stop['delivery_receipt']['physical_outcome'] == 'unverified'
    assert stop['delivery_receipt']['device_application_confirmed'] is False
    assert result['parent_session']['release_receipt'] is None
    parent = [blob for blob, meta in zip(server.uploads, server.upload_params) if meta['role'] == 'hardware_session_parent']
    assert parent and any(b'server_requested_cancellation' in blob for blob in parent)
    assert all(b'parent-only-lab-token' not in blob for blob in parent)
