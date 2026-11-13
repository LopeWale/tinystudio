"""Thin CLI and shared MCP/assistant contracts for retained study analyses."""
from copy import deepcopy
import json
from unittest.mock import Mock

import httpx
import pytest
from typer.testing import CliRunner

from tinystudio.integrations.tools import Studio, ToolSpec
from tinystudio.lab import cli
from tinystudio.lab.tools import tool_specs


def authored_definition():
    return {"name": "Researcher's analysis", "snapshot_artifact_id": "snapshot-1",
        "parameters": {"method": "custom.python", "selection": {"mode": "ids", "record_ids": ["observed-4"],
            "exclude_ids": [], "kinds": ["observation"]}, "custom_method": {"name": "Explicit method", "version": "0.8"},
            "options": {"confidence": .913, "units": "authored"}, "assumptions": ["Authored assumption"]},
        "execution": {"profile": "isolated", "backend": "container", "max_seconds": 91},
        "custom_code_artifact_id": "method-1", "extra_artifact_ids": ["extra-1", "extra-2"]}


def specs(studio=None):
    return {item.name: item for item in tool_specs(studio or Mock(project="research"), ToolSpec)}


def test_shared_tools_preserve_authored_analysis_contract_and_authority():
    studio = Mock(project="research")
    tools = specs(studio)
    definition = authored_definition()
    before = deepcopy(definition)
    tools["lab_analysis_methods"].func()
    tools["lab_study_analysis_input"].func("study-1", "frozen-input", revision=3, through_seq=0)
    tools["lab_study_analysis_input"].func("study-1", "current-input")
    tools["lab_analysis_define"].func(**definition)
    tools["lab_study_analysis_result"].func("study-1", "run-1", "result-1", notes="Observed result assessed")
    assert definition == before
    assert studio.call.call_args_list == [
        (("GET", "/api/lab/statistics/analysis-methods"), {}),
        (("POST", "/api/lab/studies/study-1/analysis-inputs"), {"json": {"idempotency_key": "frozen-input", "revision": 3, "through_seq": 0}}),
        (("POST", "/api/lab/studies/study-1/analysis-inputs"), {"json": {"idempotency_key": "current-input"}}),
        (("POST", "/api/lab/statistics/analyses"), {"json": definition}),
        (("POST", "/api/lab/studies/study-1/analysis-results"), {"json": {"run_id": "run-1", "idempotency_key": "result-1", "notes": "Observed result assessed"}}),
    ]
    assert tools["lab_analysis_methods"].read_only and not tools["lab_analysis_methods"].long_running
    assert tools["lab_study_analysis_input"].long_running and not tools["lab_study_analysis_input"].read_only
    for name in ("lab_analysis_define", "lab_study_analysis_result"):
        assert not tools[name].read_only and not tools[name].long_running


def test_analysis_tool_omits_optional_code_and_auxiliary_and_invents_no_science():
    studio = Mock(project="research")
    specs(studio)["lab_analysis_define"].func("Authored", "snapshot", {}, {"profile": "trusted_native"})
    studio.call.assert_called_once_with("POST", "/api/lab/statistics/analyses", json={
        "name": "Authored", "snapshot_artifact_id": "snapshot", "parameters": {}, "execution": {"profile": "trusted_native"}})


def test_shared_analysis_tools_use_project_scoped_authenticated_transport():
    received = []
    studio = Studio("https://fixture.invalid", project="research", token="fixture-only-token")
    studio.http.close()
    def handle(request):
        received.append(request)
        return httpx.Response(200, json={"accepted": True})
    studio.http = httpx.Client(base_url=studio.base_url, headers={"Authorization": "Bearer fixture-only-token"},
                               transport=httpx.MockTransport(handle))
    try:
        tools = specs(studio)
        tools["lab_analysis_methods"].func()
        tools["lab_study_analysis_input"].func("study", "input-key")
        tools["lab_analysis_define"].func(**authored_definition())
        tools["lab_study_analysis_result"].func("study", "run", "result-key")
    finally:
        studio.http.close()
    assert [request.url.path for request in received] == ["/api/p/research/lab/statistics/analysis-methods",
        "/api/p/research/lab/studies/study/analysis-inputs", "/api/p/research/lab/statistics/analyses",
        "/api/p/research/lab/studies/study/analysis-results"]
    assert all(request.headers["Authorization"] == "Bearer fixture-only-token" for request in received)


