"""Accepted training outputs through existing Models and Playground APIs.

Run --noconftest: UUID-owned PostgreSQL, Redis and files only. Native CPU full
and LoRA workflows are qualified here; real Linux NF4 execution is separate.
"""
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
import torch

from tests.test_lab_b_workflow import database, integrated
from tests.test_lab_qlora_workflow import pretrained_fixture
from tinystudio.inference.engine import LocalEngine, set_engine
from tinystudio.lab import service as laboratory
from tinystudio.lab.runner import Runner
from tinystudio.storage import storage


def _greedy(model, inputs, eos, count):
    """Independent uncached greedy loop, rather than another generate call."""
    batch = {key: value.clone() for key, value in inputs.items()}
    result = []
    for _ in range(count):
        with torch.no_grad():
            token = model(**batch).logits[:, -1].argmax(-1, keepdim=True)
        result.append(int(token.item()))
        if result[-1] == eos:
            break
        batch['input_ids'] = torch.cat((batch['input_ids'], token), dim=-1)
        batch['attention_mask'] = torch.cat((batch['attention_mask'], torch.ones_like(token)), dim=-1)
    return result


@pytest.mark.parametrize('strategy', ['full', 'lora'])
def test_registered_training_to_models_playground_unload_and_reload(
        integrated, pretrained_fixture, monkeypatch, strategy):
    client, prefix, other_prefix, app, _, root, factory = integrated
    # Use the actual shared storage function across lab publication and serving;
    # the older B fixture overrides only the lab service's store location.
    monkeypatch.setattr(laboratory, 'storage', storage)
    monkeypatch.setenv('TINYSTUDIO_DEVICE', 'cpu')
    monkeypatch.setenv('TINYSTUDIO_DTYPE', 'float32')
    local = LocalEngine()
    set_engine(local)
    project_prefix = prefix.rsplit('/', 1)[0]
    other_project = other_prefix.rsplit('/', 1)[0]
    slug = project_prefix.rsplit('/', 1)[1]
    spec = pretrained_fixture.specification('sft')
    definition = spec['definition']
    definition['learning']['update_strategy'] = strategy
    definition.update(name='Retained ' + strategy, engine='learning.pretrained')
    definition['execution'] = {'profile': 'trusted_native', 'backend': 'native', 'max_seconds': 120}
    references = []
    for binding in definition['inputs']:
        path = Path(spec['input_paths'][binding['artifact_id']])
        uploaded = client.post(prefix + '/inputs', files={'file': (path.name, path.read_bytes(), 'application/octet-stream')})
        assert uploaded.status_code == 201, uploaded.text
        row = uploaded.json()
        references.append({'artifact_id': row['id'], 'role': binding['role'], 'sha256': row['sha256']})
    definition['inputs'] = references
    target = client.post(prefix + '/targets', json={'name': 'native-retained-serving'}).json()
    model_id = None
    try:
        with TestClient(app, headers={'Authorization': 'Bearer ' + target['token']}) as machine:
            runner = Runner('http://127.0.0.1', target['id'], target['token'], client=machine)
            runner.advertise()
            saved = client.post(prefix + '/definitions', json={'definition': definition})
            assert saved.status_code == 201, saved.text
            submitted = client.post(prefix + '/runs', json={'definition_id': saved.json()['id'], 'revision': 1,
                'target_id': target['id'], 'idempotency_key': 'model-serving-' + strategy})
            assert submitted.status_code == 202, submitted.text
            run_id = submitted.json()['id']
            assert runner.run_once(run_id=run_id)
        run = client.get(prefix + '/runs/' + run_id).json()
        assert run['state'] == 'succeeded', run.get('error')
        bundle = next(row for row in run['result']['artifacts'] if row['role'] == 'pretrained_model_bundle')
        model_id = 'lab:' + bundle['id']
        inventory = client.get(project_prefix + '/models')
        assert inventory.status_code == 200
        model = next(row for row in inventory.json() if row['id'] == model_id)
        assert model['status'] == 'down' and model['origin'] == 'laboratory'
        assert 'r1' in model['alias'] and run_id[:8] in model['alias']
        assert not any(row['id'] == model_id for row in client.get(other_project + '/models').json())
        up = client.post(project_prefix + '/models/' + model_id + '/up')
        assert up.status_code == 200, up.text
        assert next(row for row in client.get(project_prefix + '/models').json() if row['id'] == model_id)['status'] == 'up'
        loaded = local.load(model_id, slug)
        assert loaded.resolved.retained_source.artifact_id == bundle['id']
        messages = [{'role': 'user', 'content': 'pressure high'}]
        expected = _greedy(loaded.model, loaded._encode(messages), loaded.tokenizer.eos_token_id, 3)
        expected_text = loaded.tokenizer.decode(expected, skip_special_tokens=True).strip()
        generated = local.generate(model_id, messages, 3, 0.0, project_slug=slug)
        assert generated['content'] == expected_text and generated['completion_tokens'] == len(expected)
        response = client.post(project_prefix + '/playground/chat', json={
            'model': model_id, 'messages': messages, 'temperature': 0, 'max_tokens': 3})
        assert response.status_code == 200, response.text
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ') and line[6:] != '[DONE]']
        assert ''.join(event.get('delta', '') for event in events).strip() == expected_text
        assert not any('error' in event for event in events)
        assert any(event.get('done') and event['seconds'] >= 0 for event in events)
        # Playground intentionally narrows completion SSE to done + seconds;
        # its durable trace, rather than this UI frame, retains token usage.
        from sqlalchemy import select
        from tinystudio.db.models import Trace
        with factory() as db:
            traces = list(db.scalars(select(Trace).where(Trace.model == model_id, Trace.origin == 'playground')))
            assert len(traces) == 1
            trace = traces[0]
            assert trace.project_id == loaded.resolved.retained_source.project_id
            assert trace.response == expected_text
            assert trace.prompt_tokens == loaded._encode(messages)['input_ids'].shape[1]
            assert trace.completion_tokens == len(loaded.tokenizer(expected_text, add_special_tokens=False)['input_ids'])
        # The cached policy must not allow another project's direct or qualified access.
        assert client.post(other_project + '/models/' + model_id + '/up').status_code == 400
        # An idempotent unload of B's empty slot cannot retire A's runner.
        assert client.post(other_project + '/models/' + model_id + '/down').status_code == 200
        assert client.post(other_project + '/models/' + slug + '~' + model_id + '/up').status_code == 400
        assert client.post(other_project + '/models/' + slug + '~' + model_id + '/down').status_code == 400
        assert model_id in local.loaded_ids(slug)
        assert client.post(project_prefix + '/models/' + model_id + '/down').status_code == 200
        assert model_id not in local.loaded_ids(slug)
        again = client.post(project_prefix + '/models/' + model_id + '/up')
        assert again.status_code == 200, again.text
        assert local.generate(model_id, messages, 3, 0, project_slug=slug)['content'] == expected_text
        downloaded = client.get(prefix + '/artifacts/' + bundle['id'])
        assert hashlib.sha256(downloaded.content).hexdigest() == bundle['sha256']
        assert client.post(project_prefix + '/models/' + model_id + '/down').status_code == 200
        # A verified corruption counterexample must not reach a tensor/Hub loader.
        from tinystudio.lab.models import LabArtifact
        with factory() as db:
            identity = db.get(LabArtifact, bundle['id'])
            key = identity.storage_key
        corrupt = root / 'corrupted-model'
        corrupt.write_bytes(b'corrupted retained object')
        storage().upload_file(corrupt, key)
        def no_fallback(*args, **kwargs):
            pytest.fail('corrupt retained bytes reached a model or Hub loader')
        monkeypatch.setattr('transformers.AutoModelForCausalLM.from_pretrained', no_fallback)
        rejected = client.post(project_prefix + '/models/' + model_id + '/up')
        assert rejected.status_code == 400 and 'checksum or size mismatch' in rejected.text
        assert model_id not in local.loaded_ids(slug)
    finally:
        if model_id:
            local.unload(model_id, slug)
        set_engine(None)
