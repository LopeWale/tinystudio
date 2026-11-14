"""Owned asset copies and observational custody for a managed native process.

This module launches nothing. The caller stops every writer before seal(); local
custody checks remain available after ordinary attempt authority is revoked.
Copy changes are observations, not automatic model or physical judgements.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from types import MappingProxyType

from .hardware_emulator_selection import (
    EmulatorProcessBinding, EmulatorSelection, _arguments,
    _encoded, environment_values, relative_path,
)
from .managed_process_configuration import ManagedProcessSelection, ReadinessSelection, _path


class EmulatorWorkspaceError(RuntimeError):
    """Workspace diagnostics omit private paths, environments and OS messages."""
    def __init__(self, message, *, report=None):
        super().__init__(message)
        self.report = deepcopy(report)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _public(value):
    return {key: item for key, item in value.items() if not key.startswith('_')}


@contextmanager
def _directory(path):
    """Walk an already canonical private/internal path without following links."""
    path = Path(path)
    if not path.is_absolute():
        raise EmulatorWorkspaceError('internal workspace directory must be absolute')
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            if component in {'', '.', '..'}:
                raise EmulatorWorkspaceError('internal workspace directory is not canonical')
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor); descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _descendant(descriptor, parts, *, create=False):
    current = os.dup(descriptor)
    try:
        for component in parts:
            if create:
                try: os.mkdir(component, mode=0o700, dir_fd=current)
                except FileExistsError: pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current); current = child
        yield current
    finally:
        os.close(current)


def _file_state(path=None, *, descriptor=None, name=None, check_authority=lambda: None):
    """Observe a regular file through a nonblocking descriptor; never read a FIFO."""
    try:
        initial = path.lstat() if descriptor is None else os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISLNK(initial.st_mode): return {'status': 'symlink'}
        if not stat.S_ISREG(initial.st_mode): return {'status': 'not_regular'}
        opened = os.open(path if descriptor is None else name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         **({} if descriptor is None else {'dir_fd': descriptor}))
        try:
            before = os.fstat(opened)
            if not stat.S_ISREG(before.st_mode): return {'status': 'not_regular'}
            digest, size = hashlib.sha256(), 0
            while True:
                check_authority()
                block = os.read(opened, 1024 * 1024)
                if not block: break
                digest.update(block); size += len(block)
            after = os.fstat(opened)
            current = path.lstat() if descriptor is None else os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        finally:
            os.close(opened)
        facts = {'status': 'regular', 'sha256': digest.hexdigest(), 'size': size, '_identity': _identity(after)}
        if any(_identity(info) != _identity(initial) for info in (before, after, current)):
            facts['status'] = 'changed_while_reading'
        return facts
    except FileNotFoundError:
        return {'status': 'missing'}
    except OSError as error:
        return {'status': 'unreadable', 'error_type': type(error).__name__}


def _classification(initial, final, expected=None):
    if final['status'] != 'regular': return final['status']
    if expected is not None and final['sha256'] != expected: return 'sha256_mismatch'
    if initial.get('status') != 'regular': return 'not_initially_verified'
    if final['sha256'] != initial['sha256']: return 'changed'
    before, after = initial.get('_identity'), final.get('_identity')
    if before is not None and after is not None:
        if before[:2] != after[:2]: return 'replaced'
        if before != after: return 'metadata_changed'
    return 'matched' if expected is not None else 'unchanged'


@dataclass(frozen=True)
class PreparedEmulatorAssets:
    _paths: object = field(repr=False)
    _report_json: str = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, '_paths', MappingProxyType(dict(self._paths)))

    def path(self, name):
        try: return self._paths[name]
        except KeyError: raise EmulatorWorkspaceError('unknown prepared emulator asset') from None

    @property
    def paths(self):
        return self._paths

    def as_dict(self):
        return json.loads(self._report_json)


class EmulatorWorkspace:
    def __init__(self, work, selection):
        if not isinstance(selection, EmulatorSelection):
            raise ValueError('emulator workspace requires EmulatorSelection')
        self.work, self.selection = _path(work, 'emulator work directory'), selection
        self._work_actual = None
        self._allocated = self._prepared = False
        self._root_identity = None
        self._sources, self._initial = {}, {}
        self._prepare_error = None
        self._prepared_assets = None
        self._sealed = None
        self._report_sha256 = None
        self._executable = {'status': 'not_observed', 'scope': 'observed_file_not_execution_attestation'}

    @property
    def root(self):
        return (self._work_actual or self.work) / 'parent-emulator-work'

    @property
    def evidence_directory(self):
        return (self._work_actual or self.work) / 'evidence' / 'parent-emulator-assets'

    @property
    def report_path(self):
        return self.evidence_directory / 'asset-custody.json'

    @property
    def executable_observation(self):
        return deepcopy(self._executable)

    def _allocate(self):
        # Resolve operator ancestors once. The final work entry and every
        # internal component remain subject to no-follow descriptor traversal.
        work = self.work if self.work.is_absolute() else Path.cwd() / self.work
        parent = work.parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        actual = parent / work.name
        try: actual.mkdir(mode=0o700)
        except FileExistsError: pass
        with _directory(actual) as descriptor:
            os.mkdir('parent-emulator-work', mode=0o700, dir_fd=descriptor)
            self._work_actual = actual
        with _directory(self.root) as descriptor:
            self._root_identity = _identity(os.fstat(descriptor))[:2]
        self._allocated = True

    @contextmanager
    def _root(self):
        with _directory(self.root) as descriptor:
            if _identity(os.fstat(descriptor))[:2] != self._root_identity:
                raise EmulatorWorkspaceError('owned emulator workspace directory was replaced')
            yield descriptor

    def _copied_state(self, asset, check_authority):
        if not self._allocated: return {'status': 'not_prepared'}
        try:
            with self._root() as root, _descendant(root, asset.destination.split('/')[:-1]) as parent:
                return _file_state(descriptor=parent, name=asset.destination.split('/')[-1], check_authority=check_authority)
        except FileNotFoundError:
            return {'status': 'missing'}
        except (OSError, EmulatorWorkspaceError) as error:
            return {'status': 'unreadable', 'error_type': type(error).__name__}

    def _copy(self, source, asset, check_authority):
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(source_fd)
            if not stat.S_ISREG(before.st_mode):
                raise EmulatorWorkspaceError('frozen emulator input is not a regular file')
            digest = hashlib.sha256()
            with self._root() as root, _descendant(root, asset.destination.split('/')[:-1], create=True) as parent:
                target = os.open(asset.destination.split('/')[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
                try:
                    while True:
                        check_authority()
                        block = os.read(source_fd, 1024 * 1024)
                        if not block: break
                        digest.update(block)
                        pending = memoryview(block)
                        while pending:
                            written = os.write(target, pending)
                            if written <= 0: raise EmulatorWorkspaceError('incomplete emulator asset copy')
                            pending = pending[written:]
                    os.fsync(target)
                finally:
                    os.close(target)
            if (_identity(os.fstat(source_fd)) != _identity(before) or _identity(source.lstat()) != _identity(before)
                    or digest.hexdigest() != asset.sha256):
                raise EmulatorWorkspaceError('frozen emulator input changed while copying')
        finally:
            os.close(source_fd)

    def prepare(self, input_paths, *, check_authority=lambda: None):
        if self._allocated or self._prepared_assets is not None:
            raise EmulatorWorkspaceError('emulator workspace permits only one preparation without overwrites')
        if not callable(check_authority) or not hasattr(input_paths, 'get'):
            raise ValueError('workspace preparation requires input paths and a callable authority guard')
        check_authority()
        try:
            self._allocate()
            paths = {}
            for asset in self.selection.assets:
                check_authority()
                raw = input_paths.get(asset.artifact_id)
                raw = raw.get('path') if type(raw) is dict else raw
                if raw is None: raise EmulatorWorkspaceError('selected frozen emulator input was not supplied')
                source = _path(raw, 'downloaded emulator input')
                self._sources[asset.name] = source
                initial = {'initial_original': _file_state(source, check_authority=check_authority)}
                self._initial[asset.name] = initial
                original = initial['initial_original']
                if original.get('status') != 'regular' or original.get('sha256') != asset.sha256:
                    raise EmulatorWorkspaceError('frozen emulator input could not be verified')
                self._copy(source, asset, check_authority)
                initial['initial_copy'] = self._copied_state(asset, check_authority)
                if initial['initial_copy'].get('status') != 'regular' or initial['initial_copy'].get('sha256') != asset.sha256:
                    raise EmulatorWorkspaceError('initial emulator copy differs from the frozen input')
                paths[asset.name] = self.root.joinpath(*asset.destination.split('/'))
            self._prepared = True
            report = {'status': 'prepared', 'selection_sha256': self.selection.digest, 'assets': {
                asset.name: {**asset.as_dict(), **{key: _public(value) for key, value in self._initial[asset.name].items()}}
                for asset in self.selection.assets}}
            self._prepared_assets = PreparedEmulatorAssets(paths, _encoded(report).decode('utf-8'))
            return self._prepared_assets
        except Exception as error:
            self._prepare_error = {'type': type(error).__name__}
            if isinstance(error, OSError):
                raise EmulatorWorkspaceError('emulator workspace preparation failed (' + type(error).__name__ + ')') from None
            raise

    def _resolve_argument(self, argument):
        if type(argument) is str: return argument
        if argument.kind == 'asset': return str(self._prepared_assets.path(argument.value))
        return str(self.root if argument.value == '.' else self.root.joinpath(*argument.value.split('/')))

    def _cwd(self, value):
        relative_path(value, root=True)
        parts = [] if value == '.' else value.split('/')
        with self._root() as root, _descendant(root, parts, create=True): pass
        return self.root.joinpath(*parts)

    def launch_selection(self, binding, *, tools=None, base_env=None, runtime_secrets=None, check_authority=lambda: None):
        if not self._prepared or self._sealed is not None:
            raise EmulatorWorkspaceError('launch selection requires a prepared unsealed workspace')
        if not isinstance(binding, EmulatorProcessBinding) or binding.binding_id != self.selection.binding_id:
            raise EmulatorWorkspaceError('emulator process binding does not match the authored selection')
        if runtime_secrets is not None and (type(runtime_secrets) is not dict or runtime_secrets.keys() - {'environment'}):
            raise ValueError('emulator runtime secrets support only an environment mapping')
        base = environment_values({} if base_env is None else base_env, 'base_env')
        private = environment_values((runtime_secrets or {}).get('environment', {}), 'runtime_secrets.environment')
        environment = base | dict(binding.public_env) | private
        check_authority()
        cwd = self._cwd(self.selection.cwd)
        prefix = binding.argv_prefix(tools)
        program = prefix[0]
        if os.path.dirname(program):
            candidate = Path(program) if Path(program).is_absolute() else cwd / program
            found = str(candidate) if os.access(candidate, os.X_OK) else None
        else:
            search = os.pathsep.join(part if Path(part).is_absolute() else str(cwd / part)
                                    for part in environment.get('PATH', os.defpath).split(os.pathsep))
            found = shutil.which(program, path=search)
        if found is None:
            raise EmulatorWorkspaceError('selected emulator executable is unavailable on the selected process path')
        # Executable aliases may be legitimate symlinks. Observe their target as
        # a regular file through a nonblocking FD, without claiming exec identity.
        try:
            descriptor = os.open(found, os.O_RDONLY | os.O_NONBLOCK)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode): raise EmulatorWorkspaceError('selected executable is not a regular file')
                digest, size = hashlib.sha256(), 0
                while True:
                    check_authority()
                    block = os.read(descriptor, 1024 * 1024)
                    if not block: break
                    digest.update(block); size += len(block)
                if _identity(os.fstat(descriptor)) != _identity(info) or _identity(Path(found).stat()) != _identity(info):
                    raise EmulatorWorkspaceError('selected executable changed during observation')
            finally: os.close(descriptor)
        except OSError as error:
            raise EmulatorWorkspaceError('selected executable observation failed (' + type(error).__name__ + ')') from None
        self._executable = {'status': 'observed', 'sha256': digest.hexdigest(), 'size': size,
                            'scope': 'observed_file_not_execution_attestation'}
        readiness = self.selection.readiness
        if readiness['kind'] == 'command':
            readiness['argv'] = [self._resolve_argument(item) for item in _arguments(readiness['argv'], executable=True)]
            if readiness.get('cwd') is not None: readiness['cwd'] = str(self._cwd(readiness['cwd']))
        check_authority()
        return ManagedProcessSelection(prefix + tuple(self._resolve_argument(item) for item in self.selection.argv), cwd,
            ReadinessSelection.from_dict(readiness), public_env=binding.public_env,
            **{name: getattr(self.selection, name) for name in ('startup_timeout_seconds', 'shutdown_grace_seconds',
                'max_log_bytes', 'poll_interval_seconds', 'raw_log_visibility')})

    def _write_report(self, report):
        with _directory(self._work_actual) as work, _descendant(work, ['evidence', 'parent-emulator-assets'], create=True) as directory:
            descriptor = os.open('asset-custody.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            data = _encoded(report) + b'\n'
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
                os.fsync(directory)
            except BaseException:
                # Keep partial bytes as evidence; never silently repair them.
                raise
        self._report_sha256 = hashlib.sha256(data).hexdigest()

    def seal(self, *, check_authority=lambda: None):
        """Record custody once, after the caller has stopped every owned writer.

        No ordinary live authority is required. The optional callback only adds
        a caller-selected guard; copies are not repaired or automatically judged.
        """
        if self._sealed is not None: return deepcopy(self._sealed)
        if not self._allocated:
            raise EmulatorWorkspaceError('workspace custody cannot be sealed before allocation')
        assets, matched = {}, True
        for asset in self.selection.assets:
            initial = self._initial.get(asset.name, {})
            source = self._sources.get(asset.name)
            original = _file_state(source, check_authority=check_authority) if source is not None else {'status': 'not_supplied'}
            copied = self._copied_state(asset, check_authority)
            original_status = _classification(initial.get('initial_original', {}), original, asset.sha256)
            copy_status = _classification(initial.get('initial_copy', {}), copied)
            matched = matched and original_status == 'matched'
            assets[asset.name] = {**asset.as_dict(), **{key: _public(value) for key, value in initial.items()},
                'final_original': _public(original), 'final_copy': _public(copied),
                'original_status': original_status, 'copy_status': copy_status}
        report = {'format': 'tinystudio.emulator-assets.v1', 'selection_sha256': self.selection.digest,
            'status': 'sealed' if self._prepared else 'partial_preparation', 'assets': assets,
            'prepare_error': deepcopy(self._prepare_error), 'executable_observation': self.executable_observation,
            'provenance': {'originals_match_expected': matched, 'copy_changes': 'observed_only',
                'execution_success': 'not_inferred', 'physical_outcome': 'unverified', 'writers_stopped': 'caller_responsibility'}}
        try:
            self._write_report(report)
        except OSError as error:
            raise EmulatorWorkspaceError('asset custody metadata write failed (' + type(error).__name__ + ')', report=report) from None
        self._sealed = report
        return deepcopy(report)

    def retain_artifacts(self, destination, *, visibility='evaluation'):
        if self._sealed is None:
            raise EmulatorWorkspaceError('asset custody must be sealed before retaining metadata')
        if type(visibility) is not str or visibility not in {'model', 'evaluation', 'truth'}:
            raise ValueError('invalid asset custody artifact visibility')
        current = _file_state(self.report_path)
        if current.get('status') != 'regular' or current.get('sha256') != self._report_sha256:
            raise EmulatorWorkspaceError('sealed asset custody metadata changed before retention')
        destination = _path(destination, 'asset custody destination')
        parent = destination.parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        destination = parent / destination.name
        try: destination.mkdir(mode=0o700)
        except FileExistsError: pass
        try:
            return self._retain_metadata(destination, visibility)
        except OSError as error:
            raise EmulatorWorkspaceError('asset custody retention failed (' + type(error).__name__ + ')', report=self._sealed) from None

    def _retain_metadata(self, destination, visibility):
        with _directory(destination) as directory:
            try:
                descriptor = os.open('emulator-assets.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=directory)
            except FileExistsError:
                existing = _file_state(descriptor=directory, name='emulator-assets.json')
                if existing.get('status') != 'regular' or existing.get('sha256') != self._report_sha256:
                    raise EmulatorWorkspaceError('asset custody artifact collides with existing bytes') from None
            else:
                with os.fdopen(descriptor, 'wb') as output:
                    source_fd = os.open(self.report_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(source_fd, 'rb', buffering=0) as source:
                        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                            raise EmulatorWorkspaceError('sealed asset custody metadata is not a regular file')
                        while block := source.read(1024 * 1024): output.write(block)
                    output.flush(); os.fsync(output.fileno())
            retained = _file_state(descriptor=directory, name='emulator-assets.json')
            if retained.get('status') != 'regular' or retained.get('sha256') != self._report_sha256:
                raise EmulatorWorkspaceError('asset custody metadata changed during retention')
        return [{'name': 'emulator-assets.json', 'path': str(destination / 'emulator-assets.json'),
                 'sha256': self._report_sha256, 'size': retained['size'], 'role': 'emulator_asset_custody',
                 'media_type': 'application/json', 'visibility': visibility}]
