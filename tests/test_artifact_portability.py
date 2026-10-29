"""Cross-host artifact contracts with real manifests and an in-memory S3 boundary."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import json
import shutil

import pytest

from tests.test_storage import MemoryS3
from tinystudio.db.models import Experiment
from tinystudio.services import jobs
from tinystudio.storage import ArtifactStorage, StorageError
from tinystudio.training.train import TrainConfig


@pytest.fixture
def hosts(tmp_path, monkeypatch):
    backend = ArtifactStorage(client=MemoryS3(), bucket='isolated-artifact-test')
    cfg = SimpleNamespace(data_dir=tmp_path / 'host-a')
    monkeypatch.setattr(jobs, 'config', lambda: cfg)
    monkeypatch.setattr(jobs, 'storage', lambda: backend)
    return cfg, backend, tmp_path / 'host-b'


def experiment(number=1, ancestor=None):
    cfg = TrainConfig(base_model='org/base', refinetune_from=ancestor)
    return SimpleNamespace(id=f'exp-{number}', number=number, state='succeeded', alias=f'ft-job-{number}',
                           artifact_dir=f'projects/p/experiments/j/{number}', config=cfg.as_dict(),
                           status={}, job=SimpleNamespace(project_id='p', snapshot_name='frozen'))


def write_adapter(path, content):
    (path / 'adapter').mkdir(parents=True)
    (path / 'adapter' / 'adapter_config.json').write_text('{}')
    (path / 'adapter' / 'adapter_model.safetensors').write_bytes(content)


def test_model_resolver_uses_child_owned_base_after_ancestor_deletion(hosts, monkeypatch):
    from tinystudio.services import models
    cfg, backend, host_b = hosts
    child = experiment(2, 'deleted-parent/1')
    path = jobs.exp_path(child)
    write_adapter(path, b'child-adapter')
    base = cfg.data_dir / 'standalone-base'
    base.mkdir(parents=True)
    (base / 'config.json').write_text('{}')
    (base / 'model.safetensors').write_bytes(b'retained-independent-base')
    child.status = {'weights_source': str(base)}
    child.status = jobs.publish_existing_experiment(child, path)
    shutil.rmtree(cfg.data_dir)
    cfg.data_dir = host_b
    monkeypatch.setattr(jobs, 'find_experiment', lambda *args: pytest.fail('deleted ancestor must not be resolved'))
    result = models.resolve(SimpleNamespace(scalar=lambda query: child), SimpleNamespace(id='p'), 'job/2')
    assert Path(result.base_model).is_relative_to(host_b)
    assert (Path(result.base_model) / 'model.safetensors').read_bytes() == b'retained-independent-base'


def test_warm_experiment_cache_cannot_hide_missing_remote_member(hosts):
    from tinystudio.storage import ArtifactNotFound
    cfg, backend, _ = hosts
    exp = experiment()
    path = jobs.exp_path(exp)
    write_adapter(path, b'retained-adapter')
    exp.status = jobs.publish_existing_experiment(exp, path)
    manifest = backend.read_manifest(exp.status['artifact_prefix'])
    member = next(item for item in manifest['files'] if item['path'].endswith('adapter_model.safetensors'))
    backend.delete(member['key'])
    assert (path / 'adapter' / 'adapter_model.safetensors').is_file()
    with pytest.raises(ArtifactNotFound):
        jobs.ensure_experiment_artifacts(exp)


def test_refinement_ancestry_survives_empty_host_and_lost_merge_cache(hosts, monkeypatch):
    cfg, backend, host_b = hosts
    parent, refined = experiment(), experiment(2, 'job/1')
    for exp, content in ((parent, b'first-adapter'), (refined, b'second-adapter')):
        path = jobs.exp_path(exp)
        write_adapter(path, content)
        exp.status = {'output': str(path / 'adapter'), 'weights_source': '/obsolete/host/base'}
        if exp is refined:
            base = cfg.data_dir / 'standalone-base'
            base.mkdir(parents=True)
            (base / 'config.json').write_text('{}')
            (base / 'model.safetensors').write_bytes(b'base+first-adapter')
            exp.status['weights_source'] = str(base)
        exp.status = jobs.publish_existing_experiment(exp, path)
        assert exp.status['weights_source'] == 'org/base'
        assert exp.status['output'] == 'adapter'
    monkeypatch.setattr(jobs, 'find_experiment', lambda db, project, ref: {'job/1': parent, 'job/2': refined}[ref])
    merges = []
    def merge(path, destination, base, source):
        adapter = (path / 'adapter' / 'adapter_model.safetensors').read_bytes()
        base_bytes = b'base' if source == 'org/base' else (Path(source) / 'model.safetensors').read_bytes()
        merges.append((adapter, base_bytes))
        destination.mkdir(parents=True)
        (destination / 'config.json').write_text('{}')
        (destination / 'model.safetensors').write_bytes(base_bytes + b'+' + adapter)
    monkeypatch.setattr(jobs, 'export_merged', merge)
    shutil.rmtree(cfg.data_dir)
    cfg.data_dir = host_b
    assert not host_b.exists()
    weights = jobs.parent_weights(None, SimpleNamespace(id='p'), 'job/2')
    assert weights.is_relative_to(host_b)
    assert (weights / 'model.safetensors').read_bytes() == b'base+first-adapter+second-adapter'
    assert merges == [(b'second-adapter', b'base+first-adapter')]
    assert refined.status['artifact_base_prefix'] == 'models/p/exp-2/base'
    shutil.rmtree(host_b)
    monkeypatch.setattr(jobs, 'export_merged', lambda *args: pytest.fail('published merge must hydrate'))
    assert (jobs.parent_weights(None, SimpleNamespace(id='p'), 'job/2') / 'model.safetensors').read_bytes() == b'base+first-adapter+second-adapter'


def setup_run(monkeypatch, exp):
    class DB:
        def get(self, cls, identifier):
            return exp if cls is Experiment else SimpleNamespace(id='p')
        def add(self, row):
            pass
    @contextmanager
    def session():
        yield DB()
    monkeypatch.setattr(jobs, 'session', session)
    monkeypatch.setattr(jobs.snapshots_svc, 'load_snapshot', lambda *args: ([], []))
    return session


@pytest.mark.parametrize('failure', [RuntimeError('trainer crashed'), KeyboardInterrupt()])
def test_failed_training_retains_diagnostics_and_never_final(hosts, monkeypatch, failure):
    cfg, backend, host_b = hosts
    exp = experiment()
    exp.state = 'pending'
    setup_run(monkeypatch, exp)
    def train(path, *args):
        path.mkdir(parents=True)
        (path / 'trainer_state.json').write_text('{"global_step": 7}')
        raise failure
    monkeypatch.setattr(jobs, 'train_experiment', train)
    result = jobs.run_experiment(exp.id)
    assert result['state'] == exp.state == 'failed'
    assert not result.get('artifact_prefix')
    assert not backend.exists('models/p/exp-1/final/manifest.json')
    assert result['artifact_diagnostics_prefix'] == 'models/p/exp-1/diagnostics'
    shutil.rmtree(cfg.data_dir)
    cfg.data_dir = host_b
    restored = jobs.ensure_experiment_artifacts(exp)
    assert json.loads((restored / 'trainer_state.json').read_text()) == {'global_step': 7}


def test_retention_error_preserves_original_training_failure(hosts, monkeypatch):
    cfg, backend, _ = hosts
    exp = experiment()
    exp.state = 'pending'
    setup_run(monkeypatch, exp)
    def train(path, *args):
        path.mkdir(parents=True)
        (path / 'diagnostic').write_text('available')
        backend.client.fail_upload = True
        raise RuntimeError('original training error')
    monkeypatch.setattr(jobs, 'train_experiment', train)
    result = jobs.run_experiment(exp.id)
    assert result['state'] == 'failed'
    assert result['error'] == 'RuntimeError: original training error'
    assert 'upload failed' in result['artifact_retention_error']
    assert not result.get('artifact_diagnostics_prefix')


def test_local_publication_preserves_existing_status(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'storage', lambda: ArtifactStorage(root=tmp_path / 'local-objects'))
    exp = experiment()
    exp.status = {'output': str(tmp_path / 'adapter'), 'weights_source': '/local/base'}
    assert jobs.publish_existing_experiment(exp, tmp_path) == exp.status
    assert not (tmp_path / 'local-objects').exists()


@pytest.mark.parametrize('fail_upload', [False, True])
def test_import_directory_publishes_before_committing_experiment(hosts, monkeypatch, tmp_path, fail_upload):
    from tinystudio.services import importer, interp
    from tinystudio.db.models import Job, Project
    cfg, backend, host_b = hosts
    root = tmp_path / 'legacy'
    directory = root / 'jobs' / 'job' / 'experiments' / '1'
    write_adapter(directory, b'imported-adapter')
    (root / 'tinystudio.json').write_text('{"name":"legacy"}')
    (directory.parent.parent / 'job.json').write_text('{"snapshot":"frozen"}')
    (directory / 'config.json').write_text('{"base_model":"org/base"}')
    (directory / 'status.json').write_text(json.dumps({'state':'succeeded', 'output':'/old/host/adapter', 'weights_source':'/old/host/base'}))
    project = SimpleNamespace(id='p', slug='legacy')
    added, committed = [], []
    class DB:
        def get(self, cls, identifier):
            return project
        def scalar(self, query):
            return None
        def add(self, row):
            if isinstance(row, Job):
                row.id = 'imported-job'
            if isinstance(row, Experiment):
                row.id = 'imported-exp'
                row.job = next(item for item in added if isinstance(item, Job))
            added.append(row)
        def flush(self):
            pass
    @contextmanager
    def session():
        before = len(added)
        yield DB()
        committed.extend(added[before:])
    monkeypatch.setattr(importer, 'session', session)
    monkeypatch.setattr(importer, 'config', lambda: cfg)
    monkeypatch.setattr(importer.projects_svc, 'create_project', lambda *args: project)
    monkeypatch.setattr(interp, 'ProjectStore', lambda *args: SimpleNamespace())
    backend.client.fail_upload = fail_upload
    if fail_upload:
        with pytest.raises(StorageError, match='upload failed'):
            importer.import_directory(root, link=False, progress=lambda *args: None)
        assert not any(isinstance(row, Experiment) for row in committed)
        return
    summary = importer.import_directory(root, link=False, progress=lambda *args: None)
    assert summary['experiments'] == 1
    imported = next(row for row in committed if isinstance(row, Experiment))
    assert imported.status['artifact_prefix'] == 'models/p/imported-exp/final'
    assert imported.status['output'] == 'adapter'
    assert imported.status['weights_source'] == 'org/base'
    shutil.rmtree(root)
    shutil.rmtree(cfg.data_dir)
    cfg.data_dir = host_b
    restored = jobs.ensure_experiment_artifacts(imported)
    assert (restored / 'adapter' / 'adapter_model.safetensors').read_bytes() == b'imported-adapter'



def test_trained_child_keeps_its_base_after_parent_deletion(hosts, monkeypatch):
    cfg, backend, host_b = hosts
    parent, child = experiment(), experiment(2, 'job/1')
    path = jobs.exp_path(parent)
    (path / 'model').mkdir(parents=True)
    (path / 'model' / 'config.json').write_text('{}')
    (path / 'model' / 'model.safetensors').write_bytes(b'parent-trained-weights')
    parent.status = jobs.publish_existing_experiment(parent, path)
    child.state = 'pending'
    setup_run(monkeypatch, child)
    monkeypatch.setattr(jobs, 'find_experiment', lambda db, project, ref: {'job/1': parent, 'job/2': child}[ref])
    def train(directory, config, train_rows, val_rows, progress, weights, on_log):
        assert (weights / 'model.safetensors').read_bytes() == b'parent-trained-weights'
        write_adapter(directory, b'child-adapter')
        return {'output': str(directory / 'adapter')}
    monkeypatch.setattr(jobs, 'train_experiment', train)
    assert jobs.run_experiment(child.id)['state'] == 'succeeded'
    assert child.status['artifact_base_prefix'] == 'models/p/exp-2/base'
    backend.delete_prefix('models/p/exp-1')
    shutil.rmtree(cfg.data_dir)
    cfg.data_dir = host_b
    monkeypatch.setattr(jobs, 'find_experiment', lambda db, project, ref: {'job/2': child}[ref])
    def merge(directory, destination, base, source):
        assert (Path(source) / 'model.safetensors').read_bytes() == b'parent-trained-weights'
        assert (directory / 'adapter' / 'adapter_model.safetensors').read_bytes() == b'child-adapter'
        destination.mkdir(parents=True)
        (destination / 'config.json').write_text('{}')
        (destination / 'model.safetensors').write_bytes(b'refined-child-weights')
    monkeypatch.setattr(jobs, 'export_merged', merge)
    from tinystudio.services import models
    resolved = models.resolve(SimpleNamespace(scalar=lambda query: child), SimpleNamespace(id='p'), 'job/2')
    assert resolved.kind == 'lora'
    assert (Path(resolved.base_model) / 'model.safetensors').read_bytes() == b'parent-trained-weights'
    assert (resolved.adapter_dir / 'adapter_model.safetensors').read_bytes() == b'child-adapter'
    weights = jobs.parent_weights(None, SimpleNamespace(id='p'), 'job/2')
    assert (weights / 'model.safetensors').read_bytes() == b'refined-child-weights'


def test_remote_missing_publication_never_trusts_local_mock_weights(hosts):
    exp = experiment()
    write_adapter(jobs.exp_path(exp), b'local-only-mock')
    from tinystudio.core.project import ProjectError
    with pytest.raises(ProjectError, match='no published R2 artifacts'):
        jobs.ensure_experiment_artifacts(exp)


def test_remote_training_rejects_host_bound_initial_base(hosts, monkeypatch, tmp_path):
    from tinystudio.core.project import ProjectError
    exp = experiment()
    exp.config['base_model'] = str(tmp_path / 'host-only-model')
    exp.state = 'pending'
    setup_run(monkeypatch, exp)
    monkeypatch.setattr(jobs, 'train_experiment', lambda *args: pytest.fail('must reject before training'))
    with pytest.raises(ProjectError, match='portable Hub base model ID'):
        jobs.run_experiment(exp.id)
    assert exp.state == 'pending'


def test_stopped_training_retains_available_files(hosts, monkeypatch):
    cfg, backend, host_b = hosts
    exp = experiment()
    exp.state = 'running'
    path = jobs.exp_path(exp)
    path.mkdir(parents=True)
    (path / 'trainer_state.json').write_text('{"global_step": 11}')
    setup_run(monkeypatch, exp)
    jobs.mark_stopped([exp.id])
    assert exp.state == 'failed'
    assert exp.status['error'] == 'stopped'
    assert exp.status['artifact_diagnostics_prefix'] == 'models/p/exp-1/diagnostics'
    shutil.rmtree(cfg.data_dir)
    cfg.data_dir = host_b
    assert json.loads((jobs.ensure_experiment_artifacts(exp) / 'trainer_state.json').read_text()) == {'global_step': 11}


def test_missing_published_merge_member_never_replaces_authoritative_manifest(hosts, monkeypatch):
    from tinystudio.storage import ArtifactNotFound
    cfg, backend, _ = hosts
    exp = experiment()
    path = jobs.exp_path(exp)
    write_adapter(path, b'retained-adapter')
    exp.status = jobs.publish_existing_experiment(exp, path)
    merged = path.parent / f'{exp.id}-merged'
    merged.mkdir()
    (merged / 'config.json').write_text('{}')
    (merged / 'model.safetensors').write_bytes(b'complete-merged-weights')
    prefix = 'models/p/exp-1/merged'
    manifest = backend.publish_directory(merged, prefix)
    original_manifest = backend.client.objects[prefix + '/manifest.json']
    weight = next(item for item in manifest['files'] if item['path'] == 'model.safetensors')
    backend.delete(weight['key'])
    (merged / 'model.safetensors').unlink()
    monkeypatch.setattr(jobs, 'find_experiment', lambda *args: exp)
    with pytest.raises(ArtifactNotFound):
        jobs.parent_weights(None, SimpleNamespace(id='p'), 'job/1')
    assert backend.client.objects[prefix + '/manifest.json'] == original_manifest
    assert [item['path'] for item in backend.read_manifest(prefix)['files']] == ['config.json', 'model.safetensors']


def test_unpublished_config_only_merge_cache_is_rebuilt_from_retained_inputs(hosts, monkeypatch):
    cfg, backend, _ = hosts
    exp = experiment()
    path = jobs.exp_path(exp)
    write_adapter(path, b'retained-adapter')
    exp.status = jobs.publish_existing_experiment(exp, path)
    merged = path.parent / f'{exp.id}-merged'
    merged.mkdir()
    (merged / 'config.json').write_text('{"unfinished":true}')
    monkeypatch.setattr(jobs, 'find_experiment', lambda *args: exp)
    merges = []
    def merge(directory, destination, base, source):
        assert (directory / 'adapter' / 'adapter_model.safetensors').read_bytes() == b'retained-adapter'
        merges.append(source)
        destination.mkdir(parents=True)
        (destination / 'config.json').write_text('{}')
        (destination / 'model.safetensors').write_bytes(b'new-complete-merge')
    monkeypatch.setattr(jobs, 'export_merged', merge)
    result = jobs.parent_weights(None, SimpleNamespace(id='p'), 'job/1')
    assert merges == ['org/base']
    assert (result / 'model.safetensors').read_bytes() == b'new-complete-merge'
    assert [item['path'] for item in backend.read_manifest('models/p/exp-1/merged')['files']] == ['config.json', 'model.safetensors']


@pytest.mark.parametrize('step', ['', '5'])
@pytest.mark.parametrize('base_prefix', [None, 'models/p/exp-2/missing-base'])
def test_public_resolver_loads_imported_full_refinement_after_ancestor_deletion(hosts, monkeypatch, step, base_prefix):
    from tinystudio.services import models
    from tinystudio.core.project import ProjectError
    cfg, backend, host_b = hosts
    exp = experiment(2, 'job/1')
    exp.config.update(training_type='full', update_strategy='full')
    path = jobs.exp_path(exp)
    (path / 'model').mkdir(parents=True)
    (path / 'model' / 'config.json').write_text('{}')
    (path / 'model' / 'model.safetensors').write_bytes(b'imported-standalone-refinement')
    if step:
        checkpoint = path / 'checkpoints' / 'checkpoint-5'
        checkpoint.mkdir(parents=True)
        (checkpoint / 'config.json').write_text('{}')
        (checkpoint / 'model.safetensors').write_bytes(b'imported-standalone-refinement')
    exp.status = jobs.publish_existing_experiment(exp, path)
    assert not exp.status.get('artifact_base_prefix')
    if base_prefix:
        exp.status['artifact_base_prefix'] = base_prefix
    backend.delete_prefix('models/p/exp-1')
    shutil.rmtree(cfg.data_dir)
    cfg.data_dir = host_b
    def absent_ancestor(*args):
        raise ProjectError('deleted ancestor must not be needed for a standalone model')
    monkeypatch.setattr(jobs, 'find_experiment', absent_ancestor)
    db = SimpleNamespace(scalar=lambda query: exp)
    resolved = models.resolve(db, SimpleNamespace(id='p'), 'job/2' + (f'@{step}' if step else ''))
    assert resolved.kind == 'full'
    assert (resolved.model_dir / 'model.safetensors').read_bytes() == b'imported-standalone-refinement'
