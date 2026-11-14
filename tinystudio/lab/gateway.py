"""Persistent local command boundary, independent of the TinyStudio server database.

One operator-provisioned endpoint per journal in one canonical local state directory.
Endpoint locks also prevent different journals in that directory claiming the same
transport endpoint. The installer MUST provision that single directory; copying
journals to different roots or restoring old backups is not a valid ownership transfer.
Built-in transport construction does not open devices or network connections;
operator adapters are trusted host code. API: acquire(owner, ttl), dispatch(id,
owner, epoch, parameters or payload, expires_at), heartbeat, local_stop(operator, request_id),
reconcile(operator, evidence), snapshot/journal, close. HTTP/device-token authentication and
installation belong to the local gateway host, not this module or service tokens.

The legacy wire protocol 1 carries endpoint_id, command_id, epoch and absolute expires_at.
That selected protocol requires devices to persist epoch/id fences, reject expired/stale commands and return an
application ack with the same identity, status='confirmed', applied=True. Serial
writes, Modbus register responses and MQTT PUBACK are NOT application acks.
Reconciliation returns status='state', device_epoch, stopped=True, parameters,
and command_outcomes mapping every uncertain ID to applied/not_applied.
Native command profiles preserve operator-selected bytes and protocol operations,
retaining transport delivery separately from application and physical outcomes.
Their reconciliation records an explicit operator attestation, keeping unresolved
physical outcomes unverified and command identities consumed. Exactly-once physical
effects, hardware acknowledgement and device fencing remain unqualified.
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
from .gateway_configuration import GatewayStoragePaths
from .command_profiles import COMMAND_FORMAT,RECEIPT_FORMAT
from .gateway_lease_policy import GatewayLeasePolicy
from .gateway_lifecycle import GatewayLeaseLifecycle


class GatewayError(OwnershipError):
    pass


class GatewayBusy(GatewayError):
    pass


class GatewayExpired(GatewayError):
    """A new intent expired before admission; no application write was made."""
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
    if isinstance(supplied,dict) and 'command_profile' in supplied:
        from .command_profiles import NativeCommandProfile
        try:return NativeCommandProfile.from_binding(supplied).binding
        except ValueError as error:raise GatewayError(str(error)) from error
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
        from .command_profiles import NativeCommandProfile
        self._native_profile=(NativeCommandProfile.from_binding(self._binding)
                              if 'command_profile' in self._binding else None)
        self.lease_policy=GatewayLeasePolicy.from_binding(self._binding)
        self._leases=GatewayLeaseLifecycle(self)
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
                    {"kind": kind, "host": config["host"], "port": config.get("port", 1883), "command_topic": config.get("command_topic",config.get('topic'))} if kind == "mqtt" else
                    {"kind": kind, "endpoint_id": self._binding["endpoint_id"]})
        if self._native_profile is not None:
            if 'ownership_key' in self._binding:
                identity={'operator_selected_ownership_key':self._binding['ownership_key']}
            elif kind=='serial':
                port=config['port']
                identity={'kind':kind,'port':port if '://' in port else str(Path(port).resolve())}
            elif kind in {'tcp','modbus'}:
                identity={'kind':kind,'host':config['host'],'port':config.get('port',502 if kind=='modbus' else None)}
                if kind=='modbus':identity['device_id']=config.get('device_id',1)
            elif kind=='mqtt':
                identity={'kind':kind,'host':config['host'],'port':config.get('port',1883),'command_topic':config['topic']}
            elif kind=='can':identity={'kind':kind,'interface':config['interface'],'channel':config['channel']}
            elif kind=='custom':identity={'kind':kind,'identity':config['identity']}
        paths=GatewayStoragePaths(self.path)
        self._endpoint_lock = paths.endpoint_lock(identity).open("a+b")
        os.chmod(self._endpoint_lock.name, 0o600)
        try:
            fcntl.flock(self._endpoint_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._endpoint_lock.close()
            raise GatewayBusy("another gateway owns this endpoint in the provisioned state directory") from exc
        self._lock = paths.journal_lock.open("a+b")
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
            self._leases.install()
            with self._transaction():
                state = self._db.execute("SELECT * FROM gateway WHERE id=1").fetchone()
                encoded = _json(self._binding)
                if state is None:
                    now = _finite(self._clock(), "gateway clock")
                    self._db.execute("INSERT INTO gateway VALUES(1,?,0,'idle',NULL,0,?,NULL)", (encoded, now))
                elif state["binding"] != encoded:
                    raise GatewayError("journal is bound to a different endpoint/operator/configuration")
                # An existing recovery fence already revokes pending commands.
                # Preserve it so committed, unclaimed lease actions stay current.
                elif state["state"] != "recovery_required" and (
                        state["state"] == "owned" or self._db.execute(
                            "SELECT 1 FROM commands WHERE state='pending' LIMIT 1").fetchone()):
                    self._recovery("restart_with_ownership_or_pending_command")
                self._leases.recover()
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
                self._commit()
                raise
            except BaseException as cause:
                self._rollback(cause)
                raise
            else:
                self._commit()

    def _rollback(self,cause):
        try:self._db.execute('ROLLBACK')
        except BaseException as error:cause.add_note(f'Gateway rollback unconfirmed: {type(error).__name__}: {error}')

    def _commit(self):
        try:self._db.execute('COMMIT')
        except BaseException as cause:
            self._rollback(cause)
            raise

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
            self._leases.end(state,now=now,trigger='expiry')
        return now

    def _authority(self, owner, epoch):
        if isinstance(epoch, bool) or not isinstance(epoch, int) or not isinstance(owner, str):
            raise GatewayError("ownership epoch must be an integer and owner must identify a run")
        now = self._now()
        state = self._db.execute("SELECT * FROM gateway WHERE id=1").fetchone()
        if state["state"] != "owned" or state["owner"] != owner or state["epoch"] != epoch:
            raise GatewayError("fresh matching endpoint ownership and epoch required")
        return now, state

    def _identifier(self,value,label,*,internal=False):
        maximum=(self._native_profile.binding['command_profile']['max_identity_chars']
                 if self._native_profile is not None else 128)
        if (not isinstance(value,str) or not value or '\0' in value
                or not internal and maximum is not None and len(value)>maximum):
            raise GatewayError(label+' must be a nonempty identity within the selected endpoint policy')
        return value

    def _completed(self,state):
        if self._native_profile is None:return state=='confirmed'
        from .command_profiles import COMPLETED_NATIVE_STATUSES
        return state in COMPLETED_NATIVE_STATUSES

    def acquire(self, owner, *, ttl=30):
        self._identifier(owner,'owner')
        duration = self._lease_duration(ttl)
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
        duration = self._lease_duration(ttl)
        with self._transaction():
            now, _ = self._authority(owner, epoch)
            self._db.execute("UPDATE gateway SET lease_expires=? WHERE id=1", (now + duration,))
        return now + duration

    def _lease_duration(self,ttl):
        duration=_finite(ttl,'lease ttl')
        maximum=self._binding.get('max_lease_seconds',None if self._native_profile is not None else 3600)
        if maximum is not None:
            maximum=_finite(maximum,'max_lease_seconds')
            if maximum<=0:raise GatewayError('max_lease_seconds must be positive or None')
        if duration<=0 or maximum is not None and duration>maximum:
            raise GatewayError('lease ttl exceeds the selected endpoint policy')
        return duration

    def _request(self, command_id, owner, epoch, parameters, expires_at, kind,*,payload=None):
        self._identifier(command_id,'command',internal=kind=='local_stop')
        if self._native_profile is not None:
            if parameters is not None:raise GatewayError('native profile accepts payload rather than legacy typed parameters')
            try:normalized=self._native_profile.validate_payload(payload,operator_action=kind=='local_stop')
            except ValueError as error:raise GatewayError(str(error)) from error
            return {'protocol':COMMAND_FORMAT,'endpoint_id':self._binding['endpoint_id'],
                'command_id':command_id,'epoch':epoch,'owner':owner,'payload':normalized,
                'expires_at':_finite(expires_at,'command expiry'),'kind':kind,'profile_digest':self._native_profile.digest}
        if payload is not None:raise GatewayError('legacy typed profile does not accept native payload')
        return {"protocol": 1, "endpoint_id": self._binding["endpoint_id"], "command_id": command_id,
                "epoch": epoch, "owner": owner, "parameters": _parameters(parameters, self._binding["limits"]),
                "expires_at": _finite(expires_at, "command expiry"), "kind": kind}

    def _existing_receipt(self,request):
        encoded = _json(request)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        existing = self._db.execute("SELECT * FROM commands WHERE command_id=?", (request["command_id"],)).fetchone()
        if existing:
            if existing["payload_sha256"] != fingerprint:
                raise GatewayError("command identity already belongs to a different immutable request")
            if self._completed(existing['state']):
                return json.loads(existing["ack"])
            raise GatewayError("command identity is pending/uncertain/consumed; never automatically retry")
        return None

    def _pending(self, request):
        existing=self._existing_receipt(request)
        if existing is not None:return existing
        encoded=_json(request)
        fingerprint=hashlib.sha256(encoded.encode()).hexdigest()
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
                action=self._db.execute('SELECT state,revocation_epoch FROM lease_events WHERE stop_command_id=?',
                                        (request['command_id'],)).fetchone()
                if action is not None and (action['state']!='executing' or action['revocation_epoch']!=state['epoch']):
                    raise GatewayError('lease action was consumed or superseded before its final write')
            if now >= request["expires_at"]:
                raise GatewayError("command expired at the final transport write boundary")
            if self._native_profile is not None:
                if request.get('profile_digest')!=self._native_profile.digest:
                    raise GatewayError('command profile differs from the bound immutable profile')
                try:normalized=self._native_profile.validate_payload(request['payload'],operator_action=request['kind']=='local_stop')
                except ValueError as error:raise GatewayError(str(error)) from error
                if normalized!=request['payload']:raise GatewayError('native command changed after admission')
                if request['kind']=='local_stop' and normalized!=self._native_profile.local_stop_payload:
                    raise GatewayError('operator stop must use exactly the selected stop payload')
            else:_parameters(request["parameters"], self._binding["limits"])
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
        ack=None
        try:
            self._final_authorize(request)
            ack = self.transport.exchange(deepcopy(request), timeout=self._binding["send_timeout"], cancelled=cancelled,
                                          authorize=lambda: self._final_authorize(request))
            if self._native_profile is not None:
                try:self._native_profile.validate_receipt(request,ack)
                except ValueError as error:raise GatewayError(str(error)) from error
            else:self._ack(request, ack)
        except Exception as exc:
            evidence=getattr(exc,'delivery_evidence',None)
            if evidence is None and ack is not None:
                evidence={'classification':'unverified_transport_receipt','returned_receipt':ack,
                    'physical_outcome':'unverified','validation_error':str(exc)}
            self._unknown(request,exc,evidence)
        state=ack['status'] if self._native_profile is not None else 'confirmed'
        try:
            with self._transaction():
                self._db.execute("UPDATE commands SET state=?,ack=? WHERE command_id=?", (state,_json(ack),request["command_id"]))
                self._now()  # A late receipt never renews endpoint ownership.
        except GatewayError as error:
            error.delivery_receipt=deepcopy(ack)
            raise
        except Exception as error:
            self._unknown(request,error,{'classification':'delivery_receipt_commit_unconfirmed',
                'observed_delivery':ack,'physical_outcome':'unverified'})
        return ack

    def _unknown(self,request,cause,evidence):
        try:
            encoded=_json(evidence) if evidence is not None else None
            retained=json.loads(encoded) if encoded is not None else None
        except (TypeError,ValueError,RecursionError):
            retained={'classification':'unserializable_transport_evidence','error_type':type(cause).__name__}
            encoded=_json(retained)
        failure=GatewayError('command outcome unknown; explicit reconciliation required, no automatic retry')
        failure.delivery_receipt=retained
        try:
            with self._transaction():
                self._db.execute("UPDATE commands SET state='unknown',ack=?,error=? WHERE command_id=?", (encoded,type(cause).__name__,request['command_id']))
                state=self._db.execute('SELECT * FROM gateway WHERE id=1').fetchone()
                # Uncertainty from an old lease cannot revoke a later owner, nor
                # invalidate a lease action already committed by this owner.
                if (state['state']=='idle' or state['state']=='owned'
                        and state['owner']==request['owner'] and state['epoch']==request['epoch']):
                    if state['state']=='owned':
                        failure.revoked_lease={key:state[key] for key in ('owner','epoch','lease_expires')}
                    self._recovery('unknown_command_outcome')
        except Exception as error:
            failure.add_note(f'Outcome journal update unconfirmed: {type(error).__name__}: {error}')
        raise failure from cause

    def dispatch(self, command_id, *, owner, epoch, parameters=None, expires_at, payload=None,cancelled=lambda: False):
        with self._send_lock:
            request = self._request(command_id, owner, epoch, parameters, expires_at, "command",payload=payload)
            with self._transaction():
                existing_ack=self._existing_receipt(request)
                if existing_ack is not None:return existing_ack
                now, state = self._authority(owner, epoch)
                if now>=request['expires_at']:
                    raise GatewayExpired('command has expired before admission')
                if request['expires_at']>state['lease_expires']:
                    raise GatewayError('command outlives its ownership lease')
                admitted_lease=dict(state)
                existing_ack = self._pending(request)
            if existing_ack is not None:
                return existing_ack  # Read an existing receipt; never repeat the effect.
            should_cancel = lambda: (self._stop_requested.is_set() or cancelled()
                                     or self._leases.aborted(owner,epoch))
            if should_cancel():
                with self._transaction():
                    self._db.execute("UPDATE commands SET state='cancelled' WHERE command_id=?", (command_id,))
                self._leases.cancel(request,admitted_lease)
                raise GatewayError("command cancelled; inspect the configured local-stop outcome")
            try:
                ack = self._effect(request, should_cancel)
            except GatewayError as error:
                if should_cancel():
                    try:self._leases.cancel(request,getattr(error,'revoked_lease',admitted_lease))
                    except Exception as cleanup:
                        error.add_note('Selected lease cleanup failed: '+type(cleanup).__name__)
                        if getattr(cleanup,'lifecycle_receipt',None) is not None:
                            error.lifecycle_receipt=cleanup.lifecycle_receipt
                raise
            if should_cancel():
                self._leases.cancel(request,admitted_lease)
                raise GatewayError("cancelled after dispatch; delivery receipt retained; inspect the local-stop outcome")
            return ack

    def _stop_receipt(self,request_id,operator_id):
        existing=self._db.execute('SELECT * FROM commands WHERE command_id=?',(request_id,)).fetchone()
        if existing is None:return None
        stored=json.loads(existing['request'])
        if stored['kind']!='local_stop' or stored['owner']!=operator_id:
            raise GatewayError('local stop identity collision')
        if existing['state']=='not_configured' or self._completed(existing['state']):
            return json.loads(existing['ack'])
        raise GatewayError('uncertain local stop cannot be retried automatically')

    def _stop_request(self,request_id,epoch,now):
        if self._native_profile is not None and self._native_profile.local_stop_payload is None:
            return {'protocol':COMMAND_FORMAT,'endpoint_id':self._binding['endpoint_id'],
                'command_id':request_id,'epoch':epoch,'owner':self._binding['operator_id'],'payload':None,
                'expires_at':now+self._binding['send_timeout'],'kind':'local_stop','profile_digest':self._native_profile.digest}
        return self._request(request_id,self._binding['operator_id'],epoch,
            None if self._native_profile is not None else self._binding['local_stop'],
            now+self._binding['send_timeout'],'local_stop',
            payload=self._native_profile.local_stop_payload if self._native_profile is not None else None)

    def _record_unconfigured_stop(self,request):
        receipt={'format':RECEIPT_FORMAT,
            **{key:request[key] for key in ('endpoint_id','command_id','epoch','profile_digest')},
            'status':'not_configured','device_application_confirmed':False,'physical_outcome':'unverified',
            'disclosure':'Host authority revoked; no device stop action was configured or sent.'}
        self._db.execute("UPDATE commands SET state='not_configured',ack=? WHERE command_id=?",
                         (_json(receipt),request['command_id']))
        return receipt

    def release(self,request_id,*,owner,epoch):
        return self._leases.release(request_id,owner=owner,epoch=epoch)

    def watchdog_tick(self):return self._leases.tick()

    def lease_events(self):return self._leases.events()

    def local_stop(self, operator_id, *, request_id,_internal=False):
        # Generated stop identities are fixed implementation metadata, rather
        # than authored IDs governed by the optional user-input length budget.
        self._identifier(request_id,'local stop',internal=_internal)
        if operator_id != self._binding["operator_id"]:
            raise GatewayError("local stop requires the bound operator identity")
        with self._transaction():
            completed=self._stop_receipt(request_id,operator_id)
            if completed is not None:return completed
            # Lookup and signalling share the journal mutex. An old receipt
            # replay must never leave a cancellation flag on a newer owner.
            self._stop_requested.set()
        with self._send_lock:
            with self._transaction():
                completed=self._stop_receipt(request_id,operator_id)
                if completed is not None:return completed
                now = self._now()
                self._recovery("operator_local_stop")
                epoch = self._db.execute("SELECT epoch FROM gateway WHERE id=1").fetchone()[0]
                request=self._stop_request(request_id,epoch,now)
                self._pending(request)
                if self._native_profile is not None and self._native_profile.local_stop_payload is None:
                    return self._record_unconfigured_stop(request)
            return self._effect(request, lambda: False)

    def reconcile(self, operator_id,*,evidence=None):
        if operator_id != self._binding["operator_id"]:
            raise GatewayError("explicit reconciliation requires the bound operator identity")
        if self._native_profile is not None:
            return self._reconcile_native(operator_id,evidence)
        if evidence is not None:raise GatewayError('legacy device reconciliation obtains evidence through its bound protocol')
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
                self._leases.supersede()
                self._db.execute("UPDATE gateway SET state='idle',owner=NULL,lease_expires=0,epoch=MAX(epoch,?)+1,reason=NULL WHERE id=1", (device_epoch,))
            self._stop_requested.clear()
            return {"state": "idle", "replay": False, "evidence": deepcopy(evidence)}

    def _reconcile_native(self,operator_id,evidence):
        if not isinstance(evidence,dict) or evidence.get('acknowledge_unverified_outcomes') is not True:
            raise GatewayError('native reconciliation requires explicit operator acknowledgement of unresolved physical outcomes')
        try:operator_evidence=json.loads(_json(evidence))
        except (TypeError,ValueError) as error:raise GatewayError('operator evidence must be finite JSON') from error
        with self._send_lock:
            with self._transaction():
                now=self._now()
                state=self._db.execute('SELECT state,epoch FROM gateway WHERE id=1').fetchone()
                if state['state']!='recovery_required':raise GatewayError('reconciliation requires recovery_required state')
                uncertain=[row[0] for row in self._db.execute("SELECT command_id FROM commands WHERE state IN ('pending','unknown','cancelled') ORDER BY rowid")]
                retained={'classification':'operator_attestation','operator_id':operator_id,
                    'physical_outcome':'unverified','device_application_confirmed':False,
                    'operator_evidence':operator_evidence,'consumed_command_ids':uncertain,
                    'previous_epoch':state['epoch'],'observed_at':now,
                    'disclosure':'Operator-authorized host ownership reset; no physical effect or stopping claim is verified.'}
                retained['superseded_lease_event_keys']=self._leases.supersede()
                self._db.execute('INSERT INTO recovery(recorded_at,evidence) VALUES(?,?)',(now,_json(retained)))
                self._db.execute("UPDATE commands SET state='reconciled_consumed' WHERE state IN ('pending','unknown','cancelled')")
                self._db.execute("UPDATE gateway SET state='idle',owner=NULL,lease_expires=0,epoch=epoch+1,reason=NULL WHERE id=1")
            self._stop_requested.clear()
        return {'state':'idle','replay':False,'evidence':retained}

    def snapshot(self):
        with self._transaction():
            self._now()
            state = dict(self._db.execute("SELECT epoch,state,owner,lease_expires,reason FROM gateway WHERE id=1").fetchone())
        result={**state, "endpoint_id": self._binding["endpoint_id"], "physical_qualified": False,
                "qualification": "Host journal boundary; device application and physical effects require separate evidence."}
        result['lease_policy']=self.lease_policy.as_dict()
        if self._native_profile is not None:result['command_profile']=self._native_profile.describe()
        else:result['command_profile']={'format':'tinystudio.fenced-command-profile.v1','protocol':1,
            'required_device_capabilities':['epoch_fencing','command_deduplication','command_expiry','application_ack'],
            'device_capabilities_verified':False}
        return result

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


def transport_for(binding,*,runtime_secrets=None):
    if 'command_profile' in binding:
        from .native_command_transports import native_transport_for
        return native_transport_for(binding,runtime_secrets=runtime_secrets)
    if runtime_secrets:
        raise GatewayError('the default legacy driver does not consume runtime_secrets; use an explicit operator transport factory or native profile')
    config = binding["transport"]
    factory = {"serial": SerialTransport, "modbus": ModbusMailboxTransport, "mqtt": MQTTTransport}.get(config["kind"])
    if factory is None:
        raise GatewayError("fixture transport must be supplied explicitly")
    return factory(config)
