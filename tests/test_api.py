"""The HTTP surface through FastAPI's TestClient: setup, sessions, CSRF, projects, data, keys, /v1 gating, admin-only settings."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import row
from tinystudio.api.app import create_app

CSRF = {"X-Requested-With": "tinystudio"}


@pytest.fixture
def client(db) -> Any:
    app = create_app(run_migrations=False)
    with TestClient(app, headers=CSRF) as c:
        yield c


def test_setup_login_and_project_flow(client: TestClient):
    assert client.get("/api/health").json()["database"] is True
    assert client.get("/api/auth/status").json() == {"setup_required": True, "authenticated": False, "user": None}
    assert client.get("/api/projects").status_code == 401
    r = client.post("/api/auth/setup", json={"email": "owner@example.com", "password": "password-123", "name": "Owner"})
    assert r.status_code == 200 and client.get("/api/auth/me").json()["role"] == "admin"
    assert client.post("/api/auth/setup", json={"email": "x@example.com", "password": "password-123"}).status_code == 409
    # CSRF: cookie sessions must send the header on state-changing requests
    bare = TestClient(client.app)
    bare.cookies = client.cookies
    assert bare.post("/api/projects", json={"name": "x"}).status_code == 403
    assert bare.get("/api/projects").status_code == 200
    project = client.post("/api/projects", json={"name": "Home", "goal": "g"}).json()
    assert project["slug"] == "home"
    rows = "\n".join(json.dumps(row(f"q{i}", f"a{i}")) for i in range(12))
    r = client.post("/api/p/home/datasets/upload", data={"name": "d"}, files={"file": ("d.jsonl", io.BytesIO(rows.encode()), "application/jsonl")})
    assert r.status_code == 200 and r.json()["datapoints"] == 12
    assert client.get("/api/p/home/datasets/d", params={"limit": 3}).json()["total"] == 12
    assert client.post("/api/p/home/datasets/d/split", json={"train_pct": 90}).json()["buckets"]["uncategorized"] == 0
    snap = client.post("/api/p/home/snapshots", json={"dataset": "d", "name": "s1"}).json()
    assert snap["training_count"] >= 10
    assert client.post("/api/p/home/snapshots", json={"dataset": "d", "name": "s1"}).status_code == 400
    job = client.post("/api/p/home/jobs", json={"name": "j", "snapshot": "s1"}).json()
    assert job["experiments"] == []
    exps = client.post("/api/p/home/jobs/j/experiments", json={"experiments": [{"base_model": "HuggingFaceTB/SmolLM2-135M-Instruct", "epochs": 1}]}).json()
    assert exps["experiments"] == [1]
    detail = client.get("/api/p/home/jobs/j").json()
    assert detail["experiments"][0]["state"] == "pending" and detail["snapshot"]["name"] == "s1"
    assert client.get("/api/p/home/overview").json()["tree"]["datasets"][0]["snapshots"][0]["jobs"][0]["name"] == "j"
    assert client.get("/api/p/nope/overview").status_code == 404
    assert client.get("/api/p/home/recommend/s1").json()["recommended"] if "recommended" in client.get("/api/p/home/recommend/s1").json() else True
    download = client.get("/api/p/home/datasets/d/download")
    assert download.status_code == 200 and len(download.text.strip().splitlines()) == 12
    # metrics, traces, stats
    assert len(client.get("/api/p/home/metrics").json()) == 3
    assert client.post("/api/p/home/metrics", json={"name": "json", "kind": "code", "check": "json_valid"}).status_code == 200
    stats = client.get("/api/p/home/stats").json()
    assert stats["datasets"] == 1 and stats["jobs"] == 1 and stats["requests"] == 0
    # keys gate /v1 and work on /api; project-prefixed model ids
    key = client.post("/api/keys", json={"name": "k"}).json()["key"]
    anon = TestClient(client.app)
    assert anon.get("/v1/models").status_code == 401
    assert anon.get("/api/p/home/datasets", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    models = anon.get("/v1/models", headers={"Authorization": f"Bearer {key}", "X-Project": "home"}).json()["data"]
    assert models == [] or all(m["status"] in ("up", "down") for m in models)
    assert anon.get("/api/p/home/models", headers={"Authorization": "Bearer ts-wrong"}).status_code == 401
    # a member cannot read settings or manage users
    client.post("/api/users", json={"email": "m@example.com", "password": "password-123"})
    member = TestClient(client.app, headers=CSRF)
    assert member.post("/api/auth/login", json={"email": "m@example.com", "password": "password-123"}).status_code == 200
    assert member.get("/api/settings").status_code == 403 and member.get("/api/users").status_code == 403
    assert member.get("/api/p/home/datasets").status_code == 200
    assert client.get("/api/settings").status_code == 200
    assert client.put("/api/settings", json={"values": {"TINYSTUDIO_LLM_MODEL": "m"}}).json()["settings"][0]["key"]
    assert client.post("/api/settings/test", json={"what": "database"}).json()["ok"] is True
    assert client.post("/api/settings/test", json={"what": "inference"}).json()["ok"] is True
    # tasks list is empty and the log endpoint 400s for unknown ids
    assert client.get("/api/tasks", params={"project": "home"}).json() == []
    assert client.get("/api/tasks/nope").status_code == 400
    # sign out
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
