"""Private artifact storage, with explicit local and R2 backends.

Directory manifests are published last and refer to immutable content-addressed
objects. Downloads replace files atomically; an interrupted transfer never becomes
a valid cache entry. Importing this module never opens a network connection.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from tinystudio.config import config
from tinystudio.core.project import ProjectError


class StorageError(ProjectError):
    """An artifact could not be persisted or retrieved."""


class ArtifactNotFound(StorageError):
    """The remote artifact is missing or has expired."""


def _key(value: str) -> str:
    if not isinstance(value, str) or not value or value.startswith('/') or '\\' in value or any(ord(c) < 32 for c in value):
        raise StorageError('invalid artifact key')
    if any(p in ('', '.', '..') for p in value.split('/')):
        raise StorageError('invalid artifact key')
    return value


def project_key(project_id: str, category: str, *parts: str) -> str:
    """Build a project-isolated object key; parts are individual path segments."""
    if category not in {'captures', 'retained-captures', 'snapshots', 'models', 'uploads', 'sources', 'lab'}:
        raise StorageError('invalid artifact category')
    for part in (project_id, *parts):
        if '/' in part or not part:
            raise StorageError('invalid artifact path segment')
        _key(part)
    return _key('/'.join((category, project_id, *parts)))


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _missing(cause: Exception) -> bool:
    return str(getattr(cause, 'response', {}).get('Error', {}).get('Code', '')) in {'404', 'NoSuchKey', 'NotFound'}


class ArtifactStorage:
    """Own one storage connection; callers own project authorization and keys."""

    def __init__(self, *, root: Path | None = None, client: Any = None, bucket: str | None = None) -> None:
        self._root = root
        self._client = client
        self._bucket = bucket
        if not self.remote and root is None:
            raise StorageError('local artifact root is required')
        if self.remote and not bucket:
            raise StorageError('artifact bucket is required')

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def client(self) -> Any:
        return self._client

    @property
    def bucket(self) -> str | None:
        return self._bucket

    @property
    def remote(self) -> bool:
        return self._client is not None

    def _path(self, key: str) -> Path:
        assert self.root is not None
        root = self.root.resolve()
        path = (root / _key(key)).resolve()
        if not path.is_relative_to(root):
            raise StorageError('artifact path escapes storage root')
        return path

    def upload_file(self, path: Path, key: str, content_type: str | None = None) -> str:
        key = _key(key)
        path = Path(path)
        if not path.is_file() or path.is_symlink():
            raise StorageError('artifact upload requires a regular file')
        if self.remote:
            from boto3.s3.transfer import TransferConfig
            options: dict[str, Any] = {'Config': TransferConfig(multipart_threshold=64 * 1024**2, multipart_chunksize=16 * 1024**2, max_concurrency=4)}
            if content_type:
                options['ExtraArgs'] = {'ContentType': content_type}
            try:
                self.client.upload_file(str(path), self.bucket, key, **options)
            except Exception as cause:
                raise StorageError(f'artifact upload failed: {key}') from cause
        else:
            self._copy_atomic(path, self._path(key))
        return key

    @staticmethod
    def _copy_atomic(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.artifact-', dir=target.parent)
        os.close(fd)
        try:
            shutil.copyfile(source, name)
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)

    def download_file(self, key: str, path: Path) -> Path:
        key = _key(key)
        path = Path(path)
        if not self.remote:
            source = self._path(key)
            if not source.is_file():
                raise ArtifactNotFound(f'artifact not found: {key}')
            self._copy_atomic(source, path)
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.artifact-', dir=path.parent)
        os.close(fd)
        try:
            self.client.download_file(self.bucket, key, name)
            os.replace(name, path)
        except Exception as cause:
            if _missing(cause):
                raise ArtifactNotFound(f'artifact not found or expired: {key}') from cause
            raise StorageError(f'artifact download failed: {key}') from cause
        finally:
            Path(name).unlink(missing_ok=True)
        return path

    def exists(self, key: str) -> bool:
        key = _key(key)
        if not self.remote:
            return self._path(key).is_file()
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as cause:
            if _missing(cause):
                return False
            raise StorageError(f'artifact lookup failed: {key}') from cause

    def size(self, key: str) -> int:
        key = _key(key)
        if not self.remote:
            path = self._path(key)
            if not path.is_file():
                raise ArtifactNotFound(f'artifact not found: {key}')
            return path.stat().st_size
        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)['ContentLength'])
        except Exception as cause:
            if _missing(cause):
                raise ArtifactNotFound(f'artifact not found: {key}') from cause
            raise StorageError(f'artifact lookup failed: {key}') from cause

    def delete(self, key: str) -> None:
        key = _key(key)
        if not self.remote:
            self._path(key).unlink(missing_ok=True)
            return
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as cause:
            raise StorageError(f'artifact deletion failed: {key}') from cause

    def presign(self, key: str, method: str = 'get_object', expires: int = 900, content_type: str | None = None, content_length: int | None = None) -> str:
        key = _key(key)
        if not self.remote:
            raise StorageError('presigned URLs require R2 storage')
        if method not in {'get_object', 'put_object'} or not 1 <= expires <= 3600:
            raise StorageError('invalid presigned URL request')
        params: dict[str, Any] = {'Bucket': self.bucket, 'Key': key}
        if method == 'put_object':
            if content_type:
                params['ContentType'] = content_type
            if content_length is not None:
                if content_length <= 0:
                    raise StorageError('upload size must be positive')
                params['ContentLength'] = content_length
        try:
            return self.client.generate_presigned_url(method, Params=params, ExpiresIn=expires)
        except Exception as cause:
            raise StorageError('could not sign artifact URL') from cause

    def read_manifest(self, prefix: str) -> dict[str, Any]:
        prefix = _key(prefix)
        with tempfile.TemporaryDirectory(prefix='tinystudio-manifest-') as tmp:
            path = self.download_file(prefix + '/manifest.json', Path(tmp) / 'manifest.json')
            try:
                manifest = json.loads(path.read_text())
                if manifest['version'] != 1 or not isinstance(manifest['files'], list):
                    raise ValueError('unsupported manifest')
                seen = set()
                for item in manifest['files']:
                    relative = _key(item['path'])
                    key = _key(item['key'])
                    if relative in seen or not key.startswith(prefix + '/objects/') or len(item['sha256']) != 64 or not isinstance(item['size'], int) or item['size'] < 0:
                        raise ValueError('invalid manifest entry')
                    seen.add(relative)
                return manifest
            except (KeyError, ValueError, TypeError) as cause:
                raise StorageError('invalid artifact manifest') from cause

    def publish_directory(self, path: Path, prefix: str) -> dict[str, Any]:
        """Publish only changed files, then atomically expose their complete manifest."""
        path, prefix = Path(path), _key(prefix)
        if not path.is_dir() or path.is_symlink():
            raise StorageError('artifact directory does not exist')
        try:
            previous = {f['key'] for f in self.read_manifest(prefix)['files']}
        except ArtifactNotFound:
            previous = set()
        files = []
        for source in sorted(path.rglob('*')):
            if source.is_symlink():
                raise StorageError('artifact directories cannot contain symbolic links')
            if not source.is_file() or source.name.startswith('.artifact-'):
                continue
            relative = _key(source.relative_to(path).as_posix())
            digest = _digest(source)
            key = prefix + '/objects/' + digest + '/' + relative
            if key not in previous or not self.exists(key):
                self.upload_file(source, key)
            files.append({'path': relative, 'key': key, 'sha256': digest, 'size': source.stat().st_size})
        manifest = {'version': 1, 'files': files}
        with tempfile.TemporaryDirectory(prefix='tinystudio-manifest-') as tmp:
            target = Path(tmp) / 'manifest.json'
            target.write_text(json.dumps(manifest))
            self.upload_file(target, prefix + '/manifest.json', 'application/json')
        return manifest

    def hydrate_directory(self, prefix: str, path: Path, refresh: bool = False) -> Path:
        """Hydrate missing/corrupt cache files; refresh also verifies remote lifetime."""
        manifest = self.read_manifest(prefix)
        if Path(path).is_symlink():
            raise StorageError('artifact cache directory cannot be a symbolic link')
        root = Path(path).resolve()
        expected = {item['path'] for item in manifest['files']}
        for item in manifest['files']:
            target = (root / item['path']).resolve()
            if not target.is_relative_to(root):
                raise StorageError('artifact cache path escapes directory')
            if refresh and not self.exists(item['key']):
                raise ArtifactNotFound('artifact has expired or was deleted')
            if not target.is_file() or target.stat().st_size != item['size'] or _digest(target) != item['sha256']:
                # Verify before replacing a previous usable cache entry.
                with tempfile.TemporaryDirectory(prefix='tinystudio-download-') as tmp:
                    downloaded = self.download_file(item['key'], Path(tmp) / 'file')
                    if downloaded.stat().st_size != item['size'] or _digest(downloaded) != item['sha256']:
                        raise StorageError('artifact checksum does not match manifest')
                    self._copy_atomic(downloaded, target)
        # This is an owned artifact cache. Remove stale files only after every
        # manifest member has passed verification, never during partial hydration.
        for cached in sorted(root.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            relative = cached.relative_to(root).as_posix()
            if cached.is_symlink() or cached.is_file():
                if relative not in expected:
                    cached.unlink()
            elif cached.is_dir() and not any(cached.iterdir()):
                cached.rmdir()
        return Path(path)

    def delete_prefix(self, prefix: str) -> None:
        """Delete one owned directory prefix, including superseded/orphan objects."""
        prefix = _key(prefix)
        if not self.remote:
            path = self._path(prefix)
            if path.exists():
                shutil.rmtree(path)
            return
        continuation = None
        try:
            while True:
                args = {'Bucket': self.bucket, 'Prefix': prefix + '/'}
                if continuation:
                    args['ContinuationToken'] = continuation
                page = self.client.list_objects_v2(**args)
                for item in page.get('Contents', []):
                    key = _key(item['Key'])
                    if not key.startswith(prefix + '/'):
                        raise StorageError('object listing escaped artifact prefix')
                    self.delete(key)
                if not page.get('IsTruncated'):
                    break
                continuation = page['NextContinuationToken']
        except StorageError:
            raise
        except Exception as cause:
            raise StorageError(f'artifact cleanup failed: {prefix}') from cause

    def delete_directory(self, prefix: str) -> None:
        # Remove visibility first, then sweep old versions and partial publications.
        self.delete(_key(prefix) + '/manifest.json')
        self.delete_prefix(prefix)


def storage() -> ArtifactStorage:
    """Construct per-process storage from explicit deployment configuration."""
    cfg = config()
    backend = getattr(cfg, 'storage_backend', 'local')
    if backend == 'local':
        return ArtifactStorage(root=cfg.data_dir / 'artifacts')
    if backend != 'r2':
        raise StorageError('TINYSTUDIO_STORAGE_BACKEND must be local or r2')
    required = ('r2_endpoint_url', 'r2_bucket', 'r2_access_key_id', 'r2_secret_access_key')
    if any(not getattr(cfg, name, None) for name in required):
        raise StorageError('R2 storage requires endpoint, bucket, access key ID and secret access key')
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', endpoint_url=cfg.r2_endpoint_url, aws_access_key_id=cfg.r2_access_key_id,
                          aws_secret_access_key=cfg.r2_secret_access_key, region_name=cfg.r2_region,
                          config=Config(signature_version='s3v4', request_checksum_calculation='when_required', response_checksum_validation='when_required'))
    return ArtifactStorage(client=client, bucket=cfg.r2_bucket)
