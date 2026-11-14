"""Runner transfer selection uses authenticated, centralized attempt paths."""
from dataclasses import FrozenInstanceError
from datetime import timedelta
import uuid

from fastapi.testclient import TestClient
import pytest

from tinystudio.lab import service as svc
from tinystudio.lab.models import LabAttempt
from tinystudio.lab.transfer_configuration import AttemptArtifactApiPaths
from tinystudio.lab.client_paths import RunnerApiPaths
from tinystudio.lab.finalization_configuration import FinalizationApiPaths
from tests.test_lab_transfer_api import database, setup, fixture
from tests.test_lab_foundation import definition


def test_attempt_artifact_paths_share_the_control_collection_and_encode_ids():
    paths = AttemptArtifactApiPaths('/api/lab/runner/target/attempts/attempt')
    assert paths.transfers.collection == paths.base + '/transfers'
    assert paths.policy_route == paths.transfers.policy_route
    assert paths.input_download('asset +') == paths.base + '/inputs/asset%20%2B/download'
    assert paths.input_proxy('asset +') == paths.base + '/inputs/asset%20%2B'
    assert paths.output_proxy == paths.base + '/artifacts'
    assert AttemptArtifactApiPaths.at_runner('/attempts/{id_}').input_download_route == '/attempts/{id_}/inputs/{artifact_id}/download'
    with pytest.raises(FrozenInstanceError):
        paths.base = '/other'
    for invalid in ('//external/attempt', '/a/../b', '/a?key=value', '/a\\b', '/a/'):
        with pytest.raises(ValueError):
            AttemptArtifactApiPaths(invalid)
    for invalid in ('../asset', 'asset?key=value', 'asset#fragment'):
        with pytest.raises(ValueError):
            paths.input_download(invalid)


def test_shared_runner_scope_drives_artifact_and_finalization_routes():
    routes = RunnerApiPaths()
    scope = routes.attempt('selected-target', 'selected-attempt')
    assert scope == '/api/lab/runner/selected-target/attempts/selected-attempt'
    assert FinalizationApiPaths.for_runner('selected-target', 'selected-attempt').base == scope
    assert AttemptArtifactApiPaths(scope).transfers.collection == scope + '/transfers'
    assert routes.target_route == '/api/lab/runner/{target_id}'
    assert routes.relative_attempt('selected-attempt') == '/attempts/selected-attempt'
    for invalid in ('../another-target', 'target?query=true', 'target#fragment'):
        with pytest.raises(ValueError):
            routes.target(invalid)


@pytest.mark.parametrize('remote', [False, True])
def test_runner_policy_requires_live_exact_attempt_and_does_no_provider_io(fixture, remote):
    factory, project, other, client, base, app, store = fixture
    store.storage.remote = remote
    target = client.post(base + '/targets', json={'name': 'owned-capability-target'}).json()
    from tinystudio.lab.runner import capabilities
    with TestClient(app, headers={'Authorization': 'Bearer ' + target['token']}) as machine:
        runner = '/api/lab/runner/' + target['id']
        assert machine.post(runner + '/advertise', json={'capabilities': capabilities()}).status_code == 200
        saved = client.post(base + '/definitions', json={'definition': definition()}).json()
        submitted = client.post(base + '/runs', json={'definition_id': saved['id'], 'revision': 1,
            'target_id': target['id'], 'idempotency_key': uuid.uuid4().hex})
        assert submitted.status_code == 202, submitted.text
        claimed = machine.post(runner + '/claim', json={'capabilities': capabilities()}).json()['attempt']
        paths = AttemptArtifactApiPaths(runner + '/attempts/' + claimed['attempt_id'])
        headers = {'X-Lab-Lease': claimed['lease_token']}
        assert machine.get(paths.policy_route).status_code == 403
        assert machine.get(paths.policy_route, headers={'X-Lab-Lease': 'wrong'}).status_code == 403
        response = machine.get(paths.policy_route, headers=headers)
        assert response.status_code == 200, response.text
        policy = response.json()
        assert policy['protocol_version'] == 1
        assert policy['finalization_protocol_version'] == 1
        assert policy['backend'] == ('r2' if remote else 'local')
        assert policy['resumable'] is remote
        assert policy['direct_download'] is remote
        assert policy['policy'] == client.get(base + '/transfer-policy').json()['policy']
        assert store.calls == []
        with factory.begin() as db:
            db.get(LabAttempt, claimed['attempt_id']).lease_until = svc.now() - timedelta(seconds=1)
        assert machine.get(paths.policy_route, headers=headers).status_code == 409
    with TestClient(app) as anonymous:
        assert anonymous.get(paths.policy_route, headers=headers).status_code == 401
