"""Owned SDK responses verify multipart control and linear streaming hashes."""
import hashlib
import io

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from tinystudio.storage import ArtifactStorage
from tinystudio.lab.transfer_configuration import TransferPolicy
from tinystudio.lab.transfer_storage import (
    MultipartArtifactStore, TransferStorageError, TransferVerificationError,
    TransferAuthorityError, TransferDeadlineExceeded,
)


class Body(io.BytesIO):
    def __init__(self,data):
        super().__init__(data);self.read_sizes=[];self.was_closed=False
    def read(self,size=-1):
        assert size>0
        self.read_sizes.append(size)
        return super().read(min(size,3))
    def close(self):
        self.was_closed=True;super().close()


class SDK:
    def __init__(self):
        self.calls=[];self.responses={};self.failures={}
        self.model=Session().get_service_model('s3')
    def __getattr__(self,name):
        def call(**kwargs):
            operation=''.join(part.title() for part in name.split('_'))
            validate_parameters(kwargs,self.model.operation_model(operation).input_shape)
            self.calls.append((name,kwargs))
            if name in self.failures:raise self.failures[name]
            response=self.responses.get(name,{})
            return response.pop(0) if isinstance(response,list) else response
        return call
    def generate_presigned_url(self,method,*,Params,ExpiresIn):
        validate_parameters(Params,self.model.operation_model('GetObject' if method=='get_object' else 'UploadPart').input_shape)
        self.calls.append(('sign',(method,Params,ExpiresIn)))
        return 'https://owned.invalid/part?signature=synthetic-secret'


@pytest.fixture
def sdk():return SDK()


@pytest.fixture
def store(sdk):return MultipartArtifactStore(ArtifactStorage(client=sdk,bucket='owned-bucket'))


def test_pure_constructor_and_remote_requirement(tmp_path,sdk):
    remote=MultipartArtifactStore(ArtifactStorage(client=sdk,bucket='owned'))
    local=MultipartArtifactStore(ArtifactStorage(root=tmp_path))
    assert sdk.calls==[] and remote.policy==TransferPolicy()
    for operation in (lambda:local.begin('object','text/plain'),lambda:local.parts('object','upload'),
                      lambda:local.sign_part('object','upload',1,1),lambda:local.inspect_uploads('object'),
                      lambda:local.sign_download('object'),
                      lambda:local.complete('object','upload',[{'number':1,'etag':'exact'}]),
                      lambda:local.abort('object','upload'),lambda:local.put_empty('object','text/plain')):
        with pytest.raises(TransferStorageError):operation()


def test_multipart_controls_use_exact_sdk_shapes_and_etags(store,sdk):
    sdk.responses['create_multipart_upload']={'UploadId':'owned-upload'}
    assert store.begin('lab/p/object','application/octet-stream')=='owned-upload'
    rows=[{'number':1,'etag':'"quoted"'},{'number':2,'etag':'verbatim-2'}]
    assert store.complete('lab/p/object','owned-upload',rows)['storage_key']=='lab/p/object'
    assert sdk.calls[-1][1]['MultipartUpload']=={'Parts':[{'PartNumber':1,'ETag':'"quoted"'},{'PartNumber':2,'ETag':'verbatim-2'}]}
    assert sdk.calls[-1][1]['IfNoneMatch']=='*'
    store.abort('lab/p/object','owned-upload')
    assert sdk.calls[-1][0]=='abort_multipart_upload'
    store.put_empty('lab/p/empty','text/plain')
    assert sdk.calls[-1][1]['Body']==b'' and sdk.calls[-1][1]['IfNoneMatch']=='*'


def test_exact_key_upload_discovery_and_parts_are_paginated(store,sdk):
    sdk.responses['list_multipart_uploads']=[
        {'Uploads':[{'Key':'object-extra','UploadId':'ignore'},{'Key':'object','UploadId':'one'}],
         'IsTruncated':True,'NextKeyMarker':'object','NextUploadIdMarker':'one'},
        {'Uploads':[{'Key':'object','UploadId':'two'}],'IsTruncated':False}]
    assert store.inspect_uploads('object')==['one','two']
    assert sdk.calls[1][1]['UploadIdMarker']=='one'
    sdk.responses['list_parts']=[{'Parts':[{'PartNumber':1,'Size':9,'ETag':'"a"'}],
        'IsTruncated':True,'NextPartNumberMarker':1},
        {'Parts':[{'PartNumber':2,'Size':3,'ETag':'"b"'}],'IsTruncated':False}]
    assert store.parts('object','one')==[{'number':1,'size':9,'etag':'"a"'},{'number':2,'size':3,'etag':'"b"'}]
    assert sdk.calls[-1][1]['PartNumberMarker']==1


