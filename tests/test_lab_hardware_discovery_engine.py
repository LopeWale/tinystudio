"""Engine evidence uses controlled collaborators or explicitly owned loopback."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import base64
import hashlib
from importlib import metadata
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinystudio.lab.engine import ExecutionCancelled


def engine():
    from tinystudio.lab import hardware_discovery_engine
    return hardware_discovery_engine


def spec(method="sunspec.scan", **parameters):
    selected = {"method": method, "binding_id": "owned-binding", **parameters}
    grant = {"binding_id": "owned-binding", "read_only": True, "methods": [method], "limits": {}}
    if method == "sunspec.scan":
        selected.setdefault("scan", {"base_addresses": [17], "device_id": 255})
        grant.update(endpoint={"host": "127.0.0.1", "port": 1502, "framing": "tcp", "allow_dns": False},
                     device_id=255)
    elif method == "can.enumerate":
        selected.setdefault("interfaces", ["virtual"])
        grant["interfaces"] = ["virtual", "socketcan"]
    return {"definition": {"engine": "hardware.discover", "operating_mode": "acquisition", "parameters": selected},
            "_runtime_grants": {"discovery": grant}}


class Clock:
    def __init__(self):
        self.ticks = 0

    def time(self):
        self.ticks += 1
        return 1730000000.0 + self.ticks

    def monotonic(self):
        self.ticks += 1
        return 100.0 + self.ticks


class Client:
    def __init__(self, trace, *, error=None, words=None, connect_error=None, close_error=None):
        self.trace = trace
        self.words = words or {17: 0x5375, 18: 0x6E53, 19: 64001, 20: 2, 21: 8, 22: 9,
                               23: 0xFFFF, 24: 0}
        self.error = error
        self.connect_error = connect_error
        self.close_error = close_error
        self.connected = False
        self.closed = False
        self.calls = []

    def connect(self):
        if self.connect_error:
            raise self.connect_error
        self.connected = True
        return True

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error

    def read_holding_registers(self, address, *, count, device_id):
        self.calls.append((address, count, device_id))
        tx = b"original-request"
        rx = b"original-response"
        assert self.trace(True, tx) is tx
        assert self.trace(False, rx) is rx
        if self.error:
            raise self.error
        return SimpleNamespace(registers=[self.words[i] for i in range(address, address + count)],
                               isError=lambda: False)

    read_input_registers = read_holding_registers


def run(tmp_path, authored=None, **client_options):
    clients = []
    selected_endpoints = []

    def factory(endpoint, *, timeout, trace_packet):
        selected_endpoints.append((endpoint, timeout))
        client = Client(trace_packet, **client_options)
        clients.append(client)
        return client

    result = engine().execute_hardware_discovery(authored or spec(), tmp_path, lambda event: None,
                                                  lambda: False, client_factory=factory, clock=Clock())
    report = json.loads((tmp_path / "device-discovery.json").read_text())
    return result, report, clients, selected_endpoints


def test_paths_catalog_and_selection_are_importable_without_native_sdks(monkeypatch, tmp_path):
    module = engine()
    calls = []
    monkeypatch.setattr(module.importlib, "import_module", lambda name: calls.append(name))
    catalog = module.method_catalog()
    assert {row["id"] for row in catalog["methods"]} == {"serial.enumerate", "can.enumerate", "sunspec.scan"}
    assert all(row["schema"]["additionalProperties"] is False for row in catalog["methods"])
    selected = module.validate_selection(spec()["definition"])
    assert selected.scan.base_addresses == (17,) and selected.scan.device_id == 255
    with pytest.raises(FrozenInstanceError):
        selected.binding_id = "changed"
    paths = module.HardwareDiscoveryPaths(tmp_path)
    assert paths.report.name == "device-discovery.json" and paths.wire.name == "discovery-wire.jsonl"
    with pytest.raises(FrozenInstanceError):
        paths.root = Path("elsewhere")
    assert calls == []


def test_canonical_experiment_definition_empty_defaults_are_accepted_but_active_fields_are_rejected():
    from tinystudio.lab.contracts import ExperimentDefinition

    definition = ExperimentDefinition(name='Owned discovery', engine='hardware.discover',
                                      operating_mode='acquisition', parameters=spec()['definition']['parameters'],
                                      execution={'profile': 'trusted_native'}).model_dump()
    assert definition['schema_version'] == 1
    assert engine().validate_selection(definition).scan.device_id == 255
    definition['pipeline'] = [{'op': 'normalize'}]
    with pytest.raises(ValueError, match='pipeline'):
        engine().validate_selection(definition)
    definition['pipeline'] = []
    definition['schema_version'] = 2
    with pytest.raises(ValueError, match='schema_version'):
        engine().validate_selection(definition)


@pytest.mark.parametrize('operating_mode', ['replay', 'simulation', 'shadow', 'physical_control'])
def test_live_discovery_refuses_other_canonical_operating_modes_before_io(tmp_path, operating_mode):
    from tinystudio.lab.contracts import ExperimentDefinition

    authored = spec()
    authored['definition'] = ExperimentDefinition(
        name='Owned discovery', engine='hardware.discover', operating_mode=operating_mode,
        parameters=authored['definition']['parameters'], execution={'profile': 'trusted_native'}).model_dump()
    calls = []
    with pytest.raises(ValueError, match='acquisition'):
        engine().execute_hardware_discovery(authored, tmp_path / 'untouched', lambda _: None, lambda: False,
                                             client_factory=lambda *a, **kw: calls.append(a))
    assert not calls and not (tmp_path / 'untouched').exists()


def test_registry_selects_the_authored_instrument_from_two_host_bindings(tmp_path):
    authored = spec()
    selected = authored['_runtime_grants']['discovery']
    selected['endpoint']['port'] = 2502
    other = deepcopy(selected)
    other.update(binding_id='other-instrument', device_id=7)
    other['endpoint']['port'] = 3502
    authored['_runtime_grants'] = {'bindings': {'other-instrument': other, 'owned-binding': selected}}
    original = deepcopy(authored)
    result, report, clients, endpoints = run(tmp_path, authored)
    assert authored == original
    assert report['binding_id'] == 'owned-binding' and result['records'] == 1
    assert report['source']['endpoint']['port'] == 2502 and endpoints[0][0].port == 2502
    assert all(device_id == 255 for _, _, device_id in clients[0].calls)


def test_unknown_registry_instrument_is_refused_before_io(tmp_path):
    authored = spec()
    grant = authored['_runtime_grants']['discovery']
    authored['_runtime_grants'] = {'bindings': {'owned-binding': grant}}
    authored['definition']['parameters']['binding_id'] = 'not-configured'
    calls = []
    with pytest.raises(ValueError, match='not configured'):
        engine().execute_hardware_discovery(authored, tmp_path / 'untouched', lambda _: None, lambda: False,
                                             client_factory=lambda *a, **kw: calls.append(a))
    assert not calls and not (tmp_path / 'untouched').exists()


def test_implementation_hash_covers_binding_registry_source(monkeypatch):
    module = engine()
    before = module.implementation_sha256()
    original = Path.read_bytes

    def changed_source(path):
        data = original(path)
        return data + b'\n# controlled identity change\n' if path.name == 'hardware_bindings.py' else data

    monkeypatch.setattr(Path, 'read_bytes', changed_source)
    assert module.implementation_sha256() != before


@pytest.mark.parametrize("change", [
    lambda s: s.pop("_runtime_grants"),
    lambda s: s["_runtime_grants"]["discovery"].update(read_only=False),
    lambda s: s["_runtime_grants"]["discovery"].update(binding_id="different"),
    lambda s: s["_runtime_grants"]["discovery"].update(methods=["serial.enumerate"]),
    lambda s: s["_runtime_grants"]["discovery"].update(device_id=7),
    lambda s: s["_runtime_grants"]["discovery"].update(base_addresses=[40000]),
    lambda s: s["definition"]["parameters"].update(endpoint={"host": "external.example"}),
    lambda s: s["definition"].update(grants={"discovery": {"read_only": True}}),
    lambda s: s["definition"].update(execution={"grants": {"discovery": {"read_only": True}}}),
    lambda s: s["definition"].update(operating_mode="physical_control"),
    lambda s: s["definition"].update(pipeline=[{"op": "normalize"}]),
    lambda s: s["definition"].update(objectives={"target": "anything"}),
    lambda s: s["definition"]["parameters"]["scan"].update(model_schema="ignored"),
    lambda s: s["_runtime_grants"]["discovery"]["endpoint"].update(host="peer.example", allow_dns=False),
])
def test_authority_or_ignored_semantics_refused_before_io(tmp_path, change):
    authored = spec()
    change(authored)
    factory_calls = []
    with pytest.raises(ValueError):
        engine().execute_hardware_discovery(authored, tmp_path / "untouched", lambda _: None, lambda: False,
                                             client_factory=lambda *a, **k: factory_calls.append(a))
    assert not factory_calls and not (tmp_path / "untouched").exists()


@pytest.mark.parametrize("field,value", [("max_models", 4), ("max_models", None)])
def test_explicit_model_budget_above_operator_cap_refused_before_io(tmp_path, field, value):
    authored = spec()
    authored["definition"]["parameters"]["scan"][field] = value
    authored["_runtime_grants"]["discovery"]["limits"]["max_models"] = 3
    calls = []
    with pytest.raises(ValueError, match="max_models"):
        engine().execute_hardware_discovery(authored, tmp_path, lambda _: None, lambda: False,
                                             client_factory=lambda *a, **k: calls.append(a))
    assert calls == [] and not list(tmp_path.iterdir())


@pytest.mark.parametrize('parameters,limits', [({'timeout': 3}, {'max_timeout': 2}),
                                              ({'max_frame_bytes': 9}, {'max_frame_bytes': 8}),
                                              ({'max_frame_bytes': None}, {'max_frame_bytes': 8})])
def test_timeout_and_capture_budget_refused_before_any_io(tmp_path, parameters, limits):
    authored = spec(**parameters)
    authored['_runtime_grants']['discovery']['limits'] = limits
    calls = []
    with pytest.raises(ValueError, match='operator resource budget'):
        engine().execute_hardware_discovery(authored, tmp_path, lambda _: None, lambda: False,
                                             client_factory=lambda *a, **kw: calls.append(a))
    assert not calls and not list(tmp_path.iterdir())


def test_complete_report_preserves_configuration_clocks_provenance_and_immutable_inputs(tmp_path):
    authored = spec(timeout=2.5)
    authored["definition"]["parameters"]["scan"].update(read_model_data=True, register_kind="input_registers")
    authored["_runtime_grants"]["discovery"]["endpoint"].update(host="chosen.example", allow_dns=True, framing="rtu_over_tcp")
    authored["_runtime_grants"]["discovery"]["limits"].update(max_models=7, max_frame_bytes=9)
    authored["_runtime_secrets"] = {"password": "do-not-copy-this"}
    before = deepcopy(authored)
    result, report, clients, endpoints = run(tmp_path, authored)
    assert authored == before
    assert clients[0].closed and clients[0].connected
    assert all(call[2] == 255 for call in clients[0].calls)
    assert (endpoints[0][0].host, endpoints[0][0].framing, endpoints[0][0].allow_dns, endpoints[0][1]) == (
        "chosen.example", "rtu_over_tcp", True, 2.5)
    assert report["status"] == "complete" and report["format"] == "tinystudio.hardware-discovery.v1"
    assert report["requested_configuration"] == before["definition"]["parameters"]
    assert report["effective_configuration"]["scan"]["max_models"] == 7
    assert report["effective_configuration"]["max_frame_bytes"] == 9
    assert report["discovery"]["trials"][0]["models"][0]["registers"] == [8, 9]
    assert report["clock"]["started"]["wall_time"] < report["clock"]["finished"]["wall_time"]
    assert report["clock"]["started"]["monotonic"] < report["clock"]["finished"]["monotonic"]
    assert len(report["implementation_sha256"]) == 64 and report["runtime"]["python"]
    assert report["qualification"]["qualified"] is False
    assert report["qualification"]["physical_validation"] is False
    assert "do-not-copy-this" not in json.dumps(report)
    assert result["records"] == 1 and result["metrics"]["discovery_status"] == "complete"
    assert {item["role"] for item in result["artifacts"]} == {"device_discovery", "discovery_wire"}
    for artifact in result["artifacts"]:
        assert artifact["visibility"] == "evaluation"
        assert artifact["sha256"] == hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest()


@pytest.mark.parametrize("budget", [0, 3, None])
def test_trace_budget_caps_prefix_while_preserving_full_hash_and_protocol(tmp_path, budget):
    result, report, clients, _ = run(tmp_path, spec(max_frame_bytes=budget))
    traces = [json.loads(line) for line in (tmp_path / "discovery-wire.jsonl").read_text().splitlines()]
    assert len(traces) == 6
    for trace in traces:
        original = b"original-request" if trace["direction"] == "tx" else b"original-response"
        captured = original if budget is None else original[:budget]
        assert trace["size"] == len(original) and trace["sha256"] == hashlib.sha256(original).hexdigest()
        assert trace['capture_scope'] == 'pymodbus_trace_callback_buffer'
        assert base64.b64decode(trace["prefix_base64"]) == captured
        assert trace["captured_bytes"] == len(captured) and trace["truncated"] is (len(captured) < len(original))
        assert trace["wall_time"] > 0 and trace["monotonic"] > 0
    assert "original-request" not in json.dumps(report)
    assert clients[0].closed
    assert report['trace_capture']['transport_wire_reconstruction'] is False


def test_partial_scan_and_model_budget_preserve_observations(tmp_path):
    authored = spec()
    authored["_runtime_grants"]["discovery"]["limits"]["max_models"] = 0
    result, report, clients, _ = run(tmp_path, authored)
    assert report["status"] == "partial" and clients[0].closed
    assert report["discovery"]["trials"][0]["status"] == "budget_exhausted"
    assert result["records"] == 0
    assert report["discovery"]["trials"][0]["signature"] == [0x5375, 0x6E53]


@pytest.mark.parametrize("error,status", [(ConnectionError("owned peer offline"), "failed"),
                                          (ModuleNotFoundError("pymodbus unavailable"), "unavailable")])
def test_open_failure_retains_reports_and_always_closes_client(tmp_path, error, status):
    result, report, clients, _ = run(tmp_path, connect_error=error)
    assert clients[0].closed and report["status"] == status
    assert report["error"]["type"] == type(error).__name__
    assert result["metrics"]["discovery_status"] == status
    assert (tmp_path / "discovery-wire.jsonl").is_file()


def test_close_failure_is_retained_and_does_not_erase_scan(tmp_path):
    result, report, clients, _ = run(tmp_path, close_error=OSError("owned close failure"))
    assert clients[0].closed and report["status"] == "failed"
    assert result["records"] == 1 and report["discovery"]["complete"] is True
    assert report["close_error"]["type"] == "OSError"


@pytest.mark.parametrize("cancel_at", [0, 1, 2])
def test_cancellation_attaches_retained_partial_result_and_closes(tmp_path, cancel_at):
    clients = []

    def factory(endpoint, *, timeout, trace_packet):
        clients.append(Client(trace_packet))
        return clients[-1]

    def cancelled():
        return cancel_at == 0 or bool(clients and len(clients[0].calls) >= cancel_at)

    with pytest.raises(ExecutionCancelled) as caught:
        engine().execute_hardware_discovery(spec(), tmp_path, lambda _: None, cancelled,
                                             client_factory=factory, clock=Clock())
    report = json.loads((tmp_path / "device-discovery.json").read_text())
    assert report["status"] == "cancelled" and report["clock"]["finished"]
    assert all(client.closed for client in clients)
    result = caught.value.evidence_result
    assert result["metrics"]["discovery_status"] == "cancelled"
    assert len(result["artifacts"]) == 2
    if cancel_at:
        assert report["discovery"]["registers_read"] >= 2


def test_serial_enumeration_only_reports_metadata_and_partial_availability(tmp_path, monkeypatch):
    module = engine()
    payload = {"format": "tinystudio.serial-enumeration.v1", "status": "unavailable",
               "ports": [{"device": "fixture-port"}], "error": "fixture enumeration ended"}
    monkeypatch.setattr(module, "enumerate_serial_ports", lambda: deepcopy(payload))
    result, report, clients, _ = run(tmp_path, spec("serial.enumerate"))
    assert result["records"] == 1 and report["status"] == "partial" and clients == []
    assert report["discovery"] == payload


def test_empty_unavailable_enumeration_retains_no_invented_records(tmp_path, monkeypatch):
    module = engine()
    monkeypatch.setattr(module, 'enumerate_can_interfaces', lambda interfaces, timeout: {
        'status': 'unavailable_or_empty', 'configurations': [], 'interfaces': interfaces,
        'discovery_may_send_traffic': True})
    result, report, clients, _ = run(tmp_path, spec('can.enumerate'))
    assert report['status'] == 'unavailable' and result['records'] == 0 and not clients
    assert report['discovery']['discovery_may_send_traffic'] is True


def test_serial_cancelled_after_enumeration_retains_all_observed_metadata(tmp_path, monkeypatch):
    stopped = []

    def enumeration():
        stopped.append(True)
        return {'status': 'ok', 'ports': [{'device': 'owned-fixture'}]}

    module = engine()
    monkeypatch.setattr(module, 'enumerate_serial_ports', enumeration)
    with pytest.raises(ExecutionCancelled) as caught:
        module.execute_hardware_discovery(spec('serial.enumerate'), tmp_path, lambda _: None, lambda: bool(stopped))
    report = json.loads((tmp_path / 'device-discovery.json').read_text())
    assert report['status'] == 'cancelled' and caught.value.evidence_result['records'] == 1


def test_can_uses_only_selected_authorized_providers_and_timeout(tmp_path, monkeypatch):
    module = engine()
    calls = []

    def enumeration(interfaces, *, timeout):
        calls.append((interfaces, timeout))
        return {"status": "ok", "configurations": [{"interface": "virtual", "channel": "owned"}]}

    monkeypatch.setattr(module, "enumerate_can_interfaces", enumeration)
    result, report, clients, _ = run(tmp_path, spec("can.enumerate", timeout=0))
    assert calls == [(["virtual"], 0)] and clients == [] and result["records"] == 1
    assert report["status"] == "complete"
    denied = spec("can.enumerate", interfaces=["ungranted"])
    with pytest.raises(ValueError, match="interfaces"):
        module.execute_hardware_discovery(denied, tmp_path / "denied", lambda _: None, lambda: False)
    assert len(calls) == 1


def test_cancellation_during_close_retains_completed_reads_as_cancelled_evidence(tmp_path):
    stopped = []

    class ClosingClient(Client):
        def close(self):
            super().close()
            stopped.append(True)

    with pytest.raises(ExecutionCancelled) as caught:
        engine().execute_hardware_discovery(spec(), tmp_path, lambda _: None, lambda: bool(stopped),
                                             client_factory=lambda endpoint, **kw: ClosingClient(kw['trace_packet']))
    report = json.loads((tmp_path / 'device-discovery.json').read_text())
    assert report['status'] == 'cancelled' and report['discovery']['complete'] is True
    assert caught.value.evidence_result['records'] == 1


def test_actual_sdk_reads_owned_loopback_and_retains_capped_wire_evidence(tmp_path):
    pytest.importorskip('pymodbus.client')
    from test_lab_device_discovery import mapping, owned_register_server

    authored = spec(timeout=.5, max_frame_bytes=2)
    authored['definition']['parameters']['scan'].update(read_model_data=True)
    with owned_register_server(mapping(17, [(64009, [4, 5, 6])])) as (port, requests):
        authored['_runtime_grants']['discovery']['endpoint']['port'] = port
        result = engine().execute_hardware_discovery(authored, tmp_path, lambda _: None, lambda: False)
    report = json.loads((tmp_path / 'device-discovery.json').read_text())
    traces = [json.loads(line) for line in (tmp_path / 'discovery-wire.jsonl').read_text().splitlines()]
    assert report['status'] == 'complete' and result['records'] == 1
    assert report['discovery']['trials'][0]['models'][0]['registers'] == [4, 5, 6]
    assert len(requests) == 4 and all(unit == 255 and function == 3 for unit, function, _, _ in requests)
    assert len(traces) == 8 and {trace['direction'] for trace in traces} == {'rx', 'tx'}
    assert all(trace['captured_bytes'] == 2 and trace['truncated'] for trace in traces)
    assert report['runtime']['client'] == {'module': 'pymodbus.client.tcp', 'class': 'ModbusTcpClient'}
    assert report['runtime']['dependencies'][0]['version'] == metadata.version('pymodbus')


@pytest.mark.parametrize("changes", [{"scan": {"device_id": 255}}, {"scan": {"base_addresses": [17]}},
                                    {"timeout": "5"}, {"max_frame_bytes": True}, {"binding_id": ""}])
def test_exact_parameter_schema_rejects_invalid_or_missing_choices(changes):
    authored = spec()
    authored["definition"]["parameters"].update(changes)
    with pytest.raises(ValueError):
        engine().validate_selection(authored["definition"])
