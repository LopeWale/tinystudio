"""Parent authority is a monotonic lease latch, independent of network/device I/O."""
from dataclasses import FrozenInstanceError
import hashlib
import json
import threading

import pytest

from tinystudio.lab.attempt_control import AttemptAuthorityLost, AttemptControl, AttemptIdentity


class Clock:
    def __init__(self, value=100.): self.value = value
    def __call__(self): return self.value


def identity(**changes):
    return AttemptIdentity(**{'server': 'https://research.example:8443', 'run_id': 'run-one',
        'attempt_id': 'attempt-one', 'target_id': 'target-one', 'spec_digest': 'a' * 64, **changes})


def test_identity_is_exact_frozen_defensive_and_full_tuple_hash_bound():
    selected = identity(run_id='研究-' + 'x' * 10_000)
    document = selected.as_dict()
    expected = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'),
                                          ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert selected.owner == expected
    assert set(document) == {'server', 'run_id', 'attempt_id', 'target_id', 'spec_digest'}
    document['run_id'] = 'changed'
    assert selected.as_dict()['run_id'].startswith('研究-')
    with pytest.raises(FrozenInstanceError): selected.run_id = 'changed'
    assert identity().owner != identity(attempt_id='attempt-two').owner
    assert identity().owner != identity(target_id='target-two').owner
    assert identity().owner != identity(spec_digest='b' * 64).owner
    assert identity().owner != identity(server='https://other.example').owner


@pytest.mark.parametrize('server', ['https://user:private@research.example', 'https://@research.example',
    'https://research.example?token=private', 'https://research.example?', 'https://research.example#fragment',
    'https://research.example#', 'https://research.example/deployment', 'file:///tmp/owned', '',
    'https://research.example:99999', ' https://research.example', 'https://research.example\n'])
def test_identity_rejects_non_origin_or_credential_bearing_urls(server):
    with pytest.raises(ValueError): identity(server=server)


@pytest.mark.parametrize('field,value', [('run_id', ''), ('run_id', 1), ('attempt_id', None),
    ('target_id', True), ('spec_digest', 'A' * 64), ('spec_digest', 'a' * 63)])
def test_identity_requires_exact_strings_and_a_sha256_digest(field, value):
    with pytest.raises(ValueError): identity(**{field: value})


def test_check_starts_unrenewed_but_a_fresh_first_heartbeat_can_activate():
    clock = Clock()
    control = AttemptControl(identity(), monotonic=clock)
    with pytest.raises(AttemptAuthorityLost): control.check()
    assert control.snapshot()['authorized'] is False
    assert control.snapshot()['renewed'] is False and control.snapshot()['revoked'] is False
    assert control.renew(20, sent_at=99.) is None
    assert control.check() is None
    assert control.remaining() == 19.
    state = control.snapshot()
    assert state['monotonic_deadline'] == 119.
    assert state['lease_deadline'] == 119.
    assert state['identity'] == identity().as_dict() and state['owner'] == identity().owner
    state['identity']['run_id'] = 'changed'
    assert control.identity == identity() and control.snapshot()['identity']['run_id'] == 'run-one'


def test_renewal_deadline_uses_send_time_instead_of_response_arrival():
    clock = Clock(115.)
    control = AttemptControl(identity(), monotonic=clock)
    control.renew(20, sent_at=100.)
    assert control.remaining() == 5.
    clock.value = 120.
    with pytest.raises(AttemptAuthorityLost): control.check()
    assert control.snapshot()['revocation_reason'] == 'lease_expired'


def test_a_delayed_first_heartbeat_cannot_activate_an_already_expired_grant():
    control = AttemptControl(identity(), monotonic=Clock(120.))
    with pytest.raises(AttemptAuthorityLost): control.renew(20, sent_at=100.)
    assert control.snapshot()['revoked'] is True
    with pytest.raises(AttemptAuthorityLost): control.renew(1000, sent_at=120.)


def test_a_renewal_response_after_the_previous_known_expiry_cannot_reactivate():
    clock = Clock()
    control = AttemptControl(identity(), monotonic=clock)
    control.renew(10, sent_at=100.)
    clock.value = 110.
    with pytest.raises(AttemptAuthorityLost): control.renew(100, sent_at=109.)
    assert control.snapshot()['revoked'] is True
    assert control.snapshot()['monotonic_deadline'] == 110.
    with pytest.raises(AttemptAuthorityLost): control.check()


