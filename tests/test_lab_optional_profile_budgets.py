"""Optional profile budgets use structural plans and owned backend test doubles."""
from copy import deepcopy
from dataclasses import replace
import json
import subprocess

import pytest

from tests.test_lab_profiles import DockerFixture, container, kube, request_
from tinystudio.lab import profiles
from tinystudio.lab.contracts import ExecutionProfile, ExperimentDefinition
from tinystudio.lab.execution_configuration import (
    DEFAULT_MAX_SECONDS, ExecutionBudgetExceeded,
)
from tinystudio.lab.preflight import execution_checks, resource_selection
from tinystudio.lab.profiles import Limits, ProfileExecution, RunnerGrants, build_profile


@pytest.mark.parametrize('seconds', [None, 0, 1, 86401, 10 ** 100])
def test_wall_limits_preserve_optional_zero_and_large_whole_second_choices(seconds):
    limits = Limits(wall_seconds=seconds)
    limits.validate()
    assert limits.wall_seconds == seconds
    assert Limits().wall_seconds == DEFAULT_MAX_SECONDS


@pytest.mark.parametrize('seconds', [True, False, -1, 1.0, '300', float('nan'), float('inf')])
def test_wall_limit_validation_stays_strict_and_rejects_malformed_values(seconds):
    with pytest.raises(ValueError): Limits(wall_seconds=seconds).validate()


def plan(request_, tmp_path, backend, seconds, monkeypatch):
    request = request_ if backend == 'native' else container(request_) if backend == 'container' else kube(request_, tmp_path)
    if backend == 'native':
        monkeypatch.setattr(profiles.shutil, 'which', lambda name: '/fixture/sandbox-exec')
    request = replace(request, limits=replace(request.limits, wall_seconds=seconds))
    return build_profile(request, RunnerGrants(network_policy_enforced=True), system='Darwin')


@pytest.mark.parametrize('backend', ['native', 'container', 'kubernetes'])
def test_unbounded_profile_plans_keep_none_and_never_emit_a_null_kubernetes_deadline(request_, tmp_path, monkeypatch, backend):
    selected = plan(request_, tmp_path, backend, None, monkeypatch)
    assert selected.manifest()['limits']['wall_seconds'] is None
    assert selected.manifest()['qualification'] == 'unqualified_until_denial_fixture'
    if backend == 'kubernetes':
        job = next(item for item in json.loads(selected.manifests_json)['items'] if item['kind'] == 'Job')
        assert 'activeDeadlineSeconds' not in job['spec']


@pytest.mark.parametrize('backend', ['native', 'container', 'kubernetes'])
def test_zero_wall_budget_cannot_produce_a_runnable_backend_plan(request_, tmp_path, monkeypatch, backend):
    with pytest.raises(ExecutionBudgetExceeded) as raised:
        plan(request_, tmp_path, backend, 0, monkeypatch)
    assert (raised.value.resource, raised.value.maximum, raised.value.actual) == ('wall_time', 0, 0)


@pytest.mark.parametrize('seconds', [86401, 2 ** 63 - 1])
def test_positive_kubernetes_deadline_keeps_the_authored_int64_value(request_, tmp_path, monkeypatch, seconds):
    selected = plan(request_, tmp_path, 'kubernetes', seconds, monkeypatch)
    job = next(item for item in json.loads(selected.manifests_json)['items'] if item['kind'] == 'Job')
    assert job['spec']['activeDeadlineSeconds'] == seconds


def test_kubernetes_int64_protocol_limit_is_not_a_universal_profile_wall_ceiling(request_, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='Kubernetes.*int64'):
        plan(request_, tmp_path, 'kubernetes', 2 ** 63, monkeypatch)
    assert plan(request_, tmp_path, 'container', 2 ** 63, monkeypatch).request.limits.wall_seconds == 2 ** 63


class NativeFixture:
    def __init__(self, calls):
        self.calls = calls
        self.pgid = 12345
        self.process = self
        self.closed = False

    def poll(self):
        self.calls.append('poll')
        return None

    def close(self):
        self.calls.append('close')
        self.closed = True


class KubernetesFixture:
    def __init__(self, plan):
        self.plan, self.calls = plan, []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if 'create' in args:
            resource = json.loads(kwargs['input'])
            resource['metadata']['uid'] = 'uid-' + resource['kind']
        elif 'get' in args:
            resource = {'metadata': {'uid': 'uid-Job', 'labels': {'tinystudio.owner': self.plan.owner}}, 'status': {}}
        else:
            resource = {}
        return subprocess.CompletedProcess(args, 0, json.dumps(resource), '')


def owner(selected, monkeypatch):
    if selected.backend == 'native':
        from tinystudio.lab import runner
        calls = []
        native = NativeFixture(calls)
        def create(*args, **kwargs):
            calls.append('launch')
            return native
        monkeypatch.setattr(runner, 'ProcessGroup', create)
        monkeypatch.setattr(profiles.platform, 'system', lambda: 'Darwin')
        return ProfileExecution(selected), calls
    fake = DockerFixture(selected) if selected.backend == 'container' else KubernetesFixture(selected)
    return ProfileExecution(selected, command_runner=fake, termination_probe=lambda identity: True), fake.calls


