"""Parent coordination for a session bridge and an optional owned emulator.

The caller owns its supervisor child. close() takes its cleanup assertion; a
false assertion remains restrictive. This object observes only its own bridge/
process cleanup. Exit codes and custody changes never infer a scientific or
physical outcome.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import time

from tinystudio.config import RuntimeTools
from .attempt_control import AttemptControl
from .command_profiles import _finite_json
from .emulator_workspace import EmulatorWorkspace
from .evidence_io import JsonlEvidenceWriter
from .hardware_emulator_selection import EmulatorProcessBinding, EmulatorSelection, environment_values
from .hardware_session_bridge import HardwareSessionBridge
from .hardware_session_client import HardwareSessionClient
from .hardware_session_configuration import HardwareSessionBinding, HardwareSessionRuntimePaths, SessionBridgePaths
from .managed_emulator import ManagedEmulatorProcess


class HardwareSessionRuntimeError(RuntimeError):
    def __init__(self, phase, primary_error_type, summary, *, artifacts=None):
        super().__init__('hardware session runtime failed during ' + phase + ' (' + primary_error_type + ')')
        self.phase, self.primary_error_type = phase, primary_error_type
        self.summary = deepcopy(summary)
        self.artifacts = deepcopy(artifacts or [])


def _public_shutdown(error):
    """Retain caller shutdown types without carrying private native arguments."""
    if isinstance(error, KeyboardInterrupt): return KeyboardInterrupt()
    if isinstance(error, SystemExit):
        code = error.code
        return SystemExit(code if code is None or type(code) is int else 1)
    return None


def _encoded(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _stable_file(path, *, destination=None, expected=None):
    """Bounded reads of a known regular metadata file; no FIFO/link following."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode): raise ValueError('parent metadata is not a regular file')
        checksum, size = hashlib.sha256(), 0
        output = None
        try:
            if destination is not None:
                output = destination.open('xb')
                destination.chmod(0o600)
            while block := os.read(descriptor, 1024 * 1024):
                checksum.update(block); size += len(block)
                if output is not None and output.write(block) != len(block):
                    raise OSError('parent metadata copy was incomplete')
            if output is not None: output.flush(); os.fsync(output.fileno())
        finally:
            if output is not None: output.close()
        if _identity(before) != _identity(os.fstat(descriptor)) or _identity(before) != _identity(Path(path).lstat()):
            raise ValueError('parent metadata changed while reading')
        digest = checksum.hexdigest()
        if expected is not None and digest != expected: raise ValueError('parent metadata digest changed')
        return digest, size
    finally:
        os.close(descriptor)


