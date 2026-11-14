"""Concurrent cohort publication on an explicitly owned PostgreSQL cluster."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

import pytest

from tests.test_lab_studies_postgres import postgres
from tests.test_lab_study_import import source, request
from tinystudio.lab import service as svc, studies, study_import
from tinystudio.storage import ArtifactStorage


@pytest.mark.parametrize('mode', ['same_key', 'different_keys', 'conflicting_key'])
def test_postgres_bulk_imports_are_atomic_and_retry_safe(postgres, tmp_path, monkeypatch, mode):
    p = postgres
    p.root = tmp_path
    monkeypatch.setattr(svc, 'storage', lambda: ArtifactStorage(root=tmp_path / 'objects'))
    with p.factory.begin() as db:
        study = studies.save_study(db, p.project, {'name': 'Explicit import concurrency'}, p.author, key='create')
        artifact = source(db, p)
        body = request(artifact)
        first = study_import.preview_import(db, p.project, study['id'], body)
        second_body = deepcopy(body)
        if mode == 'conflicting_key':
            second_body['projection']['condition'] = {'literal': 'another authored condition'}
        second = study_import.preview_import(db, p.project, study['id'], second_body)
    barrier = threading.Barrier(2)
    requests = [
        {**body, 'preview_digest': first['digest'], 'idempotency_key': 'chosen-cohort'},
        {**second_body, 'preview_digest': second['digest'],
         'idempotency_key': 'another-cohort' if mode == 'different_keys' else 'chosen-cohort'},
    ]
    def publish(body):
        try:
            with p.factory.begin() as db:
                barrier.wait(timeout=10)
                return study_import.apply_import(db, p.project, study['id'], body, p.author)
        except svc.LabError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(publish, requests))
    with p.factory() as db:
        rows = studies.records(db, p.project, study['id'])['records']
    if mode == 'different_keys':
        assert [row['seq'] for row in rows] == list(range(1, 9))
        assert len({result['receipt']['id'] for result in results}) == 2
        assert sorted((result['first_sequence'], result['last_sequence']) for result in results) == [(1, 3), (5, 7)]
    else:
        assert [row['seq'] for row in rows] == [1, 2, 3, 4]
        if mode == 'same_key':
            assert results[0]['receipt']['id'] == results[1]['receipt']['id']
            assert sorted(result['replayed'] for result in results) == [False, True]
        else:
            assert sum(result == 409 for result in results) == 1
    observations = [row for row in rows if row['kind'] == 'observation']
    receipts = [row for row in rows if row['kind'] == 'observation_import']
    assert len(observations) == 3 * len(receipts)
    for receipt in receipts:
        low, high = receipt['data']['first_sequence'], receipt['data']['last_sequence']
        cohort = [row for row in observations if low <= row['seq'] <= high]
        assert len(cohort) == receipt['data']['count'] == 3
        assert [row['data']['_source_projection']['line'] for row in cohort] == [1, 3, 4]


def test_postgres_outer_abort_discards_observations_and_receipt(postgres, tmp_path, monkeypatch):
    p = postgres
    p.root = tmp_path
    monkeypatch.setattr(svc, 'storage', lambda: ArtifactStorage(root=tmp_path / 'objects'))
    with p.factory.begin() as db:
        study = studies.save_study(db, p.project, {'name': 'Outer transaction'}, p.author, key='create')
        artifact = source(db, p)
        body = request(artifact)
        prepared = study_import.preview_import(db, p.project, study['id'], body)
    with p.factory() as db:
        result = study_import.apply_import(db, p.project, study['id'], {
            **body, 'preview_digest': prepared['digest'], 'idempotency_key': 'aborted-cohort'}, p.author)
        assert result['count'] == 3
        db.rollback()
    with p.factory() as db:
        assert studies.records(db, p.project, study['id'])['records'] == []
        assert studies.get_study(db, p.project, study['id'])['last_sequence'] == 0
