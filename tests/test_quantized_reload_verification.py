"""Deterministic reload-oracle tests; these do not qualify NF4 hardware."""
import hashlib
import json
import math
import subprocess
import sys
from types import SimpleNamespace

import pytest
import torch
from safetensors.torch import load_file, save_file

from tinystudio.core.file_manifest import directory_manifest
from tinystudio.training import quantization


class ProbeModel(torch.nn.Module):
    def __init__(self, values):
        super().__init__()
        self.values = values
        self.calls = 0

    def forward(self, **batch):
        value = self.values[self.calls]
        self.calls += 1
        return SimpleNamespace(logits=value.reshape(1, 1, -1))


def probes(tmp_path, values):
    path = tmp_path / 'reload-probes.safetensors'
    save_file({f'probe_{index}': value for index, value in enumerate(values)}, str(path))
    return {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'inputs': [{'input_ids': [[index + 1]], 'attention_mask': [[1]]} for index in range(3)],
            'comparison': 'bitwise_equal_last_token_logits', 'mode': 'eval; no autocast; no sampling'}


def repeated(values, dtype=torch.float32):
    return [torch.tensor(values, dtype=dtype) for _ in range(3)]


def test_default_verification_returns_measured_receipt(tmp_path):
    values = repeated([0, 1, -2])
    retained = probes(tmp_path, values)
    result = quantization.verify_probes(ProbeModel(values), tmp_path, retained, 'cpu')
    assert result['status'] == 'passed'
    assert result['comparison_policy'] == {'mode': 'bitwise'}
    assert len(result['probes']) == 3
    assert all(row['bitwise_equal'] and row['max_abs_error'] == 0 for row in result['probes'])
    assert all(row['expected_dtype'] == row['actual_dtype'] == 'float32' for row in result['probes'])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('actual', [repeated([0., 1.], torch.float64), repeated([-0., 1.])])
def test_default_bitwise_comparison_rejects_dtype_and_signed_zero_changes(tmp_path, actual):
    retained = probes(tmp_path, repeated([0., 1.]))
    with pytest.raises(ValueError, match='differs from the live trained policy') as caught:
        quantization.verify_probes(ProbeModel(actual), tmp_path, retained, 'cpu')
    receipt = caught.value.receipt
    assert receipt['status'] == 'failed'
    assert len(receipt['probes']) == 3
    assert not receipt['probes'][0]['bitwise_equal']
    json.dumps(receipt, allow_nan=False)


def test_numerical_changes_need_explicit_tolerances_and_record_errors(tmp_path):
    retained = probes(tmp_path, repeated([1., 2.]))
    actual = repeated([1.125, 1.75])
    with pytest.raises(ValueError):
        quantization.verify_probes(ProbeModel(actual), tmp_path, retained, 'cpu')
    policy = {'mode': 'allclose', 'rtol': 0., 'atol': .25}
    receipt = quantization.verify_probes(ProbeModel(actual), tmp_path, retained, 'cpu', comparison_policy=policy)
    assert receipt['status'] == 'passed' and receipt['comparison_policy'] == policy
    assert all(not row['bitwise_equal'] and row['passed'] for row in receipt['probes'])
    assert receipt['probes'][0]['max_abs_error'] == .25
    assert receipt['probes'][0]['max_rel_error'] == .125


def test_allclose_uses_expected_logits_as_relative_error_reference(tmp_path):
    retained = probes(tmp_path, repeated([1.]))
    with pytest.raises(ValueError) as caught:
        quantization.verify_probes(ProbeModel(repeated([2.])), tmp_path, retained, 'cpu',
                                   comparison_policy={'mode': 'allclose', 'rtol': .6, 'atol': 0.})
    assert not caught.value.receipt['probes'][0]['passed']
    assert caught.value.receipt['comparison_policy']['rtol'] == .6


