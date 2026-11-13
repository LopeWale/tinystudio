"""Structural discovery through operator-selected, read-only transport seams.

SunSpec models are located from the marker and reported length chain, as defined
in the SunSpec Device Information Model Specification, sections 6.1 and 6.2:
https://sunspec.org/wp-content/uploads/2025/01/SunSpec-Device-Information-Model-Specificiation-V1-2-1-1.pdf

This module does not load model schemas, infer point meaning or access rights,
qualify physical equipment, open clients, or supply endpoint authority. Serial
enumeration only obtains port metadata. CAN detection uses explicit providers;
some providers may send discovery traffic, so the caller must authorize them.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
from typing import Callable

from .engine import ExecutionCancelled
from .hardware_transports import read_registers


_SIGNATURE = [0x5375, 0x6E53]
_KINDS = frozenset({"holding_registers", "input_registers"})
_ADDRESS_SPACE = 65536
_READ_LIMIT = 125


def _integer(value, name, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"{name} is outside its integer bounds")
    return value


@dataclass(frozen=True)
class SunSpecScanConfig:
    """Explicit zero-based protocol addresses and optional operator model budget.

    No standard base-address guesses or model-count cap are supplied. A budget
    counts non-end models; one following header is read to recognize the end
    marker when the model count exactly equals the budget.
    """

    base_addresses: tuple[int, ...]
    device_id: int
    register_kind: str = "holding_registers"
    max_models: int | None = None
    read_model_data: bool = False

    def __post_init__(self):
        if not isinstance(self.base_addresses, (list, tuple)) or not self.base_addresses:
            raise ValueError("base_addresses requires an explicit nonempty address list")
        bases = tuple(_integer(value, "base address", 0, 65535) for value in self.base_addresses)
        object.__setattr__(self, "base_addresses", bases)
        _integer(self.device_id, "device_id", 0, 255)
        if not isinstance(self.register_kind, str) or self.register_kind not in _KINDS:
            raise ValueError("only holding_registers and input_registers are readable")
        if self.max_models is not None:
            _integer(self.max_models, "max_models")
        if type(self.read_model_data) is not bool:
            raise ValueError("read_model_data must be a boolean")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("base_addresses"), list):
            raise ValueError("base_addresses requires an explicit nonempty address list")
        return cls(
            base_addresses=tuple(value["base_addresses"]),
            device_id=value.get("device_id"),
            register_kind=value.get("register_kind", "holding_registers"),
            max_models=value.get("max_models"),
            read_model_data=value.get("read_model_data", False),
        )

    def as_dict(self):
        return {"base_addresses": list(self.base_addresses), "device_id": self.device_id,
                "register_kind": self.register_kind, "max_models": self.max_models,
                "read_model_data": self.read_model_data}


class SunSpecDiscovery:
    """Walk each authored base without taking ownership of the supplied client.

    Reports retain all attempted reads, including malformed and failed responses.
    Instances are indexed per model ID within each base's chain. A complete chain
    requires the exact end marker; payload failures still leave the scan partial.
    A cancellation raises ExecutionCancelled with report and partial_report
    attributes; last_report also retains that receipt for the caller.
    """

    def __init__(self, config: SunSpecScanConfig):
        if not isinstance(config, SunSpecScanConfig):
            raise ValueError("SunSpec discovery requires a validated SunSpecScanConfig")
        self.config = config
        self.last_report = None

    def _read(self, client, trial, address, count):
        request = {"kind": self.config.register_kind, "address": address, "count": count,
                   "device_id": self.config.device_id, "status": "pending", "registers": []}
        trial["reads"].append(request)
        if address < 0 or count < 1 or count > _READ_LIMIT or address + count > _ADDRESS_SPACE:
            request.update(status="address_bounds", error="read exceeds Modbus register address/count bounds")
            return request
        try:
            response = read_registers(client, kind=self.config.register_kind,
                                      address=address, count=count, device_id=self.config.device_id)
            if response is None:
                request.update(status="invalid_response", error="register read returned no response")
                return request
            if response.isError():
                request.update(status="protocol_error", error=str(response))
                return request
            registers = list(response.registers)
            # Preserve malformed integer values and short/long register replies.
            # Other value types are represented as text to keep receipts JSON-safe.
            request["registers"] = [value if type(value) in (int, str, bool, type(None)) else repr(value)
                                    for value in registers]
            received = sum(type(value) is int and 0 <= value <= 65535 for value in registers)
            trial["registers_read"] += received
            self.last_report["registers_read"] += received
            if len(registers) != count or any(type(value) is not int or not 0 <= value <= 65535 for value in registers):
                request.update(status="invalid_response", error="register response has invalid values or count")
            else:
                request["status"] = "ok"
        except ExecutionCancelled:
            request.update(status="cancelled", error="register read was cancelled")
            raise
        except Exception as exc:
            request.update(status="read_error", error=f"{type(exc).__name__}: {exc}")
        return request

    def scan(self, client, cancelled: Callable[[], bool] = lambda: False):
        report = {"format": "tinystudio.sunspec-discovery.v1", "config": self.config.as_dict(),
                  "status": "running", "complete": False, "registers_read": 0, "trials": []}
        self.last_report = report
        trial = None
        model = None

        def check_cancelled():
            if cancelled():
                raise ExecutionCancelled("SunSpec discovery cancelled")

        try:
            for base in self.config.base_addresses:
                check_cancelled()
                model = None
                trial = {"base_address": base, "device_id": self.config.device_id,
                         "register_kind": self.config.register_kind,
                         "status": "running", "complete": False, "signature": None,
                         "headers": [], "models": [], "reads": [], "registers_read": 0,
                         "end_marker": None}
                report["trials"].append(trial)
                signature = self._read(client, trial, base, 2)
                trial["signature"] = signature["registers"]
                if signature["status"] != "ok":
                    trial.update(status=signature["status"], error=signature["error"])
                    check_cancelled()
                    continue
                if signature["registers"] != _SIGNATURE:
                    trial["status"] = "not_found"
                    check_cancelled()
                    continue
                cursor = base + 2
                instances = {}
                payload_failed = False
                while True:
                    check_cancelled()
                    model = None
                    header_read = self._read(client, trial, cursor, 2)
                    if header_read["status"] != "ok":
                        trial.update(status=header_read["status"], error=header_read["error"])
                        check_cancelled()
                        break
                    header = {"address": cursor, "registers": header_read["registers"][:]}
                    trial["headers"].append(header)
                    model_id, length = header["registers"]
                    if model_id == 0xFFFF:
                        if length != 0:
                            trial.update(status="invalid_end_marker", error="end model length must be zero")
                        else:
                            trial["end_marker"] = header
                            trial.update(status="partial" if payload_failed else "complete", complete=not payload_failed)
                        check_cancelled()
                        break
                    if self.config.max_models is not None and len(trial["models"]) >= self.config.max_models:
                        trial.update(status="budget_exhausted", error="explicit max_models budget exhausted before end marker")
                        check_cancelled()
                        break
                    instance = instances.get(model_id, 0)
                    instances[model_id] = instance + 1
                    model = {"model_id": model_id, "instance": instance, "header_address": cursor,
                             "payload_address": cursor + 2, "length": length,
                             "header_registers": header["registers"][:], "registers": [],
                             "data_reads": [], "status": "header_only"}
                    trial["models"].append(model)
                    payload_end = model["payload_address"] + length
                    if payload_end > _ADDRESS_SPACE:
                        message = "reported model payload exceeds Modbus register address space"
                        model.update(status="address_bounds", error=message)
                        trial.update(status="address_bounds", error=message)
                        check_cancelled()
                        break
                    check_cancelled()
                    if self.config.read_model_data:
                        for address in range(model["payload_address"], payload_end, _READ_LIMIT):
                            check_cancelled()
                            data = self._read(client, trial, address, min(_READ_LIMIT, payload_end - address))
                            model["data_reads"].append(data)
                            if data["status"] != "ok":
                                model.update(status=data["status"], error=data["error"])
                                payload_failed = True
                                check_cancelled()
                                break
                            model["registers"].extend(data["registers"])
                            check_cancelled()
                        else:
                            model["status"] = "complete"
                    cursor = payload_end
        except ExecutionCancelled as exc:
            report.update(status="cancelled", complete=False)
            if trial is not None and trial["status"] == "running":
                trial.update(status="cancelled", complete=False)
            if model is not None and model["status"] in {"header_only", "complete"}:
                model["status"] = "cancelled"
            exc.report = report
            exc.partial_report = report
            raise
        states = [item["status"] for item in report["trials"]]
        report["complete"] = "complete" in states and all(state in {"complete", "not_found"} for state in states)
        report["status"] = "complete" if report["complete"] else "not_found" if all(
            state == "not_found" for state in states) else "partial"
        return report

    def resolve_point(self, report, *, model_id, instance, offset, count):
        """Bind an authored payload offset; this supplies no schema or write access.

        Pass an individual trial to select an unambiguous base when several bases
        contain the same model/instance. Reports describe observations and carry
        no transport authority; the caller must authorize the resulting read.
        """
        _integer(model_id, "model_id", 0, 65534)
        _integer(instance, "instance")
        _integer(offset, "offset")
        _integer(count, "count", 1, _READ_LIMIT)
        if not isinstance(report, dict):
            raise ValueError("point resolution requires a discovery report or trial")
        trials = report.get("trials", [report])
        matches = [(trial, model) for trial in trials for model in trial.get("models", [])
                   if model["model_id"] == model_id and model["instance"] == instance]
        if not matches:
            raise ValueError("requested model instance was not discovered")
        if len(matches) != 1:
            raise ValueError("ambiguous model instance across bases; select one discovery trial")
        trial, model = matches[0]
        if model["status"] == "address_bounds" or offset + count > model["length"]:
            raise ValueError("point range exceeds the discovered model payload")
        address = model["payload_address"] + offset
        if address < 0 or address + count > _ADDRESS_SPACE:
            raise ValueError("point read exceeds Modbus register address space")
        return {"kind": trial["register_kind"], "address": address, "count": count,
                "device_id": trial["device_id"], "base_address": trial["base_address"],
                "model_id": model_id, "instance": instance, "offset": offset,
                "header_address": model["header_address"], "payload_address": model["payload_address"],
                "length": model["length"]}


def enumerate_serial_ports(*, library=None):
    """Obtain pyserial port metadata without constructing or opening Serial."""
    report = {"format": "tinystudio.serial-enumeration.v1", "status": "ok", "ports": [],
              "limitations": ["Port metadata does not establish device identity, availability, or physical qualification."]}
    names = ("device", "name", "description", "hwid", "vid", "pid", "serial_number", "location",
             "manufacturer", "product", "interface")
    try:
        provider = library if library is not None else importlib.import_module("serial.tools.list_ports")
        for port in provider.comports():
            report["ports"].append({name: getattr(port, name, None) for name in names})
    except Exception as exc:
        report.update(status="unavailable", error=f"{type(exc).__name__}: {exc}")
        report["limitations"].append("Serial enumeration is unavailable for this library/platform or failed partway through.")
    return report


def enumerate_can_interfaces(interfaces, *, timeout: float = 5.0, library=None):
    """Detect only explicitly authorized python-can providers; never open Bus.

    Public detection can return an empty list for an unsupported provider, absent
    platform dependencies, no available hardware, or provider timeout. Providers
    may perform network discovery; this helper makes no promise of passive I/O.
    python-can's timeout can return while a provider thread is still finishing.
    Zero selects a nonblocking wait; provider setup and background discovery may
    still occur. The default is the SDK operational default, not a resource cap.
    """
    if not isinstance(interfaces, (list, tuple)) or not interfaces or any(
            not isinstance(value, str) or not value.strip() for value in interfaces):
        raise ValueError("CAN discovery requires an explicit nonempty interface list")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError("CAN detection timeout must be finite and nonnegative")
    selected = list(interfaces)
    report = {"format": "tinystudio.can-enumeration.v1", "status": "ok", "interfaces": selected,
              "configurations": [], "timeout": timeout, "discovery_may_send_traffic": True,
              "limitations": [
                  "Only caller-selected providers are queried; some CAN providers send discovery traffic.",
                  "Empty detection cannot distinguish unsupported providers/platforms, missing dependencies, unavailable hardware, or timeout.",
                  "Detection does not establish device identity or physical qualification; timed-out provider threads may finish later."]}
    try:
        provider = library if library is not None else importlib.import_module("can")
        report["configurations"] = [dict(value) for value in provider.detect_available_configs(
            interfaces=selected, timeout=timeout)]
        if not report["configurations"]:
            report["status"] = "unavailable_or_empty"
    except Exception as exc:
        report.update(status="unavailable", error=f"{type(exc).__name__}: {exc}")
    return report