class HardwareSessionRuntime:
    """Factories share the public lifecycle contracts of the default components.

    Bridge.start()/ManagedEmulatorProcess.start() own their startup-error cleanup.
    Their unknown auto-cleanup is consumed, not retried here. Public evidence of
    a pure validation failure can instead prove that nothing was launched.
    """

    def __init__(self, binding, control, work, *, input_paths, emulator_selection=None,
                 emulator_binding=None, runtime_secrets=None, emulator_runtime_secrets=None,
                 base_env=None, tools=None, bridge_factory=None, workspace_factory=None,
                 emulator_factory=None, transport_factory=None, clock=time):
        if not isinstance(binding, HardwareSessionBinding) or not isinstance(control, AttemptControl):
            raise ValueError('runtime requires frozen hardware binding and AttemptControl')
        if not isinstance(input_paths, Mapping): raise ValueError('runtime requires frozen input paths')
        if (emulator_selection is None) != (emulator_binding is None):
            raise ValueError('emulator selection and operator binding must be supplied together')
        if emulator_selection is not None and (not isinstance(emulator_selection, EmulatorSelection)
                or not isinstance(emulator_binding, EmulatorProcessBinding)
                or emulator_selection.binding_id != emulator_binding.binding_id):
            raise ValueError('runtime requires matching frozen emulator selection and binding')
        if tools is not None and not isinstance(tools, RuntimeTools): raise ValueError('tools must be RuntimeTools')
        if runtime_secrets is not None:
            if type(runtime_secrets) is not dict: raise ValueError('session runtime secrets must be an object')
            _finite_json(runtime_secrets)
        if emulator_runtime_secrets is not None and (type(emulator_runtime_secrets) is not dict
                or emulator_runtime_secrets.keys() - {'environment'}):
            raise ValueError('emulator runtime secrets support only environment')
        private = environment_values((emulator_runtime_secrets or {}).get('environment', {}), 'emulator environment')
        self.binding, self.control = binding, control
        self.paths = HardwareSessionRuntimePaths(work)
        self.selection, self.emulator_binding = emulator_selection, emulator_binding
        self._input_paths = deepcopy(dict(input_paths))
        self._runtime_secrets = deepcopy(runtime_secrets)
        self._emulator_secrets = {'environment': private}
        self._base_env = environment_values({} if base_env is None else base_env, 'base_env')
        self._tools, self._clock = tools, clock
        self._bridge_factory = bridge_factory or HardwareSessionBridge
        self._workspace_factory = workspace_factory or EmulatorWorkspace
        self._emulator_factory = emulator_factory or ManagedEmulatorProcess
        self._transport_factory = transport_factory
        self.bridge = self.workspace = self.emulator = None
        self._writer = self._prepared = None
        self._state, self._phase = 'created', 'created'
        self._primary = None
        self._pending_shutdown = None
        self._errors = []
        self._attempted = {'bridge': False, 'emulator': False}
        self._start_entered = {'bridge': False, 'emulator': False}
        self._start_failed = {'bridge': False, 'emulator': False}
        self._bridge_outcome = self._emulator_snapshot = None
        self._component_cleanup = {'bridge': True, 'emulator': True}
        self._component_status = {'bridge': 'not_constructed', 'emulator': 'not_selected' if emulator_selection is None else 'not_constructed'}
        self._workspace = {'status': 'not_selected' if emulator_selection is None else 'not_prepared', 'report': None}
        self._close_attempted = False
        self._close_complete = False
        self._summary = None
        self._own_files = {}
        self._retained = {}
        self._close_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._close_requested = threading.Event()
        self._supervisor_uncertain = threading.Event()
        self._supervisor_assurance = {'prior_seal_not_attested': False, 'prior_exports_not_recalled': False}
        self._startup_active = False
        self._startup_thread = None

    def _error(self, phase, error):
        self._capture_shutdown(error)
        self._errors.append({'phase': phase, 'type': type(error).__name__})

    def _capture_shutdown(self, error):
        shutdown = _public_shutdown(error)
        if shutdown is not None and self._pending_shutdown is None:
            self._pending_shutdown = shutdown
        return shutdown is not None

    def _take_shutdown(self):
        shutdown, self._pending_shutdown = self._pending_shutdown, None
        return shutdown

    def _dated(self):
        values = {}
        for name, method in (('monotonic', 'monotonic'), ('wall_time', 'time')):
            try:
                value = getattr(self._clock, method)()
                _encoded(value)
                values[name] = value
            except BaseException as error:
                values[name] = None; self._error('metadata_clock', error)
                if isinstance(error, (KeyboardInterrupt, SystemExit)): raise
        return values

    def _record(self, event, **fields):
        if self._writer is not None:
            self._writer.write_record({'event': event, 'observed_at': self._dated(), **fields})

    def _allocate(self):
        work = self.paths.work if self.paths.work.is_absolute() else Path.cwd() / self.paths.work
        parent = work.parent.resolve(); parent.mkdir(parents=True, exist_ok=True)
        work = parent / work.name
        if work.is_symlink(): raise ValueError('runtime work entry must not be a symbolic link')
        work.mkdir(mode=0o700, exist_ok=True)
        self.paths = HardwareSessionRuntimePaths(work)
        evidence_parent = self.paths.evidence.parent
        if evidence_parent.is_symlink(): raise ValueError('runtime evidence parent must not be a symbolic link')
        evidence_parent.mkdir(mode=0o700, exist_ok=True)
        self.paths.evidence.mkdir(mode=0o700, exist_ok=False)
        self._writer = JsonlEvidenceWriter(self.paths.lifecycle, self.binding.evidence, self._clock).__enter__()
        self.paths.lifecycle.chmod(0o600)

    def start(self):
        with self._lifecycle_lock:
            self._startup_thread = threading.get_ident()
            self._startup_active = True
            try: return self._start()
            finally:
                self._startup_active = False
                self._startup_thread = None

    def _start(self):
        if self._state != 'created' or self._close_attempted:
            raise RuntimeError('hardware session runtime cannot be restarted')
        self._state = 'starting'
        try:
            self._phase = 'authority'
            if self._close_requested.is_set(): raise RuntimeError('runtime close was requested before startup')
            self.control.check()
            self._phase = 'metadata_open'; self._allocate()
            self._record('runtime_opening', binding_id=self.binding.binding_id,
                         emulator_selection_sha256=self.selection.digest if self.selection else None)
            if self.selection is not None:
                self._phase = 'workspace_construct'
                self.workspace = self._workspace_factory(self.paths.work, self.selection)
                self._phase = 'workspace_prepare'
                self._prepared = self.workspace.prepare(self._input_paths, check_authority=self.control.check)
                self._workspace['status'] = 'prepared'
                self._phase = 'emulator_launch_selection'
                launch = self.workspace.launch_selection(self.emulator_binding, tools=self._tools,
                    base_env=self._base_env, runtime_secrets=self._emulator_secrets, check_authority=self.control.check)
                self._phase = 'emulator_construct'; self._attempted['emulator'] = True
                self.emulator = self._emulator_factory(launch, self.control, self.paths.emulator,
                    runtime_secrets=self._emulator_secrets, base_env=self._base_env,
                    clock=self._clock, evidence_policy=self.binding.evidence)
                if self.emulator is None: raise RuntimeError('emulator factory returned no ownership handle')
                self._phase = 'emulator_start'; self._start_entered['emulator'] = True
                try: self.emulator.start()
                except BaseException:
                    self._start_failed['emulator'] = True; raise
                self._component_status['emulator'] = 'started'
                self._phase = 'emulator_readiness'
                readiness = self.emulator.wait_ready()
                self._record('emulator_ready', readiness=readiness)
            self._phase = 'bridge_construct'; self.control.check(); self._attempted['bridge'] = True
            options = {'runtime_secrets': self._runtime_secrets}
            if self._transport_factory is not None: options['transport_factory'] = self._transport_factory
            self.bridge = self._bridge_factory(self.binding, self.control, self.paths.bridge, **options)
            if self.bridge is None: raise RuntimeError('bridge factory returned no ownership handle')
            self._phase = 'bridge_start'; self._start_entered['bridge'] = True
            try: self.bridge.start()
            except BaseException:
                self._start_failed['bridge'] = True; raise
            self._component_status['bridge'] = 'started'
            self.control.check()
            self._record('bridge_started')
            self._state, self._phase = 'started', 'started'
            return self
        except BaseException as error:
            primary = {'phase': self._phase, 'type': type(error).__name__}
            self._primary = primary
            self._capture_shutdown(error)
            # Startup owns the lifecycle lock. Finish cleanup without delivering
            # a second signal inside this original native exception handler.
            self._close_requested.set()
            self._close()
            self._refresh_supervisor_assurance()
            public_error = self._take_shutdown()
            if public_error is None:
                public_error = HardwareSessionRuntimeError(primary['phase'], primary['type'], self._summary)
        # Raise outside the native exception handler: even __context__ must not
        # retain the original exception's private diagnostic/credential text.
        raise public_error from None

    @property
    def descriptor(self):
        if self.bridge is None or self._state != 'started' or self._close_requested.is_set():
            raise RuntimeError('hardware session runtime has no active descriptor')
        descriptor = deepcopy(self.bridge.descriptor)
        # The existing client validates exact descriptor fields without I/O.
        HardwareSessionClient(descriptor).close()
        return descriptor

    def poll(self):
        observation = None
        if self.emulator is not None:
            try: observation = deepcopy(self.emulator.poll())
            except Exception as error: observation = {'status': 'unconfirmed', 'error_type': type(error).__name__}
        return {'state': self._state, 'emulator': observation,
                'workspace': self._prepared.as_dict() if self._prepared is not None else None,
                'qualification': {'scientific_outcome': 'not_inferred', 'physical_outcome': 'unverified'}}

    def _close_bridge(self):
        if self.bridge is None:
            self._component_cleanup['bridge'] = not self._attempted['bridge']
            self._component_status['bridge'] = 'ownership_unavailable' if self._attempted['bridge'] else 'not_constructed'
            return
        if not self._start_failed['bridge']:
            try: self._bridge_outcome = deepcopy(self.bridge.close())
            except BaseException as error: self._error('bridge_cleanup', error)
        else:
            self._component_status['bridge'] = 'startup_cleanup_consumed'
        try: self._component_cleanup['bridge'] = self.bridge.cleanup_confirmed is True
        except BaseException as error:
            self._component_cleanup['bridge'] = False; self._error('bridge_cleanup_observation', error)
        if not self._component_cleanup['bridge']:
            self._component_status['bridge'] = 'cleanup_unconfirmed'
        else:
            self._component_status['bridge'] = 'cleanup_confirmed'

    def _close_emulator(self):
        if self.emulator is None:
            self._component_cleanup['emulator'] = not self._attempted['emulator']
            if self._attempted['emulator']: self._component_status['emulator'] = 'ownership_unavailable'
            return
        if not self._start_failed['emulator']:
            try:
                trigger = (self._bridge_outcome or {}).get('termination', {}).get('trigger')
                reason = 'cancelled' if trigger == 'cancel' else 'authority_lost' if trigger == 'expiry' else 'startup_failed' if self._primary else 'completed'
                self.emulator.close(reason)
            except BaseException as error: self._error('emulator_cleanup', error)
        try:
            self._emulator_snapshot = deepcopy(self.emulator.snapshot())
            snapshot = self._emulator_snapshot
            pure = (self._start_failed['emulator'] and snapshot.get('state') == 'created'
                and snapshot.get('launch', {}).get('attempted') is False
                and not snapshot.get('evidence') and not snapshot.get('logs') and not snapshot.get('helpers'))
            self._component_cleanup['emulator'] = snapshot.get('cleanup_confirmed') is True or pure
            self._component_status['emulator'] = ('not_launched_publicly_observed' if pure else
                'cleanup_confirmed' if self._component_cleanup['emulator'] else 'cleanup_unconfirmed')
        except BaseException as error:
            self._component_cleanup['emulator'] = False; self._error('emulator_cleanup_observation', error)

    def _receipt(self):
        if self._writer is None: return None
        receipt = self._writer.receipt()
        receipt['errors'] = [{key: value for key, value in error.items() if key != 'message'} for error in receipt['errors']]
        return receipt

    def _make_summary(self):
        return {'format': 'tinystudio.hardware-session-runtime.v1', 'state': self._state,
            'cleanup_confirmed': all(self._component_cleanup.values()),
            'cleanup_scope': 'owned_bridge_and_emulator_only; supervisor_cleanup_is_a_caller_assertion',
            'writer_quiescence': self._writer_quiescence(),
            'supervisor_assurance': deepcopy(self._supervisor_assurance),
            'bridge': {'status': self._component_status['bridge'], 'cleanup_confirmed': self._component_cleanup['bridge'],
                       'outcome': deepcopy(self._bridge_outcome)},
            'emulator': {'status': self._component_status['emulator'], 'cleanup_confirmed': self._component_cleanup['emulator'],
                         'observation': deepcopy(self._emulator_snapshot)},
            'workspace': deepcopy(self._workspace), 'primary_error': deepcopy(self._primary),
            'metadata_complete': self._writer is not None and self._receipt()['closed'] and not self._errors
                and (self.workspace is None or self._workspace['status'] == 'sealed'),
            'errors': deepcopy(self._errors), 'evidence': self._receipt(),
            'qualification': {'scientific_outcome': 'not_inferred', 'physical_outcome': 'unverified',
                'model_fidelity': 'unverified', 'trusted_native_same_uid_not_isolated': True,
                'executed_binary_attestation': False, 'hard_real_time_guarantee': False}}

    @property
    def cleanup_confirmed(self):
        return self._close_complete and all(self._component_cleanup.values())

    def _writer_quiescence(self):
        confirmed = not self._supervisor_uncertain.is_set()
        own = all(self._component_cleanup.values())
        return {'supervisor_cleanup_confirmed': confirmed, 'supervisor_scope': 'caller_assertion_not_process_observation',
                'own_cleanup_confirmed': own, 'all_writers_quiescent': own and confirmed,
                'false_latched': not confirmed}

    def _invalidate_prior_seal(self):
        if self._workspace['status'] == 'sealed':
            self._workspace = {'status': 'prior_seal_not_attested', 'report': None,
                               'prior_report': deepcopy(self._workspace['report'])}
            self._supervisor_assurance['prior_seal_not_attested'] = True

    def _refresh_supervisor_assurance(self):
        if not self._supervisor_uncertain.is_set(): return
        previous = self._summary or {}
        stale = previous.get('writer_quiescence', {}).get('supervisor_cleanup_confirmed') is True
        stale = stale or self._workspace['status'] == 'sealed'
        if not stale: return
        self._invalidate_prior_seal()
        self._supervisor_assurance['prior_exports_not_recalled'] = bool(self._retained)
        self._retained.clear()
        # Previously hashed manifests/reports remain historical bytes. Publish
        # a separate current correction, never silently revise their contents.
        prior_manifest = self._own_files.pop(self.paths.manifest, None)
        self._summary = self._make_summary()
        correction = {**self._summary, 'supersedes_runtime_manifest_sha256': prior_manifest}
        if self._writer is not None:
            try:
                with self.paths.assurance.open('xb') as stream:
                    self.paths.assurance.chmod(0o600)
                    stream.write(_encoded(correction)); stream.flush()
                    if self.binding.evidence.fsync: os.fsync(stream.fileno())
                self._own_files[self.paths.assurance] = _stable_file(self.paths.assurance)[0]
            except BaseException as error:
                self._error('supervisor_assurance_metadata', error)
                self._summary = self._make_summary()

    def close(self, *, supervisor_cleanup_confirmed=True):
        if type(supervisor_cleanup_confirmed) is not bool:
            raise ValueError('supervisor_cleanup_confirmed must be a boolean caller assertion')
        if not supervisor_cleanup_confirmed: self._supervisor_uncertain.set()
        self._close_requested.set()
        if self._startup_active and self._startup_thread != threading.get_ident() and not self._close_attempted:
            # A concurrent shutdown must wake cooperative prepare/probe checks,
            # then wait for their writers before it can seal custody. Existing
            # cancel/expiry reasons remain latched by AttemptControl.
            self.control.revoke('parent_session_closed', termination='release')
        with self._lifecycle_lock:
            self._close()
            self._refresh_supervisor_assurance()
            summary = deepcopy(self._summary)
            shutdown = self._take_shutdown()
        if shutdown is not None: raise shutdown from None
        return summary

    def _close(self):
        with self._close_lock:
            if self._close_attempted: return deepcopy(self._summary)
            self._close_attempted = True
            self._close_bridge()
            self._close_emulator()
            if self.workspace is not None:
                if self._writer_quiescence()['all_writers_quiescent']:
                    try: self._workspace = {'status': 'sealed', 'report': self.workspace.seal()}
                    except BaseException as error:
                        self._workspace = {'status': 'seal_failed', 'report': deepcopy(getattr(error, 'report', None))}
                        self._error('workspace_seal', error)
                elif not all(self._component_cleanup.values()):
                    self._workspace = {'status': 'not_sealed_cleanup_unconfirmed', 'report': None}
                else: self._workspace = {'status': 'not_sealed_supervisor_cleanup_unconfirmed', 'report': None}
                if self._supervisor_uncertain.is_set(): self._invalidate_prior_seal()
            self._state = 'closed'
            try: self._record('runtime_closed', cleanup_confirmed=all(self._component_cleanup.values()),
                              writer_quiescence=self._writer_quiescence(), primary_error=self._primary)
            except BaseException as error: self._error('metadata_write', error)
            if self._writer is not None:
                try: self._writer.close()
                except BaseException as error: self._error('metadata_close', error)
                if self._writer.receipt()['closed']:
                    try: self._own_files[self.paths.lifecycle] = _stable_file(self.paths.lifecycle)[0]
                    except BaseException as error: self._error('metadata_identity', error)
            self._runtime_secrets = self._emulator_secrets = None
            self._base_env.clear(); self._input_paths.clear()
            self._summary = self._make_summary()
            if self._writer is not None:
                try:
                    with self.paths.manifest.open('xb') as stream:
                        self.paths.manifest.chmod(0o600)
                        stream.write(_encoded(self._summary)); stream.flush()
                        if self.binding.evidence.fsync: os.fsync(stream.fileno())
                    self._own_files[self.paths.manifest] = _stable_file(self.paths.manifest)[0]
                except BaseException as error:
                    self._error('metadata_manifest', error); self._summary = self._make_summary()
            summary = deepcopy(self._summary)
            self._close_complete = True
            return summary

    def _retain_known(self, source, destination, visibility, *, role='hardware_session_runtime', expected=None, name=None):
        name = name or source.name
        checksum, size = _stable_file(source, expected=expected)
        target = destination / name
        if target.exists():
            if _stable_file(target, expected=checksum)[0] != checksum: raise ValueError('parent artifact collision')
        else: _stable_file(source, destination=target, expected=checksum)
        _stable_file(target, expected=checksum)
        target.chmod(0o444)
        return {'name': name, 'path': str(target), 'sha256': checksum, 'size': size, 'role': role,
                'media_type': 'application/x-ndjson' if source.suffix == '.jsonl' else 'application/json', 'visibility': visibility}

    def retain_artifacts(self, destination, *, visibility='evaluation'):
        with self._lifecycle_lock:
            if not self._close_complete:
                raise RuntimeError('parent runtime close must complete before evidence retention')
            self._refresh_supervisor_assurance()
            artifacts = None
            if self._pending_shutdown is None:
                artifacts = self._retain_artifacts(destination, visibility=visibility)
            shutdown = self._take_shutdown()
        if shutdown is not None: raise shutdown from None
        return artifacts

    def _retain_artifacts(self, destination, *, visibility):
        if visibility not in {'model', 'evaluation', 'truth'}: raise ValueError('invalid artifact visibility')
        artifacts = []
        try:
            destination = Path(destination).resolve(); key = (str(destination), visibility)
            if key in self._retained:
                for artifact in self._retained[key]:
                    _stable_file(Path(artifact['path']), expected=artifact['sha256'])
                    artifacts.append(deepcopy(artifact))
                return artifacts
            destination.mkdir(parents=True, exist_ok=True, mode=0o700)
            for source, checksum in self._own_files.items():
                artifacts.append(self._retain_known(source, destination, visibility, expected=checksum))
            if self.bridge is not None and self._component_cleanup['bridge']:
                bridge_paths = SessionBridgePaths(self.paths.bridge, Path('.'))
                for source in (bridge_paths.decisions, bridge_paths.manifest):
                    if source.exists() or source.is_symlink():
                        artifacts.append(self._retain_known(source, destination, visibility,
                            role='hardware_session_parent', name=bridge_paths.exported(self.control.identity.owner, source)))
            core_metadata = (self.paths.emulator.lifecycle, self.paths.emulator.readiness, self.paths.emulator.manifest)
            if self.emulator is not None and self._writer_quiescence()['all_writers_quiescent'] and all(path.is_file() for path in core_metadata):
                artifacts.extend(self.emulator.retain_artifacts(destination, visibility=visibility))
            elif self.emulator is not None:
                # Preserve only explicitly known closed parent metadata. Raw logs
                # and mutable workspace files stay private on uncertain cleanup.
                snapshot = self._emulator_snapshot or {}
                receipts = snapshot.get('evidence', {})
                for channel, path in (('lifecycle', self.paths.emulator.lifecycle), ('readiness', self.paths.emulator.readiness)):
                    if receipts.get(channel, {}).get('closed') is True and path.exists():
                        artifacts.append(self._retain_known(path, destination, visibility,
                            role='managed_emulator_parent', name='managed-emulator-' + path.name))
                if receipts and all(value.get('closed') is True for value in receipts.values()) and self.paths.emulator.manifest.exists():
                    artifacts.append(self._retain_known(self.paths.emulator.manifest, destination, visibility,
                        role='managed_emulator_parent', name='managed-emulator-' + self.paths.emulator.manifest.name))
            if self.workspace is not None and self._writer_quiescence()['all_writers_quiescent'] and self._workspace['status'] == 'sealed':
                artifacts.extend(self.workspace.retain_artifacts(destination, visibility=visibility))
        except BaseException as error:
            if self._capture_shutdown(error):
                public_error = None
            else:
                public_error = HardwareSessionRuntimeError('artifact_retention', type(error).__name__, self._summary, artifacts=artifacts)
        else:
            self._retained[key] = deepcopy(artifacts)
            return artifacts
        if public_error is not None: raise public_error from None
        return None
