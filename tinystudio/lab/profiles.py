"""Executable isolation plans and ownership-fenced backend lifecycles.

Plans declare controls; only backend denial fixtures qualify enforcement. Native
isolation uses macOS's deprecated sandbox-exec facility and never falls back to
an unrestricted process. Docker/Kubernetes require an operator-provisioned pinned
image and target configuration. This module does not obtain credentials.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import time
from typing import Callable


FORMAT = "tinystudio.execution-profile.v1"
_LABEL = "tinystudio.owner"
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?\Z")


@dataclass(frozen=True)
class Limits:
    cpu: float = 1
    memory_bytes: int = 512 * 1024**2
    pids: int = 64
    wall_seconds: int = 300

    def validate(self):
        if isinstance(self.cpu, bool) or not isinstance(self.cpu, (int, float)) or not math.isfinite(self.cpu) or not 0 < self.cpu <= 64:
            raise ValueError("CPU limit must be finite and between zero and 64")
        for name, value, lower, upper in (("memory", self.memory_bytes, 16*1024**2, 64*1024**3),
                                           ("pids", self.pids, 1, 4096), ("wall time", self.wall_seconds, 1, 86400)):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"invalid bounded {name} limit")


@dataclass(frozen=True)
class RunnerGrants:
    """Construct only from authenticated runner/operator configuration, never a definition."""
    network_endpoints: tuple[tuple[str, int], ...] = ()
    docker_network: str | None = None
    read_secrets: tuple[Path, ...] = ()
    devices: tuple[Path, ...] = ()
    kubernetes_secrets: tuple[str, ...] = ()
    network_policy_enforced: bool = False
    kubernetes_pids_limit: int | None = None

    def validate(self):
        if any(not isinstance(value, tuple) for value in (self.network_endpoints, self.read_secrets, self.devices, self.kubernetes_secrets)):
            raise ValueError("runner grants must use immutable tuple declarations")
        for address, port in self.network_endpoints:
            ipaddress.ip_address(address)
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("network grants require exact IP/port endpoints")
        if self.docker_network is not None and (not _NAME.fullmatch(self.docker_network) or self.docker_network in {"host", "container", "bridge", "none"}):
            raise ValueError("Docker grants require an explicit operator-managed isolated network name")
        for path in (*self.read_secrets, *self.devices):
            if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink() or not path.exists() or any(char in str(path) for char in (",", "\n", "\r", "\0")):
                raise ValueError("secret/device grants require existing absolute non-symlink paths")
        if any(not path.is_file() for path in self.read_secrets):
            raise ValueError("secret grants must name individual files")
        if any(not (path.is_char_device() or path.is_block_device()) for path in self.devices):
            raise ValueError("device grants must name actual devices")
        if type(self.network_policy_enforced) is not bool or (self.kubernetes_pids_limit is not None and (type(self.kubernetes_pids_limit) is not int or not 1 <= self.kubernetes_pids_limit <= 4096)):
            raise ValueError("invalid trusted CNI/PID enforcement attestation")
        if any(not _NAME.fullmatch(name) for name in self.kubernetes_secrets):
            raise ValueError("invalid Kubernetes Secret name")


@dataclass(frozen=True)
class ProfileRequest:
    backend: str
    attempt_id: str
    attempt_root: Path
    source_root: Path
    inputs_root: Path
    output_root: Path
    command: tuple[str, ...]
    expected_os: str
    limits: Limits = field(default_factory=Limits)
    runtime_read_paths: tuple[Path, ...] = ()
    image: str | None = None
    namespace: str = "tinystudio"
    kube_context: str | None = None
    kubeconfig: Path | None = None
    storage_claim: str | None = None
    require_hard_resources: bool = False


@dataclass(frozen=True)
class LaunchPlan:
    backend: str
    owner: str
    name: str
    request: ProfileRequest
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    profile_text: str | None = None
    manifests_json: str | None = None
    limitations: tuple[str, ...] = ()
    required_attestations: tuple[str, ...] = ()
    grants_json: str = "{}"

    def manifest(self):
        return {"format": FORMAT, "backend": self.backend, "owner": self.owner,
                "qualification": "unqualified_until_denial_fixture", "limits": dict(self.request.limits.__dict__),
                "limitations": list(self.limitations), "required_attestations": list(self.required_attestations),
                "image": self.request.image, "target_os": self.request.expected_os,
                "network_default": "deny", "grants": json.loads(self.grants_json), "filesystem": {"read_source": str(self.request.source_root), "read_inputs": str(self.request.inputs_root), "write_root": str(self.request.output_root), "runtime_read_paths": [str(path) for path in self.request.runtime_read_paths]}, "service_account_token": "disabled" if self.backend == "kubernetes" else "not_applicable"}


def _path(path: Path, *, root: Path | None = None) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or path.is_symlink() or not path.exists():
        raise ValueError("profile paths must be existing absolute non-symlink paths")
    if any(character in str(path) for character in (",", "\n", "\r", "\0")):
        raise ValueError("profile paths contain unsupported control/CSV characters")
    resolved = path.resolve()
    if root is not None and (not resolved.is_relative_to(root) or resolved == root):
        raise ValueError("source/input/output paths must be distinct attempt-owned descendants")
    return resolved


def _literal(value: str) -> str:
    if "\n" in value or "\r" in value or "\0" in value:
        raise ValueError("profile literals cannot contain control characters")
    return json.dumps(value)


def _pinned(image: str | None):
    if not isinstance(image, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:+-]*@sha256:[0-9a-f]{64}", image):
        raise ValueError("container execution requires an immutable image@sha256 digest")


def build_profile(request: ProfileRequest, grants: RunnerGrants | None = None, *, system: str | None = None) -> LaunchPlan:
    """Build a plan without starting a process, container, cluster Job or network call."""
    if not isinstance(request, ProfileRequest) or (grants is not None and not isinstance(grants, RunnerGrants)):
        raise ValueError("typed runner-owned profile configuration is required")
    grants = grants or RunnerGrants()
    grants.validate(); request.limits.validate()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request.attempt_id):
        raise ValueError("invalid attempt identity")
    if not isinstance(request.command, tuple) or not request.command or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in request.command):
        raise ValueError("profile command must be an explicit nonempty argv")
    root = _path(request.attempt_root)
    source, inputs, output = (_path(path, root=root) for path in (request.source_root, request.inputs_root, request.output_root))
    if any(not path.is_dir() for path in (root,source,inputs,output)):
        raise ValueError("attempt/source/input/output roots must be directories")
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a) for a,b in ((source,inputs),(source,output),(inputs,output))):
        raise ValueError("source, inputs and output roots must not overlap")
    public_grants = json.dumps({"network_endpoints": grants.network_endpoints, "docker_network": grants.docker_network,
                               "secret_file_count": len(grants.read_secrets), "kubernetes_secret_count": len(grants.kubernetes_secrets),
                               "device_count": len(grants.devices), "network_policy_attested": grants.network_policy_enforced,
                               "kubernetes_pids_limit": grants.kubernetes_pids_limit})
    owner = secrets.token_hex(16)
    name = "tinystudio-" + owner
    environment = (("TINYSTUDIO_SKIP_DOTENV","1"),("PYTHONDONTWRITEBYTECODE","1"),("PYTHONNOUSERSITE","1"),
                   ("PYTHONPATH",str(source.parent)),("HOME",str(output)),("TMPDIR",str(output)))
    if request.backend == "native":
        if (system or platform.system()) != "Darwin" or request.expected_os != "Darwin":
            raise ValueError("isolated-native requires a matching macOS target")
        executable = shutil.which("sandbox-exec")
        if executable is None:
            raise ValueError("macOS sandbox-exec is unavailable; unrestricted fallback is prohibited")
        if request.require_hard_resources:
            raise ValueError("sandbox-exec cannot enforce per-attempt CPU, memory and PID limits")
        if grants.docker_network or grants.kubernetes_secrets:
            raise ValueError("grants do not match the native backend")
        reads = {source,source.parent,inputs,output,Path('/System/Library'),Path('/usr/lib'),Path('/dev/null'),Path('/dev/urandom'),Path('/dev/random')}
        reads.update(_path(path) for path in request.runtime_read_paths)
        reads.update(path.resolve() for path in grants.read_secrets)
        reads.update(path.resolve() for path in grants.devices)
        command_executable = _path(Path(request.command[0]).resolve())
        reads.add(command_executable)
        # Preserve venv interpreter spelling while granting only its selected
        # runtime target. macOS also checks intermediate symlinks during exec.
        pending = [Path(request.command[0])]
        visited = set()
        for _ in range(32):
            if not pending:
                break
            candidate = pending.pop()
            if candidate in visited:
                continue
            visited.add(candidate)
            reads.add(candidate)
            for component in (candidate, *candidate.parents):
                if component.is_symlink():
                    resolved = component.resolve()
                    if not any(resolved == allowed or resolved.is_relative_to(allowed) for allowed in reads if allowed.is_dir()):
                        raise ValueError("interpreter symlink escapes explicitly granted runtime roots")
                    reads.add(component)
                    target = component.readlink()
                    pending.append(target if target.is_absolute() else component.parent / target)
        if pending:
            raise ValueError("interpreter symlink chain is too deep")
        ancestors = {parent for path in reads for parent in path.parents}
        statements = ['(version 1)','(deny default)','(allow process-exec)','(allow process-fork)',
                      '(allow signal (target self))','(allow sysctl-read)', '(allow file-read* (literal "/"))']
        statements.append('(allow file-read-metadata ' + ' '.join(f'(literal {_literal(str(path))})' for path in sorted(ancestors)) + ')')
        statements.append('(allow file-read* ' + ' '.join(f'({"subpath" if path.is_dir() else "literal"} {_literal(str(path))})' for path in sorted(reads)) + ')')
        statements.append(f'(allow file-write* (subpath {_literal(str(output))}))')
        statements.append('(allow file-write-data (literal "/dev/null"))')
        for device in grants.devices:
            statements.append(f'(allow file-write* (literal {_literal(str(device.resolve()))}))')
        for address, port in grants.network_endpoints:
            if address not in {"127.0.0.1", "::1"}:
                raise ValueError("native sandbox endpoint grants support localhost TCP only; arbitrary IP grants are not broadened")
            statements.append(f'(allow network-outbound (remote tcp {_literal(f"localhost:{port}")}))')
        profile = '\n'.join(statements)+'\n'
        return LaunchPlan('native',owner,name,request,(executable,'-p',profile,*request.command),environment,profile_text=profile,grants_json=public_grants,
                          limitations=('sandbox-exec is deprecated by Apple', 'CPU/memory/PID limits require a container backend; wall time is enforced by lifecycle owner', 'root directory entries and filesystem metadata for allowlist ancestors are readable', 'native network grants are localhost TCP ports across loopback address families only'))
    if request.expected_os != "Linux":
        raise ValueError("container/Kubernetes images require an explicitly matching Linux runtime specification")
    _pinned(request.image)
    if request.runtime_read_paths:
        raise ValueError("host runtime mounts are not allowed in immutable container images")
    if request.backend == 'container':
        if grants.network_endpoints or grants.kubernetes_secrets:
            raise ValueError("Docker endpoint ACLs require a separately qualified managed network; endpoint grants are not silently broadened")
        argv=['docker','create','--pull=never','--name',name,'--label',f'{_LABEL}={owner}','--label',f'tinystudio.attempt={request.attempt_id}',
              '--read-only','--log-driver','local','--log-opt','max-size=16m','--log-opt','max-file=1','--security-opt','no-new-privileges=true','--cap-drop','ALL','--pids-limit',str(request.limits.pids),
              '--cpus',str(request.limits.cpu),'--memory',str(request.limits.memory_bytes),'--memory-swap',str(request.limits.memory_bytes),
              '--network',grants.docker_network or 'none','--user',f'{max(1,os.getuid())}:{max(1,os.getgid())}',
              '--mount',f'type=bind,src={root},dst={root},readonly', '--mount',f'type=bind,src={output},dst={output}', '--workdir',str(output)]
        for path in grants.read_secrets:
            argv.extend(['--mount',f'type=bind,src={path.resolve()},dst={path.resolve()},readonly'])
        for path in grants.devices:
            argv.extend(['--device',f'{path.resolve()}:{path.resolve()}:rw'])
        for key,value in environment:
            argv.extend(['--env',f'{key}={value}'])
        argv.extend([request.image,*request.command])
        return LaunchPlan('container',owner,name,request,tuple(argv),environment,grants_json=public_grants,
                          limitations=('host bind output quota requires the runner output-byte monitor', 'named-network endpoint isolation requires separate network qualification') if grants.docker_network else ('host bind output quota requires the runner output-byte monitor',))
    if request.backend != 'kubernetes':
        raise ValueError("unsupported isolation backend; no fallback is permitted")
    if grants.docker_network or grants.read_secrets or grants.devices:
        raise ValueError("host secrets and devices are not Kubernetes grants")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", request.namespace) or len(request.attempt_id)>63 or not request.kube_context or not request.storage_claim or not _NAME.fullmatch(request.storage_claim):
        raise ValueError("Kubernetes requires an explicit namespace/context and provisioned attempt storage claim")
    if request.kubeconfig is None:
        raise ValueError("Kubernetes requires an explicit runner-owned kubeconfig path")
    _path(request.kubeconfig)
    attestations=[]
    if not grants.network_policy_enforced:attestations.append('CNI NetworkPolicy enforcement')
    if request.require_hard_resources and (grants.kubernetes_pids_limit is None or grants.kubernetes_pids_limit > request.limits.pids):
        raise ValueError("Kubernetes per-pod PID limits require an independently attested node policy")
    labels={_LABEL:owner,'tinystudio.attempt':request.attempt_id}
    mounts=[{'name':'attempt','mountPath':str(root),'subPath':root.name,'readOnly':True},
            {'name':'attempt','mountPath':str(output),'subPath':root.name+'/'+output.relative_to(root).as_posix(),'readOnly':False}]
    volumes=[{'name':'attempt','persistentVolumeClaim':{'claimName':request.storage_claim}}]
    for index, secret in enumerate(grants.kubernetes_secrets):
        name_='secret-'+str(index);volumes.append({'name':name_,'secret':{'secretName':secret,'defaultMode':0o400}})
        mounts.append({'name':name_,'mountPath':'/run/secrets/'+secret,'readOnly':True})
    pod={'restartPolicy':'Never','automountServiceAccountToken':False,'enableServiceLinks':False,
         'hostNetwork':False,'hostPID':False,'hostIPC':False,'terminationGracePeriodSeconds':10,
         'securityContext':{'runAsNonRoot':True,'runAsUser':1000,'runAsGroup':1000,'fsGroup':1000,'seccompProfile':{'type':'RuntimeDefault'}},
         'containers':[{'name':'execution','image':request.image,'imagePullPolicy':'IfNotPresent','command':list(request.command),'workingDir':str(output),
                        'env':[{'name':key,'value':value} for key,value in environment], 'volumeMounts':mounts,
                        'resources':{'requests':{'cpu':str(request.limits.cpu),'memory':str(request.limits.memory_bytes)},'limits':{'cpu':str(request.limits.cpu),'memory':str(request.limits.memory_bytes)}},
                        'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}}], 'volumes':volumes}
    job={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':name,'namespace':request.namespace,'labels':labels},
         'spec':{'backoffLimit':0,'activeDeadlineSeconds':request.limits.wall_seconds,'ttlSecondsAfterFinished':3600,
                 'template':{'metadata':{'labels':labels},'spec':pod}}}
    egress=[]
    for address,port in grants.network_endpoints:
        ip=ipaddress.ip_address(address)
        egress.append({'to':[{'ipBlock':{'cidr':str(ip)+('/32' if ip.version==4 else '/128')}}],'ports':[{'protocol':'TCP','port':port}]})
    policy={'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':name,'namespace':request.namespace,'labels':labels},
            'spec':{'podSelector':{'matchLabels':{_LABEL:owner}},'policyTypes':['Ingress','Egress'],'ingress':[],'egress':egress}}
    argv=('kubectl','--kubeconfig',str(request.kubeconfig),'--context',request.kube_context,'--namespace',request.namespace)
    return LaunchPlan('kubernetes',owner,name,request,argv,environment,manifests_json=json.dumps({'items':[policy,job]}),
                      grants_json=public_grants, limitations=('per-pod PID enforcement is a node-policy prerequisite', 'storage quota/output-byte enforcement requires provisioned PVC and runner monitor', 'API deletion does not alone prove node process termination'), required_attestations=tuple(attestations))


class ProfileExecution:
    """Own process/container/Job identity and refuse uncertain cleanup success."""
    def __init__(self, plan: LaunchPlan, *, command_runner: Callable | None = None, termination_probe: Callable[[str], bool] | None = None):
        self.plan=plan;self._run=command_runner or subprocess.run
        self.termination_probe=termination_probe
        self.group=None;self.identity=None;self.closed=False;self.created=[]
        self.started_at=None
        self.creation_uncertain=False
        self.pending_kind=None
        self.deleted=set()

    def _command(self,args,*,data=None,check=True):
        result=self._run(list(args),input=data,text=True,capture_output=True,timeout=30,check=False)
        if check and result.returncode:
            raise RuntimeError('profile backend command failed: '+result.stderr[-2048:])
        return result

    def _kube(self,*args,data=None,check=True):
        return self._command([*self.plan.argv,*args],data=data,check=check)

    def start(self,*,stdout=None,stderr=None):
        try:
            return self._start(stdout=stdout,stderr=stderr)
        except Exception as cause:
            try:
                self.close()
            except Exception as cleanup:
                raise RuntimeError(f"profile start failed: {cause}; cleanup unconfirmed: {cleanup}") from cause
            raise

    def _start(self,*,stdout=None,stderr=None):
        if self.closed or self.started_at is not None:raise RuntimeError('profile execution already started or closed')
        if self.plan.required_attestations:raise RuntimeError('unqualified backend prerequisites: '+', '.join(self.plan.required_attestations))
        self.started_at=time.monotonic()
        if self.plan.backend=='native':
            from .runner import ProcessGroup
            if platform.system() != self.plan.request.expected_os:
                raise RuntimeError('native target OS changed before launch')
            env={key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL'}}
            env.update(dict(self.plan.environment))
            self.group=ProcessGroup(list(self.plan.argv),env=env,cwd=self.plan.request.output_root,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
            self.identity=str(self.group.pgid)
        elif self.plan.backend=='container':
            self.creation_uncertain=True
            result=self._command(self.plan.argv)
            identifier=result.stdout.strip()
            if not re.fullmatch('[0-9a-f]{64}',identifier):raise RuntimeError('container create did not return an immutable identity')
            self.identity=identifier
            self.creation_uncertain=False
            self._container_identity()
            self._command(['docker','start',self.identity])
        else:
            for item in json.loads(self.plan.manifests_json)['items']:
                self.pending_kind=item['kind']
                result=self._kube('create','-f','-','-o','json',data=json.dumps(item))
                resource=json.loads(result.stdout)
                if resource['metadata'].get('labels',{}).get(_LABEL)!=self.plan.owner or not resource['metadata'].get('uid'):
                    raise RuntimeError('created Kubernetes resource identity mismatch')
                if resource.get('kind') != item['kind'] or resource['metadata'].get('name') != self.plan.name or resource['metadata'].get('namespace') != self.plan.request.namespace:
                    raise RuntimeError('created Kubernetes resource scope mismatch')
                self.created.append((resource['kind'],resource['metadata']['uid']))
                self.pending_kind=None
            self.identity=self.created[-1][1]
        return self

    def _container_identity(self, *, allow_absent=False):
        result=self._command(['docker','inspect',self.identity or self.plan.name],check=False)
        if result.returncode:
            absent=any(text in result.stderr.lower() for text in ('no such object','no such container'))
            if absent and allow_absent:return None
            raise RuntimeError('container identity/absence could not be verified: '+result.stderr[-2048:])
        records=json.loads(result.stdout)
        if not isinstance(records,list) or len(records)!=1:raise RuntimeError('ambiguous container identity')
        data=records[0]
        identifier=data.get('Id')
        if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{64}',identifier) or (self.identity is not None and identifier!=self.identity) or data.get('Config',{}).get('Labels',{}).get(_LABEL)!=self.plan.owner or data.get('Config',{}).get('Image')!=self.plan.request.image:
            raise RuntimeError('container ownership/image identity mismatch; refusing to operate')
        self.identity=identifier
        return data

    def poll(self):
        if self.started_at is None:raise RuntimeError('profile execution was not started')
        if time.monotonic()-self.started_at>self.plan.request.limits.wall_seconds:
            self.close();raise TimeoutError('profile exceeded wall-time budget')
        if self.plan.backend=='native':return self.group.process.poll()
        if self.plan.backend=='container':
            data=self._container_identity()['State']
            return None if data.get('Running') else data.get('ExitCode')
        result=self._kube('get','job',self.plan.name,'-o','json')
        data=json.loads(result.stdout)
        if data['metadata']['uid']!=self.identity or data['metadata'].get('labels',{}).get(_LABEL)!=self.plan.owner:raise RuntimeError('Job identity changed')
        status=data.get('status',{})
        return 0 if status.get('succeeded') else 1 if status.get('failed') else None

    def logs(self):
        """Read bounded logs only from this execution's positively owned backend."""
        if self.plan.backend == "native":
            return "", ""
        if self.plan.backend == "container":
            self._container_identity()
            result = self._command(['docker','logs','--tail','1000',self.identity])
        else:
            response = self._kube('get','job',self.plan.name,'-o','json')
            job = json.loads(response.stdout)
            if job['metadata'].get('uid') != self.identity or job['metadata'].get('labels',{}).get(_LABEL) != self.plan.owner:
                raise RuntimeError('refusing logs from a changed Job identity')
            result = self._kube('logs','--selector',f'{_LABEL}={self.plan.owner}','--all-containers=true','--tail=1000','--limit-bytes=8192')
        return result.stdout[-8192:], result.stderr[-8192:]

    def close(self):
        if self.closed:return
        if self.plan.backend=='native':
            if self.group:self.group.close()
        elif self.plan.backend=='container' and (self.identity or self.creation_uncertain):
            if self._container_identity(allow_absent=True) is None:
                self.closed=True;return
            self._command(['docker','stop','--time','10',self.identity])
            self._command(['docker','rm','--force',self.identity])
            response=self._command(['docker','inspect',self.identity],check=False)
            if response.returncode==0 or not any(text in response.stderr.lower() for text in ('no such object','no such container')):
                raise RuntimeError('container cleanup is unconfirmed')
        elif self.plan.backend=='kubernetes':
            if self.pending_kind:
                result=self._kube('get','jobs' if self.pending_kind == 'Job' else 'networkpolicies',self.plan.name,'-o','json',check=False)
                if result.returncode:
                    if 'notfound' not in result.stderr.lower() and 'not found' not in result.stderr.lower():
                        raise RuntimeError('uncertain Job/resource creation could not be reconciled')
                else:
                    data=json.loads(result.stdout)
                    if data['metadata'].get('labels',{}).get(_LABEL)!=self.plan.owner or not data['metadata'].get('uid'):
                        raise RuntimeError('uncertain resource ownership mismatch')
                    self.created.append((self.pending_kind,data['metadata']['uid']))
                    if self.pending_kind=='Job':self.identity=data['metadata']['uid']
                self.pending_kind=None
            for kind,uid in reversed(self.created):
                if (kind,uid) in self.deleted:continue
                resource='jobs' if kind=='Job' else 'networkpolicies'
                api='batch/v1' if kind=='Job' else 'networking.k8s.io/v1'
                uri=f'/apis/{api}/namespaces/{self.plan.request.namespace}/{resource}/{self.plan.name}'
                options={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':uid},'propagationPolicy':'Foreground','gracePeriodSeconds':10}
                self._kube('delete','--raw',uri,'-f','-',data=json.dumps(options))
                self._kube('wait','--for=delete',resource+'/'+self.plan.name,'--timeout=30s')
                self.deleted.add((kind,uid))
            # A partitioned kubelet can outlive API deletion. Caller must supply
            # actual node termination evidence rather than treating deletion as proof.
            if any(kind == "Job" for kind,_ in self.created) and (self.termination_probe is None or not self.termination_probe(self.identity)):
                raise RuntimeError('Job deleted; node process termination evidence remains unqualified')
        self.closed=True
