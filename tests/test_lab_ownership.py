"""Ownership checks prove state transitions, not hardware gateway enforcement."""
from dataclasses import replace

import pytest

from tinystudio.lab.ownership import CommandOwnership, OwnershipError


class Clock:
    def __init__(self):
        self.time = 10.0
    def __call__(self):
        return self.time


def owned():
    clock = Clock()
    authority = CommandOwnership(clock=clock)
    grant = authority.acquire("operator", "gateway-a", ttl=30)
    return clock, authority, grant["epoch"]


def issue(authority, epoch, command_id="cmd1", **kwargs):
    return authority.issue(command_id, {"setpoint": -.125}, epoch=epoch, owner="operator", **kwargs)


def test_issue_is_not_gateway_authorization_and_dispatch_is_once():
    clock, authority, epoch = owned()
    command = issue(authority, epoch)
    with pytest.raises(OwnershipError, match="different"):
        authority.authorize_at_gateway(command, authenticated_gateway_id="gateway-b")
    receipt = authority.authorize_at_gateway(command, authenticated_gateway_id="gateway-a")
    assert receipt["command_id"] == "cmd1"
    assert receipt["epoch"] == epoch
    with pytest.raises(OwnershipError, match="consumed"):
        authority.authorize_at_gateway(command, authenticated_gateway_id="gateway-a")
    authority.record_result("cmd1", epoch=epoch, owner="operator", outcome="completed")
    authority.release(epoch=epoch, owner="operator")
    assert authority.state == "idle"


def test_immutable_payload_and_forged_commands_rejected():
    clock, authority, epoch = owned()
    command = issue(authority, epoch)
    payload = command.payload
    payload["setpoint"] = 100
    assert command.payload == {"setpoint": -.125}
    forged = replace(command, payload_json='{"setpoint":100}')
    with pytest.raises(OwnershipError, match="modified"):
        authority.authorize_at_gateway(forged, authenticated_gateway_id="gateway-a")


def test_command_expiry_enforced_at_actual_dispatch_boundary():
    clock, authority, epoch = owned()
    command = issue(authority, epoch, expires_in=2)
    clock.time += 2
    with pytest.raises(OwnershipError, match="expired"):
        authority.authorize_at_gateway(command, authenticated_gateway_id="gateway-a")
    assert authority.state == "owned"
    with pytest.raises(OwnershipError, match="already been used"):
        issue(authority, epoch)


def test_lease_expiry_requires_explicit_recovery_and_fresh_epoch():
    clock, authority, epoch = owned()
    command = issue(authority, epoch)
    clock.time += 30
    assert authority.state == "recovery_required"
    assert authority.epoch > epoch
    with pytest.raises(OwnershipError):
        authority.heartbeat(epoch=epoch, owner="operator")
    with pytest.raises(OwnershipError, match="recovery"):
        authority.acquire("operator", "gateway-a")
    receipt = authority.recover(operator="human", observed_state={"device_setpoint": 0})
    assert receipt["replay"] is False
    fresh_epoch = authority.acquire("operator", "gateway-a")["epoch"]
    assert fresh_epoch > epoch
    with pytest.raises(OwnershipError):
        authority.authorize_at_gateway(command, authenticated_gateway_id="gateway-a")
    with pytest.raises(OwnershipError, match="already been used"):
        issue(authority, fresh_epoch)


def test_disconnect_and_unknown_outcome_never_replay():
    clock, authority, epoch = owned()
    command = issue(authority, epoch)
    authority.authorize_at_gateway(command, authenticated_gateway_id="gateway-a")
    authority.record_result("cmd1", epoch=epoch, owner="operator", outcome="unknown")
    assert authority.state == "recovery_required"
    assert authority.recovery_evidence["reason"] == "command_outcome_unknown"
    with pytest.raises(OwnershipError):
        authority.recover(operator="human", observed_state={})
    authority.recover(operator="human", observed_state={"observed_output": 0})
    fresh = authority.acquire("operator", "gateway-a")["epoch"]
    authority.disconnect()
    assert authority.state == "recovery_required"
    with pytest.raises(OwnershipError):
        issue(authority, fresh, "cmd2")


def test_unreconciled_dispatch_cannot_be_released_as_idle():
    clock, authority, epoch = owned()
    command = issue(authority, epoch)
    authority.authorize_at_gateway(command, authenticated_gateway_id="gateway-a")
    with pytest.raises(OwnershipError, match="recovery"):
        authority.release(epoch=epoch, owner="operator")
    assert authority.state == "recovery_required"


def test_heartbeat_cannot_extend_existing_command_expiry():
    clock, authority, epoch = owned()
    command = issue(authority, epoch, expires_in=2)
    clock.time += 1
    authority.heartbeat(epoch=epoch, owner="operator", ttl=100)
    clock.time += 1
    with pytest.raises(OwnershipError, match="expired"):
        authority.authorize_at_gateway(command, authenticated_gateway_id="gateway-a")


def test_clock_regression_and_invalid_duration_fail_closed():
    clock, authority, epoch = owned()
    for ttl in [True, float("nan"), float("inf"), 0, -1]:
        with pytest.raises(OwnershipError):
            authority.heartbeat(epoch=epoch, owner="operator", ttl=ttl)
    clock.time -= 1
    with pytest.raises(OwnershipError, match="monotonic"):
        issue(authority, epoch)
