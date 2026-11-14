"""Owned threads call a fake gateway; no sockets, drivers, or devices are opened."""
import threading
import time

import pytest

from tinystudio.lab.gateway_lease_policy import GatewayLeasePolicy
from tinystudio.lab.gateway_watchdog import GatewayWatchdog


class Gateway:
    def __init__(self, interval=None):
        self.lease_policy = GatewayLeasePolicy(watchdog_interval_seconds=interval)
        self.calls = 0
        self.second_tick = threading.Event()
        self.first_tick_at = None

    def watchdog_tick(self):
        self.calls += 1
        if self.first_tick_at is None:
            self.first_tick_at = time.monotonic()
        if self.calls >= 2:
            self.second_tick.set()

    def local_stop(self, *args, **kwargs):
        pytest.fail('the worker must not select or resend a stop action')


def test_construction_and_disabled_start_do_not_create_threads_or_tick(monkeypatch):
    import tinystudio.lab.gateway_watchdog as module
    monkeypatch.setattr(module.threading, 'Thread', lambda **kwargs: pytest.fail('disabled watchdog must not create a thread'))
    gateway = Gateway()
    worker = GatewayWatchdog(gateway)
    assert worker.snapshot()['selected_interval_seconds'] is None
    worker.start()
    assert gateway.calls == 0 and worker.snapshot()['alive'] is False
    worker.close()
    assert worker.snapshot()['stop_requested'] is True


def test_enabled_worker_waits_selected_interval_and_periodically_ticks_only_its_gateway(monkeypatch):
    import tinystudio.lab.gateway_watchdog as module
    gateway = Gateway(.01)
    actual_thread = threading.Thread
    created = []
    def thread(**kwargs):
        value = actual_thread(**kwargs)
        created.append(value)
        return value
    monkeypatch.setattr(module.threading, 'Thread', thread)
    worker = GatewayWatchdog(gateway)
    assert gateway.calls == 0 and created == []
    started = time.monotonic()
    try:
        assert worker.start() is worker
        assert worker.start() is worker
        assert gateway.second_tick.wait(1), 'owned worker did not execute two ticks'
    finally:
        worker.close(timeout=1)
    state = worker.snapshot()
    assert len(created) == 1
    assert gateway.first_tick_at - started >= .009
    assert state['selected_interval_seconds'] == .01
    assert state['tick_count'] >= 2 and state['successful_ticks'] >= 2
    assert state['error_count'] == 0 and state['last_error'] is None
    assert state['alive'] is False and state['stop_requested'] is True


def test_explicit_policy_is_used_without_changing_the_gateway_default():
    gateway = Gateway()
    worker = GatewayWatchdog(gateway, policy=GatewayLeasePolicy(watchdog_interval_seconds=.005))
    try:
        worker.start()
        assert gateway.second_tick.wait(1)
    finally:
        worker.close(timeout=1)
    assert gateway.lease_policy.watchdog_interval_seconds is None
    assert worker.snapshot()['selected_interval_seconds'] == .005


def test_tick_exception_is_reported_without_leaking_exception_text_or_direct_effect_retries():
    class ErrorGateway(Gateway):
        def watchdog_tick(self):
            super().watchdog_tick()
            if self.calls == 1:
                raise RuntimeError('private-runtime-secret-sentinel')
    gateway = ErrorGateway(.005)
    worker = GatewayWatchdog(gateway)
    try:
        worker.start()
        assert gateway.second_tick.wait(1)
    finally:
        worker.close(timeout=1)
    state = worker.snapshot()
    assert state['error_count'] == 1 and state['last_error']['type'] == 'RuntimeError'
    assert state['successful_ticks'] >= 1 and not state['alive']
    assert 'private-runtime-secret-sentinel' not in str(state)


