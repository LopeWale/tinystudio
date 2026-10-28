"""Pure serializers and fake service boundaries; no PostgreSQL/R2 processes used."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace as NS

import pytest

from tinystudio.lab import bridge
from tinystudio.lab.contracts import checksum


def chat(source="s1", group="g1", partition="train", **extra):
    return {"source_id": source, "group_id": group, "partition": partition,
            "messages": [{"role": "user", "content": "What was observed?"}, {"role": "assistant", "content": "An explicitly recorded response."}], **extra}


@pytest.fixture
def captured(monkeypatch):
    stored = []
    def store(db, project, path, name, media_type, role="source", attempt_id=None, **kwargs):
        content = Path(path).read_bytes()
        row = NS(id=f"artifact-{len(stored)+1}", project_id=project, name=name, media_type=media_type,
                 sha256=hashlib.sha256(content).hexdigest(), size=len(content), role=role,
                 visibility=kwargs.get("visibility", "model"))
        stored.append((row, content))
        return row
    monkeypatch.setattr(bridge.service, "store_artifact", store)
    return stored


class FakeDB:
    def __init__(self, value):
        self.value = value
        self.queries = []
    def scalar(self, statement):
        self.queries.append(statement)
        return self.value
    def execute(self, statement):
        self.queries.append(statement)
        count = self.value.training_count + self.value.validation_count if self.value is not None else 1
        return NS(one=lambda: (count, 100))


def test_snapshot_export_preserves_frozen_splits_and_original_metadata(monkeypatch, captured):
    snapshot = NS(id="snapshot-id", project_id="project", name="saved", dataset_id="dataset-id", dataset_name="source-data",
                  training_count=1, validation_count=1, storage_prefix=None, created=None)
    train = [chat(extra_science={"units": "V", "sample": -.125})]
    val = [chat("s2", "g2", "validation")]
    before = deepcopy((train, val))
    monkeypatch.setattr(bridge.snapshots_svc, "snapshot_rows", lambda db, snapshot: (train, val))
    db = FakeDB(snapshot)
    result = bridge.snapshot_input(db, NS(id="project"), "snapshot-id")
    rows = [json.loads(line) for line in captured[0][1].splitlines()]
    assert [row["source_id"] for row in rows] == ["snapshot:snapshot-id:train:0", "snapshot:snapshot-id:validation:0"]
    assert [row["partition"] for row in rows] == ["train", "validation"]
    assert rows[0]["extra_science"] == {"units": "V", "sample": -.125}
    assert result["input"]["role"] == "snapshot"
    assert result["final_test_available"] is False
    manifest = json.loads(captured[1][1])
    assert manifest["records"][0]["original_row"] == train[0]
    assert manifest["snapshot_id"] == "snapshot-id"
    assert (train,val) == before
    assert "project_id" in str(db.queries[0])


def test_snapshot_name_lookup_reuses_scoped_service(monkeypatch, captured):
    snapshot = NS(id="snapshot-id", project_id="p", name="friendly", dataset_id=None, dataset_name="d", training_count=1, validation_count=0, storage_prefix=None, created=None)
    observed = []
    monkeypatch.setattr(bridge.snapshots_svc, "get_snapshot", lambda db, project, name: observed.append((project.id,name)) or snapshot)
    monkeypatch.setattr(bridge.snapshots_svc, "snapshot_rows", lambda db, row: ([chat()], []))
    bridge.snapshot_input(FakeDB(None), NS(id="p"), "friendly")
    assert observed == [("p","friendly")]


def test_snapshot_caps_reject_before_materializing_large_rows(monkeypatch):
    snapshot=NS(project_id="p",training_count=bridge.MAX_ROWS+1,validation_count=0)
    monkeypatch.setattr(bridge.snapshots_svc,"snapshot_rows",lambda *args:pytest.fail("large snapshot must not be read"))
    with pytest.raises(bridge.BridgeLimit,match="split the evidence"):
        bridge.snapshot_input(FakeDB(snapshot),NS(id="p"),"large")


def test_foreign_snapshot_is_rejected(monkeypatch):
    with pytest.raises(bridge.service.LabError,match="not found"):
        bridge.snapshot_input(FakeDB(NS(project_id="foreign")),NS(id="p"),"foreign")


def test_chat_serializer_never_fabricates_qa_and_keeps_test_distinct_from_truth():
    rows=[chat(),chat("v","vg","validation"),chat("final","fg","final_test"),chat("truth","tg","validation",visibility="truth",raw_sensor={"value":42})]
    before=deepcopy(rows)
    result,lineage,final,truth=bridge.chat_rows(rows)
    assert [row["bucket"] for row in result] == ["training","validation"]
    assert [row["source"] for row in result] == ["s1","v"]
    assert final == [rows[2]] and truth == [rows[3]]
    assert [row["partition"] for row in lineage] == ["train","validation","final_test","truth"]
    assert [row["dataset_position"] for row in lineage] == [0,1,None,None]
    assert lineage[3]["raw_row"]["raw_sensor"] == {"value":42}
    assert rows == before
    with pytest.raises(bridge.service.LabError,match="not converted into Q&A"):
        bridge.chat_rows([{"time":0,"value":1}])


def test_chat_group_overlap_and_missing_lineage_fail():
    with pytest.raises(bridge.service.LabError,match="overlaps"):
        bridge.chat_rows([chat(),chat("s2","g1","validation")])
    missing=chat();del missing["group_id"]
    with pytest.raises(bridge.service.LabError,match="known source_id"):
        bridge.chat_rows([missing])
    with pytest.raises(bridge.service.LabError,match="explicit train"):
        bridge.chat_rows([chat(partition="uncategorized")])


def test_long_source_identity_uses_durable_hash_link_and_retains_full_value():
    identity="episode:"+"abc"*100
    result,lineage,_,_=bridge.chat_rows([chat(identity)])
    assert result[0]["source"] == "lab-source:sha256:"+hashlib.sha256(identity.encode()).hexdigest()
    assert len(result[0]["source"]) <= 200
    assert lineage[0]["source_id"] == identity


def test_only_explicit_partitioned_containers_can_promote_training_from_evaluation_artifacts():
    with pytest.raises(bridge.service.LabError, match="evaluation-bound"):
        bridge.chat_rows([chat()], artifact_visibility="evaluation")
    rows, _, _, _ = bridge.chat_rows([chat(), chat("v", "vg", "validation")],
                                      artifact_visibility="evaluation", partitioned_container=True)
    assert [row["bucket"] for row in rows] == ["training", "validation"]
    with pytest.raises(bridge.service.LabError, match="evaluation-bound"):
        bridge.chat_rows([chat(source_visibility="evaluation")], artifact_visibility="evaluation", partitioned_container=True)


def test_promotion_reuses_dataset_service_preserves_raw_manifest_and_excludes_final(monkeypatch,tmp_path,captured):
    rows=[chat(),chat("v","vg","validation"),chat("t","tg","final_test"),chat("ground","gg","validation",role="ground_truth")]
    path=tmp_path/"chats.jsonl";path.write_text("".join(json.dumps(row)+"\n" for row in rows))
    artifact=NS(id="source-artifact",project_id="p",size=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),name="chats.jsonl",media_type="application/x-ndjson",visibility="evaluation",role="supervised_dataset")
    run=NS(id="run",project_id="p",state="succeeded",spec_digest="pinned-spec",result={"artifacts":[{"id":artifact.id}]})
    monkeypatch.setattr(bridge.service,"get_run",lambda *args:run)
    monkeypatch.setattr(bridge.service,"get_artifact",lambda *args:artifact)
    monkeypatch.setattr(bridge.service,"materialize",lambda *args:path)
    created=[]
    monkeypatch.setattr(bridge.datasets_svc,"create_dataset",lambda db,p,name,rows,origin:created.append((p.id,name,deepcopy(rows),origin)) or {"id":"dataset","name":name})
    result=bridge.promote_chat_dataset(object(),NS(id="p"),run.id,artifact.id,"promoted")
    assert result["promoted_rows"] == 2
    assert len(created[0][2]) == 2
    assert created[0][3]["manifest_artifact_id"] == result["provenance_manifest"]["id"]
    by_role={row.role:(row,body) for row,body in captured}
    assert by_role["final_test"][0].visibility == "evaluation"
    assert by_role["truth"][0].visibility == "truth"
    assert by_role["bridge_manifest"][0].visibility == "truth"
    manifest=json.loads(by_role["bridge_manifest"][1])
    assert len(manifest["source_mapping"]) == 4
    assert manifest["source_mapping"][0]["raw_sha256"] == checksum(rows[0])


def test_promotion_rejects_artifact_not_linked_to_run(monkeypatch):
    monkeypatch.setattr(bridge.service,"get_run",lambda *args:NS(id="run",project_id="p",state="succeeded",result={"artifacts":[]}))
    with pytest.raises(bridge.service.LabError,match="not retained"):
        bridge.promote_chat_dataset(object(),NS(id="p"),"run","foreign-artifact","name")


def test_full_model_tar_is_regular_hash_verified_and_records_real_job_identity(monkeypatch,tmp_path,captured):
    root=tmp_path/"experiment";model=root/"model";model.mkdir(parents=True)
    (model/"config.json").write_text('{"model_type":"fixture"}')
    (model/"model.safetensors").write_bytes(b"existing published bytes, not executed")
    (model/"tokenizer.json").write_text('{}')
    job=NS(id="real-job",name="job",project_id="p",snapshot_id="snap",snapshot_name="snapshot")
    experiment=NS(id="real-exp",number=3,alias="ft-job-3",state="succeeded",config={"training_type":"full","base_model":"existing"},status={},job=job)
    monkeypatch.setattr(bridge.jobs_svc,"get_experiment",lambda *args:experiment)
    monkeypatch.setattr(bridge.jobs_svc,"ensure_experiment_artifacts",lambda exp:root)
    monkeypatch.setattr(bridge.jobs_svc,"_merged_cache_paths",lambda exp:(root/"merged",))
    result=bridge.model_input(object(),NS(id="p"),"job",3)
    archive=tmp_path/"captured.tar";archive.write_bytes(captured[0][1])
    with tarfile.open(archive) as tar:
        assert all(member.isfile() for member in tar.getmembers())
        assert tar.getnames() == ["config.json","model.safetensors","tokenizer.json"]
    manifest=json.loads(captured[1][1])
    assert manifest["job"]["id"] == "real-job"
    assert manifest["experiment"]["id"] == "real-exp"
    assert manifest["experiment"]["config_sha256"] == checksum(experiment.config)
    assert result["input"]["role"] == "pretrained_model"


def test_unmerged_lora_is_never_implicitly_merged(monkeypatch,tmp_path):
    root=tmp_path/"experiment";(root/"adapter").mkdir(parents=True)
    job=NS(project_id="p")
    experiment=NS(state="succeeded",status={},job=job)
    monkeypatch.setattr(bridge.jobs_svc,"get_experiment",lambda *args:experiment)
    monkeypatch.setattr(bridge.jobs_svc,"ensure_experiment_artifacts",lambda exp:root)
    monkeypatch.setattr(bridge.jobs_svc,"_merged_cache_paths",lambda exp:(root/"merged",))
    with pytest.raises(bridge.service.LabError,match="no implicit merge"):
        bridge.model_input(object(),NS(id="p"),"job",1)


def test_model_serializer_rejects_symlinks_and_size_before_hash(monkeypatch,tmp_path):
    root=tmp_path/"model";root.mkdir();outside=tmp_path/"outside";outside.write_text("private")
    (root/"linked").symlink_to(outside)
    with pytest.raises(bridge.service.LabError,match="symbolic"):
        bridge._model_files(root,bridge._Budget())
    (root/"linked").unlink();(root/"weights").write_bytes(b"1234")
    monkeypatch.setattr(bridge,"MAX_MODEL_BYTES",3)
    with pytest.raises(bridge.BridgeLimit):
        bridge._model_files(root,bridge._Budget())
