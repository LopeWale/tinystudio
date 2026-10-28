"""Gateway-bound ownership/command state machine; this module never calls an endpoint.

A future hardware gateway MUST invoke authorize_at_gateway at its actual write
boundary using its authenticated local identity and trusted monotonic clock.
An API/service token, queued command, or issue() receipt is not write authority.
The state and epoch must be durably persisted by that gateway before production
use. This in-memory implementation is a simulation and contract qualification.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import time
from typing import Callable


class OwnershipError(RuntimeError):
    pass


@dataclass(frozen=True)
class Command:
    command_id: str
    epoch: int
    owner: str
    gateway_id: str
    issued_at: float
    expires_at: float
    payload_json: str
    payload_sha256: str

    @property
    def payload(self):
        return json.loads(self.payload_json)


class CommandOwnership:
    """One owner, one authenticated gateway, monotonic epochs and single-dispatch IDs."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._last_time = -math.inf
        self._state = "idle"
        self._epoch = 0
        self._owner = self._gateway_id = None
        self._lease_expires_at = 0.0
        self._commands: dict[str, tuple[Command, str]] = {}
        self._recovery_evidence = None

    @property
    def state(self):
        self._now()
        return self._state

    @property
    def epoch(self):
        self._now()
        return self._epoch

    @property
    def recovery_evidence(self):
        return json.loads(self._recovery_evidence) if self._recovery_evidence else None

    def _now(self):
        now = float(self._clock())
        if not math.isfinite(now) or now < self._last_time:
            raise OwnershipError("trusted gateway clock must be finite and monotonic")
        self._last_time = now
        if self._state == "owned" and now >= self._lease_expires_at:
            self._revoke("lease_expired")
        return now

    def _revoke(self, reason):
        self._state = "recovery_required"
        self._epoch += 1
        self._owner = self._gateway_id = None
        self._recovery_evidence = json.dumps({"reason": reason})

    def _owned(self, epoch, owner):
        now = self._now()
        if self._state != "owned" or epoch != self._epoch or owner != self._owner:
            raise OwnershipError("fresh ownership epoch and matching owner are required")
        return now

    @staticmethod
    def _duration(value):
        if isinstance(value, bool):
            raise OwnershipError("duration must be finite and positive")
        try:
            duration = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise OwnershipError("duration must be finite and positive") from exc
        if not math.isfinite(duration) or not 0 < duration <= 3600:
            raise OwnershipError("duration must be between 0 and 3600 seconds")
        return duration

    def acquire(self, owner: str, gateway_id: str, *, ttl: float = 30):
        now = self._now()
        if self._state != "idle":
            raise OwnershipError("ownership requires idle state; recovery cannot be bypassed")
        if not isinstance(owner, str) or not owner or not isinstance(gateway_id, str) or not gateway_id:
            raise OwnershipError("owner and authenticated gateway identity are required")
        duration = self._duration(ttl)
        self._epoch += 1
        self._owner, self._gateway_id = owner, gateway_id
        self._lease_expires_at = now + duration
        self._state = "owned"
        return {"epoch": self._epoch, "owner": owner, "gateway_id": gateway_id,
                "expires_at": self._lease_expires_at, "boundary_authorized": False}

    def heartbeat(self, *, epoch: int, owner: str, ttl: float = 30):
        now = self._owned(epoch, owner)
        self._lease_expires_at = now + self._duration(ttl)
        return self._lease_expires_at

    def issue(self, command_id: str, payload: dict, *, epoch: int, owner: str, expires_in: float = 5):
        now = self._owned(epoch, owner)
        if not isinstance(command_id, str) or not command_id or len(command_id) > 128:
            raise OwnershipError("command_id must contain 1..128 characters")
        if command_id in self._commands:
            raise OwnershipError("command_id has already been used; automatic replay is forbidden")
        if not isinstance(payload, dict):
            raise OwnershipError("command payload must be a map")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > 65536:
            raise OwnershipError("command payload exceeds 64 KiB")
        deadline = min(now + self._duration(expires_in), self._lease_expires_at)
        command = Command(command_id, epoch, owner, self._gateway_id, now, deadline, encoded,
                          hashlib.sha256(encoded.encode()).hexdigest())
        self._commands[command_id] = (command, "issued")
        return command

    def authorize_at_gateway(self, command: Command, *, authenticated_gateway_id: str):
        """Consume authority exactly once immediately before a future gateway's write.

        Caller MUST derive gateway identity from its authenticated local binding,
        never from an untrusted request field. No external operation occurs here.
        """
        if not isinstance(command, Command):
            raise OwnershipError("a locally issued immutable command is required")
        now = self._owned(command.epoch, command.owner)
        stored = self._commands.get(command.command_id)
        if stored is None or stored[0] != command or stored[1] != "issued":
            raise OwnershipError("unknown, modified, consumed, or replayed command")
        if authenticated_gateway_id != self._gateway_id or command.gateway_id != self._gateway_id:
            raise OwnershipError("command belongs to a different authenticated gateway boundary")
        if now >= command.expires_at:
            self._commands[command.command_id] = (command, "expired")
            raise OwnershipError("command expired before gateway dispatch")
        self._commands[command.command_id] = (command, "dispatched")
        return {"command_id": command.command_id, "epoch": command.epoch,
                "gateway_id": self._gateway_id, "authorized_at": now, "payload_sha256": command.payload_sha256}

    def record_result(self, command_id: str, *, epoch: int, owner: str, outcome: str):
        self._owned(epoch, owner)
        stored = self._commands.get(command_id)
        if stored is None or stored[1] != "dispatched":
            raise OwnershipError("only dispatched commands may be reconciled")
        if outcome not in {"completed", "failed", "unknown"}:
            raise OwnershipError("outcome must be completed, failed, or unknown")
        self._commands[command_id] = (stored[0], outcome)
        if outcome in {"failed", "unknown"}:
            self._revoke("command_outcome_" + outcome)

    def disconnect(self, reason: str = "gateway_disconnected"):
        self._now()
        if self._state == "owned":
            self._revoke(reason)

    def release(self, *, epoch: int, owner: str):
        self._owned(epoch, owner)
        if any(command.epoch == epoch and status == "dispatched" for command, status in self._commands.values()):
            self._revoke("unreconciled_command")
            raise OwnershipError("unreconciled dispatch requires recovery")
        self._epoch += 1
        self._owner = self._gateway_id = None
        self._state = "idle"

    def recover(self, *, operator: str, observed_state: dict):
        self._now()
        if self._state != "recovery_required" or not operator or not isinstance(observed_state, dict) or not observed_state:
            raise OwnershipError("explicit operator reconciliation and observed state are required")
        self._recovery_evidence = json.dumps({"operator": operator, "observed_state": observed_state}, sort_keys=True, allow_nan=False)
        self._epoch += 1
        self._state = "idle"
        # Previously issued/dispatched IDs remain consumed across recovery; none are replayed.
        return {"state": "idle", "epoch": self._epoch, "replay": False}
