"""
Unit tests for InProcessTokenDenylist — revocation-by-cutoff: revoking a subject/org kills
every access token issued at or before that instant, while tokens issued after survive; a
malformed (missing-iat) token fails closed; old cutoffs self-evict. Pure, no DB (the
service-level wiring — logout/deactivate/suspend → revoke — is asserted in the service tests).
"""

from __future__ import annotations

from uuid import uuid4

from app.identity.security.token_denylist import InProcessTokenDenylist

_TTL = 900  # 15 min, matching the access-token TTL


class _FakeClock:
    """A mutable clock returning a settable unix time (seconds)."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_revoke_subject_rejects_token_issued_before_cutoff() -> None:
    clock = _FakeClock(1000.0)
    denylist = InProcessTokenDenylist(access_ttl_seconds=_TTL, clock=clock)
    subject = uuid4()

    denylist.revoke_subject(subject)

    # a token issued at the cutoff instant (1000) or before is revoked
    assert denylist.is_revoked(subject, None, issued_at=1000) is True
    assert denylist.is_revoked(subject, None, issued_at=999) is True


def test_revoke_subject_allows_token_issued_after_cutoff() -> None:
    # A reactivated user logs in afresh: the NEW token (issued after the revocation) is valid.
    clock = _FakeClock(1000.0)
    denylist = InProcessTokenDenylist(access_ttl_seconds=_TTL, clock=clock)
    subject = uuid4()
    denylist.revoke_subject(subject)

    assert denylist.is_revoked(subject, None, issued_at=1001) is False


def test_unrevoked_subject_token_passes() -> None:
    denylist = InProcessTokenDenylist(access_ttl_seconds=_TTL, clock=_FakeClock(1000.0))
    assert denylist.is_revoked(uuid4(), uuid4(), issued_at=1000) is False


def test_revoke_org_rejects_every_member_token() -> None:
    clock = _FakeClock(2000.0)
    denylist = InProcessTokenDenylist(access_ttl_seconds=_TTL, clock=clock)
    org = uuid4()
    user_a, user_b = uuid4(), uuid4()

    denylist.revoke_org(org)

    # org suspension kills tokens regardless of which member holds them
    assert denylist.is_revoked(user_a, org, issued_at=1999) is True
    assert denylist.is_revoked(user_b, org, issued_at=2000) is True
    # a different org is untouched
    assert denylist.is_revoked(user_a, uuid4(), issued_at=1999) is False


def test_missing_iat_fails_closed() -> None:
    # A token whose iat couldn't be read is treated as revoked — never fail OPEN.
    denylist = InProcessTokenDenylist(access_ttl_seconds=_TTL, clock=_FakeClock(1000.0))
    assert denylist.is_revoked(uuid4(), None, issued_at=None) is True


def test_expired_cutoff_is_evicted() -> None:
    # A cutoff older than the TTL blocks only already-expired tokens, so it is dropped — the
    # store stays bounded by recently-revoked subjects, not all-time revocations.
    clock = _FakeClock(1000.0)
    denylist = InProcessTokenDenylist(access_ttl_seconds=_TTL, clock=clock)
    old_subject = uuid4()
    denylist.revoke_subject(old_subject)  # cutoff at 1000

    clock.now = 1000.0 + _TTL + 1  # advance past the TTL horizon
    denylist.revoke_subject(uuid4())  # any write triggers eviction of stale cutoffs

    # the old cutoff is gone; a (hypothetical) token from before it is no longer matched
    assert denylist.is_revoked(old_subject, None, issued_at=1000) is False


def test_reset_clears_all_revocations() -> None:
    denylist = InProcessTokenDenylist(access_ttl_seconds=_TTL, clock=_FakeClock(1000.0))
    subject, org = uuid4(), uuid4()
    denylist.revoke_subject(subject)
    denylist.revoke_org(org)

    denylist.reset()

    assert denylist.is_revoked(subject, org, issued_at=1000) is False
