"""Explicit trusted host bindings for a native hardware session broker."""
from dataclasses import dataclass, field
from copy import deepcopy
import math
from pathlib import Path

from .command_profiles import NativeCommandProfile, _finite_json
from .evidence_io import RecordWritePolicy


@dataclass(frozen=True)
class SessionBridgePaths:
    evidence: Path
    control: Path

    @staticmethod
    def parent_in_output(output):return Path(output)/'parent-session'

    @classmethod
    def evidence_in_work(cls,work):return cls.parent_in_output(Path(work)/'evidence')

    @property
    def listener(self):return self.control/'session.sock'

    @property
    def decisions(self):return self.evidence/'hardware-session-parent.jsonl'

    @property
    def manifest(self):return self.evidence/'hardware-session-parent.json'

    def exported(self,session_id,path):return 'parent-session-'+session_id+'-'+path.name


@dataclass(frozen=True)
class HardwareSessionRuntimePaths:
    work: Path

    def __post_init__(self):
        object.__setattr__(self, 'work', Path(self.work))

    @property
    def evidence(self): return self.work / 'evidence' / 'parent-session-runtime'
    @property
    def lifecycle(self): return self.evidence / 'hardware-session-runtime.jsonl'
    @property
    def manifest(self): return self.evidence / 'hardware-session-runtime.json'
    @property
    def assurance(self): return self.evidence / 'hardware-session-runtime-assurance.json'
    @property
    def bridge(self): return SessionBridgePaths.evidence_in_work(self.work)
    @property
    def emulator(self):
        from .managed_process_configuration import ManagedProcessPaths
        return ManagedProcessPaths(self.work)


def _positive(value, name):
    try:
        valid = type(value) in {int, float} and math.isfinite(value) and value > 0
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        raise ValueError(name + ' must be a positive finite number')
    return value


def _budget(value, name):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(name + ' must be a nonnegative integer or None')
    return value


@dataclass(frozen=True)
class HardwareSessionBinding:
    binding_id: str
    _native_profile: NativeCommandProfile = field(repr=False)
    state_directory: Path = field(repr=False)
    lease_seconds: float = 30
    renew_interval_seconds: float = 5
    max_request_bytes: int | None = None
    max_response_bytes: int | None = None
    evidence: RecordWritePolicy = field(default_factory=RecordWritePolicy)

    def __post_init__(self):
        if type(self.binding_id) is not str or not self.binding_id or '\0' in self.binding_id:
            raise ValueError('hardware session requires an explicit binding identity')
        if not isinstance(self._native_profile, NativeCommandProfile):
            raise ValueError('hardware session requires a native command profile')
        path = Path(self.state_directory)
        if not path.is_absolute() or '\0' in str(path):
            raise ValueError('session state_directory must be an absolute operator-selected path')
        object.__setattr__(self, 'state_directory', path)
        _positive(self.lease_seconds, 'lease_seconds')
        _positive(self.renew_interval_seconds, 'renew_interval_seconds')
        _budget(self.max_request_bytes, 'max_request_bytes')
        _budget(self.max_response_bytes, 'max_response_bytes')
        if not isinstance(self.evidence, RecordWritePolicy):
            raise ValueError('session evidence requires a RecordWritePolicy')

    @classmethod
    def from_grants(cls, grants, binding_id):
        if not isinstance(grants, dict) or grants.get('native_execution') is not True:
            raise ValueError('hardware.session requires the explicit trusted native_execution grant')
        bindings = grants.get('bindings')
        if not isinstance(bindings, dict) or type(binding_id) is not str or binding_id not in bindings:
            raise ValueError('hardware.session requires the selected operator binding')
        selected = bindings[binding_id]
        required = {'gateway_binding', 'state_directory'}
        optional = {'lease_seconds', 'renew_interval_seconds', 'max_request_bytes', 'max_response_bytes', 'evidence'}
        if not isinstance(selected, dict) or not required <= selected.keys() or selected.keys() - required - optional:
            raise ValueError('hardware session binding has missing or unsupported fields')
        _finite_json(selected)
        if type(selected['state_directory']) is not str:
            raise ValueError('session state_directory must be an absolute operator-selected path')
        profile = NativeCommandProfile.from_binding(selected['gateway_binding'])
        return cls(binding_id, profile, Path(selected['state_directory']),
                   lease_seconds=selected.get('lease_seconds', 30),
                   renew_interval_seconds=selected.get('renew_interval_seconds', 5),
                   max_request_bytes=selected.get('max_request_bytes'),
                   max_response_bytes=selected.get('max_response_bytes'),
                   evidence=RecordWritePolicy.from_parameters({'evidence': selected.get('evidence', {})}))

    @property
    def native_profile(self):
        return self._native_profile

    @property
    def gateway_binding(self):
        return self._native_profile.binding

    @property
    def profile(self):
        return self._native_profile.describe()

    def public_description(self):
        binding = self.gateway_binding
        return {'binding_id': self.binding_id, 'endpoint_id': binding['endpoint_id'],
                'operator_id': binding['operator_id'], 'profile': self.profile,
                'lease_seconds': self.lease_seconds, 'renew_interval_seconds': self.renew_interval_seconds,
                'max_request_bytes': self.max_request_bytes, 'max_response_bytes': self.max_response_bytes,
                'evidence': self.evidence.to_dict(),
                'qualification': {'execution': 'trusted_native_only', 'device_capabilities': 'operator_claims_only',
                                  'physical_outcome': 'unverified'}}

    def select_runtime_secrets(self,values):
        if values is None:values={}
        if not isinstance(values,dict) or values.keys()-{'bindings', 'emulator_bindings'}:
            raise ValueError('hardware session runtime secrets must select a bindings map')
        if not isinstance(values.get('emulator_bindings', {}), dict):
            raise ValueError('emulator secret bindings must be an object')
        bindings=values.get('bindings',{})
        if not isinstance(bindings,dict):raise ValueError('hardware session secret bindings must be an object')
        selected=bindings.get(self.binding_id,{})
        if not isinstance(selected,dict):raise ValueError('selected hardware session credentials must be an object')
        _finite_json(selected)
        return deepcopy(selected)
