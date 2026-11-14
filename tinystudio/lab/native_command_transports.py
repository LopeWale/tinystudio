"""Native command delivery through exact operator-selected transport profiles.

Construction validates configuration without opening devices or importing their
SDKs. Delivery receipts distinguish local writes, broker/protocol responses, and
operator-adapter claims. None of the built-ins confirms device application or a
physical outcome. Native DNS/open/SDK calls may finish after the selected wait;
authority is rechecked after setup and immediately before application writes.
"""
from __future__ import annotations

from copy import deepcopy
import base64
import hashlib
import importlib
import inspect
import math
from pathlib import Path
import socket
import ssl
import time

from .command_profiles import NativeCommandProfile, decode_bytes, COMMAND_FORMAT, RECEIPT_FORMAT
from .contracts import canonical
from .engine import ExecutionCancelled
from .hardware_transports import ModbusEndpoint, SerialEndpoint
from .modbus_trace import RawModbusTrace


_CHUNK = 65536  # Streaming operation size, never a response or evidence cap.
_IDENTITIES = ("endpoint_id", "command_id", "epoch", "profile_digest")


class NativeCommandTransportError(RuntimeError):
    """Dispatch failed or became uncertain; retain its explicit partial receipt."""
    def __init__(self, message, *, delivery_evidence):
        super().__init__(message)
        self.delivery_evidence = delivery_evidence


def _bytes(value, scope, maximum):
    prefix = value if maximum is None else value[:maximum]
    return {"scope": scope, "size": len(value), "sha256": hashlib.sha256(value).hexdigest(),
            "base64": base64.b64encode(prefix).decode("ascii"), "captured_bytes": len(prefix),
            "truncated": len(prefix) < len(value)}


class NativeCommandTransport:
    def __init__(self, binding, *, runtime_secrets=None, libraries=None, clock=time):
        self.profile = NativeCommandProfile.from_binding(binding)
        self._runtime_secrets = deepcopy({} if runtime_secrets is None else runtime_secrets)
        if not isinstance(self._runtime_secrets, dict):
            raise ValueError("runtime_secrets must be an operator-supplied object")
        if self.kind != "custom":
            tls_fields = {"ca_certs", "certfile", "keyfile", "key_password"}
            allowed = (tls_fields | {"server_hostname"} if self.kind == "tcp" else
                       tls_fields | {"username", "password"} if self.kind == "mqtt" else set())
            secrets = self._runtime_secrets
            if set(secrets) - allowed or any(value is not None and not isinstance(value, str) for value in secrets.values()):
                raise ValueError("runtime secrets include unsupported native fields or values")
            if any(secrets.get(key) is not None for key in tls_fields | {"server_hostname"}) and not self.binding["transport"].get("tls"):
                raise ValueError("runtime TLS settings require the explicitly selected TLS transport")
            if secrets.get("password") is not None and not isinstance(secrets.get("username"), str):
                raise ValueError("runtime MQTT password requires an explicit username")
            if any(secrets.get(key) is not None for key in {"keyfile", "key_password"}) and not secrets.get("certfile"):
                raise ValueError("runtime TLS private-key settings require certfile")
            if any(key in secrets and secrets[key] == "" for key in tls_fields - {"key_password"} | {"server_hostname"}):
                raise ValueError("runtime TLS paths and server hostname must be nonempty")
        self.libraries = dict(libraries or {})
        self.clock = clock

    @property
    def binding(self):
        return self.profile.binding

    @property
    def kind(self):
        return self.profile.binding["transport"]["kind"]

    @property
    def runtime_secrets(self):
        return deepcopy(self._runtime_secrets)

    def _library(self, name):
        return self.libraries[name] if name in self.libraries else importlib.import_module(name)

    def _validate(self, request):
        required = {"protocol", "endpoint_id", "command_id", "epoch", "owner", "expires_at", "kind", "profile_digest", "payload"}
        if not isinstance(request, dict) or set(request) != required or request["protocol"] != COMMAND_FORMAT:
            raise ValueError("native delivery requires the exact committed command envelope")
        if request["endpoint_id"] != self.binding["endpoint_id"] or request["profile_digest"] != self.profile.digest:
            raise ValueError("command belongs to a different endpoint or command profile")
        if (type(request["epoch"]) is not int or request["epoch"] < 1
                or any(not isinstance(request[key], str) or not request[key] for key in ("command_id", "owner"))
                or type(request["expires_at"]) not in (int, float) or not math.isfinite(request["expires_at"])):
            raise ValueError("invalid native command identity, epoch or expiry")
        if request["kind"] not in {"command", "local_stop"}:
            raise ValueError("unsupported native command kind")
        payload = self.profile.validate_payload(request["payload"], operator_action=request["kind"] == "local_stop")
        if request["kind"] == "local_stop" and canonical(payload) != canonical(self.profile.local_stop_payload):
            raise ValueError("local stop must match the exact operator-selected payload")
        if canonical(payload) != canonical(request["payload"]):
            raise ValueError("native request payload differs from its validated committed form")
        return payload

    def exchange(self, request, *, timeout, cancelled, authorize):
        return _Exchange(self, request, timeout, cancelled, authorize).run()


