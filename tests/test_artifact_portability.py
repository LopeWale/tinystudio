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
