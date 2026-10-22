"""Offline tests for the assistant's interface bridge and streaming: no provider calls."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

from tinystudio.integrations import assistant
from tinystudio.integrations.tools import Studio


def test_ui_bridge_round_trip_and_timeout(monkeypatch):
    import queue

    events: queue.Queue = queue.Queue()
    bridge = assistant.UiBridge(events)
    monkeypatch.setattr(assistant, "UI_TIMEOUT_SECONDS", 0.3)

    def answer():
        ev = events.get(timeout=2)
        assert ev["type"] == "ui_request" and ev["action"] == "click" and ev["args"] == {"ref": "ref_3"}
        assert assistant.UiBridge.deliver(ev["id"], {"clicked": "ref_3"}) is True

    threading.Thread(target=answer, daemon=True).start()
    assert json.loads(bridge.request("click", {"ref": "ref_3"})) == {"clicked": "ref_3"}
    # nobody answers: the tool returns an error instead of hanging the turn
    started = time.perf_counter()
    out = json.loads(bridge.request("read_page", {}))
    assert "did not answer" in out["error"] and time.perf_counter() - started < 2
    assert assistant.UiBridge.deliver("nope", {}) is False


def test_ui_tool_specs_and_context_block():
    import queue

    specs = assistant.ui_tool_specs(assistant.UiBridge(queue.Queue()))
    assert [s.name for s in specs] == ["ui_read_page", "ui_get_text", "ui_navigate", "ui_click", "ui_fill", "ui_select"]
    assert all(not s.destructive and not s.long_running for s in specs)
    tools = assistant._openai_tools(specs)
    assert tools[0]["name"] == "ui_read_page" and "interactive_only" in tools[0]["parameters"]["properties"]
    block = assistant._context_block({"route": "#/datasets/commands", "title": "commands", "text": "x" * 20000, "api": [{"path": "/api/datasets/commands", "data": "{}"}], "selection": "sel"})
    assert "route: #/datasets/commands" in block and "user's text selection: sel" in block and "data from /api/datasets/commands" in block and len(block) < 16000
    assert assistant._context_block(None) == ""


def test_stream_events_interleaves_ui_requests():
    def fake_runner(provider, studio, messages, allow_destructive, model, context, bridge):
        yield {"type": "text", "delta": "looking"}
        result = bridge.request("read_page", {"interactive_only": True})
        yield {"type": "tool_result", "id": "t1", "name": "ui_read_page", "output": result}
        yield {"type": "done", "usage": {"input_tokens": 1, "output_tokens": 1}, "model": model}

    seen = []
    for ev in assistant.stream_events("anthropic", Studio("http://127.0.0.1:1", None), [{"role": "user", "content": "hi"}], model="m", runner=fake_runner):
        seen.append(ev)
        if ev["type"] == "ui_request":
            assert assistant.UiBridge.deliver(ev["id"], "route: #/datasets\nref_1 | button | New dataset")
    kinds = [e["type"] for e in seen]
    assert kinds == ["text", "ui_request", "tool_result", "done"]
    assert "ref_1" in seen[2]["output"] and seen[3]["model"] == "m"


def test_stream_events_reports_runner_failure():
    def broken(*args):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    events = list(assistant.stream_events("anthropic", Studio("http://127.0.0.1:1", None), [{"role": "user", "content": "hi"}], runner=broken))
    assert events == [{"type": "error", "message": "RuntimeError: boom"}]


def test_list_models_without_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(assistant, "anthropic_available", lambda: False)
    out = assistant.list_models()
    assert out["anthropic"]["available"] is False and out["anthropic"]["models"] == [] and out["openai"]["available"] is False
    assert out["anthropic"]["default"].startswith("claude-")


def test_cached_model_loader_reports_errors(monkeypatch):
    assistant._MODEL_CACHE.clear()

    def boom():
        raise RuntimeError("no network")

    out = assistant._cached("test-provider", boom)
    assert out["models"] == [] and "no network" in out["error"]
    assert assistant._cached("test-provider", lambda: [SimpleNamespace(id="x")])["error"] == out["error"]  # cached for a while
    assistant._MODEL_CACHE.clear()


def test_anthropic_client_sends_workspace_header_and_explains_the_requirement(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("TINYSTUDIO_ANTHROPIC_WORKSPACE_ID", raising=False)
    assert assistant.anthropic_client().default_headers.get("anthropic-workspace-id") is None
    monkeypatch.setenv("TINYSTUDIO_ANTHROPIC_WORKSPACE_ID", " wrkspc_abc ")
    assert assistant.anthropic_client().default_headers["anthropic-workspace-id"] == "wrkspc_abc"
    api_text = "Error code: 400 - This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header"
    assert assistant.explain_provider_error(RuntimeError(api_text)) == assistant.WORKSPACE_HINT
    assert assistant.explain_provider_error(RuntimeError("boom")) == "RuntimeError: boom"
    assert assistant.explain_provider_error(assistant.ProviderError("plain words")) == "plain words"
    assistant._MODEL_CACHE.clear()

    def rejected():
        raise RuntimeError(api_text)

    assert assistant._cached("anthropic-test", rejected)["error"] == assistant.WORKSPACE_HINT
    assistant._MODEL_CACHE.clear()
