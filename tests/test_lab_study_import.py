"""Artifact rows become observations only through an explicit, verified mapping."""
import hashlib
import json

import pytest
from sqlalchemy import func, select

from tests.test_lab_studies import store, save
from tinystudio.lab import service as svc, studies
from tinystudio.lab.models import LabStudyRecord


def service():
    from tinystudio.lab import study_import
    return study_import


def source(db, store):
    path = store.root / 'curated.jsonl'
    path.write_bytes(b'{"device":"one","arm":"A","temperature":0.1}\n\n'
                     b'{"device":"one","arm":"A","temperature":0.2}\n'
                     b'{"device":"two","arm":"B","temperature":0.3}\n')
    return svc.store_artifact(db, store.project, path, path.name, 'application/jsonl', 'curated_observations')


def mapping():
    return {'selection': {'all': True}, 'number_encoding': 'decimal_strings',
            'unit_id': {'pointer': '/device'}, 'condition': {'pointer': '/arm'},
            'data': {'temperature': {'pointer': '/temperature'}, 'unit': {'literal': 'degC'}}}


def request(artifact):
    return {'artifact_id': artifact.id, 'revision': 1, 'projection': mapping()}


def preview(db, store, study, artifact):
    return service().preview_import(db, store.project, study['id'], request(artifact))


def apply(db, store, study, artifact, prepared, key='chosen-import'):
    return service().apply_import(db, store.project, study['id'],
        {**request(artifact), 'preview_digest': prepared['digest'], 'idempotency_key': key}, store.author)


def test_preview_and_import_preserve_source_identity_and_declared_units(store):
    with store.factory.begin() as db:
        study, artifact = save(db, store), source(db, store)
        prepared = preview(db, store, study, artifact)
        assert prepared['source']['sha256'] == artifact.sha256
        assert studies.records(db, store.project, study['id'])['records'] == []
        imported = apply(db, store, study, artifact, prepared)
        assert imported['count'] == 3
        retained = studies.records(db, store.project, study['id'])['records']
        observations = [row for row in retained if row['kind'] == 'observation']
        assert [row['unit_id'] for row in observations] == ['one', 'one', 'two']
        assert [row['condition'] for row in observations] == ['A', 'A', 'B']
        assert [row['data']['temperature'] for row in observations] == ['0.1', '0.2', '0.3']
        assert [row['data']['_source_projection']['line'] for row in observations] == [1, 3, 4]
        for row in observations:
            assert row['artifact_references'][0]['sha256'] == artifact.sha256
            provenance = row['data']['_source_projection']
            assert provenance['artifact_id'] == artifact.id
            assert provenance['artifact_sha256'] == artifact.sha256
            assert provenance['projection_digest'] == prepared['digest']
        assert observations[0]['data']['_source_projection']['raw_line_sha256'] == hashlib.sha256(
            b'{"device":"one","arm":"A","temperature":0.1}\n').hexdigest()
        receipts = [row for row in retained if row['kind'] == 'observation_import']
        assert len(receipts) == 1 and receipts[0]['id'] == imported['receipt']['id']
        assert receipts[0]['data']['count'] == 3


def test_import_replay_does_not_append_again_or_need_external_bytes(store):
    with store.factory.begin() as db:
        study, artifact = save(db, store), source(db, store)
        prepared = preview(db, store, study, artifact)
        first = apply(db, store, study, artifact, prepared)
        (svc.storage().root / artifact.storage_key).unlink()
        second = apply(db, store, study, artifact, prepared)
        assert second['receipt']['id'] == first['receipt']['id']
        assert second['replayed'] is True
        assert db.scalar(select(func.count()).select_from(LabStudyRecord)) == 4


