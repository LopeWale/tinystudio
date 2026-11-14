"""Parent cleanup terminates its own lease even between device submissions."""
import pytest

from tinystudio.lab.gateway import CommandGateway, GatewayError
from tests.test_lab_native_gateway import Clock, Device, binding, dispatch, payload


def selected(**policy):
    return binding(local_stop=payload('OUTPUT OFF\n'), lease_policy=policy)


@pytest.mark.parametrize('trigger', ['cancel', 'expiry'])
def test_parent_termination_uses_selected_policy_and_retains_exact_fence(tmp_path, trigger):
    device = Device()
    with CommandGateway(tmp_path / 'gateway.sqlite3', selected(**{'on_' + trigger: 'local_stop'}), device, clock=Clock()) as gateway:
        lease = gateway.acquire('attempt', ttl=20)
        receipt = gateway.terminate_lease('parent-end', owner=lease['owner'], epoch=lease['epoch'], trigger=trigger)
        assert receipt['trigger'] == trigger and receipt['action'] == 'local_stop'
        assert receipt['lease_epoch'] == lease['epoch']
        assert receipt['physical_outcome'] == 'unverified'
        assert [effect['kind'] for effect in device.effects] == ['local_stop']
        assert gateway.snapshot()['state'] == 'recovery_required'
        assert gateway.terminate_lease('parent-end', owner=lease['owner'], epoch=lease['epoch'], trigger=trigger) == receipt
        assert len(device.effects) == 1


def test_parent_does_not_repeat_inflight_cancel_or_pending_expiry(tmp_path):
    for trigger in ['cancel', 'expiry']:
        clock, device = Clock(), Device()
        with CommandGateway(tmp_path / (trigger + '.sqlite3'), selected(on_expiry='local_stop'), device, clock=clock) as gateway:
            lease = gateway.acquire('attempt', ttl=20)
            if trigger == 'cancel':
                with pytest.raises(GatewayError):
                    dispatch(gateway, lease, cancelled=lambda: True)
            else:
                clock.value = 1021.
                gateway.snapshot()  # Durable pending expiry has not effected a stop.
            receipt = gateway.terminate_lease('parent-end', owner=lease['owner'], epoch=lease['epoch'], trigger='cancel')
            assert receipt['trigger'] == trigger
            assert len(device.effects) == 1 and len(gateway.lease_events()) == 1
            assert gateway.terminate_lease('parent-again', owner=lease['owner'], epoch=lease['epoch'], trigger='cancel') == receipt
            assert len(device.effects) == 1


def test_parent_unknown_stop_is_not_replayed(tmp_path):
    device = Device()
    device.failure = TimeoutError('owned lost delivery response')
    with CommandGateway(tmp_path / 'gateway.sqlite3', selected(), device, clock=Clock()) as gateway:
        lease = gateway.acquire('attempt', ttl=20)
        with pytest.raises(GatewayError):
            gateway.terminate_lease('parent-end', owner=lease['owner'], epoch=lease['epoch'], trigger='cancel')
        with pytest.raises(GatewayError, match='uncertain'):
            gateway.terminate_lease('parent-again', owner=lease['owner'], epoch=lease['epoch'], trigger='cancel')
        assert len(device.effects) == 1 and gateway.lease_events()[0]['state'] == 'unknown'


def test_stale_cleanup_cannot_stop_a_later_owner_and_replay_is_read_only(tmp_path):
    device = Device()
    with CommandGateway(tmp_path / 'gateway.sqlite3', selected(), device, clock=Clock()) as gateway:
        old = gateway.acquire('old', ttl=20)
        prior = gateway.release('normal-end', owner=old['owner'], epoch=old['epoch'])
        new = gateway.acquire('new', ttl=20)
        assert gateway.terminate_lease('parent-end', owner=old['owner'], epoch=old['epoch'], trigger='cancel') == prior
        assert gateway.snapshot()['owner'] == new['owner'] and device.effects == []
        with pytest.raises(GatewayError):
            gateway.terminate_lease('bad', owner='other', epoch=new['epoch'], trigger='cancel')
        with pytest.raises(GatewayError):
            gateway.terminate_lease('bad', owner=new['owner'], epoch=True, trigger='cancel')
        with pytest.raises(GatewayError):
            gateway.terminate_lease('bad', owner=new['owner'], epoch=new['epoch'], trigger='release')
        assert device.effects == []


