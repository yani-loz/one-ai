"""
Role: Access-token denylist (FIX_BEFORE_PROD — immediate revocation). Access tokens are
      stateless JWTs valid for their full <=15min TTL, so logout, user-deactivation, and
      org-suspension cannot kill an already-issued one. A TokenDenylist Protocol plus an
      in-process implementation, checked in identity.dependencies on EVERY authenticated
      request, closes that window.
Used by: identity.dependencies.get_current_principal / get_current_platform_admin (the
         is_revoked check, after JWT verification); AuthService.logout (revoke_subject),
         UserService.deactivate_user (revoke_subject), PlatformOrgService.set_status →
         suspended (revoke_org). identity.dependencies builds the process-wide singleton.
Depends on: app.core.config (the access-token TTL, for eviction). Leaf otherwise — no DB,
            no network; the store is in process memory.
Key invariants:
  - REVOCATION-BY-CUTOFF, not per-jti tracking: revoking a SUBJECT (or ORG) records a
    cutoff timestamp; a token is revoked iff its `iat` (issued-at) is at or before the
    cutoff. One call kills every outstanding token for that subject/org without enumerating
    jtis — exactly what deactivate / suspend / logout need ("end all sessions"). New tokens
    issued AFTER the cutoff are unaffected, so a reactivated user can log in again.
  - FAIL-CLOSED on a malformed token: is_revoked treats a missing/unparseable `iat` as
    revoked (the dependency already rejects malformed claims, but the denylist never
    fails OPEN).
  - SINGLE-TENANT, IN-PROCESS by design (Bible: in-process suffices at single-tenant
    scale). The Protocol is the seam — a Redis-backed impl is a drop-in for horizontal
    scale. Bounded restart window: cutoffs reset on restart, but a token outlives a
    restart by at most its <=15min TTL, so a restart re-opens at most that window.
  - SELF-EVICTING: a cutoff older than the access-token TTL is dead weight (every token
    issued before it has already expired on its own), so it is evicted — the dicts stay
    bounded by the count of recently-revoked subjects/orgs, not by all-time revocations.
  - NO SECRETS stored: keys are subject/org UUIDs and timestamps only — never a token.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


class TokenDenylist(Protocol):
    """The revocation seam: revoke a subject's/org's tokens, and check on each request.

    A Redis-backed implementation satisfies the same Protocol for horizontal scale; the
    callers (dependencies + the three lifecycle services) depend only on these methods.
    """

    def revoke_subject(self, subject_id: UUID) -> None:
        """Revoke every access token issued to `subject_id` at or before now."""
        ...

    def revoke_org(self, org_id: UUID) -> None:
        """Revoke every access token issued to any user of `org_id` at or before now."""
        ...

    def is_revoked(self, subject_id: UUID, org_id: UUID | None, issued_at: int | None) -> bool:
        """True if the token (by subject/org cutoff vs its `iat`) has been revoked."""
        ...


@dataclass(slots=True)
class InProcessTokenDenylist:
    """In-process revocation-by-cutoff denylist (a process-wide singleton).

    Two dicts map subject/org UUID -> the unix-second cutoff at which it was revoked. A
    token is revoked iff its `iat` <= the subject's OR the org's cutoff. Cutoffs older than
    the access-token TTL are evicted lazily (every token they would block has expired).
    The clock + TTL are injectable for deterministic tests.
    """

    access_ttl_seconds: int
    clock: object = time.time  # callable[[], float]; injectable for tests
    _subject_cutoffs: dict[UUID, int] = field(default_factory=dict)
    _org_cutoffs: dict[UUID, int] = field(default_factory=dict)

    def _now(self) -> int:
        """Current unix time (seconds) via the injected clock — matches the JWT `iat` unit."""
        return int(self._clock_call())

    def _clock_call(self) -> float:
        return float(self.clock())  # type: ignore[operator]  # clock is callable[[], float]

    def reset(self) -> None:
        """Clear ALL revocations (both dicts) — for the singleton between tests."""
        self._subject_cutoffs.clear()
        self._org_cutoffs.clear()

    def _evict_expired(self, cutoffs: dict[UUID, int], now: int) -> None:
        """Drop cutoffs older than the access TTL — the tokens they block have all expired."""
        horizon = now - self.access_ttl_seconds
        for key in [k for k, ts in cutoffs.items() if ts < horizon]:
            del cutoffs[key]

    def revoke_subject(self, subject_id: UUID) -> None:
        """Set this subject's revocation cutoff to now (kills all its current tokens)."""
        now = self._now()
        self._evict_expired(self._subject_cutoffs, now)
        self._subject_cutoffs[subject_id] = now

    def revoke_org(self, org_id: UUID) -> None:
        """Set this org's revocation cutoff to now (kills all its users' current tokens)."""
        now = self._now()
        self._evict_expired(self._org_cutoffs, now)
        self._org_cutoffs[org_id] = now

    def is_revoked(self, subject_id: UUID, org_id: UUID | None, issued_at: int | None) -> bool:
        """True iff the token's `iat` is at/before this subject's or org's revocation cutoff.

        FAIL-CLOSED: a None/missing `issued_at` is treated as revoked. A cutoff EQUAL to the
        iat counts as revoked (the token was live at the instant of revocation).
        """
        if issued_at is None:
            return True
        subject_cutoff = self._subject_cutoffs.get(subject_id)
        if subject_cutoff is not None and issued_at <= subject_cutoff:
            return True
        if org_id is not None:
            org_cutoff = self._org_cutoffs.get(org_id)
            if org_cutoff is not None and issued_at <= org_cutoff:
                return True
        return False
