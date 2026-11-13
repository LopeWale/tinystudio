"""Explicit serial framing with bounded prefix retention and exact byte hashes."""
from dataclasses import dataclass
import hashlib
import math
import time

from .hardware_transports import SerialEndpoint


@dataclass(frozen=True)
class SerialFraming:
    kind: str = 'delimiter'
    delimiter: bytes = b'\n'
    frame_bytes: int | None = None

    def __post_init__(self):
        if self.kind=='delimiter':
            if not isinstance(self.delimiter,bytes) or not self.delimiter or self.frame_bytes is not None:
                raise ValueError('delimiter framing requires nonempty bytes and no fixed size')
        elif self.kind=='fixed_bytes':
            if self.delimiter or type(self.frame_bytes)is not int or self.frame_bytes<=0:
                raise ValueError('fixed-byte framing requires a positive size and no delimiter')
        else:raise ValueError('unsupported serial framing kind')

    @classmethod
    def from_dict(cls, value=None):
        value={} if value is None else value
        if not isinstance(value,dict):raise ValueError('serial framing must be an object')
        kind=value.get('kind','delimiter')
        if kind=='delimiter':
            if value.keys()-{'kind','delimiter_hex'}:raise ValueError('unsupported delimiter framing field')
            try:delimiter=bytes.fromhex(value.get('delimiter_hex','0a'))
            except (TypeError,ValueError) as exc:raise ValueError('delimiter_hex must encode bytes') from exc
            if not delimiter:raise ValueError('serial delimiter must not be empty')
            return cls(kind,delimiter)
        if kind=='fixed_bytes':
            if value.keys()-{'kind','frame_bytes'}:raise ValueError('unsupported fixed-byte framing field')
            size=value.get('frame_bytes')
            if type(size)is not int or size<=0:raise ValueError('frame_bytes must be a positive integer')
            return cls(kind,b'',size)
        raise ValueError('serial framing kind must be delimiter or fixed_bytes')

    def to_dict(self):
        return {'kind':self.kind,**({'delimiter_hex':self.delimiter.hex()} if self.kind=='delimiter' else {'frame_bytes':self.frame_bytes})}


class FramedSerialReader:
    """Consume a frame boundary independently of the retained prefix budget.

    A native timeout can split a frame into exact byte segments. Continuations
    carry the same frame identity and never masquerade as a new complete frame.
    SDK reads and port opening are not hard real-time operations.
    """
    def __init__(self,binding,timeout,max_frame,*,library=None,clock=time,cancelled=lambda:False,deadline=None):
        self.framing=SerialFraming.from_dict(binding.get('framing'))
        if max_frame is not None and (type(max_frame)is not int or max_frame<0):
            raise ValueError('serial prefix budget must be a nonnegative integer or null')
        self.maximum=max_frame;self.clock=clock;self.cancelled=cancelled;self.deadline=deadline
        self.frame_index=0;self._reset_frame()
        self.source_binding={'format':'tinystudio.serial-framing.v1','framing':self.framing.to_dict(),
                             'capture_scope':'new_serial_receive_segments',
                             'budget_scope':'prefix_per_logical_frame',
                             'disclosure':'Timeouts may split a logical frame; continuation segments remain explicitly incomplete for decoding.'}
        was_cancelled=cancelled()
        if was_cancelled or deadline is not None and clock.monotonic()>=deadline:
            from .engine import ExecutionCancelled
            error=ExecutionCancelled('serial collection cancelled before open') if was_cancelled else TimeoutError('serial collection deadline elapsed before open')
            error.source_binding=self.source_binding
            raise error
        self.port=SerialEndpoint.from_dict(binding['endpoint']).open(timeout,library=library)

    def _reset_frame(self):
        self._seen=0;self._captured=0;self._tail=b'';self._frame_hash=hashlib.sha256()

    def read(self,timeout):
        if isinstance(timeout,bool) or not isinstance(timeout,(float,int)) or not math.isfinite(timeout) or timeout<0:
            raise ValueError('serial timeout must be finite and nonnegative')
        until=self.clock.monotonic()+timeout
        offset=self._seen;prefix=bytearray();segment_hash=hashlib.sha256();size=0;complete=False;read_error=None
        while True:
            if self.cancelled():
                if size:break
                from .engine import ExecutionCancelled
                raise ExecutionCancelled('serial collection cancelled')
            now=self.clock.monotonic()
            if self.deadline is not None and now>=self.deadline:break
            if timeout and size and now>=until:break
            remaining=0 if timeout==0 else max(0,until-now)
            if self.deadline is not None:remaining=min(remaining,max(0,self.deadline-now))
            self.port.timeout=remaining
            try:chunk=bytes(self.port.read(1))
            except Exception as cause:
                if not size:raise
                read_error={'type':type(cause).__name__,'message':str(cause)}
                break
            if not chunk:break
            if len(chunk)!=1:raise ValueError('serial SDK read returned more bytes than requested')
            size+=1;self._seen+=1;segment_hash.update(chunk);self._frame_hash.update(chunk)
            if self.maximum is None or self._captured<self.maximum:
                prefix.extend(chunk);self._captured+=1
            if self.framing.kind=='fixed_bytes':complete=self._seen==self.framing.frame_bytes
            else:
                self._tail=(self._tail+chunk)[-len(self.framing.delimiter):]
                complete=self._tail==self.framing.delimiter
            if complete:break
        if not size:return None
        frame={'wire':bytes(prefix),'wire_scope':'serial_received_bytes',
               'wire_sha256':segment_hash.hexdigest(),'original_wire_bytes':size,
               'evidence_truncated':len(prefix)<size,
               'truncated':len(prefix)<size or not complete or offset>0,
               'frame_index':self.frame_index,'frame_offset':offset,'frame_bytes_seen':self._seen,
               'frame_complete':complete,'frame_continuation':offset>0,
               'frame_sha256_through_segment':self._frame_hash.hexdigest()}
        if read_error is not None:
            frame['fatal_transport_error']=read_error
            frame['protocol_error']='native serial read failed after receiving a partial frame'
        if complete:self.frame_index+=1;self._reset_frame()
        return frame

    def close(self):
        self.port.close()