@pytest.mark.parametrize("value", ["../other", "a/b", "a?project=other", "a#fragment", "%2e%2e", "a\\b", "", "x"*65])
@pytest.mark.parametrize("field", ["capture_study", "result_study", "result_run", "snapshot", "code", "auxiliary"])
def test_shared_analysis_tools_reject_invalid_identifiers_before_transport(value, field):
    studio = Mock(project="research")
    tools = specs(studio)
    with pytest.raises(ValueError, match="identifier"):
        if field == "capture_study":
            tools["lab_study_analysis_input"].func(value, "key")
        elif field.startswith("result_"):
            tools["lab_study_analysis_result"].func(value if field == "result_study" else "study",
                                                   value if field == "result_run" else "run", "key")
        else:
            body = authored_definition()
            body[{"snapshot": "snapshot_artifact_id", "code": "custom_code_artifact_id", "auxiliary": "extra_artifact_ids"}[field]] = [value] if field == "auxiliary" else value
            tools["lab_analysis_define"].func(**body)
    studio.call.assert_not_called()


def test_cli_analysis_commands_forward_exact_authored_json_and_boundaries(tmp_path, monkeypatch):
    received = []
    def handle(request):
        received.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={"id": "retained-id", "state": "queued"})
    monkeypatch.setattr(cli, "client", lambda: httpx.Client(base_url="https://fixture.invalid", transport=httpx.MockTransport(handle)))
    definition = authored_definition()
    source = tmp_path / "analysis.json"
    source.write_text(json.dumps(definition))
    commands = [["analysis-methods"], ["study-analysis-input", "study-1", "--idempotency-key", "input-key", "--revision", "2", "--through-seq", "0"],
        ["analysis-define", str(source)], ["study-analysis-result", "study-1", "run-1", "--idempotency-key", "result-key", "--notes", "Authored notes"]]
    for command in commands:
        result = CliRunner().invoke(cli.app, [*command, "--project", "research"])
        assert result.exit_code == 0, result.output
    assert received == [("GET", "/api/p/research/lab/statistics/analysis-methods", None),
        ("POST", "/api/p/research/lab/studies/study-1/analysis-inputs", {"idempotency_key": "input-key", "revision": 2, "through_seq": 0}),
        ("POST", "/api/p/research/lab/statistics/analyses", definition),
        ("POST", "/api/p/research/lab/studies/study-1/analysis-results", {"run_id": "run-1", "idempotency_key": "result-key", "notes": "Authored notes"})]


@pytest.mark.parametrize("command", [["study-analysis-input", "study"], ["study-analysis-result", "study", "run"]])
def test_cli_analysis_mutations_require_explicit_idempotency_key(command, monkeypatch):
    transport = Mock()
    monkeypatch.setattr(cli, "call", transport)
    result = CliRunner().invoke(cli.app, [*command, "--project", "research"])
    assert result.exit_code != 0 and "idempotency" in result.output
    transport.assert_not_called()


def test_cli_capture_omits_unselected_boundary_and_reuses_operation_response(monkeypatch):
    transport = Mock(return_value={"id": "capture-operation", "state": "queued"})
    monkeypatch.setattr(cli, "call", transport)
    result = CliRunner().invoke(cli.app, ["study-analysis-input", "study", "--project", "research", "--idempotency-key", "key"])
    assert result.exit_code == 0 and "capture-operation" in result.output
    transport.assert_called_once_with("POST", "research", "/studies/study/analysis-inputs", {"idempotency_key": "key"})


@pytest.mark.parametrize("command", [["study-analysis-input", "../escape", "--idempotency-key", "key"],
    ["study-analysis-result", "../escape", "run", "--idempotency-key", "key"],
    ["study-analysis-result", "study", "../escape", "--idempotency-key", "key"]])
def test_cli_analysis_path_escape_fails_before_transport(command, monkeypatch):
    transport = Mock()
    monkeypatch.setattr(cli, "call", transport)
    result = CliRunner().invoke(cli.app, [*command, "--project", "research"])
    assert result.exit_code != 0 and "identifier" in result.output
    transport.assert_not_called()
