"""Artifact behavior with an in-memory S3 boundary; never accesses deployment R2."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinystudio.storage import ArtifactNotFound, ArtifactStorage, StorageError, project_key


class Missing(Exception):
    response = {'Error': {'Code': 'NoSuchKey'}}


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.uploads = []
        self.fail_upload = False
        self.fail_download = False

    def upload_file(self, path, bucket, key, **options):
        if self.fail_upload:
            raise OSError('transfer interrupted')
        self.objects[key] = Path(path).read_bytes()
        self.uploads.append((key, options))

    def download_file(self, bucket, key, path):
        if key not in self.objects:
            raise Missing()
        Path(path).write_bytes(self.objects[key])
        if self.fail_download:
            raise OSError('transfer interrupted')

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise Missing()
        return {'ContentLength': len(self.objects[Key])}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)

    def list_objects_v2(self, **options):
        return {'Contents': [{'Key': key} for key in list(self.objects) if key.startswith(options['Prefix'])], 'IsTruncated': False}

    def generate_presigned_url(self, method, *, Params, ExpiresIn):
        self.signed = (method, Params, ExpiresIn)
        return 'https://example.invalid/signed'


@pytest.fixture
def backend():
    return ArtifactStorage(client=MemoryS3(), bucket='test-only')


def test_project_keys_and_local_symlink_escape(tmp_path):
    assert project_key('p1', 'captures', 'capture', 'example') == 'captures/p1/capture/example'
    for segment in ('../other', '..', '.', '/absolute', 'x\\y', 'x\ny'):
        with pytest.raises(StorageError):
            project_key('p1', 'uploads', segment)
    local = ArtifactStorage(root=tmp_path / 'objects')
    local.root.mkdir()
    (local.root / 'escape').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(StorageError):
        local.exists('escape/secret')


def test_local_upload_download_delete(tmp_path):
    backend = ArtifactStorage(root=tmp_path / 'objects')
    source = tmp_path / 'source'
    source.write_bytes(b'weight data')
    key = project_key('p', 'models', 'm', 'weights')
    backend.upload_file(source, key)
    assert backend.size(key) == 11
    output = backend.download_file(key, tmp_path / 'cache' / 'weights')
    assert output.read_bytes() == b'weight data'
    backend.delete(key)
    assert not backend.exists(key)
    with pytest.raises(ArtifactNotFound):
        backend.download_file(key, output)
    with pytest.raises(StorageError):
        backend.presign(key)


def test_multipart_and_signing_are_explicit(backend, tmp_path):
    source = tmp_path / 'blob'
    source.write_bytes(b'123')
    backend.upload_file(source, 'uploads/p/blob', 'application/json')
    options = backend.client.uploads[-1][1]
    assert options['Config'].multipart_threshold == 64 * 1024**2
    assert options['Config'].multipart_chunksize == 16 * 1024**2
    assert options['ExtraArgs'] == {'ContentType': 'application/json'}
    assert backend.size('uploads/p/blob') == 3
    backend.presign('uploads/p/blob', method='put_object', content_type='application/json', content_length=3)
    assert backend.client.signed == ('put_object', {'Bucket': 'test-only', 'Key': 'uploads/p/blob', 'ContentType': 'application/json', 'ContentLength': 3}, 900)
    with pytest.raises(StorageError):
        backend.presign('uploads/p/blob', method='delete_object')


def test_transfer_failures_never_replace_usable_cache(backend, tmp_path):
    output = tmp_path / 'cache'
    output.write_bytes(b'old bytes')
    backend.client.objects['models/p/file'] = b'incomplete transfer'
    backend.client.fail_download = True
    with pytest.raises(StorageError):
        backend.download_file('models/p/file', output)
    assert output.read_bytes() == b'old bytes'
    assert list(tmp_path.glob('.artifact-*')) == []
    backend.client.fail_upload = True
    with pytest.raises(StorageError):
        backend.upload_file(output, 'models/p/new')
    assert 'models/p/new' not in backend.client.objects


def test_directory_publication_cache_repair_and_no_read_uploads(backend, tmp_path):
    source = tmp_path / 'source'
    (source / 'nested').mkdir(parents=True)
    (source / 'nested' / 'weights').write_bytes(b'weights')
    (source / 'config.json').write_text('{}')
    prefix = 'models/p/experiment/final'
    manifest = backend.publish_directory(source, prefix)
    assert len(manifest['files']) == 2
    assert backend.client.uploads[-1][0] == prefix + '/manifest.json'
    count = len(backend.client.uploads)
    cache = tmp_path / 'cache'
    backend.hydrate_directory(prefix, cache)
    assert (cache / 'nested' / 'weights').read_bytes() == b'weights'
    (cache / 'nested' / 'weights').write_bytes(b'corrupt')
    backend.hydrate_directory(prefix, cache)
    assert (cache / 'nested' / 'weights').read_bytes() == b'weights'
    assert len(backend.client.uploads) == count
    backend.publish_directory(source, prefix)
    assert len(backend.client.uploads) == count + 1  # unchanged objects are immutable


def test_manifest_corruption_and_expiry_are_not_cached_success(backend, tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'meta.json').write_text('{"name":"capture"}')
    prefix = 'captures/p/capture/run'
    manifest = backend.publish_directory(source, prefix)
    cache = tmp_path / 'cache'
    backend.hydrate_directory(prefix, cache, refresh=True)
    del backend.client.objects[manifest['files'][0]['key']]
    with pytest.raises(ArtifactNotFound):
        backend.hydrate_directory(prefix, cache, refresh=True)
    assert (cache / 'meta.json').exists()  # retained scratch cannot bypass remote expiry
    backend.client.objects[prefix + '/manifest.json'] = json.dumps({'version': 1, 'files': [{'path': '../escape', 'key': prefix + '/objects/a', 'sha256': 'a' * 64, 'size': 1}]}).encode()
    with pytest.raises(StorageError):
        backend.hydrate_directory(prefix, cache)


def test_checksum_failure_never_installs_bytes(backend, tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'weights').write_bytes(b'correct')
    manifest = backend.publish_directory(source, 'models/p/run')
    backend.client.objects[manifest['files'][0]['key']] = b'badbytes'
    with pytest.raises(StorageError, match='checksum'):
        backend.hydrate_directory('models/p/run', tmp_path / 'cache')
    assert not (tmp_path / 'cache' / 'weights').exists()


def test_snapshot_parquet_roundtrip_preserves_heterogeneous_rows_and_legacy(backend, tmp_path, monkeypatch):
    from tinystudio.services import snapshots

    train = [{'id': 'one', 'messages': [{'role': 'user', 'content': 'alpha'}], 'score': 3, 'labels': ['x']}, {'id': 'two', 'messages': [{'role': 'assistant', 'content': [{'type': 'text', 'text': 'beta'}]}], 'score': 'three', 'metadata': {'nested': [None, True]}}]
    val = [{'id': 'three', 'messages': [], 'labels': []}]
    dataset = SimpleNamespace(id='d', name='dataset')
    monkeypatch.setattr(snapshots.datasets_svc, 'get_dataset', lambda *args: dataset)
    monkeypatch.setattr(snapshots.datasets_svc, 'rows', lambda *args, bucket: train if bucket == 'training' else val)
    monkeypatch.setattr(snapshots, 'storage', lambda: backend)
    monkeypatch.setattr(snapshots, 'config', lambda: SimpleNamespace(project_dir=lambda p: tmp_path / p))

    class DB:
        def scalar(self, query):
            return None
        def add(self, row):
            self.snapshot = row
            row.id = 'snapshot-id'
        def flush(self):
            pass
        def execute(self, *args):
            pass
        def scalars(self, query):
            return [SimpleNamespace(row={'legacy': True})]

    db = DB()
    snapshots.create_snapshot(db, SimpleNamespace(id='p'), 'dataset', 'frozen', min_training=2)
    assert db.snapshot.storage_prefix == 'snapshots/p/snapshot-id'
    actual_train, actual_val = snapshots.snapshot_rows(db, db.snapshot)
    assert actual_train == [{k: v for k, v in r.items() if k != 'id'} for r in train]
    assert actual_val == [{'messages': [], 'labels': []}]
    backend.delete(db.snapshot.storage_prefix + '/manifest.json')
    with pytest.raises(ArtifactNotFound):
        snapshots.snapshot_rows(db, db.snapshot)
    legacy = SimpleNamespace(id='legacy', storage_prefix=None)
    assert snapshots.snapshot_rows(db, legacy) == ([{'legacy': True}], [{'legacy': True}])


def test_checkpoint_listing_and_model_resolution_hydrate_empty_worker_cache(backend, tmp_path, monkeypatch):
    from tinystudio.services import jobs, models

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'adapter_config.json').write_text('{}')
    (source / 'adapter_model.safetensors').write_bytes(b'adapter weights')
    prefix = 'models/p/exp/checkpoints/5'
    backend.publish_directory(source, prefix)
    exp = SimpleNamespace(artifact_dir='projects/p/experiments/j/1', config={'base_model': 'base'}, state='running', alias='ft-job-1', status={'artifact_checkpoints': [{'step': 5, 'prefix': prefix, 'epoch': 1, 'bytes': 17}]})
    monkeypatch.setattr(jobs, 'config', lambda: SimpleNamespace(data_dir=tmp_path / 'worker'))
    monkeypatch.setattr(jobs, 'storage', lambda: backend)
    assert jobs.experiment_checkpoint_rows(exp)[0]['step'] == 5
    resolved = models._resolve_experiment(exp, 'job/1@5', '5')
    assert (resolved.adapter_dir / 'adapter_model.safetensors').read_bytes() == b'adapter weights'
    (resolved.adapter_dir / 'adapter_model.safetensors').unlink()
    assert (models._resolve_experiment(exp, 'job/1@5', '5').adapter_dir / 'adapter_model.safetensors').is_file()


def test_capture_remote_discovery_and_expired_cache(backend, tmp_path, monkeypatch):
    from tinystudio.services import interp
    from tinystudio.interp import neurons

    root = tmp_path / 'data'
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'meta.json').write_text('{"name":"example","model_id":"base"}')
    manifest = backend.publish_directory(source, 'captures/p/capture/example')
    row = SimpleNamespace(name='example', meta={'name': 'example', 'model_id': 'base', 'storage_prefix': 'captures/p/capture/example'})
    @contextmanager
    def session():
        yield SimpleNamespace(scalar=lambda query: row, scalars=lambda query: [row])
    monkeypatch.setattr(interp, 'session', session)
    monkeypatch.setattr(interp, 'storage', lambda: backend)
    monkeypatch.setattr(interp, 'config', lambda: SimpleNamespace(data_dir=root, project_dir=lambda p: root / 'projects' / p))
    store = interp.ProjectStore('p')
    assert neurons.list_captures(store)[0]['name'] == 'example'
    assert (store.captures_dir / 'example' / 'meta.json').is_file()
    del backend.client.objects[manifest['files'][0]['key']]
    assert neurons.list_captures(store)[0]['available'] is False
    with pytest.raises(neurons.InterpError, match='unavailable'):
        neurons.capture_meta(store, 'example')


def test_manifest_publication_failure_preserves_previous_generation(backend, tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'weights').write_bytes(b'first')
    backend.publish_directory(source, 'models/p/run')
    original = backend.client.objects['models/p/run/manifest.json']
    (source / 'weights').write_bytes(b'second')
    upload = backend.client.upload_file
    def fail_manifest(path, bucket, key, **options):
        if key.endswith('/manifest.json'):
            raise OSError('manifest transfer interrupted')
        upload(path, bucket, key, **options)
    monkeypatch.setattr(backend.client, 'upload_file', fail_manifest)
    with pytest.raises(StorageError):
        backend.publish_directory(source, 'models/p/run')
    assert backend.client.objects['models/p/run/manifest.json'] == original
    backend.hydrate_directory('models/p/run', tmp_path / 'cache')
    assert (tmp_path / 'cache' / 'weights').read_bytes() == b'first'
    backend.delete_directory('models/p/run')
    assert not backend.client.objects  # includes failed-publication orphan


def test_hydration_prunes_stale_files_and_reuploads_missing_objects(backend, tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'weights').write_bytes(b'first')
    manifest = backend.publish_directory(source, 'models/p/run')
    cache = tmp_path / 'cache'
    backend.hydrate_directory('models/p/run', cache)
    (cache / 'old-model.bin').write_bytes(b'stale')
    backend.hydrate_directory('models/p/run', cache)
    assert not (cache / 'old-model.bin').exists()
    del backend.client.objects[manifest['files'][0]['key']]
    backend.publish_directory(source, 'models/p/run')
    assert backend.exists(manifest['files'][0]['key'])


@pytest.mark.parametrize('fail', [False, True])
def test_worker_publishes_before_success_and_fails_when_remote_upload_fails(backend, tmp_path, monkeypatch, fail):
    from tinystudio.db.models import Experiment
    from tinystudio.services import jobs
    from tinystudio.training.train import TrainConfig

    cfg = TrainConfig(base_model='base', checkpoint_every_steps=5)
    exp = SimpleNamespace(id='exp', state='pending', alias='ft-job-1', config=cfg.as_dict(), status={}, artifact_dir='projects/p/experiments/j/1', job=SimpleNamespace(project_id='p', snapshot_name='frozen'))
    class DB:
        def get(self, cls, id):
            return exp if cls is Experiment else SimpleNamespace(id='p')
        def add(self, row):
            pass
    @contextmanager
    def session():
        yield DB()
    monkeypatch.setattr(jobs, 'session', session)
    monkeypatch.setattr(jobs, 'config', lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(jobs, 'storage', lambda: backend)
    monkeypatch.setattr(jobs.snapshots_svc, 'load_snapshot', lambda *args: ([{'messages': []}], []))
    states = []
    def state(id, value, **extra):
        states.append(value)
        exp.state = value
        exp.status = {**exp.status, **extra}
        return {'state': value, **extra}
    monkeypatch.setattr(jobs, '_set_state', state)
    def train(directory, cfg, train, val, progress, weights, on_log):
        checkpoint = directory / 'checkpoints' / 'checkpoint-5'
        checkpoint.mkdir(parents=True)
        (checkpoint / 'adapter_config.json').write_text('{}')
        (checkpoint / 'adapter_model.safetensors').write_bytes(b'checkpoint')
        on_log('checkpoint', {'step': 5, 'epoch': 1})
        final = directory / 'adapter'
        final.mkdir()
        (final / 'adapter_config.json').write_text('{}')
        (final / 'adapter_model.safetensors').write_bytes(b'final')
        if fail:
            backend.client.fail_upload = True
        return {'loss': 0.5}
    monkeypatch.setattr(jobs, 'train_experiment', train)
    result = jobs.run_experiment('exp')
    assert states == ['running', 'failed' if fail else 'succeeded']
    assert exp.status['artifact_checkpoints'][0]['step'] == 5
    if fail:
        assert 'artifact_prefix' not in result
        assert not backend.exists('models/p/exp/final/manifest.json')
        assert 'upload failed' in result['error']
    else:
        assert result['artifact_prefix'] == 'models/p/exp/final'
        assert backend.exists('models/p/exp/final/manifest.json')
        import shutil
        shutil.rmtree(jobs.exp_path(exp))
        jobs.ensure_experiment_artifacts(exp)
        assert (jobs.exp_path(exp) / 'adapter' / 'adapter_model.safetensors').read_bytes() == b'final'


def test_prefix_cleanup_paginates_without_crossing_project_boundary(backend, monkeypatch):
    backend.client.objects = {'models/p/a': b'1', 'models/p/b': b'2', 'models/p-other/a': b'keep'}
    requests = []
    def pages(**options):
        requests.append(options)
        if 'ContinuationToken' not in options:
            return {'Contents': [{'Key': 'models/p/a'}], 'IsTruncated': True, 'NextContinuationToken': 'next'}
        return {'Contents': [{'Key': 'models/p/b'}], 'IsTruncated': False}
    monkeypatch.setattr(backend.client, 'list_objects_v2', pages)
    backend.delete_prefix('models/p')
    assert backend.client.objects == {'models/p-other/a': b'keep'}
    assert requests == [{'Bucket': 'test-only', 'Prefix': 'models/p/'}, {'Bucket': 'test-only', 'Prefix': 'models/p/', 'ContinuationToken': 'next'}]


def test_storage_factory_requires_explicit_backend_and_complete_r2_config(tmp_path, monkeypatch):
    import tinystudio.storage as module

    cfg = SimpleNamespace(data_dir=tmp_path, storage_backend='local', r2_endpoint_url='https://example.invalid', r2_bucket='present', r2_access_key_id='present', r2_secret_access_key='present')
    monkeypatch.setattr(module, 'config', lambda: cfg)
    assert module.storage().remote is False
    cfg.storage_backend = 'r2'
    cfg.r2_secret_access_key = None
    with pytest.raises(StorageError, match='requires endpoint'):
        module.storage()
    cfg.storage_backend = 'typo'
    with pytest.raises(StorageError, match='local or r2'):
        module.storage()


@pytest.mark.parametrize('kind', ['model', 'adapter'])
def test_parent_weights_restore_remote_artifacts_for_continued_training(backend, tmp_path, monkeypatch, kind):
    from tinystudio.services import jobs

    source = tmp_path / 'source'
    (source / kind).mkdir(parents=True)
    (source / kind / ('config.json' if kind == 'model' else 'adapter_config.json')).write_text('{}')
    (source / kind / 'weights.safetensors').write_bytes(b'weights')
    prefix = 'models/p/parent/final'
    backend.publish_directory(source, prefix)
    parent = SimpleNamespace(id='parent-id', state='succeeded', artifact_dir='projects/p/experiments/job/1', status={'artifact_prefix': prefix, 'weights_source': 'base'}, config={'base_model': 'base'})
    monkeypatch.setattr(jobs, 'config', lambda: SimpleNamespace(data_dir=tmp_path / 'fresh-worker'))
    monkeypatch.setattr(jobs, 'storage', lambda: backend)
    monkeypatch.setattr(jobs, 'find_experiment', lambda *args: parent)
    merges = []
    def merge(path, output, base, weights):
        assert (path / 'adapter' / 'weights.safetensors').read_bytes() == b'weights'
        merges.append(weights)
        output.mkdir(parents=True)
        (output / 'config.json').write_text('{}')
    monkeypatch.setattr(jobs, 'export_merged', merge)
    weights = jobs.parent_weights(None, SimpleNamespace(id='p'), 'job/1')
    assert (weights / 'config.json').is_file()
    jobs.parent_weights(None, SimpleNamespace(id='p'), 'job/1')
    assert merges == (['base'] if kind == 'adapter' else [])


@pytest.mark.parametrize('bypass_reader_lock', [False, True])
def test_capture_reader_lock_prevents_losing_appended_sidecars(backend, tmp_path, monkeypatch, bypass_reader_lock):
    """The unprotected-reader control reproduces the actual lost-append defect."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import nullcontext
    from threading import Event
    from tinystudio.services import interp

    root = tmp_path / 'data'
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'meta.json').write_text('{"name":"run","model_id":"base"}')
    old = {'layer': 0, 'neuron': 0, 'explanation': 'old'}
    new = {'layer': 0, 'neuron': 1, 'explanation': 'new'}
    (source / 'explanations.jsonl').write_text(json.dumps(old) + '\n')
    prefix = 'captures/p/capture/run'
    backend.publish_directory(source, prefix)
    row = SimpleNamespace(name='run', meta={'name': 'run', 'model_id': 'base', 'storage_prefix': prefix})
    @contextmanager
    def session():
        yield SimpleNamespace(scalar=lambda query: row)
    monkeypatch.setattr(interp, 'session', session)
    monkeypatch.setattr(interp, 'storage', lambda: backend)
    monkeypatch.setattr(interp, 'config', lambda: SimpleNamespace(data_dir=root, project_dir=lambda p: root / 'projects' / p))
    writer, reader = interp.ProjectStore('p'), interp.ProjectStore('p')
    writer.artifact_directory('capture', 'run')
    if bypass_reader_lock:
        monkeypatch.setattr(reader, '_artifact_lock', lambda *args: nullcontext())
    publishing, release, reader_started, reader_done = Event(), Event(), Event(), Event()
    publish = backend.publish_directory
    def pause_publication(path, key):
        publishing.set()
        assert release.wait(5), 'test did not release publication'
        return publish(path, key)
    monkeypatch.setattr(backend, 'publish_directory', pause_publication)
    def read():
        reader_started.set()
        reader.artifact_directory('capture', 'run')
        reader_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(writer.append_record, 'run', 'explanations.jsonl', new, dict(row.meta))
        try:
            assert publishing.wait(5)
            reading = pool.submit(read)
            assert reader_started.wait(5)
            # A viewer must not hydrate the previous remote generation over an
            # append that another process has not yet published.
            assert reader_done.wait(0.15) is bypass_reader_lock
        finally:
            release.set()
        writing.result(timeout=5)
        reading.result(timeout=5)
    cold = tmp_path / 'cold'
    backend.hydrate_directory(prefix, cold)
    records = [json.loads(line) for line in (cold / 'explanations.jsonl').read_text().splitlines()]
    assert records == ([old] if bypass_reader_lock else [old, new])


