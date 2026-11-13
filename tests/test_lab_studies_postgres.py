"""Real study migration/locking with an explicitly supplied disposable PG server."""
from concurrent.futures import ThreadPoolExecutor
import os
import math
import threading
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from tinystudio.db.migrate import downgrade, upgrade
from tinystudio.db.models import Project, User
from tinystudio.lab import service as svc, studies
from tinystudio.lab.models import LabDefinition, LabStudy, LabStudyRecord
from tests.test_lab_studies import protocol, record


@pytest.fixture
def postgres():
    url = os.environ.get('TINYSTUDIO_STUDY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('set TINYSTUDIO_STUDY_TEST_DATABASE_URL to an owned PostgreSQL server with CREATE DATABASE')
    name = 'tinystudio_studies_'+uuid.uuid4().hex[:12]
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    owned_url = make_url(url).set(database=name).render_as_string(hide_password=False)
    engine = create_engine(owned_url)
    try:
        upgrade('h1d3e7f65a49', owned_url)
        with engine.begin() as connection:
            connection.execute(Project.__table__.insert().values(id='study-migration-sentinel', slug='preserved', name='Preserved project'))
            connection.execute(LabDefinition.__table__.insert().values(id='prior-definition', project_id='study-migration-sentinel', name='Preserved definition', revision=1))
        before = {table: inspect(engine).get_columns(table) for table in ('projects','lab_definitions','lab_runs','lab_artifacts')}
        upgrade('head', owned_url)
        with engine.connect() as connection:
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar() == 'i2e4f8067b50'
            assert connection.execute(select(Project.name).where(Project.id=='study-migration-sentinel')).scalar() == 'Preserved project'
            assert connection.execute(select(LabDefinition.name).where(LabDefinition.id=='prior-definition')).scalar() == 'Preserved definition'
        for table, columns in before.items():
            after = inspect(engine).get_columns(table)
            assert [(c['name'],str(c['type']),c['nullable'],c['default']) for c in after] == [(c['name'],str(c['type']),c['nullable'],c['default']) for c in columns]
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as db:
            user=User(email='pg-study@fixture.invalid',password_hash='not-a-login')
            db.add(user);db.flush();author=user.id
        yield SimpleNamespace(factory=factory, engine=engine, url=owned_url, project='study-migration-sentinel', author=author)
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def test_postgres_simultaneous_protocol_saves_detect_the_stale_writer(postgres):
    p=postgres
    with p.factory.begin() as db:
        study=studies.save_study(db,p.project,protocol(),p.author,key='create')
    ready=threading.Barrier(2)
    def update(label):
        try:
            with p.factory.begin() as db:
                ready.wait(timeout=5)
                saved=studies.save_study(db,p.project,protocol(name=label),p.author,id_=study['id'],expected=1)
                return saved['revision']
        except svc.LabError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(update,['first','second']))
    assert sorted(results)==[2,409]
    with p.factory() as db:
        assert len(studies.revisions(db,p.project,study['id']))==2
        assert studies.get_study(db,p.project,study['id'],revision=1)['digest']==study['digest']


def test_postgres_concurrent_record_appends_allocate_distinct_sequences(postgres):
    p=postgres
    with p.factory.begin() as db:
        study=studies.save_study(db,p.project,protocol(),p.author,key='create')
    ready=threading.Barrier(4)
    def append(n):
        with p.factory.begin() as db:
            ready.wait(timeout=5)
            return studies.append_record(db,p.project,study['id'],record(key=f'entry-{n}',unit_id=f'unit-{n}'),p.author)['seq']
    with ThreadPoolExecutor(max_workers=4) as workers:
        sequences=list(workers.map(append,range(4)))
    assert sorted(sequences)==[1,2,3,4]
    with p.factory() as db:
        assert studies.get_study(db,p.project,study['id'])['summary']=={'records':4,'declared_unit_ids':4}


