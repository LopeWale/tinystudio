"""Explicit, local NF4 training/reload measurements on the selected target.

Prerequisite discovery never grants qualification. A completed measurement is
scoped to its exact inputs/configuration, and failures remain report artifacts.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from numbers import Real
import os
from pathlib import Path
import re
import tarfile
from types import MappingProxyType

from .engine import ExecutionCancelled
from .learning import artifact
from .study_contracts import MAX_EXACT_INTEGER
from tinystudio.training.runtime_compatibility import RuntimeCompatibility


FORMAT = 'tinystudio.runtime-probe.v1'
METHOD_VERSION = '1.0.0'
OBJECTIVES = ('sft', 'causal_lm', 'dpo')
FIXTURES = ('tiny_llama', 'retained')
TOKENS = ('<pad>', '<eos>', '<unk>', 'user', 'assistant', ':', 'pressure', 'normal',
          'high', 'low', 'yes', 'no', 'sensor', 'ready')
DEFAULT_TRAINING = MappingProxyType({'steps': 3, 'context': 32, 'learning_rate': .03,
    'batch_size': 1, 'gradient_accumulation_steps': 1, 'lora_r': 2, 'lora_alpha': 4,
    'lora_dropout': 0., 'lora_targets': ('q_proj', 'v_proj')})
DEFAULT_FIXTURE_CONFIG = MappingProxyType({'vocab_size': len(TOKENS), 'hidden_size': 64,
    'intermediate_size': 128, 'num_hidden_layers': 1, 'num_attention_heads': 2,
    'num_key_value_heads': 2, 'max_position_embeddings': 64})
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


def _plain(value):
    if isinstance(value, (dict, MappingProxyType)):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    return value


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


@dataclass(frozen=True)
class RuntimeProbeProgram:
    method: str
    objective: str
    device: str
    dtype: str
    fixture: str
    seed: int
    training: object
    fixture_config: object
    nf4_execution: object
    reload_comparison: object
    inputs: tuple

    def __post_init__(self):
        for name in ('training', 'fixture_config', 'nf4_execution', 'reload_comparison', 'inputs'):
            object.__setattr__(self, name, _freeze(_plain(getattr(self, name))))

    def to_dict(self):
        return _plain(self.__dict__)


@dataclass(frozen=True)
class RuntimeProbePaths:
    root: Path

    @property
    def report(self):
        return self.root / 'runtime-probe.json'

    @property
    def fixture(self):
        return self.root / 'fixture'

    @property
    def training(self):
        return self.root / 'training'

    @property
    def partial_training_archive(self):
        return self.root / 'runtime-partial-training.tar'


def implementation_sha256():
    """One composite implementation identity shared by reports and plugins."""
    package = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for relative in ('lab/runtime_probe.py', 'lab/compatibility.py', 'lab/learning_axes.py',
                     'lab/partitioning.py', 'training/train.py', 'training/methods.py',
                     'training/runtime.py', 'training/runtime_compatibility.py',
                     'training/quantization.py', 'training/nf4_execution.py', 'core/file_manifest.py'):
        digest.update(relative.encode() + b'\0' + (package / relative).read_bytes())
    return digest.hexdigest()


def method_catalog():
    training_properties = {name: {'type': 'integer', 'minimum': 2 if name == 'context' else 1}
        for name in ('steps', 'context', 'batch_size', 'gradient_accumulation_steps', 'lora_r', 'lora_alpha')}
    training_properties.update(learning_rate={'type': 'number', 'exclusiveMinimum': 0},
        lora_dropout={'type': 'number', 'minimum': 0, 'exclusiveMaximum': 1},
        lora_targets={'type': 'array', 'minItems': 1, 'items': {'type': 'string', 'minLength': 1}})
    properties = {'method': {'const': 'nf4.training'}, 'objective': {'enum': list(OBJECTIVES)},
        'device': {'type': 'string', 'minLength': 1}, 'dtype': {'enum': ['float32', 'bfloat16', 'fp32', 'bf16']},
        'fixture': {'enum': list(FIXTURES)}, 'seed': {'type': 'integer'},
        'training': {'type': 'object', 'properties': training_properties, 'additionalProperties': False,
                     'default': _plain(DEFAULT_TRAINING)},
        'fixture_config': {'type': 'object', 'properties': {name: {'type': 'integer', 'minimum': len(TOKENS) if name == 'vocab_size' else 1}
            for name in DEFAULT_FIXTURE_CONFIG}, 'additionalProperties': False, 'default': dict(DEFAULT_FIXTURE_CONFIG)},
        'nf4_execution': {'type': 'object', 'properties': {'cpu_inference': {'enum': ['library_default', 'portable']}},
                          'additionalProperties': False, 'default': {'cpu_inference': 'library_default'}},
        'reload_comparison': {'oneOf': [
            {'type': 'object', 'properties': {'mode': {'const': 'bitwise'}}, 'required': ['mode'], 'additionalProperties': False},
            {'type': 'object', 'properties': {'mode': {'const': 'allclose'}, 'rtol': {'type': 'number', 'minimum': 0},
                'atol': {'type': 'number', 'minimum': 0}}, 'required': ['mode', 'rtol', 'atol'], 'additionalProperties': False}],
            'default': {'mode': 'bitwise'}}}
    required = ['method', 'objective', 'device', 'dtype', 'fixture']
    return {'format': 'tinystudio.runtime-methods.v1', 'implementation_sha256': implementation_sha256(), 'methods': [{
        'id': 'nf4.training', 'version': METHOD_VERSION, 'label': 'Measure NF4 training and reload',
        'description': 'Run the selected local QLoRA objective and verify NF4 retention, optimizer membership, changed adapters, frozen base and saved-policy reload.',
        'required': required, 'parameters': properties,
        'schema': {'type': 'object', 'required': required, 'properties': deepcopy(properties), 'additionalProperties': False},
        'input_roles': {'tiny_llama': [], 'retained': ['pretrained_model', 'snapshot']},
        'assumptions': ['Prerequisite availability is unprobed; only the completed target-side measurements establish the reported result.',
            'The tiny fixture has three training rows, one validation row and one excluded test row solely to exercise the implemented path; these counts are not scientific sample-size rules.',
            'Fixture structural dimensions are researcher-selectable. NF4 kernel/layout failures are measured, without a platform, architecture or package-version whitelist.',
            'A verified result covers only its exact objective, device, precision, inputs, configuration, execution policy and comparison policy; it does not qualify other models or all operations.',
            'Retained mode requires immutable pretrained_model and snapshot artifact bindings. No model or data downloads are performed.'],
        'references': ['https://huggingface.co/docs/peft/developer_guides/quantization',
                       'https://huggingface.co/docs/bitsandbytes/reference/nn/linear4bit']}]}


def _number(value, name, *, integer=False, positive=True):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f'{name} must be an explicit finite numeric value')
    try:
        finite = math.isfinite(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f'{name} is outside finite numerical representation') from exc
    if not finite or (positive is True and value <= 0) or (positive is False and value < 0):
        raise ValueError(f'{name} must be finite and {"positive" if positive else "nonnegative"}')
    if integer:
        if int(value) != value:
            raise ValueError(f'{name} must be an integer')
        return int(value)
    return float(value)


def validate_selection(value) -> RuntimeProbeProgram:
    """Validate implemented choices without importing model/kernel packages."""
    from tinystudio.training.quantization import ComparisonPolicy
    from tinystudio.training.nf4_execution import NF4ExecutionPolicy
    if not isinstance(value, dict):
        raise ValueError('runtime probe selection must be an object')
    definition = value if 'parameters' in value else {'parameters': value}
    if definition.get('mode', 'offline') != 'offline':
        raise ValueError('runtime probes execute a frozen offline measurement')
    if definition.get('operating_mode', 'replay') not in ('replay', 'simulation'):
        raise ValueError('runtime probes measure computation; acquisition and shadow behavior require their explicit adapters')
    for field in ('pipeline', 'retrieval', 'learning', 'components', 'connections', 'clocks', 'scenarios'):
        if definition.get(field):
            raise ValueError(f'runtime probe does not execute {field}; retain preparation separately and select NF4 training through probe parameters')
    params = definition.get('parameters')
    if not isinstance(params, dict):
        raise ValueError('runtime probe parameters must be an object')
    allowed = {'method', 'objective', 'device', 'dtype', 'fixture', 'training', 'fixture_config',
               'nf4_execution', 'reload_comparison', 'seed'}
    if params.keys() - allowed:
        raise ValueError('unsupported runtime probe parameter: ' + sorted(params.keys() - allowed)[0])
    for name, choices in (('method', ('nf4.training',)), ('objective', OBJECTIVES), ('fixture', FIXTURES)):
        if not isinstance(params.get(name), str) or params[name] not in choices:
            raise ValueError(f'{name} requires an explicit implemented choice')
    for name in ('device', 'dtype'):
        if not isinstance(params.get(name), str) or not params[name].strip():
            raise ValueError(f'{name} requires an explicit selection')
    dtype = {'fp32': 'float32', 'bf16': 'bfloat16'}.get(params['dtype'], params['dtype'])
    if dtype not in ('float32', 'bfloat16'):
        raise ValueError('dtype is not implemented by the current trainer; select float32 or bfloat16')
    seed = _number(params.get('seed', definition.get('seed', 42)), 'seed', integer=True, positive=None)
    if 'seed' in params and 'seed' in definition and seed != definition['seed']:
        raise ValueError('conflicting runtime probe seeds')
    training = _plain(DEFAULT_TRAINING)
    selected_training = params.get('training', {})
    if not isinstance(selected_training, dict) or selected_training.keys() - training.keys():
        raise ValueError('training contains unsupported runtime probe settings')
    training.update(deepcopy(selected_training))
    for name in ('steps', 'context', 'batch_size', 'gradient_accumulation_steps', 'lora_r', 'lora_alpha'):
        training[name] = _number(training[name], 'training.' + name, integer=True)
    if training['context'] < 2:
        raise ValueError('training.context must permit a causal input and target token')
    training['learning_rate'] = _number(training['learning_rate'], 'training.learning_rate')
    training['lora_dropout'] = _number(training['lora_dropout'], 'training.lora_dropout', positive=False)
    if training['lora_dropout'] >= 1:
        raise ValueError('training.lora_dropout must be in [0, 1)')
    if not isinstance(training['lora_targets'], list) or not training['lora_targets'] or any(
            not isinstance(name, str) or not name.strip() for name in training['lora_targets']):
        raise ValueError('training.lora_targets must explicitly name adapter target modules')
    fixture_config = dict(DEFAULT_FIXTURE_CONFIG)
    selected_config = params.get('fixture_config', {})
    if not isinstance(selected_config, dict) or selected_config.keys() - fixture_config.keys():
        raise ValueError('fixture_config contains unsupported Llama structural fields')
    if params['fixture'] == 'retained' and selected_config:
        raise ValueError('fixture_config applies to tiny_llama; retained architecture comes from its artifact')
    fixture_config.update(selected_config)
    for name in fixture_config:
        fixture_config[name] = _number(fixture_config[name], 'fixture_config.' + name, integer=True)
    hidden, heads = fixture_config['hidden_size'], fixture_config['num_attention_heads']
    if (hidden % heads or (hidden // heads) % 2 or hidden // heads < 2
            or heads % fixture_config['num_key_value_heads']):
        raise ValueError('Llama attention dimensions require divisible heads and an even rotary head dimension')
    if fixture_config['vocab_size'] < len(TOKENS):
        raise ValueError('fixture vocabulary must represent every fixed tokenizer token')
    if params['fixture'] == 'tiny_llama' and training['context'] > fixture_config['max_position_embeddings']:
        raise ValueError('training context exceeds the selected fixture positional capacity')
    inputs = definition.get('inputs', [])
    if not isinstance(inputs, list) or any(not isinstance(row, dict) for row in inputs):
        raise ValueError('runtime probe inputs must be immutable artifact bindings')
    if params['fixture'] == 'tiny_llama' and inputs:
        raise ValueError('tiny_llama generates its own explicit fixture inputs; select retained for supplied artifacts')
    if params['fixture'] == 'retained':
        if len(inputs) != 2 or sorted(row.get('role', '') for row in inputs) != ['pretrained_model', 'snapshot']:
            raise ValueError('retained probe requires one pretrained_model and one snapshot input artifact')
        for row in inputs:
            if (row.keys() & {'path', 'url', 'file', 'directory', 'source_path'}
                    or not isinstance(row.get('artifact_id'), str) or not row['artifact_id'].strip()
                    or not isinstance(row.get('sha256'), str) or not _SHA256.fullmatch(row['sha256'])
                    or row.get('visibility', 'model') != 'model'):
                raise ValueError('runtime probe inputs require model-visible immutable artifact IDs and SHA256 identities, not path inputs')
    return RuntimeProbeProgram(params['method'], params['objective'], params['device'], dtype, params['fixture'], seed,
        training, fixture_config if params['fixture'] == 'tiny_llama' else {},
        NF4ExecutionPolicy.resolve(params.get('nf4_execution')).to_dict(),
        ComparisonPolicy.resolve(params.get('reload_comparison')).report(), deepcopy(inputs))


def _cancel(cancelled):
    if cancelled():
        raise ExecutionCancelled('runtime probe cancelled')


@contextmanager
def _local_only():
    names = ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY')
    previous = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ[name] = '1'
    constants = None
    prior_offline = None
    try:
        from huggingface_hub import constants
        prior_offline = constants.HF_HUB_OFFLINE
        constants.HF_HUB_OFFLINE = True
        yield
    finally:
        if constants is not None and prior_offline is not None:
            constants.HF_HUB_OFFLINE = prior_offline
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def prepare_fixture(program: RuntimeProbeProgram, output_dir, cancelled=lambda: False):
    """Construct deterministic local fixture artifacts, without training/downloads."""
    _cancel(cancelled)
    if program.fixture != 'tiny_llama':
        raise ValueError('prepare_fixture implements the selected tiny_llama fixture only')
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=False)
    with _local_only():
        import torch
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import LlamaConfig, LlamaForCausalLM, PreTrainedTokenizerFast
        backend = Tokenizer(models.WordLevel({token: index for index, token in enumerate(TOKENS)}, unk_token='<unk>'))
        backend.pre_tokenizer = pre_tokenizers.Whitespace()
        tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, eos_token='<eos>', pad_token='<pad>', unk_token='<unk>')
        tokenizer.chat_template = "{% for message in messages %}{{ message['role'] + ': ' + message['content'] + ' ' }}{% if message['role'] == 'assistant' %}{{ eos_token }}{% endif %}{% endfor %}"
        model_dir = root / 'model'
        tokenizer.save_pretrained(model_dir)
        config = LlamaConfig(**_plain(program.fixture_config), attention_dropout=0.,
                             bos_token_id=1, eos_token_id=1, pad_token_id=0)
        _cancel(cancelled)
        with torch.random.fork_rng(devices=[]), torch.device('cpu'):
            torch.random.default_generator.manual_seed(program.seed)
            model = LlamaForCausalLM(config).float()
            model.save_pretrained(model_dir)
        del model
        _cancel(cancelled)
        archive = root / 'model.tar'
        with tarfile.open(archive, 'w', format=tarfile.PAX_FORMAT) as bundle:
            for source in sorted(model_dir.iterdir()):
                _cancel(cancelled)
                info = bundle.gettarinfo(str(source), arcname=source.name)
                info.mtime, info.uid, info.gid, info.uname, info.gname, info.mode = 0, 0, 0, '', '', 0o644
                with source.open('rb') as handle:
                    bundle.addfile(info, handle)
        rows = []
        for index in range(5):
            _cancel(cancelled)
            row = {'source_record_id': f'runtime-fixture:{index}',
                   'partition': 'train' if index < 3 else 'validation' if index == 3 else 'test'}
            if program.objective == 'causal_lm':
                row['text'] = ('pressure high yes\n', 'pressure normal no\n', 'sensor ready yes\n',
                               'pressure low no\n', 'sensor ready no\n')[index]
            else:
                row.update(messages=[{'role': 'user', 'content': 'pressure high'}, {'role': 'assistant', 'content': 'yes'}])
                if program.objective == 'dpo':
                    row['rejected'] = 'no'
            rows.append(row)
        snapshot = root / 'snapshot.jsonl'
        snapshot.write_text(''.join(json.dumps(row, sort_keys=True, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
        inputs, paths = [], {}
        for role, source in (('pretrained_model', archive), ('snapshot', snapshot)):
            identity = 'runtime-fixture-' + role
            inputs.append({'artifact_id': identity, 'role': role, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
            paths[identity] = str(source)
        from tinystudio.core.file_manifest import directory_manifest
        return {'inputs': inputs, 'input_paths': paths, 'fixture': {'kind': 'tiny_llama', 'seed': program.seed,
            'model_config': config.to_dict(), 'tokenizer_vocabulary': list(TOKENS),
            'model_files': directory_manifest(model_dir), 'artifacts': deepcopy(inputs),
            'counts': {'train': 3, 'validation': 1, 'test': 1},
            'count_scope': 'Fixed diagnostic fixture composition; not a scientific sample-size requirement.'}}


def _safe(value):
    if type(value) is int and abs(value) > MAX_EXACT_INTEGER:
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return {'invalid_number': repr(value)}
    if isinstance(value, (dict, MappingProxyType)):
        return {str(key): _safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(child) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _write_report(paths, report):
    temporary = paths.report.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(_safe(report), allow_nan=False, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(paths.report)


def _observed_evidence(manifest, inputs, result=None):
    quantization = manifest.get('quantization', {})
    return {'optimization': deepcopy(quantization.get('optimization')),
            'nf4_settings': deepcopy(quantization.get('settings')),
            'execution_policy': deepcopy(quantization.get('execution_policy')),
            'execution_configuration': deepcopy(quantization.get('execution_configuration')),
            'runtime_verification': deepcopy(manifest.get('runtime_verification')),
            'source_inputs': deepcopy(inputs), 'training_config': deepcopy(manifest.get('config')),
            'measured_steps': {'trainer_result': (result or {}).get('metrics', {}).get('steps'),
                               'retained_manifest': manifest.get('metrics', {}).get('steps')}}


def _retain_failed_training(paths, report, artifacts):
    """Retain known producer outputs without publishing a qualified model.

    The producer writes pretrained.json after training and saving, but returns
    its artifact list only after reload succeeds. Inputs and checkpoints are
    deliberately outside this inventory; absent outputs imply no completion.
    """
    from tinystudio.core.file_manifest import directory_manifest

    if not paths.training.exists():
        return
    if paths.training.is_symlink() or not paths.training.is_dir():
        raise ValueError('partial training evidence requires an owned regular directory')
    manifest_path = paths.training / 'pretrained.json'
    manifest = None
    if manifest_path.is_symlink():
        raise ValueError('partial training evidence must not contain symbolic links')
    if manifest_path.is_file():
        retained = {**artifact(manifest_path, 'pretrained_manifest'), 'visibility': 'evaluation'}
        artifacts.append(retained)
        try:
            manifest = json.loads(manifest_path.read_text())
            observed = _observed_evidence(manifest, report['scope'].get('inputs', []))
            report['evidence'].update(observed)
        except (ValueError, TypeError, AttributeError) as exc:
            report['diagnostics'].append({'stage': report['stage'], 'code': 'training_manifest_unreadable',
                                          'type': type(exc).__name__, 'message': str(exc)})
            manifest = None
        report['evidence']['pretrained_manifest_sha256'] = retained['sha256']
    if report['receipt'] is not None:
        # A failed measured reload supersedes any earlier manifest receipt.
        report['evidence']['runtime_verification'] = deepcopy(report['receipt'])
    completed = report['evidence']['completed_producer_stages'] = []
    if manifest is not None and (manifest.get('format'), manifest.get('output_kind')) == (
            'tinystudio.pretrained.v2', 'quantized_adapter'):
        steps = manifest.get('metrics', {}).get('steps')
        if type(steps) is int and steps > 0:
            completed.append({'stage': 'optimizer_training', 'status': 'completed', 'steps': steps,
                              'basis': 'pretrained.json'})
        if manifest.get('trained_directory') == 'adapter' and (paths.training / 'adapter').is_dir():
            if directory_manifest(paths.training / 'adapter') == manifest.get('trained_files'):
                completed.append({'stage': 'output_save', 'status': 'completed', 'directory': 'adapter',
                                  'basis': 'pretrained.json:trained_files'})

    sources = []
    for name in ('pretrained.json', 'partitions.json', 'nf4-policy.json', 'reload-probes.safetensors',
                 'runtime-verification.json', 'adapter', 'quantized-base'):
        path = paths.training / name
        candidates = sorted(path.rglob('*')) if path.is_dir() and not path.is_symlink() else [path]
        for source in candidates:
            if source.is_symlink():
                raise ValueError('partial training evidence must not contain symbolic links')
            if source.is_file():
                sources.append(source)
    if not sources:
        return
    target = paths.partial_training_archive
    inventory = []
    with tarfile.open(target, 'w') as bundle:
        for source in sources:
            name = source.relative_to(paths.training).as_posix()
            info = bundle.gettarinfo(str(source), arcname=name)
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, '', ''
            with source.open('rb') as handle:
                bundle.addfile(info, handle)
                handle.seek(0)
                checksum = hashlib.file_digest(handle, 'sha256').hexdigest()
            inventory.append({'path': name, 'sha256': checksum})
    with target.open('rb') as handle:
        checksum = hashlib.file_digest(handle, 'sha256').hexdigest()
    artifacts.append({'name': target.name, 'path': str(target), 'media_type': 'application/x-tar',
        'sha256': checksum, 'role': 'runtime_probe_partial_training', 'visibility': 'evaluation'})
    report['evidence']['retained_training_files'] = inventory


def _verify_evidence(manifest, result, program, inputs, selected):
    def require(condition, message):
        if not condition:
            raise ValueError('runtime measurement is not verified: ' + message)

    require(result.get('qualification', {}).get('status') == 'completed', 'training helper did not complete')
    measured_steps = result.get('metrics', {}).get('steps')
    retained_steps = manifest.get('metrics', {}).get('steps')
    require(type(measured_steps) is int and type(retained_steps) is int and
            measured_steps == retained_steps == program.training['steps'],
            'completed optimizer steps differ from the selected training steps')
    require((manifest.get('format'), manifest.get('output_kind'), manifest.get('update_strategy'), manifest.get('training_type'),
             manifest.get('objective')) == ('tinystudio.pretrained.v2', 'quantized_adapter', 'qlora', 'qlora', program.objective),
            'retained output does not match the selected NF4 objective')
    config = manifest.get('config', {})
    require(config.get('device') == selected['device'] and config.get('dtype') == selected['dtype']
            and config.get('seed') == program.seed and config.get('objective') == program.objective
            and config.get('update_strategy') == 'qlora', 'actual training configuration differs from the selection')
    for name, expected in _plain(program.training).items():
        retained_name = {'steps': 'max_steps', 'context': 'max_length'}.get(name, name)
        require(config.get(retained_name) == expected, 'actual training.' + name + ' differs from the selection')
    quantization = manifest.get('quantization', {})
    settings, optimization = quantization.get('settings', {}), quantization.get('optimization', {})
    require(quantization.get('format') == 'tinystudio.nf4-base.v1' and isinstance(settings.get('modules'), int)
            and not isinstance(settings.get('modules'), bool) and settings['modules'] > 0
            and settings.get('quant_type') == 'nf4' and settings.get('storage_dtype') == 'uint8'
            and settings.get('double_quantization') is True
            and settings.get('compute_dtypes') == [selected['dtype']], 'actual packed NF4 settings were not demonstrated')
    before, after = optimization.get('adapter_before_sha256'), optimization.get('adapter_after_sha256')
    require(isinstance(before, str) and bool(_SHA256.fullmatch(before)) and isinstance(after, str)
            and bool(_SHA256.fullmatch(after)) and before != after and optimization.get('adapter_changed') is True,
            'changed adapters were not demonstrated by distinct before/after identities')
    require(optimization.get('optimizer_parameters_verified') is True, 'optimizer adapter-only membership was not verified')
    require(optimization.get('frozen_base_verified') is True, 'frozen NF4 base was not verified after training')
    require(quantization.get('execution_policy') == _plain(program.nf4_execution), 'NF4 execution policy differs from the selection')
    execution = quantization.get('execution_configuration', {})
    require(isinstance(execution, dict) and execution.get('policy') == _plain(program.nf4_execution)
            and execution.get('device') == selected['device'], 'measured NF4 execution configuration differs from the selection')
    verification = manifest.get('runtime_verification', {})
    require(verification.get('status') == 'passed', 'retained policy reload did not pass')
    require(verification.get('comparison_policy') == _plain(program.reload_comparison), 'reload comparison differs from the explicit policy')
    require(manifest.get('reload_comparison') == _plain(program.reload_comparison), 'published reload comparison differs from the selection')
    observed = verification.get('observed_runtime', {})
    require(observed.get('device') == selected['device'] and observed.get('dtype') == selected['dtype'], 'reload used a different device or precision')
    required_checks = ('retained_base_files', 'retained_adapter_files', 'nf4_settings', 'base_state',
                       'adapter_precision', 'adapter_preserved_base_state', 'model_placement')
    require(all(verification.get('checks', {}).get(name) is True for name in required_checks), 'reload state/precision/placement checks are incomplete')
    probes = verification.get('probes', {})
    rows = probes.get('probes', [])
    require(probes.get('status') == 'passed' and probes.get('comparison_policy') == _plain(program.reload_comparison)
            and isinstance(rows, list) and len(rows) == 3 and all(row.get('passed') is True
                and row.get('finite') is True and row.get('device_matches') is True
                and row.get('actual_device') == selected['device'] and row.get('requested_device') == selected['device'] for row in rows)
            and {row.get('index') for row in rows} == {0, 1, 2},
            'three finite saved-policy probes on the selected device were not verified')
    for item in inputs:
        observed_input = manifest.get('model_input' if item['role'] == 'pretrained_model' else 'snapshot_input', {})
        require(observed_input.get('sha256') == item['sha256'] and observed_input.get('artifact_id') == item['artifact_id'],
                'retained source identities differ from the bound input artifacts')
    return _observed_evidence(manifest, inputs, result)


def _qualification(report):
    return {'kind': 'runtime_compatibility_probe', 'status': report['status'], 'qualified': report['status'] == 'verified',
        'scope': deepcopy(report['scope']), 'generalization': 'Only the selected inputs/configuration and measured training/reload operation; other models, platforms and operations remain unprobed.'}


def execute_runtime_probe(spec, output_dir, emit, cancelled):
    """Return a normal Lab result while qualification reports actual outcomes."""
    _cancel(cancelled)
    paths = RuntimeProbePaths(Path(output_dir))
    paths.root.mkdir(parents=True, exist_ok=True)
    if any(paths.root.iterdir()):
        raise ValueError('runtime probe requires an empty owned output directory')
    raw_definition = spec.get('definition', {}) if isinstance(spec, dict) else spec
    definition = raw_definition if isinstance(raw_definition, dict) else {}
    report = {'format': FORMAT, 'method_version': METHOD_VERSION, 'implementation_sha256': implementation_sha256(),
        'integer_encoding': 'safe_numbers_or_decimal_strings',
        'status': 'pending', 'stage': 'selection_validation', 'requested_configuration': _safe({
            'parameters': definition.get('parameters'), 'seed': definition.get('seed'), 'inputs': definition.get('inputs', [])}),
        'configuration': None, 'prerequisites': None, 'stages': [], 'diagnostics': [], 'error': None,
        'receipt': None, 'scope': {}, 'evidence': {}}
    context_spec = spec if isinstance(spec, dict) else {}
    runtime_context = context_spec.get('_runtime_context', {})
    runtime_context = runtime_context if isinstance(runtime_context, dict) else {}
    report['execution_context'] = _safe({'research_identity': context_spec.get('research_identity'),
        'definition_id': context_spec.get('definition_id'), 'revision': context_spec.get('revision'),
        'runtime': context_spec.get('runtime'), 'execution': definition.get('execution'),
        'spec_digest': runtime_context.get('spec_digest'), 'profile': runtime_context.get('profile', {})})
    child_result = None
    fixture_artifacts = []
    partial_training_artifacts = []

    def record_cancellation(exc):
        report['status'] = 'cancelled'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        report['qualification'] = _qualification(report)
        report['diagnostics'].append({'stage': report['stage'], 'code': 'cancelled', **report['error']})
        try:
            _write_report(paths, report)
        except OSError:
            # Failure to retain an interrupted report must not replace the
            # caller's cancellation with a different exception.
            pass

    def complete_stage():
        if report['stages'] and report['stages'][-1]['status'] == 'started':
            report['stages'][-1]['status'] = 'completed'
            emit({'event': 'runtime_probe_stage', **report['stages'][-1]})

    def begin(stage):
        _cancel(cancelled)
        complete_stage()
        report['stage'] = stage
        report['stages'].append({'stage': stage, 'status': 'started'})
        _write_report(paths, report)
        emit({'event': 'runtime_probe_stage', 'stage': stage, 'status': 'started'})

    try:
        begin('selection_validation')
        program = validate_selection(raw_definition)
        report['configuration'] = program.to_dict()
        report['scope'] = {**program.to_dict(), 'generalizes_to_other_models': False}
        begin('prerequisite_inspection')
        prerequisites = RuntimeCompatibility.inspect(device=program.device, dtype=program.dtype).to_dict()
        report['prerequisites'] = prerequisites
        if prerequisites.get('status') == 'unavailable':
            report['status'] = 'unavailable'
            report['error'] = {'type': 'RuntimeUnavailable', 'message': prerequisites.get('reason', 'selected runtime prerequisites are unavailable')}
            complete_stage()
        else:
            if prerequisites.get('status') != 'unprobed':
                raise ValueError('prerequisite inspection returned an unsupported status; no qualification measurement ran')
            selected = prerequisites.get('selection') or {'device': program.device, 'dtype': program.dtype}
            report['scope'].update(device=selected['device'], dtype=selected['dtype'])
            with _local_only():
                if program.fixture == 'tiny_llama':
                    begin('fixture_preparation')
                    bound = prepare_fixture(program, paths.fixture, cancelled)
                    report['fixture'] = bound['fixture']
                    inputs, input_paths = bound['inputs'], bound['input_paths']
                    report['scope']['inputs'] = deepcopy(inputs)
                    report['fixture']['retained_artifacts'] = []
                    for item in inputs:
                        _cancel(cancelled)
                        role = 'runtime_fixture_model' if item['role'] == 'pretrained_model' else 'runtime_fixture_snapshot'
                        retained = {**artifact(Path(input_paths[item['artifact_id']]), role,
                            'application/x-tar' if item['role'] == 'pretrained_model' else 'application/x-ndjson'),
                            'name': 'runtime-fixture-model.tar' if item['role'] == 'pretrained_model' else 'runtime-fixture-snapshot.jsonl',
                            'visibility': 'evaluation'}
                        if retained['sha256'] != item['sha256']:
                            raise ValueError('generated fixture changed before its input identity was retained')
                        fixture_artifacts.append(retained)
                        report['fixture']['retained_artifacts'].append({'role': role, 'name': retained['name'],
                            'sha256': retained['sha256'], 'input_artifact_id': item['artifact_id'], 'input_role': item['role']})
                else:
                    begin('retained_input_binding')
                    from .compatibility import resolve_role
                    inputs = _plain(program.inputs)
                    for role in ('pretrained_model', 'snapshot'):
                        _cancel(cancelled)
                        resolve_role(spec, role)
                    input_paths = deepcopy(spec.get('input_paths', {}))
                report['scope']['inputs'] = deepcopy(inputs)
                child = {'definition': {'seed': program.seed, 'learning': {'initialization': 'pretrained',
                    'update_strategy': 'qlora', 'objective': program.objective, **selected},
                    'parameters': {**_plain(program.training), 'nf4_execution': _plain(program.nf4_execution),
                                   'reload_comparison': _plain(program.reload_comparison)}, 'inputs': inputs},
                    'input_paths': input_paths, 'run': deepcopy(spec.get('run', {})),
                    'compatibility': deepcopy(spec.get('compatibility', {}))}
                begin('training_and_reload')
                from .compatibility import execute_pretrained
                child_result = execute_pretrained(child, paths.training, emit, cancelled)
                if child_result.get('qualification', {}).get('status') == 'cancelled':
                    raise ExecutionCancelled('pretrained helper returned cancellation; runtime probe is not verified')
                _cancel(cancelled)
                begin('evidence_verification')
                manifest_path = paths.training / 'pretrained.json'
                manifest = json.loads(manifest_path.read_text())
                report['evidence'] = _observed_evidence(manifest, inputs, child_result)
                report['receipt'] = deepcopy(manifest.get('runtime_verification'))
                report['evidence']['pretrained_manifest_sha256'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                _verify_evidence(manifest, child_result, program, inputs, selected)
                _cancel(cancelled)
                report['status'] = 'verified'
                complete_stage()
    except ExecutionCancelled as exc:
        record_cancellation(exc)
        raise
    except Exception as exc:
        report['status'] = 'unavailable' if isinstance(exc, ImportError) else 'failed'
        report['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        if getattr(exc, 'receipt', None) is not None:
            report['receipt'] = deepcopy(exc.receipt)
        if report['stages']:
            report['stages'][-1]['status'] = 'failed'
        if child_result is None and report['stage'] == 'training_and_reload':
            try:
                _retain_failed_training(paths, report, partial_training_artifacts)
            except Exception as retention_error:
                report['diagnostics'].append({'stage': report['stage'], 'code': 'training_evidence_retention_failed',
                    'type': type(retention_error).__name__, 'message': str(retention_error)})
    qualified = report['status'] == 'verified'
    report['qualification'] = _qualification(report)
    if report['error']:
        report['diagnostics'].append({'stage': report['stage'], 'code': report['status'], **report['error']})
    try:
        _cancel(cancelled)
    except ExecutionCancelled as exc:
        record_cancellation(exc)
        raise
    _write_report(paths, report)
    emit({'event': 'runtime_probe_complete', 'status': report['status'], 'qualified': qualified})
    return {'records': report['stages'], 'metrics': {'runtime_probe_status': report['status'], 'runtime_probe_verified': qualified,
        'training_steps': (child_result.get('metrics', {}).get('steps') if child_result else
                           next((row['steps'] for row in report['evidence'].get('completed_producer_stages', [])
                                 if row['stage'] == 'optimizer_training'), None))},
        'artifacts': [{**artifact(paths.report, 'runtime_probe'), 'visibility': 'evaluation'},
                      *fixture_artifacts,
                      *partial_training_artifacts,
                      *(child_result.get('artifacts', []) if child_result else [])],
        'qualification': _safe(report['qualification'])}
