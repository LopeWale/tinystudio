"""Import an exported spool through normal, project-authorized research HTTP.

The private journal is a durable attach intent. Once prepared, it is replayed
verbatim before consulting any source bytes. Producer metadata remains a claim;
neither this client nor a recovery receipt establishes a scientific or physical
outcome, runtime attestation, or renewed execution authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from urllib.parse import urlsplit, urlunsplit
import zipfile
import zlib

from .client_paths import RecoveredEvidencePaths, path_identifier
from .contracts import checksum
from .evidence_spool import _validate_manifest


_CHUNK = 1024 * 1024
_FORMAT = 'tinystudio.evidence-recovery-request.v1'
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_QUALIFICATION = {'scope': 'bytes_and_existing_server_identity_only', 'operator_supplied': True,
    'scientific_truth': False, 'physical_completion': False, 'runtime_attestation_verified': False,
    'cleanup_and_outcome': 'producer_claims_only', 'execution_authority_granted': False}


@dataclass(frozen=True)
class RecoveryImportLimits:
    """Optional operational byte budgets, without a file or sample count cap."""
    manifest_bytes: int | None = None
    archive_bytes: int | None = None
    evidence_bytes: int | None = None

    def __post_init__(self):
        for name in ('manifest_bytes', 'archive_bytes', 'evidence_bytes'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(name + ' must be a nonnegative host byte budget or None')


@dataclass(frozen=True)
class RecoveryImportPaths:
    archive: Path

    @property
    def request(self):
        return Path(str(Path(self.archive).expanduser().absolute()) + '.recovery-request.json')


def _json(content):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON field')
            value[key] = item
        return value
    def nonfinite(value):
        raise ValueError('nonfinite JSON number')
    return json.loads(content, object_pairs_hook=unique, parse_constant=nonfinite)


def _encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                       allow_nan=False) + '\n').encode()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _name(name):
    _require(isinstance(name, str) and bool(name)
        and not PurePosixPath(name).is_absolute() and '\\' not in name
        and not any(part in {'', '.', '..'} for part in name.split('/'))
        and not any(ord(char) < 32 or ord(char) == 127 for char in name),
        'unsafe evidence archive member path')


def _server(value):
    _require(isinstance(value, str) and not any(ord(c) <= 32 or c == '\\' for c in value),
             'research server URL must be credential-free')
    parsed = urlsplit(value)
    port = parsed.port
    _require(parsed.scheme in {'http', 'https'} and parsed.hostname and parsed.username is None
        and parsed.password is None and not parsed.query and not parsed.fragment,
        'research server URL requires HTTP(S), without credentials, query or fragment')
    host = '[' + parsed.hostname + ']' if ':' in parsed.hostname else parsed.hostname
    if port is not None and (parsed.scheme, port) not in {('http', 80), ('https', 443)}:
        host += ':' + str(port)
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip('/'), '', ''))


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_nlink)


def _private(info):
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == 0o600,
             'recovery journal and lock must be owned regular files with mode 0600 and no hard links')


@contextmanager
def _locked(path):
    lock = Path(str(path) + '.lock')
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError as exc:
        raise ValueError('cannot reserve a private recovery journal lock') from exc
    try:
        _private(os.fstat(fd))
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write_journal(path, value):
    document = deepcopy(value)
    document['checksum'] = checksum(value)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(_encoded(document))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_journal(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError('recovery journal cannot be a symlink or inaccessible file') from exc
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _private(before)
        document = _json(stream.read())
        _require(_identity(before) == _identity(os.fstat(stream.fileno())), 'recovery journal changed during reading')
    _require(isinstance(document, dict) and set(document) ==
             {'format', 'scope', 'stage', 'run_id', 'request', 'receipt', 'checksum'},
             'invalid recovery journal shape')
    recorded = document.pop('checksum')
    _require(_sha(recorded) and recorded == checksum(document), 'recovery journal checksum mismatch')
    _require(document['format'] == _FORMAT and isinstance(document['stage'], str)
             and document['stage'] in {'reserved', 'prepared', 'completed'},
             'invalid recovery journal format or stage')
    return document


def _sync_journal(path):
    """A prior failed directory fsync must not become permission to attach."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        _private(os.fstat(descriptor))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _ref(value, *, message='invalid recovered artifact identity'):
    _require(isinstance(value, dict) and isinstance(value.get('artifact_id'), str)
             and 0 < len(value['artifact_id']) <= 64 and _sha(value.get('sha256'))
             and type(value.get('size')) is int and value['size'] >= 0, message)
    return {name: value[name] for name in ('artifact_id', 'sha256', 'size')}


