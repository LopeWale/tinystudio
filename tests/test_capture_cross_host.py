"""Capture publications serialize across isolated hosts using real PostgreSQL."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from threading import Event, local
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.conftest import _create_test_database, _test_database_url
from tests.test_storage import MemoryS3
from tinystudio.db.models import Base, InterpCapture, Project, User
from tinystudio.interp.neurons import InterpError
from tinystudio.services import interp
from tinystudio.storage import ArtifactStorage, StorageError


@pytest.fixture
def capture_hosts(tmp_path, monkeypatch):
    problem = _create_test_database()
    if problem:
        pytest.skip(f"test PostgreSQL unavailable: {problem}")
    schema = "capture_hosts_" + uuid4().hex
    # Both concurrent owners fill this pool. Metadata work must reuse the
    # owner's connection rather than deadlock requesting a third slot.
    engine = create_engine(_test_database_url(), pool_size=2, max_overflow=0, pool_timeout=0.5)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(scoped, tables=[User.__table__, Project.__table__, InterpCapture.__table__])
        factory = sessionmaker(scoped, expire_on_commit=False)

        @contextmanager
        def session():
            with factory.begin() as db:
                yield db

        with session() as db:
            db.add(Project(id="p", slug="capture-test", name="Capture test"))
        backend = ArtifactStorage(client=MemoryS3(), bucket="test-only")
        host = local()
        host.root = tmp_path / "host-a"
        monkeypatch.setattr(interp, "config", lambda: SimpleNamespace(data_dir=host.root, project_dir=lambda p: host.root / "projects" / p))
        monkeypatch.setattr(interp, "storage", lambda: backend)
        monkeypatch.setattr(interp, "session", session)
        monkeypatch.setattr(interp, "db_engine", lambda: scoped)
        yield host, backend, session
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.parametrize("second_filename", ["explanations.jsonl", "tests.jsonl"])
@pytest.mark.parametrize("bypass_distributed_lock", [False, True])
def test_two_hosts_preserve_both_appends(capture_hosts, tmp_path, monkeypatch, second_filename, bypass_distributed_lock):
    """The no-ownership control loses host B's acknowledged publication."""
    host, backend, session = capture_hosts
    writer = interp.ProjectStore("p")
    directory = writer.captures_dir / "run"
    directory.mkdir(parents=True)
    meta = {"name": "run", "model_id": "base"}
    (directory / "meta.json").write_text(json.dumps(meta))
    writer.register("capture", "run", meta)
    assert backend.client.objects
    assert all(key.startswith("retained-captures/") for key in backend.client.objects)
    host_a, host_b = tmp_path / "host-a", tmp_path / "host-b"
    host.root = host_b
    second = interp.ProjectStore("p")
    assert not second.captures_dir.exists()
    if bypass_distributed_lock:
        monkeypatch.setattr(second, "_distributed_artifact_lock", lambda *args: nullcontext())
    publishing, release, second_started, second_hydrating, second_done = (Event() for _ in range(5))
    publish = backend.publish_directory

    def pause_first(path, key):
        if path.is_relative_to(host_a):
            publishing.set()
            assert release.wait(10), "host A publication was not released"
        return publish(path, key)

    monkeypatch.setattr(backend, "publish_directory", pause_first)
    hydrate = second._hydrate_artifact

    def track_hydration(*args):
        second_hydrating.set()
        return hydrate(*args)

    monkeypatch.setattr(second, "_hydrate_artifact", track_hydration)
    first_record = {"layer": 0, "neuron": 1, "explanation": "host A"}
    second_record = {"layer": 0, "neuron": 2, "explanation": "host B"}

    def append(store, root, filename, record, started=None):
        host.root = root
        if started:
            started.set()
        store.append_record("run", filename, record, meta)
        if started:
            second_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(append, writer, host_a, "explanations.jsonl", first_record)
        try:
            assert publishing.wait(10)
            other = pool.submit(append, second, host_b, second_filename, second_record, second_started)
            assert second_started.wait(5)
            assert second_hydrating.wait(0.2) is bypass_distributed_lock
            if bypass_distributed_lock:
                assert second_done.wait(10)
        finally:
            release.set()
        first.result(timeout=10)
        other.result(timeout=10)

    # Neither host's workspace is used to establish the durable result.
    cold = tmp_path / "host-c"
    backend.hydrate_directory("retained-captures/p/capture/run", cold, refresh=True)
    explanations = [json.loads(line) for line in (cold / "explanations.jsonl").read_text().splitlines()]
    tests = [json.loads(line) for line in (cold / "tests.jsonl").read_text().splitlines()] if (cold / "tests.jsonl").exists() else []
    assert explanations == ([first_record, second_record] if second_filename == "explanations.jsonl" and not bypass_distributed_lock else [first_record])
    assert tests == ([second_record] if second_filename == "tests.jsonl" and not bypass_distributed_lock else [])
    with session() as db:
        assert db.query(InterpCapture).one().meta["storage_prefix"] == "retained-captures/p/capture/run"


