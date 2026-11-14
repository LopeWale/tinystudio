"""Object transfers stream exact bytes and never inherit Studio credentials."""
import hashlib
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import httpx
import pytest

from tinystudio.lab.transfer_client import ArtifactTransferError, TransferDataClient


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_client(handler, check=lambda: None):
    return TransferDataClient(client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(handler), headers={'Authorization': 'Bearer studio-secret',
            'CF-Access-Client-Id': 'access-id', 'CF-Access-Client-Secret': 'access-secret'},
        cookies={'studio': 'private'}, auth=('private', 'password'), follow_redirects=True),
        check_authority=check)


def assert_private(request):
    for name in ['authorization', 'cookie', 'cf-access-client-id', 'cf-access-client-secret']:
        assert name not in request.headers


def test_exact_selected_part_does_not_send_control_credentials(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'prefix\x00\xff\ntrailer')
    observed = []
    def receive(request):
        assert_private(request)
        observed.append(request.content)
        return httpx.Response(200, headers={'ETag': '"provider-part"'})
    client = make_client(receive)
    result = client.upload_part({'url': 'https://objects.example/object?sig=private',
        'headers': {'Content-Length': '3'}}, source, offset=6, size=3)
    assert observed == [b'\x00\xff\n']
    assert result == {'size': 3, 'etag': '"provider-part"', 'scope': 'provider_http_acknowledgement_only'}
    assert client.source_identity(source) == {'size': 16, 'sha256': sha(source.read_bytes())}


@pytest.mark.parametrize('url', ['http://remote.example/object', 'https://name:secret@objects.example/',
    'https://objects.example/object#fragment', '//objects.example/object', 'https://objects.example/\n'])
def test_invalid_object_location_is_rejected_without_io(tmp_path, url):
    source = tmp_path / 'source'
    source.write_bytes(b'x')
    def forbidden(request):
        pytest.fail('no request was authorized')
    with pytest.raises(ArtifactTransferError, match='invalid signed'):
        make_client(forbidden).upload_part({'url': url}, source, offset=0, size=1)


def test_part_redirect_is_not_followed_and_capability_is_not_in_error(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'x')
    calls = []
    def redirect(request):
        calls.append(request)
        return httpx.Response(307, headers={'location': 'https://another.example/object'})
    with pytest.raises(ArtifactTransferError) as error:
        make_client(redirect).upload_part({'url': 'https://objects.example/object?sig=private'}, source, offset=0, size=1)
    assert len(calls) == 1 and 'private' not in str(error.value)


def test_lost_part_ack_never_retries_or_exposes_signed_url(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'x')
    calls = []
    def lost(request):
        calls.append(request.content)
        raise httpx.ReadTimeout(str(request.url), request=request)
    with pytest.raises(ArtifactTransferError) as error:
        make_client(lost).upload_part({'url': 'https://objects.example/object?sig=private'}, source, offset=0, size=1)
    assert calls == [b'x'] and 'private' not in str(error.value)


def test_source_and_part_change_or_cancellation_cannot_acknowledge_success(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'abc')
    def change(request):
        source.write_bytes(b'xyz')
        return httpx.Response(200)
    with pytest.raises(ArtifactTransferError, match='changed'):
        make_client(change).upload_part({'url': 'https://objects.example/object'}, source, offset=0, size=3)
    def denied():
        raise RuntimeError('owned authority expired')
    with pytest.raises(RuntimeError, match='owned authority'):
        make_client(lambda request: pytest.fail('no I/O after cancellation'), denied).source_identity(source)


@pytest.mark.parametrize('headers', [{'Authorization': 'Bearer secret'}, {'Content-Length': '2'},
    {'Content-Length': '1', 'content-length': '1'}, {'Cookie': 'studio=private'}])
def test_signed_headers_cannot_change_length_or_inject_credentials(tmp_path, headers):
    source = tmp_path / 'source'
    source.write_bytes(b'x')
    with pytest.raises(ArtifactTransferError):
        make_client(lambda request: pytest.fail('invalid request')).upload_part(
            {'url': 'https://objects.example/object', 'headers': headers}, source, offset=0, size=1)


class Chunks(httpx.SyncByteStream):
    def __init__(self, values):
        self.values, self.closed = values, False
    def __iter__(self):
        yield from self.values
    def close(self):
        self.closed = True


