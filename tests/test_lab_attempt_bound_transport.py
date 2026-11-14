"""Parent control composes with the actual native write/cleanup contract."""
from copy import deepcopy
import threading

import pytest

from tests.test_lab_attempt_control import Clock, identity
from tests.test_lab_native_command_transports import binding, request
from tinystudio.lab.attempt_bound_transport import AttemptBoundTransport
from tinystudio.lab.attempt_control import AttemptAuthorityLost, AttemptControl
from tinystudio.lab.gateway import CommandGateway, GatewayError
from tinystudio.lab.native_command_transports import NativeCommandTransport, NativeCommandTransportError


class Socket:
    def __init__(self, *, setup=None, after_write=None, partial=False):
        self.writes, self.closed = [], False
        self.after_write, self.partial = after_write, partial
        if setup: setup()
    def settimeout(self, value): pass
    def send(self, value):
        accepted = value[:2] if self.partial else value
        self.writes.append(accepted)
        if self.after_write: self.after_write()
        return len(accepted)
    def close(self): self.closed = True


def selected(control):
    config = binding()
    command = request(config, 'tcp.write', data={'encoding': 'hex', 'value': '00ff0a0d31'})
    command['owner'] = control.identity.owner
    return config, command


def active():
    control = AttemptControl(identity(), monotonic=Clock())
    control.renew(20, sent_at=100.)
    return control


def test_wrapper_preserves_exact_request_and_receipt_without_retries_or_mutation():
    control, seen = active(), []
    receipt = {'original': ['receipt'], 'physical_outcome': 'unverified'}
    class Inner:
        kind = 'custom'
        def exchange(self, supplied, *, timeout, cancelled, authorize):
            seen.append((supplied, timeout))
            assert not cancelled()
            assert authorize() is supplied
            return receipt
    wrapper = AttemptBoundTransport(Inner(), control)
    _, command = selected(control)
    original = deepcopy(command)
    assert wrapper.kind == 'custom'
    assert wrapper.exchange(command, timeout=.3, cancelled=lambda: False, authorize=lambda: command) is receipt
    assert seen == [(command, .3)] and command == original


def test_a_revoked_or_wrong_owner_request_does_not_even_enter_the_inner_transport():
    calls = []
    class Inner:
        kind = 'tcp'
        def exchange(self, *args, **kwargs): calls.append('entered')
    control = active()
    wrapper = AttemptBoundTransport(Inner(), control)
    _, command = selected(control)
    command['owner'] = 'a-different-attempt'
    with pytest.raises(AttemptAuthorityLost):
        wrapper.exchange(command, timeout=1, cancelled=lambda: False, authorize=lambda: command)
    assert control.check() is None
    control.revoke('cancelled')
    command['owner'] = control.identity.owner
    with pytest.raises(AttemptAuthorityLost):
        wrapper.exchange(command, timeout=1, cancelled=lambda: False, authorize=lambda: command)
    assert calls == []


def test_revocation_during_native_setup_produces_zero_ordinary_writes(monkeypatch):
    control = active()
    peer = Socket()
    def connect(*args, **kwargs):
        control.revoke('server_cancelled_during_setup')
        return peer
    monkeypatch.setattr('tinystudio.lab.native_command_transports.socket.create_connection', connect)
    config, command = selected(control)
    wrapper = AttemptBoundTransport(NativeCommandTransport(config), control)
    with pytest.raises(NativeCommandTransportError) as caught:
        wrapper.exchange(command, timeout=1, cancelled=lambda: False, authorize=lambda: command)
    assert peer.writes == [] and peer.closed is True
    assert caught.value.delivery_evidence['status'] == 'unknown'


def test_control_is_rechecked_after_gateway_authorize_before_the_native_write(monkeypatch):
    control, peer = active(), Socket()
    monkeypatch.setattr('tinystudio.lab.native_command_transports.socket.create_connection', lambda *a, **kw: peer)
    config, command = selected(control)
    def authorize():
        control.revoke('cancel_during_gateway_authorization')
        return command
    with pytest.raises(NativeCommandTransportError):
        AttemptBoundTransport(NativeCommandTransport(config), control).exchange(
            command, timeout=1, cancelled=lambda: False, authorize=authorize)
    assert peer.writes == [] and peer.closed is True


