"""Compose parent authority with the native gateway's exact write fence.

This adapter is called by the trusted parent/gateway, not exposed as a child
authorization API. Only the gateway may admit the fixed operator local stop;
this wrapper never creates, changes or grants authority for a stop request.
"""
from __future__ import annotations

import math

from .attempt_control import AttemptAuthorityLost, AttemptControl


class AttemptBoundTransport:
    def __init__(self, inner, control):
        if not isinstance(control, AttemptControl):
            raise ValueError('attempt-bound transport requires an AttemptControl')
        self._inner, self._control = inner, control

    @property
    def kind(self):
        return self._inner.kind

    def _ordinary(self, request):
        if not isinstance(request, dict) or request.get('kind') != 'command':
            raise AttemptAuthorityLost('ordinary attempt transport requires a command request')
        if request.get('owner') != self._control.identity.owner:
            raise AttemptAuthorityLost('command owner differs from the frozen attempt identity')
        self._control.check()

    def exchange(self, request, *, timeout, cancelled, authorize):
        is_stop = isinstance(request, dict) and request.get('kind') == 'local_stop'
        if not is_stop:
            self._ordinary(request)
            try:
                valid_timeout = type(timeout) in (int, float) and math.isfinite(timeout) and timeout >= 0
            except OverflowError:
                valid_timeout = False
            if not valid_timeout:
                raise ValueError('selected native timeout must be finite and nonnegative')
            timeout = min(timeout, self._control.remaining())

        def authorized():
            if not is_stop:
                self._ordinary(request)
            committed = authorize()  # No control lock is held across this call.
            if is_stop:
                if not isinstance(committed, dict) or committed.get('kind') != 'local_stop':
                    raise AttemptAuthorityLost('gateway did not authorize the selected local stop')
            else:
                self._ordinary(committed)
            return committed

        def should_cancel():
            if not is_stop:
                try:
                    self._ordinary(request)
                except AttemptAuthorityLost:
                    return True
            return cancelled()

        return self._inner.exchange(request, timeout=timeout, cancelled=should_cancel, authorize=authorized)
