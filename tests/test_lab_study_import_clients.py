"""Public clients preserve explicit artifact-to-study import choices."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from typer.testing import CliRunner

from tinystudio.cli.main import app
from tinystudio.integrations import tools as shared_tools
from tinystudio.integrations.tools import Studio, tool_specs
from tinystudio.lab import cli


def authored_request(number_encoding="json"):
    return {"artifact_id": "artifact-1", "revision": 3, "projection": {
        "selection": {"lines": [7, 2]}, "unit_id": {"pointer": "/subject/id"},
        "condition": {"literal": "Authored condition"}, "kind": "measurement",
        "data": {"response": {"pointer": "/response"},
                 "metadata": {"literal": {"units": "mV", "choices": ["retained", "unchanged"]}}},
        "number_encoding": number_encoding}}


def import_request(number_encoding="json"):
    return {**authored_request(number_encoding), "preview_digest": "a" * 64,
            "idempotency_key": "chosen-import"}


def specs(studio):
    return {item.name: item for item in tool_specs(studio)}


@pytest.mark.parametrize("number_encoding", ["json", "decimal_strings"])
def test_tools_preview_and_apply_are_separate_and_preserve_authored_projection(number_encoding):
    preview = {"digest": "a" * 64, "summary": {"count": 2}}
    studio = Mock(project="research")
    studio.call.return_value = preview
    tools = specs(studio)
    body = authored_request(number_encoding)
    before = deepcopy(body)
    result = tools["lab_study_import_preview"].func("study-1", **body)
    assert result is preview
    assert body == before
    studio.call.assert_called_once_with("POST", "/api/lab/studies/study-1/observation-imports/preview", json=before)
    assert tools["lab_study_import_preview"].read_only
    assert not tools["lab_study_import_preview"].long_running
    assert not tools["lab_study_import_preview"].destructive

    studio.call.reset_mock()
    chosen = import_request(number_encoding)
    tools["lab_study_import"].func("study-1", **chosen)
    assert body == before
    studio.call.assert_called_once_with("POST", "/api/lab/studies/study-1/observation-imports", json=chosen)
    assert not tools["lab_study_import"].read_only
    assert not tools["lab_study_import"].long_running
    assert not tools["lab_study_import"].destructive


def test_tools_use_authenticated_project_scoped_transport():
    received = []
    def handle(request):
        received.append(request)
        return httpx.Response(200, json={"accepted": True})
    studio = Studio("https://fixture.invalid", project="research", token="fixture-only-token")
    headers = dict(studio.http.headers)
    studio.http.close()
    studio.http = httpx.Client(base_url=studio.base_url, headers=headers, transport=httpx.MockTransport(handle))
    try:
        tools = specs(studio)
        tools["lab_study_import_preview"].func("study-1", **authored_request())
        tools["lab_study_import"].func("study-1", **import_request())
    finally:
        studio.http.close()
    assert [(request.method, request.url.path) for request in received] == [
        ("POST", "/api/p/research/lab/studies/study-1/observation-imports/preview"),
        ("POST", "/api/p/research/lab/studies/study-1/observation-imports")]
    assert [json.loads(request.content) for request in received] == [authored_request(), import_request()]
    assert all(request.headers["Authorization"] == "Bearer fixture-only-token" for request in received)
    assert all(request.headers["X-Project"] == "research" for request in received)


def cli_transport(monkeypatch):
    received = []
    original_studio = Studio
    def handle(request):
        received.append(request)
        return httpx.Response(200, json={"digest": "a" * 64, "accepted": True})
    def studio_factory(server, *, token):
        studio = original_studio(server, token=token)
        headers = dict(studio.http.headers)
        studio.http.close()
        studio.http = httpx.Client(base_url=server, headers=headers, transport=httpx.MockTransport(handle))
        return studio
    monkeypatch.setattr(cli.ClientConfig, "from_environment", lambda: SimpleNamespace(
        url="https://fixture.invalid", api_key="fixture-only-token"))
    monkeypatch.setattr(shared_tools, "Studio", studio_factory)
    return received


@pytest.mark.parametrize("number_encoding", ["json", "decimal_strings"])
@pytest.mark.parametrize("command,body_factory,suffix", [
    ("study-import-preview", authored_request, "/preview"),
    ("study-import", import_request, "")])
def test_cli_commands_send_one_authenticated_scoped_request_and_exact_file_body(
        tmp_path, monkeypatch, command, body_factory, suffix, number_encoding):
    received = cli_transport(monkeypatch)
    body = body_factory(number_encoding)
    content = json.dumps(body, indent=2)
    source = tmp_path / "researcher-request.json"
    source.write_text(content)
    result = CliRunner().invoke(app, ["lab", command, "study-1", str(source), "--project", "research"])
    assert result.exit_code == 0, result.output
    assert "accepted" in result.output
    assert source.read_text() == content
    assert len(received) == 1
    request, = received
    assert (request.method, request.url.path) == ("POST", "/api/p/research/lab/studies/study-1/observation-imports" + suffix)
    assert json.loads(request.content) == body
    assert request.headers["Authorization"] == "Bearer fixture-only-token"


@pytest.mark.parametrize("projection", [{}, {"selection": {"lines": []}, "data": {"n": {"literal": 0}}}])
def test_clients_do_not_infer_or_validate_scientific_choices(tmp_path, monkeypatch, projection):
    studio = Mock(project="research")
    tools = specs(studio)
    tools["lab_study_import_preview"].func("study-1", "artifact-1", 3, projection)
    studio.call.assert_called_once_with("POST", "/api/lab/studies/study-1/observation-imports/preview",
        json={"artifact_id": "artifact-1", "revision": 3, "projection": projection})
    received = cli_transport(monkeypatch)
    body = {"artifact_id": "artifact-1", "revision": 3, "projection": projection}
    source = tmp_path / "uninterpreted.json"
    source.write_text(json.dumps(body))
    result = CliRunner().invoke(app, ["lab", "study-import-preview", "study-1", str(source), "--project", "research"])
    assert result.exit_code == 0, result.output
    assert len(received) == 1 and json.loads(received[0].content) == body


@pytest.mark.parametrize("value", ["../other", "a/b", "a?project=other", "a#fragment", "%2e%2e", "a\\b", "", "x" * 65])
@pytest.mark.parametrize("field", ["study", "artifact", "project"])
@pytest.mark.parametrize("tool", ["lab_study_import_preview", "lab_study_import"])
def test_tools_reject_unsafe_identifiers_before_transport(value, field, tool):
    studio = Mock(project=value if field == "project" else "research")
    tools = specs(studio)
    body = import_request() if tool == "lab_study_import" else authored_request()
    if field == "artifact":
        body["artifact_id"] = value
    # Projects have a longer established slug limit than study/artifact IDs.
    if field == "project" and value == "x" * 65:
        studio.project = "x" * 81
    with pytest.raises(ValueError, match="identifier"):
        tools[tool].func(value if field == "study" else "study-1", **body)
    studio.call.assert_not_called()


@pytest.mark.parametrize("value", ["../other", "a/b", "a?project=other", "%2e%2e", "a\\b", "", "x" * 81])
@pytest.mark.parametrize("field", ["study", "project"])
@pytest.mark.parametrize("command", ["study-import-preview", "study-import"])
def test_cli_rejects_unsafe_path_identifiers_before_transport(tmp_path, monkeypatch, value, field, command):
    received = cli_transport(monkeypatch)
    source = tmp_path / "request.json"
    source.write_text(json.dumps(import_request() if command == "study-import" else authored_request()))
    result = CliRunner().invoke(app, ["lab", command, value if field == "study" else "study-1", str(source),
        "--project", value if field == "project" else "research"])
    assert result.exit_code != 0 and "identifier" in result.output
    assert received == []


def test_cli_commands_require_an_explicit_project_and_request_file(tmp_path, monkeypatch):
    received = cli_transport(monkeypatch)
    source = tmp_path / "request.json"
    source.write_text(json.dumps(import_request()))
    for command in ("study-import-preview", "study-import"):
        for arguments in (["study-1", str(source)], ["study-1", "--project", "research"]):
            result = CliRunner().invoke(app, ["lab", command, *arguments])
            assert result.exit_code != 0
    assert received == []
