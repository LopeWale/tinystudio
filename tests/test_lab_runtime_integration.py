"""Runtime measurements use the existing catalog, selection and execution routes."""
import json

from tinystudio.lab import service
from tinystudio.lab.contracts import ExperimentDefinition
from tinystudio.lab.execution import execute
from tinystudio.lab.preflight import execution_checks
from tinystudio.lab.runner import capabilities


def definition():
    return {"name": "Measure selected runtime", "engine": "runtime.qualify", "domain": "runtime",
        "parameters": {"method": "nf4.training", "objective": "sft", "device": "cpu", "dtype": "float32",
                       "fixture": "tiny_llama", "nf4_execution": {"cpu_inference": "portable"}},
        "execution": {"profile": "trusted_native", "backend": "native", "max_seconds": 60}}


def test_probe_catalog_is_available_to_report_unavailable_optional_dependencies(monkeypatch):
    import importlib.util
    discover = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "torch" else discover(name))
    row = next((item for item in service.catalog()["methods"] if item["id"] == "runtime.qualify"), None)
    assert row is not None, "runtime measurement engine is not registered"
    assert row["implemented"] and row["available"] and row["dependencies"] == []
    assert row["manifest"]["implementation_version"].startswith("sha256:")


def test_shared_dispatch_retains_unavailable_measurement_without_false_qualification(tmp_path, monkeypatch):
    from tinystudio.training.runtime_compatibility import RuntimeInventory, RuntimePrerequisites
    from tinystudio.lab import runtime_probe
    inventory = RuntimeInventory.capture()
    monkeypatch.setattr(runtime_probe.RuntimeCompatibility, "inspect", classmethod(lambda cls, **kwargs:
        RuntimePrerequisites("unavailable", "Measured fixture cannot run: bitsandbytes absent", inventory,
                             missing_dependencies=("bitsandbytes",))))
    output = tmp_path / "evidence"
    output.mkdir()
    profile = {"backend": "container", "profile": "isolated", "image": "operator-selected@sha256:"+"a"*64}
    result = execute({"definition": definition(), "research_identity": "retained-run",
        "_runtime_context": {"profile": {"backend": "forged"}}}, output, lambda _: None, lambda: False,
        context={"spec_digest": "b"*64, "profile_manifest": profile,
                 "_runtime_secrets": {"environment": {"PRIVATE_TOKEN": "must-not-be-retained"}}})
    artifact = next(row for row in result["artifacts"] if row["role"] == "runtime_probe")
    report = json.loads(open(artifact["path"]).read())
    assert report["status"] == "unavailable" and report["qualification"]["qualified"] is False
    assert result["qualification"]["qualified"] is False
    assert report['execution_context']['profile'] == profile
    assert report['execution_context']['spec_digest'] == 'b'*64
    assert report['execution_context']['research_identity'] == 'retained-run'
    assert 'PRIVATE_TOKEN' not in json.dumps(report) and 'must-not-be-retained' not in json.dumps(report)


def test_preflight_validates_requested_probe_without_asserting_kernel_support():
    body = definition()
    selected = capabilities()
    checks = execution_checks(ExperimentDefinition.model_validate(body), selected)
    probe = next((row for row in checks if row["code"] == "runtime_probe_selection"), None)
    assert probe is not None and probe["passed"], checks
    body["parameters"]["objective"] = "invented"
    checks = execution_checks(ExperimentDefinition.model_validate(body), selected)
    assert not next(row for row in checks if row["code"] == "runtime_probe_selection")["passed"]
