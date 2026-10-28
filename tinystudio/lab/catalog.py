"""Capability manifests describe executable support, never aspirational availability."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


def _version() -> str:
    checksum = hashlib.sha256()
    for name in ("engine.py", "signals.py"):
        checksum.update(Path(__file__).with_name(name).read_bytes())
    return "sha256:" + checksum.hexdigest()


def catalog() -> list[dict]:
    """Return the installed adapter portfolio with explicit execution status."""
    version = _version()
    parquet_available = importlib.util.find_spec("pyarrow") is not None
    result = []
    implemented = [
        ("synthetic.signal", "Synthetic signals", "signals", ["offline", "online"],
         "Seeded sine, square, sawtooth and constant signals with noise and missingness."),
        ("recorded.replay", "Immutable recorded replay", "recorded", ["offline", "online"],
         "Checksum-verified JSONL replay with copied originals and availability timestamps."),
        ("simulation.plant", "First-order closed-loop plant", "control", ["offline", "online"],
         "Scalar first-order plant with proportional control and exact zero-order-hold integration; no physical validation."),
    ]
    for adapter_id, name, domain, modes, description in implemented:
        result.append({"id": adapter_id, "name": name, "domain": domain,
                       "implemented": True, "available": parquet_available,
                       "status": "implemented" if parquet_available else "dependency_missing",
                       "reason": None if parquet_available else "pyarrow is required for Parquet artifact export",
                       "version": version, "modes": modes, "description": description,
                       "qualification": "unqualified", "qualification_reason": "No runtime receipt is attached to this catalog response",
                       "limits": {"max_records": 1_000_000},
                       "representations": ["numeric", "text", "bytes", "state_tokens"]})
    from .adapters import PYTHON_ADAPTERS, CLI_ADAPTERS, BUILTIN_ADAPTERS
    from .domain_adapters import PYTHON_TARGETS, CLI_TARGETS
    import shutil
    names = [
        ("embedded.wokwi_tinygo", "Wokwi/TinyGo", "embedded", "tinygo"),
        ("embedded.tinygo", "TinyGo firmware build", "embedded", "tinygo"),
        ("embedded.renode", "Renode", "embedded", "renode"),
        ("embedded.qemu", "QEMU", "embedded", "qemu-system-x86_64"),
        ("simulation.verilator", "Verilator", "digital", "verilator"),
        ("simulation.fmi", "FMI / FMU co-simulation", "industrial", None),
        ("simulation.openmodelica", "OpenModelica", "industrial", "omc"),
        ("simulation.boptest", "BOPTEST", "buildings", None),
        ("simulation.energyplus", "EnergyPlus", "buildings", "energyplus"),
        ("simulation.ngspice", "ngspice", "circuits", "ngspice"),
        ("simulation.pandapower", "pandapower", "energy", None),
        ("simulation.pybamm", "PyBaMM", "batteries", None),
        ("network.mininet", "Mininet", "networks", "mn"),
        ("network.ns3", "ns-3", "networks", "ns3"),
        ("protocol.modbus", "Modbus", "industrial_protocols", None),
        ("protocol.mqtt", "MQTT", "iot_protocols", None),
        ("simulation.simpy", "SimPy", "discrete_event", None),
        ("optimization.ortools", "OR-Tools", "optimization", None),
        ("testing.hypothesis", "Hypothesis", "test_generation", None),
        ("robotics.gazebo", "Gazebo", "robotics", "gz"),
        ("environment.custom", "Custom environments", "custom", None),
        ("simulation.matlab", "MATLAB/Simulink (licensed)", "industrial", "matlab"),
    ]
    adapter_version = "sha256:" + hashlib.sha256(b"".join(
        Path(__file__).with_name(name).read_bytes()
        for name in ("adapters.py", "domain_adapters.py", "modelica.py")
    )).hexdigest()
    for adapter_id, name, domain, binary in names:
        python_targets = {**PYTHON_ADAPTERS, **PYTHON_TARGETS}
        cli_targets = {**CLI_ADAPTERS, **CLI_TARGETS, "simulation.openmodelica": "omc"}
        implemented = adapter_id in python_targets or adapter_id in cli_targets or adapter_id in BUILTIN_ADAPTERS
        if adapter_id in python_targets:
            dependency = python_targets[adapter_id]
            installed = importlib.util.find_spec(dependency) is not None
        elif adapter_id in cli_targets:
            dependency = cli_targets[adapter_id]
            installed = shutil.which(dependency) is not None
        else:
            dependency = binary
            installed = adapter_id in BUILTIN_ADAPTERS
        available = implemented and installed
        reason = (None if available else f"Optional dependency missing: {dependency}" if implemented
                  else "No executable TinyStudio adapter has been implemented")
        scope = ("offline protocol packet fixture; no device/broker access" if adapter_id.startswith("protocol.") else
                 "immutable Modelica source with typed parameters; explicit native execution grant required" if adapter_id == "simulation.openmodelica" else
                 "loopback simulation API; service availability checked at execution" if adapter_id == "simulation.boptest" else
                 "uploaded model build/run; generated counter lint without an input" if adapter_id == "simulation.verilator" else
                 "generated resistor-divider fixture" if adapter_id == "simulation.ngspice" else
                 "artifact-backed optional engine; explicit runtime grants required for native/cloud/licensed operations" if adapter_id in CLI_TARGETS or adapter_id in PYTHON_TARGETS else "bounded offline fixture")
        result.append({"id": adapter_id, "name": name, "domain": domain,
                       "implemented": implemented, "available": available,
                       "status": "implemented" if available else "dependency_missing" if implemented else "not_implemented",
                       "reason": reason, "version": adapter_version if implemented else None,
                       "modes": ["offline"] if implemented else [],
                       "qualification": "unqualified", "qualification_reason": "No runtime receipt is attached to this catalog response",
                       "execution_scope": scope if implemented else None,
                       "dependency": dependency, "live_device_access": False,
                       "representations": ["numeric", "text", "bytes"] if implemented else [],
                       "command_contract": {"shell": False, "user_commands": False,
                                            "max_wall_seconds": 300 if adapter_id == "simulation.openmodelica" else 60,
                                            "max_output_bytes": 256*1024**2 if adapter_id == "simulation.openmodelica" else 16777216,
                                            "requires_implementation": not implemented}})
    for adapter_id, name, dependency in (("acquisition.serial", "Serial telemetry", "serial"),
            ("acquisition.modbus", "Modbus telemetry", "pymodbus"), ("acquisition.mqtt", "MQTT telemetry", "paho")):
        installed = parquet_available and importlib.util.find_spec(dependency) is not None
        result.append({"id": adapter_id, "name": name, "domain": "acquisition", "implemented": True,
            "available": installed, "status": "implemented" if installed else "dependency_missing",
            "reason": None if installed else f"Optional dependencies required: pyarrow and {dependency}",
            "dependency": dependency, "modes": ["online"], "operating_modes": ["acquisition", "shadow"],
            "qualification": "unqualified", "qualification_reason": "No live source execution receipt is attached.",
            "live_device_access": True, "requires_operator_binding": True,
            "execution_scope": "read-only telemetry; exact trusted binding required; serial opening may toggle control lines",
            "version": "sha256:"+hashlib.sha256(Path(__file__).with_name("acquisition.py").read_bytes()).hexdigest(),
            "representations": ["numeric", "raw_payload"]})
    from .plugins import manifest_for
    return [{**row, "manifest": manifest_for(row)} for row in result]
