"""Credential-separated, streaming artifact data transfers.

Studio authenticates small control requests. Signed object URLs use a separate
HTTP client and explicit requests, so Studio/Access headers and cookie jars never
become object-store credentials. Provider responses do not establish a file hash.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time
from urllib.parse import urlsplit

import httpx

from .contracts import checksum
from .transfer_configuration import MultipartPlan, TransferApiPaths, TransferLocalPaths


class ArtifactTransferError(RuntimeError):
    """A transfer did not establish the expected byte identity."""


def _signed_url(value):
    try:
        parsed = urlsplit(value)
        valid = (isinstance(value, str) and parsed.hostname and parsed.username is None
                 and parsed.password is None and not parsed.fragment
                 and (parsed.scheme == 'https' or parsed.scheme == 'http'
                      and parsed.hostname in {'localhost', '127.0.0.1', '::1'})
                 and not any(ord(char) <= 32 or ord(char) == 127 or char == '\\' for char in value))
        _ = parsed.port
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ArtifactTransferError('invalid signed artifact URL')
    return value


def _integer(value, label):
    if type(value) is not int or value < 0:
        raise ArtifactTransferError(label + ' must be a nonnegative integer')
    return value


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(char not in '0123456789abcdef' for char in value):
        raise ArtifactTransferError('expected SHA256 must be a lowercase digest')
    return value


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_source(path):
    path = Path(path)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ArtifactTransferError('transfer source must be a regular file')
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        stream = os.fdopen(descriptor, 'rb')
        after = os.fstat(stream.fileno())
        if not stat.S_ISREG(after.st_mode) or _identity(before) != _identity(after):
            stream.close()
            raise ArtifactTransferError('transfer source changed while opening')
        return stream, before
    except OSError:
        raise ArtifactTransferError('could not open transfer source') from None


class _FilePart(httpx.SyncByteStream):
    def __init__(self, stream, size, check):
        self.stream, self.size, self.check = stream, size, check
        self.sent = 0

    def __iter__(self):
        while self.sent < self.size:
            self.check()
            block = self.stream.read(min(1024 ** 2, self.size - self.sent))
            self.check()
            if not block:
                raise ArtifactTransferError('transfer source ended before the selected part')
            self.sent += len(block)
            yield block


class TransferDataClient:
    def __init__(self, *, client_factory=None, timeout=None, check_authority=lambda: None):
        self._factory = client_factory or (lambda: httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False))
        self.check = check_authority

    def source_identity(self, path):
        """Hash a regular source with bounded memory and cooperative checks."""
        stream, before = _open_source(path)
        sha, size = hashlib.sha256(), 0
        with stream:
            while True:
                self.check()
                block = stream.read(1024 ** 2)
                self.check()
                if not block:
                    break
                sha.update(block)
                size += len(block)
            if _identity(os.fstat(stream.fileno())) != _identity(before) or size != before.st_size:
                raise ArtifactTransferError('transfer source changed while hashing')
        return {'sha256': sha.hexdigest(), 'size': size}

    def upload_part(self, descriptor, path, *, offset, size):
        """One application PUT, without redirect/retry or Studio credentials."""
        _integer(offset, 'part offset')
        _integer(size, 'part size')
        url = _signed_url(descriptor.get('url'))
        headers = descriptor.get('headers', {})
        if (not isinstance(headers, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                or key.lower() not in {'content-length', 'content-type', 'x-amz-checksum-sha256'}
                for key, value in headers.items())):
            raise ArtifactTransferError('unsupported signed upload headers')
        # Reject mismatches before contacting the provider; do not silently
        # change headers whose exact values may participate in its signature.
        lengths = [value for key, value in headers.items() if key.lower() == 'content-length']
        if len(lengths) > 1 or lengths and lengths[0] != str(size):
            raise ArtifactTransferError('signed part length differs from selected bytes')
        headers = {key: value for key, value in headers.items() if key.lower() != 'content-length'}
        headers['Content-Length'] = str(size)
        # Supplying a prebuilt stream bypasses HTTPX's automatic Host header.
        # Derive it from this capability URL, never from the Studio client.
        headers['Host'] = httpx.URL(url).netloc.decode('ascii')
        stream, before = _open_source(path)
        with stream:
            if offset + size > before.st_size:
                raise ArtifactTransferError('part lies outside transfer source')
            stream.seek(offset)
            body = _FilePart(stream, size, self.check)
            request = httpx.Request('PUT', url, headers=headers, stream=body)
            self.check()
            try:
                with self._factory() as client:
                    # A manually constructed Request bypasses client defaults
                    # and cookies, including those of an injected test client.
                    response = client.send(request, follow_redirects=False, stream=True, auth=None)
                    try:
                        if not 200 <= response.status_code < 300:
                            raise ArtifactTransferError('artifact part was not acknowledged (HTTP ' + str(response.status_code) + ')')
                        etag = response.headers.get('etag')
                    finally:
                        response.close()
            except ArtifactTransferError:
                raise
            except httpx.HTTPError:
                # Signed URLs are capabilities; exception strings contain them.
                raise ArtifactTransferError('artifact part acknowledgement is unconfirmed') from None
            self.check()
            if body.sent != size or _identity(os.fstat(stream.fileno())) != _identity(before):
                raise ArtifactTransferError('transfer source changed during part upload')
        return {'size': size, 'etag': etag, 'scope': 'provider_http_acknowledgement_only'}

    def download(self, descriptor, path, *, size, sha256):
        """Resume a private partial file, then atomically publish verified bytes.

        The caller selects a temporary path. Its existing prefix is included in
        the final full SHA256; a server ignoring Range restarts the stream.
        """
        size, sha256 = _integer(size, 'artifact size'), _digest(sha256)
        url = _signed_url(descriptor.get('url'))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = TransferLocalPaths(path).partial
        try:
            descriptor_fd = os.open(partial, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
                                    | getattr(os, 'O_NONBLOCK', 0), 0o600)
        except OSError:
            raise ArtifactTransferError('could not open artifact partial file') from None
        with os.fdopen(descriptor_fd, 'r+b') as output:
            info = os.fstat(output.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ArtifactTransferError('artifact partial file must be a single-link regular file')
            offset = info.st_size
            if offset > size:
                raise ArtifactTransferError('artifact partial file exceeds expected size')
            digest = hashlib.sha256()
            while block := output.read(1024 ** 2):
                self.check()
                digest.update(block)
            headers = {'Range': 'bytes=' + str(offset) + '-'} if offset else {}
            if offset < size:
                request = httpx.Request('GET', url, headers=headers)
                self.check()
                try:
                    with self._factory() as client:
                        response = client.send(request, follow_redirects=False, stream=True, auth=None)
                        try:
                            if response.status_code == 200:
                                offset = 0
                                output.seek(0)
                                output.truncate()
                                digest = hashlib.sha256()
                            elif response.status_code == 206 and offset:
                                expected_range = 'bytes ' + str(offset) + '-' + str(size - 1) + '/' + str(size)
                                if response.headers.get('content-range') != expected_range:
                                    raise ArtifactTransferError('artifact range response differs from requested bytes')
                            else:
                                raise ArtifactTransferError('artifact download was not acknowledged (HTTP ' + str(response.status_code) + ')')
                            for block in response.iter_raw():
                                self.check()
                                offset += len(block)
                                if offset > size:
                                    raise ArtifactTransferError('artifact download exceeds expected size')
                                output.write(block)
                                digest.update(block)
                        finally:
                            response.close()
                except ArtifactTransferError:
                    raise
                except httpx.HTTPError:
                    raise ArtifactTransferError('artifact download interrupted; private partial bytes retained') from None
            self.check()
            if offset != size or digest.hexdigest() != sha256:
                raise ArtifactTransferError('artifact download size or SHA256 mismatch')
            output.flush()
            os.fsync(output.fileno())
            # The prefix hash predates the GET. Re-read the actual file before
            # publication so a concurrent writer cannot make that hash stale.
            before_verify = os.fstat(output.fileno())
            output.seek(0)
            actual, actual_size = hashlib.sha256(), 0
            while True:
                self.check()
                block = output.read(1024 ** 2)
                if not block:
                    break
                actual.update(block)
                actual_size += len(block)
            self.check()
            if (_identity(before_verify) != _identity(os.fstat(output.fileno()))
                    or actual_size != size or actual.hexdigest() != sha256):
                raise ArtifactTransferError('artifact partial bytes changed or failed final SHA256 verification')
            if _identity(partial.lstat()) != _identity(os.fstat(output.fileno())):
                raise ArtifactTransferError('artifact partial path changed during download')
        os.replace(partial, path)
        return path


class ResumableTransferClient:
    """Compose authenticated controls with credential-separated object bodies.

    A failed call is resumable by calling upload again with the same immutable
    request. This class never automatically retries an unconfirmed mutation or
    a failed/lost/cancelled verifier. Polling a live verifier remains read-only.
    """
    def __init__(self, client, paths: TransferApiPaths, *, headers=None, data_client=None,
                 check_authority=lambda: None, poll_seconds=0.25, deadline=None):
        if not isinstance(paths, TransferApiPaths):
            raise ValueError('transfer controls require shared TransferApiPaths')
        if type(poll_seconds) not in {int, float} or not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError('transfer poll interval must be positive finite')
        if deadline is not None and (type(deadline) not in {int, float} or not math.isfinite(deadline)):
            raise ValueError('transfer deadline must be a finite monotonic time or None')
        self.client, self.paths = client, paths
        origin = urlsplit(str(getattr(client, 'base_url', '')))
        self.origin = ((origin.scheme, origin.hostname, origin.port or (443 if origin.scheme == 'https' else 80))
                       if origin.scheme in {'http', 'https'} and origin.hostname else None)
        self.headers = {} if headers is None else dict(headers)
        self.check_authority = check_authority
        self.poll_seconds, self.deadline = poll_seconds, deadline
        self.data = data_client or TransferDataClient(check_authority=self.check)

    def check(self):
        self.check_authority()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ArtifactTransferError('selected transfer deadline expired; session remains inspectable')

    def _request(self, method, path, body=None):
        self.check()
        options = {'headers': self.headers, 'follow_redirects': False}
        if body is not None:
            options['json'] = body
        try:
            response = getattr(self.client, method.lower())(path, **options)
            if not 200 <= response.status_code < 300:
                raise ArtifactTransferError('transfer control was not acknowledged (HTTP ' + str(response.status_code) + ')')
            result = response.json()
        except ArtifactTransferError:
            raise
        except (httpx.HTTPError, ValueError):
            raise ArtifactTransferError('transfer control acknowledgement is unconfirmed; inspect the same request identity') from None
        self.check()
        if not isinstance(result, dict):
            raise ArtifactTransferError('invalid transfer control response')
        return result

    def inspect(self, session_id):
        return self._request('GET', self.paths.session(session_id))

    def _correlate(self, row, session_id, intent):
        try:
            self.paths.session(session_id)
        except ValueError:
            raise ArtifactTransferError('invalid durable transfer session identity') from None
        if (not isinstance(row, dict) or row.get('id') != session_id or row.get('intent') != intent
                or type(row.get('intent', {}).get('size')) is not int):
            raise ArtifactTransferError('transfer session identity or metadata differs from immutable request')
        return row

    def abort(self, session_id):
        return self._request('POST', self.paths.abort(session_id))

    def _wait(self):
        end = time.monotonic() + self.poll_seconds
        while time.monotonic() < end:
            self.check()
            time.sleep(min(0.25, max(0, end - time.monotonic())))
        self.check()

    @staticmethod
    def _save_state(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='.transfer-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, allow_nan=False, sort_keys=True)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def upload(self, path, *, name=None, media_type='application/octet-stream', role='source', visibility='model',
               expected_sha256=None, idempotency_key=None, state_path=None):
        self.check()
        path = Path(path)
        identity = self.data.source_identity(path)
        if expected_sha256 is not None and identity['sha256'] != _digest(expected_sha256):
            raise ArtifactTransferError('local artifact differs from expected SHA256')
        intent = {**identity, 'name': name or path.name, 'media_type': media_type, 'role': role, 'visibility': visibility}
        request_digest = checksum({'collection': self.paths.collection, 'intent': intent})
        key = idempotency_key if idempotency_key is not None else request_digest
        if not isinstance(key, str) or not key:
            raise ValueError('idempotency key must be a nonempty string')
        state_identity = {'format': 'tinystudio.artifact-transfer-client.v1', 'collection': self.paths.collection,
                          'origin': list(self.origin) if self.origin else None,
                          'request_digest': request_digest, 'idempotency_key_digest': checksum(key)}
        if state_path is not None and self.origin is None:
            raise ArtifactTransferError('durable transfer state requires a known Studio HTTP origin')
        prior = None
        if state_path is not None and Path(state_path).exists():
            # Local state contains identifiers, never URLs or credentials. An
            # identity mismatch is explicit, rather than replacing old evidence.
            try:
                stream, before = _open_source(state_path)
                with stream:
                    prior = json.load(stream)
                if (not isinstance(prior, dict) or not isinstance(prior.get('session_id'), str)
                        or any(prior.get(k) != v for k, v in state_identity.items())):
                    raise ValueError('different request')
            except (OSError, ValueError, ArtifactTransferError):
                raise ArtifactTransferError('local transfer state belongs to a different or invalid request') from None
        row = (self.inspect(prior['session_id']) if prior else
               self._request('POST', self.paths.collection, {**intent, 'idempotency_key': key}))
        session_id = row.get('id')
        if not isinstance(session_id, str) or not session_id or prior and session_id != prior['session_id']:
            raise ArtifactTransferError('transfer response has no durable session identity')
        self._correlate(row, session_id, intent)
        if state_path is not None:
            self._save_state(state_path, {**state_identity, 'session_id': session_id})
        if row.get('state') in {'admitted', 'initializing', 'initialization_uncertain'}:
            row = self._correlate(self._request('POST', self.paths.initialize(session_id)), session_id, intent)
        if row.get('state') == 'uploading':
            inventory = self._request('GET', self.paths.parts(session_id))
            self._correlate(inventory.get('transfer'), session_id, intent)
            plan = row.get('plan', {})
            size, part_bytes, count = plan.get('size'), plan.get('part_bytes'), plan.get('part_count')
            if (type(size) is not int or size != identity['size'] or type(part_bytes) is not int or part_bytes <= 0
                    or type(count) is not int or count < 0 or count != (size + part_bytes - 1) // part_bytes):
                raise ArtifactTransferError('transfer geometry differs from expected bytes')
            try:
                MultipartPlan(size, part_bytes)
            except ValueError:
                raise ArtifactTransferError('transfer geometry exceeds provider protocol bounds') from None
            parts = inventory.get('parts')
            if not isinstance(parts, list):
                raise ArtifactTransferError('invalid retained part inventory')
            seen = {}
            for item in parts:
                if (not isinstance(item, dict) or type(item.get('number')) is not int or item['number'] in seen
                        or not 1 <= item['number'] <= count or type(item.get('size')) is not int):
                    raise ArtifactTransferError('invalid or duplicate retained part')
                seen[item['number']] = item
            for number in range(1, count + 1):
                self.check()
                offset = (number - 1) * part_bytes
                selected_size = min(part_bytes, size - offset)
                if number in seen:
                    if seen[number].get('size') != selected_size:
                        raise ArtifactTransferError('retained part differs from selected geometry')
                    continue
                descriptor = self._request('POST', self.paths.part(session_id, number))
                if (any(type(descriptor.get(field)) is not int for field in ('part_number', 'offset', 'size'))
                        or (descriptor.get('part_number'), descriptor.get('offset'), descriptor.get('size')) != (number, offset, selected_size)):
                    raise ArtifactTransferError('signed part descriptor differs from selected geometry')
                self.data.upload_part(descriptor, path, offset=offset, size=selected_size)
            if self.data.source_identity(path) != identity:
                raise ArtifactTransferError('local artifact changed during transfer')
            row = self._correlate(self._request('POST', self.paths.complete(session_id)), session_id, intent)
        elif row.get('state') in {'sealing', 'sealed', 'completion_uncertain'}:
            # This explicit resume asks the server to reconcile its durable seal;
            # the server must not blindly repeat an uncertain provider completion.
            row = self._correlate(self._request('POST', self.paths.complete(session_id)), session_id, intent)
        while row.get('state') == 'verifying' and row.get('operation_state') not in {'failed', 'lost', 'cancelled'}:
            self._wait()
            row = self._correlate(self.inspect(session_id), session_id, intent)
        if row.get('state') != 'verified' or not isinstance(row.get('artifact'), dict):
            raise ArtifactTransferError('artifact remains unpublished; inspect session and explicitly recover its operation')
        artifact = row['artifact']
        if (not isinstance(artifact.get('id'), str) or not artifact['id'] or type(artifact.get('size')) is not int
                or any(artifact.get(field) != value for field, value in intent.items())):
            raise ArtifactTransferError('verified artifact receipt differs from immutable request')
        return artifact