def _artifact(value, expected=None, *, message):
    _require(isinstance(value, dict), message)
    ref = _ref({'artifact_id': value.get('id'), 'sha256': value.get('sha256'), 'size': value.get('size')}, message=message)
    _require(all(isinstance(value.get(name), str) and value[name]
                 for name in ('name', 'media_type', 'role', 'visibility')), message)
    if expected is not None:
        _require(ref == expected, message)
    return ref


def _prepared_request(value, key):
    _require(isinstance(value, dict) and set(value) == {'source_attempt_id', 'target_id', 'spec_digest',
             'manifest', 'files', 'preview_digest', 'idempotency_key'}, 'invalid prepared recovery request')
    for name in ('source_attempt_id', 'target_id'):
        path_identifier(value[name])
    _require(_sha(value['spec_digest']) and _sha(value['preview_digest']) and value['idempotency_key'] == key,
             'invalid prepared recovery request identity')
    _require(isinstance(value['manifest'], dict) and set(value['manifest']) == {'artifact_id', 'sha256', 'size'},
             'invalid prepared manifest ref')
    _ref(value['manifest'])
    _require(isinstance(value['files'], list), 'invalid prepared recovery files')
    names = []
    for item in value['files']:
        _require(isinstance(item, dict) and set(item) == {'name', 'artifact_id', 'sha256', 'size'},
                 'invalid prepared recovery file ref')
        _name(item['name'])
        _require(item['name'].startswith('evidence/'), 'prepared recovery file escapes evidence')
        _ref(item)
        names.append(item['name'])
    _require(names == sorted(set(names)), 'prepared recovery file order or inventory mismatch')


class _UploadStream:
    """Non-seekable upload reader: multipart encoders cannot read a file at once."""
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size=-1):
        block = self.stream.read(_CHUNK if size is None or size < 0 else min(size, _CHUNK))
        self.digest.update(block)
        self.size += len(block)
        return block


