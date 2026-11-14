"""Bind runner-local attempt paths to an operator-provisioned shared volume."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SharedWorkConfiguration:
    root: Path

    def __post_init__(self):
        self._validate_path(self.root)
        if self.root.is_symlink():
            raise ValueError('shared_work_root cannot be a symlink')
        # Keep platform aliases above the selected root consistent with the
        # spool policy; links within the shared volume are rejected below.
        object.__setattr__(self,'root',self.root.parent.resolve()/self.root.name)

    @staticmethod
    def _validate_path(path):
        if (not isinstance(path,Path) or not path.is_absolute() or '..' in path.parts
                or any(ord(char)<32 for char in str(path))):
            raise ValueError('shared-volume paths must be absolute without traversal or control characters')

    @classmethod
    def from_backend(cls,configuration):
        selected=configuration.get('shared_work_root')
        if not selected:
            raise ValueError('Kubernetes execution needs an operator-provisioned shared_work_root')
        return cls(Path(selected).absolute())

    def relative_path(self,path):
        """Return an existing descendant's PVC identity, rejecting internal links."""
        self._validate_path(path)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError('shared_work_root must be an existing non-symlink directory')
        resolved=path.resolve(strict=True)
        if resolved==self.root or not resolved.is_relative_to(self.root):
            raise ValueError('Kubernetes attempt paths must be descendants of operator-provisioned shared storage')
        for current in (path,*path.parents):
            if current.is_symlink():
                raise ValueError('shared-volume attempt paths cannot contain a symlink')
            if current.resolve(strict=True)==self.root:
                return resolved.relative_to(self.root)
        raise ValueError('Kubernetes attempt path has no shared-volume ancestor')
