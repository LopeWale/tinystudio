"""Catalog/admission and genuine registered HTTP/native-child planning workflow."""
import hashlib
import json

import pytest

from tinystudio.lab.contracts import ExperimentDefinition
from tinystudio.lab import service as svc
from tinystudio.lab.preflight import execution_checks
from tinystudio.lab.tools import tool_specs
from tinystudio.integrations.tools import ToolSpec
from tests.test_lab_native_workflow import native_http, enroll, posted


def definition():
    return {'name': 'Paired planning fixture', 'engine': 'statistics.plan',
            'parameters': {'method': 'means.paired', 'alpha': .05, 'power': .8,
                'alternative': 'two-sided', 'delta': .5, 'sd': 1, 'unit': 'V',
                'input_sources': {'sd': 'Authored planning assumption, not measured data'},
                'sensitivity': [{'delta': .25}]},
            'execution': {'profile': 'trusted_native', 'backend': 'native', 'max_seconds': 60}}


def test_method_catalog_advertises_dependency_and_preflight_rejects_invalid_settings():
    catalog = svc.catalog()
    rows = [m for m in catalog['methods'] if m['id'] == 'statistics.plan']
    assert len(rows) == 1, 'statistics.plan is not registered'
    assert rows[0]['implemented'] and rows[0]['dependencies'] == ['scipy']
    assert rows[0]['manifest']['fixtures']
    caps = {'engines': ['statistics.plan'], 'runtime': {'python': '3.12', 'scipy': '1.18'}}
    good = ExperimentDefinition.model_validate(definition())
    assert all(row['passed'] for row in execution_checks(good, caps))
    invalid = definition()
    invalid['parameters'].pop('sd')
    checks = execution_checks(ExperimentDefinition.model_validate(invalid), caps)
    assert not next(row for row in checks if row['code'] == 'statistics_selection')['passed']
    checks = execution_checks(good, {**caps, 'runtime': {'python': '3.12'}})
    assert not next(row for row in checks if row['code'] == 'statistics_selection')['passed']


def test_assistant_uses_same_metadata_definition_and_run_tools():
    calls = []
    class Client:
        def call(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {'id': 'definition', 'revision': 1}
    tools = {t.name: t for t in tool_specs(Client(), ToolSpec)}
    assert 'lab_statistics_methods' in tools, 'assistant cannot inspect planning methods'
    assert 'lab_statistics_define' in tools, 'assistant cannot retain a planning definition'
    tools['lab_statistics_methods'].func()
    body = definition()
    tools['lab_statistics_define'].func(body['name'], body['parameters'], body['execution'])
    assert calls[0] == ('GET', '/api/lab/statistics/methods', {})
    assert calls[1] == ('POST', '/api/lab/definitions', {'json': {'definition': body}})
    assert tools['lab_statistics_methods'].read_only
    assert not tools['lab_statistics_define'].read_only


def test_cli_exposes_method_metadata_without_inventing_a_calculation(monkeypatch):
    from tinystudio.lab import cli
    from tinystudio.cli.main import app
    from typer.testing import CliRunner
    calls = []
    monkeypatch.setattr(cli, 'call', lambda *args: calls.append(args) or {'methods': []})
    result = CliRunner().invoke(app, ['lab', 'statistics-methods', '--project', 'research'])
    assert result.exit_code == 0, result.output
    assert calls == [('GET', 'research', '/statistics/methods')]


def test_http_native_child_freezes_reopens_and_exports_planning_evidence(native_http):
    f = native_http
    response = f.http.get(f.prefix+'/statistics/methods')
    assert response.status_code == 200, response.text
    assert len(response.json()['methods']) == 4
    target, runner = enroll(f, 'statistics-native')
    try:
        body = definition()
        from tinystudio.integrations.tools import Studio
        studio = Studio(f.url, project='native-b', token=f.http.headers['authorization'][7:])
        try:
            tools = {tool.name: tool for tool in tool_specs(studio, ToolSpec)}
            metadata = tools['lab_statistics_methods'].func()
            assert len(metadata['methods']) == 4
            saved = tools['lab_statistics_define'].func(body['name'], body['parameters'], body['execution'])
        finally:
            studio.http.close()
        check = posted(f.http, f.prefix+'/preflight', json={'definition': body, 'target_id': target['id']})
        assert check['compatible'], check
        submitted = posted(f.http, f.prefix+'/runs', json={'definition_id': saved['id'], 'revision': 1,
                            'target_id': target['id'], 'idempotency_key': 'statistics-calculation'})
        changed = {**body, 'parameters': {**body['parameters'], 'delta': 10}}
        response = f.http.put(f.prefix+'/definitions/'+saved['id'],
                             json={'definition': changed, 'expected_revision': 1})
        assert response.status_code == 200, response.text
        assert runner.run_once(run_id=submitted['id'])
    finally:
        runner.client.close()
    reopened = f.http.get(f.prefix+'/runs/'+submitted['id']).json()
    assert reopened['state'] == 'succeeded', reopened.get('error')
    assert reopened['spec']['definition']['parameters']['delta'] == .5
    artifact = next(a for a in reopened['result']['artifacts'] if a['role'] == 'statistical_plan')
    content = f.http.get(f.prefix+'/artifacts/'+artifact['id'])
    assert content.status_code == 200
    assert hashlib.sha256(content.content).hexdigest() == artifact['sha256']
    report = content.json()
    assert report['records'][0]['result']['analysis_n'] == {'pairs': 34}
    assert report['records'][1]['result']['analysis_n']['pairs'] > 34
    assert report['runtime']['scipy']
    receipt = reopened['result']['execution_receipt']
    assert receipt['runtime']['scipy'] == report['runtime']['scipy']
    assert f.http.get(f.restored+'/runs/'+submitted['id']).status_code == 404
    assert f.http.get(f.restored+'/artifacts/'+artifact['id']).status_code == 404
    exported = posted(f.http, f.prefix+'/runs/'+submitted['id']+'/exports',
                      json={'idempotency_key': 'statistics-export'})
    assert exported['state'] == 'queued'
    from tinystudio.lab import operations
    assert operations.run_operation(exported['id'], f.factory)['state'] == 'succeeded'
    from zipfile import ZipFile
    import io
    downloaded = f.http.get(f.prefix+'/operations/'+exported['id']+'/download')
    assert downloaded.status_code == 200, downloaded.text
    with ZipFile(io.BytesIO(downloaded.content)) as bundle:
        assert bundle.testzip() is None
        # Portable bundle addresses retained bytes by SHA, not by display name.
        assert any(bundle.read(name) == content.content for name in bundle.namelist()
                   if not name.endswith('/'))
