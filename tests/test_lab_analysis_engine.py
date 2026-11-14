"""Observed analysis produces retained evidence through the actual engine seam."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_lab_analysis_selection import ledger, parameters


def module():
    assert importlib.util.find_spec("tinystudio.lab.analysis") is not None, "observed analysis engine is missing"
    from tinystudio.lab import analysis
    return analysis


def specification(source, params, code=None):
    inputs = [{"artifact_id": "snapshot", "role": "study_snapshot", "visibility": "evaluation",
               "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]
    paths = {"snapshot": str(source)}
    if code is not None:
        inputs.append({"artifact_id": "code", "role": "analysis_code", "visibility": "model",
                       "sha256": hashlib.sha256(code.read_bytes()).hexdigest()})
        paths["code"] = str(code)
    return {"definition": {"name": "Frozen study inference", "engine": "statistics.analyze", "mode": "offline",
                           "operating_mode": "replay", "parameters": params, "inputs": inputs, "pipeline": []},
            "input_paths": paths}


def test_builtin_analysis_retains_effect_interval_selection_and_method_source(tmp_path):
    source, _ = ledger(tmp_path)
    spec = specification(source, parameters(groups=["A", "B"]))
    events = []
    result = module().execute_analysis(spec, tmp_path / "output", events.append, lambda: False)
    roles = {item["role"]: item for item in result["artifacts"]}
    assert {"statistical_analysis", "analysis_selection", "analysis_observations", "analysis_units"} <= roles.keys()
    for artifact in roles.values():
        path = Path(artifact["path"])
        assert path.is_relative_to(tmp_path / "output")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    report = json.loads(Path(roles["statistical_analysis"]["path"]).read_text())
    assert report["format"] == "tinystudio.study-analysis.v1"
    assert report["result"]["estimate"] == -1
    assert report["result"]["interval"]["lower"] < -1 < report["result"]["interval"]["upper"]
    assert report["source"]["sha256"] == spec["definition"]["inputs"][0]["sha256"]
    assert report["source"]["through_seq"] == 4
    assert report["selection"]["counts"]["source_records"] == 4
    for ref in report["selection"]["artifacts"]:
        assert ref["sha256"] == roles[ref["role"]]["sha256"]
        assert ref["name"] == roles[ref["role"]]["name"]
    assert report["method"]["id"] == "means.welch" and report["method"]["version"]
    assert report["request"] == spec["definition"]["parameters"]
    assert len(report["source_code_sha256"]) == 64
    assert report["qualification"]["independence"] == "researcher_declared"
    assert events


def custom_params():
    return {"method": "custom.python", "selection": {"mode": "all", "kinds": ["observation"], "record_ids": [], "exclude_ids": []},
            "custom_method": {"name": "Researcher method", "version": "pilot-1"}, "options": {"scale": 2}}


def test_custom_code_requires_operator_grant_not_definition_grants(tmp_path):
    source, _ = ledger(tmp_path)
    code = tmp_path / "method.py"
    marker = tmp_path / "executed"
    code.write_text(f"from pathlib import Path; Path({str(marker)!r}).touch(); print('{{}}')")
    spec = specification(source, custom_params(), code)
    spec["definition"]["parameters"]["native_execution"] = True
    with pytest.raises(ValueError, match="unknown|grant|native"):
        module().execute_analysis(spec, tmp_path / "denied", lambda _: None, lambda: False)
    assert not marker.exists()
    spec["definition"]["parameters"].pop("native_execution")
    with pytest.raises(ValueError, match="operator|grant"):
        module().execute_analysis(spec, tmp_path / "still-denied", lambda _: None, lambda: False)
    assert not marker.exists()


def test_custom_analysis_retains_exact_code_input_and_arbitrary_output_artifact(tmp_path):
    source, _ = ledger(tmp_path)
    code = tmp_path / "method.py"
    code.write_text('''import hashlib,json,pathlib,sys
header=json.loads(sys.stdin.readline())
records=[json.loads(line)["record"] for line in sys.stdin]
out=pathlib.Path(header["output_directory"])/"observations.csv"
out.write_text("value\\n"+"".join(str(row["data"]["response"])+"\\n" for row in records))
print(json.dumps({"sum":sum(row["data"]["response"] for row in records)*header["request"]["options"]["scale"],"artifacts":[{"path":str(out),"name":"observations.csv","media_type":"text/csv"}]}))
''')
    spec = specification(source, custom_params(), code)
    spec["_runtime_grants"] = {"native_execution": True}
    result = module().execute_analysis(spec, tmp_path / "custom", lambda _: None, lambda: False)
    roles = {row["role"]: row for row in result["artifacts"]}
    report = json.loads(Path(roles["statistical_analysis"]["path"]).read_text())
    assert report["result"]["custom_output"]["sum"] == 20
    assert report["method"]["id"] == "custom.python" and report["method"]["version"] == "pilot-1"
    assert report["custom_execution"]["code_sha256"] == hashlib.sha256(code.read_bytes()).hexdigest()
    assert report["qualification"]["method_validation"] == "researcher_supplied"
    produced = next(row for row in result["artifacts"] if row["name"] == "observations.csv")
    assert produced["role"] == "analysis_custom"
    assert Path(produced["path"]).read_text() == "value\n1\n3\n2\n4\n"


def test_corrupted_snapshot_or_code_is_not_executed(tmp_path):
    source, _ = ledger(tmp_path)
    spec = specification(source, parameters(groups=["A", "B"]))
    source.write_bytes(source.read_bytes()+b"corruption")
    with pytest.raises(ValueError, match="hash|checksum|trailing"):
        module().execute_analysis(spec, tmp_path / "corrupt", lambda _: None, lambda: False)


def test_custom_program_gets_retained_auxiliary_inputs_without_ambient_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYSTUDIO_ANALYSIS_AMBIENT_SECRET", "must-not-be-inherited")
    source, _ = ledger(tmp_path)
    aux = tmp_path / "calibration.bin"
    aux.write_bytes(b"retained calibration")
    code = tmp_path / "method.py"
    code.write_text('''import hashlib,json,os,pathlib,sys
header=json.loads(sys.stdin.readline())
aux=header["inputs"][0]
print(json.dumps({"aux_sha":hashlib.sha256(pathlib.Path(aux["path"]).read_bytes()).hexdigest(),
 "ambient":os.getenv("TINYSTUDIO_ANALYSIS_AMBIENT_SECRET"),"operator":os.getenv("METHOD_LABEL")}))
''')
    spec = specification(source, custom_params(), code)
    ref = {"artifact_id": "calibration", "role": "analysis_auxiliary", "visibility": "evaluation",
           "sha256": hashlib.sha256(aux.read_bytes()).hexdigest()}
    spec["definition"]["inputs"].append(ref)
    spec["input_paths"]["calibration"] = str(aux)
    spec["_runtime_grants"] = {"native_execution": True, "environment": {"METHOD_LABEL": "pilot"}}
    result = module().execute_analysis(spec, tmp_path / "output", lambda _: None, lambda: False)
    report = json.loads(Path(next(row["path"] for row in result["artifacts"] if row["role"] == "statistical_analysis")).read_text())
    assert report["result"]["custom_output"] == {"aux_sha": ref["sha256"], "ambient": None, "operator": "pilot"}
    assert report["custom_execution"]["auxiliary_inputs"] == [{key: ref[key] for key in ("artifact_id", "sha256", "role")}]
    assert report["custom_execution"]["public_environment_keys"] == ["METHOD_LABEL"]


def test_cancelling_custom_program_reaps_its_process(tmp_path):
    import os
    from tinystudio.lab.engine import ExecutionCancelled

    source, _ = ledger(tmp_path)
    pidfile = tmp_path / "pid"
    code = tmp_path / "method.py"
    code.write_text(f'import os,pathlib,time; pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)')
    spec = specification(source, custom_params(), code)
    spec["_runtime_grants"] = {"native_execution": True}
    with pytest.raises(ExecutionCancelled):
        module().execute_analysis(spec, tmp_path / "output", lambda _: None, pidfile.exists)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


@pytest.mark.parametrize("output", [{"artifacts": [{"path": "../analysis_custom_input.jsonl"}]},
    {"artifacts": [{"path": "owned.txt", "name": "statistical_analysis.json"}]}])
def test_custom_artifacts_must_be_owned_and_unambiguous(tmp_path, output):
    source, _ = ledger(tmp_path)
    code = tmp_path / "method.py"
    code.write_text('import pathlib; pathlib.Path("owned.txt").write_text("custom output"); print('+repr(json.dumps(output))+')')
    spec = specification(source, custom_params(), code)
    spec["_runtime_grants"] = {"native_execution": True}
    with pytest.raises(ValueError, match="escapes|reserved|unique"):
        module().execute_analysis(spec, tmp_path / "output", lambda _: None, lambda: False)


@pytest.mark.parametrize("sidecar", ["analysis_custom_input.jsonl", "analysis_observations.jsonl", "analysis_units.jsonl"])
def test_custom_method_cannot_replace_prepared_provenance(sidecar, tmp_path):
    source, _ = ledger(tmp_path)
    code = tmp_path / "method.py"
    code.write_text(f'import pathlib; pathlib.Path("../{sidecar}").write_text("replaced"); print("{{}}")')
    spec = specification(source, custom_params(), code)
    spec["_runtime_grants"] = {"native_execution": True}
    with pytest.raises(ValueError, match="changed.*evidence|integrity"):
        module().execute_analysis(spec, tmp_path / "output", lambda _: None, lambda: False)


def test_custom_output_exact_integer_precision_is_explicit(tmp_path):
    source, _ = ledger(tmp_path)
    code = tmp_path / "method.py"
    code.write_text(f'print("{{\\"exact\\":{2**53+1}}}")')
    spec = specification(source, custom_params(), code)
    spec["_runtime_grants"] = {"native_execution": True}
    with pytest.raises(ValueError, match="decimal string"):
        module().execute_analysis(spec, tmp_path / "output", lambda _: None, lambda: False)
