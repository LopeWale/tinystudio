"""Native budgets include worktree bytes; publication has a separate lease."""
import copy

import pytest
from types import SimpleNamespace

from tests.test_lab_runner import Transport, attempt, runner, source
from tinystudio.lab.contracts import checksum
from tinystudio.lab.runner import LeaseHeartbeat


@pytest.mark.parametrize('seconds,output', [(None, None), (None, 1024 ** 2)])
def test_native_none_budget_executes_owned_child(source, tmp_path, monkeypatch, seconds, output):
    claimed = attempt(source)
    claimed['spec']['definition']['execution'].update(max_seconds=seconds, max_output_bytes=output)
    claimed['spec_digest'] = checksum(claimed['spec'])
    frozen = copy.deepcopy(claimed)
    transport = Transport()
    root = tmp_path / 'attempt'
    root.mkdir()
    runner(source, transport, monkeypatch).execute_attempt(claimed, root)
    assert transport.finish['state'] == 'succeeded', transport.finish
    assert transport.uploads
    assert claimed == frozen


def test_zero_wall_budget_stops_before_start_but_can_publish_failure(source, tmp_path, monkeypatch):
    claimed = attempt(source)
    claimed['spec']['definition']['execution']['max_seconds'] = 0
    claimed['spec_digest'] = checksum(claimed['spec'])
    transport = Transport()
    root = tmp_path / 'attempt'
    root.mkdir()
    worker = runner(source, transport, monkeypatch)
    monkeypatch.setattr(worker, '_download', lambda *a: pytest.fail('zero wall budget cannot stage or launch'))
    worker.execute_attempt(claimed, root)
    assert transport.finish['state'] == 'failed'
    assert transport.finish['result'] is None
    assert not transport.uploads
    assert not (root / 'runtime').exists()


def test_zero_output_budget_charges_result_and_logs_not_only_artifacts(source, tmp_path, monkeypatch):
    claimed = attempt(source)
    claimed['spec']['definition']['execution'].update(max_seconds=None, max_output_bytes=0)
    claimed['spec_digest'] = checksum(claimed['spec'])
    transport = Transport()
    root = tmp_path / 'attempt'
    root.mkdir()
    runner(source, transport, monkeypatch).execute_attempt(claimed, root)
    assert transport.finish['state'] == 'failed'
    assert transport.finish['result'] is None
    assert not transport.uploads
    assert any(path.stat().st_size for path in (root / 'work').rglob('*') if path.is_file())


def test_none_wall_still_latches_known_server_lease_expiry(tmp_path,monkeypatch):
    from tinystudio.lab import runner as module
    clock=[10.0];calls=[]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    def post(*args):calls.append(args);return {'cancel_requested':False,'lease_seconds':5}
    heartbeat=LeaseHeartbeat(SimpleNamespace(post=post),'/attempts/one','secret',tmp_path,interval=1,deadline=None)
    heartbeat.renew();clock[0]=15
    with pytest.raises(TimeoutError,match='lease'):heartbeat.check_authority()
    clock[0]=10
    with pytest.raises(TimeoutError,match='lease'):heartbeat.renew()
    assert len(calls)==1 and (tmp_path/'cancel').exists()


def test_late_heartbeat_cannot_reactivate_after_previously_known_lease(tmp_path,monkeypatch):
    from tinystudio.lab import runner as module
    clock=[0.0]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    calls=[]
    def post(*args):
        calls.append(args)
        if len(calls)==2:clock[0]=6
        return {'cancel_requested':False,'lease_seconds':5}
    heartbeat=LeaseHeartbeat(SimpleNamespace(post=post),'/attempts/one','secret',tmp_path,interval=1,deadline=None)
    heartbeat.renew();clock[0]=4
    with pytest.raises(TimeoutError,match='lease'):heartbeat.renew()
    with pytest.raises(TimeoutError,match='lease'):heartbeat.check_authority()


@pytest.mark.parametrize('failure',[KeyboardInterrupt,OSError])
def test_parent_publication_failure_always_stops_owned_heartbeat(source,tmp_path,monkeypatch,failure):
    from tinystudio.lab import runner as module
    from tinystudio.lab.runner_publication import AttemptPublication
    original=module.LeaseHeartbeat;created=[]
    def heartbeat(*args,**kwargs):
        value=original(*args,**kwargs);created.append(value);return value
    monkeypatch.setattr(module,'LeaseHeartbeat',heartbeat)
    save=AttemptPublication.save
    def broken(self,path,payload,**kwargs):
        if path==self.paths.cleanup:raise failure('owned metadata interruption')
        return save(self,path,payload,**kwargs)
    monkeypatch.setattr(AttemptPublication,'save',broken)
    claimed=attempt(source);transport=Transport();root=tmp_path/'attempt';root.mkdir()
    try:
        with pytest.raises(failure):runner(source,transport,monkeypatch).execute_attempt(claimed,root)
        assert len(created)==1
        assert created[0].stop_event.is_set() and not created[0].thread.is_alive()
    finally:
        for value in created:value.stop()
