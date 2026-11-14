"""Persistence boundaries and observation availability are explicit evidence."""
import json
import errno
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tests.test_lab_acquisition import Clock, Reader, spec
from tinystudio.lab import acquisition
from tinystudio.lab.engine import ExecutionCancelled
from tinystudio.lab.execution_outcome import RetainedExecutionError


def core():
    from tinystudio.lab import evidence_io
    return evidence_io


def test_default_policy_and_batching_are_immutable_and_have_no_count_ceiling():
    from dataclasses import FrozenInstanceError
    policy = core().RecordWritePolicy.from_parameters({})
    assert policy.to_dict() == {'flush_records': 1, 'fsync': True}
    with pytest.raises(FrozenInstanceError):
        policy.flush_records = 2
    assert core().RecordWritePolicy.from_parameters({'evidence': {'flush_records': 10 ** 30}}).flush_records == 10 ** 30
    for value in (None, 'on_close'):
        assert core().RecordWritePolicy.from_parameters({'evidence': {'flush_records': value, 'fsync': False}}).to_dict() == {
            'flush_records': None, 'fsync': False}


@pytest.mark.parametrize('evidence', [None, [], {'unknown': 1}, {'flush_records': 0},
    {'flush_records': -1}, {'flush_records': True}, {'flush_records': 1.5},
    {'flush_records': '1'}, {'fsync': 1}, {'fsync': 'false'}])
def test_invalid_policy_rejects_before_any_acquisition_io(tmp_path, evidence):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid evidence policy must fail before opening a transport')
    with pytest.raises(ValueError, match='evidence|flush_records|fsync'):
        acquisition.execute_acquisition(spec(evidence=evidence), tmp_path / 'not-created', lambda _: None,
            lambda: False, transport_factory=forbidden, clock=Clock())
    assert not (tmp_path / 'not-created').exists()


def test_each_default_record_flushes_and_fsyncs_the_real_file_descriptor(tmp_path, monkeypatch):
    calls = []
    original = os.fsync
    def sync(fd):
        calls.append(os.fstat(fd).st_size)
        original(fd)
    monkeypatch.setattr(os, 'fsync', sync)
    writer = core().JsonlEvidenceWriter(tmp_path / 'capture.jsonl', core().RecordWritePolicy(), Clock())
    with writer:
        writer.write_record({'value': 2, 'metadata': {'authored': False}})
        receipt = writer.receipt()
        assert receipt['written_records'] == receipt['flushed_records'] == receipt['fsynced_records'] == 1
        assert receipt['last_successful_boundary']['kind'] == 'fsync'
        assert json.loads((tmp_path / 'capture.jsonl').read_text())['value'] == 2
    assert len(calls) == 2 and all(size > 0 for size in calls)
    assert writer.receipt()['closed'] is True


def test_selected_batching_reports_uncommitted_records_and_close_boundary(tmp_path):
    writer = core().JsonlEvidenceWriter(tmp_path / 'capture.jsonl',
        core().RecordWritePolicy(flush_records=2), Clock())
    with writer:
        writer.write_record({'value': 1})
        assert writer.receipt()['pending_records'] == {'flush': 1, 'fsync': 1}
        assert writer.receipt()['flushed_records'] == 0
        writer.write_record({'value': 2})
        assert writer.receipt()['flushed_records'] == writer.receipt()['fsynced_records'] == 2
        writer.write_record({'value': 3})
        assert writer.receipt()['pending_records']['flush'] == 1
    assert writer.receipt()['flushed_records'] == writer.receipt()['fsynced_records'] == 3
    assert writer.receipt()['pending_records'] == {'flush': 0, 'fsync': 0}


def test_on_close_and_no_fsync_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'fsync', lambda fd: pytest.fail('fsync was explicitly disabled'))
    writer = core().JsonlEvidenceWriter(tmp_path / 'capture.jsonl',
        core().RecordWritePolicy(flush_records=None, fsync=False), Clock())
    with writer:
        writer.write_record({'value': 1})
        assert writer.receipt()['pending_records'] == {'flush': 1, 'fsync': None}
    assert writer.receipt()['flushed_records'] == 1
    assert writer.receipt()['fsynced_records'] == 0
    assert writer.receipt()['sync_scope']['fsync'] == 'not_requested'


