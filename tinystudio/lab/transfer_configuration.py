"""Operational artifact budgets, R2 protocol geometry and pure scoped paths.

Importing this module reads no environment, files, storage configuration or SDK.
None is an unbounded operational budget; zero is a selected zero budget.
Provider protocol limits are separate from those host/operator decisions.
"""
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
import math
import os
from typing import ClassVar
from urllib.parse import quote


MIB = 1024 ** 2
GIB = 1024 ** 3
TIB = 1024 ** 4
# https://developers.cloudflare.com/r2/platform/limits/ (footnotes 2, 3, 4):
# the guide's rounded 5 TiB / 5 GiB are not the exact effective byte limits.
R2_LIMITS_SOURCE = 'https://developers.cloudflare.com/r2/platform/limits/'
R2_ROUNDED_GUIDE_MAXIMUM_OBJECT_BYTES = 5 * TIB
R2_ROUNDED_GUIDE_MAXIMUM_PART_BYTES = 5 * GIB
R2_MAXIMUM_OBJECT_BYTES = 5 * TIB - 5 * GIB
R2_MAXIMUM_PART_BYTES = 5 * GIB - 5 * MIB
R2_MAXIMUM_PARTS = 10_000
R2_MAXIMUM_KEY_BYTES = 1024
# https://developers.cloudflare.com/r2/objects/multipart-objects/: all parts
# except the last have the same size and are at least 5 MiB.
R2_MINIMUM_PART_BYTES = 5 * MIB
# https://developers.cloudflare.com/r2/api/s3/presigned-urls/: 1 second–7 days.
R2_MAXIMUM_SIGNED_URL_SECONDS = 604_800
TRANSFER_COLLECTION = 'transfers'


def _integer(value, name, *, minimum=0, optional=False):
    if value is None and optional:
        return None
    if type(value) is not int or value < minimum:
        raise ValueError(name + ' must be an integer within its explicit configuration/protocol range')
    return value


def _seconds(value, name):
    if value is None:
        return None
    try:
        valid = type(value) in {int, float} and math.isfinite(value) and value >= 0
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        raise ValueError(name + ' must be a finite nonnegative number or None')
    return value


class TransferBudgetExceeded(ValueError):
    def __init__(self, resource, maximum, actual):
        # Keep exact integers in attributes without imposing Python's decimal
        # string conversion limit on otherwise valid operational counts.
        super().__init__('transfer ' + resource + ' exceed the selected operational budget')
        self.resource, self.maximum, self.actual = resource, maximum, actual


@dataclass(frozen=True)
class TransferBudget:
    max_bytes: int | None = None
    max_files: int | None = None

    def __post_init__(self):
        _integer(self.max_bytes, 'max_bytes', optional=True)
        _integer(self.max_files, 'max_files', optional=True)

    def check_size(self, size):
        _integer(size, 'size')
        if self.max_bytes is not None and size > self.max_bytes:
            raise TransferBudgetExceeded('bytes', self.max_bytes, size)

    def check_count(self, count):
        _integer(count, 'count')
        if self.max_files is not None and count > self.max_files:
            raise TransferBudgetExceeded('files', self.max_files, count)

    def check_total(self, total_bytes, file_count=None):
        self.check_size(total_bytes)
        if file_count is None and self.max_files is not None:
            raise ValueError('file_count is required to enforce the selected transfer count budget')
        if file_count is not None:
            self.check_count(file_count)

    def as_dict(self):
        return {'max_bytes': self.max_bytes, 'max_files': self.max_files}


class MultipartGeometryError(ValueError):
    def __init__(self, message, *, minimum_required_part_bytes=None):
        super().__init__(message)
        self.minimum_required_part_bytes = minimum_required_part_bytes


def _part_bytes(value):
    _integer(value, 'part_bytes', minimum=1)
    if not R2_MINIMUM_PART_BYTES <= value <= R2_MAXIMUM_PART_BYTES:
        raise MultipartGeometryError('part_bytes must be within the exact R2 multipart protocol limits')