def test_fresh_renewal_can_extend_a_live_lease_but_never_its_fixed_attempt_budget():
    clock = Clock()
    control = AttemptControl(identity(), monotonic=clock, deadline=125.)
    control.renew(10, sent_at=100.)
    clock.value = 109.
    control.renew(1000, sent_at=108.)
    assert control.snapshot()['lease_deadline'] == 1108.
    assert control.snapshot()['monotonic_deadline'] == 125.
    assert control.remaining() == 16.
    clock.value = 125.
    with pytest.raises(AttemptAuthorityLost): control.check()
    assert control.snapshot()['revocation_reason'] == 'attempt_deadline_expired'


def test_already_elapsed_attempt_budget_prevents_any_first_renewal():
    control = AttemptControl(identity(), monotonic=Clock(), deadline=99.)
    with pytest.raises(AttemptAuthorityLost): control.renew(1000, sent_at=100.)
    assert control.snapshot()['revocation_reason'] == 'attempt_deadline_expired'


@pytest.mark.parametrize('value', [0, -1, True, None, '20', float('nan'), float('inf')])
def test_renewal_requires_positive_finite_lease_seconds(value):
    control = AttemptControl(identity(), monotonic=Clock())
    with pytest.raises(ValueError): control.renew(value, sent_at=100.)
    assert control.snapshot()['authorized'] is False


@pytest.mark.parametrize('sent_at', [101., True, None, '100', float('nan'), float('inf')])
def test_send_time_must_be_a_finite_observed_monotonic_time_not_in_the_future(sent_at):
    control = AttemptControl(identity(), monotonic=Clock())
    with pytest.raises(ValueError): control.renew(10, sent_at=sent_at)
    assert control.snapshot()['authorized'] is False


def test_cancellation_is_permanent_and_preserves_the_first_revocation_reason():
    control = AttemptControl(identity(), monotonic=Clock())
    control.renew(20, sent_at=100.)
    assert control.revoke('server_cancelled') is None
    control.revoke('cleanup')
    assert control.snapshot()['revocation_reason'] == 'server_cancelled'
    with pytest.raises(AttemptAuthorityLost): control.renew(1000, sent_at=100.)
    with pytest.raises(AttemptAuthorityLost): control.remaining()


def test_delayed_heartbeat_thread_cannot_win_against_a_parent_revocation():
    control = AttemptControl(identity(), monotonic=Clock())
    control.renew(20, sent_at=100.)
    response_ready, release_response = threading.Event(), threading.Event()
    observed = []
    def response():
        response_ready.set()
        assert release_response.wait(2)
        try: control.renew(1000, sent_at=100.)
        except AttemptAuthorityLost: observed.append('denied')
    thread = threading.Thread(target=response)
    thread.start()
    try:
        assert response_ready.wait(2)
        control.revoke('parent_closed')
        release_response.set()
        thread.join(2)
        assert not thread.is_alive() and observed == ['denied']
        assert control.snapshot()['revocation_reason'] == 'parent_closed'
    finally:
        release_response.set()
        thread.join(2)


def test_an_old_attempt_revocation_does_not_mutate_a_new_control():
    old = AttemptControl(identity(), monotonic=Clock())
    new = AttemptControl(identity(attempt_id='attempt-two'), monotonic=Clock())
    old.renew(20, sent_at=100.)
    new.renew(20, sent_at=100.)
    old.revoke('old_parent_finished')
    assert new.check() is None and new.snapshot()['revoked'] is False


@pytest.mark.parametrize('value', [99., True, float('nan'), float('inf')])
def test_clock_regression_or_invalid_clock_revokes_a_previously_live_control(value):
    clock = Clock()
    control = AttemptControl(identity(), monotonic=clock)
    control.renew(20, sent_at=100.)
    clock.value = value
    with pytest.raises(AttemptAuthorityLost): control.check()
    clock.value = 101.
    assert control.snapshot()['revoked'] is True
    with pytest.raises(AttemptAuthorityLost): control.renew(20, sent_at=100.)
