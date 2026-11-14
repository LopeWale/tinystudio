"""Analysis catalog, shared dispatch and operator authority agree."""
from copy import deepcopy
import json
from pathlib import Path

from tinystudio.lab import service
from tinystudio.lab.contracts import ExperimentDefinition
from tinystudio.lab.execution import execute
from tinystudio.lab.preflight import execution_checks
from tinystudio.lab.runner import capabilities
from tests.test_lab_analysis_engine import specification
from tests.test_lab_analysis_selection import ledger, parameters


def definition(source):
    body = specification(source, parameters(groups=["A", "B"]))["definition"]
    return {**body, "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 30}}


def test_analysis_catalog_exposes_observed_inputs_and_versioned_source():
    rows = service.catalog()["methods"]
    row = next((row for row in rows if row["id"] == "statistics.analyze"), None)
    assert row is not None, "observed analysis is not registered in the Laboratory catalog"
    assert row["implemented"] and row["available"]
    assert row["manifest"]["implementation_version"].startswith("sha256:")
    assert any(port["role"] == "study_snapshot" for port in row["manifest"]["inputs"])
    assert any(port["role"] == "analysis_code" for port in row["manifest"]["inputs"])
    from tinystudio.lab.analysis import method_catalog
    methods = {item["id"]: item for item in method_catalog()["methods"]}
    assert "confidence" in methods["means.welch"]["parameters_schema"]["required"]
    assert "incomplete_pairs" in methods["means.paired"]["parameters_schema"]["required"]
    assert "confidence" not in methods["custom.python"]["parameters_schema"]["required"]
    assert methods["means.welch"]["parameters_schema"]["properties"]["missing"]["enum"] == ["error", "omit"]


def test_actual_engine_dispatch_runs_the_retained_observed_calculation(tmp_path):
    source, _ = ledger(tmp_path)
    spec = specification(source, parameters(groups=["A", "B"]))
    spec["definition"] = definition(source)
    original = deepcopy(spec)
    output = tmp_path / "evidence"
    output.mkdir()
    result = execute(spec, output, lambda _: None, lambda: False, context={"input_paths": spec["input_paths"]})
    report_ref = next(item for item in result["artifacts"] if item["role"] == "statistical_analysis")
    assert json.loads(Path(report_ref["path"]).read_text())["result"]["estimate"] == -1
    assert spec == original


def test_preflight_uses_method_dependencies_and_operator_custom_capability(tmp_path):
    source, _ = ledger(tmp_path)
    body = definition(source)
    selected = capabilities()
    checks = execution_checks(ExperimentDefinition.model_validate(body), selected)
    analysis = next((item for item in checks if item["code"] == "analysis_selection"), None)
    assert analysis is not None and analysis["passed"], checks
    missing = deepcopy(selected)
    missing["runtime_by_backend"]["native"].pop("scipy", None)
    failed = execution_checks(ExperimentDefinition.model_validate(body), missing)
    assert not next(item for item in failed if item["code"] == "analysis_selection")["passed"]
    assert capabilities().get("custom_analysis_python") is False
    granted = capabilities({"schema_version": 1, "backends": {}, "engine_grants": {"statistics.analyze": {"native_execution": True}}})
    assert granted["custom_analysis_python"] is True
