"""Project deletion respects restrictive PostgreSQL laboratory dependencies."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from tests.conftest import _test_database_url
from tinystudio.core.project import ProjectError
from tinystudio.db.models import ApiKey, Base, Dataset, Project, Repository, Setting, Task, User
from tinystudio.lab.models import LabArtifact, LabAttempt, LabDefinition, LabDispatch, LabEvent, LabOperation, LabRevision, LabRun, LabTarget
from tinystudio.services import projects


@pytest.fixture
def deletion_db(tmp_path, monkeypatch):
    schema = 'project_delete_' + uuid4().hex
    # Dataset COPY uses raw SQL, so its connection needs the isolated schema
    # in addition to SQLAlchemy's translation for mapped statements.
    engine = create_engine(_test_database_url(), connect_args={'connect_timeout': 3, 'options': f'-csearch_path={schema}'})
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as cause:
        engine.dispose()
        pytest.skip(f'isolated test PostgreSQL unavailable: {cause}')
    scoped = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(scoped)
        # create_all declares the hash-partitioned parent; mirror the 16
        # partitions from migration c5d8e2f71a04 for the reused COPY contract.
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE datapoints ALTER COLUMN id SET DEFAULT nextval('datapoints_id_seq'::regclass)"))
            for index in range(16):
                connection.execute(text(f'CREATE TABLE datapoints_p{index:02d} PARTITION OF datapoints FOR VALUES WITH (MODULUS 16, REMAINDER {index})'))
        factory = sessionmaker(scoped, expire_on_commit=False)
        storage_calls, vector_calls = [], []
        from tinystudio import storage, vectors
        monkeypatch.setattr(storage, 'storage', lambda: SimpleNamespace(remote=True, delete_prefix=storage_calls.append))
        monkeypatch.setattr(vectors, 'drop_collection', vector_calls.append)
        monkeypatch.setattr(projects, 'config', lambda: SimpleNamespace(project_dir=lambda identity: tmp_path / identity))
        (tmp_path / 'owned').mkdir()
        (tmp_path / 'owned' / 'keep-until-relational-success').write_text('artifact')
        with factory() as db:
            yield db, storage_calls, vector_calls
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def seed(db):
    db.add(User(id='owner', email='owner@example.invalid', password_hash='preserve-password'))
    db.flush()
    db.add_all([Project(id='owned', slug='owned', name='Owned', created_by='owner'), Project(id='other', slug='other', name='Other', created_by='owner'), Setting(key='credential', value='preserve-setting'), ApiKey(id='key', user_id='owner', name='keep', prefix='keep', sha256='a'*64)])
    db.flush()
    for identity in ('owned', 'other'):
        db.add_all([LabDefinition(id=identity+'-definition', project_id=identity, name='Definition'), LabTarget(id=identity+'-target', project_id=identity, name='Target'), Dataset(id=identity+'-dataset', project_id=identity, name='Dataset'), Task(id=identity+'-task', project_id=identity, kind='mock', state='succeeded'), Repository(id=identity+'-repo', project_id=identity, name='Repo', embedding_model='mock', collection=identity+'-vectors'), LabOperation(id=identity+'-operation', project_id=identity, kind='export', idempotency_key='operation', payload={}, payload_digest='a'*64, state='succeeded')])
        db.flush()
        db.add(LabRevision(definition_id=identity+'-definition', revision=1, definition={}, digest='a'*64))
        db.add(LabRun(id=identity+'-parent', project_id=identity, definition_id=identity+'-definition', target_id=identity+'-target', revision=1, idempotency_key='parent', spec={}, spec_digest='a'*64, state='succeeded'))
        db.flush()
        db.add(LabRun(id=identity+'-child', project_id=identity, definition_id=identity+'-definition', target_id=identity+'-target', revision=1, idempotency_key='child', spec={}, spec_digest='a'*64, state='succeeded', parent_run_id=identity+'-parent'))
        db.flush()
        db.add(LabAttempt(id=identity+'-attempt', run_id=identity+'-child', target_id=identity+'-target', number=1, token_hash='a'*64, lease_until=datetime.now(timezone.utc), state='succeeded', runtime={}))
        db.flush()
        db.add_all([LabArtifact(id=identity+'-artifact', project_id=identity, attempt_id=identity+'-attempt', name='model', sha256='a'*64, size=1, media_type='application/octet-stream', storage_key='lab/'+identity+'/model'), LabEvent(run_id=identity+'-child', seq=1, kind='finished', payload={}), LabDispatch(run_id=identity+'-parent')])
    db.commit()
    return db.get(Project, 'owned')


def test_delete_project_removes_parent_linked_laboratory_rows_and_preserves_neighbors(deletion_db):
    db, storage_calls, vector_calls = deletion_db
    project = seed(db)
    projects.delete_project(db, project)
    db.commit()
    for model in (Project, LabDefinition, LabTarget, LabRun, LabAttempt, LabArtifact, LabOperation, Dataset, Task, Repository):
        owned_id = 'owned' if model is Project else 'owned-%'
        assert not db.scalar(select(func.count()).select_from(model).where(model.id.like(owned_id)))
    assert db.scalar(select(func.count()).select_from(LabRun)) == 2
    assert db.scalar(select(func.count()).select_from(LabAttempt)) == 1
    for model in (LabRevision, LabEvent, LabDispatch):
        assert db.scalar(select(func.count()).select_from(model)) == 1
    assert not projects.config().project_dir('owned').exists()
    assert db.get(Project, 'other') is not None
    assert db.get(User, 'owner').password_hash == 'preserve-password'
    assert db.get(Setting, 'credential').value == 'preserve-setting'
    assert db.get(ApiKey, 'key').sha256 == 'a'*64
    assert set(storage_calls) == {f'{category}/owned' for category in ('snapshots', 'models', 'captures', 'retained-captures', 'sources', 'uploads', 'lab')} | {'tasks/owned-task'}
    assert vector_calls == ['owned-vectors']


@pytest.mark.parametrize('active', ['task-queued', 'task-running', 'run-queued', 'run-running', 'attempt-running', 'operation-queued', 'operation-running'])
def test_active_work_blocks_deletion_before_external_cleanup(deletion_db, active):
    db, storage_calls, vector_calls = deletion_db
    project = seed(db)
    kind, state = active.split('-')
    model, identity = {'task': (Task, 'owned-task'), 'run': (LabRun, 'owned-child'), 'attempt': (LabAttempt, 'owned-attempt'), 'operation': (LabOperation, 'owned-operation')}[kind]
    db.get(model, identity).state = state
    db.commit()
    with pytest.raises(ProjectError, match='active|queued|running'):
        projects.delete_project(db, project)
    assert storage_calls == vector_calls == []
    assert (projects.config().project_dir('owned') / 'keep-until-relational-success').read_text() == 'artifact'
    db.rollback()
    assert db.get(Project, 'owned') is not None
    assert db.get(LabAttempt, 'owned-attempt') is not None


@pytest.mark.parametrize('reference', ['parent', 'target', 'attempt-target'])
def test_cross_project_reference_fails_before_artifact_cleanup(deletion_db, reference):
    db, storage_calls, vector_calls = deletion_db
    project = seed(db)
    if reference == 'parent':
        db.get(LabRun, 'other-child').parent_run_id = 'owned-parent'
    elif reference == 'target':
        db.get(LabRun, 'other-child').target_id = 'owned-target'
    else:
        db.get(LabAttempt, 'other-attempt').target_id = 'owned-target'
    db.commit()
    with pytest.raises(IntegrityError):
        projects.delete_project(db, project)
    assert storage_calls == vector_calls == []
    assert (projects.config().project_dir('owned') / 'keep-until-relational-success').read_text() == 'artifact'
    db.rollback()
    assert db.get(Project, 'owned') is not None
    assert db.get(LabAttempt, 'owned-attempt') is not None
    assert db.get(Project, 'other') is not None
    if reference == 'parent':
        assert db.get(LabRun, 'other-child').parent_run_id == 'owned-parent'
    elif reference == 'target':
        assert db.get(LabRun, 'other-child').target_id == 'owned-target'
    else:
        assert db.get(LabAttempt, 'other-attempt').target_id == 'owned-target'



def test_active_guard_holds_project_lock_until_caller_rolls_back(deletion_db):
    db, storage_calls, vector_calls = deletion_db
    project = seed(db)
    db.get(Task, 'owned-task').state = 'running'
    db.commit()
    with pytest.raises(ProjectError, match='active task'):
        projects.delete_project(db, project)
    with db.get_bind().begin() as connection:
        with pytest.raises(OperationalError) as error:
            connection.execute(select(Project.id).where(Project.id == 'owned').with_for_update(nowait=True))
        assert error.value.orig.sqlstate == '55P03'
    assert storage_calls == vector_calls == []
    db.rollback()


def test_existing_project_service_contract_in_isolated_schema(deletion_db):
    """Reuse the existing service contract without its shared-DB reset fixture."""
    from tests.test_services import test_projects_slugs_builtin_metrics_and_delete
    db, _, _ = deletion_db
    project = projects.create_project(db, 'Home Assistant', 'answer smart-home commands as JSON')
    test_projects_slugs_builtin_metrics_and_delete(db, project)
