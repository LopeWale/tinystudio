import json

import httpx
from typer.testing import CliRunner

from tinystudio.lab import cli
from tinystudio.lab.transfer_client import ResumableTransferClient


def test_transfer_cli_uses_scoped_authenticated_controls_and_explicit_immutable_options(tmp_path, monkeypatch):
    source = tmp_path / 'weights.bin'
    source.write_bytes(b'\x00\xff')
    receipt = {'id': 'retained-input', 'sha256': 'a' * 64}
    observed = []
    def upload(self, path, **options):
        observed.append((self.paths.collection, self.client.headers.get('authorization'), path, options))
        return receipt
    monkeypatch.setattr(ResumableTransferClient, 'upload', upload)
    monkeypatch.setattr(cli, 'client', lambda: httpx.Client(base_url='https://studio.fixture.invalid',
        headers={'Authorization': 'Bearer owned-fixture-token'}))
    state = tmp_path / 'chosen-request.json'
    result = CliRunner().invoke(cli.app, ['transfer-upload', str(source), '--project', 'research',
        '--name', 'chosen-name.bin', '--role', 'supervisor', '--visibility', 'evaluation',
        '--sha256', 'a' * 64, '--request-file', str(state)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == receipt
    assert observed[0][:3] == ('/api/p/research/lab/transfers', 'Bearer owned-fixture-token', source)
    assert observed[0][3] == {'name': 'chosen-name.bin', 'media_type': 'application/octet-stream',
        'role': 'supervisor', 'visibility': 'evaluation', 'expected_sha256': 'a' * 64,
        'idempotency_key': None, 'state_path': state}


def test_transfer_status_is_read_only_and_abort_is_explicit(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, 'call', lambda *args: calls.append(args) or {'id': 'owned', 'state': 'uploading'})
    runner = CliRunner()
    assert runner.invoke(cli.app, ['transfer-status', 'owned', '-p', 'research']).exit_code == 0
    assert calls == [('GET', 'research', '/transfers/owned')]
    assert runner.invoke(cli.app, ['transfer-abort', 'owned', '-p', 'research']).exit_code == 0
    assert calls[-1] == ('POST', 'research', '/transfers/owned/abort', {})
