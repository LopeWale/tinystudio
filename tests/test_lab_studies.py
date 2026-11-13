"""Public study tracking behavior with owned SQLite metadata; PostgreSQL separately."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from tinystudio.db.models import ApiKey, Base, Instance, Project, User
from tinystudio.lab import service as svc
from tinystudio.lab.contracts import checksum
from tinystudio.lab.models import TABLES
from tinystudio.storage import ArtifactStorage


def module():
    assert importlib.util.find_spec('tinystudio.lab.studies') is not None, 'study tracking service is missing'
    from tinystudio.lab import studies
    return studies


def protocol(**patch):
    return {'name': 'Thermal response study', 'hypothesis': 'An authored intervention may cool or warm the device.',
            'independent_unit': 'One independently prepared device',
            'conditions': [{'id': 'baseline'}, {'id': 'intervention'}],
            'outcomes': [{'id': 'temperature', 'unit': 'degC'}],
            'sampling_plan': {'planned_devices': 1}, 'status': 'pilot', **patch}


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///'+str(tmp_path/'metadata.db'))
    Base.metadata.create_all(engine, tables=[Instance.__table__, User.__table__, Project.__table__, ApiKey.__table__, *TABLES])
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(svc, 'storage', lambda: ArtifactStorage(root=tmp_path/'objects'))
    with factory.begin() as db:
        user = User(email='study@fixture.invalid', password_hash='not-a-login')
        project = Project(slug='study', name='Studies'); other = Project(slug='other-study', name='Other')
        db.add_all([user, project, other]); db.flush()
        ids = (project.id, other.id, user.id)
    try:
        yield SimpleNamespace(factory=factory, project=ids[0], other=ids[1], author=ids[2], root=tmp_path)
    finally:
        engine.dispose()


def save(db, store, **patch):
    return module().save_study(db, store.project, protocol(**patch), store.author, key='create-study')


def record(revision=1, key='observation-1', **patch):
    return {'revision': revision, 'idempotency_key': key, 'kind': 'observation', 'unit_id': 'device-1',
            'condition': 'intervention', 'observed_at': '2026-11-13T01:00:00+00:00',
            'data': {'temperature': 41.2, 'unit': 'degC', 'uncertainty': .2}, **patch}


def test_protocol_revisions_preserve_authorship_and_researcher_choices(store):
    with store.factory.begin() as db:
        requested = protocol(stopping_rules={'authored': 'stop at researcher discretion'}, status='inconclusive-custom')
        before = deepcopy(requested)
        first = module().save_study(db, store.project, requested, store.author, key='create-study')
        assert first['revision'] == first['head_revision'] == 1
        assert first['author_id'] == store.author and first['recorded_at']
        assert requested == before
        second = module().save_study(db, store.project, {**requested, 'hypothesis': 'Revised explicitly'},
                                     store.author, id_=first['id'], expected=1)
        old = module().get_study(db, store.project, first['id'], revision=1)
        assert second['revision'] == 2 and old['head_revision'] == 2
        assert old['study']['hypothesis'] == before['hypothesis']
        assert old['study']['status'] == 'inconclusive-custom'
        assert old['digest'] == first['digest'] != second['digest']
        assert [r['revision'] for r in module().revisions(db, store.project, first['id'])] == [2, 1]


def test_creation_is_idempotent_and_conflicting_reuse_does_not_create_another_study(store):
    with store.factory.begin() as db:
        first = save(db, store)
        assert save(db, store)['id'] == first['id']
        with pytest.raises(svc.LabError) as error:
            save(db, store, hypothesis='different')
        assert error.value.status == 409
        assert len(module().list_studies(db, store.project)) == 1


def test_stale_or_unspecified_update_cannot_replace_current_protocol(store):
    with store.factory.begin() as db:
        first = save(db, store)
        module().save_study(db, store.project, protocol(name='Current'), store.author, id_=first['id'], expected=1)
        for expected in (None, 1):
            with pytest.raises(svc.LabError) as error:
                module().save_study(db, store.project, protocol(name='Stale'), store.author, id_=first['id'], expected=expected)
            assert error.value.status == 409
        assert module().get_study(db, store.project, first['id'])['study']['name'] == 'Current'


def test_records_pin_old_protocol_and_do_not_enforce_a_planned_sample_ceiling(store):
    with store.factory.begin() as db:
        first = save(db, store)
        module().save_study(db, store.project, protocol(name='New protocol'), store.author, id_=first['id'], expected=1)
        for n in (1, 2):
            row = module().append_record(db, store.project, first['id'], record(key=str(n), unit_id=f'device-{n}'), store.author)
            assert row['revision'] == 1 and row['protocol_digest'] == first['digest']
            assert row['author_id'] == store.author and row['recorded_at'] != row['observed_at']
        study = module().get_study(db, store.project, first['id'])
        assert study['summary'] == {'records': 2, 'declared_unit_ids': 2}
        assert study['study']['sampling_plan']['planned_devices'] == 1
        page = module().records(db, store.project, first['id'], after=0, limit=1)
        assert len(page['records']) == 1 and page['has_more'] and page['next_after'] == 1
        following = module().records(db, store.project, first['id'], after=1, limit=1)
        assert following['records'][0]['seq'] == 2 and not following['has_more']


def test_record_redelivery_returns_original_and_conflicting_payload_is_rejected(store):
    with store.factory.begin() as db:
        study = save(db, store)
        first = module().append_record(db, store.project, study['id'], record(), store.author)
        repeated = module().append_record(db, store.project, study['id'], record(), store.author)
        assert repeated == first
        with pytest.raises(svc.LabError) as error:
            module().append_record(db, store.project, study['id'], record(data={'changed': True}), store.author)
        assert error.value.status == 409
        assert module().get_study(db, store.project, study['id'])['last_sequence'] == 1


def test_missing_negative_and_corrected_observations_remain_explicit(store):
    with store.factory.begin() as db:
        study = save(db, store)
        first = module().append_record(db, store.project, study['id'],
            record(kind='negative-result', data={'effect': -2, 'conclusion': 'warmer'}), store.author)
        second = module().append_record(db, store.project, study['id'],
            record(key='correction', kind='correction', supersedes=first['id'], unit_id='',
                   data={'temperature': None, 'missing_reason': 'sensor calibration failed'}), store.author)
        all_rows = module().records(db, store.project, study['id'])['records']
        assert all_rows[0]['data']['effect'] == -2
        assert all_rows[1]['supersedes'] == first['id'] and second['data']['temperature'] is None
        assert module().get_study(db, store.project, study['id'])['summary'] == {'records': 2, 'declared_unit_ids': 1}


def test_foreign_evidence_and_foreign_study_cannot_be_linked(store):
    with store.factory.begin() as db:
        study = save(db, store)
        path = store.root/'input.jsonl'; path.write_text('{"value":1}\n')
        foreign = svc.store_artifact(db, store.other, path, 'data.jsonl', 'application/jsonl')
        with pytest.raises(svc.LabError) as error:
            module().append_record(db, store.project, study['id'], record(artifact_ids=[foreign.id]), store.author)
        assert error.value.status == 404
        with pytest.raises(svc.LabError) as error:
            module().get_study(db, store.other, study['id'])
        assert error.value.status == 404
        assert module().get_study(db, store.project, study['id'])['last_sequence'] == 0


def test_artifact_metadata_is_derived_server_side_and_frozen(store):
    with store.factory.begin() as db:
        study = save(db, store)
        path = store.root/'plan.json'; path.write_text('{"planning":true}')
        artifact = svc.store_artifact(db, store.project, path, 'planning.json', 'application/json', 'statistical_plan', visibility='evaluation')
        entry = module().append_record(db, store.project, study['id'], record(artifact_ids=[artifact.id]), store.author)
        ref = entry['artifact_references'][0]
        assert ref['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert ref['role'] == 'statistical_plan' and ref['visibility'] == 'evaluation'
        assert 'storage_key' not in ref
        artifact.name = 'later-label.json'; db.flush()
        assert module().records(db, store.project, study['id'])['records'][0]['artifact_references'][0]['name'] == 'planning.json'


@pytest.mark.parametrize('patch', [{'revision': True}, {'revision': 1.5}, {'observed_at': '2026-11-13T01:00:00'},
                                   {'data': {'value': float('nan')}}, {'data': {'value': float('inf')}},
                                   {'kind': ''}, {'run_ids': ['a', 'a']}, {'data': {'integer': 2**53+1}}])
def test_invalid_record_admission_is_actionable_and_atomic(store, patch):
    with store.factory.begin() as db:
        study = save(db, store)
        with pytest.raises(svc.LabError) as error:
            module().append_record(db, store.project, study['id'], record(**patch), store.author)
        assert error.value.status == 422
        assert module().get_study(db, store.project, study['id'])['last_sequence'] == 0


def test_corrupt_persisted_record_cannot_be_presented_as_verified(store):
    with store.factory.begin() as db:
        study = save(db, store)
        entry = module().append_record(db, store.project, study['id'], record(), store.author)
        from tinystudio.lab.models import LabStudyRecord
        row = db.get(LabStudyRecord, entry['id'])
        row.record = {**row.record, 'data': {'fabricated': 100}}
        db.flush()
        with pytest.raises(svc.LabError, match='checksum'):
            module().records(db, store.project, study['id'])


def test_tracking_export_is_complete_hash_checked_metadata_with_a_frozen_boundary(store):
    with store.factory.begin() as db:
        study = save(db, store)
        module().append_record(db, store.project, study['id'], record(), store.author)
        manifest = module().tracking_manifest(db, store.project, study['id'])
        module().save_study(db, store.project, protocol(name='Later'), store.author, id_=study['id'], expected=1)
        module().append_record(db, store.project, study['id'], record(2, 'later'), store.author)
        path = module().export_tracking(db, store.project, study['id'], store.root,
                                       revision=manifest['revision'], through_seq=manifest['through_seq'])
        lines = path.read_bytes().splitlines(keepends=True)
        documents = [json.loads(line) for line in lines]
        assert documents[0]['external_evidence_bytes'] is False
        assert documents[0]['revision'] == documents[0]['through_seq'] == 1
        footer = documents[-1]
        assert footer['kind'] == 'export_summary' and footer['records'] == footer['protocol_revisions'] == 1
        assert footer['sha256'] == hashlib.sha256(b''.join(lines[:-1])).hexdigest()
        rows = [d for d in documents if d['kind'] == 'record']
        assert rows[0]['record']['data']['temperature'] == 41.2
        assert all('storage_key' not in json.dumps(document) for document in documents)


def test_large_exact_values_can_be_preserved_as_decimal_strings(store):
    with store.factory.begin() as db:
        study = save(db, store)
        row = module().append_record(db, store.project, study['id'], record(data={'integer': str(2**53+1), 'valid': True}), store.author)
        assert row['data'] == {'integer': '9007199254740993', 'valid': True}


def test_export_refuses_to_overwrite_or_remove_an_existing_file(store):
    with store.factory.begin() as db:
        study = save(db, store)
        path = module().export_tracking(db, store.project, study['id'], store.root)
        before = path.read_bytes()
        with pytest.raises(FileExistsError):
            module().export_tracking(db, store.project, study['id'], store.root)
        assert path.read_bytes() == before


def test_observation_timestamp_preserves_authored_precision_and_timezone(store):
    with store.factory.begin() as db:
        study = save(db, store)
        stamp = '2026-11-13T01:00:00.123456789+02:00'
        saved = module().append_record(db, store.project, study['id'], record(observed_at=stamp), store.author)
        assert saved['observed_at'] == stamp


def test_observation_timestamp_requires_explicit_units_and_timezone(store):
    with store.factory.begin() as db:
        study = save(db, store)
        with pytest.raises(svc.LabError) as error:
            module().append_record(db, store.project, study['id'], record(observed_at=1731469200), store.author)
        assert error.value.status == 422


def test_timestamp_does_not_guess_unix_units_from_a_numeric_string(store):
    with store.factory.begin() as db:
        study = save(db, store)
        with pytest.raises(svc.LabError) as error:
            module().append_record(db, store.project, study['id'], record(observed_at='1731469200'), store.author)
        assert error.value.status == 422


def test_correction_cannot_supersede_a_record_from_another_study(store):
    with store.factory.begin() as db:
        first = save(db, store)
        source = module().append_record(db, store.project, first['id'], record(), store.author)
        other = module().save_study(db, store.project, protocol(name='Other study'), store.author, key='second')
        with pytest.raises(svc.LabError) as error:
            module().append_record(db, store.project, other['id'], record(supersedes=source['id']), store.author)
        assert error.value.status == 404
        assert module().get_study(db, store.project, other['id'])['last_sequence'] == 0


def test_record_and_export_reject_a_corrupted_protocol_snapshot(store):
    with store.factory.begin() as db:
        study = save(db, store)
        from tinystudio.lab.models import LabStudyRevision
        row = db.get(LabStudyRevision, (study['id'], 1))
        row.snapshot = {**row.snapshot, 'study': protocol(name='Changed outside revision workflow')}
        db.flush()
        for call in [lambda: module().append_record(db, store.project, study['id'], record(), store.author),
                     lambda: module().export_tracking(db, store.project, study['id'], store.root)]:
            with pytest.raises(svc.LabError, match='checksum'):
                call()


def test_foreign_run_reference_is_rejected_before_record_admission(store):
    with store.factory.begin() as db:
        study = save(db, store)
        from tinystudio.lab.models import LabDefinition, LabRun, LabTarget
        definition = LabDefinition(project_id=store.other, name='Foreign configuration')
        target = LabTarget(project_id=store.other, name='Foreign target')
        db.add_all([definition, target]); db.flush()
        spec = {'definition': {'engine': 'synthetic.signal'}}
        run = LabRun(project_id=store.other, definition_id=definition.id, revision=1, target_id=target.id,
                     idempotency_key='foreign', spec=spec, spec_digest=checksum(spec))
        db.add(run); db.flush()
        with pytest.raises(svc.LabError) as error:
            module().append_record(db, store.project, study['id'], record(run_ids=[run.id]), store.author)
        assert error.value.status == 404
        assert module().get_study(db, store.project, study['id'])['last_sequence'] == 0


def test_only_name_is_required_and_protocol_labels_can_be_simple_or_structured(store):
    with store.factory.begin() as db:
        minimal = module().save_study(db, store.project, {'name':'Unspecified assumptions'}, store.author, key='minimal')
        assert minimal['study']['hypothesis'] == '' and minimal['study']['sampling_plan'] == {}
        authored = module().save_study(db, store.project,
            {'name':'Free labels', 'status':'', 'conditions':['baseline', {'id':'custom'}],
             'outcomes':['negative', 'missing', {'id':'measurement','unit':'V'}]}, store.author, key='free-labels')
        assert authored['study']['status'] == ''
        assert authored['study']['conditions'] == ['baseline', {'id':'custom'}]
        assert authored['study']['outcomes'][0:2] == ['negative', 'missing']


@pytest.mark.parametrize('reader', ['records','study'])
def test_read_counts_and_pages_share_their_captured_sequence_boundary(store,monkeypatch,reader):
    with store.factory.begin() as db:
        study = save(db, store)
    service = module()
    original = service._study
    inserted = False
    def append_after_header(db, project, id_, **kwargs):
        nonlocal inserted
        row = original(db, project, id_, **kwargs)
        if not inserted and not kwargs.get('lock'):
            inserted = True
            with store.factory.begin() as writer:
                service.append_record(writer, store.project, study['id'], record(), store.author)
        return row
    monkeypatch.setattr(service,'_study',append_after_header)
    with store.factory() as db:
        if reader == 'records':
            page = service.records(db,store.project,study['id'])
            assert page['latest_seq'] == 0 and page['records'] == [] and not page['has_more']
        else:
            header = service.get_study(db,store.project,study['id'])
            assert header['last_sequence'] == header['summary']['records'] == 0


@pytest.mark.parametrize('damage', ['corrupt','missing'])
def test_new_associations_verify_retained_artifact_bytes_before_admission(store,damage):
    with store.factory.begin() as db:
        study = save(db, store)
        path = store.root/'source.bin'; path.write_bytes(b'original evidence')
        artifact = svc.store_artifact(db,store.project,path,'source.bin','application/octet-stream')
        retained = svc.storage().root/artifact.storage_key
        if damage == 'corrupt': retained.write_bytes(b'CORRUPT BYTES')
        else: retained.unlink()
        with pytest.raises(svc.LabError) as error:
            module().append_record(db,store.project,study['id'],record(artifact_ids=[artifact.id]),store.author)
        assert error.value.status == 409
        assert module().get_study(db,store.project,study['id'])['last_sequence'] == 0


def test_idempotent_replay_returns_the_original_association_even_if_bytes_expire_later(store):
    with store.factory.begin() as db:
        study = save(db,store)
        path=store.root/'source.bin';path.write_bytes(b'original evidence')
        artifact=svc.store_artifact(db,store.project,path,'source.bin','application/octet-stream')
        body=record(artifact_ids=[artifact.id])
        original=module().append_record(db,store.project,study['id'],body,store.author)
        assert original['artifact_references'][0]['byte_verification'] == 'sha256_and_size'
        (svc.storage().root/artifact.storage_key).unlink()
        assert module().append_record(db,store.project,study['id'],body,store.author) == original