@dataclass(frozen=True)
class MultipartPlan:
    size: int
    part_bytes: int
    part_count: int = field(init=False)

    def __post_init__(self):
        _integer(self.size, 'object size')
        _part_bytes(self.part_bytes)
        if self.size > R2_MAXIMUM_OBJECT_BYTES:
            raise MultipartGeometryError('object exceeds the exact R2 object-size protocol limit')
        count = (self.size + self.part_bytes - 1) // self.part_bytes
        if count > R2_MAXIMUM_PARTS:
            needed = (self.size + R2_MAXIMUM_PARTS - 1) // R2_MAXIMUM_PARTS
            raise MultipartGeometryError('selected geometry exceeds R2 maximum parts; choose part_bytes of at least '
                                         + str(needed), minimum_required_part_bytes=needed)
        object.__setattr__(self, 'part_count', count)

    @property
    def zero(self):
        return self.size == 0

    @property
    def one_part(self):
        return self.part_count == 1

    @property
    def mode(self):
        # Empty objects require a separate empty PutObject completion path;
        # this plan does not promise that zero-part multipart completion works.
        return 'empty_put' if self.zero else 'multipart'

    def part(self, number):
        _integer(number, 'part number', minimum=1)
        if number > self.part_count:
            raise ValueError('part number is outside the multipart plan')
        offset = (number - 1) * self.part_bytes
        return {'offset': offset, 'size': min(self.part_bytes, self.size - offset)}

    def as_dict(self):
        return {'size': self.size, 'part_bytes': self.part_bytes, 'part_count': self.part_count,
                'zero': self.zero, 'one_part': self.one_part, 'mode': self.mode}


@dataclass(frozen=True)
class MultipartGeometry:
    part_bytes: int = 16 * MIB
    concurrency: int = 4
    minimum_part_bytes: ClassVar[int] = R2_MINIMUM_PART_BYTES
    maximum_part_bytes: ClassVar[int] = R2_MAXIMUM_PART_BYTES
    maximum_parts: ClassVar[int] = R2_MAXIMUM_PARTS
    maximum_object_bytes: ClassVar[int] = R2_MAXIMUM_OBJECT_BYTES

    def __post_init__(self):
        _part_bytes(self.part_bytes)
        _integer(self.concurrency, 'concurrency', minimum=1)

    def plan(self, size):
        return MultipartPlan(size, self.part_bytes)

    def as_dict(self):
        return {'part_bytes': self.part_bytes, 'concurrency': self.concurrency,
                'provider': {'name': 'cloudflare_r2', 'minimum_part_bytes': self.minimum_part_bytes,
                             'maximum_part_bytes': self.maximum_part_bytes, 'maximum_parts': self.maximum_parts,
                             'maximum_object_bytes': self.maximum_object_bytes, 'source': R2_LIMITS_SOURCE}}


def _environment_value(environment, key, default, *, optional=False, duration=False):
    if key not in environment:
        return default
    value = environment[key]
    if optional and (value is None or type(value) is str and value.strip().lower() in {'', 'none', 'null'}):
        return None
    if type(value) is not str or not value.strip():
        raise ValueError(key + ' must contain an explicit numeric configuration value')
    try:
        return float(value) if duration else int(value, 10)
    except (ValueError, OverflowError):
        raise ValueError(key + ' must contain an explicit numeric configuration value') from None


@dataclass(frozen=True)
class TransferPolicy:
    per_file: TransferBudget = field(default_factory=TransferBudget)
    aggregate: TransferBudget = field(default_factory=TransferBudget)
    geometry: MultipartGeometry = field(default_factory=MultipartGeometry)
    signed_url_expiry_seconds: int = 900
    verifier_timeout_seconds: float | None = None

    def __post_init__(self):
        if not isinstance(self.per_file, TransferBudget) or not isinstance(self.aggregate, TransferBudget):
            raise ValueError('transfer policies require TransferBudget values')
        if not isinstance(self.geometry, MultipartGeometry):
            raise ValueError('transfer policy requires MultipartGeometry')
        _integer(self.signed_url_expiry_seconds, 'signed_url_expiry_seconds', minimum=1)
        if self.signed_url_expiry_seconds > R2_MAXIMUM_SIGNED_URL_SECONDS:
            raise ValueError('signed URL expiry exceeds the R2 signing protocol maximum')
        _seconds(self.verifier_timeout_seconds, 'verifier_timeout_seconds')

    @classmethod
    def from_environment(cls, environment=None):
        environment = os.environ if environment is None else environment
        if not isinstance(environment, Mapping):
            raise ValueError('transfer environment must be a mapping')
        def selected(name, default=None, *, optional=False, duration=False):
            return _environment_value(environment, 'TINYSTUDIO_TRANSFER_' + name, default,
                                      optional=optional, duration=duration)
        return cls(per_file=TransferBudget(max_bytes=selected('MAX_FILE_BYTES', optional=True)),
                   aggregate=TransferBudget(max_bytes=selected('MAX_TOTAL_BYTES', optional=True),
                                            max_files=selected('MAX_FILES', optional=True)),
                   geometry=MultipartGeometry(part_bytes=selected('PART_BYTES', 16 * MIB),
                                              concurrency=selected('CONCURRENCY', 4)),
                   signed_url_expiry_seconds=selected('SIGNED_URL_SECONDS', 900),
                   verifier_timeout_seconds=selected('VERIFIER_TIMEOUT_SECONDS', optional=True, duration=True))

    def as_dict(self):
        return {'per_file': self.per_file.as_dict(), 'aggregate': self.aggregate.as_dict(),
                'geometry': self.geometry.as_dict(), 'signed_url_expiry_seconds': self.signed_url_expiry_seconds,
                'verifier_timeout_seconds': self.verifier_timeout_seconds}


