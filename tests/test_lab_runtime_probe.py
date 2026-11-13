"""Runtime probe orchestration; optional real NF4 qualification stays explicit."""
from dataclasses import FrozenInstanceError
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinystudio.lab.engine import ExecutionCancelled


def module():
    assert importlib.util.find_spec('tinystudio.lab.runtime_probe') is not None, 'target-side runtime probe engine is missing'
    from tinystudio.lab import runtime_probe
    return runtime_probe


def spec(**patch):
    return {'definition': {'seed': 31, 'parameters': {
        'method': 'nf4.training', 'objective': 'sft', 'device': 'cpu', 'dtype': 'float32',
        'fixture': 'tiny_llama', 'nf4_execution': {'cpu_inference': 'portable'}, **patch}}}


def read_report(result):
    artifact = next(row for row in result['artifacts'] if row['role'] == 'runtime_probe')
    path = Path(artifact['path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256']
    return json.loads(path.read_text())


def inspect_result(status='unprobed'):
    value = {'format': 'tinystudio.runtime-prerequisites.v1', 'status': status, 'qualified': False,
             'eligible': status == 'unprobed', 'reason': 'controlled prerequisite observation',
             'selection': {'device': 'cpu', 'dtype': 'float32'},
             'inventory': {'dependencies': {'torch': 'observed-test-version'}}}
    return SimpleNamespace(status=status, to_dict=lambda: value)


def stub_prerequisites(monkeypatch, status='unprobed'):
    monkeypatch.setattr(module().RuntimeCompatibility, 'inspect', lambda **kwargs: inspect_result(status))


def retained_spec(tmp_path):
    definition = spec(fixture='retained')['definition']
    definition['inputs'] = []
    paths = {}
    for role in ('pretrained_model', 'snapshot'):
        source = tmp_path / role
        source.write_bytes(('immutable ' + role).encode())
        definition['inputs'].append({'artifact_id': role, 'role': role,
                                     'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
        paths[role] = str(source)
    return {'definition': definition, 'input_paths': paths}


def success_manifest(child_spec):
    inputs = {row['role']: row for row in child_spec['definition']['inputs']}
    params = child_spec['definition']['parameters']
    policy = params['reload_comparison']
    verification = {
        'status': 'passed', 'comparison_policy': policy,
        'observed_runtime': {'device': 'cpu', 'dtype': 'float32'},
        'checks': {name: True for name in ('retained_base_files', 'retained_adapter_files', 'nf4_settings',
            'base_state', 'adapter_precision', 'adapter_preserved_base_state', 'model_placement')},
        'probes': {'status': 'passed', 'comparison_policy': policy,
                   'probes': [{'index': index, 'passed': True, 'finite': True, 'device_matches': True,
                               'actual_device': 'cpu', 'requested_device': 'cpu'} for index in range(3)]},
    }
    return {'format': 'tinystudio.pretrained.v2', 'output_kind': 'quantized_adapter',
            'metrics': {'steps': 3},
            'reload_comparison': policy,
            'training_type': 'qlora', 'update_strategy': 'qlora', 'objective': child_spec['definition']['learning']['objective'],
            'config': {'device': 'cpu', 'dtype': 'float32', 'seed': child_spec['definition']['seed'],
                       'objective': child_spec['definition']['learning']['objective'], 'update_strategy': 'qlora',
                       **{('max_steps' if name == 'steps' else 'max_length' if name == 'context' else name): value
                          for name, value in params.items() if name not in {'nf4_execution', 'reload_comparison'}}},
            'model_input': inputs['pretrained_model'], 'snapshot_input': inputs['snapshot'],
            'quantization': {'format': 'tinystudio.nf4-base.v1', 'state_sha256': 'a' * 64,
                'settings': {'modules': 7, 'storage_dtype': 'uint8', 'quant_type': 'nf4', 'double_quantization': True,
                             'compute_dtypes': ['float32']},
                'execution_policy': params['nf4_execution'], 'execution_configuration': {
                    'qualified': False, 'device': 'cpu', 'policy': params['nf4_execution']},
                'optimization': {'adapter_before_sha256': 'b' * 64, 'adapter_after_sha256': 'c' * 64,
                                 'adapter_changed': True, 'optimizer_parameters_verified': True, 'frozen_base_verified': True}},
            'runtime_verification': verification}


def fake_training(monkeypatch, *, mutate=None, qualification_status='completed'):
    from tinystudio.lab import compatibility
    calls = []

    def execute(child_spec, output_dir, emit, cancelled):
        calls.append(child_spec)
        output_dir.mkdir(parents=True)
        manifest = success_manifest(child_spec)
        if mutate:
            mutate(manifest)
        (output_dir / 'pretrained.json').write_text(json.dumps(manifest))
        return {'records': [{'type': 'loss', 'loss': .5}], 'metrics': {'steps': 3}, 'artifacts': [],
                'qualification': {'status': qualification_status, 'saved_model_reloaded': True,
                                  'runtime_verification': manifest['runtime_verification']}}

    monkeypatch.setattr(compatibility, 'execute_pretrained', execute)
    return calls


def test_program_freezes_explicit_choices_without_sample_or_dimension_recommendations():
    program = module().validate_selection(spec(fixture_config={'hidden_size': 16, 'intermediate_size': 32})['definition'])
    assert program.fixture_config['hidden_size'] == 16
    assert program.fixture_config['intermediate_size'] == 32
    assert program.to_dict()['nf4_execution'] == {'cpu_inference': 'portable'}
    with pytest.raises(FrozenInstanceError):
        program.objective = 'dpo'
    with pytest.raises(TypeError):
        program.training['steps'] = 100


def test_catalog_and_program_allow_researcher_selected_dimensions_without_recommendation_caps():
    engine = module()
    catalog = engine.method_catalog()
    assert catalog['methods'][0]['id'] == 'nf4.training'
    assert catalog['methods'][0]['version'] and catalog['methods'][0]['assumptions']
    assert len(engine.implementation_sha256()) == 64
    selected = spec(training={'steps': 100_001}, fixture_config={'hidden_size': 192, 'intermediate_size': 384,
                     'num_attention_heads': 3, 'num_key_value_heads': 3})['definition']
    selected['seed'] = -1
    program = engine.validate_selection(selected)
    assert program.seed == -1 and program.training['steps'] == 100_001
    assert program.fixture_config['hidden_size'] == 192


@pytest.mark.parametrize('patch', [
    {'method': 'arbitrary'}, {'objective': 'rl'}, {'fixture': 'download'}, {'device': None}, {'dtype': True},
    {'training': {'steps': True}}, {'training': {'learning_rate': '0.1'}},
    {'training': {'learning_rate': math.nan}}, {'training': {'lora_dropout': 1}},
    {'fixture_config': {'hidden_size': 17, 'num_attention_heads': 2}},
    {'fixture_config': {'num_key_value_heads': 3}}, {'fixture_config': {'hidden_size': 0}},
    {'model_path': '/authored/path'}, {'reload_comparison': {'mode': 'allclose'}},
])
def test_only_implemented_well_formed_probe_contracts_are_accepted(patch):
    with pytest.raises(ValueError):
        module().validate_selection(spec(**patch)['definition'])


def test_retained_probe_requires_bound_artifacts_and_rejects_path_inputs(tmp_path):
    valid = retained_spec(tmp_path)
    assert module().validate_selection(valid['definition']).fixture == 'retained'
    for replacement in ([], [{'role': 'pretrained_model', 'path': '/arbitrary/local/model'}]):
        broken = {**valid['definition'], 'inputs': replacement}
        with pytest.raises(ValueError, match='input|artifact'):
            module().validate_selection(broken)


@pytest.mark.parametrize('field,value', [('mode', 'online'), ('pipeline', [{'operation': 'filter'}]),
    ('retrieval', {'enabled': True}), ('learning', {'update_strategy': 'full'}), ('operating_mode', 'shadow')])
def test_unexecuted_configuration_is_reported_before_training(tmp_path, monkeypatch, field, value):
    stub_prerequisites(monkeypatch)
    calls = fake_training(monkeypatch)
    selected = retained_spec(tmp_path)
    selected['definition'][field] = value
    result = module().execute_runtime_probe(selected, tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['status'] == 'failed' and report['stage'] == 'selection_validation'
    assert not report['qualification']['qualified'] and calls == []


def test_missing_dependencies_report_unavailable_without_constructing_models(tmp_path, monkeypatch):
    stub_prerequisites(monkeypatch, 'unavailable')
    monkeypatch.setattr(module(), 'prepare_fixture', lambda *args, **kwargs: pytest.fail('constructed a model with unavailable prerequisites'))
    result = module().execute_runtime_probe(spec(), tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['format'] == 'tinystudio.runtime-probe.v1'
    assert report['status'] == result['qualification']['status'] == 'unavailable'
    assert result['qualification']['qualified'] is False
    assert report['stage'] == 'prerequisite_inspection'


def test_large_seed_identity_is_retained_without_ieee754_rounding(tmp_path, monkeypatch):
    stub_prerequisites(monkeypatch, 'unavailable')
    selected = spec()
    selected['definition']['seed'] = 2**63+1
    result = module().execute_runtime_probe(selected, tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['configuration']['seed'] == str(2**63+1)
    assert report['requested_configuration']['seed'] == str(2**63+1)
    assert report['integer_encoding'] == 'safe_numbers_or_decimal_strings'


def test_invalid_numerical_selection_is_retained_as_a_finite_failure_report(tmp_path):
    result = module().execute_runtime_probe(spec(training={'learning_rate': math.nan}), tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['status'] == 'failed' and report['stage'] == 'selection_validation'
    assert report['error']['message']
    json.dumps(report, allow_nan=False)


def test_success_requires_real_optimization_and_reload_evidence_for_exact_inputs(tmp_path, monkeypatch):
    stub_prerequisites(monkeypatch)
    calls = fake_training(monkeypatch)
    selected = retained_spec(tmp_path)
    events = []
    result = module().execute_runtime_probe(selected, tmp_path / 'output', events.append, lambda: False)
    report = read_report(result)
    assert report['status'] == 'verified' and result['qualification']['qualified'] is True
    assert report['scope']['objective'] == 'sft' and report['scope']['fixture'] == 'retained'
    assert report['scope']['inputs'] == selected['definition']['inputs']
    assert report['evidence']['optimization']['adapter_changed'] is True
    assert report['evidence']['runtime_verification']['status'] == 'passed'
    assert calls[0]['definition']['learning']['update_strategy'] == 'qlora'
    assert calls[0]['definition']['parameters']['nf4_execution'] == {'cpu_inference': 'portable'}
    assert events


@pytest.mark.parametrize('fault', ['missing_result', 'partial_result', 'excess_result', 'boolean_result',
    'missing_manifest', 'partial_manifest'])
def test_selected_steps_require_matching_completed_measurements(tmp_path, monkeypatch, fault):
    from tinystudio.lab import compatibility
    stub_prerequisites(monkeypatch)
    fake_training(monkeypatch)
    execute = compatibility.execute_pretrained
    def partial(*args, **kwargs):
        result = execute(*args, **kwargs)
        if fault == 'missing_result':
            result['metrics'].pop('steps')
        elif fault.endswith('_result'):
            result['metrics']['steps'] = {'partial_result': 1, 'excess_result': 4, 'boolean_result': True}[fault]
        else:
            path = args[1] / 'pretrained.json'
            manifest = json.loads(path.read_text())
            if fault == 'missing_manifest':
                manifest['metrics'].pop('steps')
            else:
                manifest['metrics']['steps'] = 1
            path.write_text(json.dumps(manifest))
        return result
    monkeypatch.setattr(compatibility, 'execute_pretrained', partial)
    result = module().execute_runtime_probe(retained_spec(tmp_path), tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['status'] == 'failed' and not report['qualification']['qualified']
    assert 'steps' in report['error']['message']


@pytest.mark.parametrize('mutate', [
    lambda m: m['quantization']['optimization'].update(adapter_changed=False),
    lambda m: m['quantization']['optimization'].update(adapter_after_sha256='b' * 64),
    lambda m: m['quantization']['optimization'].update(optimizer_parameters_verified=False),
    lambda m: m['quantization']['optimization'].update(frozen_base_verified=False),
    lambda m: m['runtime_verification'].update(status='failed'),
    lambda m: m['runtime_verification']['probes']['probes'][0].update(passed=False),
    lambda m: m['runtime_verification']['checks'].update(base_state=False),
    lambda m: m['config'].update(dtype='bfloat16'),
    lambda m: m['config'].update(learning_rate=.5),
    lambda m: m.update(reload_comparison={'mode': 'allclose', 'rtol': 0., 'atol': .5}),
    lambda m: m['quantization']['execution_configuration'].update(policy={'cpu_inference': 'library_default'}),
])
def test_harness_success_cannot_qualify_missing_or_failed_measurements(tmp_path, monkeypatch, mutate):
    stub_prerequisites(monkeypatch)
    fake_training(monkeypatch, mutate=mutate)
    result = module().execute_runtime_probe(retained_spec(tmp_path), tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['status'] == 'failed' and report['stage'] == 'evidence_verification'
    assert result['qualification']['qualified'] is False
    assert report['error']['message']


def test_failed_retained_reload_receipt_survives_successful_helper_return(tmp_path, monkeypatch):
    stub_prerequisites(monkeypatch)
    fake_training(monkeypatch, mutate=lambda manifest: manifest['runtime_verification'].update(status='failed'))
    result = module().execute_runtime_probe(retained_spec(tmp_path), tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['status'] == 'failed' and report['receipt']['status'] == 'failed'
    assert report['evidence']['optimization']['adapter_changed'] is True
    assert report['qualification']['scope'] == report['scope']


def test_retained_checksum_change_fails_at_binding_before_training(tmp_path, monkeypatch):
    stub_prerequisites(monkeypatch)
    calls = fake_training(monkeypatch)
    selected = retained_spec(tmp_path)
    Path(selected['input_paths']['snapshot']).write_text('changed source')
    result = module().execute_runtime_probe(selected, tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['status'] == 'failed' and report['stage'] == 'retained_input_binding'
    assert calls == []


def test_failed_execution_preserves_the_actual_stage_and_exception_receipt(tmp_path, monkeypatch):
    from tinystudio.lab import compatibility
    from tinystudio.training.quantization import ReloadVerificationError
    stub_prerequisites(monkeypatch)

    def fail(*args, **kwargs):
        raise ReloadVerificationError('frozen state did not match', {'status': 'failed', 'checks': {'base_state': False}})

    monkeypatch.setattr(compatibility, 'execute_pretrained', fail)
    result = module().execute_runtime_probe(retained_spec(tmp_path), tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['status'] == 'failed' and report['stage'] == 'training_and_reload'
    assert report['receipt']['checks']['base_state'] is False
    assert not any(row.get('stage') == 'evidence_verification' for row in report['stages'])


def test_failed_fixture_training_retains_exact_generated_model_and_snapshot(tmp_path, monkeypatch):
    from tinystudio.lab import compatibility
    stub_prerequisites(monkeypatch)

    def fail(*args, **kwargs):
        raise ValueError('actual selected training path failed')

    monkeypatch.setattr(compatibility, 'execute_pretrained', fail)
    result = module().execute_runtime_probe(spec(), tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['status'] == 'failed' and report['stage'] == 'training_and_reload'
    fixtures = {row['role']: row for row in result['artifacts'] if row['role'].startswith('runtime_fixture_')}
    assert fixtures.keys() == {'runtime_fixture_model', 'runtime_fixture_snapshot'}
    inputs = {row['role']: row for row in report['scope']['inputs']}
    for artifact_role, input_role in [('runtime_fixture_model', 'pretrained_model'), ('runtime_fixture_snapshot', 'snapshot')]:
        retained = fixtures[artifact_role]
        assert Path(retained['path']).is_file()
        assert retained['sha256'] == inputs[input_role]['sha256']
        assert hashlib.sha256(Path(retained['path']).read_bytes()).hexdigest() == retained['sha256']
    assert all(row['visibility'] == 'evaluation' for row in fixtures.values())


def test_successful_generated_probe_publishes_unique_fixture_and_training_names(tmp_path, monkeypatch):
    from tinystudio.lab import compatibility
    from tinystudio.lab.learning import artifact
    stub_prerequisites(monkeypatch)
    fake_training(monkeypatch)
    training = compatibility.execute_pretrained

    def execute(*args, **kwargs):
        result = training(*args, **kwargs)
        output_dir = args[1]
        for name, role in [('snapshot.jsonl', 'training_snapshot'), ('model.tar', 'pretrained_model_bundle')]:
            path = output_dir / name
            path.write_bytes(('retained training output: ' + name).encode())
            result['artifacts'].append(artifact(path, role))
        return result

    monkeypatch.setattr(compatibility, 'execute_pretrained', execute)
    result = module().execute_runtime_probe(spec(), tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['status'] == 'verified'
    names = [row['name'] for row in result['artifacts']]
    assert len(names) == len(set(names))
    fixtures = {row['role']: row for row in result['artifacts'] if row['role'].startswith('runtime_fixture_')}
    assert fixtures['runtime_fixture_model']['name'] == 'runtime-fixture-model.tar'
    assert fixtures['runtime_fixture_snapshot']['name'] == 'runtime-fixture-snapshot.jsonl'
    inputs = {row['artifact_id']: row for row in report['scope']['inputs']}
    for binding in report['fixture']['retained_artifacts']:
        retained = fixtures[binding['role']]
        assert binding['name'] == retained['name']
        assert binding['sha256'] == retained['sha256'] == inputs[binding['input_artifact_id']]['sha256']
        assert Path(retained['path']).name in {'model.tar', 'snapshot.jsonl'}
        assert hashlib.sha256(Path(retained['path']).read_bytes()).hexdigest() == retained['sha256']


def test_cancellation_propagates_before_constructing_any_probe(tmp_path):
    with pytest.raises(ExecutionCancelled):
        module().execute_runtime_probe(spec(), tmp_path / 'output', lambda row: None, lambda: True)
    assert not (tmp_path / 'output').exists()


def test_helper_cancelled_return_never_becomes_verified(tmp_path, monkeypatch):
    stub_prerequisites(monkeypatch)
    fake_training(monkeypatch, qualification_status='cancelled')
    with pytest.raises(ExecutionCancelled):
        module().execute_runtime_probe(retained_spec(tmp_path), tmp_path / 'output', lambda row: None, lambda: False)
    report = json.loads((tmp_path / 'output/runtime-probe.json').read_text())
    assert report['status'] == report['qualification']['status'] == 'cancelled'
    assert report['qualification']['qualified'] is False
    assert report['qualification']['scope'] == report['scope']


def test_malformed_definition_still_retains_selection_failure_report(tmp_path):
    result = module().execute_runtime_probe({'definition': []}, tmp_path / 'output', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['status'] == 'failed' and report['stage'] == 'selection_validation'
    assert report['configuration'] is None and report['prerequisites'] is None


def test_local_fixture_is_deterministic_and_records_actual_artifact_hashes(tmp_path):
    program = module().validate_selection(spec()['definition'])
    first = module().prepare_fixture(program, tmp_path / 'first')
    second = module().prepare_fixture(program, tmp_path / 'second')
    assert first['fixture']['model_config']['hidden_size'] == 64
    assert first['fixture']['model_config']['intermediate_size'] == 128
    assert first['fixture']['counts'] == {'train': 3, 'validation': 1, 'test': 1}
    assert first['fixture']['seed'] == 31
    assert [row['sha256'] for row in first['inputs']] == [row['sha256'] for row in second['inputs']]
    for row in first['inputs']:
        assert hashlib.sha256(Path(first['input_paths'][row['artifact_id']]).read_bytes()).hexdigest() == row['sha256']
    snapshot = next(row for row in first['inputs'] if row['role'] == 'snapshot')
    rows = [json.loads(line) for line in Path(first['input_paths'][snapshot['artifact_id']]).read_text().splitlines()]
    assert [row['partition'] for row in rows] == ['train', 'train', 'train', 'validation', 'test']


@pytest.mark.skipif(os.environ.get('TINYSTUDIO_RUN_NF4_RUNTIME_PROBE') != '1', reason='explicit opt-in for actual NF4 target-side measurement')
@pytest.mark.parametrize('objective', ['sft', 'causal_lm', 'dpo'])
def test_actual_optional_stack_measures_only_the_selected_fixture(tmp_path, objective):
    selected = spec(device=os.environ.get('TINYSTUDIO_PROBE_DEVICE', 'cpu'),
                    dtype=os.environ.get('TINYSTUDIO_PROBE_DTYPE', 'float32'), objective=objective)
    result = module().execute_runtime_probe(selected, tmp_path / 'actual', lambda row: None, lambda: False)
    report = read_report(result)
    assert report['status'] == 'verified', report
    assert result['qualification']['qualified'] is True
    assert report['scope']['fixture'] == 'tiny_llama'
