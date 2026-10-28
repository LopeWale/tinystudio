"""Independent adapter fixtures; optional target execution is distinguished from mocks."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest

from tinystudio.lab.adapters import execute_adapter, _bounded_process, _boptest_operation
from tinystudio.lab.engine import ExecutionCancelled


def run(tmp_path, adapter_id, p=None, *, python_executable=None):
    result = execute_adapter({"definition": {"engine": adapter_id, "mode": "offline", "seed": 123,
                                            "parameters": p or {}}}, tmp_path / "out", lambda event: None,
                             lambda: False, python_executable=python_executable)
    rows = [json.loads(line) for line in (tmp_path / "out" / "processed.jsonl").read_text().splitlines()]
    return result, rows


def optional_python(package):
    external = os.environ.get("TINYSTUDIO_LAB_QUALIFICATION_PYTHON")
    if external:
        return external
    if importlib.util.find_spec(package) is None:
        pytest.skip(f"optional {package} unavailable; target remains unqualified")
    return sys.executable


def test_modbus_standard_pdu_fixture_and_artifact_hashes(tmp_path):
    result, rows = run(tmp_path, "protocol.modbus", {"registers": [109, 0, 65535]})
    assert [row["value"] for row in rows] == [109, 0, 65535]
    assert rows[0]["wire_hex"] == "0306006d0000ffff"
    assert result["qualification"]["live_device_access"] is False
    for artifact in result["artifacts"]:
        assert hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest() == artifact["sha256"]


def test_mqtt_qos_zero_reference_bytes(tmp_path):
    _, rows = run(tmp_path, "protocol.mqtt", {"topic": "a/b", "payload": "42"})
    assert rows[0]["wire_hex"] == "30070003612f623432"
    assert rows[0]["value"] == 2


@pytest.mark.parametrize("adapter,p", [("protocol.mqtt", {"broker": "example.com"}),
                                        ("protocol.modbus", {"host": "127.0.0.1"}),
                                        ("protocol.mqtt", {"topic": "a/#"}),
                                        ("protocol.modbus", {"registers": [-1]})])
def test_protocol_live_side_effects_and_invalid_fields_fail(tmp_path, adapter, p):
    with pytest.raises(RuntimeError, match="adapter process failed"):
        run(tmp_path, adapter, p)
    assert not (tmp_path / "out" / "processed.jsonl").exists()


def test_simpy_queue_reference(tmp_path):
    executable = optional_python("simpy")
    result, rows = run(tmp_path, "simulation.simpy", {"jobs": 4, "arrival_interval": 1, "service_time": 2}, python_executable=executable)
    # One server: arrivals 0,1,2,3; starts 0,2,4,6; waiting times 0,1,2,3.
    assert [row["value"] for row in rows] == [0, 1, 2, 3]
    assert [row["completed_at"] for row in rows] == [2, 4, 6, 8]
    assert result["metrics"]["simulated_duration"] == 8
    assert result["metrics"]["dependency_version"]


def test_ortools_knapsack_reference(tmp_path):
    executable = optional_python("ortools")
    result, rows = run(tmp_path, "optimization.ortools", python_executable=executable)
    # Capacity 5: items of weights 2+3 yield value 3+4=7, beating item weight4/value5.
    assert rows[0]["selected"] == [0, 1]
    assert rows[0]["total_weight"] == 5
    assert rows[0]["value"] == 7
    assert result["metrics"]["optimal"] is True


def test_hypothesis_bounded_generation_and_seed(tmp_path):
    executable = optional_python("hypothesis")
    p = {"samples": 20, "minimum": -3, "maximum": 3}
    _, rows = run(tmp_path, "testing.hypothesis", p, python_executable=executable)
    second = tmp_path / "second"
    second.mkdir()
    _, again = run(second, "testing.hypothesis", p, python_executable=executable)
    assert rows == again
    assert {row["value"] for row in rows} == {-3, -2, -1, 0, 1, 2, 3}


def test_cancel_terminates_process_group(tmp_path):
    started = time.monotonic()
    def cancelled():
        return time.monotonic() - started > .1
    with pytest.raises(ExecutionCancelled):
        _bounded_process([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path,
                         cancelled, 10, 10000)
    assert time.monotonic() - started < 3


def test_wall_and_output_budgets_enforced(tmp_path):
    with pytest.raises(TimeoutError):
        _bounded_process([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path,
                         lambda: False, .1, 10000)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises((ValueError, RuntimeError)):
        _bounded_process([sys.executable, "-c", "print('x'*100000)"], other,
                         lambda: False, 3, 1024)


def test_unimplemented_targets_do_not_execute_commands(tmp_path):
    with pytest.raises(ValueError, match="not implemented"):
        run(tmp_path, "embedded.qemu")


def test_boptest_refuses_non_loopback_before_network_access():
    for address in ["https://api.boptest.net", "http://localhost:8000", "http://127.0.0.1.evil:8000", "http://user@127.0.0.1:8000"]:
        with pytest.raises(ValueError, match="loopback"):
            _boptest_operation({"base_url": address}, 3)


def test_boptest_api_contract_with_fake_http_boundary(monkeypatch):
    calls = []
    class Response:
        def __init__(self, payload):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            return json.dumps({"status": 200, "payload": self.payload}).encode()
    class Opener:
        def open(self, request, timeout):
            calls.append((request.method, request.full_url, json.loads(request.data) if request.data else None))
            return Response({"testid": "fixture-1"} if "/select" in request.full_url else {"time": 300, "temperature": 295})
    import urllib.request
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    rows, metadata = _boptest_operation({"steps": 2}, 5)
    assert len(rows) == 2
    assert [(method, url.rsplit("/", 2)[-2]) for method, url, body in calls] == [
        ("POST", "bestest_hydronic"), ("PUT", "initialize"), ("PUT", "step"),
        ("POST", "advance"), ("POST", "advance"), ("PUT", "stop")]
    assert rows[0]["measurements"]["temperature"] == 295
    assert metadata["service_version"] == "unreported"


def test_catalog_contains_exact_requested_portfolio_and_no_false_qualification():
    from tinystudio.lab.catalog import catalog
    manifests = catalog()
    names = {item["name"] for item in manifests}
    assert {"Wokwi/TinyGo", "Renode", "QEMU", "Verilator", "OpenModelica", "FMI / FMU co-simulation", "BOPTEST", "EnergyPlus",
            "ngspice", "pandapower", "PyBaMM", "Mininet", "ns-3", "Modbus", "MQTT", "SimPy", "OR-Tools",
            "Hypothesis", "Gazebo", "Custom environments", "MATLAB/Simulink (licensed)"} <= names
    engines = {item["id"]: item for item in manifests}
    assert engines["simulation.openmodelica"]["dependency"] == "omc"
    assert engines["simulation.fmi"]["dependency"] == "fmpy"
    assert all(item.get("qualification", "unqualified") == "unqualified" for item in manifests)
    assert all(not item["available"] for item in manifests if not item["implemented"])
