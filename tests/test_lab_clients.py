from pathlib import Path

import pytest

from tinystudio.lab import cli
from tinystudio.lab.tools import tool_specs
from tinystudio.integrations.tools import ToolSpec


def test_cli_never_deletes_another_partial_export(tmp_path, monkeypatch):
    output = tmp_path / "run.zip"
    partial = tmp_path / "run.zip.partial"
    partial.write_text("owned by someone else")
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_bytes(self): yield b"download"
    class Client(Response):
        def stream(self, *args): return Response()
    monkeypatch.setattr(cli, "client", Client)
    with pytest.raises(FileExistsError):
        cli.export("run", output, "project")
    assert partial.read_text() == "owned by someone else"
    assert not output.exists()


def test_mcp_tools_use_same_http_contracts():
    calls = []
    class Client:
        def call(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {}
    tools = {tool.name: tool for tool in tool_specs(Client(), ToolSpec)}
    tools["lab_submit"].func("definition", 3, "target", "key")
    tools["lab_cancel"].func("run")
    assert calls[0] == ("POST", "/api/lab/runs", {"json": {"definition_id": "definition", "revision": 3, "target_id": "target", "idempotency_key": "key"}})
    assert calls[1] == ("POST", "/api/lab/runs/run/cancel", {"json": {}})
    assert tools["lab_run"].read_only
    assert tools["lab_submit"].long_running


@pytest.mark.parametrize("changed", [False, True])
def test_export_checks_download_and_never_overwrites_an_output_created_during_work(tmp_path, monkeypatch, changed):
    import hashlib
    output = tmp_path / "research.zip"
    content = b"pinned export bytes"
    expected = {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
    requested = []
    def call(method, project, path, body=None):
        requested.append((method, project, path))
        if changed:
            output.write_text("created by someone else while export ran")
        return {"id": "operation", "kind": "export", "state": "succeeded", "result": {"artifact": expected}}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_bytes(self): yield content
    class Client(Response):
        def stream(self, method, path):
            assert (method, path) == ("GET", "/api/p/research/lab/operations/operation/download")
            return Response()
    monkeypatch.setattr(cli, "call", call)
    monkeypatch.setattr(cli, "client", Client)
    if changed:
        with pytest.raises(FileExistsError):
            cli.export("run", output, "research")
        assert output.read_text() == "created by someone else while export ran"
    else:
        cli.export("run", output, "research")
        assert output.read_bytes() == content
    assert requested == [("POST", "research", "/runs/run/exports")]
    assert not output.with_name("research.zip.partial").exists()


def test_export_corrupt_download_never_becomes_a_complete_file(tmp_path, monkeypatch):
    import typer
    output = tmp_path / "research.zip"
    monkeypatch.setattr(cli, "call", lambda *args, **kwargs: {
        "id": "operation", "kind": "export", "state": "succeeded", "result": {"artifact": {"sha256": "0"*64, "size": 9}}})
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_bytes(self): yield b"corrupted"
    class Client(Response):
        def stream(self, *args): return Response()
    monkeypatch.setattr(cli, "client", Client)
    with pytest.raises(typer.BadParameter, match="artifact manifest"):
        cli.export("run", output, "research")
    assert not output.exists() and not output.with_name("research.zip.partial").exists()


def test_operation_controls_are_explicit_and_mutually_exclusive(monkeypatch):
    import typer
    calls = []
    monkeypatch.setattr(cli, "call", lambda *args: calls.append(args) or {})
    cli.operation("operation", "project", cancel=True)
    cli.operation("operation", "project", retry=True)
    with pytest.raises(typer.BadParameter):
        cli.operation("operation", "project", cancel=True, retry=True)
    assert calls == [("POST", "project", "/operations/operation/cancel", {}),
                     ("POST", "project", "/operations/operation/retry", {})]


def recovery_transport(monkeypatch, content=b"recovered export", *, kind="export", state="succeeded", manifest=None):
    """A retained operation is an independent server fixture, never newly admitted."""
    import hashlib
    import httpx
    metadata = manifest if manifest is not None else {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
    requests = []
    def handle(request):
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=content)
        if request.url.path.endswith("/operations"):
            return httpx.Response(200, json=[{"id": "retained-op", "kind": kind, "state": state}])
        return httpx.Response(200, json={"id": "retained-op", "kind": kind, "state": state, "result": {"artifact": metadata}})
    monkeypatch.setattr(cli, "client", lambda: httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)))
    return requests


