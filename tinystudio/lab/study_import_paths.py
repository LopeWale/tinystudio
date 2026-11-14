"""Shared observation-import routes for the service and Python clients."""
from dataclasses import dataclass
import hashlib
from typing import ClassVar

from .client_paths import LabApiPaths, path_identifier


@dataclass(frozen=True)
class StudyImportPaths:
    study_id: str
    base: str = LabApiPaths().base

    preview_route: ClassVar[str] = '/studies/{id_}/observation-imports/preview'
    apply_route: ClassVar[str] = '/studies/{id_}/observation-imports'

    @property
    def preview(self):
        return self.base + self.preview_route.format(id_=path_identifier(self.study_id))

    @property
    def apply(self):
        return self.base + self.apply_route.format(id_=path_identifier(self.study_id))


@dataclass(frozen=True)
class StudyImportKeys:
    """Server publication identities, separate from researcher-authored keys."""
    key: str
    namespace: ClassVar[str] = 'tinystudio:study-import:'

    @property
    def receipt(self):
        return self.namespace + 'receipt:' + hashlib.sha256(self.key.encode()).hexdigest()

    def row(self, line):
        identity = (self.key + ':' + str(line)).encode()
        return self.namespace + 'row:' + hashlib.sha256(identity).hexdigest()

    @classmethod
    def reserved(cls, key):
        return key.startswith(cls.namespace)