def test_serialization_failure_preserves_previous_record_and_does_not_count_a_write(tmp_path):
    writer = core().JsonlEvidenceWriter(tmp_path / 'capture.jsonl', core().RecordWritePolicy(), Clock())
    with pytest.raises(ValueError):
        with writer:
            writer.write_record({'value': 1})
            writer.write_record({'value': float('nan')})
    assert writer.receipt()['written_records'] == 1
    assert writer.receipt()['errors'][0]['operation'] == 'serialize'
    assert json.loads((tmp_path / 'capture.jsonl').read_text()) == {'value': 1}


def test_fsync_failure_keeps_written_and_flushed_counts_without_claiming_sync(tmp_path, monkeypatch):
    def failed_sync(fd):
        assert os.fstat(fd).st_size > 0
        raise OSError('owned sync failure')
    monkeypatch.setattr(os, 'fsync', failed_sync)
    writer = core().JsonlEvidenceWriter(tmp_path / 'capture.jsonl', core().RecordWritePolicy(), Clock())
    with pytest.raises(OSError, match='owned sync failure'):
        with writer:
            writer.write_record({'value': 1})
    receipt = writer.receipt()
    assert receipt['written_records'] == receipt['flushed_records'] == 1
    assert receipt['fsynced_records'] == 0 and receipt['pending_records']['fsync'] == 1
    assert receipt['closed'] and receipt['errors'][0]['operation'] == 'fsync'
    assert receipt['last_successful_boundary']['kind'] == 'flush'


def test_primary_failure_survives_close_sync_failure_with_both_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('owned close sync failure')))
    writer = core().JsonlEvidenceWriter(tmp_path / 'capture.jsonl',
        core().RecordWritePolicy(flush_records=None), Clock())
    with pytest.raises(ValueError, match='primary failure') as caught:
        with writer:
            writer.write_record({'value': 1})
            raise ValueError('primary failure')
    assert any(error['operation'] == 'fsync' for error in writer.receipt()['errors'])
    assert any('close sync failure' in note for note in caught.value.__notes__)


def test_acquisition_sync_failure_reports_written_raw_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('owned sync failure')))
    clock = Clock()
    reader = Reader(clock, [{'wire': b'{"value":2}\n'}])
    with pytest.raises(RetainedExecutionError, match='owned sync failure') as caught:
        acquisition.execute_acquisition(spec(samples=1), tmp_path, lambda _: None, lambda: False,
            transport_factory=lambda *a, **kw: reader, clock=clock)
    assert reader.closed
    audit = json.loads((tmp_path / 'acquisition-audit.json').read_text())
    assert audit['retained_raw_records'] == 1 and audit['retained_processed_records'] == 0
    assert audit['evidence_writes']['raw']['written_records'] == 1
    assert audit['evidence_writes']['raw']['fsynced_records'] == 0
    assert caught.value.evidence_result['qualification']['evidence_writes'] == audit['evidence_writes']


def test_decoded_availability_includes_queue_and_decode_but_precedes_persistence_and_prediction(tmp_path, monkeypatch):
    clock = Clock()
    original_decode = acquisition._decode
    def slow_decode(*args):
        result = original_decode(*args)
        clock.sleep(.25)
        return result
    monkeypatch.setattr(acquisition, '_decode', slow_decode)
    original_sync = os.fsync
    def slow_sync(fd):
        original_sync(fd)
        clock.sleep(.4)
    monkeypatch.setattr(os, 'fsync', slow_sync)
    frame = {'wire': b'{"value":2,"source_timestamp":1000}\n',
             'host_received_monotonic': .02, 'host_received_at': 1000.02}
    reader = Reader(clock, [frame], delays=[.1])
    def predict(row):
        assert json.loads((tmp_path / 'raw.jsonl').read_text())['decoded_payload']['value'] == 2
        assert row['available_at'] == pytest.approx(.35)
        clock.sleep(.1)
        return 3
    result = acquisition.execute_acquisition(spec(mode='shadow', samples=1, timeout=.2, stale_after=.2),
        tmp_path, lambda _: None, lambda: False, transport_factory=lambda *a, **kw: reader,
        clock=clock, predictor=predict)
    actual = json.loads((tmp_path / 'processed.jsonl').read_text())
    raw = json.loads((tmp_path / 'raw.jsonl').read_text())
    assert actual['time'] == .02
    assert actual['available_at'] == raw['available_at'] == pytest.approx(.35)
    assert actual['processed_monotonic'] == pytest.approx(.35)
    assert actual['read_completed_monotonic'] == pytest.approx(.1)
    assert actual['processing_delay_seconds'] == pytest.approx(.33)
    assert actual['source_age_seconds'] == pytest.approx(.35)
    assert actual['prediction_available_at'] == pytest.approx(.85)
    assert 'read_deadline_exceeded' not in actual['quality_flags']
    assert 'stale' in actual['quality_flags']
    assert result['qualification']['availability_boundary'] == 'decoded_and_validated_before_evidence_commit_and_inference'
    assert result['qualification']['evidence_writes']['raw']['selected_policy'] == {'flush_records': 1, 'fsync': True}


