"""Parent-owned native session broker; authored code receives proposal authority.

This is a trusted-native contract, not an isolation boundary against same-UID
code. Gateway/device credentials never enter its child IPC descriptor or public
evidence. Device effects and physical stopping remain unverified.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import tempfile
import threading
import time

from .attempt_bound_transport import AttemptBoundTransport
from .command_profiles import _finite_json
from .evidence_io import JsonlEvidenceWriter
from .gateway import CommandGateway
from .gateway_configuration import GatewayStoragePaths
from .gateway_watchdog import GatewayWatchdog
from .hardware_session_configuration import SessionBridgePaths
from .native_command_transports import native_transport_for
from .session_protocol import FORMAT,encode_frame,read_frame


def _canonical(value):
    _finite_json(value)
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


class HardwareSessionBridge:
    def __init__(self,binding,control,evidence_directory,*,runtime_secrets=None,transport_factory=None):
        self.binding,self.control=binding,control
        self.evidence_directory=Path(evidence_directory)
        self._secrets=deepcopy(runtime_secrets)
        self._factory=transport_factory or native_transport_for
        self.gateway=self.writer=self.watchdog=None
        self._listener=self._temporary=self._server=self._renewer=None
        self._clients=set();self._decisions={}
        self._mutex=threading.RLock();self._submit_mutex=threading.RLock();self._close_mutex=threading.Lock()
        self._stop=threading.Event()
        self._started=self._closing=self._closed=False
        self._lease=None;self._descriptor=None;self._outcome=None;self._errors=[]

    @property
    def descriptor(self):
        if self._descriptor is None:raise RuntimeError('hardware session has not started')
        return deepcopy(self._descriptor)

    def _record(self,kind,**fields):
        row={'kind':kind,'session_id':self.control.identity.owner,
             'wall_time':time.time(),'monotonic':time.monotonic(),**fields}
        try:self.writer.write_record(row)
        except Exception as error:
            self._errors.append({'phase':'parent_evidence','type':type(error).__name__})
            self.control.revoke('parent_evidence_write_failed')
            raise

    def _manifest(self):
        value={'format':'tinystudio.hardware-session-parent.v1','identity':self.control.identity.as_dict(),
            'session_id':self.control.identity.owner,'binding':self.binding.public_description(),
            'authority':self.control.snapshot(),'gateway_lease':deepcopy(self._lease),
            'evidence':self.writer.receipt() if self.writer is not None else None,
            'errors':deepcopy(self._errors),'outcome':deepcopy(self._outcome),
            'qualification':{'execution':'trusted_native_not_isolated','physical_outcome':'unverified',
                'credential_scope':'operator_credentials_held_by_parent; per-attempt_IPC_capability_in_child',
                'final_authority':'composed_parent_and_gateway_callback; builtins_check_before_writes; custom_adapters_are_operator_contracts',
                'deadline_scope':'cooperative_between_native_calls; not_a_hard_real_time_guarantee'}}
        temporary=self.paths.manifest.with_suffix('.json.pending')
        with temporary.open('wb') as stream:
            stream.write(_canonical(value)+b'\n');stream.flush();os.fsync(stream.fileno())
        temporary.replace(self.paths.manifest)
        return value

    def start(self):
        if self._started or self._closing:raise RuntimeError('hardware session cannot be restarted')
        self.control.check()
        root=self.binding.state_directory
        info=root.stat()
        if not root.is_dir() or stat.S_IMODE(info.st_mode)!=0o700 or info.st_uid!=os.geteuid():
            raise ValueError('session state directory must be an existing operator-owned mode 0700 directory')
        self.evidence_directory.mkdir(parents=True,exist_ok=False)
        self._temporary=tempfile.TemporaryDirectory(prefix='tinystudio-session-')
        self.paths=SessionBridgePaths(self.evidence_directory,Path(self._temporary.name))
        self.writer=JsonlEvidenceWriter(self.paths.decisions,self.binding.evidence).__enter__()
        self._started=True
        try:
            self._record('session_opening',identity=self.control.identity.as_dict(),binding=self.binding.public_description())
            inner=self._factory(self.binding.gateway_binding,runtime_secrets=deepcopy(self._secrets))
            self.gateway=CommandGateway(GatewayStoragePaths.in_directory(root).journal,self.binding.gateway_binding,
                                        AttemptBoundTransport(inner,self.control))
            self._lease=self.gateway.acquire(self.control.identity.owner,ttl=min(self.binding.lease_seconds,self.control.remaining()))
            self._record('lease_acquired',lease=self._lease)
            self.watchdog=GatewayWatchdog(self.gateway).start()
            self._listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            self._listener.bind(str(self.paths.listener));os.chmod(self.paths.listener,0o600);self._listener.listen()
            self._descriptor={'format':FORMAT,'path':str(self.paths.listener),'capability':secrets.token_urlsafe(32),
                              'session_id':self.control.identity.owner,'profile':self.binding.profile}
            self._server=threading.Thread(target=self._serve,name='tinystudio-session-ipc',daemon=True)
            self._renewer=threading.Thread(target=self._renew,name='tinystudio-session-lease',daemon=True)
            self._server.start();self._renewer.start();self._manifest()
        except BaseException as error:
            try:self.close()
            except BaseException as cleanup:error.add_note('Parent session cleanup unconfirmed: '+type(cleanup).__name__)
            raise
        return self

    def _cancelled(self):
        try:self.control.check()
        except RuntimeError:return True
        return self._stop.is_set()

    def submit(self,decision_id,payload,*,valid_for_seconds):
        if type(decision_id) is not str or not decision_id or '\0' in decision_id:
            raise ValueError('decision_id must be an explicit nonempty string')
        if type(valid_for_seconds) not in {int,float} or not math.isfinite(valid_for_seconds) or valid_for_seconds<=0:
            raise ValueError('decision validity must be positive and finite')
        decision={'decision_id':decision_id,'payload':payload,'valid_for_seconds':valid_for_seconds}
        fingerprint=hashlib.sha256(_canonical(decision)).hexdigest()
        with self._submit_mutex:
            existing=self._decisions.get(decision_id)
            if existing is not None:
                if existing['fingerprint']!=fingerprint:raise ValueError('decision identity belongs to a different immutable proposal')
                if existing.get('receipt') is not None:
                    if not self._closed:self._record('receipt',decision_id=decision_id,receipt=existing['receipt'],redelivery=True)
                    return deepcopy(existing['receipt'])
                request=existing['request']
            else:
                if not self._started or self._closing:raise RuntimeError('hardware session is not accepting proposals')
                # Retain the exact submitted decision before authority/payload
                # admission, including rejected finite JSON proposals.
                self._record('proposal',decision=decision,proposal_sha256=fingerprint)
                self.control.check()
                self.binding.native_profile.validate_payload(payload)
                now=time.time()
                with self._mutex:lease=deepcopy(self._lease)
                expires=min(now+valid_for_seconds,lease['expires_at'],now+self.control.remaining())
                command_id=hashlib.sha256(_canonical(['hardware.session.command',self.control.identity.owner,decision_id])).hexdigest()
                request={'command_id':command_id,'owner':lease['owner'],'epoch':lease['epoch'],
                         'payload':deepcopy(payload),'expires_at':expires}
                self._record('admitted',decision_id=decision_id,gateway_request=request)
                existing={'fingerprint':fingerprint,'request':request};self._decisions[decision_id]=existing
            try:
                receipt=self.gateway.dispatch(**request,cancelled=self._cancelled)
            except Exception as error:
                try:self._record('refused_or_uncertain',decision_id=decision_id,error_type=type(error).__name__,
                    delivery_receipt=getattr(error,'delivery_receipt',None),lifecycle_receipt=getattr(error,'lifecycle_receipt',None))
                except Exception as evidence:error.add_note('Parent failure evidence unconfirmed: '+type(evidence).__name__)
                raise
            existing['receipt']=deepcopy(receipt)
            try:self._record('receipt',decision_id=decision_id,receipt=receipt,redelivery=False)
            except Exception as error:
                error.delivery_receipt=deepcopy(receipt)
                raise
            return deepcopy(receipt)

    def _serve(self):
        while not self._stop.is_set():
            try:connection,_=self._listener.accept()
            except OSError:
                if self._stop.is_set():return
                self.control.revoke('session_listener_failed');return
            with self._mutex:
                # Close publishes its stop request before snapshotting clients.
                # An accept returning after that snapshot must not start a reader.
                if self._stop.is_set():
                    connection.close();return
                self._clients.add(connection)
            try:
                try:
                    message=read_frame(connection,maximum=self.binding.max_request_bytes)
                    if set(message)!={'capability','decision_id','payload','valid_for_seconds'}:
                        raise ValueError('session accepts only scoped decision proposals')
                    capability=message['capability']
                    if type(capability) is not str or not hmac.compare_digest(capability.encode(),self._descriptor['capability'].encode()):
                        raise PermissionError('invalid session capability')
                    receipt=self.submit(message['decision_id'],message['payload'],valid_for_seconds=message['valid_for_seconds'])
                    response={'ok':True,'receipt':receipt}
                except Exception as error:
                    detail={'type':type(error).__name__,'message':str(error)}
                    for key in ('delivery_receipt','lifecycle_receipt'):
                        if getattr(error,key,None) is not None:detail[key]=getattr(error,key)
                    response={'ok':False,'error':detail}
                connection.sendall(encode_frame(response,maximum=self.binding.max_response_bytes))
            except Exception:pass  # The parent ledger/gateway retain any admitted result.
            finally:
                connection.close()
                with self._mutex:self._clients.discard(connection)

    def _renew(self):
        deadline=time.monotonic()+self.binding.renew_interval_seconds
        while not self._stop.is_set():
            remaining=max(0,deadline-time.monotonic())
            if self._stop.wait(min(remaining,threading.TIMEOUT_MAX)):return
            if time.monotonic()<deadline:continue
            try:
                self.control.check()
                with self._mutex:lease=deepcopy(self._lease)
                expiry=self.gateway.heartbeat(lease['owner'],lease['epoch'],ttl=min(self.binding.lease_seconds,self.control.remaining()))
                with self._mutex:self._lease={**lease,'expires_at':expiry}
            except Exception as error:
                self._errors.append({'phase':'gateway_lease_renewal','type':type(error).__name__})
                self.control.revoke('gateway_lease_renewal_failed');return
            deadline=time.monotonic()+self.binding.renew_interval_seconds

    def close(self):
        with self._close_mutex:
            if self._closed:return deepcopy(self._outcome)
            self._closing=True;self.control.revoke('parent_session_closed');self._stop.set()
            with self._mutex:connections=list(self._clients)
            for connection in connections:
                try:connection.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            if self._listener is not None:
                try:self._listener.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                self._listener.close()
            for thread in (self._server,self._renewer):
                if thread is not None and thread.ident is not None:thread.join()
            self._outcome={'state':'closed','physical_outcome':'unverified','release_receipt':None,'errors':[]}
            if self.watchdog is not None:self.watchdog.close()
            if self.gateway is not None:
                try:
                    state=self.gateway.snapshot()
                    if (self._lease is not None and state['state']=='owned' and state['owner']==self._lease['owner']
                            and state['epoch']==self._lease['epoch']):
                        release_id=hashlib.sha256(_canonical(['hardware.session.release',self.control.identity.owner])).hexdigest()
                        self._outcome['release_receipt']=self.gateway.release(release_id,
                            owner=self._lease['owner'],epoch=self._lease['epoch'])
                    else:self._outcome['lease_actions']=self.gateway.watchdog_tick()
                except Exception as error:
                    self._outcome['errors'].append({'type':type(error).__name__,
                        'delivery_receipt':getattr(error,'delivery_receipt',None),
                        'lifecycle_receipt':getattr(error,'lifecycle_receipt',None)})
                finally:self.gateway.close()
            failure=None
            if self.writer is not None:
                try:self._record('session_closed',outcome=self._outcome)
                except Exception as error:failure=error
                try:self.writer.close()
                except Exception as error:
                    if failure is None:failure=error
                    else:failure.add_note('Parent evidence close failed: '+type(error).__name__)
                try:self._manifest()
                except Exception as error:
                    if failure is None:failure=error
                    else:failure.add_note('Parent manifest update failed: '+type(error).__name__)
            if self._temporary is not None:self._temporary.cleanup()
            self._secrets=None;self._closed=True
            if failure is not None:raise failure
            return deepcopy(self._outcome)

    @property
    def cleanup_confirmed(self):
        threads=all(thread is None or not thread.is_alive() for thread in (self._server,self._renewer))
        watchdog=self.watchdog is None or not self.watchdog.snapshot()['alive']
        gateway=self.gateway is None or self.gateway._closed
        writer=self.writer is None or self.writer._closed
        return threads and watchdog and gateway and writer and (self._listener is None or self._listener.fileno()==-1)

    def retain_artifacts(self,destination,*,visibility='evaluation'):
        if not self.cleanup_confirmed:raise RuntimeError('parent session cleanup is unconfirmed; evidence cannot be frozen')
        if visibility not in {'model','evaluation','truth'}:raise ValueError('invalid authored artifact visibility')
        from .engine import digest
        destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
        artifacts=[]
        for source in sorted(self.evidence_directory.iterdir()):
            if source.is_symlink() or not source.is_file():raise ValueError('parent session evidence must contain regular files')
            checksum=digest(source)
            exported=self.paths.exported(self.control.identity.owner,source)
            if source.resolve().is_relative_to(destination.resolve()):
                target=source
            else:
                target=destination/exported
                if target.exists():raise ValueError('parent session artifact name collides with an existing output')
                shutil.copyfile(source,target)
            if digest(target)!=checksum or digest(source)!=checksum:raise ValueError('parent session evidence changed while freezing')
            target.chmod(0o444)
            artifacts.append({'name':exported,'path':str(target),'sha256':checksum,'role':'hardware_session_parent',
                              'media_type':'application/x-ndjson' if source.suffix=='.jsonl' else 'application/json',
                              'visibility':visibility})
        return artifacts