class RecoveryArchiveClient:
    def __init__(self, http, project_base, *, server=None, limits=None):
        _require(isinstance(project_base, str) and re.fullmatch(r'/api/p/[A-Za-z0-9_-]{1,80}/lab', project_base),
                 'recovery requires a project-scoped laboratory API base')
        selected = getattr(http, 'base_url', None)
        selected = _server(str(selected)) if selected is not None else None
        explicit = _server(server) if server is not None else None
        _require(selected is not None or explicit is not None, 'recovery requires the selected research server URL')
        _require(selected is None or explicit is None or selected == explicit,
                 'explicit research server differs from the actual HTTP destination scope')
        self.http = http
        self.server = selected if selected is not None else explicit
        self.paths = RecoveredEvidencePaths(project_base)
        self.limits = RecoveryImportLimits() if limits is None else limits
        _require(isinstance(self.limits, RecoveryImportLimits), 'invalid recovery import limits')

    def import_archive(self, archive, *, idempotency_key, request_path=None, preview_only=False):
        """Prepare once, then attach/replay the saved request with the same key.

        Exceptions never clear an uncertain request. A prepared journal permits
        retries after archive loss or changed source/server verification policy.
        preview_only reserves uploads and the attach intent without publishing.
        """
        _require(isinstance(idempotency_key, str) and bool(idempotency_key.strip())
                 and len(idempotency_key) <= 120, 'recovery idempotency key must contain 1 to 120 characters')
        _require(type(preview_only) is bool, 'preview_only must be boolean')
        selected = getattr(self.http, 'base_url', None)
        _require(selected is None or _server(str(selected)) == self.server,
                 'research HTTP destination scope changed after client construction')
        archive = Path(archive).expanduser().absolute()
        path = Path(request_path).expanduser().absolute() if request_path is not None else RecoveryImportPaths(archive).request
        _require(path != archive, 'recovery journal cannot overwrite its archive')
        scope = {'server': self.server, 'project_base': self.paths.base, 'idempotency_key': idempotency_key}
        with _locked(path):
            journal = _read_journal(path)
            if journal is not None:
                _require(journal['scope'] == scope, 'recovery journal scope differs from server, project or idempotency key')
                if journal['stage'] != 'reserved':
                    path_identifier(journal['run_id'])
                    _prepared_request(journal['request'], idempotency_key)
                    if journal['stage'] == 'completed':
                        self._receipt(journal['receipt'], journal['request'])
                        return deepcopy(journal['receipt'])
                    _require(journal['receipt'] is None, 'pending recovery journal cannot contain a receipt')
                    return self._finish(path, journal, preview_only)
                _require(all(journal[name] is None for name in ('run_id', 'request', 'receipt')),
                         'reserved recovery journal contains an unexpected prepared identity')
            else:
                journal = {'format': _FORMAT, 'scope': scope, 'stage': 'reserved',
                           'run_id': None, 'request': None, 'receipt': None}
                _write_journal(path, journal)
            run_id, request = self._prepare(archive)
            request['idempotency_key'] = idempotency_key
            _prepared_request(request, idempotency_key)
            journal.update(stage='prepared', run_id=run_id, request=request)
            # Publication is forbidden until both the exact body and key are durable.
            _write_journal(path, journal)
            return self._finish(path, journal, preview_only)

    def _finish(self, path, journal, preview_only):
        if preview_only:
            return {'preview_only': True, 'request_path': str(path), 'run_id': journal['run_id'],
                    'request': deepcopy(journal['request'])}
        _sync_journal(path)
        response = self.http.post(self.paths.attach(journal['run_id']), json=deepcopy(journal['request']))
        response.raise_for_status()
        receipt = response.json()
        self._receipt(receipt, journal['request'])
        completed = {**journal, 'stage': 'completed', 'receipt': receipt}
        _write_journal(path, completed)
        return receipt

    def _receipt(self, receipt, request):
        message = 'recovery receipt does not match the saved attach request'
        _require(isinstance(receipt, dict), message)
        expected = {'classification': 'operator_recovered_evidence', 'idempotency_key': request['idempotency_key'],
            'request_digest': checksum(request), 'source_attempt_id': request['source_attempt_id'],
            'manifest_artifact_id': request['manifest']['artifact_id'], 'manifest_sha256': request['manifest']['sha256']}
        _require(all(type(receipt.get(name)) is str and receipt[name] == value for name, value in expected.items()), message)
        _require(type(receipt.get('replayed')) is bool and isinstance(receipt.get('author_id'), str)
                 and bool(receipt['author_id']), message)
        states = receipt.get('source_states')
        _require(isinstance(states, dict) and set(states) == {'run', 'attempt'}
                 and all(isinstance(value, str) and value for value in states.values()), message)
        _artifact(receipt.get('receipt'), message=message)
        _require(receipt['receipt']['role'] == 'operator_recovered_evidence'
                 and receipt['receipt']['visibility'] == 'evaluation'
                 and receipt['receipt']['media_type'] == 'application/json', message)

    def _prepare(self, path):
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            raise ValueError('cannot read a regular evidence archive without symlinks') from exc
        try:
            with os.fdopen(descriptor, 'rb') as source:
                identity = os.fstat(source.fileno())
                _require(stat.S_ISREG(identity.st_mode) and identity.st_nlink == 1,
                         'evidence archive must be a regular file without hard links')
                self._budget(identity.st_size, self.limits.archive_bytes, 'archive')
                def unchanged():
                    _require(_identity(identity) == _identity(os.fstat(source.fileno()))
                             and _identity(identity) == _identity(path.lstat()), 'evidence archive changed during import')
                with zipfile.ZipFile(source) as archive:
                    members = archive.infolist()
                    names = []
                    for member in members:
                        _name(member.filename)
                        mode = member.external_attr >> 16
                        _require(not member.is_dir() and stat.S_IFMT(mode) in {0, stat.S_IFREG}
                                 and not member.flag_bits & 1, 'evidence archive contains a link, directory, special or encrypted member')
                        names.append(member.filename)
                    _require(len(names) == len(set(names)) and 'manifest.json' in names,
                             'evidence archive contains duplicate members or lacks manifest.json')
                    manifest_info = archive.getinfo('manifest.json')
                    self._budget(manifest_info.file_size, self.limits.manifest_bytes, 'manifest')
                    with archive.open(manifest_info) as stream:
                        content = stream.read() if self.limits.manifest_bytes is None else stream.read(self.limits.manifest_bytes + 1)
                    self._budget(len(content), self.limits.manifest_bytes, 'manifest')
                    manifest = _json(content)
                    _validate_manifest(manifest)
                    origin = manifest['origin']
                    for name in ('run_id', 'attempt_id', 'target_id'):
                        path_identifier(origin[name])
                    expected_names = {'manifest.json'} | {item['path'] for item in manifest['files']}
                    _require(set(names) == expected_names, 'evidence archive does not exactly match its public manifest inventory')
                    total = sum(item['size'] for item in manifest['files'])
                    self._budget(total, self.limits.evidence_bytes, 'evidence')
                    for item in manifest['files']:
                        _require(archive.getinfo(item['path']).file_size == item['size'], 'evidence archive size differs from manifest')
                        self._verify_file(archive, item)
                    unchanged()
                    manifest_ref = self._upload(archive, 'manifest.json', hashlib.sha256(content).hexdigest(), len(content), unchanged)
                    files = []
                    for item in manifest['files']:
                        ref = self._upload(archive, item['path'], item['sha256'], item['size'], unchanged)
                        files.append({'name': item['path'], **ref})
                    request = {'source_attempt_id': origin['attempt_id'], 'target_id': origin['target_id'],
                               'spec_digest': origin['spec_digest'], 'manifest': manifest_ref, 'files': files}
                    response = self.http.post(self.paths.preview(origin['run_id']), json=deepcopy(request))
                    response.raise_for_status()
                    preview = response.json()
                    self._preview(preview, request, manifest)
                    unchanged()
                    return origin['run_id'], {**request, 'preview_digest': preview['digest']}
        except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError) as exc:
            raise ValueError('invalid evidence archive bytes or CRC') from exc

    @staticmethod
    def _budget(observed, maximum, kind):
        _require(maximum is None or observed <= maximum, kind + ' exceeds the configured host byte budget')

    @staticmethod
    def _verify_file(archive, item):
        digest = hashlib.sha256()
        size = newlines = tail = 0
        ends = False
        with archive.open(item['path']) as stream:
            while block := stream.read(_CHUNK):
                digest.update(block)
                size += len(block)
                _require(size <= item['size'], 'evidence archive exceeds its declared file size')
                newlines += block.count(b'\n')
                tail = len(block.rsplit(b'\n', 1)[-1]) if b'\n' in block else tail + len(block)
                ends = block.endswith(b'\n')
        _require((digest.hexdigest(), size) == (item['sha256'], item['size']), 'evidence archive hash or size differs from manifest')
        if 'jsonl' in item:
            _require(item['jsonl'] == {'newline_count': newlines, 'tail_bytes': tail, 'ends_with_newline': ends,
                'semantic_validation': 'not_performed'}, 'evidence JSONL byte facts differ from manifest')

    def _upload(self, archive, name, sha256, size, unchanged):
        with archive.open(name) as stream:
            upload = _UploadStream(stream)
            media = 'application/json' if name == 'manifest.json' else 'application/octet-stream'
            filename = PurePosixPath(name).name
            # Artifact metadata has a basename contract; logical ZIP inventory
            # names retain their full POSIX identity independently of that label.
            if len(filename) > 255 or ':' in filename:
                filename = 'evidence-' + hashlib.sha256(name.encode()).hexdigest()
            response = self.http.post(self.paths.base + '/inputs',
                files={'file': (filename, upload, media)})
            response.raise_for_status()
            _require((upload.digest.hexdigest(), upload.size) == (sha256, size), 'evidence bytes changed or upload was incomplete')
        unchanged()
        ref = _artifact(response.json(), message='uploaded evidence artifact response has an invalid identity')
        _require(ref['sha256'] == sha256 and ref['size'] == size, 'uploaded evidence artifact hash or size differs from source')
        return ref

    def _preview(self, preview, request, manifest):
        message = 'recovery preview does not match the selected public evidence'
        _require(isinstance(preview, dict) and _sha(preview.get('digest')), message)
        source = {'run_id': manifest['origin']['run_id'],
            'attempt_id': request['source_attempt_id'], 'target_id': request['target_id'], 'spec_digest': request['spec_digest']}
        observed_source = preview.get('source')
        # The authorized URL may contain a slug while this field is the DB ID.
        _require(isinstance(observed_source, dict) and set(observed_source) == set(source) | {'project_id'}
                 and isinstance(observed_source['project_id'], str) and bool(observed_source['project_id'])
                 and all(observed_source[name] == value for name, value in source.items()), message)
        _require(preview.get('format') == 'tinystudio.operator-recovered-evidence.v1'
                 and preview.get('classification') == 'operator_recovered_evidence'
                 and checksum(preview.get('producer_manifest')) == checksum(manifest)
                 and checksum(preview.get('qualification')) == checksum(_QUALIFICATION), message)
        _artifact(preview.get('manifest'), request['manifest'], message=message)
        files = preview.get('files')
        _require(isinstance(files, list) and len(files) == len(request['files']), message)
        for observed, expected in zip(files, request['files'], strict=True):
            _require(isinstance(observed, dict) and observed.get('name') == expected['name'], message)
            _artifact(observed.get('artifact'), _ref(expected), message=message)
            facts = observed.get('verified_bytes')
            _require(isinstance(facts, dict) and type(facts.get('size')) is int
                     and facts['size'] == expected['size'] and facts.get('sha256') == expected['sha256'], message)
        calculated = checksum({key: value for key, value in preview.items()
                              if key not in {'digest', 'source_states', 'source_states_observed_at'}})
        _require(preview['digest'] == calculated, message)
