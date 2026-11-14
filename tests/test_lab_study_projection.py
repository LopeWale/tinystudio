"""Pure JSONL projection preserves explicit numeric and observation choices."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import importlib.util
import json

import pytest


def module():
    assert importlib.util.find_spec('tinystudio.lab.study_projection') is not None, 'pure study projection core is missing'
    from tinystudio.lab import study_projection
    return study_projection


def mapping(**patch):
    return {'selection': {'all': True}, 'unit_id': {'pointer': '/unit'},
            'condition': {'pointer': '/condition'}, 'data': {'value': {'pointer': '/value'}},
            'number_encoding': 'json', **patch}


def source(tmp_path, text):
    path = tmp_path / 'records.jsonl'
    path.write_bytes(text.encode('utf-8'))
    return path


def test_native_json_projection_retains_hashes_physical_line_and_exact_timestamp(tmp_path):
    raw = b'{"unit":"u1","condition":"A","value":-0.125,"clock":"2026-11-13T12:34:56.123456789+00:00"}\r\n'
    path = tmp_path / 'records.jsonl'
    path.write_bytes(b'\n' + raw)
    program = module().StudyProjection.from_dict(mapping(observed_at={'pointer': '/clock'}))
    row, = program.iter_rows(path)
    result = row.to_dict()
    assert result['line'] == 2
    assert result['unit_id'] == 'u1' and result['condition'] == 'A'
    assert result['kind'] == 'observation' and result['data'] == {'value': -.125}
    assert result['observed_at'] == '2026-11-13T12:34:56.123456789+00:00'
    assert result['raw_line_sha256'] == hashlib.sha256(raw).hexdigest()
    canonical = b'{"clock":"2026-11-13T12:34:56.123456789+00:00","condition":"A","unit":"u1","value":-0.125}'
    assert result['decoded_row_sha256'] == hashlib.sha256(canonical).hexdigest()


def test_decimal_strings_preserves_numeric_lexemes_without_changing_json_strings(tmp_path):
    path = source(tmp_path, '{"unit":"u1","condition":"A","value":0.10000000000000001,"large":9007199254740993,"negative_zero":-0,"exponent":1.2300e+04,"already_text":"001.20"}\n')
    program = module().StudyProjection.from_dict(mapping(number_encoding='decimal_strings', data={
        name: {'pointer': '/' + name} for name in ('value', 'large', 'negative_zero', 'exponent', 'already_text')}))
    row, = program.iter_rows(path)
    assert row.to_dict()['data'] == {'value': '0.10000000000000001', 'large': '9007199254740993',
                                    'negative_zero': '-0', 'exponent': '1.2300e+04', 'already_text': '001.20'}
    summary = program.stream_summary(path)
    assert summary['number_encoding'] == 'decimal_strings'
    assert summary['disclosure'] and summary['count'] == 1


def test_json_float_rounding_is_explicit_and_unsafe_projected_integer_is_rejected(tmp_path):
    path = source(tmp_path, '{"unit":"u1","condition":"A","value":0.10000000000000001,"unused_large":9007199254740993}\n')
    program = module().StudyProjection.from_dict(mapping())
    row, = program.iter_rows(path)
    assert row.to_dict()['data']['value'] == .1
    assert program.stream_summary(path)['number_encoding'] == 'json'
    unsafe = module().StudyProjection.from_dict(mapping(data={'large': {'pointer': '/unused_large'}}))
    with pytest.raises(ValueError, match='exact JSON|decimal string'):
        list(unsafe.iter_rows(path))


def test_unsafe_literal_integer_is_rejected_only_at_the_projected_record_boundary(tmp_path):
    path = source(tmp_path, '{"unit":"u1","condition":"A","value":1}\n')
    for encoding in ('json', 'decimal_strings'):
        program = module().StudyProjection.from_dict(mapping(number_encoding=encoding,
                                                             data={'large': {'literal': 9007199254740993}}))
        with pytest.raises(ValueError, match='exact JSON|decimal string'):
            list(program.iter_rows(path))


def test_explicit_line_selection_uses_physical_lines_and_source_order(tmp_path):
    path = source(tmp_path, '\n{"unit":"u1","condition":"A","value":1}\n\n{"unit":"u2","condition":"B","value":2}\n')
    program = module().StudyProjection.from_dict(mapping(selection={'lines': [4, 2]}))
    assert [row.line for row in program.iter_rows(path)] == [2, 4]
    assert program.to_dict()['selection'] == {'lines': [2, 4]}
    summary = program.stream_summary(path)
    assert summary['count'] == summary['source_rows'] == 2
    assert summary['physical_lines'] == 4 and summary['blank_lines'] == 2


def test_explicit_empty_selection_and_empty_source_have_no_sample_minimum(tmp_path):
    program = module().StudyProjection.from_dict(mapping(selection={'lines': []}))
    path = source(tmp_path, '\n\n')
    summary = program.stream_summary(path)
    assert summary['count'] == 0 and summary['preview'] == []
    assert summary['rows_sha256'] == hashlib.sha256(b'').hexdigest()
    assert summary['physical_lines'] == summary['blank_lines'] == 2


@pytest.mark.parametrize('selection', [{'lines': [1]}, {'lines': [3]}])
def test_blank_and_absent_selected_lines_are_errors(tmp_path, selection):
    path = source(tmp_path, '\n{"unit":"u1","condition":"A","value":1}\n')
    program = module().StudyProjection.from_dict(mapping(selection=selection))
    with pytest.raises(ValueError, match='line'):
        list(program.iter_rows(path))


def test_rfc6901_escaping_arrays_empty_keys_and_whole_document_pointer(tmp_path):
    path = source(tmp_path, '{"unit":"u1","condition":"A","a/b":{"~x":[3,4]},"":7,"literal.key":8,"~1":9}\n')
    data = {'escaped': {'pointer': '/a~1b/~0x/1'}, 'empty': {'pointer': '/'},
            'dots_are_literal': {'pointer': '/literal.key'}, 'escape_order': {'pointer': '/~01'},
            'whole': {'pointer': ''}}
    row, = module().StudyProjection.from_dict(mapping(data=data)).iter_rows(path)
    result = row.to_dict()['data']
    assert result['escaped'] == 4 and result['empty'] == 7 and result['dots_are_literal'] == 8
    assert result['escape_order'] == 9 and result['whole']['unit'] == 'u1'


@pytest.mark.parametrize('pointer', ['/a/01', '/a/-', '/a/-1', '/missing', '/a/5'])
def test_unresolved_or_noncanonical_array_pointers_are_not_guessed(tmp_path, pointer):
    path = source(tmp_path, '{"unit":"u1","condition":"A","a":[1,2]}\n')
    program = module().StudyProjection.from_dict(mapping(data={'value': {'pointer': pointer}}))
    with pytest.raises(ValueError, match='pointer|resolve'):
        list(program.iter_rows(path))


def test_multiple_outcomes_and_repeated_units_are_preserved_without_reduction(tmp_path):
    path = source(tmp_path, '{"unit":"same","condition":"A","voltage":1,"current":2}\n{"unit":"same","condition":"B","voltage":3,"current":4}\n')
    program = module().StudyProjection.from_dict(mapping(kind='measurement', data={
        'voltage': {'pointer': '/voltage'}, 'current': {'pointer': '/current'}, 'units': {'literal': {'voltage': 'V', 'current': 'A'}}}))
    rows = [row.to_dict() for row in program.iter_rows(path)]
    assert [row['unit_id'] for row in rows] == ['same', 'same']
    assert [row['condition'] for row in rows] == ['A', 'B']
    assert [row['data']['voltage'] for row in rows] == [1, 3]
    assert all(row['kind'] == 'measurement' for row in rows)


def test_mapping_and_rows_are_deeply_immutable_and_copies_cannot_mutate_them(tmp_path):
    original = mapping(data={'value': {'pointer': '/value'}, 'metadata': {'literal': {'tags': ['reviewed']}}})
    before = deepcopy(original)
    before['kind'] = 'observation'
    program = module().StudyProjection.from_dict(original)
    original['data']['metadata']['literal']['tags'].append('later')
    path = source(tmp_path, '{"unit":"u1","condition":"A","value":1}\n')
    row, = program.iter_rows(path)
    assert program.to_dict() == before
    with pytest.raises(FrozenInstanceError):
        program.kind = 'changed'
    with pytest.raises(FrozenInstanceError):
        row.line = 100
    with pytest.raises(TypeError):
        row.data['value'] = 2
    output = row.to_dict()
    output['data']['metadata']['tags'].append('changed copy')
    assert row.to_dict()['data']['metadata']['tags'] == ['reviewed']


@pytest.mark.parametrize('patch', [
    {'selection': None}, {'selection': {'all': False}}, {'selection': {'all': 1}},
    {'selection': {'all': True, 'lines': [1]}}, {'selection': {'lines': [0]}},
    {'selection': {'lines': [True]}}, {'selection': {'lines': [1, 1]}}, {'selection': {'lines': ['1']}},
    {'unit_id': {'pointer': 'unit'}}, {'unit_id': {'pointer': '/bad~escape'}},
    {'condition': {'pointer': '/condition', 'literal': 'A'}}, {'data': []},
    {'data': {'_source_projection': {'literal': {}}}}, {'number_encoding': None},
    {'number_encoding': 'automatic'}, {'unknown': True},
])
def test_malformed_mapping_choices_are_rejected_without_defaults(patch):
    with pytest.raises(ValueError):
        module().StudyProjection.from_dict(mapping(**patch))


@pytest.mark.parametrize('missing', ['selection', 'unit_id', 'condition', 'data', 'number_encoding'])
def test_required_choices_are_never_invented(missing):
    selected = mapping()
    selected.pop(missing)
    with pytest.raises(ValueError, match=missing):
        module().StudyProjection.from_dict(selected)


@pytest.mark.parametrize('text', [
    '{"unit":"u1","condition":"A","value":1,"value":2}\n',
    '{"unit":"u1","condition":"A","value":{"x":1,"x":2}}\n',
    '{"unit":"u1","condition":"A","value":NaN}\n',
    '{"unit":"u1","condition":"A","value":Infinity}\n',
    '{"unit":"u1","condition":"A","value":-Infinity}\n',
    '[1,2,3]\n', 'null\n', '{broken}\n',
])
@pytest.mark.parametrize('encoding', ['json', 'decimal_strings'])
def test_malformed_nonobject_duplicate_and_nonfinite_jsonl_rows_are_rejected(tmp_path, text, encoding):
    path = source(tmp_path, text)
    program = module().StudyProjection.from_dict(mapping(number_encoding=encoding))
    with pytest.raises(ValueError, match='line 1'):
        list(program.iter_rows(path))


def test_invalid_utf8_is_rejected_with_physical_line_identity(tmp_path):
    path = tmp_path / 'records.jsonl'
    path.write_bytes(b'\n\xff\n')
    with pytest.raises(ValueError, match='line 2'):
        list(module().StudyProjection.from_dict(mapping()).iter_rows(path))


@pytest.mark.parametrize('patch', [
    {'unit_id': {'literal': 1}}, {'condition': {'literal': None}},
    {'observed_at': {'literal': '2026-11-13T12:00:00'}}, {'observed_at': {'literal': 123}},
])
def test_projected_record_types_and_observation_clock_follow_study_contract(tmp_path, patch):
    path = source(tmp_path, '{"unit":"u1","condition":"A","value":1}\n')
    with pytest.raises(ValueError):
        list(module().StudyProjection.from_dict(mapping(**patch)).iter_rows(path))


def test_explicit_empty_unit_condition_and_null_measurement_are_not_admission_errors(tmp_path):
    path = source(tmp_path, '{"value":null}\n')
    program = module().StudyProjection.from_dict(mapping(unit_id={'literal': ''}, condition={'literal': ''}))
    row, = program.iter_rows(path)
    assert row.unit_id == row.condition == ''
    assert row.to_dict()['data'] == {'value': None} and row.observed_at is None


def test_summary_hash_is_length_framed_and_previews_do_not_limit_selection(tmp_path):
    path = source(tmp_path, ''.join('{"unit":"same","condition":"A","value":' + str(index) + '}\n' for index in range(7)))
    program = module().StudyProjection.from_dict(mapping())
    rows = [row.to_dict() for row in program.iter_rows(path)]
    digest = hashlib.sha256()
    for row in rows:
        encoded = json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
    summary = program.stream_summary(path)
    assert summary['count'] == 7 and len(summary['preview']) == 5
    assert summary['preview'] == rows[:5] and summary['rows_sha256'] == digest.hexdigest()
    assert summary['method']['version'] and summary['method']['implementation_sha256']


def test_summary_never_exposes_unselected_source_metadata(tmp_path):
    path = source(tmp_path, '{"unit":"u1","condition":"A","value":1,"private_note":"NOT_PROJECTED"}\n')
    summary = module().StudyProjection.from_dict(mapping()).stream_summary(path)
    assert 'NOT_PROJECTED' not in json.dumps(summary)


def test_stream_has_no_existing_fixture_or_bridge_row_caps(tmp_path):
    path = tmp_path / 'records.jsonl'
    with path.open('wb') as stream:
        for _ in range(100_001):
            stream.write(b'{"unit":"same","condition":"A","value":1}\n')
    summary = module().StudyProjection.from_dict(mapping()).stream_summary(path)
    assert summary['count'] == summary['source_rows'] == 100_001
    assert len(summary['preview']) == 5
