"""Pure shared paths for durable attempt completion and its historical status."""
from dataclasses import dataclass
import re

from .client_paths import RunnerApiPaths, path_identifier


FINALIZATION_PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class FinalizationApiPaths:
    base: str = RunnerApiPaths().attempt_route

    def __post_init__(self):
        if (not isinstance(self.base, str) or not self.base.startswith('/') or self.base.startswith('//')
                or any(char in self.base for char in ('?', '#', '\\'))
                or any(part in {'', '.', '..'} for part in self.base[1:].split('/'))):
            raise ValueError('finalization paths require a normalized absolute attempt scope')

    @classmethod
    def for_runner(cls, target_id, attempt_id):
        return cls(RunnerApiPaths().attempt(target_id, attempt_id))

    @property
    def collection_route(self):
        return self.base+'/finalizations'

    @property
    def collection(self):
        return self.collection_route

    @property
    def legacy_finish(self):
        return self.base+'/finish'

    @property
    def digest_route(self):
        return self.collection_route+'/by-digest/{payload_digest}'

    @property
    def operation_route(self):
        return self.collection_route+'/{operation_id}'

    def by_digest(self, payload_digest):
        if not isinstance(payload_digest, str) or re.fullmatch('[0-9a-f]{64}', payload_digest) is None:
            raise ValueError('finalization lookup requires an exact canonical payload digest')
        return self.digest_route.replace('{payload_digest}', payload_digest)

    def operation(self, operation_id):
        return self.operation_route.replace('{operation_id}', path_identifier(operation_id))
