"""Authenticated ASGI fixture tests. No listener, serial device or broker is opened."""
import hashlib
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from tinystudio.lab.gateway_api import create_gateway_app, PERMISSIONS
from tinystudio.lab.gateway_cli import listener_options


class Device:
    kind = "fixture"
    def __init__(self):
        self.effects = []
        self.outcomes = {}
        self.epoch = 0
        self.value = 0
        self.lose_ack = False
    def exchange(self, request, *, timeout, cancelled, authorize):
        request = authorize()
        identity = {key: request[key] for key in ("endpoint_id", "command_id", "epoch")}
        if request["kind"] == "reconcile":
            return {**identity, "status": "state", "stopped": self.value == 0, "device_epoch": self.epoch,
                    "parameters": {"voltage": {"value": self.value, "unit": "V"}}, "command_outcomes": self.outcomes}
        assert not cancelled()
        self.effects.append(request)
        self.value = request["parameters"]["voltage"]["value"]
        self.epoch = request["epoch"]
        self.outcomes[request["command_id"]] = "applied"
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError("fixture lost application ack after effect")
        return {**identity, "status": "confirmed", "applied": True}


def provision(tmp_path, permissions=None):
    binding = {"endpoint_id": "device-a", "operator_id": "local-operator", "transport": {"kind": "fixture"},
               "limits": {"voltage": {"minimum": 0, "maximum": 5, "unit": "V"}},
               "local_stop": {"voltage": {"value": 0, "unit": "V"}}}
    secret = "fixture-secret-with-at-least-32-bytes-of-entropy"
    descriptor = {"endpoint_id": "device-a", "operator_id": "local-operator",
                  "token_sha256": hashlib.sha256(secret.encode()).hexdigest(),
                  "permissions": sorted(PERMISSIONS if permissions is None else permissions)}
    binding_path, token_path = tmp_path / "binding.json", tmp_path / "token.json"
    for path, payload in ((binding_path, binding), (token_path, descriptor)):
        path.write_text(json.dumps(payload))
        path.chmod(0o600)
    root = tmp_path / "journal"
    root.mkdir(mode=0o700)
    device = Device()
    def app():
        return create_gateway_app(binding_path, root, token_path, lambda binding: device)
    return app, device, {"Authorization": "Bearer " + secret}, binding_path, token_path


def acquire(client, headers):
    response = client.post("/acquire", headers=headers, json={"endpoint_id": "device-a", "owner": "run-a", "ttl": 30})
    assert response.status_code == 200, response.text
    return response.json()["epoch"]


def command(epoch, command_id="c1"):
    return {"endpoint_id": "device-a", "owner": "run-a", "epoch": epoch, "command_id": command_id,
            "expires_at": time.time()+5, "parameters": {"voltage": {"value": 1, "unit": "V"}}}


def test_unauthenticated_foreign_token_and_endpoint_have_no_effect(tmp_path):
    app, device, headers, *_ = provision(tmp_path)
    with TestClient(app()) as client:
        assert client.get("/state").status_code == 401
        assert client.post("/acquire", headers={"Authorization": "Bearer foreign"}, json={"endpoint_id": "device-a", "owner": "run-a"}).status_code == 401
        assert client.post("/acquire", headers=headers, json={"endpoint_id": "device-b", "owner": "run-a"}).status_code == 403
        assert client.get("/state?endpoint_id=device-b", headers=headers).status_code == 403
        assert client.get("/state", headers=headers).json()["state"] == "idle"
        assert device.effects == []


