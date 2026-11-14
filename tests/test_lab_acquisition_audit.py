"""Partial acquisition is retained explicitly, independently of run success."""
import hashlib
import json
import base64

import pytest

from tests.test_lab_acquisition import Clock, Reader, spec
from tests.test_lab_device_discovery import mapping, owned_register_server
from tests.test_lab_modbus_measurement import binding, spec as modbus_spec
from tinystudio.lab.acquisition import execute_acquisition
from tinystudio.lab.engine import ExecutionCancelled
from tinystudio.lab.execution_outcome import RetainedExecutionError


def check_evidence(root, result):
    assert result['qualification']['complete'] is False
    for item in result['artifacts']:
        assert hashlib.sha256((root / item['name']).read_bytes()).hexdigest() == item['sha256']
    return {item['name']: item for item in result['artifacts']}


def test_cancel_after_receive_retains_frame_before_closing_and_hashing(tmp_path):
    clock = Clock()
    reader = Reader(clock, [{'wire': b'{"value":2}\n'}])
    with pytest.raises(ExecutionCancelled) as caught:
        execute_acquisition(spec(), tmp_path, lambda _: None, lambda: reader.reads > 0,
                            transport_factory=lambda *a, **kw: reader, clock=clock)
    assert reader.closed
    result = caught.value.evidence_result
    files = check_evidence(tmp_path, result)
    assert set(files) == {'raw.jsonl', 'processed.jsonl', 'acquisition-audit.json'}
    assert result['records'] == 1
    assert json.loads((tmp_path / 'processed.jsonl').read_text())['value'] == 2
    audit = json.loads((tmp_path / 'acquisition-audit.json').read_text())
    assert audit['status'] == 'cancelled' and audit['complete'] is False


def test_failed_point_resolution_preserves_actual_completed_discovery(tmp_path):
    pytest.importorskip('pymodbus.client')
    words = {**mapping(1769, [(64003, [11])]), **mapping(5000, [(64003, [22])])}
    with owned_register_server(words) as (port, requests):
        selected = binding({'kind': 'holding_registers', 'device_id': 255, 'sunspec': {
            'scan': {'base_addresses': [1769, 5000], 'device_id': 255},
            'point': {'model_id': 64003, 'instance': 0, 'offset': 0, 'count': 1}}})
        selected['endpoint']['port'] = port
        document = modbus_spec(selected, samples=1, duration=2, timeout=.5)
        with pytest.raises(RetainedExecutionError, match='ambiguous') as caught:
            execute_acquisition(document, tmp_path, lambda _: None, lambda: False)
    assert isinstance(caught.value.__cause__, ValueError)
    files = check_evidence(tmp_path, caught.value.evidence_result)
    assert set(files) == {'source-binding.json', 'acquisition-audit.json'}
    receipt = json.loads((tmp_path / 'source-binding.json').read_text())
    assert receipt['resolved_read'] is None and receipt['discovery']['complete'] is True
    assert len(receipt['discovery']['trials']) == 2 and receipt['discovery_wire']
    assert requests


def test_cleanup_failure_remains_visible_in_cancelled_audit(tmp_path):
    clock = Clock()
    reader = Reader(clock, [{'wire': b'{"value":2}\n'}])
    def failed_close():
        raise OSError('owned cleanup failure: closure unconfirmed')
    reader.close = failed_close
    with pytest.raises(ExecutionCancelled) as caught:
        execute_acquisition(spec(), tmp_path, lambda _: None, lambda: reader.reads > 0,
                            transport_factory=lambda *a, **kw: reader, clock=clock)
    result = caught.value.evidence_result
    check_evidence(tmp_path, result)
    audit = json.loads((tmp_path / 'acquisition-audit.json').read_text())
    assert audit['closure_unconfirmed'] is True
    assert audit['native_close_errors'][0]['message'] == 'owned cleanup failure: closure unconfirmed'
    assert result['qualification']['closure_unconfirmed'] is True
    assert result['qualification']['native_close_errors'] == audit['native_close_errors']


def test_shadow_predictor_failure_preserves_original_observation(tmp_path):
    clock = Clock()
    payload = b'{"value":2,"record_id":"source:1"}\n'
    reader = Reader(clock, [{'wire': payload}])
    def failed_predictor(row):
        raise RuntimeError('owned predictor failure')
    with pytest.raises(RetainedExecutionError, match='owned predictor failure') as caught:
        execute_acquisition(spec(mode='shadow', samples=1), tmp_path, lambda _: None, lambda: False,
            transport_factory=lambda *a, **kw: reader, clock=clock, predictor=failed_predictor)
    assert reader.closed
    result = caught.value.evidence_result
    check_evidence(tmp_path, result)
    raw = json.loads((tmp_path / 'raw.jsonl').read_text())
    assert base64.b64decode(raw['wire_base64']) == payload
    assert raw['source_identity'] == 'source:1'
    assert (tmp_path / 'processed.jsonl').read_bytes() == b''
    audit = json.loads((tmp_path / 'acquisition-audit.json').read_text())
    assert audit['retained_raw_records'] == 1 and audit['retained_processed_records'] == 0


def test_clock_validation_failure_retains_unprocessed_receive_result(tmp_path):
    clock = Clock()
    payload = b'{"value":2}\n'
    reader = Reader(clock, [{'wire': payload}], delays=[-.01])
    with pytest.raises(RetainedExecutionError, match='clock regressed') as caught:
        execute_acquisition(spec(samples=1), tmp_path, lambda _: None, lambda: False,
            transport_factory=lambda *a, **kw: reader, clock=clock)
    files = check_evidence(tmp_path, caught.value.evidence_result)
    assert 'unprocessed-receive.json' in files
    unprocessed = json.loads((tmp_path / 'unprocessed-receive.json').read_text())
    assert base64.b64decode(unprocessed['wire_base64']) == payload
    assert unprocessed['complete'] is False