def _segment(value):
    if (type(value) is not str or not value or value in {'.', '..'}
            or any(character in value for character in '/\\?#')
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError('transfer identities must be nonempty single path segments')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ValueError('transfer identities must be UTF-8 encodable') from None
    return value


@dataclass(frozen=True)
class TransferPaths:
    project_id: str
    session_id: str

    def __post_init__(self):
        _segment(self.project_id)
        _segment(self.session_id)
        if len(self.object_key.encode('utf-8')) > R2_MAXIMUM_KEY_BYTES:
            raise ValueError('transfer object key exceeds the R2 key-length protocol limit')

    @property
    def prefix(self):
        return 'lab/' + self.project_id + '/transfers/' + self.session_id

    @property
    def object_key(self):
        return self.prefix + '/object'

    @property
    def staging_key(self):
        return self.object_key


@dataclass(frozen=True)
class TransferApiPaths:
    base: str = '/api/lab/' + TRANSFER_COLLECTION

    @classmethod
    def at_lab(cls, base):
        return cls(base.rstrip('/') + '/' + TRANSFER_COLLECTION)

    def __post_init__(self):
        if (type(self.base) is not str or not self.base.startswith('/') or self.base.startswith('//')
                or self.base.endswith('/') or any(character in self.base for character in '\\?#')
                or any(part in {'', '.', '..'} for part in self.base.split('/')[1:])
                or any(ord(character) < 32 or ord(character) == 127 for character in self.base)):
            raise ValueError('transfer API base must be an absolute route path without traversal')
        try:
            self.base.encode('utf-8')
        except UnicodeError:
            raise ValueError('transfer API base must be UTF-8 encodable') from None

    @property
    def collection(self):
        return self.base

    @property
    def policy_route(self):
        return self.base.rpartition('/')[0] + '/transfer-policy'

    @property
    def session_route(self):
        return self.base + '/{session_id}'

    @property
    def part_route(self):
        return self.session_route + '/parts/{part_number}'

    @property
    def complete_route(self):
        return self.session_route + '/complete'

    @property
    def abort_route(self):
        return self.session_route + '/abort'

    def session(self, session_id):
        return self.base + '/' + quote(_segment(session_id), safe='')

    def part(self, session_id, number):
        _integer(number, 'part number', minimum=1)
        if number > R2_MAXIMUM_PARTS:
            raise ValueError('part number exceeds R2 multipart protocol maximum')
        return self.session(session_id) + '/parts/' + str(number)

    def complete(self, session_id):
        return self.session(session_id) + '/complete'

    def abort(self, session_id):
        return self.session(session_id) + '/abort'

    @property
    def initialize_route(self):
        return self.session_route + '/initialize'

    @property
    def parts_route(self):
        return self.session_route + '/parts'

    def initialize(self, session_id):
        return self.session(session_id) + '/initialize'

    def parts(self, session_id):
        return self.session(session_id) + '/parts'


@dataclass(frozen=True)
class TransferLocalPaths:
    """Owned local recovery paths derived from a caller-selected destination."""
    target: Path

    @property
    def partial(self):
        path = Path(self.target)
        return path.with_name(path.name + '.partial')

    @property
    def state(self):
        path = Path(self.target)
        return path.with_name(path.name + '.transfer.json')


@dataclass(frozen=True)
class AttemptArtifactApiPaths:
    """One attempt scope shared by Runner clients and relative API routes."""
    base: str

    def __post_init__(self):
        # Reuse the absolute route validation used by transfer controls. This
        # also supports FastAPI's explicit path templates on the server side.
        TransferApiPaths(self.base)

    @classmethod
    def at_runner(cls, base):
        return cls(base)

    @property
    def transfers(self):
        return TransferApiPaths.at_lab(self.base)

    @property
    def policy_route(self):
        return self.transfers.policy_route

    @property
    def input_download_route(self):
        return self.input_proxy_route + '/download'

    @property
    def input_proxy_route(self):
        return self.base + '/inputs/{artifact_id}'

    def input_proxy(self, artifact_id):
        return self.input_proxy_route.replace('{artifact_id}', quote(_segment(artifact_id), safe=''))

    def input_download(self, artifact_id):
        return self.input_proxy(artifact_id) + '/download'

    @property
    def output_proxy(self):
        return self.base + '/artifacts'
