"""Private Native publication journals and authenticated protocol selection.

These records support explicit publication recovery, never execution replay.
Only selected source bytes and public identities are retained; signed URLs and
application/attempt credentials are used in memory and are never journaled.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urlsplit

import httpx

from .contracts import checksum
from .finalization_configuration import FINALIZATION_PROTOCOL_VERSION, FinalizationApiPaths
from .transfer_client import ArtifactTransferError, ResumableTransferClient, TransferDataClient
from .transfer_configuration import AttemptArtifactApiPaths, TransferBudget, TransferPolicy, MultipartGeometry


class PublicationUnconfirmed(RuntimeError):
    """The exact request and available source bytes remain available locally."""


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False)


def _pairs(items):
    result={}
    for key,value in items:
        if key in result:
            raise ValueError('duplicate private publication field')
        result[key]=value
    return result


def _finite(value):
    raise ValueError('nonfinite private publication value')


def _read(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'r',encoding='utf-8') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid()
                or stat.S_IMODE(info.st_mode)!=0o600):
            raise ValueError('private publication journal must be an owned mode-0600 regular file')
        return json.load(stream,object_pairs_hook=_pairs,parse_constant=_finite)


@dataclass(frozen=True)
class RunnerPublicationPaths:
    root: Path

    @property
    def directory(self):
        return Path(self.root)/'publication'

    @property
    def protocol(self):
        return self.directory/'protocol.json'

    @property
    def execution(self):
        return self.directory/'execution.json'

    @property
    def finalization(self):
        return self.directory/'finalization.json'

    @property
    def finalization_status(self):
        return self.directory/'finalization-status.json'

    @property
    def receipts(self):
        return self.directory/'artifact-receipts.json'

    @property
    def cleanup(self):
        return self.directory/'cleanup.json'

    def transfer(self,index):
        if type(index) is not int or index<0:
            raise ValueError('artifact index must be a nonnegative integer')
        return self.directory/('artifact-'+str(index)+'.transfer.json')

    @property
    def lock(self):
        return self.directory/'publication.lock'


def _destination(runner):
    # Include a selected deployment path, not just its host. Test transports
    # without base_url still bind to the explicitly selected Runner server.
    value=str(getattr(runner.client,'base_url',runner.server))
    parsed=urlsplit(value)
    if (parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError('publication HTTP destination must be credential-free')
    port=parsed.port or (443 if parsed.scheme=='https' else 80)
    return [parsed.scheme,parsed.hostname,port,parsed.path.rstrip('/')]


def _scope(runner,attempt):
    result={key:attempt.get(key) for key in ('run_id','attempt_id','spec_digest')}
    result.update(server=runner.server.rstrip('/'),target_id=runner.target_id,destination=_destination(runner))
    if (any(not isinstance(result[key],str) or not result[key] or '\x00' in result[key]
            for key in ('server','target_id','run_id','attempt_id','spec_digest'))
            or re.fullmatch('[0-9a-f]{64}',result['spec_digest']) is None):
        raise ValueError('publication requires a complete frozen attempt identity')
    return result


class AttemptPublication:
    def __init__(self,runner,attempt,root,*,check_authority=lambda:None):
        self.runner,self.attempt=runner,attempt
        self.root=Path(root).resolve(strict=True)
        self.paths=RunnerPublicationPaths(self.root)
        self.scope=_scope(runner,attempt)
        self.api=FinalizationApiPaths.for_runner(runner.target_id,attempt['attempt_id'])
        self.artifacts=AttemptArtifactApiPaths(self.api.base)
        self.headers={'X-Lab-Lease':attempt['lease_token']}
        self.check_authority=check_authority
        self.protocol=None

    def _directory(self):
        self.paths.directory.mkdir(mode=0o700,exist_ok=True)
        info=self.paths.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid()
                or stat.S_IMODE(info.st_mode)!=0o700):
            raise ValueError('publication directory must be owned mode-0700 and not a link')

    @contextmanager
    def locked(self):
        self._directory()
        descriptor=os.open(self.paths.lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        try:
            info=os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
                raise ValueError('invalid publication lock')
            try:
                fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise PublicationUnconfirmed('publication is already active; no execution was replayed') from None
            yield self
        finally:
            os.close(descriptor)

    def save(self,path,payload,*,immutable=False):
        self._directory()
        text=_canonical(payload)
        value={'format':'tinystudio.runner-publication.v1','scope':self.scope,
               'payload_json':text,'payload_digest':hashlib.sha256(text.encode()).hexdigest()}
        if immutable and (path.exists() or path.is_symlink()):
            prior=self.load(path)
            if _canonical(prior)!=text:
                raise PublicationUnconfirmed('a different immutable publication request is already retained')
            return prior
        descriptor,name=tempfile.mkstemp(prefix='.publication-',dir=self.paths.directory)
        try:
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
                stream.write(_canonical(value)+'\n')
                stream.flush();os.fsync(stream.fileno())
            os.replace(name,path)
            directory=os.open(self.paths.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)
        return json.loads(text)

    def load(self,path):
        self._directory()
        try:
            value=_read(path)
            if (not isinstance(value,dict) or set(value)!={'format','scope','payload_json','payload_digest'}
                    or value['format']!='tinystudio.runner-publication.v1' or value['scope']!=self.scope
                    or not isinstance(value['payload_json'],str)
                    or hashlib.sha256(value['payload_json'].encode()).hexdigest()!=value['payload_digest']):
                raise ValueError('different scope or checksum')
            return json.loads(value['payload_json'],object_pairs_hook=_pairs,parse_constant=_finite)
        except (OSError,ValueError,TypeError,KeyError):
            raise PublicationUnconfirmed('private publication journal is invalid or belongs to another attempt') from None

    @classmethod
    def recover(cls,runner,root,*,lease_token,check_authority=lambda:None):
        if not isinstance(lease_token,str) or not lease_token or '\x00' in lease_token:
            raise ValueError('publication recovery requires an explicitly supplied attempt credential')
        paths=RunnerPublicationPaths(Path(root).resolve(strict=True))
        # The protocol journal is reserved before child staging. It contains
        # only public scope; every field is checked again by load below.
        try:scope=_read(paths.protocol)['scope']
        except (OSError,ValueError,TypeError,KeyError):
            raise PublicationUnconfirmed('no valid retained publication identity is available') from None
        attempt={key:scope.get(key) for key in ('run_id','attempt_id','spec_digest')}
        attempt['lease_token']=lease_token
        value=cls(runner,attempt,root,check_authority=check_authority)
        value.protocol=value._validate_protocol(value.load(paths.protocol),allow_legacy=True)
        return value

    @staticmethod
    def _validate_protocol(body,*,allow_legacy=False):
        if allow_legacy and isinstance(body,dict) and set(body)=={'legacy'} and body['legacy'] is True:
            return {'legacy':True}
        try:
            if (not isinstance(body,dict) or type(body.get('protocol_version')) is not int
                    or body['protocol_version']!=1 or type(body.get('finalization_protocol_version')) is not int
                    or body['finalization_protocol_version']!=FINALIZATION_PROTOCOL_VERSION
                    or body.get('backend') not in {'r2','local'}
                    or type(body.get('resumable')) is not bool or type(body.get('direct_download')) is not bool
                    or body['backend']=='local' and (body['resumable'] or body['direct_download'])):
                raise ValueError('unsupported protocol')
            policy=body['policy']
            selected=TransferPolicy(per_file=TransferBudget(**policy['per_file']),aggregate=TransferBudget(**policy['aggregate']),
                geometry=MultipartGeometry(part_bytes=policy['geometry']['part_bytes'],concurrency=policy['geometry']['concurrency']),
                signed_url_expiry_seconds=policy['signed_url_expiry_seconds'],
                verifier_timeout_seconds=policy['verifier_timeout_seconds'])
        except (KeyError,TypeError,ValueError):
            raise PublicationUnconfirmed('unsupported or invalid Runner artifact capability; no fallback selected') from None
        return {key:body[key] for key in ('protocol_version','finalization_protocol_version','backend','resumable','direct_download')}|{'policy':selected.as_dict()}

    def discover(self):
        self.check_authority()
        try:
            response=self.runner.client.get(self.artifacts.policy_route,headers=self.headers,follow_redirects=False)
            if response.status_code in {404,405}:
                body={'legacy':True}
            elif response.status_code==200:
                body=self._validate_protocol(response.json())
            else:
                raise PublicationUnconfirmed('Runner artifact capability was not acknowledged')
        except (httpx.HTTPError,ValueError):
            raise PublicationUnconfirmed('Runner artifact capability is unconfirmed; no fallback selected') from None
        self.check_authority()
        self.protocol=self.save(self.paths.protocol,body,immutable=True)
        return self.protocol

    @property
    def resumable(self):
        return self.protocol is not None and self.protocol.get('resumable') is True

    @property
    def direct_download(self):
        return self.protocol is not None and self.protocol.get('direct_download') is True

    def descriptor(self,ref,frozen):
        self.check_authority()
        try:
            response=self.runner.client.post(self.artifacts.input_download(ref['artifact_id']),
                                             headers=self.headers,follow_redirects=False)
            response.raise_for_status()
            body=response.json()
        except (httpx.HTTPError,ValueError):
            raise PublicationUnconfirmed('input download descriptor is unconfirmed; no proxy fallback selected') from None
        metadata=body.get('artifact') if isinstance(body,dict) else None
        if (not isinstance(metadata,dict) or metadata.get('id')!=ref['artifact_id']
                or metadata.get('sha256')!=ref['sha256'] or type(metadata.get('size')) is not int
                or metadata['size']!=frozen['size'] or body.get('mode') not in {'direct','proxy'}):
            raise PublicationUnconfirmed('input descriptor differs from frozen artifact identity')
        self.check_authority()
        return body

    @staticmethod
    def validate_artifact(receipt,item):
        expected={name:item[name] for name in ('name','sha256','size','media_type','role','visibility')}
        if (not isinstance(receipt,dict) or not isinstance(receipt.get('id'),str) or not receipt['id']
                or type(receipt.get('size')) is not int
                or any(receipt.get(key)!=value for key,value in expected.items())):
            raise PublicationUnconfirmed('published artifact receipt differs from retained source identity')
        return {'id':receipt['id'],**expected}

    def source_path(self,item):
        if (not isinstance(item,dict) or type(item.get('size')) is not int or item['size']<0
                or any(not isinstance(item.get(key),str) or not item[key]
                       for key in ('relative_path','sha256','name','media_type','role','visibility'))
                or re.fullmatch('[0-9a-f]{64}',item['sha256']) is None):
            raise PublicationUnconfirmed('invalid retained artifact identity')
        relative=item['relative_path']
        if (not isinstance(relative,str) or not relative.startswith('work/evidence/')
                or any(part in {'','.', '..'} for part in relative.split('/')) or '\\' in relative):
            raise PublicationUnconfirmed('retained artifact path is outside the evidence directory')
        path=self.root
        for component in relative.split('/'):
            path=path/component
            if path.is_symlink():
                raise PublicationUnconfirmed('retained artifact path contains a symlink')
        return path

    def source(self,item):
        path=self.source_path(item)
        try:identity=TransferDataClient(check_authority=self.check_authority).source_identity(path)
        except (OSError,ArtifactTransferError):
            raise PublicationUnconfirmed('retained artifact source is missing or invalid') from None
        if identity!={'size':item['size'],'sha256':item['sha256']}:
            raise PublicationUnconfirmed('retained artifact source bytes changed')
        return path

    def upload(self,item,index,proxy_upload):
        path=self.source(item)
        if self.resumable:
            client=ResumableTransferClient(self.runner.client,self.artifacts.transfers,headers=self.headers,
                                          check_authority=self.check_authority)
            try:
                receipt=client.upload(path,name=item['name'],media_type=item['media_type'],role=item['role'],
                    visibility=item['visibility'],expected_sha256=item['sha256'],
                    idempotency_key=checksum({'scope':self.scope,'artifact_index':index,'artifact':item}),
                    state_path=self.paths.transfer(index))
            except ArtifactTransferError:
                raise PublicationUnconfirmed('artifact transfer remains unpublished; exact session and source bytes retained') from None
        else:
            receipt=proxy_upload(path,item)
        self.source(item)
        return self.validate_artifact(receipt,item)

    def prepare_execution(self,plan):
        return self.save(self.paths.execution,plan,immutable=True)

    def prepare_finish(self,body):
        if (not isinstance(body,dict) or set(body)!={'state','result','error'}
                or body['state'] not in {'succeeded','failed','cancelled'}
                or body['result'] is not None and not isinstance(body['result'],dict)
                or body['error'] is not None and not isinstance(body['error'],str)):
            raise ValueError('invalid completion body')
        return self.save(self.paths.finalization,body,immutable=True)

    def _ticket(self,ticket,body):
        expected={key:self.scope[key] for key in ('run_id','attempt_id','target_id','spec_digest')}
        if (not isinstance(ticket,dict) or ticket.get('kind')!='attempt_finalize'
                or not isinstance(ticket.get('id'),str) or not ticket['id']
                or ticket.get('payload_digest')!=checksum(body)
                or any(ticket.get(key)!=value for key,value in expected.items())
                or ticket.get('state') not in {'queued','running','succeeded','failed','lost','cancelled'}
                or 'result' not in ticket):
            raise PublicationUnconfirmed('finalization ticket differs from the exact retained request')
        self.api.operation(ticket['id'])
        if ticket['state']=='succeeded':
            result=self._ack(ticket.get('result'),body)
        elif ticket.get('result') is not None:
            raise PublicationUnconfirmed('unfinished finalization cannot acknowledge terminal completion')
        else:result=None
        return {key:ticket[key] for key in ('id','kind','state','payload_digest','run_id','attempt_id',
                                            'target_id','spec_digest')}|{'result':result}

    @staticmethod
    def _ack(ack,body):
        if (not isinstance(ack,dict) or set(ack)!={'accepted','state'}
                or ack.get('accepted') is not True or ack.get('state')!=body['state']):
            raise PublicationUnconfirmed('completion acknowledgement is unconfirmed; local evidence remains retained')
        return {'accepted':True,'state':body['state']}

    def lookup(self,body):
        try:
            response=self.runner.client.get(self.api.by_digest(checksum(body)),headers=self.headers,follow_redirects=False)
            if response.status_code==404:
                return None
            response.raise_for_status()
            return self._ticket(response.json(),body)
        except (httpx.HTTPError,ValueError):
            raise PublicationUnconfirmed('exact finalization status is unconfirmed; retained request was not changed') from None

    def finish(self,body,*,resume=False,ticket=None):
        body=self.prepare_finish(body)
        if self.protocol is None:
            raise PublicationUnconfirmed('publication protocol was not selected; exact outcome remains retained')
        if self.protocol.get('legacy'):
            self.check_authority()
            try:
                response=self.runner.client.post(self.api.legacy_finish,json=body,headers=self.headers)
                response.raise_for_status()
                ack=self._ack(response.json(),body)
            except (httpx.HTTPError,ValueError):
                raise PublicationUnconfirmed('legacy completion acknowledgement is unconfirmed; exact outcome retained') from None
            self.save(self.paths.finalization_status,{'ack':ack})
            return ack
        ticket=self._ticket(ticket,body) if ticket is not None else self.lookup(body) if resume else None
        if ticket is None:
            self.check_authority()
            self.save(self.paths.finalization_status,{'submitted':True,'payload_digest':checksum(body)})
            try:
                response=self.runner.client.post(self.api.collection,json=body,headers=self.headers,follow_redirects=False)
                if response.status_code!=202:
                    raise PublicationUnconfirmed('finalization admission was not acknowledged')
                ticket=self._ticket(response.json(),body)
            except (httpx.HTTPError,ValueError,PublicationUnconfirmed):
                ticket=self.lookup(body)
                if ticket is None:
                    raise PublicationUnconfirmed('finalization admission is unconfirmed; inspect the same retained request') from None
        operation_id=ticket['id']
        while ticket['state'] in {'queued','running'}:
            try:self.check_authority()
            except Exception:
                latest=self.lookup(body)
                if latest is None or latest['id']!=operation_id or latest['state']!='succeeded':
                    raise PublicationUnconfirmed('live finalization authority ended; exact request retained for historical receipt lookup') from None
                ticket=latest
                break
            # Historical lookup remains possible when finalization wins the race
            # with the next heartbeat. It grants no device/publication authority.
            time.sleep(0.05)
            ticket=self.lookup(body)
            if ticket is None:
                raise PublicationUnconfirmed('previously admitted finalization is absent; retained request unchanged')
            if ticket['id']!=operation_id:
                raise PublicationUnconfirmed('finalization operation identity changed; retained request unchanged')
        if ticket['state']!='succeeded':
            self.save(self.paths.finalization_status,{'ticket':ticket})
            raise PublicationUnconfirmed('finalization operation '+ticket['state']+'; explicit operation recovery required')
        ack=self._ack(ticket['result'],body)
        self.save(self.paths.finalization_status,{'ticket':ticket,'ack':ack})
        return ack
