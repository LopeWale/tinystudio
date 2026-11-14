"""Optional host worker; the gateway core owns durable lease/action decisions.

Ticks run serially after each selected interval. The worker does not interpret
delivery receipts or dispatch actions itself. A running gateway tick may outlast
the interval or a caller-selected join timeout; neither is a physical-stop bound.
"""
import threading
import time

from .gateway_lease_policy import GatewayLeasePolicy, _seconds


class GatewayWatchdog:
    def __init__(self, gateway, *, policy=None):
        selected = gateway.lease_policy if policy is None else policy
        if not isinstance(selected, GatewayLeasePolicy):
            raise ValueError('watchdog policy must be a GatewayLeasePolicy')
        self._gateway, self._policy = gateway, selected
        self._mutex = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._started = self._closed = False
        self._ticks = self._successful = self._errors = 0
        self._last_error = None

    @property
    def policy(self):
        return self._policy

    def _error(self, error, phase, *, fatal=False):
        # Exception text can contain runtime-only credentials or private paths.
        with self._mutex:
            self._errors += 1
            self._last_error = {'type': type(error).__name__, 'phase': phase, 'fatal': fatal}

    def start(self):
        with self._mutex:
            if self._closed:
                raise RuntimeError('watchdog is closed')
            if self._started:
                return self
            self._started = True
            if self._policy.watchdog_interval_seconds is None:
                return self
            self._thread = threading.Thread(target=self._run, name='tinystudio-gateway-watchdog', daemon=True)
            try:
                self._thread.start()
            except BaseException as error:
                self._errors += 1
                self._last_error = {'type': type(error).__name__, 'phase': 'thread_start', 'fatal': True}
                self._thread = None
                raise
        return self

    def _wait(self, interval):
        deadline = time.monotonic() + interval
        while not self._stop.is_set():
            remaining = max(0, deadline - time.monotonic())
            if self._stop.wait(min(remaining, threading.TIMEOUT_MAX)):
                return True
            if time.monotonic() >= deadline:
                return False
        return True

    def _run(self):
        try:
            while not self._wait(self._policy.watchdog_interval_seconds):
                if self._stop.is_set():
                    break
                with self._mutex:
                    self._ticks += 1
                try:
                    self._gateway.watchdog_tick()
                except Exception as error:
                    # Periodic checks continue; only the core may claim an action,
                    # and uncertain application effects must remain consumed there.
                    self._error(error, 'watchdog_tick')
                except BaseException as error:
                    self._error(error, 'watchdog_tick', fatal=True)
                    return
                else:
                    with self._mutex:
                        self._successful += 1
        except BaseException as error:
            self._error(error, 'worker_wait', fatal=True)

    def close(self, timeout=None):
        timeout = _seconds(timeout, 'watchdog join timeout', zero=True)
        with self._mutex:
            self._closed = True
            self._stop.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            if timeout is None:
                thread.join()
            else:
                deadline = time.monotonic() + timeout
                while True:
                    remaining = max(0, deadline - time.monotonic())
                    thread.join(min(remaining, threading.TIMEOUT_MAX))
                    if not thread.is_alive() or time.monotonic() >= deadline:
                        break
        return self.snapshot()

    def snapshot(self):
        with self._mutex:
            return {'selected_interval_seconds': self._policy.watchdog_interval_seconds,
                    'enabled': self._policy.watchdog_interval_seconds is not None,
                    'started': self._started, 'stop_requested': self._stop.is_set(),
                    'alive': self._thread is not None and self._thread.is_alive(),
                    'tick_count': self._ticks, 'successful_ticks': self._successful,
                    'error_count': self._errors,
                    'last_error': None if self._last_error is None else dict(self._last_error),
                    'schedule': 'selected_interval_before_first_tick_and_after_each_completed_tick',
                    'physical_outcome': 'unverified'}