def test_changed_request_cannot_reuse_import_key(store):
    with store.factory.begin() as db:
        study, artifact = save(db, store), source(db, store)
        prepared = preview(db, store, study, artifact)
        apply(db, store, study, artifact, prepared)
        changed = {**request(artifact), 'preview_digest': prepared['digest'], 'idempotency_key': 'chosen-import'}
        changed['projection']['condition'] = {'literal': 'changed'}
        with pytest.raises(svc.LabError) as error:
            service().apply_import(db, store.project, study['id'], changed, store.author)
        assert error.value.status == 409
        assert db.scalar(select(func.count()).select_from(LabStudyRecord)) == 4


def test_mismatched_preview_appends_nothing(store):
    with store.factory.begin() as db:
        study, artifact = save(db, store), source(db, store)
        prepared = preview(db, store, study, artifact)
        with pytest.raises(svc.LabError) as error:
            apply(db, store, study, artifact, {**prepared, 'digest': '0' * 64})
        assert error.value.status == 409
        assert db.scalar(select(func.count()).select_from(LabStudyRecord)) == 0


@pytest.mark.parametrize('damage', ['foreign', 'corrupt'])
def test_source_ownership_and_exact_bytes_are_required(store, damage):
    with store.factory.begin() as db:
        study, artifact = save(db, store), source(db, store)
        if damage == 'foreign':
            artifact.project_id = store.other
            db.flush()
        else:
            (svc.storage().root / artifact.storage_key).write_bytes(b'changed source')
        with pytest.raises(svc.LabError):
            preview(db, store, study, artifact)
        assert db.scalar(select(func.count()).select_from(LabStudyRecord)) == 0


def test_failed_publication_rolls_back_even_if_caller_catches_error(store, monkeypatch):
    with store.factory.begin() as db:
        study, artifact = save(db, store), source(db, store)
        prepared = preview(db, store, study, artifact)
        original = studies._append_verified_record
        calls = []
        def interrupted(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                raise svc.LabError('injected publication failure', 409)
            return original(*args, **kwargs)
        monkeypatch.setattr(studies, '_append_verified_record', interrupted)
        with pytest.raises(svc.LabError, match='injected publication failure'):
            apply(db, store, study, artifact, prepared)
        assert db.scalar(select(func.count()).select_from(LabStudyRecord)) == 0
        assert studies.get_study(db, store.project, study['id'])['last_sequence'] == 0


def test_bulk_source_is_materialized_once_per_import(store, monkeypatch):
    with store.factory.begin() as db:
        study, artifact = save(db, store), source(db, store)
        prepared = preview(db, store, study, artifact)
        original, calls = svc.materialize, []
        def measured(*args, **kwargs):
            calls.append(True)
            return original(*args, **kwargs)
        monkeypatch.setattr(svc, 'materialize', measured)
        apply(db, store, study, artifact, prepared)
        assert len(calls) == 1


def test_explicit_import_can_pin_a_retained_protocol_revision(store):
    with store.factory.begin() as db:
        study, artifact = save(db, store), source(db, store)
        prepared = preview(db, store, study, artifact)
        studies.save_study(db, store.project, {**study['study'], 'name': 'Later protocol'},
                           store.author, id_=study['id'], expected=1)
        apply(db, store, study, artifact, prepared)
        assert all(row['revision'] == 1 and row['protocol_digest'] == study['digest']
                   for row in studies.records(db, store.project, study['id'])['records'])


@pytest.mark.parametrize('row', [False, True])
def test_public_append_cannot_publish_internal_import_identities(store, row):
    with store.factory.begin() as db:
        study = save(db, store)
        if row:
            from tinystudio.lab.study_import_paths import StudyImportKeys
            key = StudyImportKeys('chosen-import').row(1)
        else:
            key = service()._marker_key('chosen-import')
        with pytest.raises(svc.LabError) as error:
            studies.append_record(db, store.project, study['id'], {
                'revision': 1, 'idempotency_key': key, 'kind': 'observation_import',
                'data': {'format': service().FORMAT, 'count': 3}}, store.author)
        assert error.value.status == 422
        assert db.scalar(select(func.count()).select_from(LabStudyRecord)) == 0
