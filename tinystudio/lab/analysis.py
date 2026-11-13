"""Observed study inference and explicitly granted researcher-supplied methods."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import platform
import sys
import time

from tinystudio.config import NativeProcessSettings
from . import analysis_methods
from .analysis_selection import ANALYSIS_FORMAT, CUSTOM_METHOD, _check, _object, implementation_sha256, prepare_selection, request_schema, validate_analysis_selection
from .contracts import canonical, checksum
from .engine import digest
from .study_contracts import finite_numbers


def method_catalog():
    methods = [*analysis_methods.method_catalog(), {
        "id": CUSTOM_METHOD, "version": "1.0.0", "label": "Custom retained Python analysis",
        "description": "Execute researcher-supplied method source with frozen selected observations and optional auxiliary artifacts.",
        "assumptions": ["Method validity, numerical choices and dependency declarations belong to the researcher.",
                        "The selected runner must explicitly grant native execution for statistics.analyze.",
                        "The program reads a JSONL manifest followed by selected records from stdin and writes one finite JSON result to stdout."],
        "references": [], "dependencies": [],
        "extension_contract": {"input_format": "tinystudio.custom-analysis-input.v1", "code_role": "analysis_code",
            "auxiliary_role": "analysis_auxiliary", "operator_grant": "native_execution",
            "outputs": "Optional artifacts array: path within output_directory, name and media_type; files are retained with measured hashes."},
    }]
    for method in methods:
        method["parameters_schema"] = request_schema(method["id"])
    return {"format": "tinystudio.analysis-methods.v1", "methods": methods,
        "selection_semantics": ["Record counts do not establish independent sample counts.",
            "Corrections are retained; select or exclude originals explicitly.",
            "Built-in contrasts use group A minus group B after the declared unit reduction; the measurement unit is researcher declared.",
            "Built-in decimal outcomes use disclosed float64 conversion; original values remain retained.",
            "The custom method controls its own estimand, numerical precision and inference assumptions."]}


def _artifact(path, role, media_type):
    return {"name": path.name, "path": str(path), "role": role, "media_type": media_type, "sha256": digest(path)}


def _bound_inputs(spec, cancelled):
    result = []
    for ref in spec["definition"].get("inputs", []):
        _check(cancelled)
        path = Path(spec.get("input_paths", {}).get(ref["artifact_id"], ""))
        if not path.is_file() or not ref.get("sha256") or digest(path) != ref["sha256"]:
            raise ValueError("analysis input hash mismatch or missing immutable input binding")
        result.append((ref, path))
    return result


def _environment(spec):
    environment = {key: value for key, value in os.environ.items() if key in
                   {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "HF_HOME", "HF_HUB_CACHE", "HF_HUB_OFFLINE", "TOKENIZERS_PARALLELISM"}}
    public = spec.get("_runtime_grants", {}).get("environment", {})
    secrets = spec.get("_runtime_secrets", {}).get("environment", {})
    for values in (public, secrets):
        if not isinstance(values, dict) or any(not isinstance(key, str) or not key or "=" in key or "\x00" in key
            or not isinstance(value, str) or "\x00" in value for key, value in values.items()):
            raise ValueError("operator custom-analysis environment requires string names and values")
        environment.update(values)
    return environment, {"public_environment_keys": sorted(public), "secret_environment_keys": sorted(secrets)}


def _custom(spec, prepared, inputs, cancelled):
    from .analysis_process import AnalysisProcess

    paths, request = prepared.paths, prepared.request
    code_ref, code = next((ref, path) for ref, path in inputs if ref["role"] == "analysis_code")
    paths.custom_outputs.mkdir()
    environment, environment_receipt = _environment(spec)
    header = {"format": "tinystudio.custom-analysis-input.v1", "kind": "manifest", "source": prepared.source,
              "request": request, "selection": prepared.counts, "output_directory": str(paths.custom_outputs.resolve()),
              "inputs": [{"artifact_id": ref["artifact_id"], "role": ref["role"], "sha256": ref["sha256"],
                          "path": str(path.resolve()), "visibility": ref.get("visibility", "model")} for ref, path in inputs if ref["role"] == "analysis_auxiliary"]}
    with paths.custom_input.open("xb") as stream:
        stream.write(canonical(header)+b"\n")
        with paths.observations.open("rb") as records:
            for line in records:
                _check(cancelled)
                stream.write(b'{"kind":"record","record":'+line.rstrip(b"\n")+b'}\n')
    frozen = {path: digest(path) for path in (paths.custom_input, paths.observations, paths.selection, paths.units)}
    frozen.update({path: ref["sha256"] for ref, path in inputs})
    with paths.custom_input.open("rb") as source, paths.custom_output.open("xb") as output:
        group = AnalysisProcess([sys.executable, "-I", str(code.resolve())], stdin=source, stdout=output,
                             env=environment, cwd=paths.custom_outputs)
        poll_seconds = NativeProcessSettings.from_environment().poll_seconds
        try:
            code = group.process.poll()
            while code is None:
                _check(cancelled)
                time.sleep(poll_seconds)
                code = group.process.poll()
            group.close()
            _check(cancelled)
            if code != 0:
                raise ValueError(f"custom analysis program exited with status {code}; its diagnostic stream is in the runner log")
        finally:
            group.close()
    for path, expected in frozen.items():
        _check(cancelled)
        if not path.is_file() or path.is_symlink() or digest(path) != expected:
            raise ValueError("custom method changed retained analysis input or selection evidence; integrity cannot be confirmed")
    try:
        custom_output = json.loads(paths.custom_output.read_text(encoding="utf-8"), object_pairs_hook=_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("custom analysis returned nonfinite JSON")))
        canonical(custom_output)
        finite_numbers(custom_output)
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError("custom analysis must emit one finite scientific JSON result; use decimal strings for exact integers outside JSON's safe range") from error
    artifacts = []
    declared = custom_output.get("artifacts", []) if isinstance(custom_output, dict) else []
    if not isinstance(declared, list):
        raise ValueError("custom output artifacts must be an array")
    names = {path.name for path in (paths.observations, paths.selection, paths.units, paths.report,
                                    paths.custom_input, paths.custom_output)}
    for item in declared:
        _check(cancelled)
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("custom output artifact requires a path")
        path = Path(item["path"])
        path = path if path.is_absolute() else paths.custom_outputs / path
        if path.is_symlink() or not path.resolve().is_relative_to(paths.custom_outputs.resolve()) or not path.is_file():
            raise ValueError("custom output artifact escapes the owned output directory")
        name = item.get("name", path.name)
        media_type = item.get("media_type", "application/octet-stream")
        if not isinstance(name, str) or not name or Path(name).name != name or name in names or not isinstance(media_type, str):
            raise ValueError("custom output artifact names must be unique filenames with a media type and cannot use reserved analysis filenames")
        names.add(name)
        artifact = _artifact(path, "analysis_custom", media_type)
        artifact["name"] = name
        artifacts.append(artifact)
    descriptor = request["custom_method"]
    method = {"id": CUSTOM_METHOD, "name": descriptor["name"], "version": descriptor["version"],
              "assumptions": descriptor.get("assumptions", []), "references": descriptor.get("references", [])}
    receipt = {"code_sha256": code_ref["sha256"], "input_sha256": frozen[paths.custom_input],
               "output_sha256": digest(paths.custom_output), "python": platform.python_version(),
               "auxiliary_inputs": [{key: ref[key] for key in ("artifact_id", "sha256", "role")} for ref, _ in inputs if ref["role"] == "analysis_auxiliary"],
               **environment_receipt}
    return {"custom_output": custom_output, "counts": {"selected_records": prepared.counts["selected_records"]}}, method, receipt, artifacts


def execute_analysis(spec, output_dir, emit, cancelled):
    request = validate_analysis_selection(spec["definition"])
    _check(cancelled)
    custom = request["method"] == CUSTOM_METHOD
    if custom and spec.get("_runtime_grants", {}).get("native_execution") is not True:
        raise ValueError("custom analysis requires the operator's native_execution grant for statistics.analyze")
    inputs = _bound_inputs(spec, cancelled)
    _, snapshot = next((ref, path) for ref, path in inputs if ref["role"] == "study_snapshot")
    prepared = prepare_selection(snapshot, request, output_dir, cancelled)
    emit({"kind": "analysis.selection", "counts": prepared.counts, "source_sha256": prepared.source["sha256"]})
    custom_execution, extra = None, []
    if custom:
        result, method, custom_execution, extra = _custom(spec, prepared, inputs, cancelled)
    else:
        result = analysis_methods.calculate(request["method"], prepared.group_a, prepared.group_b,
            confidence=request["confidence"], alternative=request["alternative"], cancelled=cancelled)
        method = deepcopy(analysis_methods.METHODS[request["method"]])
    _check(cancelled)
    qualification = {"kind": "custom_analysis_execution" if custom else "observed_data_inference",
        "independence": "researcher_declared", "method_validation": "researcher_supplied" if custom else "implemented_formula",
        "scope": "Retained observations and explicit selected method assumptions; no automatic causal, stopping-rule or physical-fidelity qualification."}
    selection_artifacts = [_artifact(prepared.paths.selection, "analysis_selection", "application/x-ndjson"),
                           _artifact(prepared.paths.observations, "analysis_observations", "application/x-ndjson"),
                           _artifact(prepared.paths.units, "analysis_units", "application/x-ndjson")]
    report = {"format": ANALYSIS_FORMAT, "source": prepared.source, "request": request, "request_sha256": checksum(request),
        "method": method, "result": result, "selection": {"counts": prepared.counts, "diagnostics": prepared.diagnostics,
            "artifacts": [{key: item[key] for key in ("name", "role", "sha256")} for item in selection_artifacts]},
        "source_code_sha256": implementation_sha256(), "runtime": result.get("runtime", {"python": platform.python_version()}),
        "qualification": qualification, "inputs": [ref for ref, _ in inputs]}
    if custom_execution is not None:
        report["custom_execution"] = custom_execution
    with prepared.paths.report.open("xb") as stream:
        stream.write(canonical(report)+b"\n")
    artifacts = [_artifact(prepared.paths.report, "statistical_analysis", "application/json"), *selection_artifacts]
    if custom:
        artifacts += [_artifact(prepared.paths.custom_input, "analysis_custom_input", "application/x-ndjson"),
                      _artifact(prepared.paths.custom_output, "analysis_custom_output", "application/json"), *extra]
    metrics = {"declared_group_a_units": prepared.counts["group_a"], "declared_group_b_units": prepared.counts["group_b"]}
    if not custom and result["estimate"] is not None:
        metrics["estimate"] = result["estimate"]
    return {"records": prepared.counts["used_records"], "artifacts": artifacts, "metrics": metrics,
        "analysis": {"source": prepared.source, "method": {key: method[key] for key in ("id", "version")},
                     "selection": prepared.counts, "qualification": qualification}, "qualification": qualification}