def test_capture_delete_waits_for_append_publication_and_preserves_lock_inode(backend, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from tinystudio.services import interp

    root = tmp_path / 'data'
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'meta.json').write_text('{"name":"run","model_id":"base"}')
    prefix = 'captures/p/capture/run'
    backend.publish_directory(source, prefix)
    row = SimpleNamespace(name='run', meta={'name': 'run', 'model_id': 'base', 'storage_prefix': prefix})
    deleted = []
    @contextmanager
    def session():
        yield SimpleNamespace(scalar=lambda query: row, delete=lambda item: deleted.append(item))
    monkeypatch.setattr(interp, 'session', session)
    monkeypatch.setattr(interp, 'storage', lambda: backend)
    monkeypatch.setattr(interp, 'config', lambda: SimpleNamespace(data_dir=root, project_dir=lambda p: root / 'projects' / p))
    writer, deleter = interp.ProjectStore('p'), interp.ProjectStore('p')
    writer.artifact_directory('capture', 'run')
    lock_path = Path(writer._artifact_lock('capture', 'run').lock_file)
    inode = lock_path.stat().st_ino
    publishing, release, deletion_started, deletion_entered = Event(), Event(), Event(), Event()
    publish = backend.publish_directory
    def pause_publication(path, key):
        publishing.set()
        assert release.wait(5)
        return publish(path, key)
    monkeypatch.setattr(backend, 'publish_directory', pause_publication)
    delete = deleter._delete_artifact
    def track_delete(*args):
        deletion_entered.set()
        return delete(*args)
    monkeypatch.setattr(deleter, '_delete_artifact', track_delete)
    def remove():
        deletion_started.set()
        deleter.delete_artifact('capture', 'run')
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(writer.append_record, 'run', 'tests.jsonl', {'layer': 0, 'neuron': 0}, dict(row.meta))
        try:
            assert publishing.wait(5)
            deleting = pool.submit(remove)
            assert deletion_started.wait(5)
            assert not deletion_entered.wait(0.15)
        finally:
            release.set()
        writing.result(timeout=5)
        deleting.result(timeout=5)
    assert deleted == [row]
    assert not backend.client.objects
    assert not (writer.captures_dir / 'run').exists()
    assert lock_path.stat().st_ino == inode
    assert not lock_path.is_relative_to(writer.captures_dir)


