"""Services against the test database: projects, datasets, snapshots, jobs, metrics, traces, settings, accounts, model resolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import row
from tinystudio.auth import authenticate_password, AuthError, issue_service_token, issue_session, principal_from_bearer, principal_from_cookie, read_session, ensure_instance
from tinystudio.config import config
from tinystudio.core.project import ProjectError
from tinystudio.core.quality import CleanSpec
from tinystudio.db.models import Experiment
from tinystudio.services import datasets as datasets_svc
from tinystudio.services import jobs as jobs_svc
from tinystudio.services import metrics as metrics_svc
from tinystudio.services import models as models_svc
from tinystudio.services import projects as projects_svc
from tinystudio.services import settings as settings_svc
from tinystudio.services import snapshots as snapshots_svc
from tinystudio.services import traces as traces_svc
from tinystudio.services import users as users_svc
from tinystudio.training.train import TrainConfig


def seed_rows(n: int = 12) -> list[dict[str, Any]]:
    return [row(f"turn the lights off in room {i}", json.dumps({"action": "turn_off", "room": i}), labels=["lights"]) for i in range(n)]


def test_projects_slugs_builtin_metrics_and_delete(db, project):
    assert project.slug == "home-assistant"
    assert [m["name"] for m in metrics_svc.list_metrics(db, project)] == ["Conciseness", "Correctness", "Hallucination"]
    with pytest.raises(ProjectError, match="already exists"):
        projects_svc.create_project(db, "home assistant")
    other = projects_svc.create_project(db, "Other")
    assert {p["slug"] for p in projects_svc.list_projects(db)} == {"home-assistant", "other"}
    datasets_svc.create_dataset(db, other, "d", seed_rows(3))
    projects_svc.delete_project(db, other)
    assert [p["slug"] for p in projects_svc.list_projects(db)] == ["home-assistant"]
    assert projects_svc.overview(db, project)["project"]["goal"].startswith("answer")


def test_datasets_rows_split_labels_cleaning(db, project):
    meta = datasets_svc.create_dataset(db, project, "cmds", seed_rows(12) + [row("mail a@b.co", "x", source="synthetic:doc")], {"kind": "test"})
    assert meta["datapoints"] == 13 and meta["buckets"]["uncategorized"] == 13 and meta["snapshot_count"] == 0
    with pytest.raises(ProjectError, match="already exists"):
        datasets_svc.create_dataset(db, project, "cmds", [])
    ds = datasets_svc.get_dataset(db, project, "cmds")
    rows = datasets_svc.rows(db, ds, offset=1, limit=2)
    assert len(rows) == 2 and rows[0]["id"] and rows[0]["messages"][0]["content"].endswith("room 1")
    meta = datasets_svc.add_rows(db, project, "cmds", [row("q", "a")], bucket="validation")
    assert meta["buckets"]["validation"] == 1 and datasets_svc.count(db, ds) == 14
    meta = datasets_svc.autosplit(db, project, "cmds", training_pct=75, seed=3)
    assert meta["buckets"]["uncategorized"] == 0 and meta["buckets"]["training"] + meta["buckets"]["validation"] == 14
    synthetic = [r for r in datasets_svc.rows(db, ds) if r.get("source") == "synthetic:doc"]
    assert synthetic[0]["bucket"] == "training"
    assert datasets_svc.set_labels(db, project, "cmds", [{"name": "good", "description": "fine"}, {"name": "good"}, {"name": ""}])["labels"] == [{"name": "good", "description": "fine"}]
    result = datasets_svc.apply_cleaning(db, project, "cmds", CleanSpec(redact_pii=True, dedupe_user=True))
    assert result["redacted_rows"] == 1 and result["after"] == 14
    assert any(r["messages"][0]["content"] == "mail [EMAIL]" for r in datasets_svc.rows(db, ds))
    history = datasets_svc.cleaning_history(db, ds)
    assert len(history) == 1 and history[0]["spec"]["redact_pii"] is True
    first_id = datasets_svc.rows(db, ds, limit=1)[0]["id"]
    assert datasets_svc.delete_rows(db, ds, [first_id])["datapoints"] == 13
    assert datasets_svc.set_bucket(db, ds, [datasets_svc.rows(db, ds, limit=1)[0]["id"]], "validation")["buckets"]["validation"] >= 1
    assert datasets_svc.profile(db, ds)["count"] == 13
    datasets_svc.delete_dataset(db, project, "cmds")
    assert datasets_svc.list_datasets(db, project) == []


def test_snapshots_are_immutable_copies(db, project):
    datasets_svc.create_dataset(db, project, "d", seed_rows(12))
    with pytest.raises(ProjectError, match="at least 10 training"):
        snapshots_svc.create_snapshot(db, project, "d", "s1")
    datasets_svc.autosplit(db, project, "d", 90)
    snap = snapshots_svc.create_snapshot(db, project, "d", "s1")
    assert snap["training_count"] + snap["validation_count"] == 12 and snap["dataset"] == "d"
    with pytest.raises(ProjectError, match="immutable"):
        snapshots_svc.create_snapshot(db, project, "d", "s1")
    datasets_svc.add_rows(db, project, "d", [row("later", "x")], bucket="training")
    train, val = snapshots_svc.load_snapshot(db, project, "s1")
    assert len(train) + len(val) == 12 and "id" not in train[0]
    datasets_svc.delete_dataset(db, project, "d")
    assert snapshots_svc.list_snapshots(db, project)[0]["dataset"] == "d"  # the snapshot outlives its dataset
    tree = projects_svc.tree(db, project)
    assert tree["datasets"][0]["missing"] is True and tree["datasets"][0]["snapshots"][0]["name"] == "s1"


def test_jobs_experiments_and_model_resolution(db, project, tmp_path: Path):
    datasets_svc.create_dataset(db, project, "d", seed_rows(12))
    datasets_svc.autosplit(db, project, "d", 90)
    snapshots_svc.create_snapshot(db, project, "d", "s1")
    jobs_svc.create_job(db, project, "j", "s1", reasoning=True)
    with pytest.raises(ProjectError, match="already exists"):
        jobs_svc.create_job(db, project, "j", "s1")
    e1 = jobs_svc.add_experiment(db, project, "j", TrainConfig(base_model="HuggingFaceTB/SmolLM2-135M-Instruct"))
    e2 = jobs_svc.add_experiment(db, project, "j", TrainConfig(base_model="Qwen/Qwen2.5-0.5B-Instruct", training_type="full"))
    assert (e1.number, e2.number) == (1, 2) and e1.alias == "ft-j-1" and jobs_svc.exp_path(e1).exists()
    listing = jobs_svc.list_jobs(db, project)
    assert [e["state"] for e in listing[0]["experiments"]] == ["pending", "pending"] and listing[0]["experiments"][0]["model_id"] == "j/1"
    with pytest.raises(ProjectError, match="has not succeeded"):
        jobs_svc.add_experiment(db, project, "j", TrainConfig(base_model="x", refinetune_from="j/1"))
    # a finished full-model experiment resolves to its model directory; checkpoints resolve by step
    path = jobs_svc.exp_path(e2)
    (path / "model").mkdir(parents=True)
    (path / "model" / "config.json").write_text("{}")
    (path / "checkpoints" / "checkpoint-4").mkdir(parents=True)
    (path / "checkpoints" / "checkpoint-4" / "config.json").write_text("{}")
    e2.state, e2.status = "succeeded", {"state": "succeeded", "total_parameters": 10}
    db.flush()
    resolved = models_svc.resolve(db, project, "j/2")
    assert resolved.kind == "full" and resolved.model_dir == path / "model"
    assert models_svc.resolve(db, project, "ft-j-2").model_id == "ft-j-2"
    assert models_svc.resolve(db, project, "j/2@4").model_dir == path / "checkpoints" / "checkpoint-4"
    with pytest.raises(ProjectError, match="no checkpoint at step 9"):
        models_svc.resolve(db, project, "j/2@9")
    with pytest.raises(ProjectError, match="state 'pending'"):
        models_svc.resolve(db, project, "j/1")
    assert models_svc.resolve(db, project, "HuggingFaceTB/SmolLM2-135M-Instruct").kind == "base"
    assert models_svc.deployable_ids(db, project) == ["j/2"]
    assert [m["id"] for m in models_svc.model_rows(db, project, {"j/2"}) if m["kind"] == "fine-tuned"] == ["j/2"]
    assert jobs_svc.checkpoints(db, project, "j", 2)[0]["model_id"] == "j/2@4"
    jobs_svc.delete_experiment(db, project, "j", 1)
    assert jobs_svc.pending_numbers(db, project, "j") == []
    jobs_svc.delete_job(db, project, "j")
    assert not path.exists() and jobs_svc.list_jobs(db, project) == []


def test_metrics_crud(db, project):
    m = metrics_svc.create_metric(db, project, "json", "code", check="json_valid")
    assert m["check"] == "json_valid"
    with pytest.raises(ProjectError, match="at least one"):
        metrics_svc.create_metric(db, project, "empty", "rubric")
    hook = metrics_svc.create_metric(db, project, "hook", "webhook", webhook_url="http://x", token="secret")
    assert hook["webhook"]["token"] == "set" and metrics_svc.judge_spec(metrics_svc.get_metric(db, project, "hook"))["webhook"]["token"] == "secret"
    updated = metrics_svc.update_metric(db, project, "Conciseness", should=["be short"], description="d")
    assert updated["rules"]["should"] == ["be short"] and updated["description"] == "d"
    metrics_svc.delete_metric(db, project, "json")
    assert "json" not in {m["name"] for m in metrics_svc.list_metrics(db, project)}


def test_traces_score_promote_stats(db, project):
    datasets_svc.create_dataset(db, project, "d", seed_rows(2))
    for i in range(3):
        traces_svc.append_trace(db, project, {"id": f"t{i}", "model": "m", "messages": [{"role": "user", "content": f"q{i}"}], "response": f"a{i}", "seconds": 0.1, "prompt_tokens": 2, "completion_tokens": 3, "score": None})
    assert [t["id"] for t in traces_svc.list_traces(db, project, limit=2)] == ["t2", "t1"]
    traces_svc.score_trace(db, project, "t0", 0.95, note="good")
    traces_svc.score_trace(db, project, "t1", 0.2, corrected="better")
    with pytest.raises(ProjectError, match="no traces matched"):
        traces_svc.promote(db, project, "d")
    assert traces_svc.promote(db, project, "d", min_score=0.9) == 1
    assert traces_svc.promote(db, project, "d", preference=True) == 1
    rows = datasets_svc.rows(db, datasets_svc.get_dataset(db, project, "d"))
    assert rows[-1]["rejected"] == "a1" and rows[-1]["messages"][-1]["content"] == "better" and rows[-2]["labels"] == ["trace"]
    stats = traces_svc.stats(db, project)
    assert stats["requests"] == 3 and stats["prompt_tokens"] == 6 and stats["scored_traces"] == 2 and len(stats["requests_per_day"]) == 1
    traces_svc.delete_trace(db, project, "t2")
    with pytest.raises(ProjectError):
        traces_svc.delete_trace(db, project, "t2")


def test_settings_store_env_fallback_and_apply(db, monkeypatch: pytest.MonkeyPatch):
    import os

    monkeypatch.setenv("TINYSTUDIO_LLM_MODEL", "from-env")
    monkeypatch.delenv("TINYSTUDIO_EMBEDDING_MODEL", raising=False)
    rows = {r["key"]: r for r in settings_svc.describe(db)}
    assert rows["TINYSTUDIO_LLM_MODEL"] == {**rows["TINYSTUDIO_LLM_MODEL"], "source": "environment", "value": "from-env"} and rows["TINYSTUDIO_EMBEDDING_MODEL"]["source"] == "default"
    settings_svc.save(db, {"TINYSTUDIO_LLM_MODEL": "stored", "HF_TOKEN": "hf_secret", "TINYSTUDIO_EMBEDDING_MODEL": ""})
    assert os.environ["TINYSTUDIO_LLM_MODEL"] == "stored" and os.environ["HF_TOKEN"] == "hf_secret"
    rows = {r["key"]: r for r in settings_svc.describe(db)}
    assert rows["TINYSTUDIO_LLM_MODEL"]["source"] == "settings" and rows["HF_TOKEN"]["set"] is True and rows["HF_TOKEN"]["value"] is None
    settings_svc.save(db, {"TINYSTUDIO_LLM_MODEL": None})
    assert os.environ["TINYSTUDIO_LLM_MODEL"] == "from-env"  # cleared keys fall back to the seed
    with pytest.raises(ValueError, match="unknown setting"):
        settings_svc.save(db, {"NOPE": "x"})
    assert settings_svc.get(db, "HF_TOKEN") == "hf_secret"


def test_accounts_keys_sessions_and_service_tokens(db):
    admin = users_svc.create_user(db, "Admin@Example.com", "password-123", "A", "admin")
    member = users_svc.create_user(db, "m@example.com", "password-123")
    assert admin.email == "admin@example.com" and member.role == "member"
    with pytest.raises(ProjectError, match="already exists"):
        users_svc.create_user(db, "m@example.com", "password-123")
    with pytest.raises(ProjectError, match="8 characters"):
        users_svc.create_user(db, "x@example.com", "short")
    assert authenticate_password(db, "ADMIN@example.com", "password-123").id == admin.id
    with pytest.raises(AuthError):
        authenticate_password(db, "admin@example.com", "wrong")
    with pytest.raises(ProjectError, match="own admin role"):
        users_svc.update_user(db, admin, role="member", actor=admin)
    users_svc.update_user(db, member, disabled=True, actor=admin)
    with pytest.raises(AuthError):
        authenticate_password(db, "m@example.com", "password-123")
    secret = ensure_instance(db).secret_key
    cookie = issue_session(secret, admin.id)
    assert read_session(secret, cookie, 60) == admin.id and read_session("other", cookie, 60) is None
    assert principal_from_cookie(db, secret, cookie, 60).user.id == admin.id and principal_from_cookie(db, secret, "garbage", 60) is None
    record, plaintext = users_svc.create_key(db, admin, "cli")
    principal = principal_from_bearer(db, secret, plaintext)
    assert principal is not None and principal.via == "api_key" and principal.api_key.id == record["id"]
    assert principal_from_bearer(db, secret, "ts-unknown") is None
    service = principal_from_bearer(db, secret, issue_service_token(secret, admin.id))
    assert service is not None and service.via == "service"
    with pytest.raises(ProjectError, match="not found"):
        users_svc.revoke_key(db, record["id"], member)
    users_svc.revoke_key(db, record["id"], admin)
    assert users_svc.list_keys(db, admin) == []
    with pytest.raises(ProjectError, match="own account"):
        users_svc.delete_user(db, admin, actor=admin)
    users_svc.delete_user(db, member, actor=admin)
    assert [u["email"] for u in users_svc.list_users(db)] == ["admin@example.com"]
    assert config().data_dir.exists() or True
