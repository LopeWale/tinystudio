"""Artifact-backed optional domain operations. No artifact downloads or device flashing.

Trusted runner injects _runtime_grants; authored execution.grants never self-authorize
native model callbacks, cloud simulation, licensed execution or host namespaces.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import time
import zipfile

from .adapters import _bounded_process, _record
from .engine import ExecutionCancelled, digest
from .signals import integer, number

PYTHON_TARGETS = {"simulation.pandapower": "pandapower", "simulation.pybamm": "pybamm", "simulation.fmi": "fmpy"}
CLI_TARGETS = {"embedded.tinygo": "tinygo", "embedded.wokwi_tinygo": "wokwi-cli",
               "embedded.renode": "renode", "embedded.qemu": "qemu-system-x86_64",
               "simulation.verilator": "verilator", "simulation.energyplus": "energyplus",
               "network.mininet": "mn", "network.ns3": "ns3", "robotics.gazebo": "gz",
               "simulation.matlab": "matlab"}
INPUT_ROLES = {
    "embedded.tinygo": {"source": ".go"}, "embedded.wokwi_tinygo": {"firmware": ".elf", "diagram": ".json"},
    "embedded.renode": {"firmware": ".elf", "platform": ".repl"}, "embedded.qemu": {"firmware": ".bin"},
    "simulation.verilator": {"source": ".sv"}, "simulation.fmi": {"fmu": ".fmu"},
    "simulation.energyplus": {"model": ".idf", "weather": ".epw"},
    "simulation.matlab": {"model": ".slx"}, "simulation.pandapower": {"network": ".json"},
    "simulation.pybamm": {"parameters": ".json"}, "robotics.gazebo": {"world": ".sdf"}}


def _copy_inputs(spec, root):
    roles = {}
    total = 0
    for item in spec["definition"].get("inputs", []):
        role = item["role"]
        suffix = INPUT_ROLES.get(spec["definition"]["engine"], {}).get(role)
        if suffix is None or role in roles:
            raise ValueError(f"unsupported or duplicate input role: {role}")
        supplied = spec.get("input_paths", {}).get(item["artifact_id"])
        source = Path(supplied["path"] if isinstance(supplied, dict) else supplied or "")
        if not source.is_file() or source.stat().st_size > 128 * 1024**2:
            raise ValueError("input missing or exceeds 128 MiB")
        total += source.stat().st_size
        if total > 256 * 1024**2:
            raise ValueError("input artifacts exceed 256 MiB")
        target = root / (role + suffix)
        shutil.copyfile(source, target)
        if digest(target) != item["sha256"]:
            raise ValueError("immutable input checksum mismatch")
        roles[role] = target
    return roles


def _fmu_check(path):
    with zipfile.ZipFile(path) as archive:
        size = 0
        for entry in archive.infolist():
            pure = PurePosixPath(entry.filename)
            if pure.is_absolute() or ".." in pure.parts or "\\" in entry.filename or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("unsafe FMU archive member")
            size += entry.file_size
            if size > 256 * 1024**2:
                raise ValueError("FMU expands beyond 256 MiB")
        if "modelDescription.xml" not in archive.namelist():
            raise ValueError("FMU modelDescription.xml is missing")


def _contained(command, root, *, allow_network=False):
    """Native uploaded callbacks receive no network and no writes outside their run."""
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists():
        quoted = json.dumps(str(root.resolve()))
        profile = f'(version 1)(allow default){"" if allow_network else "(deny network*)"}(deny file-write*)(allow file-write* (subpath {quoted}))'
        return ["/usr/bin/sandbox-exec", "-p", profile, *command]
    binary = shutil.which("bwrap") if sys.platform.startswith("linux") else None
    if binary:
        return [binary, "--die-with-parent", *([] if allow_network else ["--unshare-net"]), "--ro-bind", "/", "/", "--bind", str(root), str(root), "--dev", "/dev", "--", *command]
    raise RuntimeError("native model execution requires sandbox-exec or bubblewrap containment")


def _configuration(path, fallback):
    if path is None:
        return fallback
    if path.stat().st_size > 1024**2:
        raise ValueError("JSON model exceeds 1 MiB")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("JSON model must be an object")
    return value


def _python_operation(adapter, parameters, roles):
    if adapter == "simulation.pandapower":
        import pandapower as pp
        config = _configuration(Path(roles["network"]) if "network" in roles else None,
                                {"buses_kv": [20, 20], "load_mw": .1, "load_mvar": .02,
                                 "line": {"length_km": 1, "r_ohm_per_km": .1, "x_ohm_per_km": .1, "c_nf_per_km": 0, "max_i_ka": 1}})
        if set(config) - {"buses_kv", "load_mw", "load_mvar", "line"}:
            raise ValueError("unsupported power-network schema field")
        voltages = config.get("buses_kv", [20, 20])
        if not isinstance(voltages, list) or len(voltages) != 2:
            raise ValueError("minimal powerflow adapter requires exactly two buses")
        net = pp.create_empty_network()
        buses = [pp.create_bus(net, vn_kv=number(v, "bus voltage", minimum=1e-3)) for v in voltages]
        pp.create_ext_grid(net, bus=buses[0], vm_pu=1)
        line = config.get("line", {})
        expected = {"length_km", "r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka"}
        if set(line) != expected:
            raise ValueError("line requires length_km/r_ohm_per_km/x_ohm_per_km/c_nf_per_km/max_i_ka")
        pp.create_line_from_parameters(net, buses[0], buses[1], **{key: number(value, key, minimum=0) for key, value in line.items()})
        pp.create_load(net, buses[1], p_mw=number(config.get("load_mw", .1), "load_mw", minimum=0),
                       q_mvar=number(config.get("load_mvar", 0), "load_mvar"))
        pp.runpp(net, algorithm="nr", init="flat", numba=False, max_iteration=30, tolerance_mva=1e-8)
        if not net.converged:
            raise RuntimeError("powerflow did not converge")
        rows = [_record(int(index), float(row.vm_pu), units="pu", bus=int(index),
                        time=0.0, available_at=0.0, time_axis="static_snapshot", angle_degree=float(row.va_degree), supplied_mw=float(net.res_ext_grid.p_mw.iloc[0]))
                for index, row in net.res_bus.iterrows()]
        return rows, {"dependency_version": pp.__version__, "converged": True}
    if adapter == "simulation.pybamm":
        os.environ["PYBAMM_DISABLE_TELEMETRY"] = "true"
        import numpy as np
        import pybamm
        config = _configuration(Path(roles["parameters"]) if "parameters" in roles else None, {})
        allowed = {"Current function [A]", "Ambient temperature [K]", "Initial temperature [K]"}
        if set(config) - allowed:
            raise ValueError("battery artifact supports constant current and temperatures only")
        model = pybamm.lithium_ion.SPM()
        values = model.default_parameter_values
        values.update({key: number(value, key) for key, value in config.items()})
        duration = number(parameters.get("duration", 60), "duration", minimum=1e-6)
        if duration > 86400:
            raise ValueError("battery duration exceeds 86400 seconds")
        count = integer(parameters.get("samples", 20), "samples", 2, 10000)
        points = np.linspace(0, duration, count)
        simulation = pybamm.Simulation(model, parameter_values=values)
        solution = simulation.solve([0, duration])
        if solution.t[-1] < duration:
            raise RuntimeError("battery terminated before requested sample horizon")
        voltage = solution["Voltage [V]"](points)
        capacity = solution["Discharge capacity [A.h]"](points)
        return [_record(i, float(v), units="V", time=float(t), available_at=float(t), discharge_capacity_ah=float(q))
                for i, (t, v, q) in enumerate(zip(points, voltage, capacity))], {
                    "dependency_version": pybamm.__version__, "model": "SPM", "telemetry": False}
    if adapter == "simulation.fmi":
        import fmpy
        path = Path(roles["fmu"])
        _fmu_check(path)
        duration = number(parameters.get("duration", 1), "duration", minimum=1e-9)
        count = integer(parameters.get("samples", 100), "samples", 2, 10000)
        if duration > 86400:
            raise ValueError("FMU duration exceeds 86400 seconds")
        output = parameters.get("output")
        if not isinstance(output, str) or not output or len(output) > 200:
            raise ValueError("FMU requires one declared numeric output name")
        result = fmpy.simulate_fmu(str(path), stop_time=duration, output_interval=duration/(count-1),
                                   output=[output], validate=True, remote_platform=None, timeout=number(parameters.get("timeout_seconds", 60), "timeout"))
        return [_record(i, number(row[output], output), time=float(row["time"]), available_at=float(row["time"]),
                        units="FMU-defined", output=output) for i, row in enumerate(result)], {
                            "dependency_version": fmpy.__version__, "output": output}
    raise ValueError("unsupported Python domain operation")


def _required(roles, *names):
    if not all(name in roles for name in names):
        raise ValueError("required immutable input roles: " + ", ".join(names))


def _commands(adapter, p, roles, root):
    """Generate fixed operations; never accept arbitrary CLI argument lists or scripts."""
    binary = shutil.which(CLI_TARGETS[adapter])
    if not binary:
        raise RuntimeError("optional executable missing: " + CLI_TARGETS[adapter])
    if adapter == "embedded.tinygo":
        _required(roles, "source")
        target = p.get("target", "wasm")
        if target not in {"wasm", "pico", "arduino", "cortex-m-qemu"}:
            raise ValueError("unsupported TinyGo target")
        return [[binary, "build", "-target", target, "-o", str(root / ("firmware.wasm" if target == "wasm" else "firmware.elf")), str(roles["source"])]], binary
    if adapter == "embedded.qemu":
        _required(roles, "firmware")
        return [[binary, "-machine", "pc", "-accel", "tcg", "-m", "64", "-nic", "none", "-display", "none",
                 "-serial", "stdio", "-monitor", "none", "-no-reboot", "-snapshot", "-bios", str(roles["firmware"])]], binary
    if adapter == "simulation.verilator":
        _required(roles, "source")
        top = p.get("top_module", "testbench")
        if not isinstance(top, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,100}", top):
            raise ValueError("invalid Verilator top module")
        model_dir = root / "obj_dir"
        return [[binary, "--binary", "--timing", "--build-jobs", "1", "--top-module", top, "--Mdir", str(model_dir), "-o", "simulation", str(roles["source"])],
                [str(model_dir / "simulation")]], binary
    if adapter == "simulation.energyplus":
        _required(roles, "model", "weather")
        return [[binary, "-w", str(roles["weather"]), "-d", str(root / "energyplus-output"), "-r", str(roles["model"])]], binary
    if adapter == "embedded.renode":
        _required(roles, "firmware", "platform")
        seconds = number(p.get("simulation_seconds", 1), "simulation_seconds", minimum=.001)
        if seconds > 60:
            raise ValueError("Renode simulation horizon exceeds 60 seconds")
        uart = p.get("uart", "sysbus.uart")
        if not isinstance(uart, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,100}", uart):
            raise ValueError("invalid Renode UART identifier")
        script = root / "simulation.resc"
        script.write_text(f'mach create\nmachine LoadPlatformDescription @{roles["platform"]}\nsysbus LoadELF @{roles["firmware"]}\n{uart} CreateFileBackend @{root / "serial.log"} true\nemulation RunFor "{seconds}"\nquit\n')
        return [[binary, "--disable-gui", "--console", str(script)]], binary
    if adapter == "network.ns3":
        if not sys.platform.startswith("linux"):
            raise RuntimeError("this ns-3 fixture is Linux-only")
        return [[sys.executable, "-c", "import os,sys; os.chdir(sys.argv[1]); os.execv(sys.argv[2],sys.argv[2:])", str(Path(binary).resolve().parent), binary, "run", "first", "--no-build", f"--cwd={root}"]], binary
    if adapter == "robotics.gazebo":
        world = roles.get("world", root / "world.sdf")
        if "world" not in roles:
            world.write_text('<sdf version="1.9"><world name="bounded"><physics name="step" type="ignored"><max_step_size>0.001</max_step_size></physics><gravity>0 0 -9.8</gravity></world></sdf>')
        count = integer(p.get("iterations", 100), "iterations", 1, 100000)
        return [[binary, "sim", "-v", "4", "-s", "-r", "--iterations", str(count), str(world)]], binary
    if adapter == "network.mininet":
        if not sys.platform.startswith("linux"):
            raise RuntimeError("Mininet requires Linux and an isolated network namespace")
        unshare = shutil.which("unshare")
        if not unshare:
            raise RuntimeError("Mininet fixture requires unshare network containment")
        return [[unshare, "--net", "--pid", "--fork", "--kill-child=KILL", binary, "--switch", "user", "--controller", "none", "--topo", "single,2", "--test", "pingall"]], binary
    if adapter == "simulation.matlab":
        _required(roles, "model")
        duration = number(p.get("duration", 1), "duration", minimum=1e-9)
        if duration > 86400:
            raise ValueError("Simulink horizon exceeds 86400 seconds")
        # Fixed local basename prevents MATLAB command injection from uploaded names.
        script = root / "run_model.m"
        script.write_text(f"load_system('model.slx'); in=Simulink.SimulationInput('model'); in=in.setModelParameter('StopTime','{duration}'); out=sim(in); writematrix(out.tout,'simulation-time.csv'); save('simulation-result.mat','out'); disp(version);\n")
        return [[binary, "-batch", "run('run_model.m')"]], binary
    if adapter == "embedded.wokwi_tinygo":
        _required(roles, "firmware", "diagram")
        config = root / "wokwi.toml"
        config.write_text('[wokwi]\nversion = 1\nfirmware = "firmware.elf"\nelf = "firmware.elf"\n')
        expected = p.get("expect_text")
        if not isinstance(expected, str) or not 1 <= len(expected) <= 200:
            raise ValueError("Wokwi requires bounded expect_text")
        return [[binary, str(root), "--timeout", str(integer(p.get("simulation_ms", 1000), "simulation_ms", 1, 60000)),
                 "--expect-text", expected, "--serial-log-file", str(root / "serial.log")]], binary
    raise ValueError("unsupported domain operation")


def execute_domain(spec, output_dir, emit, cancelled, *, python_executable=None):
    definition = spec["definition"]
    adapter = definition["engine"]
    if adapter not in PYTHON_TARGETS.keys() | CLI_TARGETS.keys():
        raise ValueError("domain adapter is not implemented")
    if definition.get("mode", "offline") != "offline" or definition.get("operating_mode", "simulation") != "simulation":
        raise ValueError("domain adapters support offline simulation only")
    if definition.get("pipeline"):
        raise ValueError("domain adapters do not silently apply unsupported pipelines")
    grants = spec.get("_runtime_grants", {})
    native = adapter not in {"simulation.pandapower", "simulation.pybamm"}
    if native and grants.get("native_execution") is not True:
        raise ValueError("native_execution requires an explicit trusted runner grant; authored declarations cannot self-authorize")
    if adapter == "network.mininet" and grants.get("isolated_network_namespace") is not True:
        raise ValueError("Mininet requires an explicit isolated_network_namespace grant")
    if adapter == "simulation.matlab" and grants.get("licensed_matlab") is not True:
        raise ValueError("MATLAB requires an explicit licensed_matlab grant")
    if adapter == "embedded.wokwi_tinygo":
        if grants.get("cloud_simulation") is not True:
            raise ValueError("Wokwi requires an explicit cloud_simulation grant")
        token = spec.get("_runtime_secrets", {}).get("WOKWI_CLI_TOKEN")
        if not isinstance(token, str) or not token.startswith("wok_") or len(token) != 44:
            raise ValueError("Wokwi requires a trusted runtime CI token, never authored parameters")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("output directory must be empty")
    p = definition.get("parameters", {})
    schema = {"simulation.pybamm": {"duration", "samples"}, "simulation.fmi": {"duration", "samples", "output"},
              "embedded.tinygo": {"target"}, "embedded.wokwi_tinygo": {"expect_text", "simulation_ms"},
              "embedded.renode": {"simulation_seconds", "uart"}, "embedded.qemu": {"capture_seconds"}, "simulation.verilator": {"top_module"},
              "robotics.gazebo": {"iterations"}, "simulation.matlab": {"duration"}}
    if set(p) - (schema.get(adapter, set()) | {"timeout_seconds", "max_output_bytes"}):
        raise ValueError("unsupported domain adapter parameter")
    if len(json.dumps(p).encode()) > 1024**2:
        raise ValueError("parameters exceed 1 MiB")
    seconds = integer(p.get("timeout_seconds", 60), "timeout_seconds", 1, 300)
    max_bytes = integer(p.get("max_output_bytes", 16 * 1024**2), "max_output_bytes", 1024, 256 * 1024**2)
    roles = _copy_inputs(spec, root)
    input_hashes = {key: digest(path) for key, path in roles.items()}
    for required in ({"simulation.fmi": ["fmu"], "simulation.energyplus": ["model", "weather"]}.get(adapter, [])):
        _required(roles, required)
    if adapter == "simulation.fmi":
        _fmu_check(roles["fmu"])
    deadline = time.monotonic() + seconds
    def guarded_cancelled():
        if cancelled():
            return True
        output_bytes = 0
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("tool output contains a symlink")
            if path.is_file() and path not in roles.values():
                output_bytes += path.stat().st_size
        if output_bytes > max_bytes:
            raise ValueError("domain artifacts exceeded byte budget during execution")
        return False
    emit({"event": "adapter_started", "adapter": adapter})
    if adapter in PYTHON_TARGETS:
        request = root / "request.json"
        request.write_text(json.dumps({"adapter": adapter, "parameters": p, "roles": {key: str(value) for key, value in roles.items()}}))
        executable = os.path.abspath(python_executable or sys.executable)
        commands = [[executable, "-m", "tinystudio.lab.domain_adapters", "--worker", str(request)]]
        runtime_hash = "sha256:" + digest(Path(executable))
    else:
        commands, binary = _commands(adapter, p, roles, root)
        runtime_hash = "sha256:" + digest(Path(binary))
    for index, command in enumerate(commands):
        stage = root / f"stage-{index}"
        stage.mkdir()
        # Model scripts execute from root; bound process cwd is stage, so use a fixed chdir wrapper.
        cwd_wrapper = [sys.executable, "-c", "import os,sys; os.chdir(sys.argv[1]); os.environ['GOPROXY']='off'; os.environ['GOSUMDB']='off'; os.execv(sys.argv[2],sys.argv[2:])", str(root), *command]
        contained = _contained(cwd_wrapper, root) if native and adapter != "network.mininet" else cwd_wrapper
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("domain execution budget exhausted")
        if adapter == "embedded.wokwi_tinygo":
            with tempfile.TemporaryDirectory(prefix="tinystudio-wokwi-secret-") as private:
                secret = Path(private) / "token"
                secret.write_text(token)
                secret.chmod(0o600)
                cloud_wrapper = [sys.executable, "-c", "import os,sys; os.environ['WOKWI_CLI_TOKEN']=open(sys.argv[1]).read(); os.chdir(sys.argv[2]); os.execv(sys.argv[3],sys.argv[3:])", str(secret), str(root), *command]
                try:
                    stdout, stderr = _bounded_process(_contained(cloud_wrapper, root, allow_network=True), stage, guarded_cancelled, remaining, max_bytes)
                finally:
                    for log in stage.glob("*.log"):
                        log.write_bytes(log.read_bytes().replace(token.encode(), b"[REDACTED]"))
        else:
            horizon = number(p.get("capture_seconds", 1), "capture_seconds", minimum=.01) if adapter == "embedded.qemu" else None
            if horizon is not None and (horizon > 30 or horizon >= remaining):
                raise ValueError("QEMU capture horizon must be <=30 seconds and shorter than the wall-time budget")
            try:
                stdout, stderr = _bounded_process(contained, stage, guarded_cancelled, horizon or remaining, max_bytes)
            except TimeoutError:
                if horizon is None:
                    raise
                # A bounded serial-capture horizon is the requested operation, not a guest shutdown claim.
                stdout, stderr = stage / "stdout.log", stage / "stderr.log"
        if cancelled():
            raise ExecutionCancelled("domain execution cancelled")
    if any(digest(path) != input_hashes[key] for key, path in roles.items()):
        raise ValueError("tool mutated an immutable input copy")
    if adapter in PYTHON_TARGETS:
        payload = json.loads(stdout.read_text())
        rows, metrics = payload["rows"], payload["metrics"]
    elif adapter == "simulation.energyplus":
        result = root / "energyplus-output" / "eplusout.csv"
        if not result.is_file():
            raise RuntimeError("EnergyPlus did not produce requested CSV observations")
        rows = []
        with result.open() as stream:
            for index, fields in enumerate(csv.DictReader(stream)):
                if index >= 100000:
                    raise ValueError("EnergyPlus CSV exceeds 100000 rows")
                rows.append(_record(index, None, observed=False, quality="raw_measurement_map", measurements=fields,
                                    time=0.0, available_at=0.0, sequence=index, time_axis="report_row"))
        metrics = {"operation": "weather_model_simulation"}
    else:
        text = stdout.read_text(errors="replace") + "\n" + stderr.read_text(errors="replace")
        if adapter in {"embedded.renode", "embedded.wokwi_tinygo"}:
            serial = root / "serial.log"
            if not serial.is_file() or not serial.stat().st_size:
                raise RuntimeError("simulation produced no serial observations")
            text = serial.read_text(errors="replace")
        if adapter == "simulation.matlab":
            result = root / "simulation-time.csv"
            if not result.is_file():
                raise RuntimeError("Simulink did not produce simulation times")
            rows = [_record(i, number(line, "simulation time"), units="s", time=number(line, "simulation time"), available_at=number(line, "simulation time")) for i, line in enumerate(result.read_text().splitlines())]
        else:
            rows = [_record(i, None, observed=False, quality="tool_trace", text=line, time=0.0, available_at=0.0, sequence=i, time_axis="trace_sequence")
                    for i, line in enumerate(text.splitlines()) if line.strip()]
            if adapter == "embedded.tinygo":
                firmware = root / ("firmware.wasm" if p.get("target", "wasm") == "wasm" else "firmware.elf")
                if not firmware.is_file():
                    raise RuntimeError("TinyGo did not produce firmware")
                rows = [_record(0, firmware.stat().st_size, units="bytes", firmware_sha256=digest(firmware))] + rows
            if not rows:
                raise RuntimeError("domain tool completed without observations; execution is unqualified")
        metrics = {"operation": adapter, "runtime_binary_version": runtime_hash}
        if adapter == "embedded.qemu":
            metrics.update(capture_horizon_seconds=p.get("capture_seconds", 1), guest_shutdown_verified=False)
    records = root / "processed.jsonl"
    with records.open("w") as stream:
        for row in rows:
            if cancelled():
                raise ExecutionCancelled("domain execution cancelled")
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    artifacts = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("tool output contains a symlink")
        if path.is_file():
            if path not in roles.values():
                total += path.stat().st_size
            if total > max_bytes:
                raise ValueError("domain artifacts exceed total output byte budget")
            artifacts.append({"name": str(path.relative_to(root)), "path": str(path), "sha256": digest(path),
                              "media_type": "application/x-ndjson" if path == records else "application/octet-stream",
                              "role": "original" if path in roles.values() else "derived"})
    return {"records": len(rows), "artifacts": artifacts, "metrics": metrics,
            "qualification": {"adapter": adapter, "executed": True, "physical_validation": False,
                              "source_version": "sha256:" + digest(Path(__file__)), "runtime_version": runtime_hash,
                              "native_grant_used": native, "network_fetches": False, "cloud_simulation": adapter == "embedded.wokwi_tinygo", **metrics}}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("only internal --worker execution is supported")
    request = json.loads(Path(sys.argv[2]).read_text())
    rows, metrics = _python_operation(request["adapter"], request["parameters"], request["roles"])
    print(json.dumps({"rows": rows, "metrics": metrics}, allow_nan=False))