def test_cli_recovers_existing_export_without_duplicate_admission(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    requests = recovery_transport(monkeypatch)
    output = tmp_path / "recovered.zip"
    result = CliRunner().invoke(cli.app, ["operation", "retained-op", "--project", "alpha", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert output.read_bytes() == b"recovered export"
    assert not output.with_name("recovered.zip.partial").exists()
    assert requests == [("GET", "/api/p/alpha/lab/operations/retained-op"), ("GET", "/api/p/alpha/lab/operations/retained-op/download")]


@pytest.mark.parametrize("kind,state", [("import", "succeeded"), ("export", "queued"), ("export", "running"), ("export", "failed"), ("export", "lost"), ("export", "cancelled")])
def test_cli_recovery_requires_completed_export(tmp_path, monkeypatch, kind, state):
    from typer.testing import CliRunner
    requests = recovery_transport(monkeypatch, kind=kind, state=state)
    output = tmp_path / "recovered.zip"
    result = CliRunner().invoke(cli.app, ["operation", "retained-op", "--project", "alpha", "--output", str(output)])
    assert result.exit_code != 0
    assert "succeeded export" in result.output
    assert requests == [("GET", "/api/p/alpha/lab/operations/retained-op")]
    assert not output.exists() and not output.with_name("recovered.zip.partial").exists()


@pytest.mark.parametrize("mismatch", ["checksum", "short", "long"])
def test_recovered_export_checks_hash_and_size_and_removes_owned_partial(tmp_path, monkeypatch, mismatch):
    import hashlib
    from typer.testing import CliRunner
    content = b"pinned recovered bytes"
    manifest = {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
    if mismatch == "checksum": manifest["sha256"] = "0"*64
    if mismatch == "short": manifest["size"] -= 1
    if mismatch == "long": manifest["size"] += 1
    requests = recovery_transport(monkeypatch, content, manifest=manifest)
    output = tmp_path / "recovered.zip"
    result = CliRunner().invoke(cli.app, ["operation", "retained-op", "--project", "alpha", "--output", str(output)])
    assert result.exit_code != 0
    assert "downloaded export does not match its completed artifact manifest" in " ".join(result.output.replace("│", " ").split())
    assert len(requests) == 2 and all(method == "GET" for method, _ in requests)
    assert not output.exists() and not output.with_name("recovered.zip.partial").exists()


@pytest.mark.parametrize("occupied", ["output", "partial"])
def test_recovery_preserves_foreign_files_before_any_http(tmp_path, monkeypatch, occupied):
    from typer.testing import CliRunner
    requests = recovery_transport(monkeypatch)
    output = tmp_path / "recovered.zip"
    foreign = output if occupied == "output" else output.with_name("recovered.zip.partial")
    foreign.write_bytes(b"foreign bytes")
    result = CliRunner().invoke(cli.app, ["operation", "retained-op", "--project", "alpha", "--output", str(output)])
    assert result.exit_code != 0
    assert foreign.read_bytes() == b"foreign bytes"
    assert requests == []


def test_cli_operations_lists_retained_ids_without_mutation(monkeypatch):
    from typer.testing import CliRunner
    requests = recovery_transport(monkeypatch)
    result = CliRunner().invoke(cli.app, ["operations", "--project", "alpha"])
    assert result.exit_code == 0 and "retained-op" in result.output
    assert requests == [("GET", "/api/p/alpha/lab/operations")]


@pytest.mark.parametrize("value", ["../other", "a/b", "a?project=other", "a#fragment", "%2e%2e", "a\\b", "", "x"*65])
@pytest.mark.parametrize("command", ["inspect", "events", "cancel", "retry", "recover", "operation", "export"])
def test_cli_rejects_url_segment_escape_before_http(monkeypatch, tmp_path, command, value):
    from unittest.mock import Mock
    import typer
    http_call = Mock()
    monkeypatch.setattr(cli, "call", http_call)
    with pytest.raises(typer.BadParameter, match="identifier"):
        if command == "export": cli.export(value, tmp_path / "export.zip", "alpha")
        else: getattr(cli, command)(value, "alpha")
    http_call.assert_not_called()


@pytest.mark.parametrize("value", ["../other", "a/b", "a?project=other", "a#fragment", "%2e%2e", "a\\b", "", "x"*65])
@pytest.mark.parametrize("tool", ["lab_run", "lab_events", "lab_cancel", "lab_retry", "lab_export", "lab_operation", "lab_operation_cancel", "lab_operation_retry", "lab_recover"])
def test_mcp_rejects_url_segment_escape_before_http(tool, value):
    from unittest.mock import Mock
    studio = Mock(project="alpha")
    specs = {item.name: item for item in tool_specs(studio, ToolSpec)}
    with pytest.raises(ValueError, match="identifier"):
        if tool == "lab_export": specs[tool].func(value, "explicit-export-key")
        else: specs[tool].func(value)
    studio.call.assert_not_called()


def test_mcp_operation_inspection_inventory_and_controls_have_distinct_authority():
    from unittest.mock import Mock
    studio = Mock(project="alpha")
    specs = {item.name: item for item in tool_specs(studio, ToolSpec)}
    assert specs["lab_operation"].read_only and specs["lab_operations"].read_only
    assert not specs["lab_operation_cancel"].read_only
    assert not specs["lab_operation_retry"].read_only
    assert specs["lab_operation_retry"].long_running
    specs["lab_operations"].func()
    studio.call.assert_called_once_with("GET", "/api/lab/operations")
    studio.call.reset_mock()
    specs["lab_operation_cancel"].func("retained-op")
    studio.call.assert_called_once_with("POST", "/api/lab/operations/retained-op/cancel", json={})
    studio.call.reset_mock()
    specs["lab_operation_retry"].func("retained-op")
    studio.call.assert_called_once_with("POST", "/api/lab/operations/retained-op/retry", json={})


@pytest.mark.parametrize("occupied", ["output", "partial"])
def test_recovered_download_never_replaces_a_file_created_after_operation_inspection(tmp_path, monkeypatch, occupied):
    from typer.testing import CliRunner
    requests = recovery_transport(monkeypatch)
    output = tmp_path / "recovered.zip"
    foreign = output if occupied == "output" else output.with_name("recovered.zip.partial")
    original_call = cli.call
    def inspect_then_race(*args, **kwargs):
        operation = original_call(*args, **kwargs)
        foreign.write_bytes(b"concurrent owner")
        return operation
    monkeypatch.setattr(cli, "call", inspect_then_race)
    result = CliRunner().invoke(cli.app, ["operation", "retained-op", "--project", "alpha", "--output", str(output)])
    assert result.exit_code != 0
    assert isinstance(result.exception, FileExistsError)
    assert foreign.read_bytes() == b"concurrent owner"
    assert all(method == "GET" for method, _ in requests)
    if occupied == "output": assert not output.with_name("recovered.zip.partial").exists()
    else: assert not output.exists()


def test_recovered_download_cannot_be_combined_with_mutating_controls(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    requests = recovery_transport(monkeypatch)
    for control in ("--cancel", "--retry"):
        result = CliRunner().invoke(cli.app, ["operation", "retained-op", "--project", "alpha", "--output", str(tmp_path / "recover.zip"), control])
        assert result.exit_code != 0
        assert "download cannot be combined" in result.output
    assert requests == []


def test_lab_clients_reject_project_path_escape_before_transport(monkeypatch):
    from unittest.mock import Mock
    import typer
    requests = recovery_transport(monkeypatch)
    with pytest.raises(typer.BadParameter, match="identifier"):
        cli.operations("../other")
    assert requests == []
    studio = Mock(project="../other")
    specs = {item.name: item for item in tool_specs(studio, ToolSpec)}
    with pytest.raises(ValueError, match="identifier"):
        specs["lab_operations"].func()
    studio.call.assert_not_called()