@pytest.mark.parametrize('backend', ['native', 'container', 'kubernetes'])
def test_direct_start_of_a_manually_zeroed_plan_cannot_call_any_backend(request_, tmp_path, monkeypatch, backend):
    selected = plan(request_, tmp_path, backend, 1, monkeypatch)
    selected = replace(selected, request=replace(selected.request, limits=replace(selected.request.limits, wall_seconds=0)))
    execution, calls = owner(selected, monkeypatch)
    with pytest.raises(ExecutionBudgetExceeded): execution.start()
    assert calls == [] and execution.closed


@pytest.mark.parametrize('backend', ['native', 'container', 'kubernetes'])
def test_poll_accepts_none_without_adding_an_authored_timeout(request_, tmp_path, monkeypatch, backend):
    selected = plan(request_, tmp_path, backend, None, monkeypatch)
    clock = [10.25]
    monkeypatch.setattr(profiles.time, 'monotonic', lambda: clock[0])
    execution, calls = owner(selected, monkeypatch)
    execution.start()
    clock[0] += 10 ** 100
    assert execution.poll() == (0 if backend == 'container' else None)
    assert not execution.closed
    execution.close()


@pytest.mark.parametrize('backend', ['native', 'container', 'kubernetes'])
def test_poll_exhausts_at_the_exact_deadline_and_closes_only_its_owned_execution(request_, tmp_path, monkeypatch, backend):
    selected = plan(request_, tmp_path, backend, 2, monkeypatch)
    clock = [100.25]
    monkeypatch.setattr(profiles.time, 'monotonic', lambda: clock[0])
    execution, calls = owner(selected, monkeypatch)
    execution.start()
    clock[0] = 102.249
    assert execution.poll() == (0 if backend == 'container' else None)
    clock[0] = 102.25
    with pytest.raises(TimeoutError, match='wall-time'):
        execution.poll()
    assert execution.closed


@pytest.mark.parametrize('seconds', [None, 0, 86401, 10 ** 100])
def test_preflight_preserves_resources_and_authored_optional_budget_without_type_errors(seconds):
    definition = ExperimentDefinition(name='budget fixture', engine='synthetic.signal', execution={
        'profile': 'isolated', 'backend': 'container', 'max_seconds': seconds, 'max_output_bytes': None,
        'requirements': {'cpu': .5, 'memory_bytes': 32 * 1024 ** 2, 'pids': 8}})
    before = deepcopy(definition.model_dump())
    capabilities = {'profiles_by_backend': {'container': ['isolated']},
        'engines_by_backend': {'container': ['synthetic.signal']}, 'runtime_by_backend': {'container': {'python': '3.12'}},
        'resource_limits_by_backend': {'container': {'cpu': 2, 'memory_bytes': 512 * 1024 ** 2, 'pids': 64}}}
    checks = {check['code']: check for check in execution_checks(definition, capabilities)}
    assert checks['resource_limits']['passed'] is True
    assert definition.model_dump() == before


@pytest.mark.parametrize('seconds', [None, 0])
def test_optional_wall_time_does_not_relax_existing_cpu_memory_or_pid_policies(seconds):
    for requirement in ({'cpu': 0}, {'cpu': 65}, {'memory_bytes': 0}, {'pids': 0}, {'pids': 4097}):
        execution = ExecutionProfile(profile='isolated', backend='container', max_seconds=seconds, requirements=requirement)
        with pytest.raises(ValueError): resource_selection(execution, {})


def test_container_setup_exhaustion_cleans_created_identity_without_starting_it(request_, tmp_path, monkeypatch):
    selected = plan(request_, tmp_path, 'container', 2, monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(profiles.time, 'monotonic', lambda: clock[0])
    fake = DockerFixture(selected)

    def slow_inspect(args, **kwargs):
        response = fake(args, **kwargs)
        if args[1] == 'inspect':
            clock[0] = 102.0
        return response

    execution = ProfileExecution(selected, command_runner=slow_inspect)
    with pytest.raises(ExecutionBudgetExceeded): execution.start()
    assert not any(args[1] == 'start' for args in fake.calls)
    assert fake.removed and execution.closed


def test_kubernetes_setup_exhaustion_deletes_only_created_policy_and_never_creates_job(request_, tmp_path, monkeypatch):
    selected = plan(request_, tmp_path, 'kubernetes', 2, monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(profiles.time, 'monotonic', lambda: clock[0])
    fake = KubernetesFixture(selected)
    created = []

    def slow_policy(args, **kwargs):
        response = fake(args, **kwargs)
        if 'create' in args:
            created.append(json.loads(kwargs['input'])['kind'])
            clock[0] = 102.0
        return response

    execution = ProfileExecution(selected, command_runner=slow_policy)
    with pytest.raises(ExecutionBudgetExceeded): execution.start()
    assert created == ['NetworkPolicy'] and execution.closed
    assert execution.deleted == {('NetworkPolicy', 'uid-NetworkPolicy')}
