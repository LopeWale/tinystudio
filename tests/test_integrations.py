"""Offline tests for the shared tool catalog, the assistant plumbing, and the Hub helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tinystudio.integrations import assistant

from tinystudio.integrations import hub
from tinystudio.integrations.tools import Studio, StudioError, tool_specs


def test_tool_catalog_shape():
    specs = tool_specs(Studio("http://127.0.0.1:1", None))
    names = [s.name for s in specs]
    assert len(names) == len(set(names)) == 55
    assert {"get_overview", "create_synthetic_dataset", "start_job", "run_eval", "chat", "delete", "search_hub_models", "download_hub_model", "clean_dataset", "run_autopilot_cycle", "get_autopilot_history", "capture_activations", "get_neuron", "test_neurons", "compare_models"} <= set(names)
    assert [s.name for s in specs if s.destructive] == ["delete"]
    assert all(s.description for s in specs)
    assert sum(s.long_running for s in specs) == 14 and sum(s.read_only for s in specs) == 25


def test_studio_without_server_raises_clean_error():
    studio = Studio("http://127.0.0.1:1", "home", "ts-x")
    with pytest.raises(Exception):
        studio.call("GET", "/api/overview")
    with pytest.raises(StudioError, match="not reachable"):
        studio.ensure_running()


def test_studio_scopes_project_paths():
    from tinystudio.integrations.tools import scoped

    assert scoped("/api/datasets/x", "home") == "/api/p/home/datasets/x"
    assert scoped("/api/overview", "home") == "/api/p/home/overview"
    assert scoped("/api/tasks/1", "home") == "/api/tasks/1" and scoped("/api/datasets", None) == "/api/datasets"
    studio = Studio("http://127.0.0.1:1", "home", "ts-key")
    assert studio.http.headers["authorization"] == "Bearer ts-key" and studio.http.headers["x-project"] == "home"


def test_json_tool_wraps_errors_and_truncates():
    def boom(x: int) -> dict:
        """boom"""
        raise StudioError("nope")

    def big(x: int) -> str:
        """big"""
        return "a" * (assistant.MAX_RESULT_CHARS + 50)

    spec = SimpleNamespace(func=boom, name="boom", description="boom")
    assert json.loads(assistant._json_tool(spec)(1)) == {"error": "nope"}
    spec = SimpleNamespace(func=big, name="big", description="big")
    out = assistant._json_tool(spec)(1)
    assert out.endswith("[truncated 50 chars]") and len(out) < assistant.MAX_RESULT_CHARS + 60


def test_assistant_status_and_openai_tools_schema():
    st = assistant.status()
    assert set(st) == {"anthropic", "openai"} and st["anthropic"]["model"].startswith("claude-")
    specs = [s for s in tool_specs(Studio("http://127.0.0.1:1", None)) if s.name in ("get_datapoints", "set_labels")]
    tools = assistant._openai_tools(specs)
    assert tools[0]["type"] == "function" and tools[0]["parameters"]["required"] == ["dataset"]
    assert tools[1]["parameters"]["properties"]["labels"]["type"] == "array"


def test_hub_describe_handles_missing_metadata():
    info = SimpleNamespace(id="org/model", safetensors=SimpleNamespace(total=135_000_000), card_data={"license": "apache-2.0"}, tags=["onnx", "transformers"], gated=False, downloads=10, likes=1, library_name="transformers", pipeline_tag="text-generation", last_modified=None)
    d = hub.describe(info)
    assert d["params_b"] == 0.135 and d["license"] == "apache-2.0" and d["transformers_loadable"] is True and d["format_tags"] == ["onnx"] and d["mlx"] is False
    bare = hub.describe(SimpleNamespace(id="mlx-community/x", safetensors=None, card_data=None, tags=["mlx", "license:mit"], gated="auto", downloads=None, likes=None, library_name=None, pipeline_tag=None, last_modified=None))
    assert bare["params_b"] is None and bare["license"] == "mit" and bare["gated"] == "auto" and bare["transformers_loadable"] is False and bare["mlx"] is True


def test_hub_save_token_rejects_malformed():
    with pytest.raises(Exception, match="start with hf_"):
        hub.save_token("not-a-token")


def test_mcp_server_registers_every_tool():
    import asyncio

    from tinystudio.integrations.mcp_server import build_server

    server = build_server(Studio("http://127.0.0.1:1", None))
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert len(names) == 55 and "delete" in names and "get_overview" in names
    delete = next(t for t in tools if t.name == "delete")
    assert delete.annotations.destructive_hint is True
    assert next(t for t in tools if t.name == "get_overview").annotations.read_only_hint is True


def test_json_tool_hardening_against_bad_arguments():
    def strict(x: int) -> dict:
        """strict"""
        return {"x": x}

    spec = SimpleNamespace(func=strict, name="strict", description="strict")
    out = json.loads(assistant._json_tool(spec)(y=1))
    assert "bad arguments" in out["error"]
