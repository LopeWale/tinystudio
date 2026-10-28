"""Optional domain qualifications use actual installed scientific solvers when selected."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import zipfile

import pytest

from tinystudio.lab.domain_adapters import execute_domain, _commands, _copy_inputs, _fmu_check


def model_spec(root, adapter, role, payload):
    source = root / "uploaded-model"
    source.write_text(json.dumps(payload))
    return {"definition": {"engine": adapter, "inputs": [{"artifact_id": "model", "role": role,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]},
            "input_paths": {"model": str(source)}}


def optional_python(package):
    executable = os.environ.get("TINYSTUDIO_LAB_QUALIFICATION_PYTHON")
    if executable:
        return executable
    if importlib.util.find_spec(package) is None:
        pytest.skip(f"optional {package} absent; operation remains unqualified")
    return None


def execute(root, spec, package):
    result = execute_domain(spec, root / "out", lambda event: None, lambda: False,
                            python_executable=optional_python(package))
    rows = [json.loads(line) for line in (root / "out" / "processed.jsonl").read_text().splitlines()]
    return result, rows


def test_powerflow_user_artifact_matches_resistive_two_bus_solution(tmp_path):
    # Pure resistance, unity power factor: Vpu solves v^2-v+RP/Vll^2=0.
    spec = model_spec(tmp_path, "simulation.pandapower", "network", {
        "buses_kv": [20, 20], "load_mw": .1, "load_mvar": 0,
        "line": {"length_km": 1, "r_ohm_per_km": .1, "x_ohm_per_km": 0,
                 "c_nf_per_km": 0, "max_i_ka": 1}})
    original = (tmp_path / "uploaded-model").read_bytes()
    result, rows = execute(tmp_path, spec, "pandapower")
    assert rows[0]["value"] == pytest.approx(1)
    assert rows[1]["value"] == pytest.approx((1 + math.sqrt(1 - 4*.1*.1/20**2))/2, abs=1e-9)
    assert rows[1]["supplied_mw"] > .1  # Positive ohmic losses.
    assert result["metrics"]["converged"] is True
    assert (tmp_path / "uploaded-model").read_bytes() == original
    assert (tmp_path / "out" / "network.json").read_bytes() == original
    assert result["qualification"]["physical_validation"] is False


def test_battery_constant_current_charge_integral(tmp_path):
    spec = model_spec(tmp_path, "simulation.pybamm", "parameters", {"Current function [A]": .5})
    spec["definition"]["parameters"] = {"duration": 10, "samples": 5, "timeout_seconds": 60}
    result, rows = execute(tmp_path, spec, "pybamm")
    assert len(rows) == 5
    assert [row["time"] for row in rows] == [0, 2.5, 5, 7.5, 10]
    assert rows[-1]["discharge_capacity_ah"] == pytest.approx(.5*10/3600, abs=1e-9)
    assert all(2 < row["value"] < 5 for row in rows)
    assert rows[-1]["value"] < rows[0]["value"]
    assert result["metrics"]["telemetry"] is False


@pytest.mark.parametrize("adapter", ["embedded.tinygo", "embedded.renode", "embedded.qemu", "simulation.verilator",
                                      "simulation.energyplus", "simulation.fmi", "simulation.matlab", "robotics.gazebo"])
def test_authored_native_grants_never_self_authorize(tmp_path, adapter):
    with pytest.raises(ValueError, match="trusted runner grant"):
        execute_domain({"definition": {"engine": adapter, "execution": {"grants": {"native_execution": True}}}},
                       tmp_path, lambda event: None, lambda: False)


def test_native_input_hash_mismatch_rejected(tmp_path):
    spec = model_spec(tmp_path, "simulation.pandapower", "network", {})
    spec["definition"]["inputs"][0]["sha256"] = "0"*64
    target = tmp_path / "copies"
    target.mkdir()
    with pytest.raises(ValueError, match="checksum"):
        _copy_inputs(spec, target)


def test_fmu_zip_traversal_and_expansion_validation(tmp_path):
    bad = tmp_path / "bad.fmu"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("modelDescription.xml", "<fmiModelDescription/>")
        archive.writestr("../outside", "invalid")
    with pytest.raises(ValueError, match="unsafe"):
        _fmu_check(bad)


def test_fixed_verilator_operation_builds_and_runs_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: "/tools/" + name)
    commands, _ = _commands("simulation.verilator", {"top_module": "testbench"}, {"source": tmp_path / "source.sv"}, tmp_path)
    assert commands[0][1:3] == ["--binary", "--timing"]
    assert commands[1] == [str(tmp_path / "obj_dir" / "simulation")]
    with pytest.raises(ValueError, match="invalid"):
        _commands("simulation.verilator", {"top_module": "bad;system"}, {"source": tmp_path / "source.sv"}, tmp_path)


def test_qemu_fixed_boot_contract_disables_network_and_host_monitor(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: "/tools/" + name)
    commands, _ = _commands("embedded.qemu", {}, {"firmware": tmp_path / "firmware.bin"}, tmp_path)
    command = commands[0]
    assert command[command.index("-nic")+1] == "none"
    assert command[command.index("-monitor")+1] == "none"
    assert command[command.index("-bios")+1] == str(tmp_path / "firmware.bin")


def test_wokwi_requires_separate_cloud_grant_and_runtime_secret(tmp_path):
    spec = {"definition": {"engine": "embedded.wokwi_tinygo"}, "_runtime_grants": {"native_execution": True}}
    with pytest.raises(ValueError, match="cloud_simulation"):
        execute_domain(spec, tmp_path, lambda event: None, lambda: False)
    spec["_runtime_grants"]["cloud_simulation"] = True
    with pytest.raises(ValueError, match="runtime CI token"):
        execute_domain(spec, tmp_path, lambda event: None, lambda: False)


def test_matlab_requires_separate_license_grant(tmp_path):
    with pytest.raises(ValueError, match="licensed_matlab"):
        execute_domain({"definition": {"engine": "simulation.matlab"}, "_runtime_grants": {"native_execution": True}},
                       tmp_path, lambda event: None, lambda: False)


def test_fmu_uploaded_fixture_when_explicitly_provided(tmp_path):
    fixture = os.environ.get("TINYSTUDIO_FMU_QUALIFICATION_FIXTURE")
    output = os.environ.get("TINYSTUDIO_FMU_QUALIFICATION_OUTPUT")
    if not fixture or not output:
        pytest.skip("no local platform-compatible FMU/output fixture supplied; FMU runtime remains unqualified")
    path = Path(fixture)
    spec = {"definition": {"engine": "simulation.fmi", "parameters": {"output": output, "duration": 1},
                           "inputs": [{"artifact_id": "model", "role": "fmu", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]},
            "input_paths": {"model": str(path)}, "_runtime_grants": {"native_execution": True}}
    result, rows = execute(tmp_path, spec, "fmpy")
    assert rows and all(math.isfinite(row["value"]) for row in rows)
    assert result["qualification"]["executed"] is True


@pytest.mark.parametrize("adapter", ["embedded.tinygo", "embedded.wokwi_tinygo", "embedded.renode", "embedded.qemu",
                                      "simulation.verilator", "simulation.energyplus", "network.mininet", "network.ns3",
                                      "robotics.gazebo", "simulation.matlab"])
def test_native_domain_qualification_with_explicit_local_fixture(tmp_path, adapter):
    from tinystudio.lab.domain_adapters import CLI_TARGETS
    if shutil.which(CLI_TARGETS[adapter]) is None:
        pytest.skip(f"{CLI_TARGETS[adapter]} not installed; target execution remains unqualified")
    folder = os.environ.get("TINYSTUDIO_DOMAIN_QUALIFICATION_DIR")
    if not folder:
        pytest.skip("no explicit native qualification fixture directory supplied")
    fixture_path = Path(folder) / (adapter + ".json")
    if not fixture_path.is_file():
        pytest.skip("no immutable local fixture and independently expected observation supplied for this target")
    fixture = json.loads(fixture_path.read_text())
    spec = fixture["spec"]  # Trusted harness context, including separately authorized runtime grants.
    assert spec["definition"]["engine"] == adapter
    expected = fixture["expected"]
    assert "text_contains" in expected or "artifact" in expected
    result = execute_domain(spec, tmp_path / "out", lambda event: None, lambda: False)
    assert result["records"] > 0
    assert result["qualification"]["physical_validation"] is False
    if "text_contains" in expected:
        assert expected["text_contains"] in (tmp_path / "out" / "processed.jsonl").read_text()
    if "artifact" in expected:
        artifact = next(item for item in result["artifacts"] if item["name"] == expected["artifact"])
        assert Path(artifact["path"]).stat().st_size > 0
        assert hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest() == artifact["sha256"]