@pytest.mark.parametrize('status', [200, 206])
def test_download_resumes_or_restarts_and_checks_entire_sha(tmp_path, status):
    path = tmp_path / 'download'
    partial = tmp_path / 'download.partial'
    partial.write_bytes(b'abc')
    body = Chunks([b'abc', b'\x00\xffdef'] if status == 200 else [b'\x00', b'\xffdef'])
    def receive(request):
        assert_private(request)
        assert request.headers['range'] == 'bytes=3-'
        return httpx.Response(status, headers={'Content-Range': 'bytes 3-7/8'}, stream=body)
    assert make_client(receive).download({'url': 'https://objects.example/object?sig=private'}, path,
        size=8, sha256=sha(b'abc\x00\xffdef')) == path
    assert path.read_bytes() == b'abc\x00\xffdef' and not partial.exists() and body.closed


def test_corrupt_prefix_fails_full_sha_without_publishing(tmp_path):
    path = tmp_path / 'download'
    (tmp_path / 'download.partial').write_bytes(b'bad')
    body = Chunks([b'def'])
    def receive(request):
        return httpx.Response(206, headers={'Content-Range': 'bytes 3-5/6'}, stream=body)
    with pytest.raises(ArtifactTransferError, match='SHA256'):
        make_client(receive).download({'url': 'https://objects.example/object'}, path, size=6, sha256=sha(b'abcdef'))
    assert not path.exists() and body.closed


@pytest.mark.parametrize('body_bytes,size,content_range', [(b'x', 6, 'bytes 2-5/6'), (b'defg', 6, 'bytes 3-5/6'), (b'd', 6, 'bytes 3-5/6')])
def test_wrong_range_or_size_never_replaces_destination(tmp_path, body_bytes, size, content_range):
    path = tmp_path / 'download'
    path.write_bytes(b'previous verified artifact')
    (tmp_path / 'download.partial').write_bytes(b'abc')
    body = Chunks([body_bytes])
    def receive(request):
        return httpx.Response(206, headers={'Content-Range': content_range}, stream=body)
    with pytest.raises(ArtifactTransferError):
        make_client(receive).download({'url': 'https://objects.example/object'}, path, size=size, sha256=sha(b'abcdef'))
    assert path.read_bytes() == b'previous verified artifact' and body.closed


def test_missing_empty_artifact_needs_no_network_to_establish_empty_bytes(tmp_path):
    path = tmp_path / 'empty'
    make_client(lambda request: pytest.fail('zero-byte identity is deterministic')).download(
        {'url': 'https://objects.example/empty'}, path, size=0, sha256=sha(b''))
    assert path.read_bytes() == b''


def test_symlink_and_hardlinked_partial_are_rejected_without_touching_target(tmp_path):
    victim = tmp_path / 'victim'
    victim.write_bytes(b'untouched')
    path = tmp_path / 'download'
    partial = tmp_path / 'download.partial'
    partial.symlink_to(victim)
    client = make_client(lambda request: pytest.fail('no request'))
    with pytest.raises(ArtifactTransferError):
        client.download({'url': 'https://objects.example/object'}, path, size=10, sha256=sha(b'0123456789'))
    partial.unlink()
    os.link(victim, partial)
    with pytest.raises(ArtifactTransferError):
        client.download({'url': 'https://objects.example/object'}, path, size=10, sha256=sha(b'0123456789'))
    assert victim.read_bytes() == b'untouched'


def test_partial_prefix_modified_during_download_cannot_publish_a_false_hash(tmp_path):
    path = tmp_path / 'download'
    partial = tmp_path / 'download.partial'
    partial.write_bytes(b'abc')
    def receive(request):
        with partial.open('r+b') as stream:
            stream.write(b'bad')
        return httpx.Response(206, headers={'Content-Range': 'bytes 3-5/6'}, stream=Chunks([b'def']))
    with pytest.raises(ArtifactTransferError, match='SHA256|changed'):
        make_client(receive).download({'url': 'https://objects.example/object'}, path, size=6, sha256=sha(b'abcdef'))
    assert not path.exists()


def test_real_http_put_has_url_derived_host_without_client_credentials(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'\x00\xff\n')
    received = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_PUT(self):
            received.append((dict(self.headers), self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.send_header('ETag', '"owned-part"')
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = TransferDataClient(client_factory=lambda: httpx.Client(
            headers={'Authorization': 'Bearer control-token', 'CF-Access-Client-Secret': 'control-secret'},
            cookies={'control': 'private'}, timeout=2, trust_env=False))
        url = 'http://127.0.0.1:' + str(server.server_port) + '/object?signature=owned-private'
        client.upload_part({'url': url, 'headers': {'Content-Length': '3'}}, source, offset=0, size=3)
        assert len(received) == 1 and received[0][1] == b'\x00\xff\n'
        headers = {key.lower(): value for key, value in received[0][0].items()}
        assert headers['host'] == '127.0.0.1:' + str(server.server_port)
        assert headers['content-length'] == '3'
        assert not {'authorization', 'cookie', 'cf-access-client-secret'} & headers.keys()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()
