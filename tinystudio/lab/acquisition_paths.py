"""Shared artifact layout for an acquisition attempt."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AcquisitionPaths:
    root: Path

    def __post_init__(self):
        object.__setattr__(self, 'root', Path(self.root))

    @property
    def raw(self):
        return self.root / 'raw.jsonl'

    @property
    def processed(self):
        return self.root / 'processed.jsonl'

    @property
    def source_binding(self):
        return self.root / 'source-binding.json'

    @property
    def failure(self):
        return self.root / 'acquisition-audit.json'

    @property
    def unprocessed_receive(self):
        return self.root / 'unprocessed-receive.json'

    @property
    def streams(self):
        return ((self.raw, 'original'), (self.processed, 'derived_records'))
