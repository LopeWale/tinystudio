"""Resolve one host's spool location independently of its disposable cache."""
from dataclasses import dataclass
from pathlib import Path

from tinystudio.config import config, RuntimePaths
from .evidence_spool import SpoolPolicy
from .shared_work_configuration import SharedWorkConfiguration


@dataclass(frozen=True)
class SpoolConfiguration:
    policy: SpoolPolicy
    requires_explicit_root: bool
    root_configured: bool
    mode: str

    @classmethod
    def resolve(cls, profile, engine, backend, *, work_root=None, host=None):
        host=host or config()
        profile={} if profile is None else profile
        selected=profile.get('evidence_spool',{})
        directory=profile.get('backends',{}).get(backend,{}).get('shared_work_root') if backend=='kubernetes' else work_root
        if backend=='kubernetes' and directory:
            directory=SharedWorkConfiguration.from_backend(profile['backends'][backend]).root
        default_root=RuntimePaths(Path(directory).absolute()).laboratory_evidence_spool if directory else host.laboratory_evidence_spool
        policy=SpoolPolicy.from_profile(profile,engine,default_root)
        explicit=bool(directory or host.lab_evidence_dir or selected.get('root'))
        mode='automatic_hardware' if 'enabled' not in selected else 'all' if selected['enabled'] else 'disabled'
        return cls(policy,host.storage_backend=='r2',explicit or host.storage_backend!='r2',mode)

    def require_ready(self):
        if self.policy.enabled and self.requires_explicit_root and not self.root_configured:
            raise ValueError('R2 uses a disposable host cache; configure a persistent TINYSTUDIO_LAB_EVIDENCE_DIR or operator evidence_spool.root before hardware capture')

    def advertised(self):
        return {'mode':self.mode,'requires_explicit_root':self.requires_explicit_root,
                'root_configured':self.root_configured,
                'qualification':'Configured host location only; filesystem durability is unqualified.'}
