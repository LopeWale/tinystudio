"""Artifact-bound supervisors submit through parent-owned hardware session IPC.

Local evidence describes supervisor execution and IPC responses. The parent owns
lease authority and independently retains admission/delivery evidence; neither
record establishes physical outcomes or a hard real-time control guarantee.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from importlib.machinery import SourceFileLoader
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
from types import MappingProxyType
import uuid

from .engine import ExecutionCancelled, digest
from .evidence_io import JsonlEvidenceWriter, RecordWritePolicy
from .execution_outcome import RetainedExecutionError
from .session_protocol import SESSION_FORMAT as IPC_FORMAT
from .hardware_session_configuration import SessionBridgePaths


FORMAT = 'tinystudio.hardware-session.v1'
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def _json(value):
    if value is None or type(value) in {str, int, bool}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('session values must contain finite JSON numbers')
    elif type(value) is list:
        for child in value:
            _json(child)
    elif type(value) is dict:
        if not all(type(key) is str for key in value):
            raise ValueError('session JSON object keys must be strings')
        for child in value.values():
            _json(child)
    else:
        raise ValueError('session values must be exact JSON values')


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    return value


def _plain(value):
    if isinstance(value, (dict, MappingProxyType)):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(child) for child in value]
    return value


def _positive(value, label, *, optional=False):
    if optional and value is None:
        return
    try:
        valid = type(value) in {int, float} and value > 0 and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(label + ' must be positive and finite' + (' or null' if optional else ''))


@dataclass(frozen=True)
class HardwareSessionSelection:
    binding_id: str
    supervisor: object
    supervisor_parameters: object
    evidence: RecordWritePolicy
    ipc_timeout_seconds: float | None
    artifact_visibility: str = 'evaluation'

    def __post_init__(self):
        for name in ('supervisor', 'supervisor_parameters'):
            object.__setattr__(self, name, _freeze(getattr(self, name)))

    def to_dict(self):
        return {'binding_id': self.binding_id, 'supervisor': _plain(self.supervisor),
                'supervisor_parameters': _plain(self.supervisor_parameters),
                'evidence': self.evidence.to_dict(), 'ipc_timeout_seconds': self.ipc_timeout_seconds,
                'artifact_visibility': self.artifact_visibility}


def validate_selection(definition):
    """Validate authored choices without opening files, IPC or devices."""
    if not isinstance(definition, dict) or definition.get('engine') != 'hardware.session':
        raise ValueError('hardware session requires engine hardware.session')
    _json(definition)
    execution = definition.get('execution', {})
    if not isinstance(execution, dict) or execution.get('backend') != 'native' or execution.get('profile') != 'trusted_native':
        raise ValueError('hardware session requires native backend and trusted_native profile')
    if definition.get('operating_mode') not in {'physical_control', 'shadow', 'acquisition', 'simulation'}:
        raise ValueError('unsupported hardware session operating mode')
    parameters = definition.get('parameters', {})
    if not isinstance(parameters, dict) or parameters.keys() - {
            'binding_id', 'supervisor', 'supervisor_parameters', 'evidence', 'ipc_timeout_seconds', 'artifact_visibility'}:
        raise ValueError('hardware session parameters contain unsupported fields')
    binding_id = parameters.get('binding_id')
    if type(binding_id) is not str or not binding_id:
        raise ValueError('hardware session requires an explicit binding_id')
    supervisor = parameters.get('supervisor')
    if type(supervisor) is not dict or supervisor.get('kind') not in {'recorded', 'python'}:
        raise ValueError('supervisor must select recorded or python')
    fields = {'kind', 'artifact_id'} | ({'callable'} if supervisor['kind'] == 'python' else set())
    if supervisor.keys() != fields or type(supervisor['artifact_id']) is not str or not supervisor['artifact_id']:
        raise ValueError('supervisor has missing or unsupported fields')
    if supervisor['kind'] == 'python':
        name = supervisor['callable']
        if type(name) is not str or not name or not all(part.isidentifier() for part in name.split('.')):
            raise ValueError('supervisor callable must be a qualified Python name')
    refs = definition.get('inputs', [])
    if not isinstance(refs, list) or not all(type(ref) is dict for ref in refs):
        raise ValueError('hardware session inputs must be frozen artifact references')
    matches = [ref for ref in refs if ref.get('artifact_id') == supervisor['artifact_id']]
    if len(matches) != 1 or type(matches[0].get('sha256')) is not str or not _SHA256.fullmatch(matches[0]['sha256']):
        raise ValueError('supervisor must reference one frozen input with its SHA-256')
    settings = parameters.get('supervisor_parameters', {})
    if type(settings) is not dict:
        raise ValueError('supervisor_parameters must be a finite JSON object')
    _json(settings)
    _positive(parameters.get('ipc_timeout_seconds'), 'ipc_timeout_seconds', optional=True)
    visibility = parameters.get('artifact_visibility', 'evaluation')
    if type(visibility) is not str or visibility not in {'model', 'evaluation', 'truth'}:
        raise ValueError('artifact_visibility must select model, evaluation or truth')
    return HardwareSessionSelection(binding_id, deepcopy(supervisor), deepcopy(settings),
        RecordWritePolicy.from_parameters(parameters), parameters.get('ipc_timeout_seconds'), visibility)


@dataclass(frozen=True)
class HardwareSessionPaths:
    root: Path

    @property
    def source(self): return self.root / 'supervisor-source'
    @property
    def report(self): return self.root / 'supervisor-report.json'
    @property
    def submissions(self): return self.root / 'session-submissions.jsonl'
    @property
    def receipts(self): return self.root / 'session-receipts.jsonl'
    @property
    def manifest(self): return self.root / 'hardware-session.json'


def _session_client(descriptor, timeout):
    from .hardware_session_client import HardwareSessionClient
    return HardwareSessionClient(descriptor, timeout=timeout)


def _cancel(cancelled):
    if cancelled():
        raise ExecutionCancelled('hardware session supervisor cancelled')


def _error(cause, capability):
    return {'type': type(cause).__name__, 'message': str(cause).replace(capability, '[REDACTED]')}


def _load_json(line):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate supervisor JSON field')
            result[key] = value
        return result
    value = json.loads(line, object_pairs_hook=unique)
    _json(value)
    return value


class _RecordedClient:
    """Public client surface with local pre-admission and response evidence."""
    def __init__(self, client, profile, submissions, receipts, cancelled, capability):
        self._client, self._profile = client, deepcopy(profile)
        self._submissions, self._receipts = submissions, receipts
        self._cancelled, self._capability = cancelled, capability

    @property
    def profile(self): return deepcopy(self._profile)

    def close(self): self._client.close()

    def submit(self, decision_id, payload, *, valid_for_seconds):
        _cancel(self._cancelled)
        if type(decision_id) is not str or not decision_id or type(payload) is not dict:
            raise ValueError('decision requires a nonempty decision_id and JSON object payload')
        _json(payload)
        _positive(valid_for_seconds, 'valid_for_seconds')
        decision = {'decision_id': decision_id, 'payload': deepcopy(payload), 'valid_for_seconds': valid_for_seconds}
        started = time.monotonic()
        self._submissions.write_record({**decision, 'started_monotonic': started, 'started_wall_time': time.time(),
            'timing_scope': 'local_submission_before_parent_admission'})
        try:
            receipt = self._client.submit(decision_id, deepcopy(payload), valid_for_seconds=valid_for_seconds)
            _json(receipt)
        except Exception as cause:
            row = {'decision_id': decision_id, 'status': 'error', 'error': _error(cause, self._capability),
                   'finished_monotonic': time.monotonic(), 'timing_scope': 'local_ipc_response_not_physical_completion'}
            for field in ('delivery_receipt', 'lifecycle_receipt'):
                if getattr(cause, field, None) is not None:
                    row[field] = deepcopy(getattr(cause, field))
            try:
                self._receipts.write_record(row)
            except Exception as secondary:
                cause.add_note('Local error receipt retention also failed: ' + type(secondary).__name__)
            raise
        finished = time.monotonic()
        self._receipts.write_record({'decision_id': decision_id, 'status': 'returned', 'receipt': receipt,
            'started_monotonic': started, 'finished_monotonic': finished, 'elapsed_seconds': finished-started,
            'timing_scope': 'local_ipc_response_not_physical_completion'})
        return deepcopy(receipt)


def _python_supervisor(path, sha256, name, client, context):
    identity = '_tinystudio_supervisor_' + sha256 + '_' + uuid.uuid4().hex
    loader = SourceFileLoader(identity, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(identity, loader))
    sys.modules[identity] = module
    try:
        # Compile the verified source directly, bypassing timestamp-based .pyc
        # reuse while retaining SourceFileLoader support for extensionless files.
        code = loader.source_to_code(loader.get_data(str(path)), str(path))
        exec(code, module.__dict__)
        target = module
        for part in name.split('.'):
            target = getattr(target, part)
        if not callable(target):
            raise ValueError('selected supervisor attribute is not callable')
        return target(client, context)
    finally:
        if sys.modules.get(identity) is module:
            del sys.modules[identity]


def _artifact(path, role, media_type='application/json', *, visibility='evaluation'):
    return {'name': path.name, 'path': str(path), 'sha256': digest(path),
            'media_type': media_type, 'role': role, 'visibility': visibility}


def _retained_source_integrity(path, expected):
    """Observe retained bytes without following links or blocking on a FIFO.

    These checks identify the retained file, not isolation from trusted native
    code that continues changing files after the engine returns.
    """
    def state(status, observed=None, **fields):
        return {'status': status, 'observed_sha256': observed, **fields}

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_mtime_ns, info.st_ctime_ns)

    try:
        initial = path.lstat()
        if stat.S_ISLNK(initial.st_mode):
            return state('symlink')
        if not stat.S_ISREG(initial.st_mode):
            return state('not_regular')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                return state('not_regular')
            value = hashlib.sha256()
            while block := os.read(descriptor, 1024 * 1024):
                value.update(block)
            after = os.fstat(descriptor)
            current = path.lstat()
        finally:
            os.close(descriptor)
        if stat.S_ISLNK(current.st_mode):
            return state('symlink')
        if not stat.S_ISREG(current.st_mode):
            return state('not_regular')
        observed = value.hexdigest()
        if any(identity(info) != identity(initial) for info in (before, after, current)):
            return state('changed_while_reading', observed)
        return state('matched' if observed == expected else 'sha256_mismatch', observed)
    except FileNotFoundError:
        return state('missing')
    except OSError as cause:
        return state('unreadable', error_type=type(cause).__name__)


def _remove_unpublishable_source(path):
    """Remove only the replaced source entry so the remaining audit can export."""
    entry_type = 'unknown'
    try:
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            return {'status': 'not_removed', 'entry_type': 'regular'}
        entry_type = ('symlink' if stat.S_ISLNK(info.st_mode) else
                      'directory' if stat.S_ISDIR(info.st_mode) else
                      'fifo' if stat.S_ISFIFO(info.st_mode) else
                      'socket' if stat.S_ISSOCK(info.st_mode) else 'special')
        if entry_type == 'directory':
            shutil.rmtree(path)
        else:
            path.unlink()
        return {'status': 'removed', 'entry_type': entry_type}
    except FileNotFoundError:
        return {'status': 'already_missing', 'entry_type': entry_type}
    except Exception as cause:
        return {'status': 'failed', 'entry_type': entry_type, 'error_type': type(cause).__name__}


def execute_hardware_session(spec, output_dir, emit, cancelled):
    """Run one authored supervisor; authority remains with the parent broker."""
    selection = validate_selection(spec.get('definition'))
    descriptor = spec.get('_hardware_session')
    if (type(descriptor) is not dict or set(descriptor) != {'format', 'path', 'capability', 'session_id', 'profile'}
            or descriptor.get('format') != IPC_FORMAT or type(descriptor.get('profile')) is not dict
            or any(type(descriptor.get(key)) is not str or not descriptor[key] for key in ('path', 'capability', 'session_id'))
            or not Path(descriptor['path']).is_absolute()):
        raise ValueError('hardware session requires the parent IPC descriptor')
    _json(descriptor['profile'])
    _cancel(cancelled)
    paths = HardwareSessionPaths(Path(output_dir))
    paths.root.mkdir(parents=True, exist_ok=True)
    parent=SessionBridgePaths.parent_in_output(paths.root)
    if any(path!=parent or path.is_symlink() or not path.is_dir() for path in paths.root.iterdir()):
        raise ValueError('hardware session requires an empty owned output directory')
    definition = deepcopy(spec['definition'])
    profile = deepcopy(descriptor['profile'])
    source_ref = next(ref for ref in definition['inputs'] if ref['artifact_id'] == selection.supervisor['artifact_id'])
    failure = None
    client = None
    submissions = receipts = None
    report = None
    source_verified = False
    cleanup_errors = []
    try:
        input_paths = {}
        for index, ref in enumerate(definition['inputs']):
            _cancel(cancelled)
            raw_paths = spec.get('input_paths', {})
            raw = raw_paths.get(ref['artifact_id']) if isinstance(raw_paths, dict) else raw_paths[index]
            path = Path(raw['path'] if isinstance(raw, dict) else raw)
            if not path.is_file() or path.is_symlink() or digest(path) != ref.get('sha256'):
                raise ValueError('hardware session input SHA-256 or regular-file identity mismatch')
            input_paths[ref['artifact_id']] = str(path)
        shutil.copyfile(input_paths[source_ref['artifact_id']], paths.source)
        if digest(paths.source) != source_ref['sha256']:
            raise ValueError('supervisor source changed while copying')
        source_verified = True
        input_paths[source_ref['artifact_id']] = str(paths.source)
        context = {'definition': deepcopy(definition), 'seed': definition.get('seed'),
                   'supervisor_parameters': _plain(selection.supervisor_parameters),
                   'inputs': deepcopy(definition['inputs']), 'input_paths': input_paths,
                   'output_dir': str(paths.root), 'profile': deepcopy(profile)}
        submissions = JsonlEvidenceWriter(paths.submissions, selection.evidence)
        receipts = JsonlEvidenceWriter(paths.receipts, selection.evidence)
        with submissions, receipts:
            client = _session_client(deepcopy(descriptor), selection.ipc_timeout_seconds)
            public = _RecordedClient(client, profile, submissions, receipts, cancelled, descriptor['capability'])
            emit({'event': 'hardware_session_supervisor', 'status': 'started', 'binding_id': selection.binding_id})
            if selection.supervisor['kind'] == 'recorded':
                count = 0
                with paths.source.open(encoding='utf-8') as stream:
                    for position, line in enumerate(stream, 1):
                        _cancel(cancelled)
                        row = _load_json(line)
                        if type(row) is not dict or row.keys() != {'decision_id', 'payload', 'valid_for_seconds'}:
                            raise ValueError('recorded supervisor row must contain exactly decision_id, payload and valid_for_seconds at line ' + str(position))
                        public.submit(**row)
                        count += 1
                report = {'kind': 'recorded', 'completed_decisions': count}
            else:
                report = _python_supervisor(paths.source, source_ref['sha256'], selection.supervisor['callable'], public, context)
            if report is not None and type(report) is not dict:
                raise ValueError('supervisor must return None or a finite JSON object')
            _json(report)
            paths.report.write_text(json.dumps(report, allow_nan=False) + '\n', encoding='utf-8')
            _cancel(cancelled)
    except BaseException as cause:
        # Authored Python may call sys.exit after completed submissions. Retain
        # that explicit failure just like other supervisor exceptions.
        failure = cause
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as cause:
                cleanup_errors.append(_error(cause, descriptor['capability']))
                if failure is None:
                    failure = cause
                else:
                    failure.add_note('Session client cleanup also failed: ' + type(cause).__name__)
    if failure is None:
        try:
            _cancel(cancelled)
            emit({'event': 'hardware_session_supervisor', 'status': 'completed', 'binding_id': selection.binding_id})
        except Exception as cause:
            failure = cause
    source_initially_verified = source_verified
    source_integrity = None
    source_integrity_failed = False

    def check_retained_source():
        nonlocal source_verified, source_integrity, source_integrity_failed, failure
        source_integrity = _retained_source_integrity(paths.source, source_ref['sha256'])
        source_verified = source_verified and source_integrity['status'] == 'matched'
        if source_initially_verified and source_integrity['status'] != 'matched' and not source_integrity_failed:
            source_integrity_failed = True
            cause = ValueError('retained supervisor source integrity failed: ' + source_integrity['status'])
            if failure is None:
                failure = cause
            else:
                failure.add_note(str(cause))

    check_retained_source()
    status = 'cancelled' if isinstance(failure, ExecutionCancelled) else 'failed' if failure else 'completed'
    writes = {name: writer.receipt() for name, writer in (('submissions', submissions), ('receipts', receipts)) if writer is not None}
    qualification = {'status': status, 'complete': failure is None, 'binding_id': selection.binding_id,
        'physical_outcome': 'unverified', 'physical_validation': False,
        'scope': 'Supervisor execution and local IPC evidence; parent admission/delivery records remain separate.',
        'evidence_writes': writes, 'cleanup_errors': cleanup_errors, 'source_integrity': source_integrity}
    try:
        retained = {'format': FORMAT, 'status': status, 'selection': selection.to_dict(), 'seed': definition.get('seed'),
            'inputs': deepcopy(definition['inputs']), 'supervisor_source': {**source_ref, 'verified': source_verified,
                                                                         'integrity': source_integrity},
            'profile': profile, 'qualification': qualification, 'evidence_writes': writes,
            'error': _error(failure, descriptor['capability']) if failure else None}
        paths.manifest.write_text(json.dumps(retained, allow_nan=False) + '\n', encoding='utf-8')
        # Match the source artifact to the last observation after report and
        # manifest serialization, rather than publishing a newly hashed source
        # under an earlier verified claim.
        previous_integrity = source_integrity
        check_retained_source()
        if source_integrity['status'] not in {'matched', 'sha256_mismatch', 'missing'}:
            cleanup = _remove_unpublishable_source(paths.source)
            source_integrity['cleanup'] = cleanup
            if cleanup['status'] == 'failed':
                note = 'Retained supervisor source cleanup failed; audit export may remain unavailable: ' + cleanup['error_type']
                if failure is None:
                    failure = ValueError(note)
                else:
                    failure.add_note(note)
        if source_integrity != previous_integrity:
            status = 'cancelled' if isinstance(failure, ExecutionCancelled) else 'failed' if failure else 'completed'
            qualification.update(status=status, complete=failure is None, source_integrity=source_integrity)
            retained.update(status=status, error=_error(failure, descriptor['capability']) if failure else None)
            retained['supervisor_source'].update(verified=source_verified, integrity=source_integrity)
            paths.manifest.write_text(json.dumps(retained, allow_nan=False) + '\n', encoding='utf-8')
        artifacts = [_artifact(path, role, media, visibility=selection.artifact_visibility if failure is None else 'evaluation')
                     for path, role, media in (
            (paths.report, 'hardware_session_report', 'application/json'),
            (paths.submissions, 'hardware_session_submissions', 'application/x-ndjson'),
            (paths.receipts, 'hardware_session_receipts', 'application/x-ndjson'),
            (paths.manifest, 'hardware_session_manifest', 'application/json')) if path.is_file()]
        if source_integrity['status'] in {'matched', 'sha256_mismatch'}:
            artifacts.insert(0, {'name': paths.source.name, 'path': str(paths.source),
                'sha256': source_integrity['observed_sha256'], 'role': 'hardware_session_supervisor',
                'media_type': 'text/plain', 'visibility': selection.artifact_visibility if failure is None else 'evaluation'})
        result = {'records': deepcopy(report['records']) if isinstance(report, dict) and isinstance(report.get('records'), list) else
                  writes.get('receipts', {}).get('written_records', 0),
                  'metrics': deepcopy(report.get('metrics', {})) if isinstance(report, dict) else {},
                  'artifacts': artifacts, 'qualification': qualification}
    except Exception as retention_error:
        if failure is None:
            raise
        failure.add_note('Hardware session evidence retention failed; no result published: ' + type(retention_error).__name__)
        raise failure
    if failure is not None:
        if isinstance(failure, ExecutionCancelled):
            failure.evidence_result = result
            raise failure
        raise RetainedExecutionError(type(failure).__name__ + ': ' + _error(failure, descriptor['capability'])['message'],
                                     evidence_result=result) from failure
    return result
