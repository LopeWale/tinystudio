import hashlib
import json
import math
from pathlib import Path
import shutil

import pytest

from tinystudio.lab.modelica import execute_modelica, simulation_script, typed_overrides


def specification(tmp_path):
    source = tmp_path / "decay.mo"
    source.write_text('model Decay\n parameter Real k=1; Real x(start=1,fixed=true); equation der(x)=-k*x; end Decay;\n')
    return {"definition": {"engine": "simulation.openmodelica", "parameters": {"model": "Decay", "output": "x", "samples": 11,
                        "tolerance": 1e-8, "overrides": {"k": {"type": "real", "value": 1}}},
                        "inputs": [{"artifact_id": "source", "role": "model", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]},
            "input_paths": {"source": str(source)}, "_runtime_grants": {"native_execution": True}}


def test_typed_overrides_preserve_negative_decimals_and_reject_injection():
    assert typed_overrides({"k": {"type": "real", "value": -.125}}) == "k=-0.125"
    assert typed_overrides({"enabled": {"type": "boolean", "value": False}}) == "enabled=false"
    for override in ({"x;system(\"bad\")": {"type": "real", "value": 1}}, {"k": {"type": "real", "value": "1;quit()"}},
                     {"k": {"type": "integer", "value": 1.2}}, {"k": {"type": "real", "value": True}}):
        with pytest.raises(ValueError):
            typed_overrides(override)


def test_script_uses_fixed_csv_operation_and_rejects_arbitrary_flags():
    script, metrics = simulation_script({"model": "Package.Decay", "overrides": {"k": {"type": "real", "value": -.125}}})
    assert 'loadFile("model.mo", uses=false)' in script
    assert 'simulate(Package.Decay,' in script
    assert 'outputFormat="csv"' in script
    assert 'simflags="-override=k=-0.125"' in script
    with pytest.raises(ValueError):
        simulation_script({"model": "Decay);system(\"bad\")"})
    with pytest.raises(ValueError, match="unsupported"):
        simulation_script({"model": "Decay", "extra_cli_args": ["--unsafe"]})


def test_authored_grant_does_not_authorize_native_compiler(tmp_path):
    spec = specification(tmp_path)
    del spec["_runtime_grants"]
    spec["definition"]["execution"] = {"grants": {"native_execution": True}}
    with pytest.raises(ValueError, match="trusted"):
        execute_modelica(spec, tmp_path / "out", lambda event: None, lambda: False)


def test_public_dispatch_requires_runner_authority(tmp_path):
    from tinystudio.lab.execution import execute

    spec = specification(tmp_path)
    # Even a direct caller cannot smuggle authority in the authored specification.
    with pytest.raises(ValueError, match="trusted"):
        execute(spec, tmp_path / "out", lambda event: None, lambda: False,
                context={"input_paths": spec["input_paths"]})


@pytest.mark.parametrize("installed", [False, True])
def test_catalog_distinguishes_adapter_from_missing_compiler(monkeypatch, installed):
    from tinystudio.lab.catalog import catalog

    monkeypatch.setattr(shutil, "which", lambda name: "/fixture/omc" if installed and name == "omc" else None)
    entry = next(item for item in catalog() if item["id"] == "simulation.openmodelica")
    assert entry["implemented"] is True
    assert entry["available"] is installed
    assert entry["status"] == ("implemented" if installed else "dependency_missing")
    assert entry["dependency"] == "omc"
    assert entry["qualification"] == "unqualified"
    assert entry["command_contract"]["max_wall_seconds"] == 300
    assert entry["command_contract"]["max_output_bytes"] == 256*1024**2


@pytest.mark.parametrize("planted_output", [None, "symlink", "existing_file"])
def test_compiler_contract_retains_source_and_selects_numeric_csv(monkeypatch, tmp_path, planted_output):
    import tinystudio.lab.modelica as module
    spec = specification(tmp_path)
    source = Path(spec["input_paths"]["source"])
    original = source.read_bytes()
    protected = tmp_path / "protected.jsonl"
    protected.write_text("preserve this evidence")
    binary = tmp_path / "fake-omc"
    binary.write_text("fixture executable identity only")
    monkeypatch.setattr(module.shutil, "which", lambda name: str(binary))
    containment_calls = []
    monkeypatch.setattr(module, "_contained", lambda command, root: containment_calls.append(command) or command)
    def compiler(command, stage, cancelled, remaining, max_bytes):
        stdout, stderr = stage / "stdout.log", stage / "stderr.log"
        stderr.write_text("")
        if "--version" in command:
            stdout.write_text("OpenModelica fixture version (not a runtime qualification)")
        else:
            assert "-override=k=1.0" in (stage.parent / "run.mos").read_text()
            (stage.parent / "result_res.csv").write_text('"time","x"\n0,1\n1,0.36787944117144233\n')
            if planted_output == "symlink":
                (stage.parent / "processed.jsonl").symlink_to(protected)
            elif planted_output == "existing_file":
                (stage.parent / "processed.jsonl").write_text("compiler-owned output")
            stdout.write_text("fixture returned a CSV")
        return stdout, stderr
    monkeypatch.setattr(module, "_bounded_process", compiler)
    if planted_output:
        expected = ValueError if planted_output == "symlink" else FileExistsError
        with pytest.raises(expected):
            execute_modelica(spec, tmp_path / "out", lambda event: None, lambda: False)
        assert protected.read_text() == "preserve this evidence"
        if planted_output == "existing_file":
            assert (tmp_path / "out" / "processed.jsonl").read_text() == "compiler-owned output"
        return
    from tinystudio.lab.execution import execute
    result = execute(spec, tmp_path / "out", lambda event: None, lambda: False,
                     context={"input_paths": spec["input_paths"], "_runtime_grants": {"native_execution": True}})
    assert len(containment_calls) == 2
    rows = [json.loads(line) for line in (tmp_path / "out" / "processed.jsonl").read_text().splitlines()]
    assert [row["value"] for row in rows] == [1, math.exp(-1)]
    assert source.read_bytes() == original
    assert (tmp_path / "out" / "model.mo").read_bytes() == original
    assert result["qualification"]["physical_validation"] is False


def test_installed_openmodelica_decay_fixture(tmp_path):
    if shutil.which("omc") is None:
        pytest.skip("omc not installed; actual OpenModelica compiler/model execution remains unqualified")
    spec = specification(tmp_path)
    result = execute_modelica(spec, tmp_path / "out", lambda event: None, lambda: False)
    rows = [json.loads(line) for line in (tmp_path / "out" / "processed.jsonl").read_text().splitlines()]
    assert rows[0]["value"] == pytest.approx(1)
    assert rows[-1]["time"] == pytest.approx(1)
    assert rows[-1]["value"] == pytest.approx(math.exp(-1), abs=1e-5)
    assert result["qualification"]["runtime_version"].startswith("OpenModelica")
