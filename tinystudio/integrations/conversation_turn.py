"""Persist a provider turn independently of the browser connection's lifetime."""

from typing import Any

from tinystudio import db
from tinystudio.services import assistant_conversations as conversations


class ConversationTurn:
    def __init__(self, project_id: str, user_id: str, snapshot: dict[str, Any], provider: str, model: str | None):
        self.project_id = project_id
        self.user_id = user_id
        self.snapshot = snapshot
        self.provider = provider
        self.model = model or snapshot["model"]

    def __call__(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        reply: dict[str, Any] = {"role": "assistant", "content": "", "calls": [], "sources": [], "searches": []}
        for event in events:
            if event["type"] == "text":
                reply["content"] += event["delta"]
            elif event["type"] == "search" and len(reply["searches"]) < 128:
                reply["searches"].append({key: event[key] for key in ("id", "state", "query", "error") if key in event})
            elif event["type"] == "source" and len(reply["sources"]) < 128 and len(event["url"]) <= 4000:
                reply["sources"].append({"url": event["url"], "title": event.get("title", "")[:1000]})
            elif event["type"] == "tool_call":
                reply["calls"].append({"id": event["id"], "name": event["name"], "input": event.get("input", {}),
                                       "kind": "ui" if event["name"].startswith("ui_") else "tool"})
            elif event["type"] == "tool_result":
                call = next((c for c in reply["calls"] if c["id"] == event["id"]), None)
                if call is not None:
                    call["output"] = event.get("output")
        history = self.snapshot["history"] + ([reply] if reply["content"] or reply["calls"] or reply["sources"] or reply["searches"] else [])
        body = conversations.ConversationWrite(revision=self.snapshot["revision"], history=history,
                                               provider=self.provider, model=self.model)
        with db.session() as session:
            saved = conversations.save(session, self.project_id, self.user_id, body)
        return {"type": "conversation_saved", "revision": saved["revision"], "updated": saved["updated"]}
