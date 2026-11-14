"""Increment B is reachable through the existing command and tool entrypoints."""
import httpx
from typer.testing import CliRunner

from tinystudio.cli.main import app
from tinystudio.integrations.tools import Studio, scoped, tool_specs


def test_main_cli_registers_lab_and_runner():
    runner = CliRunner()
    for command, expected in [(["lab", "--help"], "submit"), (["runner", "--help"], "credential-file")]:
        result = runner.invoke(app, command, color=False)
        assert result.exit_code == 0, result.output
        assert expected in result.output


def test_shared_lab_catalog_uses_authenticated_project_transport():
    received = []
    def handle(request):
        received.append(request)
        return httpx.Response(200, json={"accepted": True})
    studio = Studio("http://test", project="alpha", token="fixture-token")
    studio.http.close()
    studio.http = httpx.Client(base_url="http://test", headers={"Authorization": "Bearer fixture-token"}, transport=httpx.MockTransport(handle))
    try:
        specs = {item.name: item for item in tool_specs(studio)}
        assert len(specs) == len(tool_specs(studio))
        assert {name for name in specs if name.startswith("lab_")} == {
            "lab_catalog", "lab_definitions", "lab_define", "lab_preflight", "lab_submit",
            "lab_runtime_methods", "lab_runtime_define",
            "lab_hardware_discovery_methods",
            "lab_statistics_methods", "lab_statistics_define",
            "lab_analysis_methods", "lab_study_analysis_input", "lab_analysis_define", "lab_study_analysis_result",
            "lab_studies", "lab_study", "lab_study_save", "lab_study_revisions",
            "lab_study_records", "lab_study_record", "lab_study_manifest",
            "lab_study_import_preview", "lab_study_import",
            "lab_run", "lab_events", "lab_cancel", "lab_retry", "lab_targets",
            "lab_recovered_evidence", "lab_recovered_evidence_preview", "lab_recovered_evidence_attach",
            "lab_export", "lab_operation", "lab_import", "lab_recover",
            "lab_operations", "lab_operation_cancel", "lab_operation_retry",
            "lab_promote_dataset",
            "lab_repository_input",
        }
        assert specs["lab_preflight"].read_only
        assert specs["lab_submit"].long_running
        specs["lab_preflight"].func({"name": "Control"}, "local")
        specs["lab_submit"].func("definition-1", 2, "local", "dedupe-1")
        assert [request.url.path for request in received] == ["/api/p/alpha/lab/preflight", "/api/p/alpha/lab/runs"]
        assert all(request.headers["authorization"] == "Bearer fixture-token" for request in received)
        assert b'"revision":2' in received[1].content
        assert scoped("/api/auth/status", "alpha") == "/api/auth/status"
    finally:
        studio.http.close()


def test_data_bridge_tools_require_explicit_opt_in():
    from tinystudio.integrations.tools import ToolSpec
    from tinystudio.lab.tools import tool_specs as lab_specs

    default = {item.name for item in lab_specs(None, ToolSpec)}
    opt_in = {item.name for item in lab_specs(None, ToolSpec, include_data_bridges=True)}
    assert {"lab_analysis_methods", "lab_study_analysis_input", "lab_analysis_define", "lab_study_analysis_result"} <= default
    assert not default & {"lab_snapshot_input", "lab_model_input"}
    assert opt_in - default == {"lab_snapshot_input", "lab_model_input"}


def test_main_cli_hides_data_bridge_commands_until_explicit_opt_in():
    from tinystudio.lab.cli import register
    import typer

    runner = CliRunner()
    default = runner.invoke(app, ["lab", "--help"], color=False)
    assert "promote-dataset" in default.output
    for command in ("snapshot-input", "model-input"):
        assert command not in default.output
    explicit = typer.Typer()
    register(explicit, include_data_bridges=True)
    result = runner.invoke(explicit, ["lab", "--help"], color=False)
    assert result.exit_code == 0
    for command in ("snapshot-input", "model-input", "promote-dataset"):
        assert command in result.output


def test_b_operation_tools_and_schedule_use_the_existing_scoped_transport():
    from unittest.mock import Mock
    studio = Mock()
    studio.project = "alpha"
    specs = {item.name: item for item in tool_specs(studio)}
    specs["lab_export"].func("run-1", "export-key")
    studio.call.assert_called_with("POST", "/api/lab/runs/run-1/exports", json={"idempotency_key": "export-key"})
    specs["lab_operation"].func("op-1")
    studio.call.assert_called_with("GET", "/api/lab/operations/op-1")
    specs["lab_import"].func("artifact-1", "import-key")
    studio.call.assert_called_with("POST", "/api/lab/bundle-imports", json={"artifact_id": "artifact-1", "idempotency_key": "import-key"})
    specs["lab_recover"].func("target-1")
    studio.call.assert_called_with("POST", "/api/lab/targets/target-1/recover", json={})
    specs["lab_submit"].func("definition-1", 1, "local", "submit-key", not_before="2031-01-02T03:04:00Z")
    studio.call.assert_called_with("POST", "/api/lab/runs", json={"definition_id": "definition-1", "revision": 1, "target_id": "local", "idempotency_key": "submit-key", "not_before": "2031-01-02T03:04:00Z"})
    specs["lab_submit"].func("definition-1", 1, "local", "submit-key")
    assert "not_before" not in studio.call.call_args.kwargs["json"]


def test_durable_dataset_promotion_uses_the_shared_cli_and_tool_contract(monkeypatch):
    from tinystudio.lab import cli
    calls = []
    monkeypatch.setattr(cli, "call", lambda *args: calls.append(args) or {"id": "op", "state": "queued"})
    result = CliRunner().invoke(app, ["lab", "promote-dataset", "run-1", "artifact-1", "prepared", "--project", "alpha",
                                   "--idempotency-key", "stable-key", "--snapshot-name", "v1"])
    assert result.exit_code == 0, result.output
    expected = {"run_id": "run-1", "artifact_id": "artifact-1", "name": "prepared", "idempotency_key": "stable-key",
                "snapshot_name": "v1", "min_training": 10}
    assert calls == [("POST", "alpha", "/datasets/from-run", expected)]
    from unittest.mock import Mock
    studio = Mock(project="alpha")
    specs = {item.name: item for item in tool_specs(studio)}
    assert specs["lab_promote_dataset"].long_running
    assert not specs["lab_promote_dataset"].read_only
    specs["lab_promote_dataset"].func("run-1", "artifact-1", "prepared", "stable-key", "v1")
    studio.call.assert_called_once_with("POST", "/api/lab/datasets/from-run", json=expected)
