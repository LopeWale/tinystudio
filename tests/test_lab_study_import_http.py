"""Authenticated artifact-to-study-to-inference wiring on an owned HTTP server."""
import hashlib
import json
import math
import threading

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from tests.test_lab_native_workflow import native_http, posted
from tests.test_lab_studies import protocol
from tests.test_lab_analysis_selection import parameters
from tinystudio.lab.analysis_selection import prepare_selection
from tinystudio.lab.analysis_methods import calculate


def test_imported_observations_reach_frozen_analysis_with_original_provenance(native_http):
    f = native_http
    wire = b''.join(json.dumps({'device': unit, 'condition': arm, 'response': value}).encode() + b'\n'
        for unit, arm, value in [('a1', 'A', 1), ('a2', 'A', 3), ('b1', 'B', 2), ('b2', 'B', 4)])
    uploaded = f.http.post(f.prefix + '/inputs', files={'file': ('curated.jsonl', wire, 'application/jsonl')})
    assert uploaded.status_code == 201, uploaded.text
    artifact = uploaded.json()
    study = posted(f.http, f.prefix + '/studies', json={'study': protocol(), 'idempotency_key': 'import-study'})
    links = {key: value.replace('/api/lab', f.prefix, 1) for key, value in study['actions'].items()}
    body = {'artifact_id': artifact['id'], 'revision': 1, 'projection': {
        'selection': {'all': True}, 'number_encoding': 'decimal_strings',
        'unit_id': {'pointer': '/device'}, 'condition': {'pointer': '/condition'},
        'data': {'response': {'pointer': '/response'}}}}
    prepared = posted(f.http, links['observation_import_preview'], json=body)
    assert prepared['summary']['count'] == 4
    assert f.http.get(f.prefix + '/studies/' + study['id']).json()['last_sequence'] == 0
    exact = {**body, 'preview_digest': prepared['digest'], 'idempotency_key': 'selected-source-rows'}
    applied = posted(f.http, links['observation_import'], json=exact)
    assert applied['count'] == 4
    replayed = posted(f.http, links['observation_import'], json=exact)
    assert replayed['receipt']['id'] == applied['receipt']['id'] and replayed['replayed']
    assert f.http.get(f.prefix + '/studies/' + study['id']).json()['last_sequence'] == 5
    for link in links.values():
        assert f.http.post(link.replace(f.prefix, f.restored, 1), json=exact if link == links['observation_import'] else body).status_code == 404
    forged = f.http.post(links['observation_import'], json={**exact, 'author_id': 'forged'})
    assert forged.status_code == 422
    manifest = f.http.get(f.prefix + '/studies/' + study['id'] + '/manifest').json()
    snapshot = f.http.get(manifest['export_path'].replace('/api/lab', f.prefix, 1))
    assert snapshot.status_code == 200
    assert hashlib.sha256(snapshot.content).hexdigest() == snapshot.headers['x-content-sha256']
    path = f.root / 'imported-study.jsonl'
    path.write_bytes(snapshot.content)
    selected = prepare_selection(path, parameters(groups=['A', 'B']), f.root / 'selected')
    assert selected.group_a == [1, 3] and selected.group_b == [2, 4]
    retained = [json.loads(line) for line in selected.paths.observations.read_text().splitlines()]
    used = [row for row in retained if row['kind'] == 'observation']
    assert len(used) == 4
    assert all(row['data']['_source_projection']['artifact_sha256'] == artifact['sha256'] for row in used)
    assert all(row['artifact_references'][0]['sha256'] == artifact['sha256'] for row in used)
    result = calculate('means.welch', selected.group_a, selected.group_b, confidence=.95, alternative='two-sided')
    assert result['counts'] == {'group_a': 2, 'group_b': 2}
    assert result['estimate'] == -1
    assert result['standard_error'] == pytest.approx(math.sqrt(2))
    assert result['degrees_of_freedom'] == pytest.approx(2)


def test_commit_failure_cannot_return_a_successful_import_receipt(native_http, monkeypatch):
    f = native_http
    uploaded = f.http.post(f.prefix + '/inputs', files={'file': ('one.jsonl', b'{"unit":"u"}\n', 'application/jsonl')})
    assert uploaded.status_code == 201, uploaded.text
    study = posted(f.http, f.prefix + '/studies', json={'study': protocol(), 'idempotency_key': 'commit-boundary'})
    links = {key: value.replace('/api/lab', f.prefix, 1) for key, value in study['actions'].items()}
    body = {'artifact_id': uploaded.json()['id'], 'revision': 1, 'projection': {
        'selection': {'all': True}, 'number_encoding': 'decimal_strings',
        'unit_id': {'pointer': '/unit'}, 'condition': {'literal': 'A'},
        'data': {'response': {'literal': 1}}}}
    prepared = posted(f.http, links['observation_import_preview'], json=body)
    attempted = threading.Event()
    def failed_commit(db):
        attempted.set()
        raise RuntimeError('injected database commit failure')
    # SQLite's legacy driver does not begin transactions for SELECT; its first
    # SAVEPOINT can otherwise commit on RELEASE. Model PostgreSQL's real outer
    # transaction here so this fault tests both acknowledgement and rollback.
    with f.factory() as db:
        engine = db.get_bind()
    def begin_transaction(connection):
        connection.exec_driver_sql('BEGIN')
    event.listen(engine, 'begin', begin_transaction)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(Session, 'commit', failed_commit)
            response = f.http.post(links['observation_import'], json={**body,
                'preview_digest': prepared['digest'], 'idempotency_key': 'original-import'})
            assert attempted.wait(timeout=5), 'owned request never reached its commit boundary'
    finally:
        event.remove(engine, 'begin', begin_transaction)
    assert response.status_code == 500, response.text
    assert f.http.get(f.prefix + '/studies/' + study['id'] + '/records').json()['records'] == []
