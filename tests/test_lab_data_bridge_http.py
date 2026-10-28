"""Registered authenticated HTTP + real RQ + existing dataset/snapshot readers."""
import json

from rq import Queue, SimpleWorker

from tests.test_lab_b_workflow import database, integrated
from tinystudio.lab import dispatch, service as svc


def test_registered_promotion_is_durable_project_scoped_and_legacy_readable(integrated):
    client, prefix, other_prefix, _, connection, root, factory = integrated
    project_slug = prefix.split("/")[3]
    # Software qualification data; no training, base-model download or inference.
    rows = [{"source_record_id": f"fixture:{i}", "episode_id": f"episode:{i}",
             "partition": "train" if i < 10 else "validation" if i < 12 else "final_test",
             "messages": [{"role": "user", "content": f"Recorded observation {i}"},
                          {"role": "assistant", "content": "-1.25 V"}]} for i in range(13)]
    path = root / "recorded-chats.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    from tinystudio.services.projects import get_project
    with factory.begin() as db:
        project = get_project(db, project_slug)
        artifact = svc.store_artifact(db, project.id, path, path.name, "application/x-ndjson", "supervised_dataset", visibility="evaluation")
        definition = svc.save_definition(db, project.id, {"name": "Recorded evidence", "engine": "synthetic.signal",
            "parameters": {"samples": 4}, "execution": {"profile": "trusted_native"}})
        run = svc.submit(db, project.id, definition["id"], 1, "local", "http-recorded")
        run.state, run.result = "succeeded", {"artifacts": [svc.artifact_dict(artifact)]}
        run_id, artifact_id = run.id, artifact.id
    body = {"run_id": run_id, "artifact_id": artifact_id, "name": "prepared-http",
            "snapshot_name": "prepared-http-v1", "idempotency_key": "http-promotion"}
    response = client.post(prefix + "/datasets/from-run", json=body)
    assert response.status_code == 202, response.text
    operation_id = response.json()["id"]
    assert response.json()["state"] == "queued"
    assert client.get(other_prefix + "/operations/" + operation_id).status_code == 404
    assert client.post(other_prefix + "/datasets/from-run", json=body).status_code == 404
    assert client.get(prefix + "/operations").json()[0]["resource"] == {"run_id": run_id, "artifact_id": artifact_id}
    assert dispatch.tick()["operations_dispatched"] >= 1
    SimpleWorker([Queue("tinystudio", connection=connection)], connection=connection).work(burst=True)
    completed = client.get(prefix + "/operations/" + operation_id).json()
    assert completed["state"] == "succeeded", completed.get("error")
    assert completed["result"]["snapshot"]["training_count"] == 10
    assert completed["result"]["excluded_final_test_rows"] == 1
    assert client.post(prefix + "/datasets/from-run", json=body).json()["id"] == operation_id
    datasets = client.get(f"/api/p/{project_slug}/datasets").json()
    assert any(row["name"] == "prepared-http" for row in datasets)
    snapshots = client.get(f"/api/p/{project_slug}/snapshots").json()
    assert any(row["name"] == "prepared-http-v1" and row["training_count"] == 10 for row in snapshots)
    assert client.get(f"/api/p/{project_slug}/jobs").json() == []
    assert client.post(prefix + "/inputs/from-snapshot", json={"snapshot": "prepared-http-v1"}).status_code == 404