def test_remote_registration_without_prefix_never_serves_warm_mock_cache(capture_hosts):
    _, backend, session = capture_hosts
    store = interp.ProjectStore("p")
    directory = store.captures_dir / "mock"
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text('{"name":"mock"}')
    with session() as db:
        db.add(InterpCapture(project_id="p", kind="capture", name="mock", meta={"name": "mock"}, artifact_dir=str(directory)))
    with pytest.raises(InterpError, match="unavailable.*no durable storage prefix"):
        store.artifact_directory("capture", "mock")
    assert store.list_artifacts("capture")[0]["available"] is False
    assert backend.client.objects == {}  # old mock files are never migrated


def test_new_remote_capture_can_prepare_output_directory(capture_hosts):
    _, backend, _ = capture_hosts
    store = interp.ProjectStore("p")
    directory = store.artifact_directory("capture", "new")
    assert directory == store.captures_dir / "new"
    assert not directory.exists()
    assert backend.client.objects == {}


def test_publication_failure_releases_cross_host_ownership(capture_hosts, tmp_path):
    host, backend, _ = capture_hosts
    store = interp.ProjectStore("p")
    directory = store.captures_dir / "run"
    directory.mkdir(parents=True)
    meta = {"name": "run"}
    (directory / "meta.json").write_text(json.dumps(meta))
    store.register("capture", "run", meta)
    backend.client.fail_upload = True
    with pytest.raises(StorageError, match="upload failed"):
        store.append_record("run", "tests.jsonl", {"neuron": 1}, meta)
    backend.client.fail_upload = False
    host.root = tmp_path / "host-b"
    second = interp.ProjectStore("p")
    second.append_record("run", "tests.jsonl", {"neuron": 2}, meta)
    assert (second.captures_dir / "run" / "tests.jsonl").read_text() == '{"neuron": 2}\n'


def test_failed_initial_publication_deletes_only_durable_orphans(capture_hosts, monkeypatch):
    _, backend, session = capture_hosts
    store = interp.ProjectStore("p")
    directory = store.captures_dir / "run"
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text('{"name":"run"}')
    backend.client.objects["captures/p/capture/legacy/manifest.json"] = b"legacy untouched"
    upload = backend.client.upload_file

    def fail_manifest(path, bucket, key, **options):
        if key.endswith("manifest.json"):
            raise OSError("controlled manifest failure")
        return upload(path, bucket, key, **options)

    monkeypatch.setattr(backend.client, "upload_file", fail_manifest)
    with pytest.raises(StorageError, match="upload failed"):
        store.register("capture", "run", {"name": "run"})
    assert any(key.startswith("retained-captures/p/capture/run/objects/") for key in backend.client.objects)
    with session() as db:
        assert db.query(InterpCapture).count() == 0
    store.delete_artifact("capture", "run")
    assert backend.client.objects == {"captures/p/capture/legacy/manifest.json": b"legacy untouched"}
