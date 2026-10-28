"""Persistent local command boundary, independent of the TinyStudio server database.

One operator-provisioned endpoint per journal in one canonical local state directory.
Endpoint locks also prevent different journals in that directory claiming the same
transport endpoint. The installer MUST provision that single directory; copying
journals to different roots or restoring old backups is not a valid ownership transfer. Constructing
plugins never opens a device/network. API: acquire(owner, ttl), dispatch(id,
owner, epoch, parameters, expires_at), heartbeat, local_stop(operator, request_id),
reconcile(operator), snapshot/journal, close. HTTP/device-token authentication and
installation belong to the local gateway host, not this module or service tokens.

Wire protocol 1 requires endpoint_id, command_id, epoch and absolute expires_at.
Devices MUST persist epoch/id fences, reject expired/stale commands and return an
application ack with the same identity, status='confirmed', applied=True. Serial
writes, Modbus register responses and MQTT PUBACK are NOT application acks.
Reconciliation returns status='state', device_epoch, stopped=True, parameters,
and command_outcomes mapping every uncertain ID to applied/not_applied.
No generic transport or this fixture-tested implementation proves exactly-once
physical effects. Physical acknowledgement/fencing remains unqualified.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import threading
import time
import uuid

from .ownership import OwnershipError


class GatewayError(OwnershipError):
    pass


class GatewayBusy(GatewayError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise GatewayError(f"{name} must be a finite numeric value")
    return float(value)


def _parameters(parameters, limits):
    if not isinstance(parameters, dict) or not parameters or set(parameters) - limits.keys():
        raise GatewayError("parameters must use the endpoint's nonempty allowlist")
    result = {}
    for name, typed in parameters.items():
        limit = limits[name]
        if not isinstance(typed, dict) or set(typed) != {"value", "unit"} or typed["unit"] != limit["unit"]:
            raise GatewayError("typed parameter units must match the endpoint binding")
        value = _finite(typed["value"], name)
        if not limit["minimum"] <= value <= limit["maximum"]:
            raise GatewayError(f"{name} exceeds the endpoint's typed limits")
        result[name] = {"value": value, "unit": typed["unit"]}
    return result


def _binding(supplied):
    value = deepcopy(supplied)
    required = {"endpoint_id", "operator_id", "transport", "limits", "local_stop"}
    if not isinstance(value, dict) or not required <= value.keys():
        raise GatewayError("endpoint/operator/transport/limits/local_stop binding is required")
    for key in ("endpoint_id", "operator_id"):
        if not isinstance(value[key], str) or not 1 <= len(value[key]) <= 128:
            raise GatewayError(f"invalid {key}")
    transport = value["transport"]
    if not isinstance(transport, dict) or transport.get("kind") not in {"fixture", "serial", "modbus", "mqtt"}:
        raise GatewayError("unsupported explicitly bound transport")
    if any(key in transport for key in ("password", "token", "database_url")):
        raise GatewayError("transport secrets are runtime-only and cannot enter the journal binding")
    kind = transport["kind"]
    if kind == "serial":
        if not isinstance(transport.get("port"), str) or not transport["port"]:
            raise GatewayError("serial binding requires an explicit local device path")
        baud = transport.get("baudrate", 115200)
        if isinstance(baud, bool) or not isinstance(baud, int) or not 1 <= baud <= 4_000_000:
            raise GatewayError("invalid bound serial baudrate")
    elif kind in {"mqtt", "modbus"}:
        try:
            ipaddress.ip_address(transport.get("host", ""))
        except ValueError as exc:
            raise GatewayError("network binding requires a numeric device/broker address") from exc
        port = transport.get("port", 1883 if kind == "mqtt" else 502)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise GatewayError("invalid bound network port")
        if kind == "mqtt":
            topics = [transport.get("command_topic"), transport.get("ack_topic")]
            if any(not isinstance(topic, str) or not topic or any(c in topic for c in ("#", "+", "\0")) for topic in topics) or topics[0] == topics[1]:
                raise GatewayError("MQTT binding requires distinct exact command/ack topics")
        else:
            capacity = transport.get("capacity", 512)
            unit = transport.get("device_id", 1)
            starts = [transport.get("request_register"), transport.get("response_register")]
            if isinstance(capacity, bool) or not isinstance(capacity, int) or not 2 <= capacity <= 4096:
                raise GatewayError("Modbus mailbox capacity must be 2..4096 registers")
            if isinstance(unit, bool) or not isinstance(unit, int) or not 1 <= unit <= 247:
                raise GatewayError("invalid bound Modbus device id")
            if any(isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= 65536-capacity for start in starts):
                raise GatewayError("invalid bound Modbus mailbox range")
            if abs(starts[0]-starts[1]) < capacity:
                raise GatewayError("Modbus request/ack mailboxes must not overlap")
    if not isinstance(value["limits"], dict) or not 1 <= len(value["limits"]) <= 32:
        raise GatewayError("binding requires 1..32 typed parameter limits")
    for name, limit in value["limits"].items():
        if not isinstance(name, str) or not isinstance(limit, dict) or set(limit) != {"minimum", "maximum", "unit"}:
            raise GatewayError("typed limits require minimum/maximum/unit")
        low, high = _finite(limit["minimum"], "minimum"), _finite(limit["maximum"], "maximum")
        if low > high or not isinstance(limit["unit"], str):
            raise GatewayError("invalid typed limits")
        limit.update(minimum=low, maximum=high)
    value["local_stop"] = _parameters(value["local_stop"], value["limits"])
    if value["local_stop"].keys() != value["limits"].keys():
        raise GatewayError("local stop must cover every allowlisted control parameter")
    timeout = _finite(value.get("send_timeout", 2), "send_timeout")
    if not .05 <= timeout <= 30:
        raise GatewayError("send_timeout must be .05..30 seconds")
    value["send_timeout"] = timeout
    if len(_json(value).encode()) > 65536:
        raise GatewayError("endpoint binding exceeds 64 KiB")
    return value


class CommandGateway:
    """Durable ownership and final-send checks, with a single serialized endpoint."""

    def __init__(self, path, binding, transport=None, *, clock=time.time):
        self._binding = _binding(binding)
        self._clock = clock
        self._mutex = threading.RLock()
        self._send_lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._closed = False
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = self._binding["transport"]
        kind = config["kind"]
        identity = ({"kind": kind, "port": str(Path(config["port"]).resolve())} if kind == "serial" else
                    {"kind": kind, "host": config["host"], "port": config.get("port", 502), "device_id": config.get("device_id", 1)} if kind == "modbus" else
                    {"kind": kind, "host": config["host"], "port": config.get("port", 1883), "command_topic": config["command_topic"]} if kind == "mqtt" else
                    {"kind": kind, "endpoint_id": self._binding["endpoint_id"]})
        endpoint_key = hashlib.sha256(_json(identity).encode()).hexdigest()
        self._endpoint_lock = (self.path.parent / ("." + endpoint_key + ".endpoint.lock")).open("a+b")
        os.chmod(self._endpoint_lock.name, 0o600)
        try:
            fcntl.flock(self._endpoint_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._endpoint_lock.close()
            raise GatewayBusy("another gateway owns this endpoint in the provisioned state directory") from exc
        self._lock = self.path.with_suffix(self.path.suffix + ".lock").open("a+b")
        os.chmod(self._lock.name, 0o600)
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock.close()
            fcntl.flock(self._endpoint_lock.fileno(), fcntl.LOCK_UN)
            self._endpoint_lock.close()
            raise GatewayBusy("another gateway owns this journal") from exc
        try:
            self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=5)
            os.chmod(self.path, 0o600)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript('''
                CREATE TABLE IF NOT EXISTS gateway (
                    id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL,
                    epoch INTEGER NOT NULL, state TEXT NOT NULL, owner TEXT,
                    lease_expires REAL NOT NULL, last_time REAL NOT NULL, reason TEXT);
                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL, owner TEXT NOT NULL,
                    request TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL, ack TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS recovery (
                    id INTEGER PRIMARY KEY, recorded_at REAL NOT NULL, evidence TEXT NOT NULL);
            ''')
            with self._transaction():
                state = self._db.execute("SELECT * FROM gateway WHERE id=1").fetchone()
                encoded = _json(self._binding)
                if state is None:
                    now = _finite(self._clock(), "gateway clock")
                    self._db.execute("INSERT INTO gateway VALUES(1,?,0,'idle',NULL,0,?,NULL)", (encoded, now))
                elif state["binding"] != encoded:
                    raise GatewayError("journal is bound to a different endpoint/operator/configuration")
                elif state["state"] == "owned" or self._db.execute("SELECT 1 FROM commands WHERE state='pending' LIMIT 1").fetchone():
                    self._recovery("restart_with_ownership_or_pending_command")
            self.transport = transport if transport is not None else transport_for(self._binding)
            if self.transport.kind != self._binding["transport"]["kind"]:
                raise GatewayError("transport implementation does not match the explicit binding")
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()
            fcntl.flock(self._endpoint_lock.fileno(), fcntl.LOCK_UN)
            self._endpoint_lock.close()
            raise

    @contextmanager
    def _transaction(self):
        with self._mutex:
            if self._closed:
                raise GatewayError("gateway is closed")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except GatewayError:
                # Fail-closed epoch revocations/expiry checks must survive their reported rejection.
                self._db.execute("COMMIT")
                raise
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def _recovery(self, reason):
        self._db.execute("UPDATE gateway SET state='recovery_required', epoch=epoch+1, owner=NULL, lease_expires=0, reason=? WHERE id=1", (reason,))

    def _now(self):
        now = _finite(self._clock(), "gateway clock")
        state = self._db.execute("SELECT * FROM gateway WHERE id=1").fetchone()
        if now < state["last_time"]:
            self._recovery("gateway_clock_regressed")
            raise GatewayError("persistent gateway clock regressed; explicit recovery required")
        self._db.execute("UPDATE gateway SET last_time=? WHERE id=1", (now,))
        if state["state"] == "owned" and now >= state["lease_expires"]:
            self._recovery("lease_expired")
        return now

    def _authority(self, owner, epoch):
        if isinstance(epoch, bool) or not isinstance(epoch, int) or not isinstance(owner, str):
            raise GatewayError("ownership epoch must be an integer and owner must identify a run")
        now = self._now()
        state = self._db.execute("SELECT * FROM gateway WHERE id=1").fetchone()
        if state["state"] != "owned" or state["owner"] != owner or state["epoch"] != epoch:
            raise GatewayError("fresh matching endpoint ownership and epoch required")
        return now, state

    def acquire(self, owner, *, ttl=30):
        if not isinstance(owner, str) or not 1 <= len(owner) <= 128:
            raise GatewayError("owner must identify one run")
        duration = _finite(ttl, "lease ttl")
        if not 0 < duration <= 3600:
            raise GatewayError("lease ttl must be 0..3600 seconds")
        with self._transaction():
            now = self._now()
            state = self._db.execute("SELECT * FROM gateway WHERE id=1").fetchone()
            if state["state"] != "idle":
                raise GatewayError("endpoint is owned or recovery_required; no automatic reassignment")
            self._db.execute("UPDATE gateway SET state='owned', epoch=epoch+1, owner=?, lease_expires=?, reason=NULL WHERE id=1", (owner, now + duration))
            epoch = self._db.execute("SELECT epoch FROM gateway WHERE id=1").fetchone()[0]
        self._stop_requested.clear()
        return {"epoch": epoch, "owner": owner, "endpoint_id": self._binding["endpoint_id"], "expires_at": now + duration}

    def heartbeat(self, owner, epoch, *, ttl=30):
        duration = _finite(ttl, "lease ttl")
        if not 0 < duration <= 3600:
            raise GatewayError("lease ttl must be 0..3600 seconds")
        with self._transaction():
            now, _ = self._authority(owner, epoch)
            self._db.execute("UPDATE gateway SET lease_expires=? WHERE id=1", (now + duration,))
        return now + duration

    def _request(self, command_id, owner, epoch, parameters, expires_at, kind):
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise GatewayError("command identity must contain 1..128 characters")
        return {"protocol": 1, "endpoint_id": self._binding["endpoint_id"], "command_id": command_id,
                "epoch": epoch, "owner": owner, "parameters": _parameters(parameters, self._binding["limits"]),
                "expires_at": _finite(expires_at, "command expiry"), "kind": kind}

    def _pending(self, request):
        encoded = _json(request)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        existing = self._db.execute("SELECT * FROM commands WHERE command_id=?", (request["command_id"],)).fetchone()
        if existing:
            if existing["payload_sha256"] != fingerprint:
                raise GatewayError("command identity already belongs to a different immutable request")
            if existing["state"] == "confirmed":
                return json.loads(existing["ack"])
            raise GatewayError("command identity is pending/uncertain/consumed; never automatically retry")
        self._db.execute("INSERT INTO commands VALUES(?,?,?,?,?,'pending',NULL,NULL)",
                         (request["command_id"], request["epoch"], request["owner"], encoded, fingerprint))
        return None

    def _final_authorize(self, request):
        """Transport MUST call immediately before its one application-level write."""
        with self._transaction():
            if request["kind"] == "command":
                now, state = self._authority(request["owner"], request["epoch"])
            else:
                now = self._now()
                state = self._db.execute("SELECT * FROM gateway WHERE id=1").fetchone()
                if request["epoch"] != state["epoch"]:
                    raise GatewayError("local stop epoch changed before dispatch")
            if now >= request["expires_at"]:
                raise GatewayError("command expired at the final transport write boundary")
            _parameters(request["parameters"], self._binding["limits"])
            stored = self._db.execute("SELECT request,state FROM commands WHERE command_id=?", (request["command_id"],)).fetchone()
            if not stored or stored["state"] != "pending" or stored["request"] != _json(request):
                raise GatewayError("final write requires the exact committed pending journal entry")
            return json.loads(stored["request"])

    @staticmethod
    def _ack(request, ack):
        if not isinstance(ack, dict) or any(ack.get(field) != request[field] for field in ("endpoint_id", "command_id", "epoch")):
            raise GatewayError("device acknowledgement identity/epoch does not match")
        if isinstance(ack.get("epoch"), bool) or not isinstance(ack.get("epoch"), int):
            raise GatewayError("device acknowledgement epoch must be an integer")
        if ack.get("status") != "confirmed" or ack.get("applied") is not True:
            raise GatewayError("transport delivery is not a confirmed device application acknowledgement")
        if len(_json(ack).encode()) > 65536:
            raise GatewayError("device acknowledgement exceeds 64 KiB")
        return ack

    def _effect(self, request, cancelled):
        try:
            self._final_authorize(request)
            ack = self.transport.exchange(deepcopy(request), timeout=self._binding["send_timeout"], cancelled=cancelled,
                                          authorize=lambda: self._final_authorize(request))
            self._ack(request, ack)
        except Exception as exc:
            with self._transaction():
                self._db.execute("UPDATE commands SET state='unknown',error=? WHERE command_id=?", (type(exc).__name__, request["command_id"]))
                self._recovery("unknown_command_outcome")
            raise GatewayError("command outcome unknown; explicit device reconciliation required, no retry") from exc
        with self._transaction():
            self._db.execute("UPDATE commands SET state='confirmed',ack=? WHERE command_id=?", (_json(ack), request["command_id"]))
            self._now()  # A late ack is retained, but an expired ownership lease still revokes authority.
        return ack

    def dispatch(self, command_id, *, owner, epoch, parameters, expires_at, cancelled=lambda: False):
        with self._send_lock:
            request = self._request(command_id, owner, epoch, parameters, expires_at, "command")
            with self._transaction():
                now, state = self._authority(owner, epoch)
                if not now < request["expires_at"] <= state["lease_expires"]:
                    raise GatewayError("command expired or outlives its ownership lease")
                existing_ack = self._pending(request)
            if existing_ack is not None:
                return existing_ack  # Read an existing receipt; never repeat the effect.
            should_cancel = lambda: self._stop_requested.is_set() or cancelled()
            if should_cancel():
                with self._transaction():
                    self._db.execute("UPDATE commands SET state='cancelled' WHERE command_id=?", (command_id,))
                self.local_stop(self._binding["operator_id"], request_id="cancel:" + hashlib.sha256(command_id.encode()).hexdigest())
                raise GatewayError("command cancelled; configured local stop acknowledged")
            try:
                ack = self._effect(request, should_cancel)
            except GatewayError:
                if should_cancel():
                    self.local_stop(self._binding["operator_id"], request_id="cancel:" + hashlib.sha256(command_id.encode()).hexdigest())
                raise
            if should_cancel():
                self.local_stop(self._binding["operator_id"], request_id="cancel:" + hashlib.sha256(command_id.encode()).hexdigest())
                raise GatewayError("cancelled after dispatch; application ack retained and local stop acknowledged")
            return ack

    def local_stop(self, operator_id, *, request_id):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise GatewayError("local stop request identity must contain 1..128 characters")
        if operator_id != self._binding["operator_id"]:
            raise GatewayError("local stop requires the bound operator identity")
        self._stop_requested.set()  # Bounded transport calls must observe this before the serialized stop.
        with self._send_lock:
            with self._transaction():
                now = self._now()
                existing = self._db.execute("SELECT * FROM commands WHERE command_id=?", (request_id,)).fetchone()
                if existing:
                    stored = json.loads(existing["request"])
                    if stored["kind"] != "local_stop" or stored["owner"] != operator_id:
                        raise GatewayError("local stop identity collision")
                    if existing["state"] == "confirmed":
                        return json.loads(existing["ack"])
                    raise GatewayError("uncertain local stop cannot be retried automatically")
                self._recovery("operator_local_stop")
                epoch = self._db.execute("SELECT epoch FROM gateway WHERE id=1").fetchone()[0]
                request = self._request(request_id, operator_id, epoch, self._binding["local_stop"], now + self._binding["send_timeout"], "local_stop")
                self._pending(request)
            return self._effect(request, lambda: False)

    def reconcile(self, operator_id):
        if operator_id != self._binding["operator_id"]:
            raise GatewayError("explicit reconciliation requires the bound operator identity")
        with self._send_lock:
            with self._transaction():
                now = self._now()
                state = self._db.execute("SELECT * FROM gateway WHERE id=1").fetchone()
                if state["state"] != "recovery_required":
                    raise GatewayError("reconciliation requires recovery_required state")
                request = {"protocol": 1, "kind": "reconcile", "endpoint_id": self._binding["endpoint_id"],
                           "command_id": "query:" + uuid.uuid4().hex, "epoch": state["epoch"],
                           "expires_at": now + self._binding["send_timeout"],
                           "uncertain_command_ids": [row[0] for row in self._db.execute(
                               "SELECT command_id FROM commands WHERE state IN ('pending','unknown') ORDER BY rowid")]}
            evidence = self.transport.exchange(request, timeout=self._binding["send_timeout"], cancelled=lambda: False,
                                               authorize=lambda: deepcopy(request))
            if (not isinstance(evidence, dict) or isinstance(evidence.get("epoch"), bool)
                    or not isinstance(evidence.get("epoch"), int)
                    or any(evidence.get(key) != request[key] for key in ("endpoint_id", "command_id", "epoch"))):
                raise GatewayError("reconciliation identity mismatch")
            device_epoch = evidence.get("device_epoch")
            if evidence.get("status") != "state" or evidence.get("stopped") is not True or isinstance(device_epoch, bool) or not isinstance(device_epoch, int) or device_epoch < 0:
                raise GatewayError("device must attest its stopped state and persistent epoch")
            observed = _parameters(evidence.get("parameters"), self._binding["limits"])
            if observed.keys() != self._binding["limits"].keys():
                raise GatewayError("reconciliation must report every bound control parameter")
            outcomes = evidence.get("command_outcomes", {})
            if not isinstance(outcomes, dict) or len(_json(evidence).encode()) > 65536:
                raise GatewayError("invalid reconciliation evidence")
            with self._transaction():
                now = self._now()
                uncertain = self._db.execute("SELECT command_id FROM commands WHERE state IN ('pending','unknown')").fetchall()
                if any(outcomes.get(row[0]) not in {"applied", "not_applied"} for row in uncertain):
                    raise GatewayError("every uncertain command needs an explicit observed device outcome")
                for row in uncertain:
                    self._db.execute("UPDATE commands SET state=?,ack=? WHERE command_id=?", ("reconciled_" + outcomes[row[0]], _json(evidence), row[0]))
                self._db.execute("INSERT INTO recovery(recorded_at,evidence) VALUES(?,?)", (now, _json(evidence)))
                self._db.execute("UPDATE gateway SET state='idle',owner=NULL,lease_expires=0,epoch=MAX(epoch,?)+1,reason=NULL WHERE id=1", (device_epoch,))
            self._stop_requested.clear()
            return {"state": "idle", "replay": False, "evidence": deepcopy(evidence)}

    def snapshot(self):
        with self._transaction():
            self._now()
            state = dict(self._db.execute("SELECT epoch,state,owner,lease_expires,reason FROM gateway WHERE id=1").fetchone())
        return {**state, "endpoint_id": self._binding["endpoint_id"], "physical_qualified": False,
                "qualification": "fixture-tested journal boundary; matching hardware acknowledgement/fencing unqualified"}

    def journal(self):
        with self._transaction():
            return [dict(row) for row in self._db.execute("SELECT * FROM commands ORDER BY rowid")]

    def close(self):
        with self._send_lock, self._mutex:
            if not self._closed:
                self._db.close()
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
                self._lock.close()
                fcntl.flock(self._endpoint_lock.fileno(), fcntl.LOCK_UN)
                self._endpoint_lock.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _matched(request, response):
    return isinstance(response, dict) and all(response.get(key) == request[key] for key in ("endpoint_id", "command_id", "epoch"))


def _deadline(timeout, cancelled, started):
    if cancelled():
        raise GatewayError("transport cancelled; application outcome may be unknown")
    if time.monotonic() - started >= timeout:
        raise TimeoutError("device application acknowledgement timed out")


class SerialTransport:
    """JSON-lines application protocol over explicit serial binding; not generic device control."""
    kind = "serial"
    def __init__(self, config):
        self.config = deepcopy(config)  # No port is opened here.

    def exchange(self, request, *, timeout, cancelled, authorize):
        import serial
        payload = (_json(request) + "\n").encode()
        if len(payload) > 65536:
            raise GatewayError("serial request exceeds 64 KiB")
        started = time.monotonic()
        _deadline(timeout, cancelled, started)
        with serial.Serial(self.config["port"], baudrate=int(self.config.get("baudrate", 115200)),
                           timeout=min(.1, timeout), write_timeout=timeout, exclusive=True) as port:
            request = authorize()
            payload = (_json(request) + "\n").encode()
            _deadline(timeout, cancelled, started)
            if port.write(payload) != len(payload):
                raise GatewayError("partial serial command write")
            buffer = bytearray()
            while True:
                _deadline(timeout, cancelled, started)
                buffer.extend(port.read_until(b"\n", 65536 - len(buffer)))
                if len(buffer) >= 65536:
                    raise GatewayError("serial acknowledgement exceeds 64 KiB")
                if buffer.endswith(b"\n"):
                    response = json.loads(buffer)
                    buffer.clear()
                    if _matched(request, response):
                        return response


class ModbusMailboxTransport:
    """Custom JSON register mailbox, NOT arbitrary standard Modbus actuator registers.

    Each mailbox starts with byte length then big-endian UTF-8 word pairs. Zero
    request length means staging, not dispatch. Payload writes are chunked; writing
    the final nonzero length commits the application request. Device firmware must
    atomically stage, fence, deduplicate and acknowledge this custom schema.
    """
    kind = "modbus"
    def __init__(self, config):
        self.config = deepcopy(config)

    def exchange(self, request, *, timeout, cancelled, authorize):
        from pymodbus.client import ModbusTcpClient
        payload = _json(request).encode()
        capacity = int(self.config.get("capacity", 512))
        if not 2 <= capacity <= 4096 or len(payload) > (capacity - 1)*2:
            raise GatewayError("application envelope exceeds the bound Modbus mailbox capacity")
        padded = payload + (b"\0" if len(payload) % 2 else b"")
        registers = list(struct.unpack(f">{len(padded)//2}H", padded))
        started = time.monotonic()
        _deadline(timeout, cancelled, started)
        # Numeric address avoids unbounded DNS resolution in a physical write path.
        ipaddress.ip_address(self.config["host"])
        client = ModbusTcpClient(self.config["host"], port=int(self.config.get("port", 502)), timeout=min(.2, timeout), retries=0)
        unit = int(self.config.get("device_id", 1))
        try:
            if not client.connect():
                raise ConnectionError("bound Modbus endpoint unavailable")
            _deadline(timeout, cancelled, started)
            start = int(self.config["request_register"])
            def write(address, values):
                _deadline(timeout, cancelled, started)
                response = client.write_registers(address, values, device_id=unit)
                if response.isError():
                    raise GatewayError("Modbus staging/commit register write failed")
            write(start, [0])
            for index in range(0, len(registers), 123):
                write(start + 1 + index, registers[index:index+123])
            authorize()  # Only the final length write authorizes device-side application.
            write(start, [len(payload)])
            while True:
                words = []
                for offset in range(0, capacity, 125):
                    _deadline(timeout, cancelled, started)
                    response = client.read_holding_registers(int(self.config["response_register"]) + offset,
                                                             count=min(125, capacity-offset), device_id=unit)
                    if response.isError():
                        raise GatewayError("Modbus acknowledgement mailbox unavailable")
                    words.extend(response.registers)
                if words and 0 < words[0] <= (len(words)-1)*2:
                    data = struct.pack(f">{len(words)-1}H", *words[1:])[:words[0]]
                    ack = json.loads(data)
                    if _matched(request, ack):
                        return ack
                time.sleep(.01)
        finally:
            client.close()


class MQTTTransport:
    """One publish plus application ack; broker QoS delivery is not an actuation ack."""
    kind = "mqtt"
    def __init__(self, config, *, username=None, password=None, tls_context=None):
        self.config = deepcopy(config)
        self._credentials = username, password
        self._tls_context = tls_context

    def exchange(self, request, *, timeout, cancelled, authorize):
        import paho.mqtt.client as mqtt
        ipaddress.ip_address(self.config["host"])
        started = time.monotonic()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311, reconnect_on_failure=False)
        client.connect_timeout = min(timeout, 2)
        if self._credentials[0] is not None:
            client.username_pw_set(*self._credentials)
        if self._tls_context is not None:
            client.tls_set_context(self._tls_context)
        received = []
        subscribed = []
        connected = []
        def on_connect(client, userdata, flags, reason_code, properties):
            if reason_code.is_failure:
                raise GatewayError("MQTT broker rejected the authenticated connection")
            connected.append(True)
        def on_message(client, userdata, message):
            if len(message.payload) > 65536:
                raise GatewayError("MQTT application acknowledgement exceeds 64 KiB")
            if message.retain:
                return  # A retained historical reply cannot authorize a new command.
            response = json.loads(message.payload)
            if _matched(request, response):
                received.append(response)
        def on_subscribe(client, userdata, mid, reason_codes, properties):
            if any(code.is_failure for code in reason_codes):
                raise GatewayError("MQTT ack subscription rejected")
            subscribed.append(True)
        client.on_message, client.on_subscribe, client.on_connect = on_message, on_subscribe, on_connect
        try:
            _deadline(timeout, cancelled, started)
            client.connect(self.config["host"], int(self.config.get("port", 1883)), keepalive=30)
            while not connected:
                _deadline(timeout, cancelled, started)
                if client.loop(timeout=min(.1, timeout)) != mqtt.MQTT_ERR_SUCCESS:
                    raise ConnectionError("MQTT connection failed before CONNACK")
            result, _ = client.subscribe(self.config["ack_topic"], qos=1)
            if result != mqtt.MQTT_ERR_SUCCESS:
                raise GatewayError("MQTT ack subscription failed")
            while not subscribed:
                _deadline(timeout, cancelled, started)
                if client.loop(timeout=min(.1, timeout)) != mqtt.MQTT_ERR_SUCCESS:
                    raise ConnectionError("MQTT connection failed before dispatch")
            _deadline(timeout, cancelled, started)
            request = authorize()
            _deadline(timeout, cancelled, started)
            result = client.publish(self.config["command_topic"], _json(request), qos=1, retain=False)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                raise ConnectionError("MQTT publish failed")
            while not received:
                _deadline(timeout, cancelled, started)
                if client.loop(timeout=min(.1, timeout)) != mqtt.MQTT_ERR_SUCCESS:
                    raise ConnectionError("MQTT disconnected with unknown command outcome")
            return received[0]
        finally:
            client.disconnect()


def transport_for(binding):
    config = binding["transport"]
    factory = {"serial": SerialTransport, "modbus": ModbusMailboxTransport, "mqtt": MQTTTransport}.get(config["kind"])
    if factory is None:
        raise GatewayError("fixture transport must be supplied explicitly")
    return factory(config)