def test_partial_stream_acceptance_is_retained_and_revocation_prevents_the_next_write(monkeypatch):
    control = active()
    peer = Socket(partial=True, after_write=lambda: control.revoke('lease_connectivity_lost'))
    monkeypatch.setattr('tinystudio.lab.native_command_transports.socket.create_connection', lambda *a, **kw: peer)
    config, command = selected(control)
    with pytest.raises(NativeCommandTransportError) as caught:
        AttemptBoundTransport(NativeCommandTransport(config), control).exchange(
            command, timeout=1, cancelled=lambda: False, authorize=lambda: command)
    assert peer.writes == [b'\x00\xff'] and peer.closed is True
    retained = caught.value.delivery_evidence['evidence']['written']
    assert retained['size'] == 2 and retained['base64'] == 'AP8='
    assert caught.value.delivery_evidence['operational']['application_write_attempts'] == 1


def test_gateway_authorization_is_outside_the_control_lock():
    control = active()
    entered, released = threading.Event(), threading.Event()
    denied = []
    class Inner:
        kind = 'custom'
        def exchange(self, supplied, *, timeout, cancelled, authorize): return authorize()
    _, command = selected(control)
    def authorize():
        entered.set()
        assert released.wait(2)
        return command
    def exchange():
        try:
            AttemptBoundTransport(Inner(), control).exchange(command, timeout=1, cancelled=lambda: False, authorize=authorize)
        except AttemptAuthorityLost: denied.append(True)
    thread = threading.Thread(target=exchange)
    thread.start()
    try:
        assert entered.wait(2)
        control.revoke('cancel_while_gateway_waits')
        released.set()
        thread.join(2)
        assert not thread.is_alive() and denied == [True]
    finally:
        released.set()
        thread.join(2)


def test_scoped_gateway_stop_still_uses_its_exact_payload_and_final_fence_after_revocation(tmp_path, monkeypatch):
    control, peer = active(), Socket()
    config, _ = selected(control)
    config['local_stop'] = {'operation': 'tcp.write', 'data': {'encoding': 'utf8', 'value': 'STOP\r\n'}}
    monkeypatch.setattr('tinystudio.lab.native_command_transports.socket.create_connection', lambda *a, **kw: peer)
    wrapper = AttemptBoundTransport(NativeCommandTransport(config), control)
    with CommandGateway(tmp_path / 'gateway.sqlite3', config, wrapper) as gateway:
        gateway.acquire(control.identity.owner, ttl=20)
        control.revoke('parent_cancelled')
        receipt = gateway.local_stop(config['operator_id'], request_id='operator-stop')
        assert receipt['status'] == 'delivered_unverified'
        assert receipt['physical_outcome'] == 'unverified' and receipt['device_application_confirmed'] is False
        assert peer.writes == [b'STOP\r\n'] and peer.closed is True
        assert gateway.local_stop(config['operator_id'], request_id='operator-stop') == receipt
        assert peer.writes == [b'STOP\r\n']


def test_stop_does_not_bypass_a_gateway_authorization_rejection(monkeypatch):
    control, peer = active(), Socket()
    config, command = selected(control)
    config['local_stop'] = deepcopy(command['payload'])
    command = request(config, 'tcp.write', data={'encoding': 'hex', 'value': '00ff0a0d31'})
    command.update(kind='local_stop', owner=config['operator_id'])
    control.revoke('parent_cancelled')
    monkeypatch.setattr('tinystudio.lab.native_command_transports.socket.create_connection', lambda *a, **kw: peer)
    def deny(): raise GatewayError('old stop epoch is superseded')
    with pytest.raises(NativeCommandTransportError):
        AttemptBoundTransport(NativeCommandTransport(config), control).exchange(
            command, timeout=1, cancelled=lambda: False, authorize=deny)
    assert peer.writes == [] and peer.closed is True