def test_postgres_concurrent_duplicate_creation_and_recording_are_idempotent(postgres):
    p=postgres
    ready=threading.Barrier(2)
    def create(_):
        with p.factory.begin() as db:
            ready.wait(timeout=5)
            return studies.save_study(db,p.project,protocol(),p.author,key='same-key')['id']
    with ThreadPoolExecutor(max_workers=2) as workers:
        ids=list(workers.map(create,range(2)))
    assert ids[0]==ids[1]
    def append(_):
        with p.factory.begin() as db:
            ready.wait(timeout=5)
            return studies.append_record(db,p.project,ids[0],record(),p.author)['id']
    with ThreadPoolExecutor(max_workers=2) as workers:
        records=list(workers.map(append,range(2)))
    assert records[0]==records[1]
    with p.factory() as db:
        assert studies.get_study(db,p.project,ids[0])['last_sequence']==1


def test_postgres_downgrade_preserves_nonempty_tracking_history(postgres):
    p=postgres
    with p.factory.begin() as db:
        study=studies.save_study(db,p.project,protocol(),p.author,key='retain')
        studies.append_record(db,p.project,study['id'],record(),p.author)
    with pytest.raises(Exception,match='retain study tracking history'):
        downgrade('h1d3e7f65a49',p.url)
    with p.factory() as db:
        assert studies.get_study(db,p.project,study['id'])['summary']['records']==1
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar()=='i2e4f8067b50'


def test_postgres_empty_tracking_migration_round_trips_without_legacy_loss(postgres):
    p=postgres
    downgrade('h1d3e7f65a49',p.url)
    assert 'lab_studies' not in inspect(p.engine).get_table_names()
    with p.engine.connect() as connection:
        assert connection.execute(select(LabDefinition.name).where(LabDefinition.id=='prior-definition')).scalar()=='Preserved definition'
    upgrade('head',p.url)
    assert {'lab_studies','lab_study_revisions','lab_study_records'} <= set(inspect(p.engine).get_table_names())


def test_postgres_preserves_canonical_float_values_without_jsonb_normalization(postgres):
    p=postgres
    data={'negative_zero':-0.0,'scientific_float':1e20,'small_float':1.25e-30}
    with p.factory.begin() as db:
        study=studies.save_study(db,p.project,protocol(metadata=data),p.author,key='float-values')
        entry=studies.append_record(db,p.project,study['id'],record(data=data),p.author)
    with p.factory() as db:
        reloaded=studies.get_study(db,p.project,study['id'])
        stored=studies.records(db,p.project,study['id'])['records'][0]
        assert reloaded['digest']==study['digest'] and stored['digest']==entry['digest']
        assert math.copysign(1,stored['data']['negative_zero'])==-1
        assert type(stored['data']['scientific_float']) is float
        assert stored['data']==data


def test_postgres_object_verification_does_not_hold_the_study_write_lock(postgres,tmp_path,monkeypatch):
    from tinystudio.storage import ArtifactStorage
    p=postgres
    monkeypatch.setattr(svc,'storage',lambda:ArtifactStorage(root=tmp_path/'objects'))
    source=tmp_path/'source.bin';source.write_bytes(b'retained evidence')
    with p.factory.begin() as db:
        study=studies.save_study(db,p.project,protocol(),p.author,key='create')
        artifact=svc.store_artifact(db,p.project,source,'source.bin','application/octet-stream')
        artifact_id=artifact.id
    entered=threading.Event();release=threading.Event()
    materialize=svc.materialize
    def slow_verification(row,directory):
        entered.set()
        assert release.wait(timeout=10),'owned verification barrier timed out'
        return materialize(row,directory)
    monkeypatch.setattr(svc,'materialize',slow_verification)
    def append(key,refs):
        with p.factory.begin() as db:
            return studies.append_record(db,p.project,study['id'],record(key=key,artifact_ids=refs),p.author)['seq']
    with ThreadPoolExecutor(max_workers=2) as workers:
        slow=workers.submit(append,'with-source',[artifact_id])
        try:
            assert entered.wait(timeout=5)
            fast=workers.submit(append,'without-source',[])
            assert fast.result(timeout=3)==1
        finally:
            release.set()
        assert slow.result(timeout=5)==2
