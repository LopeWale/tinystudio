"""Source bytes cross API/worker hosts without any shared directory."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_storage import MemoryS3
from tinystudio.services import source_artifacts as sources
from tinystudio.storage import ArtifactStorage, StorageError


@pytest.fixture
def backend(monkeypatch):
    store = ArtifactStorage(client=MemoryS3(), bucket="source-test-only")
    monkeypatch.setattr(sources, "storage", lambda: store)
    return store


@pytest.mark.parametrize("kind,field", [("repo.add", "paths"), ("dataset.synthetic", "files")])
def test_queued_input_survives_removal_of_original_host(tmp_path, backend, kind, field):
    host_a = tmp_path / "api-host"
    host_a.mkdir()
    source = host_a / "notes.txt"
    source.write_bytes(b"The measured answer is 42.\n")
    args = sources.prepare_task_sources("project-a", kind, {field: [str(source)], "urls": []})
    assert args[field][0]["storage_key"].startswith("sources/project-a/")
    assert str(host_a) not in str(args)
    source.unlink()
    host_a.rmdir()
    with sources.materialized_sources("project-a", args[field]) as paths:
        copied = paths[0]
        assert copied.name == "notes.txt"
        assert copied.read_bytes() == b"The measured answer is 42.\n"
    assert not copied.exists(), "the executing worker must clean its temporary input"


def test_task_admission_fails_before_creating_row_when_source_cannot_publish(tmp_path, backend, monkeypatch):
    from tinystudio import worker
    source = tmp_path / "notes.txt"
    source.write_text("never admit host-local input")
    backend.client.fail_upload = True
    created = []
    monkeypatch.setattr(worker.tasks_svc, "create_task", lambda *a: created.append(a))
    with pytest.raises(StorageError, match="upload failed"):
        worker.enqueue(None, "repo.add", {"paths": [str(source)]}, SimpleNamespace(id="p"))
    assert created == []


def test_cross_project_and_corrupted_sources_are_rejected(tmp_path, backend):
    path = tmp_path / "private.txt"
    path.write_bytes(b"private project bytes")
    reference = sources.SourceArtifacts("a", backend).retain(path)
    with pytest.raises(StorageError, match="this project"):
        sources.SourceArtifacts("b", backend).materialize(reference, tmp_path / "other")
    backend.client.objects[reference["storage_key"]] = b"corrupt"
    with pytest.raises(StorageError, match="checksum"):
        sources.SourceArtifacts("a", backend).materialize(reference, tmp_path / "corrupt")
    assert not (tmp_path / "corrupt" / "private.txt").exists()


def test_remote_worker_refuses_legacy_local_path_even_if_present(tmp_path, backend):
    source = tmp_path / "still-present.txt"
    source.write_text("a warm cache is not durable input")
    with pytest.raises(StorageError, match="host-local"):
        with sources.materialized_sources("p", [str(source)]):
            pytest.fail("remote task must not consume unretained local input")


def test_repository_handler_materializes_original_document(tmp_path, backend, monkeypatch):
    from tinystudio.worker.handlers import Context, repo_add
    from tinystudio.services import repositories
    path = tmp_path / "reference.txt"
    path.write_bytes(b"original document")
    args = sources.prepare_task_sources("p", "repo.add", {"repository_id": "r", "paths": [str(path)]})
    path.unlink()
    def add(repository, paths, urls, *rest):
        assert repository == "r"
        assert [p.read_bytes() for p in paths] == [b"original document"]
        return {"added": [p.name for p in paths]}
    monkeypatch.setattr(repositories, "add_documents", add)
    result = repo_add(Context("task", "p", None, args, lambda *a: None))
    assert result == {"added": ["reference.txt"]}


def test_dataset_import_retains_raw_bytes_in_addition_to_rows(tmp_path, backend, monkeypatch):
    from tinystudio.services import datasets
    import tinystudio.storage as storage_module
    path = tmp_path / "dataset.jsonl"
    original = b'{"messages":[{"role":"user","content":"what?"},{"role":"assistant","content":"42"}]}\n'
    path.write_bytes(original)
    monkeypatch.setattr(storage_module, "storage", lambda: backend)
    def create(db, project, name, rows, origin):
        return {"name": name, "rows": rows, "origin": origin}
    monkeypatch.setattr(datasets, "create_dataset", create)
    result = datasets.create_from_jsonl(None, SimpleNamespace(id="p"), "answer", path)
    ref = result["origin"]["source_artifact"]
    assert backend.client.objects[ref["storage_key"]] == original
    assert result["origin"]["path"] == "dataset.jsonl"
    assert str(tmp_path) not in str(result["origin"])
    assert result["rows"][0]["messages"][-1]["content"] == "42"


def test_source_failure_prevents_dataset_row_creation(tmp_path, backend, monkeypatch):
    from tinystudio.services import datasets
    import tinystudio.storage as storage_module
    path = tmp_path / "dataset.jsonl"
    path.write_text('{"messages":[{"role":"user","content":"q"},{"role":"assistant","content":"a"}]}\n')
    monkeypatch.setattr(storage_module, "storage", lambda: backend)
    monkeypatch.setattr(datasets, "create_dataset", lambda *a: pytest.fail("rows must wait for source publication"))
    backend.client.fail_upload = True
    with pytest.raises(StorageError):
        datasets.create_from_jsonl(None, SimpleNamespace(id="p"), "data", path)


def test_local_input_mode_is_compatible(tmp_path, monkeypatch):
    backend = ArtifactStorage(root=tmp_path / "objects")
    monkeypatch.setattr(sources, "storage", lambda: backend)
    path = tmp_path / "source.txt"
    path.write_text("local mode")
    ref = sources.prepare_task_sources("p", "repo.add", {"paths": [str(path)]})["paths"][0]
    path.unlink()
    with sources.materialized_sources("p", [ref]) as paths:
        assert paths[0].read_text() == "local mode"


def test_r2_configuration_ignores_persistent_data_root(tmp_path, monkeypatch):
    import tinystudio.config as module
    monkeypatch.setenv("TINYSTUDIO_STORAGE_BACKEND", "r2")
    monkeypatch.setenv("TINYSTUDIO_DATA_DIR", str(tmp_path / "old-durable-data"))
    monkeypatch.delenv("TINYSTUDIO_CACHE_DIR", raising=False)
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(tmp_path / "scratch"))
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket-a")
    first = module._load()
    assert first.data_dir.is_relative_to(tmp_path / "scratch")
    assert not first.data_dir.is_relative_to(tmp_path / "old-durable-data")
    assert first.artifact_location == "r2://bucket-a"
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket-b")
    assert module._load().data_dir != first.data_dir
    monkeypatch.setenv("TINYSTUDIO_CACHE_DIR", str(tmp_path / "worker-b-cache"))
    assert module._load().cache_dir == tmp_path / "worker-b-cache"
    monkeypatch.setenv("TINYSTUDIO_STORAGE_BACKEND", "local")
    assert module._load().data_dir == tmp_path / "old-durable-data"


@pytest.mark.parametrize("endpoint", ["synthetic", "repository"])
def test_multipart_api_retains_inputs_before_queuing(tmp_path, backend, monkeypatch, endpoint):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tinystudio import worker
    from tinystudio.api.deps import current_user, get_db, project_dep
    from tinystudio.api.routes import data, serving
    app = FastAPI()
    app.include_router(data.router)
    app.include_router(serving.router)
    app.dependency_overrides[project_dep] = lambda: SimpleNamespace(id="p", goal="goal")
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(user=SimpleNamespace(id="u"))
    monkeypatch.setattr(serving.repositories_svc, "get_repository", lambda *a: SimpleNamespace(id="r"))
    queued = []
    def enqueue(db, kind, args, *rest):
        queued.append((kind, args))
        return {"id": "queued-on-another-host"}
    monkeypatch.setattr(worker, "enqueue", enqueue)
    url = "/api/p/demo/datasets/synthetic" if endpoint == "synthetic" else "/api/p/demo/repositories/reference/documents"
    response = TestClient(app).post(url, data={"name": "generated"}, files={"files": ("source.txt", b"remote input", "text/plain")})
    assert response.status_code == 200, response.text
    kind, args = queued[0]
    field = "files" if kind == "dataset.synthetic" else "paths"
    assert len(args[field]) == 1
    with sources.materialized_sources("p", args[field]) as paths:
        assert paths[0].read_bytes() == b"remote input"
        assert paths[0].name == "source.txt"


def test_original_source_can_be_downloaded_through_another_api_host(tmp_path, backend, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tinystudio.api.deps import project_dep
    from tinystudio.api.routes import artifacts
    path = tmp_path / "original.txt"
    path.write_text("original source")
    ref = sources.SourceArtifacts("p", backend).retain(path)
    path.unlink()
    app = FastAPI()
    app.include_router(artifacts.router)
    app.dependency_overrides[project_dep] = lambda: SimpleNamespace(id="p")
    monkeypatch.setattr(artifacts, "storage", lambda: backend)
    response = TestClient(app).get(f"/api/p/demo/artifacts/sources/{ref['sha256']}/original.txt", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://example.invalid/signed"
    assert backend.client.signed[1]["Key"] == ref["storage_key"]
