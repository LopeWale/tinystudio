"""Explicit curation budgets and nested, authored acquisition selectors."""
import hashlib
import json
from pathlib import Path

import pytest

from tinystudio.lab import data


def experiment(rows=None, *, resources=None, stages=None, limits=None, **parameters):
    if rows is not None:
        parameters['records'] = rows
    if resources is not None:
        parameters['resources'] = resources
    spec = {'definition': {'engine': 'data.pipeline', 'mode': 'offline',
                          'parameters': parameters, 'pipeline': stages or []}}
    if limits is not None:
        spec['_runtime_grants'] = {'limits': limits}
    return spec


def row(index=0):
    return {'source_record_id': f'capture:{index}', 'time': index,
            'available_at': index, 'value': index + .25, 'units': 'V'}


def run(spec, output):
    return data.execute_data(spec, output, lambda event: None, lambda: False)


def imported(path, **parameters):
    spec = experiment(**parameters)
    spec['definition']['inputs'] = [{'artifact_id': 'capture', 'role': 'source',
                                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
    spec['input_paths'] = {'capture': str(path)}
    return spec


def test_single_record_budget_and_unpartitioned_receipt(tmp_path):
    result = run(experiment([row()], resources={'max_rows': 1}), tmp_path)
    assert result['records'] == 1
    assert result['qualification']['dataset_ready'] is False
    resources = result['qualification']['resources']
    assert resources['selected']['max_rows'] == 1
    assert resources['selected']['source_bytes'] is None
    assert resources['actual']['rows'] == 1
    assert resources['actual']['pipeline_stages'] == 0
    assert 'in-memory' in resources['disclosure']


def test_more_than_one_hundred_thousand_rows_are_not_silently_cropped(tmp_path):
    rows = [row(index) for index in range(100_001)]
    acquired, sources, maximum = data.acquire(experiment(rows, resources={'max_rows': 100_001}), tmp_path, lambda: False)
    assert maximum == len(acquired) == 100_001
    assert acquired[-1]['record'] == rows[-1]
    assert Path(sources[0]['path']).read_bytes().count(b'\n') == 100_001


def test_source_over_the_old_fixture_byte_ceiling_is_allowed(tmp_path):
    path = tmp_path / 'large.jsonl'
    path.write_bytes(json.dumps(row()).encode() + b' ' * (32 * 1024 ** 2) + b'\n')
    result = run(imported(path, resources={'max_rows': 1}), tmp_path / 'out')
    assert result['qualification']['resources']['actual']['source_bytes'] == path.stat().st_size
    frozen = next(item for item in result['artifacts'] if item['role'] == 'original_evidence')
    assert frozen['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('resources,limits', [
    ({'max_rows': 2}, {'max_rows': 1}),
    ({'source_bytes': None}, {'source_bytes': 100}),
    ({'pipeline_stages': 3}, {'pipeline_stages': 2}),
    ({'window_records': None}, {'window_records': 8}),
])
def test_operator_budget_rejects_before_input_access(tmp_path, resources, limits):
    spec = experiment(resources=resources, limits=limits)
    spec['definition']['inputs'] = [{'artifact_id': 'missing', 'sha256': '0' * 64}]
    spec['input_paths'] = {'missing': str(tmp_path / 'does-not-exist')}
    with pytest.raises(ValueError, match='operator resource budget'):
        run(spec, tmp_path / 'out')


@pytest.mark.parametrize('name', ['source_bytes', 'per_source_bytes'])
def test_configured_bytes_are_checked_before_file_read(tmp_path, monkeypatch, name):
    path = tmp_path / 'source.jsonl'
    path.write_text(json.dumps(row()) + '\n')
    spec = imported(path, resources={name: path.stat().st_size - 1})
    original_open = Path.open

    def guarded_open(self, *args, **kwargs):
        if self == path:
            pytest.fail('source was opened before its configured byte budget was checked')
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', guarded_open)
    with pytest.raises(ValueError, match=name):
        run(spec, tmp_path / 'out')


def test_omitted_limits_inherit_operator_and_explicit_alias_conflicts_fail(tmp_path):
    result = run(experiment([row()], limits={'max_rows': 1, 'source_bytes': 500}), tmp_path / 'ok')
    assert result['qualification']['resources']['selected']['max_rows'] == 1
    assert result['qualification']['resources']['selected']['source_bytes'] == 500
    with pytest.raises(ValueError, match='conflict'):
        run(experiment([row()], resources={'max_rows': 2}, max_rows=1), tmp_path / 'conflict')
    assert run(experiment([row()], max_rows=1), tmp_path / 'alias')['records'] == 1


def test_row_exhaustion_errors_instead_of_returning_a_prefix(tmp_path):
    with pytest.raises(ValueError, match='max_rows'):
        run(experiment([row(), row(1)], resources={'max_rows': 1}), tmp_path)
    assert not (tmp_path / 'transformed.jsonl').exists()


def test_configurable_stage_and_window_budgets(tmp_path):
    stages = [{'kind': 'scale', 'parameters': {'factor': 1}} for _ in range(33)]
    result = run(experiment([row()], stages=stages, resources={'pipeline_stages': 33}), tmp_path / 'many')
    assert result['qualification']['resources']['actual']['pipeline_stages'] == 33
    with pytest.raises(ValueError, match='pipeline_stages'):
        run(experiment([row()], stages=stages, resources={'pipeline_stages': 32}), tmp_path / 'stages')
    result = run(experiment([row(index) for index in range(300)],
                           stages=[{'kind': 'features', 'parameters': {'window': 300}}],
                           resources={'window_records': 300}), tmp_path / 'wide')
    final = json.loads((tmp_path / 'wide' / 'transformed.jsonl').read_text().splitlines()[-1])
    assert final['features']['window_count'] == 300
    assert result['qualification']['resources']['actual']['window_records'] == 300
    with pytest.raises(ValueError, match='window_records'):
        run(experiment([row()], stages=[{'kind': 'features', 'parameters': {'window': 2}}],
                       resources={'window_records': 1}), tmp_path / 'window')


def test_nested_acquisition_outcomes_and_literal_metadata_keep_raw_provenance(tmp_path):
    original = {'source_record_id': 'frame:1', 'acquisition': {'decoded_payload': {
        'event': {'tick': 17, 'available': 18}, 'channels': {'v/a': [1.25, 2.5]},
        'event_code': '0007'}}, 'true_value': 42}
    path = tmp_path / 'capture.jsonl'
    raw = json.dumps(original, separators=(',', ':')).encode() + b'\r\n'
    path.write_bytes(raw)
    schema = {'value': {'pointer': '/acquisition/decoded_payload/channels/v~1a/0'},
              'current': {'pointer': '/acquisition/decoded_payload/channels/v~1a/1'},
              'event_code': {'pointer': '/acquisition/decoded_payload/event_code'},
              'time': {'pointer': '/acquisition/decoded_payload/event/tick'},
              'available_at': {'pointer': '/acquisition/decoded_payload/event/available'},
              'units': {'literal': 'V'}, 'metadata': {'literal': {'tags': ['authored'], 'enabled': False}}}
    result = run(imported(path, schema=schema), tmp_path / 'out')
    normalized = json.loads((tmp_path / 'out' / 'transformed.jsonl').read_text())
    assert (normalized['value'], normalized['current'], normalized['event_code']) == (1.25, 2.5, '0007')
    assert normalized['metadata'] == {'tags': ['authored'], 'enabled': False}
    assert (normalized['time'], normalized['available_at']) == (17, 18)
    assert normalized['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert normalized['source_artifact_id'] == 'capture' and normalized['source_position'] == 0
    assert normalized['source_record_id'] == 'frame:1'
    assert 'true_value' not in normalized
    assert json.loads((tmp_path / 'out' / 'truth.jsonl').read_text())['true_value'] == 42
    frozen = next(item for item in result['artifacts'] if item['role'] == 'original_evidence')
    assert Path(frozen['path']).read_bytes() == raw == path.read_bytes()
    assert result['qualification']['dataset_ready'] is False


def test_nested_selector_missing_is_an_error_unless_null_was_authored(tmp_path):
    with pytest.raises(ValueError, match='resolve'):
        run(experiment([row()], schema={'value': {'pointer': '/absent'}}), tmp_path / 'missing')
    run(experiment([row()], schema={'value': {'pointer': '/absent', 'missing': 'null'}}), tmp_path / 'null')
    actual = json.loads((tmp_path / 'null' / 'transformed.jsonl').read_text())
    assert actual['value'] is None and actual['observed'] is False


def test_flat_schema_mapping_remains_literal_key_compatible(tmp_path):
    run(experiment([{'elapsed.ms': 2, 'voltage': 1.5}],
                   schema={'time': 'elapsed.ms', 'value': 'voltage'}), tmp_path)
    actual = json.loads((tmp_path / 'transformed.jsonl').read_text())
    assert actual['time'] == 2 and actual['value'] == 1.5


def test_schema_cannot_weaken_raw_source_protection_or_replace_partition(tmp_path):
    result = run(experiment([{**row(), 'visibility': 'truth'}],
                            schema={'visibility': {'literal': 'model'}}), tmp_path / 'truth')
    assert all(item['visibility'] == 'truth' for item in result['artifacts'])
    actual = json.loads((tmp_path / 'truth' / 'transformed.jsonl').read_text())
    assert actual['source_visibility'] == 'truth'
    with pytest.raises(ValueError, match='partition'):
        run(experiment([{**row(), 'partition': 'test'}],
                       schema={'partition': {'literal': 'train'}}), tmp_path / 'partition')


@pytest.mark.parametrize('format', ['json', 'jsonl', 'csv', 'parquet'])
def test_all_formats_enforce_actual_row_budget(tmp_path, format):
    path = tmp_path / f'capture.{format}'
    rows = [row(), row(1)]
    if format == 'json':
        path.write_text(json.dumps(rows))
    elif format == 'jsonl':
        path.write_text(''.join(json.dumps(value) + '\n' for value in rows))
    elif format == 'csv':
        path.write_text('time,value\n0,1\n1,2\n')
    else:
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.Table.from_pylist(rows), path)
    with pytest.raises(ValueError, match='max_rows'):
        run(imported(path, resources={'max_rows': 1}, format=format), tmp_path / 'out')


def test_total_source_budget_accumulates_across_inputs(tmp_path):
    spec = experiment(resources={'source_bytes': 199, 'per_source_bytes': 100})
    spec['definition']['inputs'], spec['input_paths'] = [], {}
    for index in range(2):
        path = tmp_path / f'source-{index}.jsonl'
        encoded = json.dumps(row(index)).encode()
        assert len(encoded) <= 100
        path.write_bytes(encoded + b' ' * (100 - len(encoded)))
        name = f'capture-{index}'
        spec['definition']['inputs'].append({'artifact_id': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        spec['input_paths'][name] = str(path)
    with pytest.raises(ValueError, match='source_bytes'):
        run(spec, tmp_path / 'out')


def test_resampling_exhaustion_errors_and_reports_peak_rows(tmp_path):
    stages = [{'kind': 'resample', 'parameters': {'dt': .25}}]
    with pytest.raises(ValueError, match='max_rows'):
        run(experiment([row(), row(1)], resources={'max_rows': 4}, stages=stages), tmp_path / 'limit')
    result = run(experiment([row(), row(1)], resources={'max_rows': 5}, stages=stages), tmp_path / 'ok')
    assert result['records'] == 5
    assert result['qualification']['resources']['actual']['rows'] == 5


def test_large_authored_window_and_resource_values_have_no_ceiling(tmp_path):
    result = run(experiment([row()], resources={'source_bytes': 10 ** 30},
                            stages=[{'kind': 'features', 'parameters': {'window': 100_001}}]), tmp_path / 'inline')
    assert result['qualification']['resources']['actual']['window_records'] == 1
    path = tmp_path / 'source.jsonl'
    path.write_text(json.dumps(row()) + '\n')
    assert run(imported(path, resources={'source_bytes': 10 ** 30}), tmp_path / 'file')['records'] == 1


def test_resample_requires_positive_representable_progress_without_a_unit_floor(tmp_path):
    result = run(experiment([row(), {**row(1), 'time': 1e-12, 'available_at': 1e-12}],
                            stages=[{'kind': 'resample', 'parameters': {'dt': 5e-13}}]), tmp_path / 'tiny')
    assert result['records'] == 3


def test_explicit_empty_evidence_has_no_scientific_row_floor(tmp_path):
    result = run(experiment([], resources={'max_rows': 0, 'source_bytes': 0,
                                           'per_source_bytes': 0}), tmp_path / 'inline')
    assert result['records'] == result['qualification']['resources']['actual']['rows'] == 0
    assert result['qualification']['dataset_ready'] is False
    path = tmp_path / 'empty.jsonl'
    path.write_bytes(b'')
    result = run(imported(path, resources={'max_rows': 0}, format='jsonl'), tmp_path / 'file')
    assert result['records'] == 0


@pytest.mark.parametrize('value', [True, -1, 1.5, 'NaN'])
def test_malformed_authored_budget_is_rejected(tmp_path, value):
    with pytest.raises(ValueError, match='max_rows'):
        run(experiment([row()], resources={'max_rows': value}), tmp_path)


def test_json_selector_configuration_is_deeply_immutable_and_types_are_authored():
    from dataclasses import FrozenInstanceError
    from tinystudio.lab.json_selection import JsonSelector
    authored = {'literal': {'array': [False, None, 9007199254740993, '0007']}}
    selector = JsonSelector.from_dict(authored)
    authored['literal']['array'].append('later')
    assert selector.resolve({}) == {'array': [False, None, 9007199254740993, '0007']}
    with pytest.raises(FrozenInstanceError):
        selector.value = 'changed'
    with pytest.raises(TypeError):
        selector.value['changed'] = True
    copied = selector.to_dict()
    copied['literal']['array'].append('copy')
    assert selector.resolve({}) == {'array': [False, None, 9007199254740993, '0007']}


def test_null_missing_policy_is_opt_in_and_study_projection_stays_strict():
    from tinystudio.lab.json_selection import JsonSelector
    assert JsonSelector.from_dict({'pointer': '/absent', 'missing': 'null'}, allow_missing=True).resolve({}) is None
    with pytest.raises(ValueError, match='selector'):
        JsonSelector.from_dict({'pointer': '/absent', 'missing': 'null'})
    with pytest.raises(ValueError, match='missing'):
        JsonSelector.from_dict({'pointer': '/absent', 'missing': 'guess'}, allow_missing=True)


def test_sampling_interval_below_the_old_floor_is_valid(tmp_path):
    rows = [{'value': 1}, {'value': 2}, {'value': 3}]
    result = run(experiment(rows, sampling_interval=.5e-12), tmp_path)
    actual = [json.loads(line) for line in (tmp_path / 'transformed.jsonl').read_text().splitlines()]
    assert [value['time'] for value in actual] == [0, .5e-12, 1e-12]
    assert result['records'] == 3


def test_positive_affine_scale_below_the_old_floor_is_valid(tmp_path):
    spec = experiment([row(), row(1), row(2)])
    spec['definition']['clocks'] = {'alignment': {'source:inline': {'scale': .5e-12, 'target': 'authored'}}}
    run(spec, tmp_path)
    actual = [json.loads(line) for line in (tmp_path / 'transformed.jsonl').read_text().splitlines()]
    assert [value['time'] for value in actual] == [0, .5e-12, 1e-12]
    assert [value['clock_alignment']['source_time'] for value in actual] == [0, 1, 2]


@pytest.mark.parametrize('value', [0, -1, float('inf')])
@pytest.mark.parametrize('parameter', ['sampling_interval', 'scale'])
def test_sampling_and_clock_scale_require_positive_finite_values(tmp_path, value, parameter):
    spec = experiment([{'value': 1}], sampling_interval=value) if parameter == 'sampling_interval' else experiment([row()])
    if parameter == 'scale':
        spec['definition']['clocks'] = {'alignment': {'source:inline': {'scale': value, 'target': 'authored'}}}
    with pytest.raises(ValueError, match='positive|finite'):
        run(spec, tmp_path)


def test_affine_coordinates_must_remain_finite_and_preserve_recorded_delay(tmp_path):
    for name, source_row, alignment in [
        ('overflow', {**row(), 'time': 1e300, 'available_at': 1e300}, {'scale': 1e100, 'target': 'authored'}),
        ('collapsed_delay', {**row(), 'available_at': 1}, {'scale': .5e-12, 'offset': 1e20, 'target': 'authored'}),
    ]:
        spec = experiment([source_row])
        spec['definition']['clocks'] = {'alignment': {'source:inline': alignment}}
        with pytest.raises(ValueError, match='finite|representable'):
            run(spec, tmp_path / name)


def test_affine_collapsed_events_cannot_be_silently_removed_as_duplicates(tmp_path):
    spec = experiment([row(), row(1)], cleaning={'duplicates': 'first'})
    spec['definition']['clocks'] = {'alignment': {'source:inline': {
        'scale': .5e-12, 'offset': 1e20, 'target': 'authored'}}}
    with pytest.raises(ValueError, match='representable'):
        run(spec, tmp_path)
