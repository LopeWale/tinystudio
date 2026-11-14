"""Bounded observations of SDK callback buffers, without socket-wire claims.

PyModbus may report cumulative decoder buffers on receive. Callbacks are not
assumed to be disjoint transport chunks, successful sends, or physical traffic.
"""
from __future__ import annotations

import base64
import hashlib
import time


class RawModbusTrace:
    capture_scope = 'pymodbus_trace_callback_buffer'

    def __init__(self, maximum, *, clock=time, framing='tcp'):
        if maximum is not None and (type(maximum) is not int or maximum < 0):
            raise ValueError('trace maximum must be a nonnegative byte budget or None')
        if framing not in {'tcp', 'rtu_over_tcp'}:
            raise ValueError('trace framing must be tcp or rtu_over_tcp')
        self.maximum, self.clock, self.framing = maximum, clock, framing
        self.reset()

    def reset(self):
        self.receipts = []
        self.captured_bytes = {'tx': 0, 'rx': 0}
        self.observed_buffer_bytes = {'tx': 0, 'rx': 0}

    @property
    def truncated(self):
        return any(item['truncated'] for item in self.receipts)

    def observe(self, sending, buffer):
        direction = 'tx' if sending else 'rx'
        remaining = len(buffer) if self.maximum is None else max(0, self.maximum - self.captured_bytes[direction])
        prefix = buffer[:remaining]
        self.captured_bytes[direction] += len(prefix)
        self.observed_buffer_bytes[direction] += len(buffer)
        self.receipts.append({'direction': direction, 'capture_scope': self.capture_scope,
                              'framing': self.framing, 'size': len(buffer),
                              'sha256': hashlib.sha256(buffer).hexdigest(),
                              'prefix_base64': base64.b64encode(prefix).decode('ascii'),
                              'captured_bytes': len(prefix), 'truncated': len(prefix) < len(buffer),
                              'wall_time': self.clock.time(), 'monotonic': self.clock.monotonic()})
        return buffer

    def summary(self):
        return {'capture_scope': self.capture_scope,
                'budget_scope': 'aggregate_per_direction_for_callback_receipts',
                'observed_buffer_bytes': dict(self.observed_buffer_bytes),
                'captured_bytes': dict(self.captured_bytes), 'truncated': self.truncated,
                'transport_wire_reconstruction': False}