def test_signing_binds_exact_part_size_and_refreshes_without_storing_url(store,sdk):
    first=store.sign_part('object','one',2,7,3601)
    second=store.sign_part('object','one',2,7)
    assert first['headers']=={'Content-Length':'7'} and first['expires_in']==3601
    assert second['expires_in']==900
    assert sdk.calls[0][1][1]=={'Bucket':'owned-bucket','Key':'object','UploadId':'one','PartNumber':2,'ContentLength':7}
    assert len(sdk.calls)==2
    assert 'signature' not in repr(store)


@pytest.mark.parametrize('parts',[[],[{'number':2,'etag':'a'}],
    [{'number':1,'etag':'a'},{'number':1,'etag':'b'}],[{'number':1,'etag':''}]])
def test_complete_rejects_invalid_part_order_without_sdk_mutation(store,sdk,parts):
    with pytest.raises(TransferStorageError):store.complete('object','one',parts)
    assert sdk.calls==[]


def test_streaming_verification_ignores_metadata_and_composite_checksums(store,sdk):
    data=b'owned-linear-payload';body=Body(data);checks=[]
    sdk.responses['get_object']={'Body':body,'ContentLength':len(data),'ETag':'composite-3',
        'ChecksumSHA256':'not-a-linear-hash','Metadata':{'sha256':'untrusted'}}
    result=store.verify('object',len(data),hashlib.sha256(data).hexdigest(),lambda:checks.append(True))
    assert result=={'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'storage_key':'object'}
    assert len(body.read_sizes)>1 and body.was_closed and len(checks)>len(body.read_sizes)


@pytest.mark.parametrize('header,data,size,digest',[
    (3,b'abc',2,hashlib.sha256(b'ab').hexdigest()),
    (None,b'abc',2,hashlib.sha256(b'ab').hexdigest()),
    (None,b'a',2,hashlib.sha256(b'ab').hexdigest()),
    (None,b'ab',2,'0'*64)])
def test_verification_rejects_size_and_hash_mismatch_and_closes(store,sdk,header,data,size,digest):
    body=Body(data);sdk.responses['get_object']={'Body':body}
    if header is not None:sdk.responses['get_object']['ContentLength']=header
    with pytest.raises(TransferVerificationError):store.verify('object',size,digest)
    assert body.was_closed
    if header is not None:assert body.read_sizes==[]


def test_authority_and_deadline_interrupt_reads_and_close_without_leaking(store,sdk,monkeypatch):
    body=Body(b'abcdef');sdk.responses['get_object']={'Body':body,'ContentLength':6}
    checks=[]
    def revoke():
        checks.append(True)
        if len(checks)==4:raise RuntimeError('private-authority-token')
    with pytest.raises(TransferAuthorityError) as caught:
        store.verify('object',6,hashlib.sha256(b'abcdef').hexdigest(),revoke)
    assert body.was_closed and body.read_sizes and 'private-authority-token' not in str(caught.value)
    assert caught.value.__suppress_context__
    from tinystudio.lab import transfer_storage
    clock=iter([1.,1.,1.,3.])
    monkeypatch.setattr(transfer_storage.time,'monotonic',lambda:next(clock))
    body=Body(b'abcdef');sdk.responses['get_object']={'Body':body,'ContentLength':6}
    with pytest.raises(TransferDeadlineExceeded):store.verify('object',6,'0'*64,deadline=2.)
    assert body.was_closed


def test_transport_read_failure_and_client_failure_are_safe_and_not_retried(store,sdk):
    class BrokenBody(Body):
        def read(self,size):raise RuntimeError('https://signed.invalid/?token=private')
    body=BrokenBody(b'abc');sdk.responses['get_object']={'Body':body,'ContentLength':3}
    with pytest.raises(TransferStorageError) as caught:store.verify('object',3,'0'*64)
    assert body.was_closed and 'private' not in str(caught.value) and caught.value.__suppress_context__
    sdk.failures['complete_multipart_upload']=RuntimeError('private-provider-token')
    with pytest.raises(TransferStorageError) as caught:store.complete('object','one',[{'number':1,'etag':'a'}])
    assert 'private' not in str(caught.value) and caught.value.__suppress_context__
    assert [name for name,_ in sdk.calls].count('complete_multipart_upload')==1


def test_head_is_advisory_and_empty_object_verifies(store,sdk):
    sdk.responses['head_object']={'ContentLength':0,'ETag':'not-content-sha'}
    assert store.head('object')['size']==0
    body=Body(b'');sdk.responses['get_object']={'Body':body,'ContentLength':0}
    assert store.verify('object',0,hashlib.sha256(b'').hexdigest())['size']==0
    assert body.was_closed


def test_local_verification_streams_without_cloud(tmp_path):
    target=tmp_path/'object';target.write_bytes(b'owned')
    local=MultipartArtifactStore(ArtifactStorage(root=tmp_path))
    assert local.verify('object',5,hashlib.sha256(b'owned').hexdigest())['size']==5


def test_actual_botocore_signing_binds_content_length_without_network():
    import boto3
    from botocore.config import Config
    from urllib.parse import parse_qs,urlsplit
    client=boto3.client('s3',endpoint_url='https://owned.invalid',region_name='auto',
        aws_access_key_id='owned-fixture-key',aws_secret_access_key='owned-fixture-secret',
        config=Config(signature_version='s3v4',retries={'total_max_attempts':1}))
    backend=MultipartArtifactStore(ArtifactStorage(client=client,bucket='owned-bucket'))
    descriptor=backend.sign_part('object','owned-upload',1,17,604800)
    query=parse_qs(urlsplit(descriptor['url']).query)
    assert query['X-Amz-SignedHeaders']==['content-length;host']
    assert query['X-Amz-Expires']==['604800'] and query['partNumber']==['1']
    assert descriptor['headers']=={'Content-Length':'17'}
    assert client.meta.config.retries['total_max_attempts']==1
    assert backend.retry_scope['adapter_retries'] is False
    descriptor=backend.sign_download('object',604800)
    query=parse_qs(urlsplit(descriptor['url']).query)
    assert query['X-Amz-SignedHeaders']==['host'] and query['X-Amz-Expires']==['604800']
    assert descriptor['headers']=={} and descriptor['expires_in']==604800


@pytest.mark.parametrize('method,response',[
    ('list_multipart_uploads',{'Uploads':None}),('list_parts',{'Parts':['private']}),
    ('list_parts',{'Parts':[],'IsTruncated':'yes'}),('create_multipart_upload',None)])
def test_malformed_sdk_responses_use_safe_exception_classes(store,sdk,method,response):
    sdk.responses[method]=response
    invoke={'list_multipart_uploads':lambda:store.inspect_uploads('object'),
        'list_parts':lambda:store.parts('object','one'),
        'create_multipart_upload':lambda:store.begin('object','text/plain')}[method]
    with pytest.raises(TransferStorageError):invoke()


def test_pagination_cannot_loop_and_does_not_duplicate_parts(store,sdk):
    page={'Uploads':[],'IsTruncated':True,'NextKeyMarker':'object','NextUploadIdMarker':'one'}
    sdk.responses['list_multipart_uploads']=[page,page]
    with pytest.raises(TransferStorageError,match='advance'):store.inspect_uploads('object')
    sdk.responses['list_parts']=[{'Parts':[{'PartNumber':1,'Size':1,'ETag':'a'}],
        'IsTruncated':True,'NextPartNumberMarker':1},{'Parts':[{'PartNumber':1,'Size':1,'ETag':'a'}]}]
    with pytest.raises(TransferStorageError,match='duplicate'):store.parts('object','one')


def test_known_provider_errors_are_classified_without_retaining_raw_text(store,sdk):
    from botocore.exceptions import ClientError
    from tinystudio.lab.transfer_storage import TransferObjectConflict,TransferUploadMissing
    sdk.failures['head_object']=ClientError({'Error':{'Code':'NoSuchKey','Message':'private-token'}},'HeadObject')
    assert store.head('object') is None
    sdk.failures['abort_multipart_upload']=ClientError({'Error':{'Code':'NoSuchUpload','Message':'private-token'}},'AbortMultipartUpload')
    with pytest.raises(TransferUploadMissing) as caught:store.abort('object','one')
    assert 'private-token' not in str(caught.value) and caught.value.__suppress_context__
    sdk.failures['put_object']=ClientError({'Error':{'Code':'PreconditionFailed','Message':'private-token'}},'PutObject')
    with pytest.raises(TransferObjectConflict):store.put_empty('object','text/plain')


@pytest.mark.parametrize('number,size,expiry',[(True,1,1),(0,1,1),(10001,1,1),(1,0,1),(1,1,0),(1,1,604801)])
def test_invalid_part_signing_stops_before_sdk(store,sdk,number,size,expiry):
    with pytest.raises(TransferStorageError):store.sign_part('object','one',number,size,expiry)
    assert sdk.calls==[]


def test_selected_zero_verifier_timeout_precedes_storage_access(sdk,monkeypatch):
    from tinystudio.lab import transfer_storage
    monkeypatch.setattr(transfer_storage.time,'monotonic',lambda:10.)
    store=MultipartArtifactStore(ArtifactStorage(client=sdk,bucket='owned'),TransferPolicy(verifier_timeout_seconds=0))
    with pytest.raises(TransferDeadlineExceeded):store.verify('object',0,hashlib.sha256(b'').hexdigest())
    assert sdk.calls==[]


def test_close_failure_is_safe_and_does_not_hide_a_verification_failure(store,sdk):
    class BadClose(Body):
        def close(self):
            already=self.was_closed
            super().close()
            if not already:raise RuntimeError('private-close-token')
    first=BadClose(b'a');sdk.responses['get_object']={'Body':first}
    with pytest.raises(TransferVerificationError):store.verify('object',2,'0'*64)
    assert first.was_closed
    second=BadClose(b'a');sdk.responses['get_object']={'Body':second}
    with pytest.raises(TransferStorageError) as caught:
        store.verify('object',1,hashlib.sha256(b'a').hexdigest())
    assert second.was_closed and 'private-close-token' not in str(caught.value)


def test_large_verification_keeps_each_read_bounded_and_streams_to_linear_hash(store,sdk):
    data=b'owned'*(500_000)
    class LargeBody(io.BytesIO):
        def __init__(self):super().__init__(data);self.read_sizes=[]
        def read(self,size):self.read_sizes.append(size);return super().read(size)
    body=LargeBody();sdk.responses['get_object']={'Body':body,'ContentLength':len(data)}
    assert store.verify('object',len(data),hashlib.sha256(data).hexdigest())['size']==len(data)
    assert body.closed and len(body.read_sizes)>2 and max(body.read_sizes)<=1024*1024


def test_process_interruption_still_closes_an_obtained_body(store,sdk):
    class OwnedInterruption(BaseException):pass
    body=Body(b'a');sdk.responses['get_object']={'Body':body,'ContentLength':1}
    checks=[]
    def interrupted():
        checks.append(True)
        if len(checks)==2:raise OwnedInterruption()
    with pytest.raises(OwnedInterruption):store.verify('object',1,'0'*64,interrupted)
    assert body.was_closed


@pytest.mark.parametrize('expiry',[True,0,-1,604801,1.0])
def test_signed_download_rejects_invalid_expiry_before_sdk(store,sdk,expiry):
    with pytest.raises(TransferStorageError):store.sign_download('object',expiry)
    assert sdk.calls==[]


def test_signed_download_uses_policy_default_and_safe_signing_error(store,sdk,monkeypatch):
    assert store.sign_download('object')=={'url':'https://owned.invalid/part?signature=synthetic-secret',
        'headers':{},'expires_in':900}
    assert sdk.calls[-1][1]==('get_object',{'Bucket':'owned-bucket','Key':'object'},900)
    def failed(*args,**kwargs):raise RuntimeError('private-provider-token')
    monkeypatch.setattr(sdk,'generate_presigned_url',failed)
    with pytest.raises(TransferStorageError) as caught:store.sign_download('object')
    assert caught.value.__suppress_context__ and 'private-provider-token' not in str(caught.value)
