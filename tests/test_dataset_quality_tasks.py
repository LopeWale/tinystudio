"""Quality HTTP contract and durable worker results, including profiles above the task preview limit."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from rq import Queue, SimpleWorker
from sqlalchemy import event

from tests.conftest import row
from tinystudio import worker
from tinystudio.api.app import create_app
from tinystudio.api.routes import data as data_routes
from tinystudio.services import datasets as datasets_svc
from tinystudio.services import tasks as tasks_svc
from tinystudio.services import users as users_svc


@pytest.fixture
def quality_client(db, monkeypatch):
    user = users_svc.create_user(db, "quality@example.com", "password-123", role="admin")
    _, key = users_svc.create_key(db, user, "quality")
    db.commit()
    real = data_routes.config()
    monkeypatch.setattr(data_routes, "config", lambda: SimpleNamespace(**{**real.__dict__, "sync_row_limit": 10}))
    with TestClient(create_app(run_migrations=False), headers={"Authorization": f"Bearer {key}"}) as client:
        yield client


def run_burst():
    conn = worker.redis_conn()
    SimpleWorker([Queue(worker.QUEUE_NAME, connection=conn)], connection=conn).work(burst=True)


@pytest.mark.parametrize("count", [0, 9, 10])
def test_quality_at_or_below_limit_remains_inline(db, project, quality_client, count):
    datasets_svc.create_dataset(db, project, "small", [row(f"question {i}", '{"ok": true}') for i in range(count)])
    db.commit()
    response = quality_client.get(f"/api/p/{project.slug}/datasets/small/quality")
    assert response.status_code == 200
    profile = response.json()
    assert profile["count"] == count
    assert profile["format"]["answers_json"] == count
    assert profile["buckets"] == {"uncategorized": count, "training": 0, "validation": 0}
    assert "duplicates" in profile and "generated" in profile and "queued" not in profile
    assert tasks_svc.list_tasks(db, project) == []


def test_large_quality_queues_without_profiling_and_persists_complete_result(db, project, quality_client, redis_available, monkeypatch):
    # Duplicate examples are intentionally long enough to exceed the generic task preview limit.
    questions = [f"{i}" + " x" * 10_000 for i in range(6)]
    datasets_svc.create_dataset(db, project, "big", [row(q, '{"ok": true}') for q in questions for _ in range(2)])
    db.commit()
    with monkeypatch.context() as request_patch:
        def forbidden_profile(*args, **kwargs):
            pytest.fail("the request must not compute a queued profile")

        request_patch.setattr(datasets_svc, "profile", forbidden_profile)
        response = quality_client.get(f"/api/p/{project.slug}/datasets/big/quality")
    assert response.status_code == 202
    queued = response.json()
    assert queued["queued"] is True
    task = queued["task"]
    assert task["kind"] == "dataset.profile" and task["args"] == {"dataset": "big"}
    assert task["project_id"] == project.id and task["state"] == "queued" and task["result"] is None

    run_burst()
    # Removing the Redis job establishes that GET reads the durable database result.
    worker.redis_conn().flushdb()
    finished = quality_client.get(f"/api/tasks/{task['id']}")
    assert finished.status_code == 200
    completed = finished.json()
    assert completed["state"] == "succeeded", completed["error"]
    assert completed["started"] and completed["finished"]
    result = completed["result"]
    assert len(json.dumps(result)) > 200_000
    assert "truncated" not in result
    assert result["count"] == 12 and result["format"]["answers_json"] == 12
    assert result["duplicates"]["exact_user_groups"] == 6
    assert result["duplicates"]["exact_user_rows"] == 6
    assert result["duplicates"]["examples"] == [[q, q] for q in questions[:5]]
    assert result["user_chars"]["summary"]["min"] == 20_001
    assert result["user_chars"]["summary"]["max"] == 20_001
    assert worker._jsonable(result)["truncated"] is True  # other task kinds retain their preview cap

    statements = []

    def record_task_select(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "FROM tasks" in statement:
            statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record_task_select)
    try:
        lightweight = quality_client.get(f"/api/tasks?include_result=false&project={project.slug}")
    finally:
        event.remove(engine, "before_cursor_execute", record_task_select)
    assert lightweight.status_code == 200
    assert len(lightweight.json()) == 1
    assert lightweight.json()[0] == {**completed, "result": None}
    assert statements and all("tasks.result" not in statement for statement in statements)
    # Existing consumers retain complete results unless they explicitly request a lightweight list.
    full_list = quality_client.get(f"/api/tasks?project={project.slug}")
    assert full_list.status_code == 200 and full_list.json() == [completed]
    assert quality_client.get(f"/api/tasks/{task['id']}").json()["result"] == result


def test_quality_worker_failure_is_visible_through_task_get(db, project, quality_client, redis_available):
    datasets_svc.create_dataset(db, project, "doomed", [row(f"question {i}", "answer") for i in range(11)])
    db.commit()
    response = quality_client.get(f"/api/p/{project.slug}/datasets/doomed/quality")
    assert response.status_code == 202
    task_id = response.json()["task"]["id"]
    # A real dataset disappearing before execution must follow the normal failed-task contract.
    datasets_svc.delete_dataset(db, project, "doomed")
    db.commit()
    run_burst()
    task = quality_client.get(f"/api/tasks/{task_id}").json()
    assert task["state"] == "failed" and task["result"] is None
    assert "doomed" in task["error"] and "ProjectError" in task["error"]
    assert task["started"] and task["finished"] and any("failed" in line for line in task["log_tail"])


def test_quality_validates_auth_project_and_dataset_before_enqueue(db, project, quality_client, redis_available):
    datasets_svc.create_dataset(db, project, "big", [row(f"question {i}", "answer") for i in range(11)])
    db.commit()
    path = f"/api/p/{project.slug}/datasets/big/quality"
    with TestClient(create_app(run_migrations=False)) as anonymous:
        assert anonymous.get(path).status_code == 401
    assert quality_client.get("/api/p/missing-project/datasets/big/quality").status_code == 404
    assert quality_client.get(f"/api/p/{project.slug}/datasets/missing/quality").status_code == 400
    assert worker.queue().count == 0
    assert tasks_svc.list_tasks(db, project) == []
