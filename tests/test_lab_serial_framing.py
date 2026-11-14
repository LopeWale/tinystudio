"""Framing and prefix budgets are independent on an actual owned serial loop."""
import hashlib
import time
from types import SimpleNamespace

import pytest

from tinystudio.lab.acquisition import SerialReader
from tinystudio.lab.engine import ExecutionCancelled
from tinystudio.lab.serial_framing import SerialFraming
from tests.test_lab_acquisition import run, spec


def binding(framing=None):
    return {'endpoint':{'port':'loop://','baudrate':115200,'write_timeout':1},
            **({'framing':framing} if framing is not None else {})}


@pytest.mark.parametrize('maximum,prefix',[(None,b'long record\n'),(0,b''),(3,b'lon')])
def test_prefix_budget_consumes_whole_boundary_without_inventing_tail_measurement(maximum,prefix):
    pytest.importorskip('serial')
    reader=SerialReader(binding(),.1,maximum)
    try:
        reader.port.write(b'long record\n7\n')
        first=reader.read(0)
        assert first['wire']==prefix
        assert first['original_wire_bytes']==len(b'long record\n')
        assert first['wire_sha256']==hashlib.sha256(b'long record\n').hexdigest()
        assert first['frame_complete'] and first['frame_index']==0
        assert first['evidence_truncated'] is (maximum is not None and maximum<len(b'long record\n'))
        second=reader.read(0)
        assert second['wire']==(b'' if maximum==0 else b'7\n')
        assert second['frame_index']==1 and not second['frame_continuation']
        assert second['original_wire_bytes']==2
        assert reader.read(0) is None
    finally:reader.close()


def test_fixed_binary_frame_ignores_newline_and_preserves_timeout_continuation():
    pytest.importorskip('serial')
    reader=SerialReader(binding({'kind':'fixed_bytes','frame_bytes':4}),.1,None)
    try:
        reader.port.write(b'\x01\n\xff\x00ZZ')
        first=reader.read(0)
        assert first['wire']==b'\x01\n\xff\x00' and first['frame_complete']
        partial=reader.read(0)
        assert partial['wire']==b'ZZ' and partial['truncated'] and not partial['frame_complete']
        reader.port.write(b'AA')
        continuation=reader.read(0)
        assert continuation['wire']==b'AA' and continuation['frame_complete']
        assert continuation['frame_continuation'] and continuation['frame_offset']==2
        assert continuation['truncated'], 'a tail cannot be decoded as a fresh complete frame'
        assert continuation['frame_sha256_through_segment']==hashlib.sha256(b'ZZAA').hexdigest()
    finally:reader.close()


def test_delimiter_can_cross_reads_without_resetting_logical_prefix_budget():
    pytest.importorskip('serial')
    reader=SerialReader(binding({'kind':'delimiter','delimiter_hex':b'END'.hex()}),.1,3)
    try:
        reader.port.write(b'abE')
        first=reader.read(0)
        assert first['wire']==b'abE' and not first['frame_complete']
        reader.port.write(b'ND')
        last=reader.read(0)
        assert last['wire']==b'' and last['original_wire_bytes']==2
        assert last['frame_complete'] and last['frame_continuation'] and last['evidence_truncated']
        assert last['frame_sha256_through_segment']==hashlib.sha256(b'abEND').hexdigest()
    finally:reader.close()


@pytest.mark.parametrize('framing',[{'kind':'guessed'},{'delimiter_hex':''},{'delimiter_hex':'oops'},
    {'kind':'fixed_bytes','frame_bytes':0},{'kind':'fixed_bytes','frame_bytes':True},
    {'kind':'fixed_bytes','frame_bytes':1,'delimiter_hex':'0a'}])
def test_invalid_framing_rejects_before_open(framing):
    with pytest.raises(ValueError):
        SerialReader(binding(framing),.1,3,library=SimpleNamespace(Serial=lambda **kw:pytest.fail('must not open')))


