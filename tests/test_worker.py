"""The queue: enqueue, execute with an in-process worker, task rows, logs, stop. Needs Redis (database 9)."""

from __future__ import annotations

from pathlib import Path

import pytest
from rq import Queue, SimpleWorker

from tests.conftest import needs_tokenizer, tiny_bert_dir
from tinystudio import worker
from tinystudio.config import config
from tinystudio.core.project import ProjectError
from tinystudio.services import repositories as repo
from tinystudio.services import tasks as tasks_svc


def run_burst() -> None:
    SimpleWorker([Queue(worker.QUEUE_NAME, connection=worker.redis_conn())], connection=worker.redis_conn()).work(burst=True)


def test_unknown_kind_is_rejected(db, project, redis_available):
    with pytest.raises(ProjectError, match="unknown task kind"):
        worker.enqueue(db, "nope", {}, project)


def test_task_fails_cleanly_when_the_handler_raises(db, project, redis_available):
    task = worker.enqueue(db, "repo.add", {"repository_id": "missing", "paths": [], "urls": []}, project)
    db.commit()
    assert task["state"] == "queued"
    run_burst()
    db.expire_all()
    row = tasks_svc.get_task(db, task["id"])
    assert row.state == "failed" and "repository does not exist" in (row.error or "") and row.started is not None and row.finished is not None
    assert config().task_log(row.id).exists() and any("failed" in line for line in row.log_tail)


@needs_tokenizer
def test_repository_indexing_through_the_queue(db, project, redis_available, tmp_path: Path):
    model_dir = tiny_bert_dir(tmp_path)
    repo.create_repository(db, project, "docs", embedding_model=str(model_dir))
    repository_id = repo.get_repository(db, project, "docs").id
    doc = tmp_path / "a.txt"
    doc.write_text("Lights can be dimmed to a percentage. " * 20)
    task = worker.enqueue(db, "repo.add", {"repository_id": repository_id, "repository": "docs", "paths": [str(doc)], "urls": []}, project)
    db.commit()
    seen: list[str] = []
    run_burst()
    db.expire_all()
    row = tasks_svc.get_task(db, task["id"])
    assert row.state == "succeeded", row.error
    assert row.result and row.result["chunk_count"] >= 1 and any("chunks embedded" in line for line in row.log_tail)
    assert tasks_svc.list_tasks(db, project)[0]["id"] == task["id"]
    assert repo.get_repository(db, project, "docs").documents[0].chunks == row.result["chunk_count"]


def test_stop_cancels_a_queued_task(db, project, redis_available):
    task = worker.enqueue(db, "hub.download", {"repo_id": "nobody/nothing"}, project)
    db.commit()
    stopped = worker.stop(db, task["id"])
    assert stopped["state"] == "stopped"
    run_burst()  # the cancelled job must not run
    db.expire_all()
    assert tasks_svc.get_task(db, task["id"]).state == "stopped"
    assert worker.recover(db) == 0
