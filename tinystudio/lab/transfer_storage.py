"""Multipart control and linear verification over an existing artifact store.

Construction is pure. Mutations make one SDK invocation without adapter retries;
the supplied client's transport retry policy is unchanged. Callers retain unique
object/upload identities and own durable admission, sealing and publication.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import math
import re
import time

from tinystudio.storage import ArtifactStorage, ArtifactNotFound, StorageError
from .transfer_configuration import TransferPolicy, R2_MAXIMUM_KEY_BYTES, R2_MAXIMUM_SIGNED_URL_SECONDS


class TransferStorageError(StorageError):
    """A storage operation failed; messages never include provider exception text."""


class TransferObjectMissing(TransferStorageError, ArtifactNotFound):pass
class TransferUploadMissing(TransferStorageError):pass
class TransferObjectConflict(TransferStorageError):pass
class TransferVerificationError(TransferStorageError):pass
class TransferAuthorityError(TransferStorageError):pass
class TransferDeadlineExceeded(TransferStorageError):pass


def _integer(value, *, minimum=0, maximum=None):
    if type(value) is not int or value<minimum or maximum is not None and value>maximum:
        raise TransferStorageError('invalid transfer integer or provider protocol range')
    return value


def _text(value):
    if not isinstance(value,str) or not value or any(ord(char)<32 or ord(char)==127 for char in value):
        raise TransferStorageError('invalid transfer text')
    return value


def _key(value):
    value=_text(value)
    try:encoded=value.encode('utf-8')
    except UnicodeError:raise TransferStorageError('invalid transfer object key') from None
    if (value.startswith('/') or '\\' in value or any(part in {'','.','..'} for part in value.split('/'))
            or len(encoded)>R2_MAXIMUM_KEY_BYTES):
        raise TransferStorageError('invalid transfer object key')
    return value


class MultipartArtifactStore:
    def __init__(self,storage: ArtifactStorage,policy: TransferPolicy | None=None):
        if not isinstance(storage,ArtifactStorage) or policy is not None and not isinstance(policy,TransferPolicy):
            raise TransferStorageError('typed artifact storage and transfer policy required')
        self.storage=storage
        self.policy=TransferPolicy() if policy is None else policy

    @property
    def retry_scope(self):
        return {'adapter_retries':False,'sdk_retries':'supplied_client_configuration_unchanged'}

    def _remote(self,key):
        key=_key(key)
        if not self.storage.remote:
            raise TransferStorageError('multipart control requires remote artifact storage')
        return {'Bucket':self.storage.bucket,'Key':key}

    def _call(self,method,**parameters):
        try:response=getattr(self.storage.client,method)(**parameters)
        except Exception as cause:
            response=getattr(cause,'response',None)
            error=response.get('Error',{}) if isinstance(response,dict) else {}
            code=error.get('Code') if isinstance(error,dict) else None
            if isinstance(code,str) and code in {'404','NoSuchKey','NotFound'}:
                raise TransferObjectMissing('transfer object is missing') from None
            if code=='NoSuchUpload':raise TransferUploadMissing('multipart upload is missing') from None
            if isinstance(code,str) and code in {'412','PreconditionFailed'}:
                raise TransferObjectConflict('transfer object already exists') from None
            raise TransferStorageError('artifact storage operation failed; outcome may require reconciliation') from None
        if not isinstance(response,dict):raise TransferStorageError('invalid artifact storage response')
        return response

    @staticmethod
    def _rows(response,field):
        rows=response.get(field,[])
        if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
            raise TransferStorageError('invalid multipart listing response')
        if type(response.get('IsTruncated',False)) is not bool:
            raise TransferStorageError('invalid multipart pagination response')
        return rows

    @staticmethod
    def _media(parameters,media_type):
        if media_type is not None:parameters['ContentType']=_text(media_type)
        return parameters

    def begin(self,key,media_type=None):
        response=self._call('create_multipart_upload',**self._media(self._remote(key),media_type))
        return _text(response.get('UploadId'))

    def inspect_uploads(self,key):
        parameters=self._remote(key);parameters.pop('Key');parameters['Prefix']=key
        result=[];seen=set();markers=set()
        while True:
            response=self._call('list_multipart_uploads',**parameters)
            for row in self._rows(response,'Uploads'):
                if row.get('Key')==key:
                    identity=_text(row.get('UploadId'))
                    if identity not in seen:result.append(identity);seen.add(identity)
            if not response.get('IsTruncated',False):return result
            marker=(_text(response.get('NextKeyMarker')),response.get('NextUploadIdMarker'))
            if marker in markers:raise TransferStorageError('multipart upload pagination did not advance')
            markers.add(marker);parameters['KeyMarker']=marker[0]
            if marker[1] is not None:parameters['UploadIdMarker']=_text(marker[1])
            else:parameters.pop('UploadIdMarker',None)

    def parts(self,key,upload_id):
        parameters={**self._remote(key),'UploadId':_text(upload_id)}
        result={};markers=set()
        while True:
            response=self._call('list_parts',**parameters)
            for row in self._rows(response,'Parts'):
                number=_integer(row.get('PartNumber'),minimum=1,maximum=self.policy.geometry.maximum_parts)
                if number in result:raise TransferStorageError('duplicate multipart part in storage response')
                result[number]={'number':number,'size':_integer(row.get('Size'),maximum=self.policy.geometry.maximum_part_bytes),
                                'etag':_text(row.get('ETag'))}
            if not response.get('IsTruncated',False):return [result[number] for number in sorted(result)]
            marker=_integer(response.get('NextPartNumberMarker'),minimum=1,maximum=self.policy.geometry.maximum_parts)
            if marker in markers:raise TransferStorageError('multipart part pagination did not advance')
            markers.add(marker);parameters['PartNumberMarker']=marker

    def sign_part(self,key,upload_id,number,size,expiry=None):
        parameters={**self._remote(key),'UploadId':_text(upload_id),
            'PartNumber':_integer(number,minimum=1,maximum=self.policy.geometry.maximum_parts),
            'ContentLength':_integer(size,minimum=1,maximum=self.policy.geometry.maximum_part_bytes)}
        expiry=self.policy.signed_url_expiry_seconds if expiry is None else expiry
        _integer(expiry,minimum=1,maximum=R2_MAXIMUM_SIGNED_URL_SECONDS)
        try:url=self.storage.client.generate_presigned_url('upload_part',Params=parameters,ExpiresIn=expiry)
        except Exception:raise TransferStorageError('could not sign multipart part') from None
        if not isinstance(url,str) or not url:raise TransferStorageError('invalid multipart signing response')
        return {'url':url,'headers':{'Content-Length':str(size)},'expires_in':expiry}

    def sign_download(self,key,expiry=None):
        """Return a read capability; its URL is not evidence of content identity."""
        parameters=self._remote(key)
        expiry=self.policy.signed_url_expiry_seconds if expiry is None else expiry
        _integer(expiry,minimum=1,maximum=R2_MAXIMUM_SIGNED_URL_SECONDS)
        try:url=self.storage.client.generate_presigned_url('get_object',Params=parameters,ExpiresIn=expiry)
        except Exception:raise TransferStorageError('could not sign artifact download') from None
        if not isinstance(url,str) or not url:raise TransferStorageError('invalid artifact signing response')
        return {'url':url,'headers':{},'expires_in':expiry}

    def complete(self,key,upload_id,parts):
        parameters={**self._remote(key),'UploadId':_text(upload_id)}
        if not isinstance(parts,(list,tuple)) or not parts or len(parts)>self.policy.geometry.maximum_parts:
            raise TransferStorageError('completion requires an ordered nonempty multipart part list')
        ordered=[]
        for number,row in enumerate(parts,1):
            if not isinstance(row,dict) or type(row.get('number')) is not int or row['number']!=number:
                raise TransferStorageError('completion part numbers must be ordered and contiguous')
            ordered.append({'PartNumber':number,'ETag':_text(row.get('etag'))})
        # R2 evaluates this condition at commit. CreateMultipartUpload conditions
        # alone do not prevent overwrite (R2 S3 conditional-operations docs).
        response=self._call('complete_multipart_upload',**parameters,MultipartUpload={'Parts':ordered},IfNoneMatch='*')
        result={'storage_key':key}
        if isinstance(response.get('ETag'),str):result['etag']=response['ETag']
        return result

    def abort(self,key,upload_id):
        self._call('abort_multipart_upload',**self._remote(key),UploadId=_text(upload_id))

    def put_empty(self,key,media_type=None):
        self._call('put_object',**self._media(self._remote(key),media_type),Body=b'',ContentLength=0,IfNoneMatch='*')
        return {'storage_key':key}

    def head(self,key):
        key=_key(key)
        if not self.storage.remote:
            try:
                path=self.storage._path(key)
                return {'storage_key':key,'size':path.stat().st_size} if path.is_file() else None
            except Exception:raise TransferStorageError('could not inspect local transfer object') from None
        try:response=self._call('head_object',**self._remote(key))
        except TransferObjectMissing:return None
        result={'storage_key':key,'size':_integer(response.get('ContentLength'))}
        if isinstance(response.get('ETag'),str):result['etag']=response['ETag']
        return result

    @staticmethod
    @contextmanager
    def _body(body):
        try:yield body
        except BaseException:
            try:body.close()
            except Exception:pass
            raise
        else:
            try:body.close()
            except Exception:raise TransferStorageError('could not close transfer verification stream') from None

    def verify(self,key,expected_size,expected_sha256,check_authority=lambda:None,deadline=None):
        key=_key(key);_integer(expected_size)
        if not isinstance(expected_sha256,str) or re.fullmatch('[0-9a-f]{64}',expected_sha256) is None:
            raise TransferVerificationError('verification requires an exact linear SHA-256 digest')
        if deadline is not None:
            try:valid=type(deadline) in {float,int} and math.isfinite(deadline)
            except (OverflowError,ValueError):valid=False
            if not valid:raise TransferStorageError('verification deadline must be finite monotonic time')
        timeout=self.policy.verifier_timeout_seconds
        if timeout is not None:
            selected=time.monotonic()+timeout
            deadline=selected if deadline is None else min(deadline,selected)
        def checkpoint():
            if deadline is not None and time.monotonic()>=deadline:
                raise TransferDeadlineExceeded('artifact verification deadline expired')
            try:check_authority()
            except Exception:raise TransferAuthorityError('artifact verification authority is no longer available') from None
        checkpoint()
        if self.storage.remote:
            response=self._call('get_object',**self._remote(key))
            body=response.get('Body');length=response.get('ContentLength')
        else:
            try:
                path=self.storage._path(key)
                body=path.open('rb');length=None
            except FileNotFoundError:raise TransferObjectMissing('transfer object is missing') from None
            except Exception:raise TransferStorageError('could not open transfer verification stream') from None
        with self._body(body):
            checkpoint()
            if length is not None and (type(length) is not int or length!=expected_size):
                raise TransferVerificationError('transfer object size does not match expected size')
            digest=hashlib.sha256();size=0
            while True:
                checkpoint()
                try:chunk=body.read(min(1024*1024,expected_size-size+1))
                except Exception:raise TransferStorageError('could not read transfer verification stream') from None
                checkpoint()
                if not isinstance(chunk,bytes):raise TransferStorageError('invalid transfer verification stream')
                if not chunk:break
                size+=len(chunk)
                if size>expected_size:raise TransferVerificationError('transfer object exceeds expected size')
                digest.update(chunk)
            if size!=expected_size or digest.hexdigest()!=expected_sha256:
                raise TransferVerificationError('transfer object size or linear SHA-256 does not match')
            checkpoint()
            result={'size':size,'sha256':digest.hexdigest(),'storage_key':key}
        return result
