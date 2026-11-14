"""Attempt-scoped decision submission; lease and stop authority stay in the host."""
from copy import deepcopy
import json
import math
from pathlib import Path
import socket
import threading

from .session_protocol import SESSION_FORMAT, encode_frame, read_frame
from .command_profiles import _json


class HardwareSessionError(RuntimeError):
    def __init__(self, message, *, remote_type=None, delivery_receipt=None, lifecycle_receipt=None):
        super().__init__(message)
        self.remote_type = remote_type
        self.delivery_receipt = deepcopy(delivery_receipt)
        self.lifecycle_receipt = deepcopy(lifecycle_receipt)


def _seconds(value, *, optional=False, zero=False):
    if value is None:
        return optional
    try:
        return (type(value) in {int, float} and math.isfinite(value)
                and (value >= 0 if zero else value > 0))
    except (OverflowError, ValueError):
        return False


class HardwareSessionClient:
    def __init__(self, descriptor, *, timeout=None):
        try:
            if (type(descriptor) is not dict or set(descriptor) != {'format', 'path', 'capability', 'session_id', 'profile'}
                    or descriptor['format'] != SESSION_FORMAT):
                raise ValueError('descriptor')
            _json(descriptor)
            for name in ('path', 'capability', 'session_id'):
                if type(descriptor[name]) is not str or not descriptor[name] or '\0' in descriptor[name]:
                    raise ValueError('identity')
            if not Path(descriptor['path']).is_absolute() or type(descriptor['profile']) is not dict:
                raise ValueError('path/profile')
            if not _seconds(timeout, optional=True, zero=True):
                raise ValueError('timeout')
            self._path, self._capability = descriptor['path'], descriptor['capability']
            self._profile_json = _json(descriptor['profile'])
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise HardwareSessionError('invalid hardware session descriptor or timeout') from None
        self._timeout, self._closed = timeout, False
        self._mutex = threading.Lock()

    @property
    def profile(self):
        return json.loads(self._profile_json)

    def close(self):
        with self._mutex:
            self._closed = True

    def _redact(self, text):
        return text.replace(self._capability, '[REDACTED]')

    def _response(self, response):
        if response.get('ok') is True and set(response) == {'ok', 'receipt'} and type(response['receipt']) is dict:
            return response['receipt']
        if response.get('ok') is not False or set(response) != {'ok', 'error'}:
            raise HardwareSessionError('invalid hardware session response envelope')
        error = response['error']
        if (type(error) is not dict or not {'type', 'message'} <= error.keys()
                or error.keys() - {'type', 'message', 'delivery_receipt', 'lifecycle_receipt'}
                or type(error['type']) is not str or type(error['message']) is not str
                or any(error.get(key) is not None and type(error[key]) is not dict
                       for key in ('delivery_receipt', 'lifecycle_receipt'))):
            raise HardwareSessionError('invalid hardware session error envelope')
        raise HardwareSessionError(self._redact(error['message']), remote_type=self._redact(error['type']),
            delivery_receipt=error.get('delivery_receipt'), lifecycle_receipt=error.get('lifecycle_receipt'))

    def submit(self, decision_id, payload, *, valid_for_seconds):
        with self._mutex:
            if self._closed:
                raise HardwareSessionError('hardware session client is closed')
        try:
            if (type(decision_id) is not str or not decision_id or '\0' in decision_id
                    or type(payload) is not dict or not _seconds(valid_for_seconds)):
                raise ValueError('decision')
            frame = encode_frame({'capability': self._capability, 'decision_id': decision_id,
                                  'payload': payload, 'valid_for_seconds': valid_for_seconds})
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise HardwareSessionError('invalid hardware session decision') from None
        phase = 'connect'
        try:
            # Exactly one connection and submission: loss of a response does not
            # authorize replay of an application command through another socket.
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self._timeout)
                connection.connect(self._path)
                phase = 'submit'
                connection.sendall(frame)
                phase = 'response'
                response = read_frame(connection)
            return self._response(response)
        except HardwareSessionError:
            raise
        except Exception as error:
            raise HardwareSessionError('hardware session ' + phase + ' failed (' + type(error).__name__ + ')') from None
