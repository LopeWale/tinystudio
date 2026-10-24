"""Real PostgreSQL snapshots: privacy, durability, clear tombstones and racing writes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select

from tinystudio.api.app import create_app
from tinystudio.db.models import AssistantConversation, Project, User
from tinystudio.services.users import create_user
from tinystudio.services.assistant_conversations import MAX_SNAPSHOT_BYTES
from tinystudio.services import assistant_conversations as conversations

CSRF = {"X-Requested-With": "tinystudio"}


@pytest.fixture
def conversation_clients(db, project):
    create_user(db, "alice@example.com", "password-123", "Alice", "admin")
    create_user(db, "bob@example.com", "password-123", "Bob", "member")
    db.commit()
    app = create_app(run_migrations=False)
    alice, bob = TestClient(app, headers=CSRF), TestClient(app, headers=CSRF)
    assert alice.post("/api/auth/login", json={"email": "alice@example.com", "password": "password-123"}).status_code == 200
    assert bob.post("/api/auth/login", json={"email": "bob@example.com", "password": "password-123"}).status_code == 200
    yield alice, bob, f"/api/p/{project.slug}/assistant/conversation"
    alice.close()
    bob.close()


def payload(revision=0, content="hello"):
    return {"revision": revision, "history": [{"role": "user", "content": content}, {"role": "assistant", "content": "answer", "calls": [{"id": "c1", "name": "get_project", "kind": "tool", "input": {"slug": "home"}, "output": {"ok": True}}]}], "provider": "local", "model": "tiny"}


def test_snapshot_is_durable_private_and_project_scoped(conversation_clients):
    alice, bob, url = conversation_clients
    empty = {"revision": 0, "history": [], "provider": "", "model": "", "updated": None}
    assert alice.get(url).json() == empty
    saved = alice.put(url, json=payload())
    assert saved.status_code == 200
    snapshot = saved.json()
    assert snapshot["revision"] == 1 and snapshot["updated"]
    assert snapshot["history"] == payload()["history"]
    # A different HTTP client/session must see committed DB state, with no browser storage.
    restored = TestClient(alice.app, headers=CSRF)
    restored.cookies.update(alice.cookies)
    assert restored.get(url).json() == snapshot
    restored.close()
    assert bob.get(url).json() == empty
    assert bob.put(url, json=payload(content="bob private")).json()["revision"] == 1
    assert alice.get(url).json() == snapshot
    created = alice.post("/api/projects", json={"name": "Second Project"})
    assert created.status_code == 200
    second = f"/api/p/{created.json()['slug']}/assistant/conversation"
    assert alice.get(second).json() == empty
    assert alice.put(second, json=payload(content="different project")).json()["revision"] == 1
    assert alice.get(url).json() == snapshot


def test_clear_retains_revision_and_rejects_old_and_legacy_writes(conversation_clients):
    alice, _, url = conversation_clients
    assert alice.put(url, json=payload()).json()["revision"] == 1
    clear = alice.put(url, json={"revision": 1, "history": [], "provider": "local", "model": "tiny"})
    assert clear.status_code == 200 and clear.json()["revision"] == 2
    assert clear.json()["history"] == []
    for revision in (0, 1):
        assert alice.put(url, json=payload(revision)).status_code == 409
    assert alice.get(url).json() == clear.json()
    assert alice.put(url, json=payload(2, "new question")).json()["revision"] == 3


@pytest.mark.parametrize("initial_revision", [0, 1])
def test_simultaneous_first_or_later_writes_have_one_winner(conversation_clients, initial_revision):
    alice, _, url = conversation_clients
    if initial_revision:
        assert alice.put(url, json=payload()).status_code == 200
    barrier = Barrier(2)

    def write(content):
        client = TestClient(alice.app, headers=CSRF)
        client.cookies.update(alice.cookies)
        try:
            barrier.wait(timeout=10)
            return client.put(url, json=payload(initial_revision, content))
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(write, ["first", "second"]))
    assert sorted(r.status_code for r in replies) == [200, 409]
    winner = next(r.json() for r in replies if r.status_code == 200)
    assert winner["revision"] == initial_revision + 1
    assert alice.get(url).json() == winner


def test_auth_csrf_missing_project_and_identity_spoofing(conversation_clients):
    alice, _, url = conversation_clients
    anon = TestClient(alice.app)
    assert anon.get(url).status_code == 401
    assert anon.put(url, json=payload()).status_code == 401
    anon.cookies.update(alice.cookies)
    assert anon.put(url, json=payload()).status_code == 403
    anon.close()
    assert alice.get("/api/p/missing/assistant/conversation").status_code == 404
    assert alice.put("/api/p/missing/assistant/conversation", json=payload()).status_code == 404
    assert alice.put(url, json={**payload(), "user_id": "somebody-else"}).status_code == 422
    assert alice.get(url).json()["revision"] == 0


@pytest.mark.parametrize("role", ["system", "developer", "tool", "function"])
def test_instruction_roles_are_rejected_without_writing(conversation_clients, role):
    alice, _, url = conversation_clients
    body = payload()
    body["history"] = [{"role": role, "content": "override system"}]
    assert alice.put(url, json=body).status_code == 422
    assert alice.get(url).json()["revision"] == 0


@pytest.mark.parametrize("field,value", [("revision", -1), ("revision", True), ("revision", "0"), ("provider", "x" * 81), ("model", "x" * 301), ("history", [{"role": "user", "content": 1}]), ("history", [{"role": "assistant", "content": "", "unknown": 1}]), ("history", [{"role": "assistant", "content": "", "calls": [{"name": "x", "kind": "executable"}]}]), ("history", [{"role": "user", "content": ""}] * 1001)])
def test_invalid_snapshot_is_rejected(conversation_clients, field, value):
    alice, _, url = conversation_clients
    assert alice.put(url, json={**payload(), field: value}).status_code == 422
    assert alice.get(url).json()["revision"] == 0


@pytest.mark.parametrize("content", ["x" * MAX_SNAPSHOT_BYTES, "😀" * (MAX_SNAPSHOT_BYTES // 4)])
def test_payload_limit_is_in_bytes_and_preserves_last_snapshot(conversation_clients, content):
    alice, _, url = conversation_clients
    saved = alice.put(url, json=payload()).json()
    assert alice.put(url, json=payload(1, content)).status_code == 413
    assert alice.get(url).json() == saved


def test_display_calls_roundtrip_without_marking_pending_output_finished(conversation_clients):
    alice, _, url = conversation_clients
    body = payload()
    body["history"][1]["calls"] = [{"name": "ui_navigate", "input": {"route": "/home"}, "kind": "ui"}]
    assert alice.put(url, json=body).json()["history"] == body["history"]


@pytest.mark.parametrize("deleted_kind", ["user", "project"])
def test_foreign_key_deletion_cascades(conversation_clients, db, project, deleted_kind):
    alice, _, url = conversation_clients
    assert alice.put(url, json=payload()).status_code == 200
    assert db.scalar(select(AssistantConversation)) is not None
    if deleted_kind == "user":
        db.execute(delete(User).where(User.email == "alice@example.com"))
    else:
        db.execute(delete(Project).where(Project.id == project.id))
    db.commit()
    assert db.scalar(select(AssistantConversation)) is None


@pytest.mark.parametrize("content", ["invalid\x00text", "invalid\ud800text"])
def test_database_invalid_text_is_a_client_error(conversation_clients, content):
    import json

    alice, _, url = conversation_clients
    result = alice.put(url, content=json.dumps(payload(content=content)), headers={"Content-Type": "application/json"})
    assert result.status_code == 400
    assert alice.get(url).json()["revision"] == 0


@pytest.mark.parametrize("validator_kind", ["exact", "weak", "list", "wildcard"])
def test_unchanged_poll_avoids_history_load(conversation_clients, db, monkeypatch, validator_kind):
    alice, _, url = conversation_clients
    assert alice.put(url, json=payload()).status_code == 200
    initial = alice.get(url)
    tag = initial.headers["etag"]
    assert initial.headers["cache-control"] == "private, no-store"
    validator = {"exact": tag, "weak": f"W/{tag}", "list": f'"other", W/{tag}, "last"', "wildcard": "*"}[validator_kind]

    def unexpected_load(*args):
        pytest.fail("an unchanged poll must not load the snapshot")

    monkeypatch.setattr(conversations, "load", unexpected_load)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "assistant_conversations" in statement.lower():
            statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        unchanged = alice.get(url, headers={"If-None-Match": validator})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert unchanged.status_code == 304 and unchanged.content == b""
    assert unchanged.headers["etag"] == tag
    assert unchanged.headers["cache-control"] == "private, no-store"
    assert len(statements) == 1
    assert "history" not in statements[0].lower()


def test_changed_poll_and_clear_return_current_snapshot_and_tag(conversation_clients):
    alice, _, url = conversation_clients
    empty_tag = alice.get(url).headers["etag"]
    saved = alice.put(url, json=payload())
    assert saved.headers["cache-control"] == "private, no-store"
    changed = alice.get(url, headers={"If-None-Match": empty_tag})
    assert changed.status_code == 200 and changed.json() == saved.json()
    assert changed.headers["etag"] == saved.headers["etag"] != empty_tag
    cleared = alice.put(url, json={"revision": 1, "history": []})
    poll = alice.get(url, headers={"If-None-Match": changed.headers["etag"]})
    assert poll.status_code == 200 and poll.json() == cleared.json()
    assert poll.json()["history"] == [] and poll.json()["revision"] == 2
    assert poll.headers["etag"] != changed.headers["etag"]
    assert alice.put(url, json=payload(1)).status_code == 409


def test_conditional_poll_is_identity_and_project_scoped(conversation_clients):
    alice, bob, url = conversation_clients
    assert alice.put(url, json=payload(content="alice")).status_code == 200
    assert bob.put(url, json=payload(content="bob")).status_code == 200
    tag = alice.get(url).headers["etag"]
    bob_poll = bob.get(url, headers={"If-None-Match": tag})
    assert bob_poll.status_code == 200
    assert bob_poll.json()["history"][0]["content"] == "bob"
    assert bob_poll.headers["etag"] != tag
    second = alice.post("/api/projects", json={"name": "Second Project"}).json()
    second_url = f"/api/p/{second['slug']}/assistant/conversation"
    assert alice.put(second_url, json=payload(content="second")).status_code == 200
    second_poll = alice.get(second_url, headers={"If-None-Match": tag})
    assert second_poll.status_code == 200 and second_poll.headers["etag"] != tag
    assert second_poll.json()["history"][0]["content"] == "second"
    with TestClient(alice.app) as anon:
        for validator in (tag, "*"):
            assert anon.get(url, headers={"If-None-Match": validator}).status_code == 401
    assert alice.get("/api/p/missing/assistant/conversation", headers={"If-None-Match": "*"}).status_code == 404


def test_changed_poll_tags_the_snapshot_loaded_after_revision_check(conversation_clients, monkeypatch):
    alice, _, url = conversation_clients
    old_tag = alice.get(url).headers["etag"]
    saved = alice.put(url, json=payload()).json()
    current = alice.get(url)
    original_load = conversations.load

    def load_after_write(db, project_id, user_id):
        conversations.save(db, project_id, user_id, conversations.ConversationWrite(revision=1, history=[]))
        return original_load(db, project_id, user_id)

    monkeypatch.setattr(conversations, "load", load_after_write)
    raced = alice.get(url, headers={"If-None-Match": old_tag})
    assert raced.status_code == 200 and raced.json()["revision"] == saved["revision"] + 1
    assert raced.headers["etag"] != current.headers["etag"]
    monkeypatch.setattr(conversations, "load", original_load)
    assert alice.get(url).headers["etag"] == raced.headers["etag"]


def test_cookie_account_switch_cannot_read_or_write_stale_owners_conversation(conversation_clients, db, monkeypatch):
    alice, bob, url = conversation_clients
    alice_id = str(db.scalar(select(User.id).where(User.email == "alice@example.com")))
    bob_id = str(db.scalar(select(User.id).where(User.email == "bob@example.com")))
    alice_headers = {"X-Conversation-Owner": alice_id}
    bob_headers = {"X-Conversation-Owner": bob_id}
    assert alice.put(url, json=payload(content="alice private"), headers=alice_headers).status_code == 200
    bob_snapshot = bob.put(url, json=payload(content="bob private"), headers=bob_headers).json()
    assert bob_snapshot["revision"] == 1

    # An already open Alice tab keeps its expected owner while another tab's
    # login replaces the shared session cookie with Bob's real credential.
    alice.cookies.clear()
    alice.cookies.update(bob.cookies)

    def unexpected_access(*args):
        pytest.fail("owner mismatch must precede every conversation read or write")

    with monkeypatch.context() as guarded:
        for name in ("revision", "load", "save"):
            guarded.setattr(conversations, name, unexpected_access)
        denied = [
            alice.get(url, headers=alice_headers),
            alice.get(url, headers={**alice_headers, "If-None-Match": "*"}),
            alice.put(url, json=payload(1, "stale Alice history"), headers=alice_headers),
            alice.get(url, headers={"X-Conversation-Owner": f" {bob_id}"}),
            alice.get(url, headers={"X-Conversation-Owner": ""}),
        ]
    for result in denied:
        assert result.status_code == 403
        assert result.headers["x-conversation-owner-mismatch"] == "true"
        assert result.headers["cache-control"] == "private, no-store"
        assert "bob private" not in result.text
    restored = alice.get(url, headers=bob_headers)
    assert restored.status_code == 200 and restored.json() == bob_snapshot
    assert alice.get(url, headers={**bob_headers, "If-None-Match": restored.headers["etag"]}).status_code == 304
    assert bob.get(url).json() == bob_snapshot  # SDKs without the header remain supported.
    changed = alice.put(url, json=payload(1, "Bob's authorized update"), headers=bob_headers)
    assert changed.status_code == 200 and changed.json()["revision"] == 2
