"""Owned localhost test processes and simulated MQTT protocol-1 device.

These helpers exercise real broker/client wire traffic. They provide no physical
hardware, firmware, timing, or stability qualification. All control values and
units must come from the caller; there are no paper or hardware defaults.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import socket
import subprocess
import tempfile
import threading
import time
import uuid


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _typed_parameters(value, units=None):
    if not isinstance(value, dict) or not value or (units is not None and set(value) - set(units)):
        raise ValueError("nonempty, known typed fixture parameters required")
    result = {}
    for key, typed in value.items():
        if (not isinstance(key, str) or not isinstance(typed, dict)
                or set(typed) != {"value", "unit"} or not isinstance(typed["unit"], str)
                or isinstance(typed["value"], bool) or not isinstance(typed["value"], (int, float))
                or not math.isfinite(typed["value"])
                or (units is not None and typed["unit"] != units[key])):
            raise ValueError("invalid typed fixture parameter")
        result[key] = deepcopy(typed)
    return result


class OwnedMqttBroker:
    """Explicit Mosquitto executable, temporary state, foreground owned process.

    The constructor never starts a process. ``start`` and the context manager
    return self. Only IPv4 127.0.0.1 is accepted, and port=0 selects a free port.
    ``close`` terminates/reaps only this process and removes its temporary state.
    The final log remains available as ``log_text`` after cleanup.
    """

    def __init__(self, binary, root, host="127.0.0.1", port=0):
        if host != "127.0.0.1":
            raise ValueError("owned MQTT broker host must be exactly 127.0.0.1")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("invalid owned MQTT port")
        self.binary = Path(binary).resolve()
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise ValueError("an explicit executable Mosquitto binary is required")
        self.root, self.host, self.port = Path(root), host, port
        self.process = None
        self.runtime_path = None
        self.log_text = ""
        self._temporary = None
        self._log = None
        self._closed = False

    @property
    def url(self):
        return f"mqtt://{self.host}:{self.port}"

    def start(self):
        if self._closed:
            raise RuntimeError("closed broker cannot be restarted")
        if self.process is not None:
            raise RuntimeError("owned broker has already been started")
        self.root.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(prefix="owned-mqtt-", dir=self.root)
        self.runtime_path = Path(self._temporary.name)
        try:
            if not self.port:
                with socket.socket() as reservation:
                    reservation.bind((self.host, 0))
                    self.port = reservation.getsockname()[1]
            config = self.runtime_path / "mosquitto.conf"
            config.write_text(f"listener {self.port} {self.host}\nallow_anonymous true\n"
                              "persistence false\nlog_dest stdout\nlog_type all\n")
            self._log = (self.runtime_path / "broker.log").open("w+")
            self.process = subprocess.Popen([str(self.binary), "-c", str(config)],
                                            cwd=self.runtime_path, stdin=subprocess.DEVNULL,
                                            stdout=self._log, stderr=subprocess.STDOUT)
            until = time.monotonic() + 5
            while time.monotonic() < until:
                if self.process.poll() is not None:
                    raise RuntimeError("owned broker exited before readiness")
                try:
                    with socket.create_connection((self.host, self.port), timeout=.05):
                        return self
                except OSError:
                    time.sleep(.01)
            raise TimeoutError("owned broker did not open its localhost listener")
        except BaseException as exc:
            self.close()
            raise RuntimeError(f"owned MQTT broker startup failed: {exc}\n{self.log_text}") from exc

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=3)
                else:
                    self.process.wait()
        finally:
            if self._log is not None:
                self._log.seek(0)
                self.log_text = self._log.read()
                self._log.close()
            if self._temporary is not None:
                self._temporary.cleanup()

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()


class MqttTestDevice:
    """Host-paced simulated device implementing gateway.py wire protocol 1.

    ``topics`` is a mapping with distinct exact ``command``, ``ack``, and
    ``telemetry`` strings. Parameter values/units and the complete stopped state
    are caller supplied. Epochs, immutable command receipts, outcomes and applied
    parameters are atomically persisted in ``state_path`` before an application
    ACK. The default state_path is inside the owned broker's temporary directory;
    callers may explicitly supply a path to retain state across broker lifetimes.

    Fault controls are diagnostic: ``drop_command_ids`` drops before applying,
    ``drop_ack_ids`` applies without publishing its ACK, and ``ack_delay`` delays
    ACKs without blocking commands/telemetry. Noise uses a local seeded RNG.
    ``parameters``, ``outcomes``, ``effects``, ``received_requests`` and ``events``
    return thread-safe snapshots. No application or firmware code is generated.
    """

    def __init__(self, broker, topics, endpoint_id, initial_parameters, *, stop_parameters,
                 telemetry_parameter=None, sample_period=.025, ack_delay=0,
                 drop_ack_ids=(), drop_command_ids=(), noise=0, seed=0, state_path=None):
        if not isinstance(broker, OwnedMqttBroker) or broker.host != "127.0.0.1":
            raise ValueError("an owned localhost broker is required")
        if (not isinstance(topics, dict) or set(topics) != {"command", "ack", "telemetry"}
                or any(not isinstance(topic, str) or not topic or any(c in topic for c in "#+\0")
                       for topic in topics.values()) or len(set(topics.values())) != 3):
            raise ValueError("three distinct exact MQTT topics are required")
        if not isinstance(endpoint_id, str) or not 1 <= len(endpoint_id) <= 128:
            raise ValueError("fixture endpoint identity is required")
        for name, value in [("sample_period", sample_period), ("ack_delay", ack_delay), ("noise", noise)]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {name}")
        if sample_period < .001:
            raise ValueError("sample_period must be at least .001 seconds")
        self.broker, self.topics, self.endpoint_id = broker, deepcopy(topics), endpoint_id
        self._parameters = _typed_parameters(initial_parameters)
        self._units = {name: typed["unit"] for name, typed in self._parameters.items()}
        self.stop_parameters = _typed_parameters(stop_parameters, self._units)
        if set(self.stop_parameters) != set(self._parameters):
            raise ValueError("stop_parameters must cover every fixture parameter")
        self.telemetry_parameter = telemetry_parameter or next(iter(self._parameters))
        if self.telemetry_parameter not in self._parameters:
            raise ValueError("unknown telemetry_parameter")
        self.sample_period, self.ack_delay, self.noise = sample_period, ack_delay, noise
        self.drop_ack_ids, self.drop_command_ids = frozenset(drop_ack_ids), frozenset(drop_command_ids)
        self._random = random.Random(seed)
        self.state_path = Path(state_path) if state_path is not None else None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error = None
        self.client = self.thread = None
        self._epoch = 0
        self._outcomes, self._receipts = {}, {}
        self._effects, self._requests, self._events = [], [], []
        self._pending_acks = []
        self._stream = uuid.uuid4().hex
        self._tick = 0

    @property
    def parameters(self):
        with self._lock:
            return deepcopy(self._parameters)

    @property
    def outcomes(self):
        with self._lock:
            return deepcopy(self._outcomes)

    @property
    def effects(self):
        with self._lock:
            return deepcopy(self._effects)

    @property
    def received_requests(self):
        with self._lock:
            return deepcopy(self._requests)

    @property
    def events(self):
        with self._lock:
            return deepcopy(self._events)

    @property
    def device_epoch(self):
        with self._lock:
            return self._epoch

    def _persist(self):
        document = {"endpoint_id": self.endpoint_id, "stop_parameters": self.stop_parameters,
                    "epoch": self._epoch, "parameters": self._parameters,
                    "outcomes": self._outcomes, "receipts": self._receipts}
        temporary = self.state_path.with_name(self.state_path.name + ".pending")
        with temporary.open("w") as output:
            output.write(_json(document) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.state_path)
        descriptor = os.open(self.state_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _identity(self, request):
        return {key: request.get(key) for key in ("endpoint_id", "command_id", "epoch")}

    def _reject(self, request, reason, *, remember=True):
        ack = {**self._identity(request), "status": "rejected", "applied": False, "reason": reason}
        command_id = request.get("command_id")
        if remember and isinstance(command_id, str) and command_id not in self._receipts:
            self._outcomes[command_id] = "not_applied"
            self._receipts[command_id] = {"request": request, "ack": ack}
            self._persist()
        self._events.append({"event": "rejected", "command_id": command_id,
                             "reason": reason, "host_monotonic": time.monotonic()})
        return ack

    def _handle(self, request):
        self._requests.append(deepcopy(request))
        epoch, expiry, command_id = request.get("epoch"), request.get("expires_at"), request.get("command_id")
        if (request.get("protocol") != 1 or request.get("endpoint_id") != self.endpoint_id
                or not isinstance(command_id, str) or not 1 <= len(command_id) <= 128
                or isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0
                or isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry)):
            return self._reject(request, "invalid_envelope", remember=False)
        if expiry <= time.time():
            return self._reject(request, "expired")
        if epoch < self._epoch:
            return self._reject(request, "stale_epoch")
        if request.get("kind") == "reconcile":
            ids = request.get("uncertain_command_ids")
            if not isinstance(ids, list) or any(not isinstance(key, str) for key in ids):
                return self._reject(request, "invalid_reconciliation", remember=False)
            self._epoch = epoch
            self._persist()
            return {**self._identity(request), "status": "state", "device_epoch": self._epoch,
                    "stopped": self._parameters == self.stop_parameters,
                    "parameters": deepcopy(self._parameters),
                    "command_outcomes": {key: self._outcomes.get(key, "not_applied") for key in ids}}
        if command_id in self._receipts:
            receipt = self._receipts[command_id]
            if receipt["request"] != request:
                return self._reject(request, "command_identity_collision", remember=False)
            return deepcopy(receipt["ack"])
        if request.get("kind") not in {"command", "local_stop"} or not isinstance(request.get("owner"), str):
            return self._reject(request, "invalid_command")
        try:
            parameters = _typed_parameters(request.get("parameters"), self._units)
        except ValueError:
            return self._reject(request, "invalid_typed_parameters")
        if request["kind"] == "local_stop" and parameters != self.stop_parameters:
            return self._reject(request, "local_stop_parameters_mismatch")
        self._epoch = epoch
        if command_id in self.drop_command_ids:
            self._reject(request, "configured_drop_before_apply")
            return None
        self._parameters.update(parameters)
        self._outcomes[command_id] = "applied"
        ack = {**self._identity(request), "status": "confirmed", "applied": True,
               "applied_parameters": deepcopy(self._parameters), "simulated_device": True}
        self._receipts[command_id] = {"request": deepcopy(request), "ack": deepcopy(ack)}
        self._persist()
        self._effects.append(deepcopy(request))
        self._events.append({"event": "applied", "command_id": command_id, "host_monotonic": time.monotonic()})
        return ack

    def _publish(self, topic, document, *, qos):
        result = self.client.publish(topic, _json(document), qos=qos, retain=False)
        if result.rc != 0:
            raise ConnectionError("simulated MQTT device publish failed")

    def _run(self):
        next_sample = time.monotonic()
        try:
            while not self._stop.is_set():
                if self.client.loop(timeout=.005) != 0:
                    raise ConnectionError("simulated MQTT device disconnected")
                if not self._ready.is_set():
                    continue
                with self._lock:
                    now = time.monotonic()
                    pending, self._pending_acks = self._pending_acks, []
                    for due, response in pending:
                        if due > now:
                            self._pending_acks.append((due, response))
                            continue
                        self._publish(self.topics["ack"], response, qos=1)
                        self._events.append({"event": "ack_published", "command_id": response["command_id"],
                                             "host_monotonic": time.monotonic()})
                    if now >= next_sample:
                        self._tick += 1
                        value = self._parameters[self.telemetry_parameter]["value"]
                        telemetry = {"value": value + self._random.uniform(-self.noise, self.noise),
                                     "record_id": f"owned-mqtt:{self.endpoint_id}:{self._stream}:{self._tick}",
                                     "source_timestamp": time.time(), "host_monotonic": now, "tick": self._tick,
                                     "simulation_time": self._tick * self.sample_period,
                                     "clock": {"source_timestamp": "host_unix_time", "simulation": "host-paced discrete ticks",
                                               "tick_period_seconds": self.sample_period},
                                     "endpoint_id": self.endpoint_id, "epoch": self._epoch,
                                     "parameters": deepcopy(self._parameters), "simulated_device": True,
                                     "physical_stability_qualified": False}
                        self._publish(self.topics["telemetry"], telemetry, qos=0)
                        next_sample = now + self.sample_period
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            self._stop.set()

    def start(self):
        import paho.mqtt.client as mqtt
        if self.client is not None or self._stop.is_set():
            raise RuntimeError("simulated device may only be started once")
        if self.broker.process is None or self.broker.process.poll() is not None:
            raise RuntimeError("owned broker must be running")
        if self.state_path is None:
            suffix = hashlib.sha256(self.endpoint_id.encode()).hexdigest()[:16]
            self.state_path = self.broker.runtime_path / f"device-{suffix}.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            document = json.loads(self.state_path.read_text())
            if document["endpoint_id"] != self.endpoint_id or document["stop_parameters"] != self.stop_parameters:
                raise ValueError("persisted fixture state belongs to a different binding")
            restored = _typed_parameters(document["parameters"], self._units)
            if set(restored) != set(self._units):
                raise ValueError("persisted fixture parameters do not cover the binding")
            self._parameters, self._epoch = restored, document["epoch"]
            self._outcomes, self._receipts = document["outcomes"], document["receipts"]
        else:
            self._persist()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="owned-device-" + uuid.uuid4().hex,
                                  protocol=mqtt.MQTTv311, reconnect_on_failure=False)
        self.client.connect_timeout = 2

        def on_connect(client, userdata, flags, reason_code, properties):
            if reason_code.is_failure:
                raise ConnectionError("simulated device connection rejected")
            result, _ = client.subscribe(self.topics["command"], qos=1)
            if result != mqtt.MQTT_ERR_SUCCESS:
                raise ConnectionError("simulated device command subscribe failed")

        def on_subscribe(client, userdata, mid, reason_codes, properties):
            if any(code.is_failure for code in reason_codes):
                raise ConnectionError("simulated device command subscription rejected")
            self._ready.set()

        def on_message(client, userdata, message):
            if message.retain or message.topic != self.topics["command"]:
                return
            if len(message.payload) > 65536:
                raise ValueError("simulated device command exceeds 64 KiB")
            document = json.loads(message.payload)
            if not isinstance(document, dict):
                raise ValueError("simulated device command must be a JSON object")
            with self._lock:
                response = self._handle(document)
                if response is not None and document.get("command_id") not in self.drop_ack_ids:
                    self._pending_acks.append((time.monotonic() + self.ack_delay, response))

        self.client.on_connect, self.client.on_subscribe, self.client.on_message = on_connect, on_subscribe, on_message
        try:
            self.client.connect(self.broker.host, self.broker.port, keepalive=30)
            self.thread = threading.Thread(target=self._run, name="owned-mqtt-simulated-device", daemon=True)
            self.thread.start()
            if not self._ready.wait(timeout=3):
                raise TimeoutError("simulated device command subscription timed out")
            if self._error:
                raise RuntimeError("simulated device failed to start") from self._error
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout=3)
        if self.client is not None:
            self.client.disconnect()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=1)
            if self.thread.is_alive():
                raise RuntimeError("owned MQTT simulated device thread did not stop")

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        if exc_type is None and self._error is not None:
            raise RuntimeError("owned MQTT simulated device failed") from self._error
