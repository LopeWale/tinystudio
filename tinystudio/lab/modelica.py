"""Standalone uploaded .mo -> bounded omc compile/simulate -> numeric CSV adapter.

Engine simulation.openmodelica accepts one immutable role=model artifact, typed
parameters under overrides {name:{type:real|integer|boolean,value:...}}, and a
selected model/output. Trusted runner _runtime_grants.native_execution is required;
authored grants cannot authorize native callbacks. No library/model downloads.
"""
from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time

from .adapters import _bounded_process
from .domain_adapters import _contained
from .engine import ExecutionCancelled, digest
from .signals import integer, number

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def typed_overrides(supplied):
    if not isinstance(supplied, dict) or len(supplied) > 100:
        raise ValueError("overrides must be a map of at most 100 typed parameters")
    if any(not isinstance(name, str) for name in supplied):
        raise ValueError("Modelica parameter names must be strings")
    result = []
    for name, parameter in sorted(supplied.items()):
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name) or len(name) > 200:
            raise ValueError("invalid Modelica parameter identifier")
        if not isinstance(parameter, dict) or set(parameter) != {"type", "value"}:
            raise ValueError("Modelica overrides require explicit type and value")
        kind, value = parameter["type"], parameter["value"]
        if kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError("boolean override requires a boolean")
            encoded = "true" if value else "false"
        elif kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int) or abs(value) > 2**31-1:
                raise ValueError("integer override requires a bounded integer")
            encoded = str(value)
        elif kind == "real":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("real override requires a finite numeric value")
            encoded = repr(float(value))
        else:
            raise ValueError("only real/integer/boolean Modelica parameters are supported")
        result.append(f"{name}={encoded}")
    return ",".join(result)


def simulation_script(p):
    allowed = {"model", "output", "units", "overrides", "start_time", "stop_time", "samples", "tolerance", "solver", "timeout_seconds", "max_output_bytes"}
    if set(p) - allowed:
        raise ValueError("unsupported OpenModelica operation parameter")
    model = p.get("model")
    if not isinstance(model, str) or len(model) > 200 or not IDENTIFIER.fullmatch(model):
        raise ValueError("model must name a qualified Modelica class")
    output = p.get("output", "x")
    if not isinstance(output, str) or not 1 <= len(output) <= 200:
        raise ValueError("one declared numeric output name is required")
    start = number(p.get("start_time", 0), "start_time")
    stop = number(p.get("stop_time", 1), "stop_time")
    if not 0 < stop-start <= 86400:
        raise ValueError("simulation interval must be positive and <=86400 seconds")
    samples = integer(p.get("samples", 101), "samples", 2, 10000)
    tolerance = number(p.get("tolerance", 1e-6), "tolerance", minimum=1e-12)
    if tolerance > 1e-2:
        raise ValueError("solver tolerance exceeds 1e-2")
    solver = p.get("solver", "dassl")
    if solver not in {"dassl", "ida", "euler", "rungekutta"}:
        raise ValueError("unsupported OpenModelica solver")
    overrides = typed_overrides(p.get("overrides", {}))
    flags = "-override=" + overrides if overrides else ""
    script = (f'loadFile("model.mo", uses=false);\ngetErrorString();\n'
              f'simulate({model},startTime={start},stopTime={stop},numberOfIntervals={samples-1},'
              f'tolerance={tolerance},method={json.dumps(solver)},outputFormat="csv",'
              f'fileNamePrefix="result",simflags={json.dumps(flags)});\ngetErrorString();\n')
    return script, {"model": model, "output": output, "solver": solver, "tolerance": tolerance,
                    "start_time": start, "stop_time": stop, "requested_samples": samples}


