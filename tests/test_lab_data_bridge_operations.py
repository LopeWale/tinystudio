"""Real legacy dataset COPY and snapshot storage behind durable lab operations."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from test_lab_operations import database, setup  # isolated PostgreSQL fixtures
from tinystudio.db.models import Dataset, Project, Snapshot
from tinystudio.lab import bridge, operations as ops, service as svc
from tinystudio.services import datasets, snapshots


@pytest.fixture
def evidence(setup, tmp_path, monkeypatch):
    factory, project, other, run_id, store = setup
    monkeypatch.setattr(snapshots, "storage", lambda: store)
    monkeypatch.setattr(snapshots, "config", lambda: SimpleNamespace(project_dir=lambda _: tmp_path / "cache"))
    rows = []
    for partition, count in (("train", 10), ("validation", 2), ("final_test", 1), ("truth", 1)):
        for index in range(count):
            rows.append({"source_record_id": f"{partition}:{index}", "episode_id": f"episode:{partition}:{index}",
                         "partition": partition, "value": -1.25 + index / 8, "units": "V",
                         "messages": [{"role": "user", "content": f"Recorded {partition} {index}"},
                                      {"role": "assistant", "content": "-1.25 V"}]})
    path = tmp_path / "authored.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with factory.begin() as db:
        artifact = svc.store_artifact(db, project, path, path.name, "application/x-ndjson", "chat_dataset")
        svc.get_run(db, project, run_id).result = {"artifacts": [svc.artifact_dict(artifact)]}
        artifact_id = artifact.id
    return factory, project, other, run_id, artifact_id


def admit(evidence, key="promotion", **options):
    factory, project, _, run_id, artifact_id = evidence
    with factory.begin() as db:
        return ops.request_dataset_promotion(db, project, run_id, artifact_id, "prepared", key,
                                             snapshot_name="prepared-v1", **options).id


def test_promotes_once_and_freezes_legacy_snapshot_without_final_test_or_truth(evidence):
    factory, project, _, run_id, artifact_id = evidence
    operation_id = admit(evidence)
    assert admit(evidence) == operation_id
    result = ops.run_operation(operation_id, factory)
    assert result["state"] == "succeeded"
    assert admit(evidence) == operation_id  # replay after names are occupied
    assert result["kind"] == "data_bridge"
    assert result["result"]["snapshot"]["training_count"] == 10
    assert result["result"]["snapshot"]["validation_count"] == 2
    assert result["result"]["excluded_final_test_rows"] == 1
    assert result["result"]["excluded_truth_rows"] == 1
    assert ops.run_operation(operation_id, factory) is None
    with factory() as db:
        p = db.get(Project, project)
        dataset = datasets.get_dataset(db, p, "prepared")
        assert dataset.origin["run_id"] == run_id
        assert dataset.origin["artifact_id"] == artifact_id
        snapshot = snapshots.get_snapshot(db, p, "prepared-v1")
        train, validation = snapshots.snapshot_rows(db, snapshot)
        assert len(train) == 10 and len(validation) == 2
        assert {row["source"] for row in train} == {f"train:{i}" for i in range(10)}
        assert {row["source"] for row in validation} == {"validation:0", "validation:1"}
        assert all(row["messages"][-1]["content"] == "-1.25 V" for row in train + validation)
        assert db.scalar(select(func.count()).select_from(Dataset).where(Dataset.project_id == project)) == 1
        assert db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.project_id == project)) == 1
        output = result["result"]
        manifest = svc.get_artifact(db, project, output["provenance_manifest"]["id"])
        retained = json.loads(svc.materialize(manifest, snapshots.config().project_dir(project) / "manifest").read_text())
        assert [m["dataset_position"] for m in retained["source_mapping"]] == list(range(12)) + [None, None]
        assert {ref["role"] for ref in output["excluded_artifacts"]} == {"final_test", "truth"}


def test_admission_checks_scope_completed_run_and_request_identity(evidence):
    factory, project, other, run_id, artifact_id = evidence
    operation_id = admit(evidence)
    with factory.begin() as db:
        with pytest.raises(svc.LabError):
            ops.request_dataset_promotion(db, other, run_id, artifact_id, "prepared", "other")
        with pytest.raises(svc.LabError, match="different operation"):
            ops.request_dataset_promotion(db, project, run_id, artifact_id, "changed", "promotion", snapshot_name="prepared-v1")
        assert ops.get_operation(db, project, operation_id).state == "queued"
        svc.get_run(db, project, run_id).state = "running"
    with factory.begin() as db:
        with pytest.raises(svc.LabError, match="completed"):
            ops.request_dataset_promotion(db, project, run_id, artifact_id, "later", "later")


def test_frozen_source_change_fails_without_creating_dataset(evidence):
    factory, project, _, run_id, _ = evidence
    operation_id = admit(evidence)
    with factory.begin() as db:
        run = svc.get_run(db, project, run_id)
        run.result = {**run.result, "changed_after_submission": True}
    assert ops.run_operation(operation_id, factory) is None
    with factory() as db:
        row = ops.get_operation(db, project, operation_id)
        assert row.state == "failed" and "changed" in row.error
        assert row.result is None
        assert db.scalar(select(func.count()).select_from(Dataset).where(Dataset.project_id == project)) == 0


def test_cancelled_publication_rolls_back_then_explicit_retry_succeeds(evidence, monkeypatch):
    factory, project, _, _, _ = evidence
    operation_id = admit(evidence)
    original = bridge.promote_chat_dataset

    def cancel_after_dataset(*args, **kwargs):
        output = original(*args, **kwargs)
        with factory.begin() as db:
            ops.cancel_operation(db, project, operation_id)
        return output

    monkeypatch.setattr(bridge, "promote_chat_dataset", cancel_after_dataset)
    assert ops.run_operation(operation_id, factory) is None
    with factory() as db:
        row = ops.get_operation(db, project, operation_id)
        assert row.state == "cancelled" and row.result is None
        assert db.scalar(select(func.count()).select_from(Dataset).where(Dataset.project_id == project)) == 0
        assert db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.project_id == project)) == 0
    monkeypatch.setattr(bridge, "promote_chat_dataset", original)
    with factory.begin() as db:
        ops.retry_operation(db, project, operation_id)
    assert ops.run_operation(operation_id, factory)["state"] == "succeeded"


def test_snapshot_failure_rolls_back_dataset_and_can_retry(evidence, monkeypatch):
    factory, project, _, _, _ = evidence
    operation_id = admit(evidence)
    original = snapshots.create_snapshot

    def fail(*args, **kwargs):
        raise RuntimeError("controlled snapshot storage failure")

    monkeypatch.setattr(snapshots, "create_snapshot", fail)
    assert ops.run_operation(operation_id, factory) is None
    with factory() as db:
        row = ops.get_operation(db, project, operation_id)
        assert row.state == "failed" and "controlled snapshot storage failure" in row.error
        assert db.scalar(select(func.count()).select_from(Dataset).where(Dataset.project_id == project)) == 0
    monkeypatch.setattr(snapshots, "create_snapshot", original)
    with factory.begin() as db:
        ops.retry_operation(db, project, operation_id)
    assert ops.run_operation(operation_id, factory)["state"] == "succeeded"


def test_persisted_snapshot_corruption_prevents_publication(evidence, monkeypatch, setup):
    factory, project, _, _, _ = evidence
    operation_id = admit(evidence)
    store = setup[-1]
    original = store.publish_directory
    corrupted = []

    def corrupt_snapshot(path, prefix):
        manifest = original(path, prefix)
        if prefix.startswith("snapshots/"):
            store._path(manifest["files"][0]["key"]).write_bytes(b"corrupt persisted shard")
            corrupted.append(prefix)
        return manifest

    monkeypatch.setattr(store, "publish_directory", corrupt_snapshot)
    assert ops.run_operation(operation_id, factory) is None
    assert len(corrupted) == 1  # establish that the intended storage fault ran
    with factory() as db:
        row = ops.get_operation(db, project, operation_id)
        assert row.state == "failed" and "checksum" in row.error
        assert db.scalar(select(func.count()).select_from(Dataset).where(Dataset.project_id == project)) == 0
        assert db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.project_id == project)) == 0


def test_execution_limit_retains_machine_readable_failure(evidence, monkeypatch):
    factory, project, _, _, _ = evidence
    operation_id = admit(evidence)
    monkeypatch.setattr(bridge, "MAX_ROWS", 2)
    assert ops.run_operation(operation_id, factory) is None
    with factory() as db:
        row = ops.operation_dict(ops.get_operation(db, project, operation_id))
        assert row["state"] == "failed" and row["result"] is None
        assert row["error_detail"] == {"status": 413, "code": "limit_exceeded"}


def test_consistent_but_wrong_snapshot_bytes_cannot_replace_admitted_chats(evidence, monkeypatch, setup):
    import pyarrow as pa
    import pyarrow.parquet as pq
    factory, project, _, _, _ = evidence
    operation_id = admit(evidence)
    store = setup[-1]
    original = store.publish_directory
    injected = []

    def replace_before_upload(path, prefix):
        if prefix.startswith("snapshots/"):
            target = next(path.glob("train-*.parquet"))
            rows = pq.read_table(target)["row_json"].to_pylist()
            wrong = json.loads(rows[0])
            wrong["messages"][-1]["content"] = "+1.25 V substituted during publication"
            rows[0] = json.dumps(wrong)
            pq.write_table(pa.table({"row_json": pa.array(rows, type=pa.string())}), target)
            injected.append(prefix)
        return original(path, prefix)  # valid checksums for the wrong scientific data

    monkeypatch.setattr(store, "publish_directory", replace_before_upload)
    assert ops.run_operation(operation_id, factory) is None
    assert len(injected) == 1
    with factory() as db:
        row = ops.get_operation(db, project, operation_id)
        assert row.state == "failed" and "snapshot rows" in row.error
        assert db.scalar(select(func.count()).select_from(Dataset).where(Dataset.project_id == project)) == 0


def test_omitted_snapshot_manifest_members_prevent_publication(evidence, monkeypatch, setup):
    factory, project, _, _, _ = evidence
    operation_id = admit(evidence)
    store = setup[-1]
    original = store.publish_directory
    injected = []

    def omit_members(path, prefix):
        manifest = original(path, prefix)
        if prefix.startswith("snapshots/"):
            store._path(prefix + "/manifest.json").write_text(json.dumps({"version": 1, "files": []}))
            injected.append(prefix)
        return manifest

    monkeypatch.setattr(store, "publish_directory", omit_members)
    assert ops.run_operation(operation_id, factory) is None
    assert len(injected) == 1
    with factory() as db:
        row = ops.get_operation(db, project, operation_id)
        assert row.state == "failed" and "snapshot rows" in row.error
        assert db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.project_id == project)) == 0


def test_source_mutation_during_work_fails_final_validation(evidence, monkeypatch):
    factory, project, _, run_id, _ = evidence
    operation_id = admit(evidence)
    original = bridge.promote_chat_dataset

    def change_after_dataset(*args, **kwargs):
        output = original(*args, **kwargs)
        with factory.begin() as db:
            run = svc.get_run(db, project, run_id)
            run.result = {**run.result, "changed_during_materialization": True}
        return output

    monkeypatch.setattr(bridge, "promote_chat_dataset", change_after_dataset)
    assert ops.run_operation(operation_id, factory) is None
    with factory() as db:
        row = ops.get_operation(db, project, operation_id)
        assert row.state == "failed" and "changed" in row.error
        assert db.scalar(select(func.count()).select_from(Dataset).where(Dataset.project_id == project)) == 0


def test_competing_admissions_do_not_create_duplicate_names(evidence):
    factory, project, _, _, _ = evidence
    first, second = admit(evidence, "first"), admit(evidence, "second")
    assert ops.run_operation(first, factory)["state"] == "succeeded"
    assert ops.run_operation(second, factory) is None
    with factory() as db:
        row = ops.get_operation(db, project, second)
        assert row.state == "failed" and row.error_detail["status"] == 409
        assert db.scalar(select(func.count()).select_from(Dataset).where(Dataset.project_id == project)) == 1


def test_migration_refuses_to_discard_historical_bridge_operations(evidence):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from tinystudio.db.migrate import downgrade
    factory, project, _, _, _ = evidence
    operation_id = admit(evidence)
    engine = factory.kw["bind"]
    with pytest.raises(DBAPIError, match="retain data bridge operations"):
        downgrade("g0c2d6e54f38", engine.url.render_as_string(hide_password=False))
    with factory() as db:
        assert db.execute(text("select version_num from alembic_version")).scalar() == "h1d3e7f65a49"
        assert ops.get_operation(db, project, operation_id).state == "queued"