@pytest.mark.parametrize('policy', [
    {'mode': 'allclose'}, {'mode': 'allclose', 'rtol': 0.}, {'mode': 'allclose', 'atol': 0.},
    {'mode': 'allclose', 'rtol': -.1, 'atol': 0.}, {'mode': 'allclose', 'rtol': True, 'atol': 0.},
    {'mode': 'allclose', 'rtol': math.inf, 'atol': 0.}, {'mode': 'allclose', 'rtol': 0., 'atol': math.nan},
    {'mode': 'bitwise', 'atol': 0.}, {'mode': 'automatic'}, 'allclose',
])
def test_comparison_policy_has_no_inferred_or_invalid_tolerances(tmp_path, policy):
    retained = probes(tmp_path, repeated([1.]))
    with pytest.raises(ValueError, match='comparison|tolerance|rtol|atol'):
        quantization.verify_probes(ProbeModel(repeated([1.])), tmp_path, retained, 'cpu', comparison_policy=policy)


def test_failed_oracles_preserve_checksum_diagnostics(tmp_path):
    retained = probes(tmp_path, repeated([1.]))
    retained['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='checksum') as caught:
        quantization.verify_probes(ProbeModel(repeated([1.])), tmp_path, retained, 'cpu')
    assert caught.value.receipt['status'] == 'failed'
    assert caught.value.receipt['diagnostics']


def test_nonfinite_logits_never_pass_or_produce_nonfinite_receipt_numbers(tmp_path):
    retained = probes(tmp_path, repeated([math.inf, math.nan]))
    with pytest.raises(ValueError) as caught:
        quantization.verify_probes(ProbeModel(repeated([math.inf, math.nan])), tmp_path, retained, 'cpu',
                                   comparison_policy={'mode': 'allclose', 'rtol': 0., 'atol': 0.})
    assert caught.value.receipt['status'] == 'failed'
    json.dumps(caught.value.receipt, allow_nan=False)


def test_shape_mismatch_records_both_shapes_without_broadcasting(tmp_path):
    retained = probes(tmp_path, repeated([1., 2.]))
    with pytest.raises(ValueError) as caught:
        quantization.verify_probes(ProbeModel(repeated([1.])), tmp_path, retained, 'cpu',
                                   comparison_policy={'mode': 'allclose', 'rtol': 0., 'atol': 0.})
    row = caught.value.receipt['probes'][0]
    assert row['expected_shape'] == [2] and row['actual_shape'] == [1]
    assert row['max_abs_error'] is None and not row['passed']


def test_relative_error_at_zero_reference_is_json_safe_and_explicit(tmp_path):
    retained = probes(tmp_path, repeated([0., 2.]))
    result = quantization.verify_probes(ProbeModel(repeated([.125, 2.])), tmp_path, retained, 'cpu',
                                        comparison_policy={'mode': 'allclose', 'rtol': 0., 'atol': .125})
    row = result['probes'][0]
    assert row['max_abs_error'] == .125
    assert row['max_rel_error'] is None and row['relative_error_unbounded']
    json.dumps(result, allow_nan=False)


def test_verification_honors_retained_no_autocast_probe_mode(tmp_path):
    class LinearProbe(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.layer = torch.nn.Linear(1, 2, bias=False)
            with torch.no_grad():
                self.layer.weight.copy_(torch.tensor([[1.], [2.]]))

        def forward(self, input_ids, **kwargs):
            return SimpleNamespace(logits=self.layer(input_ids.float().unsqueeze(-1)))

    retained = probes(tmp_path, [torch.tensor([1., 2.]), torch.tensor([2., 4.]), torch.tensor([3., 6.])])
    with torch.autocast('cpu', dtype=torch.bfloat16):
        result = quantization.verify_probes(LinearProbe(), tmp_path, retained, 'cpu')
    assert result['status'] == 'passed'
    assert all(row['actual_dtype'] == 'float32' for row in result['probes'])


def test_capture_and_verification_both_disable_enclosing_autocast(tmp_path):
    class LinearProbe(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.layer = torch.nn.Linear(1, 2, bias=False)
            with torch.no_grad():
                self.layer.weight.copy_(torch.tensor([[1.], [2.]]))

        def forward(self, input_ids, **kwargs):
            return SimpleNamespace(logits=self.layer(input_ids.float().unsqueeze(-1)))

    def tokenizer(prompt, **kwargs):
        return {'input_ids': torch.tensor([[len(prompt)]]), 'attention_mask': torch.ones((1, 1), dtype=torch.long)}

    model = LinearProbe()
    with torch.autocast('cpu', dtype=torch.bfloat16):
        retained = quantization.capture_probes(model, tokenizer, tmp_path, 'cpu', 32)
        result = quantization.verify_probes(model, tmp_path, retained, 'cpu')
    expected = load_file(str(tmp_path / retained['file']))
    assert torch.equal(expected['probe_0'], torch.tensor([15., 30.]))
    assert all(value.dtype == torch.float32 for value in expected.values())
    assert result['status'] == 'passed'


def test_adapter_digest_excludes_base_and_preserves_dtype_and_bytes():
    model = torch.nn.Module()
    model.weight = torch.nn.Parameter(torch.tensor([1.]))
    model.lora_A = torch.nn.Parameter(torch.tensor([0.]))
    first = quantization.adapter_state_digest(model)
    model.weight.data.add_(1)
    assert quantization.adapter_state_digest(model) == first
    model.lora_A.data[0] = -0.
    second = quantization.adapter_state_digest(model)
    assert second != first
    model.lora_A.data = model.lora_A.data.to(torch.float64)
    assert quantization.adapter_state_digest(model) != second


def test_policy_and_receipt_helpers_import_without_optional_runtime_kernels():
    script = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'torch', 'transformers', 'peft', 'bitsandbytes'}:
        raise AssertionError('optional runtime imported during metadata import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from tinystudio.training.quantization import ComparisonPolicy
assert ComparisonPolicy.resolve().report() == {'mode': 'bitwise'}
'''
    subprocess.run([sys.executable, '-c', script], check=True, capture_output=True, text=True)


@pytest.fixture
def reload_bundle(tmp_path, monkeypatch):
    import peft
    import transformers

    base = torch.nn.Linear(1, 1, bias=False)
    values = repeated([1., 2.])

    class Policy(ProbeModel):
        peft_config = {}

        def __init__(self):
            super().__init__(values)
            self.base = base
            self.lora_A = torch.nn.Parameter(torch.tensor([.5]))

        def get_base_model(self):
            return self.base

    (tmp_path / 'quantized-base').mkdir()
    (tmp_path / 'quantized-base/packed.safetensors').write_bytes(b'unit test packed-state placeholder')
    (tmp_path / 'adapter').mkdir()
    (tmp_path / 'adapter/adapter.safetensors').write_bytes(b'unit test adapter placeholder')
    settings = {'modules': 1, 'storage_dtype': 'uint8', 'quant_type': 'nf4', 'double_quantization': True,
                'compute_dtypes': ['float32']}
    retained_runtime = {'os': 'another OS', 'machine': 'another machine', 'python': '3.12.0',
                        'device': 'cuda:9', 'dtype': 'float32', 'dependencies': {'torch': 'other build'}}
    manifest = {'format': 'tinystudio.pretrained.v2', 'output_kind': 'quantized_adapter',
                'update_strategy': 'qlora', 'training_type': 'qlora', 'trained_directory': 'adapter',
                'trained_files': directory_manifest(tmp_path / 'adapter'),
                'quantization': {'format': 'tinystudio.nf4-base.v1', 'directory': 'quantized-base',
                    'files': directory_manifest(tmp_path / 'quantized-base'), 'settings': settings,
                    'state_sha256': quantization.state_digest(base, base_only=True),
                    'runtime': retained_runtime, 'precision': {'adapter_dtype': 'float32'}},
                'reload_probes': probes(tmp_path, values)}
    manifest_path = tmp_path / 'pretrained.json'
    manifest_path.write_text(json.dumps(manifest))
    observed = {'loads': [], 'policy': None}

    def load_base(path, **kwargs):
        observed['loads'].append((path, kwargs))
        return base

    def load_adapter(*args, **kwargs):
        observed['policy'] = Policy()
        return observed['policy']

    monkeypatch.setattr(transformers.AutoModelForCausalLM, 'from_pretrained', load_base)
    monkeypatch.setattr(transformers.AutoTokenizer, 'from_pretrained', lambda *args, **kwargs: object())
    monkeypatch.setattr(peft.PeftModel, 'from_pretrained', load_adapter)
    monkeypatch.setattr(peft, 'prepare_model_for_kbit_training', lambda model, **kwargs: model)
    monkeypatch.setattr(quantization, 'nf4_settings', lambda model: settings.copy())
    monkeypatch.setattr(quantization, 'dependency_versions', lambda: {'torch': 'observed build'})
    return tmp_path, manifest_path.read_bytes(), manifest, observed


def test_selected_device_is_measured_despite_retained_runtime_metadata_differences(reload_bundle):
    from tinystudio.lab.compatibility import load_pretrained_output
    root, original, retained, observed = reload_bundle
    model, _, manifest = load_pretrained_output(root, device='cpu')
    assert model is observed['policy']
    assert observed['loads'][0][1]['device_map'] == {'': 'cpu'}
    receipt = manifest['runtime_verification']
    assert receipt['status'] == 'passed'
    assert receipt['retained_runtime'] == retained['quantization']['runtime']
    assert receipt['observed_runtime']['device'] == 'cpu'
    assert receipt['observed_runtime']['dependencies'] == {'torch': 'observed build'}
    assert receipt['placement']['model_devices'] == ['cpu']
    assert receipt['probes']['status'] == 'passed'
    assert (root / 'pretrained.json').read_bytes() == original


def test_loader_propagates_explicit_policy_into_runtime_receipt(reload_bundle):
    from tinystudio.lab.compatibility import load_pretrained_output
    root, _, _, _ = reload_bundle
    policy = {'mode': 'allclose', 'rtol': 0., 'atol': 0.}
    _, _, manifest = load_pretrained_output(root, device='cpu', comparison_policy=policy)
    assert manifest['runtime_verification']['probes']['comparison_policy'] == policy


def test_loader_keeps_declared_artifact_comparison_and_allows_strict_override(reload_bundle, monkeypatch):
    import peft
    from tinystudio.lab.compatibility import load_pretrained_output
    root, _, retained, _ = reload_bundle
    retained['reload_comparison'] = {'mode': 'allclose', 'rtol': 0., 'atol': .25}
    (root / 'pretrained.json').write_text(json.dumps(retained))
    load = peft.PeftModel.from_pretrained
    def altered_logits(*args, **kwargs):
        model = load(*args, **kwargs)
        model.values = repeated([1.125, 1.75])
        return model
    monkeypatch.setattr(peft.PeftModel, 'from_pretrained', altered_logits)
    _, _, result = load_pretrained_output(root, device='cpu')
    receipt = result['runtime_verification']
    assert receipt['comparison_policy'] == retained['reload_comparison']
    assert receipt['comparison_policy_source'] == 'retained_artifact'
    with pytest.raises(quantization.ReloadVerificationError) as caught:
        load_pretrained_output(root, device='cpu', comparison_policy={'mode': 'bitwise'})
    assert caught.value.receipt['comparison_policy_source'] == 'explicit_request'


def test_undeclared_artifact_policy_preserves_bitwise_default(reload_bundle):
    from tinystudio.lab.compatibility import load_pretrained_output
    root, _, _, _ = reload_bundle
    _, _, result = load_pretrained_output(root, device='cpu')
    assert result['runtime_verification']['comparison_policy'] == {'mode': 'bitwise'}
    assert result['runtime_verification']['comparison_policy_source'] == 'default_bitwise'


def test_loader_retains_failed_state_receipt_without_loading_float_fallback(reload_bundle):
    from tinystudio.lab.compatibility import load_pretrained_output
    root, _, retained, observed = reload_bundle
    retained['quantization']['state_sha256'] = '0' * 64
    (root / 'pretrained.json').write_text(json.dumps(retained))
    with pytest.raises(ValueError, match='base state') as caught:
        load_pretrained_output(root, device='cpu')
    assert caught.value.receipt['status'] == 'failed'
    assert caught.value.receipt['retained_runtime'] == retained['quantization']['runtime']
    assert len(observed['loads']) == 1 and observed['policy'] is None


def test_loader_rejects_adapter_checksum_change_before_model_load(reload_bundle):
    from tinystudio.lab.compatibility import load_pretrained_output
    root, _, _, observed = reload_bundle
    (root / 'adapter/adapter.safetensors').write_bytes(b'changed')
    with pytest.raises(ValueError, match='adapter checksum') as caught:
        load_pretrained_output(root, device='cpu')
    assert caught.value.receipt['status'] == 'failed'
    assert observed['loads'] == []


@pytest.mark.parametrize('runtime', [None, [], 'unknown'])
def test_malformed_retained_runtime_preserves_readable_failure_receipt(reload_bundle, runtime):
    from tinystudio.lab.compatibility import load_pretrained_output
    root, _, retained, observed = reload_bundle
    retained['quantization']['runtime'] = runtime
    (root / 'pretrained.json').write_text(json.dumps(retained))
    with pytest.raises(ValueError, match='runtime|precision') as caught:
        load_pretrained_output(root, device='cpu')
    assert caught.value.receipt['status'] == 'failed'
    assert observed['loads'] == []


def test_quantized_markers_prevent_a_downgraded_manifest_from_using_float_fallback(reload_bundle):
    from tinystudio.lab.compatibility import load_pretrained_output
    root, _, retained, observed = reload_bundle
    retained.update(format='tinystudio.pretrained.v1', output_kind='adapter', update_strategy='lora', training_type='lora')
    (root / 'pretrained.json').write_text(json.dumps(retained))
    with pytest.raises(ValueError, match='inconsistent quantized'):
        load_pretrained_output(root, device='cpu')
    assert observed['loads'] == []


def test_declared_adapter_precision_cannot_silently_change_on_reload(reload_bundle):
    from tinystudio.lab.compatibility import load_pretrained_output
    root, _, retained, observed = reload_bundle
    retained['quantization']['precision']['adapter_dtype'] = 'bfloat16'
    (root / 'pretrained.json').write_text(json.dumps(retained))
    with pytest.raises(ValueError, match='precision') as caught:
        load_pretrained_output(root, device='cpu')
    assert caught.value.receipt['status'] == 'failed'
    assert observed['loads'] == []


def test_loader_failed_probe_retains_runtime_and_each_probe_measurement(reload_bundle):
    from tinystudio.lab.compatibility import load_pretrained_output
    root, _, retained, _ = reload_bundle
    retained['reload_probes'] = probes(root, repeated([1.5, 2.]))
    (root / 'pretrained.json').write_text(json.dumps(retained))
    before = (root / 'pretrained.json').read_bytes()
    with pytest.raises(ValueError, match='differs from the live trained policy') as caught:
        load_pretrained_output(root, device='cpu')
    receipt = caught.value.receipt
    assert receipt['status'] == receipt['probes']['status'] == 'failed'
    assert len(receipt['probes']['probes']) == 3
    assert receipt['probes']['probes'][0]['max_abs_error'] == .5
    assert receipt['observed_runtime']['device'] == 'cpu'
    assert (root / 'pretrained.json').read_bytes() == before
    json.dumps(receipt, allow_nan=False)