def execute_modelica(spec, output_dir, emit, cancelled):
    definition = spec["definition"]
    if definition.get("engine") != "simulation.openmodelica":
        raise ValueError("standalone Modelica requires engine simulation.openmodelica")
    if definition.get("mode", "offline") != "offline" or definition.get("operating_mode", "simulation") != "simulation":
        raise ValueError("OpenModelica supports offline simulation only")
    if spec.get("_runtime_grants", {}).get("native_execution") is not True:
        raise ValueError("uploaded Modelica requires an explicit trusted native_execution grant")
    if definition.get("pipeline"):
        raise ValueError("OpenModelica adapter does not silently apply unsupported pipeline stages")
    inputs = definition.get("inputs", [])
    if len(inputs) != 1 or inputs[0].get("role") != "model":
        raise ValueError("one immutable model artifact is required")
    p = definition.get("parameters", {})
    script, metrics = simulation_script(p)
    seconds = integer(p.get("timeout_seconds", 60), "timeout_seconds", 1, 300)
    max_bytes = integer(p.get("max_output_bytes", 64*1024**2), "max_output_bytes", 1024, 256*1024**2)
    binary = shutil.which("omc")
    if not binary:
        raise RuntimeError("optional OpenModelica compiler omc is not installed; execution unqualified")
    supplied = spec.get("input_paths", {}).get(inputs[0]["artifact_id"])
    source = Path(supplied["path"] if isinstance(supplied, dict) else supplied or "")
    if not source.is_file() or source.stat().st_size > 1024**2:
        raise ValueError("Modelica source missing or exceeds 1 MiB")
    source.read_text(encoding="utf-8")  # Reject binary/invalid text before invoking a compiler.
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("output directory must be empty")
    if cancelled():
        raise ExecutionCancelled("Modelica execution cancelled")
    model = root / "model.mo"
    shutil.copyfile(source, model)
    if digest(model) != inputs[0]["sha256"]:
        raise ValueError("immutable Modelica source checksum mismatch")
    mos = root / "run.mos"
    mos.write_text(script)
    started = time.monotonic()
    def guarded_cancelled():
        if time.monotonic() - started > seconds:
            raise TimeoutError("OpenModelica execution exhausted wall-time budget")
        if cancelled():
            return True
        size = 0
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("OpenModelica output contains a symlink")
            try:
                if path.is_file() and path != model:
                    size += path.stat().st_size
            except FileNotFoundError:
                continue  # Compilers can remove their own temporary files between directory reads.
        if size > max_bytes:
            raise ValueError("OpenModelica output exceeded its byte budget")
        return False
    emit({"event": "adapter_started", "adapter": "simulation.openmodelica"})
    version = None
    for index, arguments in enumerate((["--version"], [str(mos)])):
        stage = root / f"stage-{index}"
        stage.mkdir()
        command = [sys.executable, "-c", "import os,sys; os.chdir(sys.argv[1]); os.execv(sys.argv[2],sys.argv[2:])", str(root), binary, *arguments]
        remaining = seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("OpenModelica execution exhausted wall-time budget")
        stdout, stderr = _bounded_process(_contained(command, root), stage, guarded_cancelled, remaining, max_bytes)
        if index == 0:
            version = stdout.read_text().strip()
            if not version or len(version) > 4096:
                raise RuntimeError("omc did not report a usable runtime version")
    # Compiler descendants have stopped. Validate the whole output tree before
    # reading results or creating derived files, including compiler-created links.
    if guarded_cancelled():
        raise ExecutionCancelled("Modelica execution cancelled")
    if digest(model) != inputs[0]["sha256"]:
        raise ValueError("OpenModelica mutated the immutable source copy")
    result = root / "result_res.csv"
    if not result.is_file():
        raise RuntimeError("OpenModelica did not produce result_res.csv; inspect compiler diagnostics")
    records = root / "processed.jsonl"
    count = 0
    with result.open() as stream, records.open("x") as output_stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "time" not in reader.fieldnames or metrics["output"] not in reader.fieldnames:
            raise ValueError("OpenModelica CSV does not contain requested time/output columns")
        last_time = -math.inf
        for values in reader:
            if guarded_cancelled():
                raise ExecutionCancelled("Modelica execution cancelled")
            timestamp = number(values["time"], "simulation time")
            if timestamp < last_time:
                raise ValueError("OpenModelica output time moved backwards")
            last_time = timestamp
            count += 1
            if count > 100000:
                raise ValueError("OpenModelica output exceeds 100000 observations")
            row = {"record_id": f"e0:r{count-1}", "episode_id": "e0", "time": timestamp, "available_at": timestamp,
                   "value": number(values[metrics["output"]], metrics["output"]), "units": str(p.get("units", "model-defined")),
                   "observed": True, "quality": "simulated", "model": metrics["model"], "output": metrics["output"],
                   "input_artifact_ids": [inputs[0]["artifact_id"]]}
            output_stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    if count == 0:
        raise ValueError("OpenModelica produced no observations")
    if guarded_cancelled():
        raise ExecutionCancelled("Modelica execution cancelled")
    artifacts = []
    for path in sorted(root.rglob("*")):
        if guarded_cancelled():
            raise ExecutionCancelled("Modelica execution cancelled")
        if path.is_file():
            artifacts.append({"name": str(path.relative_to(root)), "path": str(path), "sha256": digest(path),
                              "media_type": "application/x-ndjson" if path == records else "text/csv" if path == result else "application/octet-stream",
                              "role": "original" if path == model else "derived"})
    emit({"event": "progress", "records": count})
    return {"records": count, "artifacts": artifacts, "metrics": metrics,
            "qualification": {"engine": "simulation.openmodelica", "executed": True, "physical_validation": False,
                              "runtime_version": version, "compiler_sha256": digest(Path(binary)),
                              "source_version": "sha256:" + digest(Path(__file__)), "network_fetches": False,
                              "native_grant_used": True, **metrics}}
