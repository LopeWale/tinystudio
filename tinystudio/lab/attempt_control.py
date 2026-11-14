"""Parent-owned attempt identity and a conservative monotonic authority latch.

No tokens, network clients, device handles or waits belong to this object. Its
checks compose with the gateway's durable ownership checks; they do not provide
isolation from another process running under the same operating-system user.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
import threading
import time
from urllib.parse import urlsplit

from .contracts import canonical


class AttemptAuthorityLost(RuntimeError):
    """This attempt has no currently usable parent write authority."""


def _finite(value, label):
    try:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(label + ' must be a finite number')
        return float(value)
    except (OverflowError, TypeError) as error:
        raise ValueError(label + ' must be a finite number') from error


@dataclass(frozen=True)
class AttemptIdentity:
    server: str
    run_id: str
    attempt_id: str
    target_id: str
    spec_digest: str

    def __post_init__(self):
        for name, value in self.as_dict().items():
            if type(value) is not str or not value:
                raise ValueError(name + ' must be an exact nonempty string')
            try:
                value.encode('utf-8')
            except UnicodeError as error:
                raise ValueError(name + ' must be an encodable Unicode string') from error
        if not re.fullmatch(r'[0-9a-f]{64}', self.spec_digest):
            raise ValueError('spec_digest must be a lowercase SHA256 digest')
        if any(ord(char) <= 32 or ord(char) == 127 or char == '\\' for char in self.server):
            raise ValueError('server must be a credential-free HTTP(S) origin')
        try:
            origin = urlsplit(self.server)
            _ = origin.port
        except ValueError as error:
            raise ValueError('server must be a valid HTTP(S) origin') from error
        if (origin.scheme not in {'http', 'https'} or not origin.hostname or origin.username is not None
                or origin.password is not None or origin.path not in {'', '/'}
                or '?' in self.server or '#' in self.server):
            raise ValueError('server must be an HTTP(S) origin without credentials, path, query or fragment')

    def as_dict(self):
        return {'server': self.server, 'run_id': self.run_id, 'attempt_id': self.attempt_id,
                'target_id': self.target_id, 'spec_digest': self.spec_digest}

    @property
    def owner(self):
        return hashlib.sha256(canonical(self.as_dict())).hexdigest()


class AttemptControl:
    def __init__(self, identity, *, monotonic=time.monotonic, deadline=None):
        if not isinstance(identity, AttemptIdentity):
            raise ValueError('control requires a frozen AttemptIdentity')
        if not callable(monotonic):
            raise ValueError('monotonic must be a clock callable')
        self._identity = identity
        self._monotonic = monotonic
        self._attempt_deadline = None if deadline is None else _finite(deadline, 'attempt deadline')
        self._lease_deadline = None
        self._revocation_reason = None
        self._termination_trigger = None
        self._last_time = None
        self._lock = threading.Lock()

    @property
    def identity(self):
        return self._identity

    def _revoke_locked(self, reason, termination=None):
        if self._revocation_reason is None:
            self._revocation_reason = reason
            self._termination_trigger = termination

    def _now_locked(self):
        try:
            now = _finite(self._monotonic(), 'monotonic clock')
        except Exception as error:
            self._revoke_locked('monotonic_clock_invalid', 'expiry')
            raise AttemptAuthorityLost('attempt monotonic clock is invalid') from error
        if self._last_time is not None and now < self._last_time:
            self._revoke_locked('monotonic_clock_regressed', 'expiry')
            raise AttemptAuthorityLost('attempt monotonic clock regressed')
        self._last_time = now
        return now

    def _deadline_locked(self):
        if self._lease_deadline is None:
            return self._attempt_deadline
        if self._attempt_deadline is None:
            return self._lease_deadline
        return min(self._lease_deadline, self._attempt_deadline)

    def _observe_locked(self, now):
        attempt_expired = self._attempt_deadline is not None and now >= self._attempt_deadline
        lease_expired = self._lease_deadline is not None and now >= self._lease_deadline
        if attempt_expired and (not lease_expired or self._attempt_deadline <= self._lease_deadline):
            self._revoke_locked('attempt_deadline_expired', 'cancel')
        elif lease_expired:
            self._revoke_locked('lease_expired', 'expiry')

    def _check_locked(self):
        if self._revocation_reason is not None:
            raise AttemptAuthorityLost('attempt authority revoked: ' + self._revocation_reason)
        now = self._now_locked()
        self._observe_locked(now)
        if self._revocation_reason is not None:
            raise AttemptAuthorityLost('attempt authority revoked: ' + self._revocation_reason)
        if self._lease_deadline is None:
            raise AttemptAuthorityLost('attempt authority has not been renewed')
        return now

    def renew(self, lease_seconds, *, sent_at):
        ttl = _finite(lease_seconds, 'lease_seconds')
        sent = _finite(sent_at, 'heartbeat sent_at')
        if ttl <= 0:
            raise ValueError('lease_seconds must be positive')
        candidate = _finite(sent + ttl, 'lease deadline')
        with self._lock:
            if self._revocation_reason is not None:
                raise AttemptAuthorityLost('attempt authority revoked: ' + self._revocation_reason)
            now = self._now_locked()
            self._observe_locked(now)
            if self._revocation_reason is not None:
                raise AttemptAuthorityLost('attempt authority revoked: ' + self._revocation_reason)
            if sent > now:
                raise ValueError('heartbeat sent_at cannot be in the future')
            if candidate <= now:
                self._revoke_locked('lease_expired', 'expiry')
                raise AttemptAuthorityLost('heartbeat response arrived after its conservative lease deadline')
            self._lease_deadline = candidate

    def check(self):
        with self._lock:
            self._check_locked()

    def remaining(self):
        with self._lock:
            now = self._check_locked()
            return max(0., self._deadline_locked() - now)

    def revoke(self, reason, *, termination=None):
        if type(reason) is not str or not reason:
            raise ValueError('revocation requires a nonempty reason')
        if termination is not None and (type(termination) is not str or termination not in {'release','cancel','expiry'}):
            raise ValueError('termination must select release, cancel, expiry or None')
        with self._lock:
            if self._revocation_reason is None:
                try:
                    self._observe_locked(self._now_locked())
                except AttemptAuthorityLost:
                    pass  # The conservative clock-failure expiry is latched.
            self._revoke_locked(reason, termination)

    def snapshot(self):
        with self._lock:
            if self._revocation_reason is None:
                try:
                    self._observe_locked(self._now_locked())
                except AttemptAuthorityLost:
                    pass  # Clock failure has already latched its public reason.
            renewed = self._lease_deadline is not None
            return {'identity': self._identity.as_dict(), 'owner': self._identity.owner,
                    'renewed': renewed, 'authorized': renewed and self._revocation_reason is None,
                    'lease_deadline': self._lease_deadline, 'attempt_deadline': self._attempt_deadline,
                    'monotonic_deadline': self._deadline_locked(), 'revoked': self._revocation_reason is not None,
                    'revocation_reason': self._revocation_reason,
                    'termination_trigger': self._termination_trigger,
                    'termination_reason': self._revocation_reason}
