"""Opt-in, owned localhost MQTT wire evidence; never physical qualification."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
import socket
import time

import pytest

from hardware_emulators import MqttTestDevice, OwnedMqttBroker
from tinystudio.lab.acquisition import MQTTReader
from tinystudio.lab.gateway import CommandGateway, GatewayError, MQTTTransport


TOPICS = {"command": "owned-fixture/command", "ack": "owned-fixture/ack",
          "telemetry": "owned-fixture/telemetry"}
INITIAL = {"diagnostic": {"value": 1.75, "unit": "fixture-unit"}}
STOP = {"diagnostic": {"value": -0.25, "unit": "fixture-unit"}}


@pytest.fixture
def broker(tmp_path):
    binary = os.environ.get("TINYSTUDIO_TEST_MQTT_BROKER")
    if not binary:
        pytest.skip("real MQTT wire integration unqualified: set TINYSTUDIO_TEST_MQTT_BROKER to an owned Mosquitto executable")
    pytest.importorskip("paho.mqtt.client", reason="real MQTT wire integration requires paho-mqtt")
    with OwnedMqttBroker(binary, tmp_path / "broker") as owned:
        yield owned


def binding(broker, *, timeout=1):
    return {"endpoint_id": "owned-simulated-endpoint", "operator_id": "fixture-operator",
            "transport": {"kind": "mqtt", "host": broker.host, "port": broker.port,
                          "command_topic": TOPICS["command"], "ack_topic": TOPICS["ack"]},
            "limits": {"diagnostic": {"minimum": -5, "maximum": 5, "unit": "fixture-unit"}},
            "local_stop": deepcopy(STOP), "send_timeout": timeout}


def device(broker, **kwargs):
    return MqttTestDevice(broker, TOPICS, "owned-simulated-endpoint", INITIAL,
                          stop_parameters=STOP, telemetry_parameter="diagnostic", **kwargs)


def request(command_id="direct-command", *, epoch=1, parameters=None, kind="command"):
    return {"protocol": 1, "endpoint_id": "owned-simulated-endpoint", "command_id": command_id,
            "epoch": epoch, "expires_at": time.time() + 5, "owner": "fixture-run", "kind": kind,
            "parameters": parameters or {"diagnostic": {"value": 2.125, "unit": "fixture-unit"}}}


def exchange(broker, document, *, timeout=1):
    transport = MQTTTransport(binding(broker)["transport"])
    return transport.exchange(document, timeout=timeout, cancelled=lambda: False,
                              authorize=lambda: deepcopy(document))


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.1", "localhost", "::1"])
def test_broker_refuses_any_non_owned_ipv4_loopback_listener(tmp_path, host):
    with pytest.raises(ValueError, match="127.0.0.1"):
        OwnedMqttBroker("/not-used", tmp_path, host=host)


def test_real_gateway_apply_sampling_stop_reconcile_and_owned_process_cleanup(broker, tmp_path):
    process = broker.process
    with device(broker) as simulated, CommandGateway(tmp_path / "gateway.sqlite3", binding(broker)) as gateway:
        lease = gateway.acquire("fixture-run", ttl=10)
        applied = {"diagnostic": {"value": 2.125, "unit": "fixture-unit"}}
        ack = gateway.dispatch("gateway-command", owner="fixture-run", epoch=lease["epoch"],
                               parameters=applied, expires_at=time.time() + 5)
        assert ack["status"] == "confirmed" and ack["applied"] is True
        assert ack["applied_parameters"] == applied == simulated.parameters
        assert simulated.outcomes == {"gateway-command": "applied"}
        reader = MQTTReader({"endpoint": {"host": broker.host, "port": broker.port,
                                          "topic": TOPICS["telemetry"], "tls": False}}, 1, None)
        try:
            frames = [reader.read(1), reader.read(1)]
        finally:
            reader.close()
        bodies = [json.loads(frame["wire"]) for frame in frames]
        assert all(body["value"] == 2.125 for body in bodies)
        assert bodies[1]["tick"] > bodies[0]["tick"]
        assert bodies[0]["record_id"] != bodies[1]["record_id"]
        assert bodies[0]["clock"]["source_timestamp"] == "host_unix_time"
        assert bodies[0]["clock"]["simulation"] == "host-paced discrete ticks"
        assert bodies[0]["physical_stability_qualified"] is False
        assert abs(bodies[0]["source_timestamp"] - time.time()) < 5
        for frame in frames:
            assert frame["topic"] == TOPICS["telemetry"]
            assert frame["wire_scope"] == "mqtt_application_payload"
            assert frame["wire_sha256"] == hashlib.sha256(frame["wire"]).hexdigest()
            assert frame["retained"] is False and frame["truncated"] is False
        stopped = gateway.local_stop("fixture-operator", request_id="operator-stop")
        assert stopped["applied"] is True and simulated.parameters == STOP
        recovered = gateway.reconcile("fixture-operator")
        assert recovered["state"] == "idle"
        assert recovered["evidence"]["stopped"] is True
        assert recovered["evidence"]["parameters"] == STOP
        assert gateway.snapshot()["physical_qualified"] is False
        assert [row["state"] for row in gateway.journal()] == ["confirmed", "confirmed"]
    broker.close()
    assert process.poll() is not None
    assert not simulated.thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex((broker.host, broker.port)) != 0


def test_broker_puback_without_device_application_ack_stays_unknown(broker, tmp_path):
    with device(broker, drop_ack_ids={"lost-ack"}) as simulated:
        with CommandGateway(tmp_path / "lost.sqlite3", binding(broker, timeout=.3)) as gateway:
            lease = gateway.acquire("fixture-run", ttl=10)
            with pytest.raises(GatewayError, match="outcome unknown"):
                gateway.dispatch("lost-ack", owner="fixture-run", epoch=lease["epoch"],
                                 parameters={"diagnostic": {"value": 3.5, "unit": "fixture-unit"}},
                                 expires_at=time.time() + 5)
            assert simulated.parameters["diagnostic"]["value"] == 3.5
            assert simulated.outcomes["lost-ack"] == "applied"
            assert gateway.journal()[0]["state"] == "unknown"
            assert gateway.snapshot()["state"] == "recovery_required"
            gateway.local_stop("fixture-operator", request_id="stop-after-lost-ack")
            reconciled = gateway.reconcile("fixture-operator")
            assert reconciled["evidence"]["command_outcomes"]["lost-ack"] == "applied"
            assert gateway.journal()[0]["state"] == "reconciled_applied"
    broker.close()
    assert "Sending PUBACK" in broker.log_text


def test_owned_broker_and_device_are_reaped_when_context_body_fails(broker, tmp_path):
    owned = OwnedMqttBroker(broker.binary, tmp_path / "exception-owned")
    simulated = None
    with pytest.raises(RuntimeError, match="fixture-body-failure"):
        with owned:
            with device(owned) as simulated:
                raise RuntimeError("fixture-body-failure")
    assert owned.process.poll() is not None
    assert not simulated.thread.is_alive()
    assert not owned.runtime_path.exists()
    assert "mosquitto version" in owned.log_text


def test_direct_wire_fencing_typed_parameters_deduplication_and_persisted_outcomes(broker):
    with device(broker) as simulated:
        first = request(epoch=4)
        confirmed = exchange(broker, first)
        assert confirmed["applied_parameters"] == first["parameters"]
        assert exchange(broker, first) == confirmed
        assert len(simulated.effects) == 1
        collision = deepcopy(first)
        collision["parameters"]["diagnostic"]["value"] = 4
        assert exchange(broker, collision)["reason"] == "command_identity_collision"
        stale = request("stale", epoch=3)
        expired = request("expired", epoch=4)
        expired["expires_at"] = time.time() - 1
        wrong_unit = request("wrong-unit", epoch=4,
                             parameters={"diagnostic": {"value": 4, "unit": "wrong-unit"}})
        for rejected, reason in [(stale, "stale_epoch"), (expired, "expired"),
                                 (wrong_unit, "invalid_typed_parameters")]:
            result = exchange(broker, rejected)
            assert result["status"] == "rejected" and result["applied"] is False
            assert result["reason"] == reason
            assert all(result[key] == rejected[key] for key in ("endpoint_id", "command_id", "epoch"))
        assert simulated.parameters == first["parameters"]
        state_path = simulated.state_path
    with device(broker, state_path=state_path) as restarted:
        assert restarted.parameters == first["parameters"]
        assert restarted.device_epoch == 4
        assert exchange(broker, first) == confirmed
        assert restarted.effects == []
        assert exchange(broker, request("post-restart-stale", epoch=3))["reason"] == "stale_epoch"
        exchange(broker, request("direct-stop", epoch=5, parameters=STOP, kind="local_stop"))
        query = {"protocol": 1, "endpoint_id": first["endpoint_id"], "command_id": "query:fixture",
                 "epoch": 6, "expires_at": time.time() + 5, "kind": "reconcile",
                 "uncertain_command_ids": [first["command_id"], "expired", "never-arrived"]}
        state = exchange(broker, query)
        assert state["status"] == "state" and state["stopped"] is True
        assert state["command_outcomes"] == {first["command_id"]: "applied", "expired": "not_applied",
                                              "never-arrived": "not_applied"}
        assert state["device_epoch"] == 6


def test_configured_drop_before_apply_is_reported_as_not_applied_over_wire(broker):
    with device(broker, drop_command_ids={"dropped"}) as simulated:
        with pytest.raises(TimeoutError):
            exchange(broker, request("dropped"), timeout=.25)
        assert simulated.parameters == INITIAL
        assert simulated.outcomes["dropped"] == "not_applied"
        exchange(broker, request("stop", epoch=2, parameters=STOP, kind="local_stop"))
        query = {"protocol": 1, "endpoint_id": "owned-simulated-endpoint", "command_id": "query:drop",
                 "epoch": 3, "expires_at": time.time() + 5, "kind": "reconcile",
                 "uncertain_command_ids": ["dropped"]}
        assert exchange(broker, query)["command_outcomes"] == {"dropped": "not_applied"}


def test_configured_ack_delay_is_after_parameter_application(broker):
    with device(broker, ack_delay=.12) as simulated:
        document = request("delayed")
        ack = exchange(broker, document)
        assert ack["applied_parameters"] == document["parameters"]
        applied = next(event for event in simulated.events if event["event"] == "applied")
        sent = next(event for event in simulated.events if event["event"] == "ack_published")
        assert sent["host_monotonic"] - applied["host_monotonic"] >= .12
        assert simulated.parameters == document["parameters"]