def test_storage_connection_configuration_cannot_be_mutated(backend, tmp_path):
    for name, value in [('root', tmp_path), ('client', None), ('bucket', 'other-bucket'), ('remote', False)]:
        with pytest.raises(AttributeError):
            setattr(backend, name, value)
    assert backend.remote is True
    assert backend.bucket == 'test-only'
    local = ArtifactStorage(root=tmp_path)
    assert local.remote is False
    assert local.root == tmp_path


@pytest.mark.parametrize('delete_job', [False, True])
@pytest.mark.parametrize('orphan', ['downloads/archive.zip', 'final/objects/partial/weights'])
def test_experiment_deletion_sweeps_marker_free_remote_objects(backend, tmp_path, monkeypatch, delete_job, orphan):
    from tinystudio.services import jobs

    exp = SimpleNamespace(id='exp', state='failed', status={}, artifact_dir='projects/p/experiments/j/1')
    job = SimpleNamespace(experiments=[exp])
    backend.client.objects = {f'models/p/exp/{orphan}': b'orphan', f'models/p/exp-other/{orphan}': b'neighbor', f'models/other/exp/{orphan}': b'other project'}
    monkeypatch.setattr(jobs, 'storage', lambda: backend)
    monkeypatch.setattr(jobs, 'config', lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(jobs, 'get_job', lambda *args: job)
    monkeypatch.setattr(jobs, 'get_experiment', lambda *args: exp)
    removed = []
    db = SimpleNamespace(delete=lambda row: removed.append(row), flush=lambda: None)
    if delete_job:
        jobs.delete_job(db, SimpleNamespace(id='p'), 'job')
    else:
        jobs.delete_experiment(db, SimpleNamespace(id='p'), 'job', 1)
    assert removed == [job if delete_job else exp]
    assert backend.client.objects == {f'models/p/exp-other/{orphan}': b'neighbor', f'models/other/exp/{orphan}': b'other project'}


@pytest.mark.parametrize('delete_job', [False, True])
def test_deleted_lora_experiment_number_reuse_cannot_reuse_merged_weights(backend, tmp_path, monkeypatch, delete_job):
    from tinystudio.services import jobs

    project = SimpleNamespace(id='p')
    exp = SimpleNamespace(id='original-id', state='succeeded', artifact_dir='projects/p/experiments/job/1', status={'artifact_prefix': 'models/p/original-id/final', 'weights_source': 'base'}, config={'base_model': 'base'})
    job = SimpleNamespace(experiments=[exp])
    def publish(exp, content):
        source = tmp_path / exp.id
        (source / 'adapter').mkdir(parents=True)
        (source / 'adapter' / 'adapter_config.json').write_text('{}')
        (source / 'adapter' / 'weights.safetensors').write_bytes(content)
        backend.publish_directory(source, exp.status['artifact_prefix'])
    publish(exp, b'original weights')
    monkeypatch.setattr(jobs, 'config', lambda: SimpleNamespace(data_dir=tmp_path / 'cache'))
    monkeypatch.setattr(jobs, 'storage', lambda: backend)
    monkeypatch.setattr(jobs, 'find_experiment', lambda *args: exp)
    monkeypatch.setattr(jobs, 'get_experiment', lambda *args: exp)
    monkeypatch.setattr(jobs, 'get_job', lambda *args: job)
    merges = []
    def merge(path, output, base, weights):
        content = (path / 'adapter' / 'weights.safetensors').read_bytes()
        merges.append(content)
        output.mkdir(parents=True)
        (output / 'config.json').write_text('{}')
        (output / 'weights.safetensors').write_bytes(content)
    monkeypatch.setattr(jobs, 'export_merged', merge)
    original_merged = jobs.parent_weights(None, project, 'job/1')
    assert original_merged.name == 'original-id-merged'
    assert (original_merged / 'weights.safetensors').read_bytes() == b'original weights'
    legacy = jobs.exp_path(exp).parent / '1-merged'
    legacy.mkdir()
    (legacy / 'config.json').write_text('{}')
    (legacy / 'weights.safetensors').write_bytes(b'obsolete number cache')
    db = SimpleNamespace(delete=lambda row: None, flush=lambda: None)
    if delete_job:
        jobs.delete_job(db, project, 'job')
    else:
        jobs.delete_experiment(db, project, 'job', 1)
    assert not original_merged.exists()
    assert not legacy.exists()
    exp = SimpleNamespace(id='replacement-id', state='succeeded', artifact_dir='projects/p/experiments/job/1', status={'artifact_prefix': 'models/p/replacement-id/final', 'weights_source': 'base'}, config={'base_model': 'base'})
    job = SimpleNamespace(experiments=[exp])
    publish(exp, b'different replacement weights')
    replacement_merged = jobs.parent_weights(None, project, 'job/1')
    assert replacement_merged.name == 'replacement-id-merged'
    assert replacement_merged != original_merged
    assert (replacement_merged / 'weights.safetensors').read_bytes() == b'different replacement weights'
    assert merges == [b'original weights', b'different replacement weights']
