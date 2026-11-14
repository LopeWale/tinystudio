"""An explicitly selected, parent-owned native process and scoped readiness.

This is lifecycle supervision, not device identity, physical qualification,
application acknowledgement or a hard real-time deadline. Raw logs are private
unless their unchanged captured bytes are explicitly selected for export.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import fcntl
import json
import os
from pathlib import Path
import selectors
import shutil
import stat
import subprocess
import struct
import sys
import threading
import termios
import time

from .attempt_control import AttemptAuthorityLost, AttemptControl
from .evidence_io import JsonlEvidenceWriter, RecordWritePolicy
from .managed_process_configuration import ManagedProcessPaths, ManagedProcessSelection


class ManagedEmulatorError(RuntimeError):
    """Public diagnostics intentionally omit command, environment and OS text."""


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _file_sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _environment(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(label + ' must be an explicit environment mapping')
    result = {}
    for key, item in value.items():
        if (type(key) is not str or not key or '=' in key or '\0' in key
                or type(item) is not str or '\0' in item):
            raise ValueError(label + ' must contain valid environment strings')
        try:
            key.encode('utf-8'); item.encode('utf-8')
        except UnicodeError:
            raise ValueError(label + ' must contain UTF-8 strings') from None
        result[key] = item
    return result


class _LogDrain:
    """Nonblocking reads with a private wake pipe; no blocked read to abandon."""

    def __init__(self, paths, streams, selection):
        self._selector = selectors.DefaultSelector()
        self._wake_read, self._wake_write = os.pipe()
        os.set_blocking(self._wake_read, False)
        self._selector.register(self._wake_read, selectors.EVENT_READ, None)
        self._lock = threading.Lock()
        self._stop = False
        self._thread = None
        self._streams = {}
        self._marker = selection.readiness.marker if selection.readiness.kind == 'log_bytes' else None
        self._marker_stream = selection.readiness.stream
        self._limit = selection.max_log_bytes
        try:
            for name in ('stdout', 'stderr'):
                output = paths.private_log(name).open('xb', buffering=0)
                paths.private_log(name).chmod(0o600)
                stream = streams.get(name)
                state = {'output': output, 'input': stream, 'observed': 0, 'captured': 0,
                         'observed_hash': hashlib.sha256(), 'captured_hash': hashlib.sha256(),
                         'tail': b'', 'matched': self._marker == b'' and name == self._marker_stream,
                         'eof': False, 'errors': []}
                self._streams[name] = state
                if stream is not None:
                    os.set_blocking(stream.fileno(), False)
                    self._selector.register(stream, selectors.EVENT_READ, name)
        except BaseException:
            self.stop()
            raise

    def start(self):
        self._thread = threading.Thread(target=self._run, name='managed-emulator-log-drain', daemon=False)
        self._thread.start()

    def _consume(self, name, block):
        state = self._streams[name]
        with self._lock:
            state['observed'] += len(block)
            state['observed_hash'].update(block)
            if name == self._marker_stream and self._marker is not None and not state['matched']:
                combined = state['tail'] + block
                state['matched'] = self._marker in combined
                state['tail'] = combined[-(len(self._marker) - 1):] if len(self._marker) > 1 else b''
            capture = block if self._limit is None else block[:max(0, self._limit - state['captured'])]
            if capture and not state['errors']:
                try:
                    count = state['output'].write(capture)
                    # A partial write is evidence too; never describe the entire chunk as captured.
                    if count:
                        state['captured'] += count
                        state['captured_hash'].update(capture[:count])
                    if count != len(capture):
                        raise OSError('incomplete raw log write')
                except Exception as error:
                    state['errors'].append({'operation': 'capture', 'type': type(error).__name__})

    def _run(self):
        try:
            while True:
                events = self._selector.select()
                stopped = False
                for key, _ in events:
                    if key.data is None:
                        try: os.read(self._wake_read, 4096)
                        except BlockingIOError: pass
                        stopped = self._stop
                        continue
                    name = key.data
                    # Bound each scheduling turn, not total observed/captured bytes.
                    try:
                        block = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    except Exception as error:
                        with self._lock:
                            self._streams[name]['errors'].append({'operation': 'read', 'type': type(error).__name__})
                        self._selector.unregister(key.fileobj)
                        continue
                    if block:
                        self._consume(name, block)
                    else:
                        with self._lock: self._streams[name]['eof'] = True
                        self._selector.unregister(key.fileobj)
                if stopped:
                    # Freeze the amount currently queued in each pipe. A live
                    # process after uncertain cleanup cannot extend this join
                    # indefinitely by continually supplying more output.
                    for key in list(self._selector.get_map().values()):
                        if key.data is None: continue
                        try:
                            pending = struct.unpack('I', fcntl.ioctl(key.fd, termios.FIONREAD, struct.pack('I', 0)))[0]
                            while pending:
                                block = os.read(key.fd, min(pending, 65536))
                                if not block: break
                                self._consume(key.data, block)
                                pending -= len(block)
                            # EOF is affirmative evidence; an unavailable read
                            # after the cutoff does not establish completeness.
                            if self._selector.select(0):
                                try: extra = os.read(key.fd, 1)
                                except BlockingIOError: extra = None
                                if extra: self._consume(key.data, extra)
                                elif extra == b'':
                                    with self._lock: self._streams[key.data]['eof'] = True
                        except Exception as error:
                            with self._lock:
                                self._streams[key.data]['errors'].append({'operation': 'read', 'type': type(error).__name__})
                    return
        except BaseException as error:
            with self._lock:
                for state in self._streams.values():
                    state['errors'].append({'operation': 'read', 'type': type(error).__name__})

    def stop(self):
        if self._selector is None:
            return
        self._stop = True
        if self._thread is not None:
            os.write(self._wake_write, b'x')
            self._thread.join()
        for state in self._streams.values():
            for label in ('input', 'output'):
                stream = state[label]
                if stream is not None:
                    try: stream.close()
                    except Exception as error:
                        state['errors'].append({'operation': label + '_close', 'type': type(error).__name__})
        self._selector.close()
        self._selector = None
        os.close(self._wake_read); os.close(self._wake_write)

    def snapshot(self):
        with self._lock:
            return {name: {'observed_bytes': state['observed'], 'captured_bytes': state['captured'],
                          'observed_sha256': state['observed_hash'].hexdigest(),
                          'captured_sha256': state['captured_hash'].hexdigest(),
                          'capture_truncated': state['captured'] < state['observed'],
                          'observation_complete': state['eof'] and not any(e['operation'] == 'read' for e in state['errors']),
                          'marker_observed': state['matched'], 'errors': deepcopy(state['errors']),
                          'max_log_bytes': self._limit, 'budget_scope': 'per_stream',
                          'writer_closed': state['output'].closed}
                    for name, state in self._streams.items()}


class ManagedEmulatorProcess:
    """Single launch; only this object's main/helper groups may be torn down.

    Environment precedence is explicit base_env, selected public_env, then
    private runtime_secrets.environment. The ambient application environment is
    never copied. A trusted child can still disclose its own secrets in raw logs.
    """

    def __init__(self, selection, control, paths, *, runtime_secrets=None, base_env=None,
                 process_factory=None, clock=time, evidence_policy=None):
        if not isinstance(selection, ManagedProcessSelection) or not isinstance(paths, ManagedProcessPaths):
            raise ValueError('managed process requires frozen selection and paths')
        if not isinstance(control, AttemptControl):
            raise ValueError('managed process requires AttemptControl')
        if runtime_secrets is not None and (type(runtime_secrets) is not dict or runtime_secrets.keys() - {'environment'}):
            raise ValueError('runtime_secrets supports only an explicit environment mapping')
        private = _environment((runtime_secrets or {}).get('environment'), 'runtime_secrets.environment')
        base = _environment(base_env, 'base_env')
        self.selection, self.control, self.paths = selection, control, paths
        self._env = base | dict(selection.public_env) | private
        self._environment = {'inherit_ambient_environment': False,
            'precedence': ['base_env', 'public_env', 'runtime_secrets.environment'],
            'base_keys': sorted(base), 'public_keys': sorted(selection.public_env), 'private_keys': sorted(private),
            'values_published': False}
        self._factory, self._clock = process_factory, clock
        self._policy = evidence_policy if evidence_policy is not None else RecordWritePolicy()
        if not isinstance(self._policy, RecordWritePolicy):
            raise ValueError('evidence_policy must be RecordWritePolicy')
        self._state = 'created'
        self._group = self._drain = None
        self._writers = {}
        self._started_at = self._closed_at = None
        self._started_monotonic = None
        self._helpers = []
        self._helper_groups = []
        self._ownership_lock = threading.RLock()
        self._writer_lock = threading.RLock()
        self._owns_evidence = False
        self._launch_attempted = False
        self._close_attempted = False
        self._cleanup = False
        self._errors = []
        self._exit_code = None
        self._readiness = {'ready': False, 'status': 'not_checked', 'scope': selection.readiness.scope}
        self._executable = {'scope': 'observed_file_not_execution_attestation', 'status': 'not_observed'}
        self._selection_sha = _sha(selection.as_dict())

    def _dated(self):
        return {'monotonic': self._clock.monotonic(), 'wall_time': self._clock.time()}

    def _error(self, phase, error):
        self._errors.append({'phase': phase, 'type': type(error).__name__})

    def _record(self, channel, event, **fields):
        with self._writer_lock:
            writer = self._writers.get(channel)
            if writer is not None and not writer.receipt()['closed']:
                writer.write_record({'event': event, 'observed_at': self._dated(), **fields})

    def _launch(self, argv, **kwargs):
        factory = self._factory
        if factory is None:
            from .runner import ProcessGroup
            factory = ProcessGroup
        return factory(list(argv), grace=self.selection.shutdown_grace_seconds, stdin=subprocess.DEVNULL,
                       env=dict(self._env), **kwargs)

    def _observe_executable(self):
        # This observation is separate from kernel execution and makes no TOCTOU attestation.
        try:
            command = self.selection.argv[0]
            cwd = self.selection.cwd
            if not cwd.is_absolute(): cwd = Path.cwd() / cwd
            if os.path.dirname(command):
                candidate = Path(command)
                if not candidate.is_absolute(): candidate = cwd / candidate
            else:
                # exec searches relative and empty PATH entries after changing
                # into the selected process cwd. Observe the same candidate,
                # without changing any authored argv, environment or paths.
                search = os.pathsep.join(entry if Path(entry).is_absolute() else str(cwd / entry)
                                         for entry in self._env.get('PATH', os.defpath).split(os.pathsep))
                found = shutil.which(command, path=search)
                if found is None: return
                candidate = Path(found)
            # Legitimate executable aliases may be symlinks. Follow the alias,
            # but never block on or read a replaced FIFO/device after launch.
            descriptor = os.open(candidate, os.O_RDONLY | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError('observed executable is not a regular file')
                digest, size = hashlib.sha256(), 0
                while block := os.read(descriptor, 1024 * 1024):
                    digest.update(block); size += len(block)
            finally:
                os.close(descriptor)
            self._executable = {'scope': 'observed_file_not_execution_attestation', 'status': 'observed',
                                'sha256': digest.hexdigest(), 'size': size,
                                'observed_at': self._dated(), 'runtime_version': 'not_observed'}
        except Exception as error:
            self._executable['status'] = 'observation_failed'
            self._executable['error_type'] = type(error).__name__

    def start(self):
        if self._state != 'created' or self._close_attempted:
            raise ManagedEmulatorError('managed process permits only one launch')
        self.control.check()
        self._started_at = self._dated()
        self._started_monotonic = self._started_at['monotonic']
        self._state = 'starting'
        try:
            for directory in (self.paths.evidence_directory, self.paths.private_directory):
                directory.mkdir(parents=True, exist_ok=False, mode=0o700)
                directory.chmod(0o700)
                if directory == self.paths.evidence_directory: self._owns_evidence = True
            for name, path in (('lifecycle', self.paths.lifecycle), ('readiness', self.paths.readiness)):
                if path.exists(): raise ManagedEmulatorError('managed evidence path already exists')
                self._writers[name] = JsonlEvidenceWriter(path, self._policy, self._clock).__enter__()
                path.chmod(0o600)
            self._record('lifecycle', 'launch_requested', selection_sha256=self._selection_sha)
            self.control.check()
            with self._ownership_lock:
                if self._close_attempted:
                    raise ManagedEmulatorError('managed process launch was interrupted by close')
                self._launch_attempted = True
                self._group = self._launch(self.selection.argv, cwd=self.selection.cwd,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self._drain = _LogDrain(self.paths, {name: getattr(self._group.process, name, None)
                                                  for name in ('stdout', 'stderr')}, self.selection)
                self._drain.start()
                self.control.check()
                if self._close_attempted:
                    raise ManagedEmulatorError('managed process launch was interrupted by close')
                self._state = 'running'
                self._record('lifecycle', 'launched', pid=self._group.process.pid, pid_role='owned_main_process')
            self._observe_executable()
            return self.snapshot()
        except BaseException as error:
            self._error('launch', error)
            try: self.close('startup_failed')
            except Exception: pass  # Original launch/authority failure remains primary.
            if isinstance(error, (AttemptAuthorityLost, ManagedEmulatorError, KeyboardInterrupt, SystemExit)):
                raise
            raise ManagedEmulatorError('managed process launch failed (' + type(error).__name__ + ')') from None

    def _remaining(self):
        remaining = self.control.remaining()
        selected = self.selection.startup_timeout_seconds
        if selected is not None:
            remaining = min(remaining, selected - (self._clock.monotonic() - self._started_monotonic))
        return max(0., remaining)

    def _sleep(self, remaining):
        self._clock.sleep(min(self.selection.poll_interval_seconds, remaining))

    def _ready_result(self, status, *, ready=False, **extra):
        if self._close_attempted:
            raise ManagedEmulatorError('managed readiness was interrupted by close')
        self._readiness = {'ready': ready, 'status': status, 'scope': self.selection.readiness.scope,
                           'observed_at': self._dated(), **extra}
        self._record('readiness', 'readiness_observation', **self._readiness)
        if ready: self._state = 'ready'
        return deepcopy(self._readiness)

    def _probe(self, argv, cwd):
        self.control.check()
        if self._remaining() <= 0:
            return None
        record = {'kind': self.selection.readiness.kind, 'cleanup_confirmed': False, 'pid': None,
                  'pid_role': 'owned_readiness_helper', 'exit_code': None, 'started_at': self._dated(),
                  'ownership_handle_returned': False}
        owned = None
        try:
            with self._ownership_lock:
                if self._close_attempted:
                    raise ManagedEmulatorError('managed readiness was interrupted by close')
                self._helpers.append(record)
                group = self._launch(argv, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                owned = {'group': group, 'record': record, 'lock': threading.Lock(), 'close_attempted': False}
                self._helper_groups.append(owned)
                record['pid'] = group.process.pid
                record['ownership_handle_returned'] = True
            self.control.check()
            while True:
                if self._close_attempted:
                    raise ManagedEmulatorError('managed readiness was interrupted by close')
                self.control.check()
                remaining = self._remaining()
                with owned['lock']:
                    status = group.process.poll()
                if status is not None:
                    record['exit_code'] = status
                    record['observed_at'] = self._dated()
                    return status if remaining > 0 else None
                if remaining <= 0:
                    return None
                self._sleep(remaining)
        except BaseException as error:
            record['error_type'] = type(error).__name__
            if isinstance(error, (AttemptAuthorityLost, ManagedEmulatorError, KeyboardInterrupt, SystemExit)):
                raise
            raise ManagedEmulatorError('managed readiness helper failed (' + type(error).__name__ + ')') from None
        finally:
            if owned is not None and not self._close_helper(owned):
                # Preserve a primary authority/predicate exception while exposing
                # independently unconfirmed cleanup in the public snapshot.
                if sys.exc_info()[0] is None:
                    raise ManagedEmulatorError('managed readiness helper cleanup is unconfirmed') from None
            if owned is None:
                record['closed_at'] = self._dated()

    def _close_helper(self, owned):
        with owned['lock']:
            record = owned['record']
            if not owned['close_attempted']:
                owned['close_attempted'] = True
                try:
                    owned['group'].close()
                    record['cleanup_confirmed'] = bool(owned['group'].closed)
                    record['exit_code'] = owned['group'].process.poll()
                except Exception as error:
                    record['cleanup_error_type'] = type(error).__name__
                    self._error('helper_cleanup', error)
                record['closed_at'] = self._dated()
            return record['cleanup_confirmed']

    def wait_ready(self):
        if self._group is None or self._close_attempted:
            raise ManagedEmulatorError('managed readiness requires a running owned process')
        self.control.check()
        if self._readiness['status'] != 'not_checked':
            if self._readiness['ready']: return deepcopy(self._readiness)
            raise ManagedEmulatorError('managed readiness predicate was not satisfied')
        selected = self.selection.readiness
        try:
            while True:
                if self._close_attempted:
                    raise ManagedEmulatorError('managed readiness was interrupted by close')
                self.control.check()
                status = self.poll()['exit_code']
                if status is not None:
                    self._ready_result('process_exited', exit_code=status)
                    break
                if (self.selection.startup_timeout_seconds not in {None, 0}
                        and self._remaining() <= 0):
                    self._ready_result('timed_out')
                    break
                if selected.kind == 'process_running':
                    return self._ready_result('satisfied', ready=True)
                if selected.kind == 'log_bytes' and self._drain.snapshot()[selected.stream]['marker_observed']:
                    return self._ready_result('satisfied', ready=True)
                remaining = self._remaining()
                if remaining <= 0:
                    self._ready_result('not_observed' if self.selection.startup_timeout_seconds == 0 else 'timed_out')
                    break
                if selected.kind == 'command':
                    result = self._probe(selected.argv, selected.cwd or self.selection.cwd)
                    self.control.check()
                    if self.poll()['exit_code'] is not None:
                        self._ready_result('process_exited', exit_code=self._exit_code)
                    elif result is None or self._remaining() <= 0:
                        self._ready_result('timed_out')
                    else:
                        ready = result == selected.expected_exit_code
                        self._ready_result('satisfied' if ready else 'predicate_failed', ready=ready, exit_code=result)
                        if ready: return deepcopy(self._readiness)
                    break
                if selected.kind == 'tcp':
                    # DNS/connect may block in stdlib. Its entire process is owned,
                    # so outer authority can terminate it without abandoning a thread.
                    code = 'import socket,sys; s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=float(sys.argv[3])); s.close()'
                    result = self._probe((sys.executable, '-I', '-c', code, selected.host, str(selected.port), str(remaining)), self.selection.cwd)
                    self.control.check()
                    if result == 0 and self._remaining() > 0 and self.poll()['exit_code'] is None:
                        return self._ready_result('satisfied', ready=True)
                self._sleep(self._remaining())
        except BaseException as error:
            if not self._close_attempted and self._readiness['status'] == 'not_checked':
                self._ready_result('interrupted', error_type=type(error).__name__)
            raise
        raise ManagedEmulatorError('managed readiness predicate was not satisfied')

    def poll(self):
        if self._group is not None:
            try:
                with self._ownership_lock:
                    self._exit_code = self._group.process.poll()
            except Exception as error:
                self._error('process_status', error)
                raise ManagedEmulatorError('managed process status is unconfirmed') from None
            if self._exit_code is not None and not self._close_attempted:
                self._state = 'exited'
        return self.snapshot()

    def _writer_receipts(self):
        receipts = {}
        for name, writer in self._writers.items():
            receipt = writer.receipt()
            receipt['errors'] = [{key: value for key, value in error.items() if key != 'message'} for error in receipt['errors']]
            receipts[name] = receipt
        return receipts

    def snapshot(self):
        return {'format': 'tinystudio.managed-emulator.v1', 'state': self._state,
                'selection_sha256': self._selection_sha, 'pid': self._group.process.pid if self._group else None,
                'launch': {'attempted': self._launch_attempted, 'ownership_handle_returned': self._group is not None,
                           'no_handle_cleanup_scope': 'not_independently_confirmed' if self._launch_attempted and self._group is None else None},
                'pid_role': 'owned_main_process', 'exit_code': self._exit_code, 'started_at': deepcopy(self._started_at),
                'closed_at': deepcopy(self._closed_at), 'cleanup_confirmed': self._cleanup,
                'readiness': deepcopy(self._readiness), 'helpers': deepcopy(self._helpers),
                'logs': self._drain.snapshot() if self._drain else {},
                'raw_log_policy': {**self.selection.raw_log_policy, 'max_log_bytes': self.selection.max_log_bytes,
                                   'max_log_bytes_scope': 'per_stream', 'continue_draining_after_capture_limit': True},
                'settings': {'startup_timeout_seconds': self.selection.startup_timeout_seconds,
                             'shutdown_grace_seconds': self.selection.shutdown_grace_seconds,
                             'poll_interval_seconds': self.selection.poll_interval_seconds,
                             'readiness_kind': self.selection.readiness.kind},
                'environment': deepcopy(self._environment), 'executable_identity': deepcopy(self._executable),
                'evidence': self._writer_receipts(), 'errors': deepcopy(self._errors),
                'qualification': {'physical_outcome': 'unverified', 'application_outcome': 'unverified',
                                  'model_fidelity': 'unverified', 'executed_binary_attestation': False,
                                  'hard_real_time_guarantee': False}}

    def close(self, reason='closed'):
        if self._close_attempted:
            if not self._cleanup:
                raise ManagedEmulatorError('managed process cleanup is unconfirmed; cleanup was not retried')
            return self.snapshot()
        self._close_attempted = True
        reason_kind = reason if type(reason) is str and reason in {
            'completed', 'cancelled', 'authority_lost', 'startup_failed', 'readiness_failed', 'process_exited', 'shutdown', 'closed'} else 'caller_requested'
        failed = self._launch_attempted and self._group is None
        with self._ownership_lock:
            for helper in self._helper_groups:
                if not self._close_helper(helper): failed = True
            if self._group is not None:
                try:
                    self._group.close()
                    if not self._group.closed: raise RuntimeError('group did not confirm cleanup')
                    self._exit_code = self._group.process.poll()
                except Exception as error:
                    failed = True; self._error('main_cleanup', error)
        if self._drain is not None:
            try: self._drain.stop()
            except Exception as error:
                failed = True; self._error('log_drain_close', error)
        failed = failed or any(not helper['cleanup_confirmed'] for helper in self._helpers)
        self._closed_at = self._dated()
        self._state = 'cleanup_unconfirmed' if failed else 'closed'
        self._cleanup = not failed
        try:
            self._record('lifecycle', 'closed', reason=reason_kind, cleanup_confirmed=self._cleanup, exit_code=self._exit_code)
        except Exception as error:
            failed = True; self._error('lifecycle_write', error)
        with self._writer_lock:
            for writer in self._writers.values():
                try: writer.close()
                except Exception as error:
                    failed = True; self._error('evidence_close', error)
        self._cleanup = not failed
        self._state = 'cleanup_unconfirmed' if failed else 'closed'
        self._env.clear()
        if self._owns_evidence:
            try:
                self.paths.manifest.write_text(json.dumps(self.snapshot(), sort_keys=True, allow_nan=False) + '\n')
                self.paths.manifest.chmod(0o600)
            except Exception as error:
                self._cleanup = False; self._state = 'cleanup_unconfirmed'; self._error('manifest_write', error)
        if not self._cleanup:
            raise ManagedEmulatorError('managed process cleanup is unconfirmed')
        return self.snapshot()

    def retain_artifacts(self, destination, *, visibility='evaluation'):
        if not self._close_attempted or not self._cleanup:
            raise ManagedEmulatorError('managed process cleanup must be confirmed before freezing evidence')
        if visibility not in {'model', 'evaluation', 'truth'}:
            raise ValueError('invalid artifact visibility')
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        sources = [(self.paths.lifecycle, 'managed_emulator_parent', 'application/x-ndjson', visibility),
                   (self.paths.readiness, 'managed_emulator_parent', 'application/x-ndjson', visibility),
                   (self.paths.manifest, 'managed_emulator_parent', 'application/json', visibility)]
        if self.selection.export_raw_logs:
            for name in ('stdout', 'stderr'):
                exported = self.paths.exported_log(name)
                if not exported.exists():
                    shutil.copyfile(self.paths.private_log(name), exported)
                    exported.chmod(0o600)
                if _file_sha(exported) != self.snapshot()['logs'][name]['captured_sha256']:
                    raise ManagedEmulatorError('managed raw log changed before freezing')
                sources.append((exported, 'managed_emulator_raw_log', 'application/octet-stream', self.selection.raw_log_visibility))
        artifacts = []
        for source, role, media_type, selected_visibility in sources:
            if source.is_symlink() or not source.is_file():
                raise ManagedEmulatorError('managed evidence is not a regular file')
            checksum = _file_sha(source)
            target = destination / ('managed-emulator-' + source.name)
            if source.resolve() == target.resolve():
                pass
            elif target.exists():
                if _file_sha(target) != checksum:
                    raise ManagedEmulatorError('managed artifact name collides with different bytes')
            else:
                shutil.copyfile(source, target)
            if _file_sha(target) != checksum or _file_sha(source) != checksum:
                raise ManagedEmulatorError('managed evidence changed while freezing')
            target.chmod(0o444)
            artifacts.append({'name': target.name, 'path': str(target), 'sha256': checksum, 'size': target.stat().st_size,
                              'role': role, 'media_type': media_type, 'visibility': selected_visibility})
        return artifacts
