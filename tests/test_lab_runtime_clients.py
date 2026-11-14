"""Runtime qualification clients preserve authored settings and share HTTP routes."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from unittest.mock import Mock

import httpx
import pytest
from typer.testing import CliRunner

from tinystudio.cli.main import app
from tinystudio.integrations.tools import Studio, ToolSpec
from tinystudio.lab import cli
from tinystudio.lab.client_paths import RuntimeApiPaths, runtime_definition
from tinystudio.lab.contracts import ExperimentDefinition
from tinystudio.lab.runtime_probe import validate_selection
from tinystudio.lab.tools import tool_specs


def authored_settings():
    return {"name": "Researcher NF4 fixture", "parameters": {"method": "nf4.training",
        "objective": "sft", "device": "cuda:2", "dtype": "bfloat16", "fixture": "retained-fixture",
        "nf4_execution": {"double_quantization": False}, "reload_comparison": {"rtol": .0017, "atol": .000008},
        "training": {"learning_rate": .00013, "steps": 7}, "fixture_config": {"sequence_length": 23}},
        "execution": {"profile": "isolated", "backend": "container", "max_seconds": 471},
        "inputs": [{"artifact_id": "retained-artifact", "sha256": "1"*64, "role": "fixture", "visibility": "evaluation"}]}


def test_runtime_routes_are_shared_and_immutable():
    assert RuntimeApiPaths().methods == "/api/lab/runtime/methods"
    assert RuntimeApiPaths().definitions == "/api/lab/definitions"
    assert RuntimeApiPaths(base="").methods == "/runtime/methods"
    assert RuntimeApiPaths(base="").definitions == "/definitions"
    with pytest.raises(FrozenInstanceError):
        RuntimeApiPaths().base = "/other"


def test_shared_definition_builder_changes_only_structural_engine_fields():
    settings = authored_settings()
    original = deepcopy(settings)
    definition = runtime_definition(**settings)
    assert definition == {**settings, "engine": "runtime.qualify", "domain": "runtime", "mode": "offline", "operating_mode": "replay"}
    definition["parameters"]["training"]["steps"] = 900
    definition["execution"]["max_seconds"] = 1
    definition["inputs"][0]["visibility"] = "model"
    assert settings == original
    assert runtime_definition("Unspecified", {}, {}) == {"name": "Unspecified", "engine": "runtime.qualify",
        "domain": "runtime", "mode": "offline", "operating_mode": "replay", "parameters": {}, "execution": {}}


def test_runtime_tools_use_authenticated_scoped_http_and_never_submit():
    received = []
    studio = Studio("https://runtime.invalid", project="research", token="fixture-runtime-token")
    studio.http.close()
    def handle(request):
        received.append(request)
        return httpx.Response(200, json={"accepted": True})
    studio.http = httpx.Client(base_url=studio.base_url, headers={"Authorization": "Bearer fixture-runtime-token"},
                               transport=httpx.MockTransport(handle))
    settings = authored_settings()
    original = deepcopy(settings)
    try:
        specs = {item.name: item for item in tool_specs(studio, ToolSpec)}
        assert specs["lab_runtime_methods"].read_only and not specs["lab_runtime_methods"].long_running
        assert not specs["lab_runtime_define"].read_only and not specs["lab_runtime_define"].long_running
        specs["lab_runtime_methods"].func()
        specs["lab_runtime_define"].func(**settings)
    finally:
        studio.http.close()
    assert settings == original
    assert [(request.method, request.url.path) for request in received] == [
        ("GET", "/api/p/research/lab/runtime/methods"), ("POST", "/api/p/research/lab/definitions")]
    assert all(request.headers["Authorization"] == "Bearer fixture-runtime-token" for request in received)
    assert json.loads(received[1].content) == {"definition": runtime_definition(**settings)}


def test_runtime_tool_omits_unprovided_inputs_and_does_not_infer_runtime_choices():
    studio = Mock(project="research")
    specs = {item.name: item for item in tool_specs(studio, ToolSpec)}
    specs["lab_runtime_define"].func("Authored later", {}, {})
    studio.call.assert_called_once_with("POST", RuntimeApiPaths().definitions,
        json={"definition": runtime_definition("Authored later", {}, {})})


@pytest.mark.parametrize("surface", ["cli", "tool"])
@pytest.mark.parametrize("seed", [0, 31])
def test_public_runtime_clients_preserve_authored_seed_through_generic_definition_contract(tmp_path, monkeypatch, surface, seed):
    settings = {"name": "Authored seed", "parameters": {"method": "nf4.training", "objective": "sft",
        "device": "cpu", "dtype": "float32", "fixture": "tiny_llama", "seed": seed},
        "execution": {"profile": "trusted_native"}}
    original = deepcopy(settings)
    saved = []
    def handle(request):
        assert request.method == "POST" and request.url.path == "/api/p/research/lab/definitions"
        # Saving applies the same generic contract before later preflight or execution.
        definition = ExperimentDefinition.model_validate(json.loads(request.content)["definition"]).model_dump()
        saved.append(definition)
        return httpx.Response(201, json={"definition": definition})
    transport = httpx.MockTransport(handle)
    if surface == "cli":
        source = tmp_path / "runtime.json"
        source.write_text(json.dumps(settings))
        monkeypatch.setattr(cli, "client", lambda: httpx.Client(base_url="https://runtime.invalid", transport=transport))
        result = CliRunner().invoke(app, ["lab", "runtime-define", str(source), "--project", "research"])
        assert result.exit_code == 0, result.output
    else:
        studio = Studio("https://runtime.invalid", project="research", token="fixture-runtime-token")
        studio.http.close()
        studio.http = httpx.Client(base_url=studio.base_url, transport=transport)
        try:
            tools = {item.name: item for item in tool_specs(studio, ToolSpec)}
            tools["lab_runtime_define"].func(**settings)
        finally:
            studio.http.close()
    assert len(saved) == 1
    assert validate_selection(saved[0]).seed == seed
    assert saved[0]["seed"] == seed and saved[0]["parameters"] == settings["parameters"]
    assert settings == original


@pytest.mark.parametrize("project", ["../other", "a/b", "a?project=other", "a#fragment", "%2e%2e", "a\\b", "", "x"*81])
@pytest.mark.parametrize("tool", ["lab_runtime_methods", "lab_runtime_define"])
def test_runtime_tools_reject_project_escape_before_http(project, tool):
    studio = Mock(project=project)
    specs = {item.name: item for item in tool_specs(studio, ToolSpec)}
    with pytest.raises(ValueError, match="identifier"):
        if tool == "lab_runtime_methods":
            specs[tool].func()
        else:
            specs[tool].func(**authored_settings())
    studio.call.assert_not_called()


def test_main_cli_runtime_commands_use_shared_scoped_routes_and_exact_authored_settings(tmp_path, monkeypatch):
    received = []
    settings = authored_settings()
    source = tmp_path / "runtime.json"
    source.write_text(json.dumps(settings))
    def handle(request):
        received.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={"saved": True})
    monkeypatch.setattr(cli, "client", lambda: httpx.Client(base_url="https://runtime.invalid", transport=httpx.MockTransport(handle)))
    runner = CliRunner()
    for command in (["runtime-methods"], ["runtime-define", str(source)]):
        result = runner.invoke(app, ["lab", *command, "--project", "research"])
        assert result.exit_code == 0, result.output
    assert received == [("GET", "/api/p/research/lab/runtime/methods", None),
        ("POST", "/api/p/research/lab/definitions", {"definition": runtime_definition(**settings)})]
    assert json.loads(source.read_text()) == settings


@pytest.mark.parametrize("command", ["runtime-methods", "runtime-define"])
def test_cli_runtime_commands_reject_project_escape_before_http(tmp_path, monkeypatch, command):
    source = tmp_path / "runtime.json"
    source.write_text(json.dumps(authored_settings()))
    received = []
    def handle(request):
        received.append(request)
        return httpx.Response(200, json={})
    monkeypatch.setattr(cli, "client", lambda: httpx.Client(base_url="https://runtime.invalid", transport=httpx.MockTransport(handle)))
    arguments = [command, str(source)] if command == "runtime-define" else [command]
    result = CliRunner().invoke(cli.app, [*arguments, "--project", "../other"])
    assert result.exit_code != 0 and "identifier" in result.output
    assert received == []


@pytest.mark.parametrize("document", [[], {"name": "Missing settings"}, {**authored_settings(), "target_id": "auto-run"}])
def test_cli_runtime_define_requires_the_authored_envelope_without_auto_execution(tmp_path, monkeypatch, document):
    source = tmp_path / "runtime.json"
    source.write_text(json.dumps(document))
    transport = Mock()
    monkeypatch.setattr(cli, "call", transport)
    result = CliRunner().invoke(cli.app, ["runtime-define", str(source), "--project", "research"])
    assert result.exit_code != 0
    transport.assert_not_called()