def test_gateway_authorize_cannot_replace_the_bound_owner_or_promote_a_stop_to_a_command():
    control = active()
    _, command = selected(control)
    class Inner:
        kind = 'custom'
        def exchange(self, supplied, *, timeout, cancelled, authorize): return authorize()
    wrapper = AttemptBoundTransport(Inner(), control)
    with pytest.raises(AttemptAuthorityLost):
        wrapper.exchange(command, timeout=1, cancelled=lambda: False,
                         authorize=lambda: {**command, 'owner': 'other-attempt'})
    stop = {**command, 'kind': 'local_stop', 'owner': 'operator-one'}
    control.revoke('cancelled')
    with pytest.raises(AttemptAuthorityLost):
        wrapper.exchange(stop, timeout=1, cancelled=lambda: False, authorize=lambda: command)


def test_revocation_observation_in_cancelled_does_not_mutate_a_new_control():
    old = active()
    new = AttemptControl(identity(attempt_id='attempt-two'), monotonic=Clock())
    new.renew(20, sent_at=100.)
    seen = []
    class Inner:
        kind = 'custom'
        def exchange(self, supplied, *, timeout, cancelled, authorize):
            old.revoke('old_attempt_closed')
            seen.append(cancelled())
            return {'unchanged': True}
    _, command = selected(old)
    result = AttemptBoundTransport(Inner(), old).exchange(command, timeout=1, cancelled=lambda: False, authorize=lambda: command)
    assert result == {'unchanged': True} and seen == [True]
    assert new.check() is None


@pytest.mark.parametrize('selected_timeout,expected', [(3., 3.), (100., 5.), (0., 0.)])
def test_ordinary_timeout_uses_the_remaining_parent_budget_without_changing_request_or_receipt(selected_timeout, expected):
    clock = Clock(115.)
    control = AttemptControl(identity(), monotonic=clock)
    control.renew(20, sent_at=100.)
    observed = []
    receipt = {'exact': 'native receipt'}
    class Inner:
        kind = 'custom'
        def exchange(self, supplied, *, timeout, cancelled, authorize):
            observed.append(timeout)
            assert authorize() is supplied
            return receipt
    _, command = selected(control)
    original = deepcopy(command)
    returned = AttemptBoundTransport(Inner(), control).exchange(command, timeout=selected_timeout,
        cancelled=lambda: False, authorize=lambda: command)
    assert observed == [expected] and returned is receipt and command == original


def test_revoked_stop_keeps_its_selected_wait_and_still_honors_external_cancellation():
    control = active()
    control.revoke('parent_closed')
    seen = []
    class Inner:
        kind = 'custom'
        def exchange(self, supplied, *, timeout, cancelled, authorize):
            seen.append((timeout, cancelled()))
            assert authorize() is supplied
            return {'selected_stop': True}
    stop = {'kind': 'local_stop', 'owner': 'bound-operator', 'payload': {'operation': 'selected.stop'}}
    wrapper = AttemptBoundTransport(Inner(), control)
    assert wrapper.exchange(stop, timeout=99., cancelled=lambda: False, authorize=lambda: stop) == {'selected_stop': True}
    assert wrapper.exchange(stop, timeout=99., cancelled=lambda: True, authorize=lambda: stop) == {'selected_stop': True}
    assert seen == [(99., False), (99., True)]


def test_actual_native_receipt_reports_the_effective_parent_limited_wait(monkeypatch):
    control, peer = active(), Socket()
    monkeypatch.setattr('tinystudio.lab.native_command_transports.socket.create_connection', lambda *a, **kw: peer)
    config, command = selected(control)
    result = AttemptBoundTransport(NativeCommandTransport(config), control).exchange(
        command, timeout=1000., cancelled=lambda: False, authorize=lambda: command)
    assert result['operational']['selected_timeout'] == 20.
    assert peer.writes == [b'\x00\xff\n\r1'] and peer.closed is True
