"""Discovery fixtures never open devices or send traffic outside owned loopback."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import json
import socket
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from tinystudio.lab.device_discovery import (
    SunSpecDiscovery,
    SunSpecScanConfig,
    enumerate_can_interfaces,
    enumerate_serial_ports,
)
from tinystudio.lab.engine import ExecutionCancelled
from tinystudio.lab.hardware_transports import ModbusEndpoint


class Registers:
    def __init__(self, registers, error=False):
        self.registers = registers
        self.error = error

    def isError(self):
        return self.error

    def __str__(self):
        return "controlled Modbus exception" if self.error else "register response"


class ReadOnlyClient:
    def __init__(self, words, failures=None):
        self.words = words
        self.failures = failures or {}
        self.calls = []

    def read_holding_registers(self, address, *, count, device_id):
        self.calls.append(("holding_registers", address, count, device_id))
        failure = self.failures.get(address)
        if isinstance(failure, Exception):
            raise failure
        if address in self.failures:
            return failure
        return Registers([self.words[pos] for pos in range(address, address + count)])

    def read_input_registers(self, address, *, count, device_id):
        result = self.read_holding_registers(address, count=count, device_id=device_id)
        self.calls[-1] = ("input_registers", address, count, device_id)
        return result

    def __getattr__(self, name):
        if name.startswith("write") or name in {"connect", "close"}:
            pytest.fail("discovery must neither write nor manage the supplied client")
        raise AttributeError(name)


def mapping(base, models, *, end=True):
    words = {base: 0x5375, base + 1: 0x6E53}
    cursor = base + 2
    for model_id, data in models:
        words.update({cursor: model_id, cursor + 1: len(data)})
        words.update({cursor + 2 + index: value for index, value in enumerate(data)})
        cursor += 2 + len(data)
    if end:
        words.update({cursor: 0xFFFF, cursor + 1: 0})
    return words


def scanner(bases=(1769,), **kwargs):
    return SunSpecDiscovery(SunSpecScanConfig.from_dict({
        "base_addresses": list(bases), "device_id": 7, **kwargs,
    }))


def test_chain_uses_reported_lengths_unknown_and_repeated_models_without_model_schema():
    client = ReadOnlyClient(mapping(1769, [(64001, [8, 9, 10]), (103, [1] * 5), (103, [2, 3])]))
    discovery = scanner(read_model_data=True)
    report = discovery.scan(client)
    trial = report["trials"][0]
    assert report["complete"] is True and trial["complete"] is True
    assert [(m["model_id"], m["instance"], m["header_address"], m["payload_address"], m["length"])
            for m in trial["models"]] == [
                (64001, 0, 1771, 1773, 3), (103, 0, 1776, 1778, 5), (103, 1, 1783, 1785, 2)]
    assert trial["headers"][-1] == {"address": 1787, "registers": [0xFFFF, 0]}
    assert trial["end_marker"] == {"address": 1787, "registers": [0xFFFF, 0]}
    assert trial["models"][2]["registers"] == [2, 3]
    assert report["registers_read"] == 20
    assert sum(len(item["registers"]) for item in trial["reads"]) == 20
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("prefix_length,expected", [(1, 1008), (11, 1018), (257, 1264)])
def test_point_binding_relocates_when_preceding_model_is_extended(prefix_length, expected):
    discovery = scanner((1000,))
    report = discovery.scan(ReadOnlyClient(mapping(1000, [(62000, [0] * prefix_length), (777, [20] * 9)])))
    binding = discovery.resolve_point(report, model_id=777, instance=0, offset=1, count=2)
    assert {key: binding[key] for key in ("kind", "address", "count", "device_id")} == {
        "kind": "holding_registers", "address": expected, "count": 2, "device_id": 7}
    assert not {"units", "writable", "physical_validation", "identity"}.intersection(binding)


def test_all_authored_bases_are_tried_and_wrong_signature_is_preserved():
    words = {3: 1, 4: 2, **mapping(700, [(1000, [])])}
    client = ReadOnlyClient(words)
    report = scanner((3, 700)).scan(client)
    assert [trial["base_address"] for trial in report["trials"]] == [3, 700]
    assert report["trials"][0]["status"] == "not_found"
    assert report["trials"][0]["signature"] == [1, 2]
    assert report["trials"][1]["complete"] is True
    assert report["complete"] is True
    assert client.calls[0] == ("holding_registers", 3, 2, 7)


def test_not_found_never_claims_complete():
    report = scanner((22,)).scan(ReadOnlyClient({22: 0, 23: 0}))
    assert report["status"] == "not_found" and report["complete"] is False


def test_no_implicit_256_model_or_identity_admission_cap():
    models = [(61000, []) for _ in range(300)]
    report = scanner((0,)).scan(ReadOnlyClient(mapping(0, models)))
    assert report["complete"] is True
    assert len(report["trials"][0]["models"]) == 300
    assert report["trials"][0]["models"][-1]["instance"] == 299


def test_explicit_budget_retains_observed_next_header_without_claiming_complete():
    client = ReadOnlyClient(mapping(100, [(777, [8]), (888, [9])]))
    report = scanner((100,), max_models=1).scan(client)
    trial = report["trials"][0]
    assert report["complete"] is False
    assert trial["status"] == "budget_exhausted" and trial["end_marker"] is None
    assert len(trial["models"]) == 1
    assert trial["headers"][-1] == {"address": 105, "registers": [888, 1]}
    assert not any(address == 107 for _, address, _, _ in client.calls)


def test_end_marker_after_exact_budget_is_still_recognized():
    report = scanner((0,), max_models=1).scan(ReadOnlyClient(mapping(0, [(123, [])])))
    assert report["complete"] is True


def test_payload_reads_are_chunked_at_modbus_125_and_input_registers_supported():
    client = ReadOnlyClient(mapping(50, [(500, list(range(251)))]))
    report = scanner((50,), register_kind="input_registers", read_model_data=True).scan(client)
    model = report["trials"][0]["models"][0]
    assert model["registers"] == list(range(251))
    assert [(r["address"], r["count"]) for r in model["data_reads"]] == [(54, 125), (179, 125), (304, 1)]
    assert all(kind == "input_registers" and count <= 125 for kind, _, count, _ in client.calls)


@pytest.mark.parametrize("failure,status", [
    (TimeoutError("controlled timeout"), "read_error"),
    (Registers([], True), "protocol_error"),
    (Registers([1]), "invalid_response"),
    (None, "invalid_response"),
])
def test_missing_end_or_failed_header_is_explicit_and_retains_models(failure, status):
    client = ReadOnlyClient(mapping(20, [(9, [2])], end=False), {25: failure})
    report = scanner((20,)).scan(client)
    assert len(report["trials"][0]["models"]) == 1
    assert report["complete"] is False
    assert report["trials"][0]["status"] == status
    assert report["trials"][0]["reads"][-1]["status"] == status


def test_model_data_error_preserves_prior_chunk_and_walks_next_header():
    client = ReadOnlyClient(mapping(50, [(500, list(range(130)))]), {179: TimeoutError("controlled timeout")})
    report = scanner((50,), read_model_data=True).scan(client)
    trial = report["trials"][0]
    model = trial["models"][0]
    assert model["status"] == "read_error" and model["registers"] == list(range(125))
    assert model["data_reads"][-1]["status"] == "read_error"
    assert trial["end_marker"] == {"address": 184, "registers": [0xFFFF, 0]}
    assert report["complete"] is False and trial["status"] == "partial"


def test_end_marker_with_nonzero_length_is_structurally_invalid():
    words = {0: 0x5375, 1: 0x6E53, 2: 0xFFFF, 3: 1}
    report = scanner((0,)).scan(ReadOnlyClient(words))
    trial = report["trials"][0]
    assert trial["status"] == "invalid_end_marker"
    assert trial["end_marker"] is None and report["complete"] is False
    assert trial["headers"] == [{"address": 2, "registers": [0xFFFF, 1]}]


def test_payload_or_next_header_out_of_address_space_never_sends_invalid_read():
    client = ReadOnlyClient({65530: 0x5375, 65531: 0x6E53, 65532: 77, 65533: 3})
    report = scanner((65530,), read_model_data=True).scan(client)
    assert report["trials"][0]["status"] == "address_bounds"
    assert report["trials"][0]["models"][0]["status"] == "address_bounds"
    assert client.calls == [("holding_registers", 65530, 2, 7), ("holding_registers", 65532, 2, 7)]


def test_no_end_header_room_cannot_be_misreported_as_complete():
    client = ReadOnlyClient({65532: 0x5375, 65533: 0x6E53, 65534: 77, 65535: 0})
    report = scanner((65532,)).scan(client)
    assert report["trials"][0]["status"] == "address_bounds" and report["complete"] is False
    assert all(address + count <= 65536 for _, address, count, _ in client.calls)


@pytest.mark.parametrize("offset,count", [(-1, 1), (0, 0), (8, 2), (9, 1), (False, 1), (0, 126)])
def test_resolution_rejects_range_outside_discovered_payload(offset, count):
    discovery = scanner((0,))
    report = discovery.scan(ReadOnlyClient(mapping(0, [(777, [0] * 9)])))
    with pytest.raises(ValueError):
        discovery.resolve_point(report, model_id=777, instance=0, offset=offset, count=count)


def test_resolution_rejects_ambiguous_bases_and_allows_operator_selected_trial():
    discovery = scanner((0, 100))
    report = discovery.scan(ReadOnlyClient({**mapping(0, [(777, [0] * 9)]), **mapping(100, [(777, [0] * 9)])}))
    with pytest.raises(ValueError, match="ambiguous"):
        discovery.resolve_point(report, model_id=777, instance=0, offset=1, count=1)
    binding = discovery.resolve_point(report["trials"][1], model_id=777, instance=0, offset=1, count=1)
    assert binding["address"] == 105


def test_cancellation_before_any_read_retains_cancelled_receipt():
    discovery = scanner((0,))
    client = ReadOnlyClient(mapping(0, []))
    with pytest.raises(ExecutionCancelled) as caught:
        discovery.scan(client, cancelled=lambda: True)
    assert client.calls == []
    assert caught.value.report is discovery.last_report
    assert caught.value.partial_report["status"] == "cancelled"
    assert caught.value.report["complete"] is False


def test_cancellation_after_read_retains_last_response_and_partial_model():
    discovery = scanner((0,), read_model_data=True)
    client = ReadOnlyClient(mapping(0, [(777, [3] * 200)]))
    with pytest.raises(ExecutionCancelled) as caught:
        discovery.scan(client, cancelled=lambda: len(client.calls) >= 3)
    trial = caught.value.report["trials"][0]
    assert trial["models"][0]["registers"] == [3] * 125
    assert trial["models"][0]["status"] == "cancelled"
    assert caught.value.report["registers_read"] == 129
    assert trial["reads"][-1]["registers"] == [3] * 125


@pytest.mark.parametrize("change", [
    {"base_addresses": None}, {"base_addresses": []}, {"base_addresses": [False]},
    {"base_addresses": [-1]}, {"base_addresses": [65536]}, {"device_id": True},
    {"device_id": -1}, {"device_id": 256}, {"max_models": -1}, {"max_models": True},
    {"register_kind": "coils"}, {"read_model_data": 1}, {"base_addresses": "40000"},
])
def test_config_rejects_invalid_protocol_or_operator_fields(change):
    with pytest.raises(ValueError):
        SunSpecScanConfig.from_dict({"base_addresses": [0], "device_id": 7, **change})


def test_config_requires_authored_bases_and_is_frozen_without_mutable_input_alias():
    with pytest.raises(ValueError):
        SunSpecScanConfig.from_dict({"device_id": 7})
    bases = [9]
    config = SunSpecScanConfig.from_dict({"base_addresses": bases, "device_id": 255})
    bases.append(10)
    assert config.base_addresses == (9,) and config.max_models is None
    with pytest.raises(FrozenInstanceError):
        config.device_id = 8


def test_serial_enumeration_uses_only_comports_metadata():
    calls = []
    port = SimpleNamespace(device="/dev/fixture", name="fixture", description="metadata", hwid="usb",
                           vid=123, pid=456, serial_number="fixture-serial", location="1-2", manufacturer="fixture",
                           product="adapter", interface=None)
    class Library:
        def comports(self):
            calls.append("comports")
            return [port]
        def __getattr__(self, name):
            pytest.fail("serial enumeration must not construct or open a port")
    report = enumerate_serial_ports(library=Library())
    assert calls == ["comports"] and report["status"] == "ok"
    assert report["ports"][0]["device"] == "/dev/fixture"
    assert report["ports"][0]["vid"] == 123


def test_can_enumeration_only_calls_public_detector_for_explicit_interfaces():
    calls = []
    class Library:
        def detect_available_configs(self, *, interfaces, timeout):
            calls.append((interfaces, timeout))
            return [{"interface": "socketcan", "channel": "vcan-fixture"}]
        def __getattr__(self, name):
            pytest.fail("enumeration must not construct a CAN Bus")
    report = enumerate_can_interfaces(["socketcan"], library=Library())
    assert calls == [(["socketcan"], 5.0)]
    assert report["configurations"] == [{"interface": "socketcan", "channel": "vcan-fixture"}]
    assert report["discovery_may_send_traffic"] is True
    with pytest.raises(ValueError):
        enumerate_can_interfaces([], library=Library())


def test_can_empty_results_explain_backend_and_platform_ambiguity():
    report = enumerate_can_interfaces(["unsupported-fixture"], library=SimpleNamespace(
        detect_available_configs=lambda **kwargs: []))
    assert report["status"] == "unavailable_or_empty"
    assert any("unsupported" in value for value in report["limitations"])


@pytest.mark.parametrize("timeout", [0, .125])
def test_can_discovery_forwards_and_reports_authored_timeout(timeout):
    calls = []
    def detect(*, interfaces, timeout):
        calls.append((interfaces, timeout))
        return []
    report = enumerate_can_interfaces(["socketcan"], timeout=timeout,
                                      library=SimpleNamespace(detect_available_configs=detect))
    assert calls == [(["socketcan"], timeout)]
    assert report["timeout"] == timeout


@pytest.mark.parametrize("timeout", [-.001, float("inf"), float("nan"), True, "1"])
def test_can_discovery_rejects_invalid_timeout_before_detector(timeout):
    def forbidden(**kwargs):
        pytest.fail("invalid timeout must fail before native detection")
    with pytest.raises(ValueError, match="timeout"):
        enumerate_can_interfaces(["socketcan"], timeout=timeout,
                                 library=SimpleNamespace(detect_available_configs=forbidden))


def test_enumeration_errors_are_retained_as_limitations():
    def unavailable(**kwargs):
        raise ImportError("controlled missing platform dependency")
    serial_report = enumerate_serial_ports(library=SimpleNamespace(comports=unavailable))
    can_report = enumerate_can_interfaces(["fixture"], library=SimpleNamespace(detect_available_configs=unavailable))
    assert serial_report["status"] == can_report["status"] == "unavailable"
    assert "controlled missing" in serial_report["error"]
    assert "controlled missing" in can_report["error"]


@contextmanager
def owned_register_server(words):
    """Independent MBAP/register oracle; rejects every non-read function code."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(.1)
    stopped = Event()
    requests, failures = [], []

    def receive(connection, size):
        data = bytearray()
        while len(data) < size:
            chunk = connection.recv(size - len(data))
            if not chunk:
                raise EOFError
            data.extend(chunk)
        return bytes(data)

    def serve():
        try:
            connection=None
            while not stopped.is_set():
                try:
                    connection=listener.accept()[0]
                    break
                except socket.timeout:
                    continue
            if connection is None:return
            with connection:
                connection.settimeout(2)
                while not stopped.is_set():
                    try:
                        header = receive(connection, 6)
                        body = receive(connection, int.from_bytes(header[4:6], "big"))
                    except EOFError:
                        break
                    unit, function = body[:2]
                    address = int.from_bytes(body[2:4], "big")
                    count = int.from_bytes(body[4:6], "big")
                    assert function in (3, 4), "discovery attempted a non-read function code"
                    assert header[2:4] == b"\x00\x00" and len(body) == 6
                    assert 1 <= count <= 125 and address + count <= 65536
                    requests.append((unit, function, address, count))
                    payload = bytes([unit, function, count * 2]) + b"".join(
                        words[index].to_bytes(2, "big") for index in range(address, address + count))
                    connection.sendall(header[:4] + len(payload).to_bytes(2, "big") + payload)
        except Exception as exc:
            if not stopped.is_set():
                failures.append(exc)

    thread = Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], requests
    finally:
        stopped.set()
        listener.close()
        thread.join(3)
        assert not thread.is_alive(), "owned register emulator failed to stop"
        assert not failures, failures


@pytest.mark.parametrize("kind,function", [("holding_registers", 3), ("input_registers", 4)])
def test_installed_pymodbus_scans_owned_loopback_with_unit255_and_read_only_packets(kind, function):
    pytest.importorskip("pymodbus.client")
    words = mapping(1750, [(64009, list(range(260))), (777, [3, 4])])
    discovery = scanner((1750,), register_kind=kind, read_model_data=True)
    discovery = SunSpecDiscovery(SunSpecScanConfig.from_dict({
        **discovery.config.as_dict(), "device_id": 255,
    }))
    with owned_register_server(words) as (port, requests):
        client = ModbusEndpoint.from_dict({"host": "127.0.0.1", "port": port}).create_client(.5)
        try:
            assert client.connect()
            report = discovery.scan(client)
            assert client.connected, "supplied client remains owned by its caller"
        finally:
            client.close()
    assert report["complete"] is True and report["registers_read"] == 270
    assert all(unit == 255 and code == function for unit, code, _, _ in requests)
    assert [count for _, _, address, count in requests if 1754 <= address < 2014] == [125, 125, 10]
    binding = discovery.resolve_point(report, model_id=777, instance=0, offset=1, count=1)
    assert binding["address"] == 2017 and binding["device_id"] == 255
