"""Pure portable asset/token selections and trusted process bindings.

No files, executables, hosts or credentials are inspected here. A binding is an
operator-native execution choice; its name or tool alias proves no vendor,
platform, physical behaviour or isolation property.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType

from tinystudio.config import RuntimeTools
from .managed_process_configuration import ManagedProcessSelection, ReadinessSelection, _text


def _encoded(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('emulator selection requires finite UTF-8 JSON') from None


def _fields(value, required, optional=(), label='emulator selection'):
    if type(value) is not dict or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError(label + ' has missing or unsupported fields')


def relative_path(value, *, root=False):
    """Logical POSIX path, without filename-length or platform apparatus caps."""
    _text(value, 'relative workspace path')
    if root and value == '.':
        return value
    if (value.startswith('/') or '\\' in value or any(part in {'', '.', '..'} for part in value.split('/'))
            or any(ord(character) < 32 for character in value)):
        raise ValueError('workspace paths must be explicit relative POSIX paths without traversal')
    return value


def environment_values(value, label='public_env'):
    if not isinstance(value, Mapping):
        raise ValueError(label + ' must be a string mapping')
    result = {}
    for key, item in value.items():
        _text(key, label + ' key')
        if '=' in key:
            raise ValueError(label + ' key must not contain =')
        result[key] = _text(item, label + ' value', empty=True)
    return result


@dataclass(frozen=True)
class EmulatorAsset:
    name: str
    artifact_id: str
    destination: str
    sha256: str

    def __post_init__(self):
        _text(self.name, 'asset name'); _text(self.artifact_id, 'asset artifact_id')
        relative_path(self.destination)
        if type(self.sha256) is not str or re.fullmatch('[0-9a-f]{64}', self.sha256) is None:
            raise ValueError('asset requires its frozen input SHA-256')

    def reference(self):
        return {'name': self.name, 'artifact_id': self.artifact_id, 'destination': self.destination}

    def as_dict(self):
        return {**self.reference(), 'sha256': self.sha256}


@dataclass(frozen=True)
class EmulatorArgument:
    kind: str
    value: str

    def __post_init__(self):
        if type(self.kind) is not str or self.kind not in {'asset', 'workspace'}:
            raise ValueError('argument token must select asset or workspace')
        if self.kind == 'workspace': relative_path(self.value, root=True)
        else: _text(self.value, 'argument asset name')

    def as_dict(self):
        return {self.kind: self.value}


def _argument(value):
    if isinstance(value, EmulatorArgument):
        return value
    if type(value) is str:
        return _text(value, 'argument literal', empty=True)
    if type(value) is dict and len(value) == 1 and next(iter(value)) in {'asset', 'workspace'}:
        return EmulatorArgument(next(iter(value)), next(iter(value.values())))
    raise ValueError('argument must be a literal string or one exact asset/workspace token')


def _arguments(value, *, executable=False):
    if type(value) not in {list, tuple}:
        raise ValueError('argv must be an explicit list or tuple')
    result = tuple(_argument(item) for item in value)
    if executable and (not result or result[0] == ''):
        raise ValueError('readiness command requires an explicit nonempty executable')
    return result


def _wire_argument(value):
    return value.as_dict() if isinstance(value, EmulatorArgument) else value


def _readiness(value):
    if type(value) is not dict:
        raise ValueError('emulator readiness must be an explicit object')
    projection = dict(value)
    if value.get('kind') == 'command':
        args = _arguments(value.get('argv'), executable=True)
        projection['argv'] = [item if type(item) is str else 'resolved-token' for item in args]
        if value.get('cwd') is not None:
            relative_path(value['cwd'], root=True)
    ReadinessSelection.from_dict(projection)
    return _encoded(value).decode('utf-8')


_RESOURCE_NAMES = ('startup_timeout_seconds', 'shutdown_grace_seconds', 'max_log_bytes',
                   'poll_interval_seconds', 'raw_log_visibility')
EMULATOR_EXIT_POLICIES = ('record', 'fail_session')


@dataclass(frozen=True)
class EmulatorSelection:
    binding_id: str
    assets: tuple[EmulatorAsset, ...]
    argv: tuple[str | EmulatorArgument, ...]
    cwd: str
    _readiness_json: str = field(repr=False)
    startup_timeout_seconds: float | None = None
    shutdown_grace_seconds: float = 3
    max_log_bytes: int | None = None
    poll_interval_seconds: float = .02
    raw_log_visibility: str | None = None
    on_exit: str = 'record'

    def __post_init__(self):
        _text(self.binding_id, 'emulator binding_id')
        if type(self.on_exit) is not str or self.on_exit not in EMULATOR_EXIT_POLICIES:
            raise ValueError('emulator on_exit must select record or fail_session')
        if type(self.assets) not in {tuple, list} or any(not isinstance(asset, EmulatorAsset) for asset in self.assets):
            raise ValueError('emulator assets require frozen EmulatorAsset values')
        object.__setattr__(self, 'assets', tuple(self.assets))
        object.__setattr__(self, 'argv', _arguments(self.argv))
        relative_path(self.cwd, root=True)
        object.__setattr__(self, '_readiness_json', _readiness(json.loads(self._readiness_json)))
        names = [asset.name for asset in self.assets]
        destinations = [asset.destination for asset in self.assets]
        if len(names) != len(set(names)) or any(a == b or a.startswith(b + '/') or b.startswith(a + '/')
            for index, a in enumerate(destinations) for b in destinations[index + 1:]):
            raise ValueError('asset names and file destinations must be unique without ancestor collisions')
        directories = [self.cwd]
        readiness = self.readiness
        if readiness.get('cwd') is not None: directories.append(readiness['cwd'])
        if any(directory == path or directory.startswith(path + '/') for directory in directories for path in destinations):
            raise ValueError('working directories cannot be asset files or descendants of asset files')
        arguments = self.argv + (_arguments(readiness['argv'], executable=True) if readiness['kind'] == 'command' else ())
        for argument in arguments:
            if isinstance(argument, EmulatorArgument) and argument.kind == 'asset' and argument.value not in names:
                raise ValueError('argument token refers to an unknown asset')
        ManagedProcessSelection(('configuration-validation',), Path('.'), ReadinessSelection('process_running'),
                                **{name: getattr(self, name) for name in _RESOURCE_NAMES})

    @classmethod
    def from_definition(cls, definition):
        if type(definition) is not dict or type(definition.get('parameters', {})) is not dict:
            raise ValueError('emulator selection requires a definition and parameter object')
        parameters = definition.get('parameters', {})
        if 'emulator' not in parameters or parameters['emulator'] is None:
            return None
        if definition.get('engine') != 'hardware.session':
            raise ValueError('managed emulator selection requires hardware.session')
        selected = parameters['emulator']
        _fields(selected, {'binding_id', 'assets', 'argv', 'readiness'}, {'cwd', 'on_exit', *_RESOURCE_NAMES})
        if type(selected['assets']) is not list or type(definition.get('inputs', [])) is not list:
            raise ValueError('emulator assets and frozen inputs must be explicit lists')
        assets = []
        for value in selected['assets']:
            _fields(value, {'name', 'artifact_id', 'destination'}, label='emulator asset')
            matches = [ref for ref in definition.get('inputs', []) if type(ref) is dict and ref.get('artifact_id') == value['artifact_id']]
            if len(matches) != 1:
                raise ValueError('each emulator asset must select exactly one frozen input')
            assets.append(EmulatorAsset(**value, sha256=matches[0].get('sha256')))
        return cls(selected['binding_id'], tuple(assets), _arguments(selected['argv']), selected.get('cwd', '.'),
                   _readiness(selected['readiness']), on_exit=selected.get('on_exit', 'record'),
                   **{name: selected[name] for name in _RESOURCE_NAMES if name in selected})

    @property
    def readiness(self):
        return json.loads(self._readiness_json)

    def as_dict(self):
        return {'binding_id': self.binding_id, 'assets': [asset.reference() for asset in self.assets],
                'argv': [_wire_argument(item) for item in self.argv], 'cwd': self.cwd, 'readiness': self.readiness,
                'on_exit': self.on_exit,
                **{name: getattr(self, name) for name in _RESOURCE_NAMES}}

    @staticmethod
    def json_schema():
        def obj(required, properties):
            return {'type': 'object', 'required': required, 'additionalProperties': False, 'properties': properties}
        text = {'type': 'string', 'minLength': 1}
        argument = {'oneOf': [{'type': 'string'}, obj(['asset'], {'asset': text}),
                              obj(['workspace'], {'workspace': text})]}
        arguments = {'type': 'array', 'items': argument}
        marker = obj(['encoding', 'value'], {'encoding': {'enum': ['utf8', 'hex', 'base64']},
                                           'value': {'type': 'string'}})
        readiness = {'oneOf': [
            obj(['kind'], {'kind': {'const': 'process_running'}}),
            obj(['kind', 'host', 'port'], {'kind': {'const': 'tcp'}, 'host': text,
                'port': {'type': 'integer', 'minimum': 1, 'maximum': 65535}}),
            obj(['kind', 'stream', 'marker'], {'kind': {'const': 'log_bytes'},
                'stream': {'enum': ['stdout', 'stderr']}, 'marker': marker}),
            obj(['kind', 'argv'], {'kind': {'const': 'command'}, 'argv': arguments | {'minItems': 1},
                'cwd': {'type': ['string', 'null']}, 'expected_exit_code': {'type': 'integer'}})]}
        return obj(['binding_id', 'assets', 'argv', 'readiness'], {
            'binding_id': text, 'assets': {'type': 'array', 'items': obj(['name', 'artifact_id', 'destination'],
                {'name': text, 'artifact_id': text, 'destination': text})},
            'argv': arguments, 'cwd': {'type': 'string', 'default': '.'}, 'readiness': readiness,
            'on_exit': {'enum': list(EMULATOR_EXIT_POLICIES), 'default': 'record'},
            'startup_timeout_seconds': {'type': ['number', 'null'], 'minimum': 0, 'default': None},
            'shutdown_grace_seconds': {'type': 'number', 'minimum': 0, 'default': 3},
            'max_log_bytes': {'type': ['integer', 'null'], 'minimum': 0, 'default': None},
            'poll_interval_seconds': {'type': 'number', 'minimum': 0, 'default': .02},
            'raw_log_visibility': {'enum': [None, 'model', 'evaluation', 'truth'], 'default': None}})

    @property
    def digest(self):
        return hashlib.sha256(_encoded({'selection': self.as_dict(), 'input_hashes': {
            asset.name: asset.sha256 for asset in self.assets}})).hexdigest()


@dataclass(frozen=True)
class EmulatorProcessBinding:
    binding_id: str
    _executable_json: str = field(repr=False)
    public_env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        _text(self.binding_id, 'emulator process binding_id')
        selected = json.loads(self._executable_json)
        if type(selected) is not dict or len(selected) != 1:
            raise ValueError('operator executable must select one runtime_tool or argv_prefix')
        if 'runtime_tool' in selected:
            if type(selected['runtime_tool']) is not str or selected['runtime_tool'] not in RuntimeTools.names:
                raise ValueError('unknown centrally configured runtime tool alias; explicit argv_prefix is also supported')
        elif 'argv_prefix' in selected:
            args = _arguments(selected['argv_prefix'], executable=True)
            if any(type(item) is not str for item in args):
                raise ValueError('operator argv_prefix requires literal arguments')
        else:
            raise ValueError('operator executable must select runtime_tool or argv_prefix')
        object.__setattr__(self, '_executable_json', _encoded(selected).decode('utf-8'))
        object.__setattr__(self, 'public_env', MappingProxyType(environment_values(self.public_env)))

    @classmethod
    def from_grants(cls, grants, binding_id):
        if type(grants) is not dict or grants.get('native_execution') is not True:
            raise ValueError('managed emulator requires the trusted native_execution grant')
        bindings = grants.get('emulator_bindings')
        if type(bindings) is not dict or type(binding_id) is not str or binding_id not in bindings:
            raise ValueError('managed emulator requires its selected operator process binding')
        selected = bindings[binding_id]
        _fields(selected, {'executable'}, {'public_env'}, label='operator emulator binding')
        return cls(binding_id, _encoded(selected['executable']).decode('utf-8'), selected.get('public_env', {}))

    @property
    def executable(self):
        return json.loads(self._executable_json)

    def argv_prefix(self, tools=None):
        selected = self.executable
        if 'argv_prefix' in selected:
            return tuple(selected['argv_prefix'])
        tools = RuntimeTools.from_environment() if tools is None else tools
        if not isinstance(tools, RuntimeTools):
            raise ValueError('runtime executable selection requires RuntimeTools')
        return (tools.command(selected['runtime_tool']),)

    def as_dict(self):
        return {'binding_id': self.binding_id, 'executable': self.executable, 'public_env': dict(self.public_env)}

    def select_runtime_secrets(self, values):
        if values is None: values = {}
        _fields(values, set(), {'bindings', 'emulator_bindings'}, label='hardware session runtime secrets')
        bindings = values.get('emulator_bindings', {})
        if type(bindings) is not dict:
            raise ValueError('emulator secret bindings must be an object')
        selected = bindings.get(self.binding_id, {})
        _fields(selected, set(), {'environment'}, label='selected emulator runtime secrets')
        return ({'environment': environment_values(selected['environment'], 'private environment')}
                if 'environment' in selected else {})

    @property
    def digest(self):
        return hashlib.sha256(_encoded(self.as_dict())).hexdigest()


def emulator_binding_capabilities(grants) -> list[dict]:
    """Declare configured process bindings without resolving or probing them.

    The digest identifies the configured public binding, including its selected
    executable and public environment. Neither those values nor private runtime
    environment overlays are disclosed in this target capability declaration.
    """
    if type(grants) is not dict:
        raise ValueError('hardware session grants must be an object')
    bindings = grants.get('emulator_bindings', {})
    if type(bindings) is not dict:
        raise ValueError('hardware session emulator bindings must be an object')
    if grants.get('native_execution') is not True:
        return []
    configured = [EmulatorProcessBinding.from_grants(grants, key) for key in bindings]
    return [
        {'binding_id': binding.binding_id, 'binding_sha256': binding.digest,
         'qualification': {'availability': 'configured_only', 'executable': 'not_checked',
                           'readiness': 'not_checked', 'firmware': 'not_checked',
                           'model_fidelity': 'unverified', 'physical_outcome': 'unverified'}}
        for binding in sorted(configured, key=lambda value: value.binding_id)
    ]
