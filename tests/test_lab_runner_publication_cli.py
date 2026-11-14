"""Publication recovery is explicit, credential scoped and cannot rerun work."""
import json

from typer.testing import CliRunner

from tinystudio.lab import cli, runner as runner_module


def credential(tmp_path, mode=0o600):
    path = tmp_path / 'operator.json'
    path.write_text(json.dumps({'server': 'https://studio.fixture.invalid',
        'target_id': 'owned-target', 'token': 'private-target-token'}))
    path.chmod(mode)
    return path


def test_explicit_resume_prompts_privately_and_only_calls_publication(tmp_path, monkeypatch):
    calls = []
    class Client:
        def close(self): calls.append(('close',))
    class Runner:
        def __init__(self, *args, **kwargs):
            calls.append(('construct', args, kwargs)); self.client = Client()
        def resume_publication(self, root, *, lease_token):
            calls.append(('resume', root, lease_token))
            return {'accepted': True, 'state': 'failed'}
        def execute_attempt(self, *args):
            raise AssertionError('Recovery must never execute the child')
    monkeypatch.setattr(runner_module, 'Runner', Runner)
    result = CliRunner().invoke(cli.app, ['runner-resume', str(tmp_path),
        '--credential-file', str(credential(tmp_path))], input='private-attempt-token\n')
    assert result.exit_code == 0, result.output
    assert calls == [('construct', ('https://studio.fixture.invalid', 'owned-target', 'private-target-token'),
        {'profile_config': None, 'access': None}), ('resume', tmp_path, 'private-attempt-token'), ('close',)]
    assert '"accepted": true' in result.output and '"state": "failed"' in result.output
    assert 'private-attempt-token' not in result.output and 'private-target-token' not in result.output


def test_uncertain_resume_closes_transport_and_does_not_expose_exception_credentials(tmp_path, monkeypatch):
    closed = []
    class Client:
        def close(self): closed.append(True)
    class Runner:
        def __init__(self, *args, **kwargs): self.client = Client()
        def resume_publication(self, *args, **kwargs):
            raise RuntimeError('https://objects.invalid/?signature=private-signature')
    monkeypatch.setattr(runner_module, 'Runner', Runner)
    result = CliRunner().invoke(cli.app, ['runner-resume', str(tmp_path),
        '--credential-file', str(credential(tmp_path))], input='private-attempt-token\n')
    assert result.exit_code != 0 and closed == [True]
    assert 'unconfirmed' in result.output and 'retained' in result.output
    assert 'private-signature' not in result.output and 'private-attempt-token' not in result.output


def test_resume_refuses_nonprivate_operator_credential_before_constructing_runner(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('Invalid credentials must not construct a client')
    monkeypatch.setattr(runner_module, 'Runner', unexpected)
    result = CliRunner().invoke(cli.app, ['runner-resume', str(tmp_path),
        '--credential-file', str(credential(tmp_path, 0o644))], input='private-attempt-token\n')
    assert result.exit_code != 0
    assert 'unconfirmed' in result.output and 'retained' in result.output
    assert 'private-target-token' not in result.output and 'private-attempt-token' not in result.output
