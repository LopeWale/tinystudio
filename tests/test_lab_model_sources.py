"""Retained source boundaries; all metadata and storage are owned temporary fixtures."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tinystudio.core.file_manifest import directory_manifest
from tinystudio.core.project import ProjectError
from tinystudio.db.models import Base, Project
from tinystudio.lab.models import LabArtifact, LabAttempt, LabDefinition, LabEvent, LabRun, LabTarget
from tinystudio.serving import retained
from tinystudio.services import laboratory_models
from tinystudio.storage import ArtifactStorage, project_key

P, A = "1" * 32, "2" * 32


def bundle(tmp_path, monkeypatch, strategy="full", mutate=None):
    tree = tmp_path / "tree"
    tree.mkdir()
    trained = tree / ("model" if strategy == "full" else "adapter")
    trained.mkdir()
    (trained / "config.json").write_text('{}')
    (trained / "weights.safetensors").write_bytes(b"fixture weights")
    manifest = {"format": "tinystudio.pretrained.v1", "training_type": strategy,
        "update_strategy": strategy, "output_kind": "model" if strategy == "full" else "adapter",
        "trained_directory": trained.name, "trained_files": directory_manifest(trained)}
    if strategy == "lora":
        source = tree / "source-model"
        source.mkdir()
        (source / "config.json").write_text('{}')
        manifest["source_files"] = directory_manifest(source)
    if mutate:
        mutate(tree, manifest)
    (tree / "pretrained.json").write_text(json.dumps(manifest))
    archive = tmp_path / "bundle.tar"
    with tarfile.open(archive, "w") as stream:
        for path in tree.rglob("*"):
            if path.is_file():
                stream.add(path, arcname=path.relative_to(tree).as_posix())
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    source = retained.RetainedModelSource(P, A, sha, archive.stat().st_size, project_key(P, "lab", sha[:2], sha))
    store = ArtifactStorage(root=tmp_path / "objects")
    store.upload_file(archive, source.storage_key)
    monkeypatch.setattr(retained, "storage", lambda: store)
    return source, store


@pytest.mark.parametrize("value", ["lab:", "lab:" + "A" * 32, "lab:" + "1" * 31, "lab:../x"])
def test_reserved_malformed_ids_fail(value):
    with pytest.raises(ProjectError):
        retained.parse_laboratory_id(value)


def test_parser_preserves_hub_namespace_and_digit_identity():
    assert retained.parse_laboratory_id("lab/any-model") is None
    assert retained.parse_laboratory_id("lab:" + "0" * 32) == "0" * 32


@pytest.mark.parametrize("change", [{"project_id": "invalid"}, {"artifact_id": "../x"}, {"sha256": "X" * 64},
    {"size": True}, {"size": -1}, {"storage_key": "models/foreign/file"}, {"project_id": "3" * 32}])
def test_source_binding_rejects_invalid_fields(tmp_path, monkeypatch, change):
    source, _ = bundle(tmp_path, monkeypatch)
    with pytest.raises(ProjectError):
        replace(source, **change)


def test_source_identity_is_frozen_and_includes_project(tmp_path, monkeypatch):
    source, _ = bundle(tmp_path, monkeypatch)
    with pytest.raises(FrozenInstanceError):
        source.size = 0
    other = replace(source, project_id="3" * 32, storage_key=project_key("3" * 32, "lab", source.sha256[:2], source.sha256))
    assert source != other


@pytest.mark.parametrize("strategy", ["full", "lora"])
def test_open_owns_verified_files_and_cleans_after_close(tmp_path, monkeypatch, strategy):
    source, _ = bundle(tmp_path, monkeypatch, strategy)
    with source.open() as root:
        assert root.is_dir() and (root / "pretrained.json").is_file()
        owner = root.parent
    assert not owner.exists()


@pytest.mark.parametrize("failure", ["hash", "size", "download", "consumer"])
def test_cleanup_on_failure_after_allocation(tmp_path, monkeypatch, failure):
    source, store = bundle(tmp_path, monkeypatch)
    allocations = []
    original = retained.tempfile.TemporaryDirectory
    def allocate(*args, **kwargs):
        value = original(*args, **kwargs)
        allocations.append(Path(value.name))
        return value
    monkeypatch.setattr(retained.tempfile, "TemporaryDirectory", allocate)
    if failure == "hash":
        store._path(source.storage_key).write_bytes(b"x" * source.size)
    elif failure == "size":
        source = replace(source, size=source.size + 1)
    elif failure == "download":
        store.delete(source.storage_key)
    with pytest.raises((ProjectError, RuntimeError)):
        with source.open():
            raise RuntimeError("consumer failed")
    assert allocations and all(not path.exists() for path in allocations)


@pytest.mark.parametrize("change", [
    {"format": "tinystudio.pretrained.v9"}, {"trained_directory": "../model"}, {"trained_directory": "."},
    {"output_kind": "adapter"}, {"update_strategy": "lora"}, {"trained_files": None},
    {"quantization": {}}, {"training_type": "qlora"},
])
def test_inconsistent_manifest_rejected(tmp_path, monkeypatch, change):
    source, _ = bundle(tmp_path, monkeypatch, mutate=lambda tree, manifest: manifest.update(change))
    with pytest.raises(ProjectError):
        with source.open():
            pytest.fail("invalid manifest reached consumer")


def test_adapter_requires_retained_source_manifest(tmp_path, monkeypatch):
    source, _ = bundle(tmp_path, monkeypatch, "lora", mutate=lambda tree, manifest: manifest.pop("source_files"))
    with pytest.raises(ProjectError, match="source files checksum"):
        with source.open():
            pytest.fail("unverified source reached consumer")


@pytest.mark.parametrize("dependency", ["../outside.safetensors", "/outside.safetensors", "missing.safetensors"])
def test_weight_index_dependencies_cannot_escape_verified_model(tmp_path, monkeypatch, dependency):
    def mutate(tree, manifest):
        (tree / "outside.safetensors").write_bytes(b"unbound")
        (tree / "model" / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"weight": dependency}}))
        manifest["trained_files"] = directory_manifest(tree / "model")
    source, _ = bundle(tmp_path, monkeypatch, mutate=mutate)
    with pytest.raises(ProjectError):
        with source.open():
            pytest.fail("unsafe weight dependency reached consumer")


@pytest.mark.parametrize("kind", ["traversal", "symlink"])
def test_unsafe_archive_rejected_and_cleaned(tmp_path, monkeypatch, kind):
    source, store = bundle(tmp_path, monkeypatch)
    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w") as stream:
        member = tarfile.TarInfo("../escape" if kind == "traversal" else "link")
        if kind == "symlink":
            member.type, member.linkname = tarfile.SYMTYPE, "/tmp/foreign"
            stream.addfile(member)
        else:
            member.size = 1
            stream.addfile(member, io.BytesIO(b"x"))
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    source = replace(source, sha256=sha, size=archive.stat().st_size, storage_key=project_key(P, "lab", sha[:2], sha))
    store.upload_file(archive, source.storage_key)
    with pytest.raises(ProjectError):
        with source.open():
            pytest.fail("unsafe archive reached consumer")
    assert not (tmp_path / "escape").exists()


@pytest.fixture
def accepted():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Project.__table__, LabDefinition.__table__, LabTarget.__table__,
        LabRun.__table__, LabAttempt.__table__, LabArtifact.__table__, LabEvent.__table__])
    with Session(engine) as db:
        project = Project(id=P, slug="owned", name="Owned")
        target = LabTarget(id="3" * 32, project_id=P, name="Local")
        run = LabRun(id="4" * 32, project_id=P, definition_id="5" * 32, revision=2, target_id=target.id,
            idempotency_key="owned", spec={"definition": {"name": "Frozen model", "engine": "learning.pretrained", "learning": {"update_strategy": "full"}}},
            spec_digest="a" * 64, state="succeeded")
        attempt = LabAttempt(id="6" * 32, run_id=run.id, target_id=target.id, number=1, token_hash="b" * 64,
            lease_until=datetime.now(timezone.utc), state="succeeded", runtime={})
        artifact = LabArtifact(id=A, project_id=P, attempt_id=attempt.id, name="model.tar", sha256="c" * 64,
            size=123, media_type="application/x-tar", role="pretrained_model_bundle", visibility="model",
            storage_key=project_key(P, "lab", "cc", "c" * 64))
        reference = {key: getattr(artifact, key) for key in ("id", "name", "sha256", "size", "role", "visibility")}
        run.result = {"artifacts": [reference], "metrics": {"total_parameters": 17}}
        event = LabEvent(run_id=run.id, seq=0, kind="server.outcome", payload={"state": "succeeded", "attempt_id": attempt.id})
        db.add_all([project, target, run, attempt, artifact, event])
        db.commit()
        yield db, project, run, attempt, artifact, event, target
    engine.dispose()


def test_projection_and_resolution_are_metadata_only(accepted, monkeypatch):
    db, project, run, _, artifact, _, _ = accepted
    monkeypatch.setattr(retained, "storage", lambda: pytest.fail("listing downloaded artifact"))
    rows = laboratory_models.rows(db, project, {f"lab:{A}"})
    assert len(rows) == 1
    assert rows[0]["alias"] == "Frozen model r2 · 44444444"
    assert rows[0]["origin"] == "laboratory" and rows[0]["parameters"] == 17 and rows[0]["status"] == "up"
    assert laboratory_models.source(db, project, f"lab:{A}").sha256 == artifact.sha256


@pytest.mark.parametrize("object_name,field,value", [
    ("run", "state", "failed"), ("run", "state", "cancelled"), ("attempt", "state", "running"),
    ("artifact", "project_id", "7" * 32), ("run", "project_id", "7" * 32), ("target", "project_id", "7" * 32),
    ("artifact", "attempt_id", None), ("artifact", "visibility", "truth"), ("artifact", "visibility", "evaluation"),
    ("artifact", "role", "upload"), ("artifact", "sha256", "d" * 64), ("artifact", "size", 124),
    ("artifact", "name", "other.tar"), ("artifact", "storage_key", "lab/foreign/object"),
    ("attempt", "run_id", "7" * 32), ("attempt", "target_id", "7" * 32),
])
def test_projection_excludes_invalid_accepted_bindings(accepted, object_name, field, value):
    db, project, run, attempt, artifact, event, target = accepted
    setattr(locals()[object_name], field, value)
    db.commit()
    assert laboratory_models.rows(db, project, set()) == []
    with pytest.raises(ProjectError, match="not found"):
        laboratory_models.source(db, project, f"lab:{A}")


@pytest.mark.parametrize("failure", ["engine", "event", "event_attempt", "event_state", "manifest", "duplicate"])
def test_projection_requires_actual_accepted_pretrained_result(accepted, failure):
    db, project, run, attempt, artifact, event, _ = accepted
    if failure == "engine":
        run.spec = {"definition": {"engine": "learning.scratch"}}
    elif failure == "event":
        db.delete(event)
    elif failure == "event_attempt":
        event.payload = {"state": "succeeded", "attempt_id": "7" * 32}
    elif failure == "event_state":
        event.payload = {"state": "failed", "attempt_id": attempt.id}
    elif failure == "manifest":
        run.result = {"artifacts": []}
    else:
        run.result = {"artifacts": run.result["artifacts"] * 2}
    db.commit()
    assert laboratory_models.rows(db, project, set()) == []


def quantized(tree, manifest):
    manifest.update(format="tinystudio.pretrained.v2", training_type="qlora", update_strategy="qlora", output_kind="quantized_adapter")
    base = tree / "quantized-base"
    base.mkdir()
    (base / "config.json").write_text('{"quantization_config":{"quant_method":"bitsandbytes"}}')
    (base / "model.safetensors").write_bytes(b"retained packed base")
    probe = tree / "reload-probes.safetensors"
    probe.write_bytes(b"probe tensors validated by actual loader")
    manifest["quantization"] = {"format": "tinystudio.nf4-base.v1", "directory": "quantized-base", "files": directory_manifest(base)}
    manifest["reload_probes"] = {"file": probe.name, "sha256": hashlib.sha256(probe.read_bytes()).hexdigest(),
        "inputs": [{"input_ids": [[1]]}] * 3}


def test_v2_structure_preserves_packed_base_for_actual_loader(tmp_path, monkeypatch):
    source, _ = bundle(tmp_path, monkeypatch, "lora", mutate=quantized)
    with source.open() as root:
        assert (root / "quantized-base" / "model.safetensors").read_bytes() == b"retained packed base"
        assert json.loads((root / "pretrained.json").read_text())["output_kind"] == "quantized_adapter"


@pytest.mark.parametrize("failure", ["base_path", "base_hash", "adapter_hash", "probe_hash", "probe_path", "probe_inputs", "kind", "format"])
def test_v2_cannot_discard_packed_base_or_probes(tmp_path, monkeypatch, failure):
    def mutate(tree, manifest):
        quantized(tree, manifest)
        if failure == "base_path":
            manifest["quantization"]["directory"] = "source-model"
        elif failure == "base_hash":
            (tree / "quantized-base" / "model.safetensors").write_bytes(b"changed base")
        elif failure == "adapter_hash":
            (tree / "adapter" / "weights.safetensors").write_bytes(b"changed adapter")
        elif failure == "probe_hash":
            manifest["reload_probes"]["sha256"] = "d" * 64
        elif failure == "probe_path":
            manifest["reload_probes"]["file"] = "../reload-probes.safetensors"
        elif failure == "probe_inputs":
            manifest["reload_probes"]["inputs"] = []
        elif failure == "kind":
            manifest["output_kind"] = "adapter"
        else:
            manifest["format"] = "tinystudio.pretrained.v1"
    source, _ = bundle(tmp_path, monkeypatch, "lora", mutate=mutate)
    with pytest.raises(ProjectError):
        with source.open():
            pytest.fail("incomplete quantized bundle reached loader")


def test_weight_index_valid_member_can_load(tmp_path, monkeypatch):
    def mutate(tree, manifest):
        (tree / "model" / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"weight": "weights.safetensors"}}))
        manifest["trained_files"] = directory_manifest(tree / "model")
    source, _ = bundle(tmp_path, monkeypatch, mutate=mutate)
    with source.open() as root:
        assert (root / "model" / "weights.safetensors").is_file()


def test_revalidation_observes_metadata_change_in_current_session(accepted):
    db, project, _, _, artifact, _, _ = accepted
    assert laboratory_models.source(db, project, f"lab:{A}")
    artifact.visibility = "evaluation"
    db.commit()
    with pytest.raises(ProjectError):
        laboratory_models.source(db, project, f"lab:{A}")


def test_projectless_inventory_empty_and_source_requires_project():
    assert laboratory_models.rows(None, None, set()) == []
    with pytest.raises(ProjectError, match="project is required"):
        laboratory_models.source(None, None, f"lab:{A}")


def test_oversize_archive_rejected_before_storage_or_allocation(tmp_path, monkeypatch):
    source, _ = bundle(tmp_path, monkeypatch)
    source = replace(source, size=8 * 1024 ** 3 + 1)
    monkeypatch.setattr(retained, "storage", lambda: pytest.fail("oversize archive attempted storage access"))
    monkeypatch.setattr(retained.tempfile, "TemporaryDirectory", lambda **kwargs: pytest.fail("oversize archive allocated scratch storage"))
    with pytest.raises(ProjectError, match="8 GiB serving limit"):
        with source.open():
            pytest.fail("oversize archive reached loader")


def test_nested_index_cannot_authorize_a_different_dependency_root(tmp_path, monkeypatch):
    # Installed Transformers get_checkpoint_shard_files joins to the model root,
    # even if an index resides deeper. A file beside the index cannot cover it.
    def mutate(tree, manifest):
        nested = tree / "model" / "nested"
        nested.mkdir()
        (nested / "only-here.safetensors").write_bytes(b"not the loader dependency")
        (nested / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"weight": "only-here.safetensors"}}))
        manifest["trained_files"] = directory_manifest(tree / "model")
    source, _ = bundle(tmp_path, monkeypatch, mutate=mutate)
    with pytest.raises(ProjectError, match="missing or unsafe"):
        with source.open():
            pytest.fail("index-relative shard incorrectly covered loader dependency")