def test_termination_request_identity_cannot_be_rebound(tmp_path):
    with CommandGateway(tmp_path / 'gateway.sqlite3', selected(), Device(), clock=Clock()) as gateway:
        old = gateway.acquire('old', ttl=20)
        gateway.terminate_lease('same', owner=old['owner'], epoch=old['epoch'], trigger='cancel')
        gateway.reconcile('operator-one', evidence={'acknowledge_unverified_outcomes': True})
        new = gateway.acquire('new', ttl=20)
        with pytest.raises(GatewayError, match='different immutable request'):
            gateway.terminate_lease('same', owner=new['owner'], epoch=new['epoch'], trigger='cancel')
        assert gateway.snapshot()['owner'] == new['owner']


@pytest.mark.parametrize('restart', [False, True])
def test_late_cancel_after_unknown_ordinary_command_uses_persisted_revocation_proof(tmp_path, restart):
    clock, device = Clock(), Device()
    path = tmp_path / 'gateway.sqlite3'
    gateway = CommandGateway(path, selected(), device, clock=clock)
    try:
        lease = gateway.acquire('attempt', ttl=20)
        device.failure = TimeoutError('owned ordinary command response loss')
        with pytest.raises(GatewayError):
            dispatch(gateway, lease)
        assert gateway.snapshot()['state'] == 'recovery_required' and gateway.lease_events() == []
        device.failure = None
        if restart:
            gateway.close()
            gateway = CommandGateway(path, selected(), device, clock=clock)
        receipt = gateway.terminate_lease('late-cancel', owner=lease['owner'], epoch=lease['epoch'], trigger='cancel')
        assert receipt['trigger'] == 'cancel' and receipt['lease_expires'] == 1020.
        assert [effect['kind'] for effect in device.effects] == ['command', 'local_stop']
        assert gateway.journal()[0]['state'] == 'unknown'
        assert gateway.terminate_lease('another-cancel', owner=lease['owner'], epoch=lease['epoch'], trigger='cancel') == receipt
        assert len(device.effects) == 2
    finally:
        gateway.close()


def test_unknown_old_command_proof_cannot_terminate_after_operator_recovery(tmp_path):
    device = Device()
    with CommandGateway(tmp_path / 'gateway.sqlite3', selected(), device, clock=Clock()) as gateway:
        old = gateway.acquire('old', ttl=20)
        device.failure = TimeoutError('owned ordinary command response loss')
        with pytest.raises(GatewayError):
            dispatch(gateway, old)
        device.failure = None
        gateway.reconcile('operator-one', evidence={'acknowledge_unverified_outcomes': True})
        new = gateway.acquire('new', ttl=20)
        with pytest.raises(GatewayError):
            gateway.terminate_lease('late-cancel', owner=old['owner'], epoch=old['epoch'], trigger='cancel')
        assert gateway.snapshot()['owner'] == new['owner']
        assert len(device.effects) == 1


def test_alias_of_prior_action_cannot_be_rebound_to_a_later_lease(tmp_path):
    device = Device()
    with CommandGateway(tmp_path / 'gateway.sqlite3', selected(), device, clock=Clock()) as gateway:
        old = gateway.acquire('old', ttl=20)
        prior = gateway.release('normal-release', owner=old['owner'], epoch=old['epoch'])
        assert gateway.terminate_lease('alias', owner=old['owner'], epoch=old['epoch'], trigger='cancel') == prior
        new = gateway.acquire('new', ttl=20)
        with pytest.raises(GatewayError, match='different immutable request'):
            gateway.terminate_lease('alias', owner=new['owner'], epoch=new['epoch'], trigger='cancel')
        assert gateway.snapshot()['owner'] == new['owner'] and device.effects == []