@pytest.mark.parametrize('cancelled,deadline,error',[(True,None,ExecutionCancelled),(False,-1,TimeoutError)])
def test_cancelled_or_elapsed_selection_does_not_open_port(cancelled,deadline,error):
    with pytest.raises(error):
        SerialReader(binding(),.1,3,cancelled=lambda:cancelled,deadline=deadline,
                     library=SimpleNamespace(Serial=lambda **kw:pytest.fail('must not open')))


def test_partial_bytes_survive_native_read_exception():
    class Port:
        def __init__(self,**kw):self.calls=0
        def open(self):pass
        def close(self):pass
        def read(self,size):
            self.calls+=1
            if self.calls==1:return b'x'
            raise OSError('owned serial disconnect')
    reader=SerialReader(binding(),.1,3,library=SimpleNamespace(serial_for_url=lambda *a,**kw:Port()))
    try:
        frame=reader.read(.1)
        assert frame['wire']==b'x' and frame['original_wire_bytes']==1
        assert frame['fatal_transport_error']['message']=='owned serial disconnect'
        assert frame['truncated'] and not frame['frame_complete']
    finally:reader.close()


@pytest.mark.parametrize('delimiter,payload,decode,value', [
    (b'\x00', b'{"value":42}', 'json', 42),
    (b'END', b'-.125', 'decimal_text', -.125),
    (b'\r\n', b'{"value":-1.25}', 'json', -1.25),
])
def test_complete_delimited_frame_decodes_payload_and_retains_exact_wire(tmp_path, delimiter, payload, decode, value):
    import base64
    pytest.importorskip('serial')
    selected = {'kind': 'delimiter', 'delimiter_hex': delimiter.hex()}
    reader = SerialReader(binding(selected), .1, None)
    wire = payload + delimiter
    try:
        reader.port.write(wire)
        frame = reader.read(0)
    finally:
        reader.close()
    _, rows, raw, *_ = run(tmp_path, spec(samples=1, decode=decode), [frame])
    assert rows[0]['value'] == value and rows[0]['observed'] is True
    assert rows[0]['quality'] == 'good'
    assert base64.b64decode(raw[0]['wire_base64']) == wire
    assert raw[0]['transport_metadata']['wire_sha256'] == hashlib.sha256(wire).hexdigest()
    assert raw[0]['transport_metadata']['framing'] == selected
    assert raw[0]['transport_metadata']['frame_complete'] is True


def test_delimiter_split_by_timeout_never_decodes_a_tail_as_a_measurement(tmp_path):
    import base64
    pytest.importorskip('serial')
    reader = SerialReader(binding({'kind': 'delimiter', 'delimiter_hex': '454e44'}), .1, None)
    try:
        reader.port.write(b'42E')
        prefix = reader.read(0)
        reader.port.write(b'ND')
        tail = reader.read(0)
    finally:
        reader.close()
    _, rows, raw, *_ = run(tmp_path, spec(samples=2, decode='decimal_text'), [prefix, tail])
    assert all(row['value'] is None and row['observed'] is False for row in rows)
    assert all(row['quality'] == 'invalid' for row in rows)
    assert [base64.b64decode(row['wire_base64']) for row in raw] == [b'42E', b'ND']
    assert tail['frame_complete'] and tail['frame_continuation']


def test_fixed_size_frame_does_not_strip_payload_bytes_as_a_delimiter(tmp_path):
    pytest.importorskip('serial')
    wire = b'{"value":42}\x00'
    reader = SerialReader(binding({'kind': 'fixed_bytes', 'frame_bytes': len(wire)}), .1, None)
    try:
        reader.port.write(wire)
        frame = reader.read(0)
    finally:
        reader.close()
    _, rows, _, *_ = run(tmp_path, spec(samples=1), [frame])
    assert rows[0]['quality'] == 'invalid' and rows[0]['value'] is None