def test_invalid_decode_still_measures_its_validation_boundary(tmp_path, monkeypatch):
    clock = Clock()
    def failed_decode(*args):
        clock.sleep(.25)
        raise ValueError('owned invalid decode')
    monkeypatch.setattr(acquisition, '_decode', failed_decode)
    reader = Reader(clock, [{'wire': b'invalid'}], delays=[.1])
    acquisition.execute_acquisition(spec(samples=1, timeout=.2), tmp_path, lambda _: None, lambda: False,
        transport_factory=lambda *a, **kw: reader, clock=clock)
    row = json.loads((tmp_path / 'processed.jsonl').read_text())
    assert row['quality'] == 'invalid' and row['available_at'] == pytest.approx(.35)
    assert 'read_deadline_exceeded' not in row['quality_flags']


@pytest.mark.parametrize('policy,expected_bytes', [({}, True), ({'flush_records': None, 'fsync': False}, False)])
def test_abrupt_owned_process_exit_demonstrates_selected_buffer_boundary(tmp_path, policy, expected_bytes):
    target = tmp_path / 'capture.jsonl'
    program = '''import json,os,sys,time
from tinystudio.lab.evidence_io import JsonlEvidenceWriter,RecordWritePolicy
writer=JsonlEvidenceWriter(sys.argv[1],RecordWritePolicy.from_parameters({'evidence':json.loads(sys.argv[2])}),time)
writer.__enter__()
writer.write_record({'value':1})
os._exit(23)
'''
    process = subprocess.run([sys.executable, '-c', program, str(target), json.dumps(policy)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=5)
    assert process.returncode == 23, process.stderr.decode()
    assert bool(target.read_bytes()) is expected_bytes


def test_owned_kill_after_default_raw_commit_preserves_the_received_record(tmp_path):
    marker = tmp_path / 'prediction-entered'
    output = tmp_path / 'evidence'
    program = '''import sys,time
from pathlib import Path
from tests.test_lab_acquisition import spec,Clock,Reader
from tinystudio.lab.acquisition import execute_acquisition
clock=Clock();reader=Reader(clock,[{'wire':b'{"value":2}\\n'}])
def predict(row):
    Path(sys.argv[2]).write_text('raw commit completed before inference')
    time.sleep(10)
execute_acquisition(spec(mode='shadow',samples=1),Path(sys.argv[1]),lambda _:None,lambda:False,transport_factory=lambda *a,**kw:reader,clock=clock,predictor=predict)
'''
    process = subprocess.Popen([sys.executable, '-c', program, str(output), str(marker)],
        cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists(), process.communicate(timeout=1)
        process.kill()
        process.communicate(timeout=3)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
    assert json.loads((output / 'raw.jsonl').read_text())['decoded_payload']['value'] == 2
    assert (output / 'processed.jsonl').read_bytes() == b''


@pytest.mark.parametrize('cancelled', [False, True])
def test_audit_storage_failure_keeps_the_primary_exception_and_claims_no_published_audit(tmp_path, monkeypatch, cancelled):
    clock = Clock()
    reader = Reader(clock, [{'wire': b'{"value":2}\n'}])
    original = Path.write_text
    def exhausted(path, *args, **kwargs):
        if path.name == 'acquisition-audit.json':
            raise OSError(errno.ENOSPC, 'owned audit storage exhausted')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', exhausted)
    primary = RuntimeError('owned primary inference failure')
    def predict(row):
        raise primary
    expected = ExecutionCancelled if cancelled else RuntimeError
    with pytest.raises(expected) as caught:
        acquisition.execute_acquisition(spec(mode='acquisition' if cancelled else 'shadow', samples=1), tmp_path,
            lambda _: None, lambda: cancelled and reader.reads > 0,
            transport_factory=lambda *a, **kw: reader, clock=clock, predictor=None if cancelled else predict)
    if not cancelled:
        assert caught.value is primary
    assert any('owned audit storage exhausted' in note for note in caught.value.__notes__)
    assert getattr(caught.value, 'evidence_result', None) is None
    assert reader.closed and json.loads((tmp_path / 'raw.jsonl').read_text())['decoded_payload']['value'] == 2
    assert not (tmp_path / 'acquisition-audit.json').exists()
