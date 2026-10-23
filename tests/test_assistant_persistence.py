"""A provider reply remains durable when its browser stream disappears."""

import threading

import pytest
from fastapi.testclient import TestClient

from tinystudio import db as db_mod
from tinystudio.api.app import create_app
from tinystudio.integrations import assistant
from tinystudio.integrations.conversation_turn import ConversationTurn
from tinystudio.services import assistant_conversations as conversations
from tinystudio.services.users import create_user


def seed(db, project):
    user = create_user(db, "durable@example.com", "password-123", "Durable", "admin")
    snapshot = conversations.save(db, project.id, user.id, conversations.ConversationWrite(
        revision=0, history=[{"role": "user", "content": "Remember the evaluation"}], provider="openai", model="test"))
    db.commit()
    return user, snapshot


def test_reply_persists_after_stream_close(db, project):
    user, snapshot = seed(db, project)
    release, saved = threading.Event(), threading.Event()
    turn = ConversationTurn(project.id, user.id, snapshot, "openai", "test")

    def runner(*args):
        yield {"type": "text", "delta": "First "}
        assert release.wait(5)
        yield {"type": "text", "delta": "and final"}

    def finish(events):
        try:
            return turn(events)
        finally:
            saved.set()

    stream = assistant.stream_events("openai", None, [], with_ui=False, runner=runner, on_complete=finish)
    assert next(stream) == {"type": "text", "delta": "First "}
    stream.close()
    release.set()
    assert saved.wait(5)
    with db_mod.session() as independent:
        result = conversations.load(independent, project.id, user.id)
    assert result["revision"] == 2
    assert result["history"][-1]["content"] == "First and final"


def test_delayed_reply_cannot_restore_cleared_conversation(db, project):
    user, snapshot = seed(db, project)
    conversations.save(db, project.id, user.id, conversations.ConversationWrite(revision=1, history=[]))
    db.commit()
    turn = ConversationTurn(project.id, user.id, snapshot, "openai", "test")
    with pytest.raises(conversations.ConversationConflict):
        turn([{"type": "text", "delta": "obsolete reply"}])
    assert conversations.load(db, project.id, user.id)["history"] == []


def test_chat_endpoint_saves_reply_and_rejects_stale_turn(db, project, monkeypatch):
    user, _ = seed(db, project)
    def runner(*args):
        yield {"type": "text", "delta": "Saved evaluation review"}
        yield {"type": "done", "model": "test", "usage": {"input_tokens": 1, "output_tokens": 1}}
    monkeypatch.setattr(assistant, "run", runner)
    with TestClient(create_app(run_migrations=False), headers={"X-Requested-With": "tinystudio"}) as client:
        assert client.post("/api/auth/login", json={"email": user.email, "password": "password-123"}).status_code == 200
        body = {"project": project.slug, "provider": "openai", "model": "test", "with_ui": False,
                "messages": [{"role": "user", "content": "Remember the evaluation"}], "conversation_revision": 1}
        result = client.post("/api/assistant/chat", json=body)
        assert result.status_code == 200
        assert '"type": "conversation_saved", "revision": 2' in result.text
        restored = client.get(f"/api/p/{project.slug}/assistant/conversation").json()
        assert restored["history"][-1]["content"] == "Saved evaluation review"
        assert client.post("/api/assistant/chat", json=body).status_code == 409


@pytest.mark.parametrize("conflict", [False, True])
def test_completion_failure_distinguishes_storage_from_revision_conflicts(conflict):
    def runner(*args):
        yield {"type": "text", "delta": "Keep this generated reply"}
    def failed_save(events):
        if conflict:
            raise conversations.ConversationConflict("newer conversation")
        raise RuntimeError("database outage with private connection details")
    events = list(assistant.stream_events("openai", None, [], with_ui=False, runner=runner, on_complete=failed_save))
    assert events[0]["delta"] == "Keep this generated reply"
    assert events[-1]["type"] == "conversation_error"
    assert events[-1]["conflict"] is conflict
    assert "private connection details" not in events[-1]["message"]
