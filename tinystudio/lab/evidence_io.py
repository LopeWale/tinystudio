"""Explicit JSONL write, flush and file-descriptor fsync acknowledgements."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import time


@dataclass(frozen=True)
class RecordWritePolicy:
    flush_records: int | None = 1
    fsync: bool = True

    def __post_init__(self):
        if self.flush_records is not None and (type(self.flush_records) is not int or self.flush_records < 1):
            raise ValueError('evidence.flush_records must be a positive integer or null for on-close flushing')
        if type(self.fsync) is not bool:
            raise ValueError('evidence.fsync must be a boolean')

    @classmethod
    def from_parameters(cls, parameters):
        if not isinstance(parameters, dict):
            raise ValueError('evidence parameters must be an object')
        supplied = parameters.get('evidence', {})
        if not isinstance(supplied, dict) or supplied.keys() - {'flush_records', 'fsync'}:
            raise ValueError('evidence policy must contain only flush_records and fsync')
        selected = supplied.get('flush_records', 1)
        if selected == 'on_close':
            selected = None
        return cls(flush_records=selected, fsync=supplied.get('fsync', True))

    def to_dict(self):
        return asdict(self)


class JsonlEvidenceWriter:
    """A successful newline write, flush and fsync are separate boundaries.

    Pending records have no explicit flush/sync acknowledgement. Python or the
    OS may have written some of those bytes before the selected boundary.
    """

    def __init__(self, path, policy, clock=time):
        if not isinstance(policy, RecordWritePolicy):
            raise ValueError('JSONL evidence writer requires a RecordWritePolicy')
        self.path, self.policy, self.clock = Path(path), policy, clock
        self._stream = None
        self._closed = False
        self._written = self._flushed = self._synced = 0
        self._flush_operations = self._sync_operations = 0
        self._last_boundary = None
        self._errors = []

    def _error(self, operation, cause):
        self._errors.append({'operation': operation, 'type': type(cause).__name__, 'message': str(cause),
                             'written_records': self._written, 'flushed_records': self._flushed,
                             'fsynced_records': self._synced})

    def __enter__(self):
        if self._stream is not None or self._closed:
            raise RuntimeError('JSONL evidence writer cannot be reopened')
        try:
            self._stream = self.path.open('wb')
        except Exception as cause:
            self._error('open', cause)
            raise
        return self

    def _boundary(self, kind, records):
        self._last_boundary = {'kind': kind, 'records': records,
                               'monotonic': self.clock.monotonic(), 'wall_time': self.clock.time()}

    def write_record(self, row):
        if self._stream is None or self._closed:
            raise RuntimeError('JSONL evidence writer must be open')
        try:
            encoded = (json.dumps(row, allow_nan=False, sort_keys=True) + '\n').encode('utf-8')
        except Exception as cause:
            self._error('serialize', cause)
            raise
        try:
            if self._stream.write(encoded) != len(encoded):
                raise OSError('JSONL record write was incomplete')
        except Exception as cause:
            self._error('write', cause)
            raise
        # Count completed writes before the separately fallible flush and fsync.
        self._written += 1
        if self.policy.flush_records is not None and self._written - self._flushed >= self.policy.flush_records:
            self.flush()

    def flush(self):
        if self._stream is None or self._closed:
            raise RuntimeError('JSONL evidence writer must be open')
        try:
            self._stream.flush()
        except Exception as cause:
            self._error('flush', cause)
            raise
        self._flushed = self._written
        self._flush_operations += 1
        self._boundary('flush', self._flushed)
        if self.policy.fsync:
            try:
                os.fsync(self._stream.fileno())
            except Exception as cause:
                self._error('fsync', cause)
                raise
            self._synced = self._flushed
            self._sync_operations += 1
            self._boundary('fsync', self._synced)

    def close(self):
        if self._closed:
            return
        failure = None
        if self._stream is not None:
            try:
                self.flush()
            except Exception as cause:
                failure = cause
            try:
                self._stream.close()
            except Exception as cause:
                self._error('close', cause)
                if failure is None:
                    failure = cause
                else:
                    failure.add_note(f'JSONL stream close also failed: {cause}')
            self._closed = self._stream.closed
        else:
            self._closed = True
        if failure is not None:
            raise failure

    def __exit__(self, kind, cause, traceback):
        try:
            self.close()
        except Exception as close_error:
            if cause is None:
                raise
            cause.add_note(f'JSONL evidence close failed: {close_error}')
        return False

    def receipt(self):
        return {'selected_policy': self.policy.to_dict(), 'written_records': self._written,
                'flushed_records': self._flushed, 'fsynced_records': self._synced,
                'pending_records': {'flush': self._written - self._flushed,
                                    'fsync': self._written - self._synced if self.policy.fsync else None},
                'flush_operations': self._flush_operations, 'fsync_operations': self._sync_operations,
                'closed': self._closed,
                'sync_scope': {'flush': 'python_buffer_to_operating_system',
                               'fsync': 'file_descriptor_os_fsync_acknowledgement' if self.policy.fsync else 'not_requested',
                               'parent_directory_synced': False, 'physical_power_loss_verified': False},
                'last_successful_boundary': dict(self._last_boundary) if self._last_boundary is not None else None,
                'errors': [dict(error) for error in self._errors],
                'disclosure': 'A completed newline write is separate from flush/fsync acknowledgement. Pending records '
                              'may have reached the OS without an explicit boundary. Successful file-descriptor fsync '
                              'does not establish parent-directory persistence, physical power-loss protection or '
                              'underlying local/remote storage behavior.'}
