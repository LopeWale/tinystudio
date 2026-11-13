"""Actual owned Modbus acquisition retains a resolved model-relative source."""
import base64
import hashlib
import json

import pytest

from tests.test_lab_device_discovery import mapping, owned_register_server
from tests.test_lab_modbus_measurement import binding, spec
from tinystudio.lab.acquisition import execute_acquisition


def test_dynamic_measurement_artifact_and_zero_byte_wire_budget(tmp_path):
    pytest.importorskip('pymodbus.client')
    pytest.importorskip('pyarrow')
    words = mapping(1769, [(64003, [1, 2, 3]), (64003, [10, 11, 0, 23])])
    with owned_register_server(words) as (port, requests):
        selected = binding()
        selected['endpoint']['port'] = port
        document = spec(selected, samples=1, duration=2, timeout=.5,
                        max_frame_bytes=0, decode='uint32', units='mV')
        document['research_identity'] = 'owned-model-resolution'
        result = execute_acquisition(document, tmp_path, lambda _: None, lambda: False)
    artifacts = {item['name']: item for item in result['artifacts']}
    source = artifacts['source-binding.json']
    wire = (tmp_path / source['name']).read_bytes()
    assert source['role'] == 'measurement_binding' and source['visibility'] == 'evaluation'
    assert hashlib.sha256(wire).hexdigest() == source['sha256']
    receipt = json.loads(wire)
    # Signature:1769..1770, model0 header1771 payload1773..1775,
    # model1 header1776 payload1778..1781, selected offset2 ->1780.
    assert receipt['resolved_read'] == {'kind': 'holding_registers', 'device_id': 255,
                                       'address': 1780, 'count': 2}
    assert receipt['requested_read']['sunspec']['point']['instance'] == 1
    assert receipt['discovery']['complete'] is True
    assert (255, 3, 1780, 2) in requests
    assert receipt['discovery_wire']
    assert all(row['captured_bytes'] == 0 and row['prefix_base64'] == '' and row['truncated']
               for row in receipt['discovery_wire'])
    raw = json.loads((tmp_path / 'raw.jsonl').read_text())
    assert base64.b64decode(raw['wire_base64']) == b''
    assert raw['transport_metadata']['trace_capture']['observed_buffer_bytes']['rx'] > 0
    assert raw['transport_metadata']['trace_capture']['transport_wire_reconstruction'] is False
    assert raw['raw_values'] == [0, 23]
    row = json.loads((tmp_path / 'processed.jsonl').read_text())
    assert row['value'] == 23 and row['units'] == 'mV' and row['observed']
    assert 'wire_evidence_truncated' in row['quality_flags']
    assert result['qualification']['physical_validation'] is False
