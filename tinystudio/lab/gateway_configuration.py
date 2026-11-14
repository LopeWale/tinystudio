"""Importable gateway paths and operator-selected configuration read budgets."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat


_OPERATIONS={'state':'GET','journal':'GET','command-profile':'GET','acquire':'POST',
             'heartbeat':'POST','commands':'POST','stop':'POST','reconcile':'POST'}


def decode_gateway_json(data):
    """Decode a finite JSON object without silently losing duplicate fields."""
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('duplicate gateway JSON field')
            value[key]=item
        return value
    def nonfinite(value):raise ValueError('gateway JSON contains a nonfinite number')
    value=json.loads(data,object_pairs_hook=unique,parse_constant=nonfinite)
    if not isinstance(value,dict):raise ValueError('gateway JSON must contain an object')
    # Exponents such as 1e999 overflow the native parser without invoking its
    # parse_constant hook. Reject them rather than retaining an Infinity value.
    json.dumps(value,allow_nan=False)
    return value


@dataclass(frozen=True)
class GatewayApiPaths:
    base: str = ''

    def route(self,name):
        if name not in _OPERATIONS:
            raise ValueError('unknown gateway operation')
        return self.base+'/'+name

    @property
    def command_profile(self):return self.route('command-profile')

    @property
    def mutation_routes(self):return frozenset(self.route(name) for name,method in _OPERATIONS.items() if method=='POST')


@dataclass(frozen=True)
class GatewayStoragePaths:
    journal: Path

    @classmethod
    def in_directory(cls,root):return cls(Path(root)/'gateway.sqlite3')

    @property
    def journal_lock(self):return self.journal.with_suffix(self.journal.suffix+'.lock')

    def endpoint_lock(self,identity):
        encoded=json.dumps(identity,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        return self.journal.parent/('.'+hashlib.sha256(encoded).hexdigest()+'.endpoint.lock')


@dataclass(frozen=True)
class GatewayByteBudget:
    max_bytes: int | None = None

    def __post_init__(self):
        if self.max_bytes is not None and (type(self.max_bytes) is not int or self.max_bytes<0):
            raise ValueError('gateway byte budget must be nonnegative or None')


@dataclass(frozen=True)
class GatewayFilePolicy(GatewayByteBudget):

    @classmethod
    def from_environment(cls):
        value=os.environ.get('TINYSTUDIO_GATEWAY_CONFIG_BYTES','').strip()
        return cls(None if not value else int(value))

    def read(self,path):
        try:descriptor=os.open(Path(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except OSError as error:raise ValueError('gateway configuration must be an existing readable non-symlink file') from error
        try:
            before=os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1
                    or stat.S_IMODE(before.st_mode)!=0o600 or before.st_uid!=os.geteuid()):
                raise ValueError('gateway configuration must be an operator-owned regular file with mode 0600')
            chunks=[];size=0
            while block:=os.read(descriptor,65536):
                size+=len(block)
                if self.max_bytes is not None and size>self.max_bytes:
                    raise ValueError('gateway configuration exceeds its selected byte budget')
                chunks.append(block)
            after=os.fstat(descriptor)
            if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
                    !=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
                    or size!=after.st_size):
                raise ValueError('gateway configuration changed during reading')
        finally:os.close(descriptor)
        return decode_gateway_json(b''.join(chunks))


class GatewayRequestTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class GatewayRequestPolicy(GatewayByteBudget):
    @classmethod
    def from_environment(cls):
        value=os.environ.get('TINYSTUDIO_GATEWAY_HTTP_BYTES','').strip()
        return cls(None if not value else int(value))

    def check_size(self,size):
        if self.max_bytes is not None and size>self.max_bytes:
            raise GatewayRequestTooLarge('gateway HTTP body exceeds its selected byte budget')
