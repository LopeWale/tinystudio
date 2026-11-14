"""Optional real Renode software fixture; no physical or model-fidelity claim.

Set TINYSTUDIO_RENODE to an already installed operator-selected executable.
This test never downloads Renode or supplies cloud/device credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tinystudio.config import RuntimeTools
from tests.support.managed_renode import ManagedRenodePaths, OwnedRenodeFixture, encode_firmware, prepare_fixture


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='owned native process/PTY qualification requires POSIX')


def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def test_owned_firmware_encoding_and_frozen_source_provenance(tmp_path):
    binary, listing = encode_firmware()
    assert len(binary) == 32 and len(listing) == 8
    # Known bytes also exercised against the actual vendor CPU/UART model.
    assert hashlib.sha256(binary).hexdigest() == '580a353d88b299894c8c6e033e5424441cd465c1743172c88a1bc6da68fc2354'
    paths = ManagedRenodePaths(tmp_path / 'owned')
    assert not paths.root.exists()
    provenance = prepare_fixture(paths)
    assert provenance['isa_used'] == 'RV32I subset: LUI,LBU,ANDI,BEQ,XORI,SB,JAL'
    assert provenance['cross_compiler_used'] is False and provenance['same_c_binary_claim'] is False
    for item in provenance['files']:
        source = paths.root / item['name']
        assert sha(source) == item['sha256'] and source.stat().st_size == item['bytes']
        assert source.stat().st_mode & 0o222 == 0
    assert paths.root.stat().st_mode & 0o777 == 0o700
    assert 'CreateUartPtyTerminal' in paths.script.read_text()
    assert 'CreateServerSocketTerminal' not in paths.script.read_text()
    with pytest.raises(FileExistsError):
        prepare_fixture(paths)


def test_version_wait_failure_keeps_primary_and_does_not_retry_uncertain_cleanup(tmp_path, monkeypatch):
    class Process:
        pid = os.getpid()  # Observation only; the fake never sends a signal.
        def wait(self, timeout): raise TimeoutError('owned version wait failure')
        def poll(self): return None
    class Group:
        process = Process()
        closed = False
        close_calls = 0
        def __init__(self, *args, **kwargs): pass
        def close(self):
            Group.close_calls += 1
            raise OSError('owned cleanup uncertainty')
    monkeypatch.setattr('tinystudio.lab.runner.ProcessGroup', Group)
    owned = OwnedRenodeFixture(tmp_path / 'selected-unused-tool', ManagedRenodePaths(tmp_path / 'owned'))
    with pytest.raises(TimeoutError, match='version wait failure'):
        with owned:
            pytest.fail('failed version reached fixture body')
    assert Group.close_calls == 1
    assert not owned.receipt['cleanup_confirmed']
    assert any(error['phase'] == 'version_cleanup' for error in owned.receipt['errors'])


@pytest.fixture
def renode_binary():
    if os.name != 'posix':
        pytest.skip('native Renode process/PTY lifecycle qualification requires POSIX')
    try:
        return RuntimeTools.from_environment().resolve('renode')
    except FileNotFoundError:
        pytest.skip('native Renode software fixture unqualified: set TINYSTUDIO_RENODE to an installed owned executable')


def test_real_managed_renode_uart_xor_private_logs_and_owned_cleanup(renode_binary, tmp_path):
    owned = OwnedRenodeFixture(renode_binary, ManagedRenodePaths(tmp_path / 'renode'))
    with owned:
        assert owned.ready['scope'] == 'endpoint_reachability_only'
        assert owned.bridge_identity['address'][0] == '127.0.0.1'
        assert int(owned.paths.pid.read_text()) == owned.managed.snapshot()['pid']
        response = owned.exchange_all_bytes()
        assert response == bytes(value ^ 0xa5 for value in range(256))
        assert owned.paths.request.read_bytes() == bytes(range(256))
        client_report = json.loads(owned.paths.client_result.read_text())
        assert client_report['pid'] == owned.client.process.pid
        assert client_report['request_sha256'] == sha(owned.paths.request)
        assert client_report['response_sha256'] == sha(owned.paths.response)
        assert owned.managed.poll()['exit_code'] is None
        assert owned.control.snapshot()['authorized']
        owned.control.revoke('owned_fixture_cancelled', termination='cancel')
    receipt = owned.receipt
    assert receipt['cleanup_confirmed'] and receipt['loopback_port_closed']
    assert all(item['absent_at_observation'] for item in receipt['known_pids'])
    assert receipt['client']['exit_code'] == 0 and receipt['managed']['exit_code'] == 0
    assert all(helper['cleanup_confirmed'] for helper in receipt['managed']['helpers'])
    assert receipt['managed']['raw_log_policy']['export_raw_logs'] is False
    assert receipt['managed']['qualification']['physical_outcome'] == 'unverified'
    assert receipt['managed']['qualification']['model_fidelity'] == 'unverified'
    for stream in ('stdout', 'stderr'):
        log = receipt['managed']['logs'][stream]
        assert log['observation_complete'] and log['writer_closed'] and not log['capture_truncated']
        assert owned.managed.paths.private_log(stream).stat().st_mode & 0o077 == 0
        assert sha(owned.managed.paths.private_log(stream)) == log['captured_sha256'] == log['observed_sha256']
    for channel in ('lifecycle', 'readiness'):
        writer = receipt['managed']['evidence'][channel]
        assert writer['closed'] and writer['written_records'] == writer['fsynced_records']
    assert all(artifact['media_type'] != 'application/octet-stream' for artifact in receipt['artifacts'])
    assert all(sha(artifact['path']) == artifact['sha256'] for artifact in receipt['artifacts'])
    assert all(sha(owned.paths.root / item['name']) == item['sha256'] for item in owned.provenance['files'])
    evidence = json.loads(owned.paths.result.read_text())
    assert evidence['scope']['physical_device'] is False
    assert evidence['scope']['integrated_runner_server_lease'] is False
    assert evidence['source_provenance']['same_c_binary_claim'] is False
    assert evidence['tool']['identity_scope'] == 'observed_file_and_version_output_not_execution_attestation'