def native_transport_for(binding, *, runtime_secrets=None):
    """Construct an unopened native driver without SDK/device/network I/O."""
    return NativeCommandTransport(binding, runtime_secrets=runtime_secrets)


class _Exchange:
    def __init__(self, driver, request, timeout, cancelled, authorize):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("selected native timeout must be finite and nonnegative")
        self.driver, self.request = driver, deepcopy(request)
        self.payload = driver._validate(self.request)
        self.config = driver.binding["transport"]
        self.capture = driver.binding["command_profile"]["max_capture_bytes"]
        self.response_limit = driver.binding["command_profile"]["max_response_bytes"]
        self.timeout, self.cancelled, self.authorize = float(timeout), cancelled, authorize
        self.started = driver.clock.monotonic()
        self.deadline = self.started + timeout
        self.resource = self.closer = None
        self.response = bytearray()
        self.written = bytearray()
        self.written_scope = "native_successful_return_values_only_application_payload"
        self.response_scope = "received_application_stream_bytes"
        self.trace = None
        self.phase = "validated"
        self.receipt = {"format": RECEIPT_FORMAT, **{key: self.request[key] for key in _IDENTITIES},
            "status": "unknown", "device_application_confirmed": False, "physical_outcome": "unverified",
            "evidence": {"transport_kind": driver.kind}, "cleanup": {"confirmed": True},
            "operational": {"selected_timeout": self.timeout, "authorization_checks": 0,
                "application_write_attempts": 0, "automatic_application_retries": False,
                "application_write_attempt_scope": "native_application_request_calls; stream continuations are not retries",
                "deadline_scope": "checks_between_native_calls_and_before_writes; DNS/open/SDK calls may exceed selected waits"}}

    def _error(self, error):
        text = str(error)
        def strings(value):
            if isinstance(value, str) and value:
                yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    yield from strings(item)
        for secret in sorted(strings(self.driver.runtime_secrets), key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        return {"type": type(error).__name__, "message": text}

    def guard(self):
        if self.cancelled():
            raise ExecutionCancelled("native command delivery cancelled")
        if self.driver.clock.monotonic() >= self.deadline:
            raise TimeoutError("selected native command wait expired")
        if self.driver.clock.time() >= self.request["expires_at"]:
            raise TimeoutError("committed native command expired")

    def remaining(self):
        self.guard()
        return max(0, self.deadline - self.driver.clock.monotonic())

    def authorize_write(self):
        self.phase = "authorize"
        self.guard()
        committed = self.authorize()
        self.driver._validate(committed)
        if canonical(committed) != canonical(self.request):
            raise ValueError("write authority differs from the exact committed request")
        self.guard()
        self.receipt["operational"]["authorization_checks"] += 1
        return deepcopy(committed)

    def begin_write(self):
        self.authorize_write()
        self.phase = "write"
        self.receipt["operational"]["application_write_attempts"] += 1

    def _response_budget(self, expected=None):
        read = self.config.get("read")
        limits = [value for value in (self.response_limit, read.get("max_bytes") if read else None) if value is not None]
        maximum = min(limits) if limits else None
        if expected is not None and maximum is not None and expected > maximum:
            raise ValueError("selected response exceeds the explicit operational response byte budget")
        return maximum

    def run(self):
        failure = None
        try:
            self.guard()
            getattr(self, "_" + self.driver.kind)()
            self.guard()
        except Exception as error:
            failure = error
        finally:
            if self.closer is not None:
                try:
                    self.closer()
                except Exception as error:
                    self.receipt["cleanup"] = {"confirmed": False, "error": self._error(error)}
                    if failure is None:
                        failure = error
                        self.phase = "close"
            if self.driver.kind in {"serial", "tcp", "mqtt", "can"}:
                self.receipt["evidence"]["written"] = _bytes(bytes(self.written), self.written_scope, self.capture)
                if self.response or self.payload["operation"].endswith(".query"):
                    self.receipt["evidence"]["response"] = _bytes(bytes(self.response), self.response_scope, self.capture)
            elif self.response:
                self.receipt["evidence"]["response"] = _bytes(bytes(self.response), self.response_scope, self.capture)
            if self.trace is not None:
                self.receipt["evidence"]["trace"] = {"summary": self.trace.summary(), "receipts": deepcopy(self.trace.receipts)}
            self.receipt["operational"]["elapsed_seconds"] = self.driver.clock.monotonic() - self.started
        if failure is None:
            try:
                self.guard()
                self.driver.profile.validate_receipt(self.request, self.receipt)
            except Exception as error:
                failure = error
                self.phase = "receipt"
        if failure is not None:
            self.receipt["delivery_status_before_error"] = self.receipt["status"]
            self.receipt.update(status="unknown", phase=self.phase, error=self._error(failure))
            raise NativeCommandTransportError(self.receipt["error"]["message"], delivery_evidence=self.receipt) from failure
        return self.receipt

    def _raw_payload(self):
        value = decode_bytes(self.payload["data"])
        self.receipt["evidence"]["request"] = _bytes(value, "authored_application_payload", self.capture)
        return value

    def _read_response(self, read):
        framing = self.config["read"]
        expected = framing.get("count") if framing["kind"] == "fixed_bytes" else None
        maximum = self._response_budget(expected)
        delimiter = decode_bytes(framing["delimiter"]) if expected is None else None
        self.phase = "read"
        while True:
            if expected is not None and len(self.response) >= expected:
                return
            if delimiter is not None:
                boundary = self.response.find(delimiter)
                if boundary >= 0:
                    self.receipt["evidence"]["response_framing"] = {"kind": "delimiter",
                        "frame_bytes": boundary + len(delimiter), "trailing_received_bytes": len(self.response) - boundary - len(delimiter)}
                    return
            if maximum is not None and len(self.response) >= maximum:
                raise ValueError("response framing incomplete at the explicit operational byte budget")
            size = min(_CHUNK, expected - len(self.response)) if expected is not None else _CHUNK
            if maximum is not None:
                size = min(size, maximum - len(self.response))
            self.guard()
            block = read(size)
            if not block:
                raise EOFError("native query ended before its explicitly selected response framing")
            self.response.extend(block)

    def _serial(self):
        payload = self._raw_payload()
        if self.payload["operation"].endswith(".query"):
            self._response_budget(self.config["read"].get("count"))
        endpoint = SerialEndpoint.from_dict({key: value for key, value in self.config.items() if key not in {"kind", "read"}})
        self.phase = "connect"
        port = endpoint.open(self.remaining(), library=self.driver._library("serial"))
        self.resource, self.closer = port, port.close
        self.guard()
        native_write_wait = getattr(port, "write_timeout", None)
        bounded_write_wait = not self.config["port"].lower().startswith("rfc2217://")
        self.receipt["operational"]["serial_write_timeout"] = {"selected_native": native_write_wait,
            "bounded_by_remaining_wait": bounded_write_wait,
            "scope": "requested SDK timeout; native calls and host scheduling are not hard-deadline guarantees"}
        while len(self.written) < len(payload):
            if bounded_write_wait:
                remaining = self.remaining()
                port.write_timeout = remaining if native_write_wait is None else min(native_write_wait, remaining)
            self.begin_write()
            written = port.write(payload[len(self.written):])
            if type(written) is not int or not 0 < written <= len(payload) - len(self.written):
                raise OSError("serial write returned an invalid or zero byte count")
            self.written.extend(payload[len(self.written):len(self.written) + written])
        if self.payload["operation"].endswith(".query"):
            def read(size):
                port.timeout = self.remaining()
                # A delimiter read need not wait for a full streaming chunk.
                if self.config["read"]["kind"] == "delimiter":
                    size = min(size, max(1, getattr(port, "in_waiting", 0)))
                return port.read(size)
            self._read_response(read)
            self.receipt["status"] = "response_received"
        else:
            self.receipt["status"] = "delivered_unverified"

    def _tls_context(self):
        secrets = self.driver.runtime_secrets
        context = ssl.create_default_context(cafile=secrets.get("ca_certs"))
        if secrets.get("certfile"):
            context.load_cert_chain(secrets["certfile"], secrets.get("keyfile"), secrets.get("key_password"))
        return context

    def _tcp(self):
        payload = self._raw_payload()
        if self.payload["operation"].endswith(".query"):
            self._response_budget(self.config["read"].get("count"))
        self.phase = "connect"
        link = socket.create_connection((self.config["host"], self.config["port"]), timeout=self.remaining())
        self.resource, self.closer = link, link.close
        if self.config["tls"]:
            link.settimeout(self.remaining())
            link = self._tls_context().wrap_socket(link, server_hostname=self.driver.runtime_secrets.get("server_hostname") or self.config["host"])
            self.resource, self.closer = link, link.close
        self.guard()
        while len(self.written) < len(payload):
            link.settimeout(self.remaining())
            self.begin_write()
            written = link.send(payload[len(self.written):])
            if type(written) is not int or not 0 < written <= len(payload) - len(self.written):
                raise OSError("TCP write returned an invalid or zero byte count")
            self.written.extend(payload[len(self.written):len(self.written) + written])
        if self.payload["operation"].endswith(".query"):
            def read(size):
                link.settimeout(self.remaining())
                return link.recv(size)
            self._read_response(read)
            self.receipt["status"] = "response_received"
        else:
            self.receipt["status"] = "delivered_unverified"

    def _modbus(self):
        operation = self.payload["operation"].removeprefix("modbus.")
        response_expected = self.config["response_expected"]
        count = self.payload.get("count", 0)
        overhead = 9 if self.config["framing"] == "tcp" else 5
        response_bytes = overhead + (2 * count if "registers" in operation else (count + 7) // 8) if operation.startswith("read_") else overhead + 3
        self._response_budget(response_bytes if response_expected else 0)
        self.receipt["evidence"]["request"] = {"scope": "pymodbus_public_method_arguments", "payload": deepcopy(self.payload)}
        self.trace = RawModbusTrace(self.capture, clock=self.driver.clock, framing=self.config["framing"])
        def trace(sending, buffer):
            if sending:
                self.authorize_write()
                self.phase = "native_call"
            return self.trace.observe(sending, buffer)
        endpoint = ModbusEndpoint.from_dict({key: value for key, value in self.config.items() if key not in {"kind", "device_id", "response_expected"}})
        client = endpoint.create_client(self.remaining(), trace_packet=trace, library=self.driver._library("pymodbus.client"))
        self.resource, self.closer = client, client.close
        self.phase = "connect"
        if not client.connect():
            raise ConnectionError("selected Modbus endpoint connection failed")
        self.guard()
        method = getattr(client, operation)
        parameters = inspect.signature(method).parameters
        device = "device_id" if "device_id" in parameters else "slave" if "slave" in parameters else None
        if device is None:
            raise ValueError("native Modbus method lacks a public device_id/slave parameter")
        kwargs = {key: self.payload[key] for key in ("address", "count", "value", "values") if key in self.payload}
        kwargs[device] = self.config["device_id"]
        if "no_response_expected" in parameters:
            kwargs["no_response_expected"] = not response_expected
        elif not response_expected:
            raise ValueError("selected Modbus SDK cannot express no-response operation")
        if hasattr(client, "timeout"):
            client.timeout = self.remaining()
        self.begin_write()
        response = method(**kwargs)
        sdk = {"response_expected": response_expected, "application_write_retries": 0,
               "framing": self.config["framing"], "device_id": self.config["device_id"], "response": None}
        self.receipt["evidence"]["sdk"] = sdk
        if not response_expected:
            self.receipt["status"] = "delivered_unverified"
            return
        if response is None:
            raise EOFError("Modbus operation returned no protocol response")
        fields = {key: deepcopy(getattr(response, key)) for key in
                  ("function_code", "exception_code", "address", "count", "value", "registers", "bits") if hasattr(response, key)}
        sdk["response"] = fields
        if response.isError():
            raise RuntimeError("Modbus protocol exception response")
        if operation in {"read_holding_registers", "read_input_registers"}:
            if len(fields.get("registers", [])) != count:
                raise ValueError("Modbus register response count differs from requested count")
            observed_size = overhead + 2 * len(fields["registers"])
        elif operation in {"read_coils", "read_discrete_inputs"}:
            if len(fields["bits"]) < count:
                raise ValueError("Modbus bit response is shorter than requested count")
            sdk["requested_bits"] = fields["bits"][:count]
            sdk["padding_bits"] = fields["bits"][count:]
            observed_size = overhead + (len(fields["bits"]) + 7) // 8
        else:
            if fields.get("address") != self.payload["address"]:
                raise ValueError("Modbus write response address differs from the request")
            if operation == "write_register" and fields.get("registers", [fields.get("value")]) != [self.payload["value"]]:
                raise ValueError("Modbus write response value differs from the request")
            if operation == "write_coil" and fields.get("bits", [fields.get("value")]) != [self.payload["value"]]:
                raise ValueError("Modbus coil write response value differs from the request")
            if operation in {"write_registers", "write_coils"} and fields.get("count") != len(self.payload["values"]):
                raise ValueError("Modbus write response count differs from the request")
            observed_size = overhead + 3
        self._response_budget(observed_size)
        sdk["response_size"] = {"scope": "decoded_protocol_adu_bytes", "size": observed_size}
        self.receipt["status"] = "response_received" if operation.startswith("read_") else "protocol_accepted"

    def _mqtt(self):
        library = self.driver._library("paho.mqtt.client")
        payload = self._raw_payload()
        client = library.Client(callback_api_version=library.CallbackAPIVersion.VERSION2,
                                clean_session=True, reconnect_on_failure=False)
        self.resource, self.closer = client, client.disconnect
        client.connect_timeout = self.remaining()
        secrets = self.driver.runtime_secrets
        if "username" in secrets:
            client.username_pw_set(secrets["username"], secrets.get("password"))
        if self.config["tls"]:
            client.tls_set_context(self._tls_context())
        connected, errors = [], []
        def on_connect(client, userdata, flags, code, properties):
            if getattr(code, "is_failure", code != 0):
                errors.append(ConnectionError("MQTT broker rejected connection"))
            else:
                connected.append(True)
        client.on_connect = on_connect
        self.phase = "connect"
        if client.connect(self.config["host"], self.config["port"], keepalive=30) != library.MQTT_ERR_SUCCESS:
            raise ConnectionError("MQTT connection initialization failed")
        while not connected:
            self.guard()
            if errors:
                raise errors[0]
            code = client.loop(timeout=min(.1, self.remaining()))
            if code != library.MQTT_ERR_SUCCESS:
                raise ConnectionError("MQTT connection loop failed")
        self.begin_write()
        info = client.publish(self.config["topic"], payload=payload, qos=self.payload["qos"], retain=self.payload["retain"])
        if info.rc != library.MQTT_ERR_SUCCESS:
            raise ConnectionError("MQTT SDK rejected publish request")
        self.written.extend(payload)
        self.written_scope = "paho_accepted_application_payload_not_wire_capture"
        self.receipt["evidence"]["sdk"] = {"topic": self.config["topic"], "mid": info.mid,
            "qos": self.payload["qos"], "retain": self.payload["retain"],
            "acknowledgement_scope": "broker_protocol_only", "wire_capture_available": False,
            "protocol_retransmission": "MQTT QoS handshakes are SDK-managed; reconnect and application republish are disabled"}
        self.phase = "read"
        while not info.is_published():
            self.guard()
            # Loop may emit QoS protocol packets, so recheck authority before
            # allowing that native call. This does not republish the application.
            self.authorize_write()
            code = client.loop(timeout=min(.1, self.remaining()))
            if code != library.MQTT_ERR_SUCCESS:
                raise ConnectionError("MQTT disconnected before publish completion")
        self.receipt["status"] = "protocol_accepted" if self.payload["qos"] else "delivered_unverified"

    def _can(self):
        library = self.driver._library("can")
        payload = self._raw_payload()
        kwargs = {key: self.payload[key] for key in ("arbitration_id", "is_extended_id", "is_fd", "is_remote_frame",
                  "is_error_frame", "bitrate_switch", "error_state_indicator", "dlc") if key in self.payload}
        message = library.Message(data=payload, is_rx=False, check=True, **kwargs)
        self.phase = "connect"
        bus = library.Bus(interface=self.config["interface"], channel=self.config["channel"], ignore_config=True,
                          **self.config["options"])
        self.resource, self.closer = bus, bus.shutdown
        self.guard()
        selected_timeout = self.remaining()
        self.begin_write()
        bus.send(message, timeout=selected_timeout)
        self.written.extend(payload)
        self.written_scope = "python_can_send_accepted_application_payload_not_bus_bitstream"
        self.receipt["evidence"]["sdk"] = {key: getattr(message, key) for key in
            ("arbitration_id", "is_extended_id", "is_fd", "is_remote_frame", "is_error_frame",
             "bitrate_switch", "error_state_indicator", "dlc", "is_rx")}
        self.receipt["status"] = "delivered_unverified"

    def _custom(self):
        self.receipt["operational"].update(application_write_attempts=None,
                                           application_write_attempt_scope="operator_adapter_calls_are_not_instrumented")
        self.receipt["evidence"]["request"] = _bytes(canonical(self.payload), "canonical_json_adapter_payload", self.capture)
        module_name, class_name = self.config["entrypoint"].split(":", 1)
        self.phase = "connect"
        module = self.driver._library(module_name)
        factory=module
        for component in class_name.split('.'):
            factory=getattr(factory,component)
        adapter = factory(binding=deepcopy(self.driver.binding), runtime_secrets=deepcopy(self.driver.runtime_secrets))
        self.resource = adapter
        self.closer = getattr(adapter, "close", None)
        self.guard()
        descriptor = {"entrypoint": self.config["entrypoint"], "declared_identity": deepcopy(self.config["identity"]),
                      "version_claim": getattr(module, "__version__", None), "source_sha256": None,
                      "source_hash_scope": "module_file_bytes_observed_after_import",
                      "executed_code_attested": False,
                      "authorization_enforcement": "operator_adapter_contract; not measured device fencing"}
        if getattr(module, "__file__", None):
            source_hash = hashlib.sha256()
            with Path(module.__file__).open("rb") as stream:
                while block := stream.read(_CHUNK):
                    source_hash.update(block)
            descriptor["source_sha256"] = source_hash.hexdigest()
        self.receipt["evidence"]["adapter"] = descriptor
        result = adapter.exchange(deepcopy(self.request), timeout=self.remaining(), cancelled=self.cancelled,
                                  authorize=self.authorize_write)
        encoded = canonical(result)  # Reject nonfinite or unserializable reports.
        self.response.extend(encoded)
        self.response_scope = "canonical_json_operator_adapter_report_not_wire_capture"
        self.receipt["evidence"]["adapter_report"] = deepcopy(result)
        self._response_budget(len(encoded))
        if self.receipt["operational"]["authorization_checks"] == 0:
            raise ValueError("operator adapter did not check exact write authority")
        if not isinstance(result, dict) or any(key in result and result[key] != self.request[key] for key in _IDENTITIES):
            raise ValueError("operator adapter report identity differs from command")
        self.receipt["status"] = result.get("status", "delivered_unverified")
        self.receipt["device_application_confirmed"] = result.get("device_application_confirmed", False)
        if self.receipt['status']=='application_reported':
            self.receipt["application_confirmation_scope"] = "adapter_claim"
