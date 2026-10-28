"""No physical/network I/O: a device fixture exercises the durable final boundary."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import subprocess
import sys
import threading

import pytest

from tinystudio.lab.gateway import CommandGateway, GatewayError, GatewayBusy, transport_for


class Clock:
    def __init__(self):
        self.now = 1000.
    def __call__(self):
        return self.now


def binding():
    return {"endpoint_id": "device-a", "operator_id": "local-operator", "transport": {"kind": "fixture"},
            "limits": {"voltage": {"minimum": -1, "maximum": 5, "unit": "V"}},
            "local_stop": {"voltage": {"value": 0, "unit": "V"}}, "send_timeout": .2}


class InjectedCrash(BaseException):
    pass


class Device:
    kind = "fixture"
    def __init__(self, clock):
        self.clock = clock
        self.effects = []
        self.outcomes = {}
        self.value = 0
        self.epoch = 0
        self.lose_ack = False
        self.crash = False
        self.before_write = lambda: None
        self.after_write = lambda: None
        self.journal_path = None
        self.bad_ack = False
    def exchange(self, request, *, timeout, cancelled, authorize):
        if request["kind"] == "reconcile":
            return {**{key: request[key] for key in ("endpoint_id", "command_id", "epoch")},
                    "status": "state", "device_epoch": self.epoch, "stopped": self.value == 0,
                    "parameters": {"voltage": {"value": self.value, "unit": "V"}}, "command_outcomes": self.outcomes}
        self.before_write()
        request = authorize()  # The actual write-boundary recheck, after connection/setup delay.
        if cancelled():
            raise RuntimeError("cancelled before physical fixture effect")
        if self.journal_path:
            with sqlite3.connect(self.journal_path) as external_reader:
                assert external_reader.execute("select state from commands where command_id=?", (request["command_id"],)).fetchone()[0] == "pending"
        assert request["epoch"] >= self.epoch
        assert request["expires_at"] > self.clock()
        self.epoch = request["epoch"]
        self.value = request["parameters"]["voltage"]["value"]
        self.effects.append(copy.deepcopy(request))
        self.outcomes[request["command_id"]] = "applied"
        self.after_write()
        if self.crash:
            self.crash = False
            raise InjectedCrash()
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError("effect happened, acknowledgement was lost")
        return {"endpoint_id": request["endpoint_id"], "command_id": request["command_id"],
                "epoch": request["epoch"] + (1 if self.bad_ack else 0), "status": "confirmed", "applied": True}


def setup(tmp_path):
    clock = Clock()
    device = Device(clock)
    path = tmp_path / "gateway.sqlite3"
    device.journal_path = path
    gateway = CommandGateway(path, binding(), device, clock=clock)
    return clock, device, gateway


def dispatch(gateway, epoch, command_id="c1", value=1, **kwargs):
    return gateway.dispatch(command_id, owner="run-a", epoch=epoch,
                            parameters={"voltage": {"value": value, "unit": "V"}}, expires_at=1005, **kwargs)


def test_pending_is_durable_before_effect_and_duplicate_does_not_send(tmp_path):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        epoch = gateway.acquire("run-a")["epoch"]
        first = dispatch(gateway, epoch)
        assert dispatch(gateway, epoch) == first
        assert len(device.effects) == 1
        assert gateway.journal()[0]["state"] == "confirmed"
        with sqlite3.connect(gateway.path) as reader:
            assert reader.execute("pragma journal_mode").fetchone()[0] == "wal"
        assert gateway.snapshot()["physical_qualified"] is False


def test_ack_lost_after_effect_never_retries_and_requires_reconciliation(tmp_path):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        epoch = gateway.acquire("run-a")["epoch"]
        device.lose_ack = True
        with pytest.raises(GatewayError, match="outcome unknown"):
            dispatch(gateway, epoch)
        assert len(device.effects) == 1
        assert gateway.snapshot()["state"] == "recovery_required"
        with pytest.raises(GatewayError):
            dispatch(gateway, epoch)
        with pytest.raises(GatewayError, match="reassignment"):
            gateway.acquire("run-b")
        with pytest.raises(GatewayError, match="stopped"):
            gateway.reconcile("local-operator")
        gateway.local_stop("local-operator", request_id="operator-stop")
        assert len(device.effects) == 2
        assert gateway.local_stop("local-operator", request_id="operator-stop")["status"] == "confirmed"
        assert len(device.effects) == 2
        result = gateway.reconcile("local-operator")
        assert result["replay"] is False
        assert gateway.journal()[0]["state"] == "reconciled_applied"
        fresh = gateway.acquire("run-a")["epoch"]
        assert fresh > epoch
        with pytest.raises(GatewayError, match="identity"):
            dispatch(gateway, fresh)
        assert len(device.effects) == 2


def test_crash_after_effect_restart_requires_recovery_and_retains_pending(tmp_path):
    clock, device, gateway = setup(tmp_path)
    epoch = gateway.acquire("run-a")["epoch"]
    device.crash = True
    with pytest.raises(InjectedCrash):
        dispatch(gateway, epoch)
    assert gateway.journal()[0]["state"] == "pending"
    gateway.close()
    with CommandGateway(gateway.path, binding(), device, clock=clock) as restarted:
        assert restarted.snapshot()["state"] == "recovery_required"
        assert len(device.effects) == 1
        assert restarted.journal()[0]["state"] == "pending"
        with pytest.raises(GatewayError):
            dispatch(restarted, epoch)
        restarted.local_stop("local-operator", request_id="restart-stop")
        restarted.reconcile("local-operator")
        assert len(device.effects) == 2


def test_final_transport_boundary_rechecks_expiry_after_connection_delay(tmp_path):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        epoch = gateway.acquire("run-a")["epoch"]
        device.before_write = lambda: setattr(clock, "now", 1005)
        with pytest.raises(GatewayError):
            dispatch(gateway, epoch)
        assert device.effects == []
        assert gateway.snapshot()["state"] == "recovery_required"


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1.001, 5.001])
def test_typed_numeric_limits_reject_without_effect(tmp_path, value):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        epoch = gateway.acquire("run-a")["epoch"]
        with pytest.raises(GatewayError):
            dispatch(gateway, epoch, value=value)
        assert device.effects == []
        assert gateway.journal() == []


def test_wrong_units_unknown_parameters_stale_epoch_and_competing_owner(tmp_path):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        epoch = gateway.acquire("run-a")["epoch"]
        with pytest.raises(GatewayError):
            gateway.acquire("run-b")
        for parameters in ({"voltage": {"value": 1, "unit": "A"}}, {"motor": {"value": 1, "unit": "V"}}):
            with pytest.raises(GatewayError):
                gateway.dispatch("invalid", owner="run-a", epoch=epoch, parameters=parameters, expires_at=1005)
        with pytest.raises(GatewayError):
            dispatch(gateway, epoch-1)
        with pytest.raises(GatewayError):
            dispatch(gateway, True)
        assert device.effects == []


def test_concurrent_duplicates_produce_one_effect(tmp_path):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        epoch = gateway.acquire("run-a")["epoch"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: dispatch(gateway, epoch), range(12)))
        assert all(result == results[0] for result in results)
        assert len(device.effects) == 1


def test_journal_lock_is_exclusive_in_another_process(tmp_path):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        script = "from tinystudio.lab.gateway import CommandGateway,GatewayBusy; import json,sys\ntry: CommandGateway(sys.argv[1],json.loads(sys.argv[2]),object())\nexcept GatewayBusy: sys.exit(0)\nsys.exit(9)"
        result = subprocess.run([sys.executable, "-c", script, str(gateway.path), json.dumps(binding())], capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr


def test_binding_cannot_be_changed_on_existing_journal(tmp_path):
    clock, device, gateway = setup(tmp_path)
    gateway.close()
    other = binding()
    other["endpoint_id"] = "different-device"
    with pytest.raises(GatewayError, match="different endpoint"):
        CommandGateway(gateway.path, other, device, clock=clock)


def test_cancel_before_effect_sends_only_explicit_bound_local_stop(tmp_path):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        epoch = gateway.acquire("run-a")["epoch"]
        with pytest.raises(GatewayError, match="cancelled"):
            dispatch(gateway, epoch, cancelled=lambda: True)
        assert len(device.effects) == 1
        assert device.effects[0]["kind"] == "local_stop"
        assert device.value == 0
        assert gateway.snapshot()["state"] == "recovery_required"


def test_bad_application_ack_does_not_count_as_confirmed(tmp_path):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        epoch = gateway.acquire("run-a")["epoch"]
        device.bad_ack = True
        with pytest.raises(GatewayError, match="outcome unknown"):
            dispatch(gateway, epoch)
        assert gateway.journal()[0]["state"] == "unknown"


def test_plugins_construct_without_importing_or_opening_live_endpoints():
    for config in ({"kind": "serial", "port": "/nonexistent/device"},
                   {"kind": "modbus", "host": "192.0.2.1", "request_register": 0, "response_register": 200},
                   {"kind": "mqtt", "host": "192.0.2.1", "command_topic": "device/cmd", "ack_topic": "device/ack"}):
        plugin = transport_for({"transport": config})
        assert plugin.kind == config["kind"]


def test_same_endpoint_cannot_be_claimed_through_a_second_journal(tmp_path):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        with pytest.raises(GatewayBusy, match="endpoint"):
            CommandGateway(tmp_path / "competing.sqlite3", binding(), device, clock=clock)


def test_clock_regression_revokes_durably_and_expired_lease_cannot_renew(tmp_path):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        epoch = gateway.acquire("run-a", ttl=1)["epoch"]
        clock.now += 1
        with pytest.raises(GatewayError):
            gateway.heartbeat("run-a", epoch)
        assert gateway.snapshot()["state"] == "recovery_required"
        assert device.effects == []
        clock.now -= 1
        with pytest.raises(GatewayError, match="regressed"):
            gateway.snapshot()
        with sqlite3.connect(gateway.path) as reader:
            assert reader.execute("select reason from gateway").fetchone()[0] == "gateway_clock_regressed"


def test_reconciliation_cannot_omit_an_uncertain_device_outcome(tmp_path):
    clock, device, gateway = setup(tmp_path)
    with gateway:
        epoch = gateway.acquire("run-a")["epoch"]
        device.lose_ack = True
        with pytest.raises(GatewayError):
            dispatch(gateway, epoch)
        gateway.local_stop("local-operator", request_id="stop")
        del device.outcomes["c1"]
        with pytest.raises(GatewayError, match="every uncertain"):
            gateway.reconcile("local-operator")
        assert gateway.snapshot()["state"] == "recovery_required"


def test_cancellation_after_effect_retains_ack_and_sends_bound_stop(tmp_path):
    clock, device, gateway = setup(tmp_path)
    cancelled = threading.Event()
    with gateway:
        epoch = gateway.acquire("run-a")["epoch"]
        device.after_write = cancelled.set
        with pytest.raises(GatewayError, match="cancelled after dispatch"):
            dispatch(gateway, epoch, cancelled=cancelled.is_set)
        assert [request["kind"] for request in device.effects] == ["command", "local_stop"]
        assert device.value == 0
        assert [entry["state"] for entry in gateway.journal()] == ["confirmed", "confirmed"]
        assert gateway.snapshot()["state"] == "recovery_required"


def test_modbus_staged_commit_contract_without_any_network(monkeypatch):
    import types
    from tinystudio.lab.gateway import ModbusMailboxTransport
    calls = []
    request = {"protocol": 1, "kind": "command", "endpoint_id": "fixture", "command_id": "c1", "epoch": 7,
               "parameters": {"voltage": {"value": 1., "unit": "V"}}, "expires_at": 2000}
    ack = {"endpoint_id": "fixture", "command_id": "c1", "epoch": 7, "status": "confirmed", "applied": True}
    encoded = json.dumps(ack).encode()
    padded = encoded + (b"\0" if len(encoded) % 2 else b"")
    import struct
    mailbox = [len(encoded), *struct.unpack(f">{len(padded)//2}H", padded)]
    mailbox += [0] * (512 - len(mailbox))
    class Result:
        def __init__(self, registers=None):
            self.registers = registers
        def isError(self):
            return False
    class FakeClient:
        def __init__(self, *args, **kwargs):
            assert kwargs["retries"] == 0
        def connect(self):
            calls.append("connect")
            return True
        def write_registers(self, address, values, *, device_id):
            assert len(values) <= 123
            calls.append(("write", address, list(values)))
            return Result()
        def read_holding_registers(self, address, *, count, device_id):
            assert count <= 125
            return Result(mailbox[address-1000:address-1000+count])
        def close(self):
            calls.append("close")
    module = types.ModuleType("pymodbus.client")
    module.ModbusTcpClient = FakeClient
    monkeypatch.setitem(sys.modules, "pymodbus.client", module)
    def authorize():
        calls.append("final_authorize")
        return request
    plugin = ModbusMailboxTransport({"host": "192.0.2.1", "request_register": 0, "response_register": 1000})
    assert plugin.exchange(request, timeout=.2, cancelled=lambda: False, authorize=authorize) == ack
    writes = [call for call in calls if isinstance(call, tuple)]
    assert writes[0] == ("write", 0, [0])
    assert writes[-1][1] == 0 and writes[-1][2][0] > 0
    assert calls.index("final_authorize") == calls.index(writes[-1]) - 1


def test_mqtt_waits_for_connack_subscription_and_device_ack_without_network(monkeypatch):
    import types
    from tinystudio.lab.gateway import MQTTTransport
    calls = []
    request = {"endpoint_id": "fixture", "command_id": "c1", "epoch": 1, "expires_at": 2000}
    class FakeClient:
        def __init__(self, *args, **kwargs):
            assert kwargs["reconnect_on_failure"] is False
            self.phase = 0
        def connect(self, *args, **kwargs):
            calls.append("connect")
        def loop(self, timeout):
            if self.phase == 0:
                self.on_connect(self, None, None, types.SimpleNamespace(is_failure=False), None)
                self.phase = 1
            elif self.phase == 2:
                self.on_subscribe(self, None, 1, [types.SimpleNamespace(is_failure=False)], None)
                self.phase = 3
            elif self.phase == 4:
                ack = {**request, "status": "confirmed", "applied": True}
                self.on_message(self, None, types.SimpleNamespace(payload=json.dumps(ack).encode(), retain=False))
                self.phase = 5
            return 0
        def subscribe(self, topic, qos):
            assert self.phase == 1
            calls.append("subscribe")
            self.phase = 2
            return 0, 1
        def publish(self, topic, payload, qos, retain):
            assert self.phase == 3 and calls[-1] == "authorize"
            assert json.loads(payload) == request and retain is False
            calls.append("publish")
            self.phase = 4
            return types.SimpleNamespace(rc=0)
        def disconnect(self):
            calls.append("disconnect")
    root = types.ModuleType("paho")
    package = types.ModuleType("paho.mqtt")
    module = types.ModuleType("paho.mqtt.client")
    root.mqtt, package.client = package, module
    module.Client = FakeClient
    module.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
    module.MQTTv311, module.MQTT_ERR_SUCCESS = 4, 0
    for name, value in (("paho", root), ("paho.mqtt", package), ("paho.mqtt.client", module)):
        monkeypatch.setitem(sys.modules, name, value)
    def authorize():
        calls.append("authorize")
        return request
    plugin = MQTTTransport({"host": "192.0.2.1", "command_topic": "fixture/cmd", "ack_topic": "fixture/ack"})
    ack = plugin.exchange(request, timeout=.2, cancelled=lambda: False, authorize=authorize)
    assert ack["status"] == "confirmed"
    assert calls == ["connect", "subscribe", "authorize", "publish", "disconnect"]
