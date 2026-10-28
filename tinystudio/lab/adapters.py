"""Optional bounded simulation adapters. No device writes, broker connections, or user commands."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
from urllib.parse import urlsplit

from .engine import ExecutionCancelled, digest
from .signals import integer, number


PYTHON_ADAPTERS = {"simulation.simpy": "simpy", "optimization.ortools": "ortools", "testing.hypothesis": "hypothesis"}
CLI_ADAPTERS = {"simulation.ngspice": "ngspice", "simulation.verilator": "verilator"}
BUILTIN_ADAPTERS = {"protocol.modbus", "protocol.mqtt", "simulation.boptest"}


def _record(index, value, *, units="unitless", **extra):
    return {"record_id": f"e0:r{index}", "episode_id": "e0", "time": float(index),
            "available_at": float(index), "value": value, "units": units,
            "observed": True, "quality": "simulated", **extra}


def _python_operation(adapter_id, p, seed, seconds):
    if adapter_id == "simulation.simpy":
        import simpy
        count = integer(p.get("jobs", 10), "jobs", 1, 10000)
        arrival = number(p.get("arrival_interval", 1), "arrival_interval", minimum=1e-6)
        duration = number(p.get("service_time", 2), "service_time", minimum=1e-6)
        capacity = integer(p.get("capacity", 1), "capacity", 1, 100)
        env = simpy.Environment()
        server = simpy.Resource(env, capacity=capacity)
        rows = []
        def job(index):
            yield env.timeout(index * arrival)
            entered = env.now
            with server.request() as request:
                yield request
                started = env.now
                yield env.timeout(duration)
                rows.append(_record(index, started - entered, units="s", arrival_time=entered,
                                    service_started=started, completed_at=env.now, time=entered, available_at=env.now))
        for index in range(count):
            env.process(job(index))
        env.run()
        rows.sort(key=lambda row: row["time"])
        return rows, {"simulated_duration": env.now, "dependency_version": importlib.metadata.version("simpy")}
    if adapter_id == "optimization.ortools":
        from ortools.sat.python import cp_model
        weights = p.get("weights", [2, 3, 4])
        values = p.get("values", [3, 4, 5])
        if not isinstance(weights, list) or not isinstance(values, list) or len(weights) != len(values) or not 1 <= len(weights) <= 1000:
            raise ValueError("weights and values must have equal length, between 1 and 1000")
        weights = [integer(v, "weight", 1, 10**6) for v in weights]
        values = [integer(v, "value", 0, 10**6) for v in values]
        capacity = integer(p.get("capacity", 5), "capacity", 0, 10**9)
        model = cp_model.CpModel()
        chosen = [model.new_bool_var(f"item_{i}") for i in range(len(weights))]
        model.add(sum(w*x for w, x in zip(weights, chosen)) <= capacity)
        model.maximize(sum(v*x for v, x in zip(values, chosen)))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(.01, seconds * .8)
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = seed % (2**31 - 1)
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(f"solver did not produce a feasible solution: {solver.status_name(status)}")
        selected = [i for i, variable in enumerate(chosen) if solver.value(variable)]
        return [_record(0, sum(values[i] for i in selected), selected=selected,
                        total_weight=sum(weights[i] for i in selected), solver_status=solver.status_name(status))], {
                            "optimal": status == cp_model.OPTIMAL, "dependency_version": importlib.metadata.version("ortools")}
    if adapter_id == "testing.hypothesis":
        from hypothesis import given, settings, seed as hypothesis_seed, strategies as st, Phase
        count = integer(p.get("samples", 100), "samples", 1, 10000)
        low = integer(p.get("minimum", -100), "minimum", -(2**31), 2**31 - 1)
        high = integer(p.get("maximum", 100), "maximum", low, 2**31 - 1)
        rows = []
        @hypothesis_seed(seed)
        @settings(max_examples=count, database=None, deadline=None, phases=[Phase.generate])
        @given(st.integers(min_value=low, max_value=high))
        def collect(value):
            rows.append(_record(len(rows), value))
        collect()
        return rows, {"requested_samples": count, "actual_samples": len(rows),
                      "sampling": "Hypothesis generation; finite domains may yield fewer unique examples",
                      "dependency_version": importlib.metadata.version("hypothesis")}
    raise ValueError("unsupported Python adapter")


def _protocol_operation(adapter_id, p):
    if any(key in p for key in ("host", "port", "broker", "device", "url", "write")):
        raise ValueError("protocol fixtures are offline only; live endpoints and writes are forbidden")
    if adapter_id == "protocol.modbus":
        registers = p.get("registers", [0, 109, 65535])
        if not isinstance(registers, list) or not 1 <= len(registers) <= 125:
            raise ValueError("registers must contain 1..125 unsigned 16-bit values")
        registers = [integer(v, "register", 0, 65535) for v in registers]
        # Function 03 response PDU: function, byte count, big-endian registers.
        packet = bytes([3, len(registers) * 2]) + struct.pack(f">{len(registers)}H", *registers)
        decoded = list(struct.unpack(f">{len(registers)}H", packet[2:]))
        return [_record(i, value, wire_hex=packet.hex(), protocol="Modbus PDU function 03", units="register") for i, value in enumerate(decoded)], {"scope": "offline response PDU encoding/decoding; no RTU/TCP transport"}
    topic = p.get("topic", "lab/value")
    payload = str(p.get("payload", "42")).encode("utf-8")
    if not isinstance(topic, str) or not topic or any(c in topic for c in ("\x00", "+", "#")):
        raise ValueError("MQTT topic must be a nonempty UTF-8 publish topic without wildcards")
    encoded = topic.encode("utf-8")
    if len(encoded) > 65535 or len(payload) > 65535:
        raise ValueError("MQTT fixture topic/payload exceeds 65535 bytes")
    body = struct.pack(">H", len(encoded)) + encoded + payload
    remaining = len(body)
    header = bytearray([0x30])  # PUBLISH, QoS 0, retain false.
    while True:
        digit = remaining % 128
        remaining //= 128
        header.append(digit | (128 if remaining else 0))
        if not remaining:
            break
    packet = bytes(header) + body
    return [_record(0, len(payload), units="bytes", wire_hex=packet.hex(), topic=topic,
                    payload_utf8=payload.decode(), protocol="MQTT 3.1.1 PUBLISH QoS 0")], {"scope": "offline packet encoding; no broker or delivery claim"}


def _boptest_operation(p, seconds):
    import urllib.request
    base = str(p.get("base_url", "http://127.0.0.1:8000")).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"} or parsed.username or parsed.password or parsed.path:
        raise ValueError("BOPTEST must use a numeric loopback HTTP simulation service without credentials")
    testcase = str(p.get("testcase", "bestest_hydronic"))
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", testcase):
        raise ValueError("invalid BOPTEST testcase")
    steps = integer(p.get("steps", 10), "steps", 1, 10000)
    deadline = time.monotonic() + seconds * .8
    received_bytes = 0
    max_bytes = integer(p.get("max_output_bytes", 1024 * 1024), "max_output_bytes", 1024, 16 * 1024 * 1024)
    def request(method, path, payload=None):
        nonlocal received_bytes
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("BOPTEST time budget exhausted")
        body = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(base + "/" + path, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        # A local simulator cannot redirect the adapter to another host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError("BOPTEST redirects are forbidden")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=min(remaining, 5)) as response:
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("BOPTEST response exceeds 1 MiB")
            received_bytes += len(data)
            if received_bytes > max_bytes:
                raise ValueError("BOPTEST cumulative response budget exceeded")
            result = json.loads(data)
        if isinstance(result, dict) and result.get("status", 200) != 200:
            raise RuntimeError("BOPTEST rejected the simulation request")
        return result.get("payload", result)
    selected = request("POST", f"testcases/{testcase}/select")
    testid = str(selected["testid"])
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", testid):
        raise ValueError("invalid BOPTEST testid")
    try:
        request("PUT", f"initialize/{testid}", {"start_time": 0, "warmup_period": 0})
        request("PUT", f"step/{testid}", {"step": number(p.get("step", 300), "step", minimum=1)})
        rows = []
        for index in range(steps):
            measurement = request("POST", f"advance/{testid}", {})
            candidates = sorted(key for key, value in measurement.items()
                                if key != "time" and isinstance(value, (int, float)) and not isinstance(value, bool))
            point = p.get("measurement", candidates[0] if candidates else None)
            if point not in measurement:
                raise ValueError("BOPTEST response has no requested numeric measurement")
            event_time = number(measurement.get("time", index), "measurement time")
            rows.append(_record(index, number(measurement[point], "measurement"), units="unreported",
                                time=event_time, available_at=event_time, measurement=point, measurements=measurement))
        return rows, {"scope": "loopback BOPTEST simulation API", "testcase": testcase,
                      "service_version": "unreported", "dependency_version": "BOPTEST REST interface"}
    finally:
        request("PUT", f"stop/{testid}")


def _bounded_process(command, output_dir, cancelled, seconds, max_bytes):
    """Run a fixed adapter command, with process-group cancellation and output budgets."""
    stdout = output_dir / "stdout.log"
    stderr = output_dir / "stderr.log"
    # The wrapper sets kernel CPU/file limits before exec; no preexec_fn in threaded workers.
    wrapper = "import os,resource,sys; resource.setrlimit(resource.RLIMIT_CPU,(int(float(sys.argv[1]))+1,int(float(sys.argv[1]))+1)); resource.setrlimit(resource.RLIMIT_FSIZE,(int(sys.argv[2]),int(sys.argv[2]))); os.execv(sys.argv[3],sys.argv[3:])"
    if cancelled():
        raise ExecutionCancelled("adapter cancelled")
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                   "HOME": str(output_dir), "TMPDIR": str(output_dir), "LANG": "C.UTF-8", "OMP_NUM_THREADS": "1",
                   "TINYSTUDIO_SKIP_DOTENV": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    with stdout.open("wb") as out, stderr.open("wb") as err:
        from .runner import ProcessGroup
        group = ProcessGroup([sys.executable, "-c", wrapper, str(seconds), str(max_bytes), *command],
                                   cwd=output_dir, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                   env=environment)
        process = group.process
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancelled():
                    raise ExecutionCancelled("adapter cancelled")
                if time.monotonic() - started > seconds:
                    raise TimeoutError("adapter wall-time budget exceeded")
                if stdout.stat().st_size + stderr.stat().st_size > max_bytes:
                    raise ValueError("adapter output budget exceeded")
                time.sleep(.02)
        finally:
            group.close()
    if process.returncode:
        raise RuntimeError(f"adapter process failed ({process.returncode}); inspect stderr.log")
    if stdout.stat().st_size + stderr.stat().st_size > max_bytes:
        raise ValueError("adapter output budget exceeded")
    return stdout, stderr


def execute_adapter(spec, output_dir, emit, cancelled, *, python_executable=None):
    """Execute a concrete optional adapter operation using the frozen lab definition."""
    definition = spec["definition"]
    adapter_id = definition["engine"]
    if adapter_id not in PYTHON_ADAPTERS.keys() | CLI_ADAPTERS.keys() | BUILTIN_ADAPTERS:
        raise ValueError(f"adapter not implemented: {adapter_id}")
    if definition.get("mode", "offline") != "offline":
        raise ValueError("optional adapters currently support offline simulation only")
    if definition.get("inputs") or definition.get("pipeline"):
        raise ValueError("adapter fixtures do not accept input scripts or scientific pipeline stages")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError("output directory must be empty")
    seconds = integer(definition.get("parameters", {}).get("timeout_seconds", 10), "timeout_seconds", 1, 60)
    max_bytes = integer(definition.get("parameters", {}).get("max_output_bytes", 1024 * 1024), "max_output_bytes", 1024, 16 * 1024 * 1024)
    seed = integer(definition.get("seed", 0), "seed", 0, 2**53)
    parameters = definition.get("parameters", {})
    if len(json.dumps(parameters).encode()) > 1024 * 1024:
        raise ValueError("adapter parameters exceed 1 MiB")
    emit({"event": "adapter_started", "adapter": adapter_id})
    executable = os.path.abspath(python_executable or sys.executable)
    if adapter_id in CLI_ADAPTERS:
        binary = shutil.which(CLI_ADAPTERS[adapter_id])
        if binary is None:
            raise RuntimeError(f"dependency missing: {CLI_ADAPTERS[adapter_id]}")
        if adapter_id == "simulation.ngspice":
            voltage = number(parameters.get("voltage", 10), "voltage")
            resistance = number(parameters.get("resistance", 1000), "resistance", minimum=1e-9)
            model = output_dir / "divider.cir"
            model.write_text(f"TinyStudio divider\nV1 in 0 {voltage}\nR1 in out {resistance}\nR2 out 0 {resistance}\n.control\nop\nprint v(out)\n.endc\n.end\n")
            command = [binary, "-n", "-b", str(model)]
        else:
            model = output_dir / "counter.sv"
            model.write_text("module counter(input logic clk, input logic reset, output logic [7:0] count); always_ff @(posedge clk) if(reset) count <= 0; else count <= count + 1; endmodule\n")
            command = [binary, "--lint-only", "-Wall", str(model)]
    else:
        request_file = output_dir / "request.json"
        request_file.write_text(json.dumps({"adapter": adapter_id, "parameters": parameters, "seed": seed, "seconds": seconds}))
        command = [executable, "-m", "tinystudio.lab.adapters", "--worker", str(request_file)]
    stdout, _ = _bounded_process(command, output_dir, cancelled, seconds, max_bytes)
    if adapter_id in CLI_ADAPTERS:
        text = stdout.read_text(errors="replace")
        if adapter_id == "simulation.ngspice":
            matched = re.search(r"v\(out\)\s*=\s*([-+0-9.eE]+)", text)
            if not matched:
                raise RuntimeError("ngspice did not return divider voltage")
            rows = [_record(0, number(matched.group(1), "v(out)"), units="V")]
        else:
            rows = [_record(0, 1, units="lint_success")]
        metadata = {"operation": "resistor_divider" if adapter_id == "simulation.ngspice" else "generated_counter_lint",
                    "dependency_version": "sha256:" + digest(Path(binary)), "executable": binary}
    else:
        payload = json.loads(stdout.read_text())
        rows, metadata = payload["rows"], payload["metadata"]
    records_path = output_dir / "processed.jsonl"
    with records_path.open("w") as stream:
        for row in rows:
            if cancelled():
                raise ExecutionCancelled("adapter cancelled")
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    artifacts = []
    for path in sorted(output_dir.iterdir()):
        if path.is_file():
            artifacts.append({"name": path.name, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                              "role": "derived" if path == records_path else "adapter_evidence",
                              "media_type": "application/x-ndjson" if path == records_path else "text/plain"})
    emit({"event": "progress", "records": len(rows)})
    return {"records": len(rows), "artifacts": artifacts, "metrics": metadata,
            "qualification": {"adapter": adapter_id, "executed": True, "physical_validation": False,
                              "live_device_access": False, "source_version": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                              "budgets": {"wall_seconds": seconds, "output_bytes": max_bytes}, **metadata}}


def _worker(path):
    request = json.loads(Path(path).read_text())
    adapter_id, p = request["adapter"], request["parameters"]
    if adapter_id in PYTHON_ADAPTERS:
        rows, metadata = _python_operation(adapter_id, p, request["seed"], request["seconds"])
    elif adapter_id in {"protocol.modbus", "protocol.mqtt"}:
        rows, metadata = _protocol_operation(adapter_id, p)
    elif adapter_id == "simulation.boptest":
        rows, metadata = _boptest_operation(p, request["seconds"])
    else:
        raise ValueError("unsupported worker adapter")
    print(json.dumps({"rows": rows, "metadata": metadata}, allow_nan=False))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("only the internal --worker invocation is supported")
    _worker(sys.argv[2])
