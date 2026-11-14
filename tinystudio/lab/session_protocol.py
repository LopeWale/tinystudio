"""Length-prefixed finite JSON for an attempt-scoped local session channel."""
import struct

from .command_profiles import _json
from .gateway_configuration import decode_gateway_json


SESSION_FORMAT = 'tinystudio.hardware-session-ipc.v1'
FORMAT = SESSION_FORMAT


class SessionProtocolError(ValueError):
    pass


class SessionFrameEOF(EOFError):
    def __init__(self, phase, expected, received):
        super().__init__('session socket ended during ' + phase + ' frame')
        self.phase, self.expected, self.received = phase, expected, received


def _budget(maximum):
    if maximum is not None and (type(maximum) is not int or maximum < 0):
        raise SessionProtocolError('session frame byte budget must be nonnegative or None')


def encode_frame(value, *, maximum=None):
    _budget(maximum)
    try:
        if type(value) is not dict:
            raise ValueError('object required')
        body = _json(value).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise SessionProtocolError('session frame requires an encodable finite JSON object') from None
    if maximum is not None and len(body) > maximum:
        raise SessionProtocolError('session frame exceeds the selected byte budget')
    if len(body) > 0xFFFFFFFFFFFFFFFF:
        raise SessionProtocolError('session frame exceeds the unsigned 64-bit length protocol')
    return struct.pack('>Q', len(body)) + body


def _read_exact(connection, size, phase):
    body = bytearray()
    while len(body) < size:
        # This is an allocation chunk, not a frame-size limit. With no selected
        # budget, storage grows only for bytes actually received from the peer.
        chunk = connection.recv(min(size - len(body), 65536))
        if not chunk:
            raise SessionFrameEOF(phase, size, len(body))
        body.extend(chunk)
    return bytes(body)


def read_frame(connection, *, maximum=None):
    _budget(maximum)
    length = struct.unpack('>Q', _read_exact(connection, 8, 'header'))[0]
    if maximum is not None and length > maximum:
        raise SessionProtocolError('session frame exceeds the selected byte budget')
    body = _read_exact(connection, length, 'body')
    try:
        value = decode_gateway_json(body.decode('utf-8'))
        _json(value)  # Also rejects unencodable JSON surrogate strings.
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise SessionProtocolError('session frame requires unique-key encodable finite JSON') from None
    return value
