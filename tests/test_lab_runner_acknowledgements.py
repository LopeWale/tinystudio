"""A runner acknowledgement follows the database transaction it describes."""
import hashlib
import threading

import pytest
from sqlalchemy.orm import Session

from tests.test_lab_native_workflow import native_http, posted, submit_signal
from tinystudio.lab.runner import capabilities


@pytest.mark.parametrize('operation,fail_at', [
    ('advertise', 1), ('claim', 1), ('heartbeat', 1), ('finish', 2), ('artifacts', 2),
])
def test_commit_failure_cannot_acknowledge_runner_publication(native_http, monkeypatch, operation, fail_at):
    f = native_http
    target = posted(f.http, f.prefix + '/targets', json={'name': 'commit-boundary'})
    prefix = '/api/lab/runner/' + target['id']
    headers = {'Authorization': 'Bearer ' + target['token']}
    advertised = capabilities()
    body = {'capabilities': advertised}
    request_path = prefix + '/' + operation
    run = None
    if operation != 'advertise':
        posted(f.http, prefix + '/advertise', headers=headers, json=body)
        run = submit_signal(f, target, maximum=1024)
        body = {**body, 'run_id': run['id']}
        if operation != 'claim':
            claimed = posted(f.http, prefix + '/claim', headers=headers, json=body)['attempt']
            headers['X-Lab-Lease'] = claimed['lease_token']
            request_path = prefix + '/attempts/' + claimed['attempt_id'] + '/' + operation
    payload = {'json': body}
    if operation == 'heartbeat':
        payload = {'json': {'events': [{'type': 'owned-test-observation'}]}}
    elif operation == 'finish':
        payload = {'json': {'state': 'cancelled'}}
    elif operation == 'artifacts':
        wire = b'original observed bytes'
        payload = {'params': {'sha256': hashlib.sha256(wire).hexdigest(), 'role': 'original'},
                   'files': {'file': ('observed.bin', wire, 'application/octet-stream')}}
    attempted = threading.Event()
    original_commit = Session.commit
    calls = 0
    def failing_commit(db):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            attempted.set()
            raise RuntimeError('injected runner publication commit failure')
        return original_commit(db)
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', failing_commit)
        response = f.http.post(request_path, headers=headers, **payload)
        assert attempted.wait(timeout=5)
    assert response.status_code == 500, (operation, response.status_code, response.text)
    if run is not None:
        retained = f.http.get(f.prefix + '/runs/' + run['id']).json()
        assert retained['state'] == ('queued' if operation == 'claim' else 'running')
        if operation == 'artifacts':
            assert retained.get('result') is None