def test_returned_unknown_outcome_remains_a_core_result_without_worker_interpretation():
    class UnknownGateway(Gateway):
        def watchdog_tick(self):
            super().watchdog_tick()
            return {'state': 'unknown', 'physical_outcome': 'claimed_by_adapter', 'private_context': 'sentinel'}
    gateway = UnknownGateway(.005)
    worker = GatewayWatchdog(gateway)
    try:
        worker.start()
        assert gateway.second_tick.wait(1)
    finally:
        worker.close(timeout=1)
    state = worker.snapshot()
    assert state['physical_outcome'] == 'unverified' and state['error_count'] == 0
    assert 'sentinel' not in str(state) and 'claimed_by_adapter' not in str(state)


def test_fatal_tick_exit_is_reported_without_silent_worker_restart():
    entered = threading.Event()
    class ExitGateway(Gateway):
        def watchdog_tick(self):
            self.calls += 1
            entered.set()
            raise SystemExit('private-exit-sentinel')
    gateway = ExitGateway(.001)
    worker = GatewayWatchdog(gateway)
    try:
        worker.start()
        assert entered.wait(1)
    finally:
        worker.close(timeout=1)
    state = worker.snapshot()
    assert state['alive'] is False and state['error_count'] == 1 and gateway.calls == 1
    assert state['last_error'] == {'type': 'SystemExit', 'phase': 'watchdog_tick', 'fatal': True}
    assert 'private-exit-sentinel' not in str(state)


def test_thread_start_failure_propagates_and_remains_visible_in_snapshot(monkeypatch):
    def unavailable(thread):
        raise RuntimeError('private-start-failure-sentinel')
    monkeypatch.setattr(threading.Thread, 'start', unavailable)
    worker = GatewayWatchdog(Gateway(.005))
    with pytest.raises(RuntimeError, match='private-start-failure-sentinel'):
        worker.start()
    state = worker.snapshot()
    assert state['alive'] is False and state['tick_count'] == 0
    assert state['last_error'] == {'type': 'RuntimeError', 'phase': 'thread_start', 'fatal': True}
    assert 'private-start-failure-sentinel' not in str(state)
    worker.close()


def test_worker_can_request_its_own_shutdown_without_joining_itself():
    entered = threading.Event()
    class ClosingGateway(Gateway):
        def watchdog_tick(self):
            worker.close()
            entered.set()
    worker = GatewayWatchdog(ClosingGateway(.001))
    try:
        worker.start()
        assert entered.wait(1)
    finally:
        worker.close(timeout=1)
    assert not worker.snapshot()['alive'] and worker.snapshot()['error_count'] == 0


def test_bounded_close_reports_a_still_running_tick_and_can_join_it_later():
    entered, release = threading.Event(), threading.Event()
    class BlockedGateway(Gateway):
        def watchdog_tick(self):
            entered.set()
            assert release.wait(2), 'owned test must release its blocked tick'
    worker = GatewayWatchdog(BlockedGateway(.001))
    try:
        worker.start()
        assert entered.wait(1)
        worker.close(timeout=0)
        assert worker.snapshot()['stop_requested'] is True
        assert worker.snapshot()['alive'] is True
    finally:
        release.set()
        worker.close(timeout=1)
    assert worker.snapshot()['alive'] is False


def test_close_interrupts_a_selected_interval_larger_than_native_wait_limits():
    gateway = Gateway(10 ** 30)
    worker = GatewayWatchdog(gateway)
    worker.start()
    worker.close(timeout=1)
    state = worker.snapshot()
    assert gateway.calls == 0 and state['error_count'] == 0
    assert state['alive'] is False


def test_a_closed_worker_cannot_be_restarted():
    worker = GatewayWatchdog(Gateway(.005))
    worker.close()
    with pytest.raises(RuntimeError, match='closed'):
        worker.start()


@pytest.mark.parametrize('policy', [{}, False, 'policy'])
def test_policy_must_be_the_validated_policy_value_object(policy):
    with pytest.raises(ValueError, match='policy'):
        GatewayWatchdog(Gateway(), policy=policy)


@pytest.mark.parametrize('timeout', [-1, True, '1', float('nan'), float('inf')])
def test_close_rejects_ambiguous_or_nonfinite_join_timeouts(timeout):
    worker = GatewayWatchdog(Gateway())
    with pytest.raises(ValueError, match='timeout'):
        worker.close(timeout=timeout)