def test_http_duplicate_stale_epoch_competing_owner_and_journal(tmp_path):
    app, device, headers, *_ = provision(tmp_path)
    with TestClient(app()) as client:
        epoch = acquire(client, headers)
        body = command(epoch)
        response = client.post("/commands", headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert client.post("/commands", headers=headers, json=body).json() == response.json()
        assert len(device.effects) == 1
        stale = command(epoch+1, "stale")
        assert client.post("/commands", headers=headers, json=stale).status_code == 409
        assert client.post("/acquire", headers=headers, json={"endpoint_id": "device-a", "owner": "run-b"}).status_code == 409
        assert client.post("/heartbeat", headers=headers, json={"endpoint_id": "device-a", "owner": "run-a", "epoch": epoch, "ttl": 30}).status_code == 200
        journal = client.get("/journal?limit=1", headers=headers).json()
        assert journal["items"][0]["state"] == "confirmed"
        assert "token" not in json.dumps(journal)


def test_unknown_outcome_restart_stop_and_explicit_reconciliation(tmp_path):
    app, device, headers, *_ = provision(tmp_path)
    with TestClient(app()) as client:
        epoch = acquire(client, headers)
        device.lose_ack = True
        body = command(epoch)
        response = client.post("/commands", headers=headers, json=body)
        assert response.status_code == 409
        assert response.json()["retry_allowed"] is False
        assert device.value == 1 and len(device.effects) == 1
    with TestClient(app()) as restarted:
        assert restarted.get("/state", headers=headers).json()["state"] == "recovery_required"
        assert restarted.post("/commands", headers=headers, json=body).status_code == 409
        assert len(device.effects) == 1
        assert restarted.post("/stop", headers=headers, json={"endpoint_id": "device-a", "request_id": "operator-stop"}).status_code == 200
        assert restarted.post("/reconcile", headers=headers, json={"endpoint_id": "device-a"}).json()["replay"] is False
        assert restarted.get("/state", headers=headers).json()["state"] == "idle"
        assert len(device.effects) == 2


def test_typed_limits_expiry_and_identity_validation(tmp_path):
    app, device, headers, *_ = provision(tmp_path)
    with TestClient(app()) as client:
        epoch = acquire(client, headers)
        for mutate in (lambda b: b.update(epoch=True), lambda b: b.update(command_id=""),
                       lambda b: b["parameters"]["voltage"].update(value=True),
                       lambda b: b["parameters"]["voltage"].update(value=5.01),
                       lambda b: b["parameters"]["voltage"].update(unit="A")):
            body = command(epoch)
            mutate(body)
            assert client.post("/commands", headers=headers, json=body).status_code == 422
        body = command(epoch)
        body["expires_at"] = time.time()-1
        assert client.post("/commands", headers=headers, json=body).status_code == 410
        assert device.effects == []


def test_permission_allowlist_does_not_silently_grant_control(tmp_path):
    app, device, headers, *_ = provision(tmp_path, {"state"})
    with TestClient(app()) as client:
        assert client.get("/state", headers=headers).status_code == 200
        assert client.post("/acquire", headers=headers, json={"endpoint_id": "device-a", "owner": "run-a"}).status_code == 403
        assert device.effects == []


def test_private_config_modes_and_foreign_descriptor_are_rejected(tmp_path):
    app, device, headers, binding_path, token_path = provision(tmp_path)
    token_path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        app()
    token_path.chmod(0o600)
    token = json.loads(token_path.read_text())
    token["endpoint_id"] = "other-device"
    token_path.write_text(json.dumps(token))
    with pytest.raises(ValueError, match="scope"):
        app()
    assert device.effects == []


def test_lifespan_closes_gateway_lock_for_subsequent_instance(tmp_path):
    app, device, headers, *_ = provision(tmp_path)
    instance = app()
    with TestClient(instance) as client:
        assert client.get("/state", headers=headers).status_code == 200
        gateway = instance.state.gateway
    assert instance.state.gateway is None
    with pytest.raises(Exception, match="closed"):
        gateway.snapshot()
    with TestClient(app()) as other:
        assert other.get("/state", headers=headers).status_code == 200


def test_listener_requires_explicit_tls_or_proxy_before_public_binding():
    assert listener_options("127.0.0.1", "loopback")["host"] == "127.0.0.1"
    with pytest.raises(ValueError, match="loopback"):
        listener_options("0.0.0.0", "loopback")
    with pytest.raises(ValueError, match="certificate"):
        listener_options("0.0.0.0", "tls")
    with pytest.raises(ValueError, match="proxy"):
        listener_options("0.0.0.0", "reverse-proxy")
    assert listener_options("0.0.0.0", "reverse-proxy", trusted_proxies="127.0.0.1")["proxy_headers"] is True
