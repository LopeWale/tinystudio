"""Direct upload acceptance: authorization, staging isolation, and import completion."""
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from tinystudio.api.deps import get_db, project_dep
from tinystudio.api.routes import artifacts


@pytest.fixture
def upload_api(monkeypatch):
    class Store:
        remote = True
        objects = {}
        signed = []
        def presign(self, key, **kwargs):
            self.signed.append((key, kwargs))
            return "https://example.r2.cloudflarestorage.com/signed"
        def exists(self, key): return key in self.objects
        def size(self, key): return len(self.objects[key])
        def download_file(self, key, path): Path(path).write_bytes(self.objects[key])
        def delete(self, key): del self.objects[key]
    store = Store()
    monkeypatch.setattr(artifacts, "storage", lambda: store)
    app = FastAPI()
    app.include_router(artifacts.router)
    app.dependency_overrides[project_dep] = lambda: SimpleNamespace(id="project-one")
    app.dependency_overrides[get_db] = lambda: object()
    return TestClient(app), store


def test_grant_chooses_key_and_signs_size(upload_api):
    client, store = upload_api
    response = client.post('/api/p/demo/artifacts/uploads', json={"filename":"../../data.jsonl","size":34})
    assert response.status_code == 200
    key, params = store.signed[0]
    assert key.startswith('uploads/project-one/') and key.endswith('/data.jsonl')
    assert '..' not in key and params['content_length'] == 34 and params['expires'] == 900
    assert client.post('/api/p/demo/artifacts/uploads', json={"filename":"a","size":0}).status_code == 422


def test_foreign_and_missing_upload_rejected(upload_api):
    client, store = upload_api
    for key in ('uploads/project-two/'+'a'*32+'/data.jsonl', 'uploads/project-one/../../data.jsonl'):
        assert client.post('/api/p/demo/datasets/from-object',json={"name":"d","key":key}).status_code == 400
    key='uploads/project-one/'+'a'*32+'/data.jsonl'
    assert client.post('/api/p/demo/datasets/from-object',json={"name":"d","key":key}).status_code == 400


def test_completion_imports_bytes_before_deleting_staging(upload_api, monkeypatch):
    client, store = upload_api
    key='uploads/project-one/'+'a'*32+'/data.jsonl'
    store.objects[key]=b'validated fixture bytes'
    seen=[]
    def import_rows(db, project, name, path, filename):
        seen.append(Path(path).read_bytes())
        assert key in store.objects
        return {"name": name}
    monkeypatch.setattr(artifacts.datasets,'create_from_jsonl',import_rows)
    response=client.post('/api/p/demo/datasets/from-object',json={"name":"d","key":key})
    assert response.status_code==200 and seen==[b'validated fixture bytes']
    assert key not in store.objects


def test_failed_validation_preserves_retryable_staging(upload_api, monkeypatch):
    client, store = upload_api
    key='uploads/project-one/'+'a'*32+'/data.jsonl'
    store.objects[key]=b'invalid'
    def reject(*args): raise ValueError('invalid datapoint')
    monkeypatch.setattr(artifacts.datasets,'create_from_jsonl',reject)
    assert client.post('/api/p/demo/datasets/from-object',json={"name":"d","key":key}).status_code==400
    assert key in store.objects


@pytest.mark.parametrize('failure', ['archive', 'configuration', 'upload', 'sign'])
def test_weight_download_failure_removes_temp_archive(monkeypatch, tmp_path, failure):
    from fastapi import BackgroundTasks
    from tinystudio.api.routes import training
    import tinystudio.storage as storage_module
    source = tmp_path / 'experiment' / 'model'
    source.mkdir(parents=True)
    (source / 'weights.bin').write_bytes(b'owned model bytes')
    archive_dir = tmp_path / 'archive'
    archive_dir.mkdir()
    monkeypatch.setattr(training.jobs_svc, 'get_experiment', lambda *a: SimpleNamespace(id='experiment'))
    monkeypatch.setattr(training.jobs_svc, 'ensure_experiment_artifacts', lambda e: source.parent)
    monkeypatch.setattr(training.tempfile, 'mkdtemp', lambda **k: str(archive_dir))
    def fail(*a, **kw): raise RuntimeError('injected failure')
    class Store:
        remote = True
        def upload_file(self, *a, **kw):
            if failure == 'upload': fail()
        def presign(self, *a, **kw): fail()
    if failure == 'archive': monkeypatch.setattr(training.shutil, 'make_archive', fail)
    monkeypatch.setattr(storage_module, 'storage', fail if failure == 'configuration' else lambda: Store())
    with pytest.raises(RuntimeError, match='injected failure'):
        training.download_experiment('job', 1, BackgroundTasks(), SimpleNamespace(id='project'), object())
    assert not archive_dir.exists()
    assert (source/'weights.bin').read_bytes() == b'owned model bytes'
