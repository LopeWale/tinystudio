"""IPC framing preserves authored JSON with selected operational byte budgets."""
import json
import socket
import struct

import pytest

from tinystudio.lab.session_protocol import encode_frame, read_frame, SessionFrameEOF, SessionProtocolError


class BytesSocket:
    def __init__(self, data):
        self.data = bytearray(data)
        self.requests = []

    def recv(self, count):
        self.requests.append(count)
        result = bytes(self.data[:count])
        del self.data[:count]
        return result


def test_frame_preserves_unicode_encoded_wire_large_integer_strings_and_trailing_content():
    value = {'payload': {'operation': 'serial.write', 'data': {'encoding': 'utf8', 'value': 'é\x00\r\n9007199254740993'}},
             'integer': 9007199254740993, 'nested': [True, None, -.125]}
    frame = encode_frame(value)
    assert struct.unpack('>Q', frame[:8])[0] == len(frame[8:])
    assert json.loads(frame[8:].decode()) == value
    stream = BytesSocket(frame + encode_frame({'second': True}))
    assert read_frame(stream) == value
    assert read_frame(stream) == {'second': True}


@pytest.mark.parametrize('maximum', [None, 2, 3])
def test_exact_or_unbounded_body_budget_accepts_an_empty_object(maximum):
    assert read_frame(BytesSocket(encode_frame({}, maximum=maximum)), maximum=maximum) == {}


@pytest.mark.parametrize('maximum', [0, 1])
def test_zero_and_exhausted_budget_are_honored_before_reading_body(maximum):
    stream = BytesSocket(struct.pack('>Q', 2) + b'{}')
    with pytest.raises(SessionProtocolError, match='budget'):
        read_frame(stream, maximum=maximum)
    assert stream.data == b'{}' and stream.requests == [8]
    with pytest.raises(SessionProtocolError, match='budget'):
        encode_frame({}, maximum=maximum)


def test_maximum_unsigned_declared_length_is_rejected_before_body_allocation_when_budgeted():
    stream = BytesSocket(struct.pack('>Q', 0xFFFFFFFFFFFFFFFF))
    with pytest.raises(SessionProtocolError, match='budget'):
        read_frame(stream, maximum=1024)
    assert stream.requests == [8]


@pytest.mark.parametrize('maximum', [-1, True, 1.5, '2'])
def test_invalid_byte_budget_is_rejected(maximum):
    with pytest.raises(SessionProtocolError):
        encode_frame({}, maximum=maximum)
    with pytest.raises(SessionProtocolError):
        read_frame(BytesSocket(b''), maximum=maximum)


@pytest.mark.parametrize('body', [b'', b'[]', b'true', b'{"a":1,"a":2}', b'{"a":{"x":1,"x":2}}',
    b'{"a":NaN}', b'{"a":1e999}', b'{}{}', b'{"a":"\xff"}', b'{"a":"\\ud800"}'])
def test_malformed_ambiguous_or_nonfinite_json_is_rejected(body):
    with pytest.raises(SessionProtocolError):
        read_frame(BytesSocket(struct.pack('>Q', len(body)) + body))


@pytest.mark.parametrize('value', [[], None, {1: 'key'}, {'x': float('nan')}, {'x': b'wire'}, {'x': (1, 2)}, {'x': '\ud800'}])
def test_encoder_accepts_only_exact_encodable_finite_json_objects(value):
    with pytest.raises(SessionProtocolError):
        encode_frame(value)


@pytest.mark.parametrize('data,phase,expected,received', [(b'', 'header', 8, 0), (b'\x00' * 3, 'header', 8, 3),
    (struct.pack('>Q', 5) + b'{}', 'body', 5, 2)])
def test_end_of_stream_reports_truncation_explicitly(data, phase, expected, received):
    with pytest.raises(SessionFrameEOF) as caught:
        read_frame(BytesSocket(data))
    assert (caught.value.phase, caught.value.expected, caught.value.received) == (phase, expected, received)


def test_actual_owned_socket_pair_receives_fragmented_header_and_body():
    import threading
    left, right = socket.socketpair()
    value = {'data': 'x' * 200_000 + '\n\x00'}
    frame = encode_frame(value)
    def send():
        with left:
            for offset in range(0, len(frame), 137):
                left.sendall(frame[offset:offset + 137])
    worker = threading.Thread(target=send)
    worker.start()
    try:
        right.settimeout(2)
        assert read_frame(right) == value
    finally:
        right.close()
        worker.join(2)
    assert not worker.is_alive()
