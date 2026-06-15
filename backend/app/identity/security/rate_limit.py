"""
Role: Pre-bcrypt login throttle (FIX_BEFORE_PROD N-01). A RateLimiter Protocol plus an
      in-process sliding-window + lockout implementation, checked at the TOP of both
      login services BEFORE any bcrypt work, so a credential-stuffing / bcrypt-CPU-DoS
      attacker pays for being blocked, not the server.
Used by: AuthService.login + PlatformAuthService.login (the check); identity.dependencies
         (constructs the process-wide singleton); identity.error_handlers (maps
         RateLimitedError -> 429). Tests construct InProcessRateLimiter directly with a
         fake clock.
Depends on: app.core.config (the limit knobs), app.identity.exceptions (RateLimitedError).
            Leaf otherwise (no DB, no network — the store is in process memory).
Key invariants:
  - SINGLE-TENANT, IN-PROCESS by design (Bible: "in-process async is sufficient at
    single-tenant scale"). The RateLimiter Protocol is the seam: a Redis-backed impl is a
    drop-in for horizontal scale with NO caller change. Bounded restart window — counters
    reset on process restart, but access tokens are <=15min TTL and the throttle only
    delays brute force, so a restart re-opens at most a short window, never leaks data.
  - The check MUST run BEFORE bcrypt. `check(key)` raises RateLimitedError when a key is
    over its sliding-window limit OR inside a backoff lockout; `register_failure(key)` is
    called ONLY after a verified failed login, and escalates the lockout once the window
    limit is exceeded. A SUCCESSFUL login does not consume the failure budget.
  - The limiter is THREAD/TASK-safe enough for a single event loop: all mutation happens
    in synchronous, non-awaiting methods, so no interleaving occurs between read and write
    within one call (asyncio gives cooperative, not preemptive, scheduling).
  - NO SECRETS as keys: callers key by client IP and by NORMALIZED email — never a
    password. Keys are namespaced ("ip:" / "account:") so the two budgets never collide.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Protocol

from app.identity.exceptions import RateLimitedError

# Key namespaces — keep the per-IP and per-account budgets in separate buckets so an
# attacker hammering one account from many IPs and one IP across many accounts are both
# independently bounded.
IP_KEY_PREFIX = "ip:"
ACCOUNT_KEY_PREFIX = "account:"


def ip_key(client_ip: str | None) -> str:
    """Return the throttle key for a client IP (None -> a shared 'unknown' bucket).

    A missing client IP (e.g. an in-process test transport) collapses to one shared
    bucket rather than bypassing the throttle entirely — fail closed, not open.
    """
    return f"{IP_KEY_PREFIX}{client_ip or 'unknown'}"


def account_key(email: str) -> str:
    """Return the throttle key for a normalized account email."""
    return f"{ACCOUNT_KEY_PREFIX}{email}"


class RateLimiter(Protocol):
    """The throttle seam: check a key before bcrypt, register a failure after one.

    A Redis-backed implementation satisfies this same Protocol for horizontal scale; the
    callers (login services) depend only on these two methods, never on the impl.
    """

    def check(self, key: str) -> None:
        """Raise RateLimitedError if `key` is over its window limit or in lockout."""
        ...

    def register_failure(self, key: str) -> None:
        """Record one failed attempt for `key`, escalating to lockout past the limit."""
        ...


@dataclass(slots=True)
class _KeyState:
    """Per-key sliding-window timestamps + the current lockout expiry (monotonic seconds)."""

    failures: deque[float] = field(default_factory=deque)
    locked_until: float = 0.0


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """A sliding-window-plus-lockout policy for one key namespace.

    Attributes:
        max_attempts: failed attempts allowed inside `window_seconds` before lockout.
        window_seconds: the sliding window width (older failures fall out of the count).
        base_lockout_seconds: the FIRST lockout duration once the window limit is hit.
        max_lockout_seconds: the ceiling for the exponentially-doubling lockout.
    """

    max_attempts: int
    window_seconds: float
    base_lockout_seconds: float
    max_lockout_seconds: float


class InProcessRateLimiter:
    """In-process sliding-window throttle with exponential-backoff lockout.

    A process-wide singleton (see identity.dependencies). All state lives in a dict keyed
    by the namespaced throttle key. `check` is read-mostly (it also evicts a lapsed
    lockout); `register_failure` records an attempt and arms/escalates the lockout once the
    window count exceeds the policy. The clock is injectable for deterministic tests.
    """

    def __init__(
        self,
        ip_policy: RateLimitPolicy,
        account_policy: RateLimitPolicy,
        *,
        clock: object = time.monotonic,
    ) -> None:
        """Bind the per-IP and per-account policies and a monotonic clock.

        `clock` is any zero-arg callable returning seconds (monotonic by default); tests
        pass a fake to advance time deterministically. Annotated as object to keep the
        signature dependency-free; it is invoked as clock().
        """
        self._ip_policy = ip_policy
        self._account_policy = account_policy
        self._clock = clock  # type: ignore[assignment]  # callable[[], float]; see docstring
        self._state: dict[str, _KeyState] = defaultdict(_KeyState)

    def reset(self) -> None:
        """Clear ALL throttle state (every key's failures + lockout).

        For the process-wide singleton between tests so an accumulated window from one test
        can't trip the throttle in the next. NOT used in production request handling.
        """
        self._state.clear()

    def _policy_for(self, key: str) -> RateLimitPolicy:
        """Pick the policy by key namespace (account keys get the stricter budget)."""
        return self._account_policy if key.startswith(ACCOUNT_KEY_PREFIX) else self._ip_policy

    def _now(self) -> float:
        """Return the current monotonic time via the injected clock."""
        return float(self._clock())  # type: ignore[operator]  # clock is callable[[], float]

    def check(self, key: str) -> None:
        """Raise RateLimitedError if `key` is locked out, else return (window not consumed).

        Called BEFORE bcrypt. A still-active lockout raises immediately with the seconds
        remaining (so the attacker, not the server, eats the wait); an expired lockout is
        cleared in passing. The sliding-window count is NOT incremented here — only a
        verified failed login (register_failure) consumes the budget.
        """
        state = self._state.get(key)
        if state is None:
            return
        now = self._now()
        if state.locked_until > now:
            retry_after = int(state.locked_until - now) + 1
            raise RateLimitedError(f"Too many attempts. Try again in {retry_after} seconds.")

    def register_failure(self, key: str) -> None:
        """Record one failed attempt for `key`; arm/escalate lockout past the window limit.

        Trims failures older than the window, appends now, and if the in-window count
        reaches the policy's max, sets (or doubles) the lockout up to the ceiling. Escalation
        is based on how far past the limit the count is, so repeated hammering backs off
        exponentially rather than re-arming the same short lockout.
        """
        policy = self._policy_for(key)
        now = self._now()
        state = self._state[key]
        cutoff = now - policy.window_seconds
        while state.failures and state.failures[0] < cutoff:
            state.failures.popleft()
        state.failures.append(now)

        if len(state.failures) >= policy.max_attempts:
            overflow = len(state.failures) - policy.max_attempts
            lockout = min(
                policy.base_lockout_seconds * (2**overflow),
                policy.max_lockout_seconds,
            )
            state.locked_until = now + lockout
