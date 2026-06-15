"""
Unit tests for app.identity.security.rate_limit — the in-process pre-bcrypt login throttle.

Pure in-memory unit tests with an INJECTED fake clock (no DB, no sleep) so the sliding
window and exponential-backoff lockout are deterministic. These pin the core throttle
contract; the service- and route-level tests (test_auth_service / test_auth_routes) prove
the check runs BEFORE bcrypt.
"""

from __future__ import annotations

import pytest

from app.identity.exceptions import RateLimitedError
from app.identity.security.rate_limit import (
    InProcessRateLimiter,
    RateLimitPolicy,
    account_key,
    ip_key,
)


class _FakeClock:
    """A monotonic-style clock advanced by the test, so lockouts expire deterministically."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _limiter(clock: _FakeClock) -> InProcessRateLimiter:
    # Small, explicit limits so the test reads clearly: IP allows 3/window, account 2/window.
    return InProcessRateLimiter(
        ip_policy=RateLimitPolicy(
            max_attempts=3,
            window_seconds=60.0,
            base_lockout_seconds=30.0,
            max_lockout_seconds=240.0,
        ),
        account_policy=RateLimitPolicy(
            max_attempts=2,
            window_seconds=60.0,
            base_lockout_seconds=30.0,
            max_lockout_seconds=240.0,
        ),
        clock=clock,
    )


def test_check_fresh_key_under_limit_does_not_raise() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)

    limiter.check(ip_key("1.2.3.4"))  # never seen -> allowed


def test_register_failures_up_to_limit_then_check_raises() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    key = account_key("victim@acme.example")

    limiter.register_failure(key)
    limiter.register_failure(key)  # hits account max_attempts=2 -> lockout armed

    with pytest.raises(RateLimitedError):
        limiter.check(key)


def test_check_under_account_limit_still_allows() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    key = account_key("victim@acme.example")

    limiter.register_failure(key)  # 1 failure, account limit is 2

    limiter.check(key)  # still under limit -> no raise


def test_lockout_expires_after_window_advances() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    key = account_key("victim@acme.example")
    limiter.register_failure(key)
    limiter.register_failure(key)

    clock.advance(31.0)  # base lockout is 30s -> now expired

    limiter.check(key)  # lockout lapsed -> allowed again


def test_lockout_still_active_before_expiry_raises() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    key = account_key("victim@acme.example")
    limiter.register_failure(key)
    limiter.register_failure(key)

    clock.advance(10.0)  # still inside the 30s lockout

    with pytest.raises(RateLimitedError):
        limiter.check(key)


def test_lockout_backoff_doubles_on_continued_failures() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    key = account_key("victim@acme.example")
    limiter.register_failure(key)
    limiter.register_failure(key)  # base lockout 30s

    limiter.register_failure(key)  # overflow=1 -> 60s lockout

    clock.advance(31.0)  # past the 30s base, but not the 60s escalation
    with pytest.raises(RateLimitedError):
        limiter.check(key)
    clock.advance(30.0)  # now past 60s total -> cleared
    limiter.check(key)


def test_lockout_caps_at_max_lockout_seconds() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    key = account_key("victim@acme.example")
    # Drive many overflows; lockout would be 30 * 2^n but must cap at 240s.
    for _ in range(12):
        limiter.register_failure(key)

    clock.advance(239.0)
    with pytest.raises(RateLimitedError):
        limiter.check(key)
    clock.advance(2.0)  # past the 240s cap
    limiter.check(key)


def test_ip_and_account_budgets_are_independent() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    ip = ip_key("9.9.9.9")
    # Exhaust the account budget for one email; the IP budget (limit 3) is untouched.
    limiter.register_failure(account_key("a@acme.example"))
    limiter.register_failure(account_key("a@acme.example"))

    limiter.check(ip)  # IP has zero failures -> allowed despite the account lockout


def test_window_slides_so_old_failures_do_not_count() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    key = ip_key("5.5.5.5")  # IP limit 3 within 60s
    limiter.register_failure(key)
    limiter.register_failure(key)

    clock.advance(61.0)  # the two failures age out of the window
    limiter.register_failure(key)  # only 1 in-window failure now

    limiter.check(key)  # under the limit -> allowed


def test_none_ip_collapses_to_shared_bucket_not_bypass() -> None:
    clock = _FakeClock()
    limiter = _limiter(clock)
    key = ip_key(None)  # missing IP -> shared 'unknown' bucket, still throttled
    limiter.register_failure(key)
    limiter.register_failure(key)
    limiter.register_failure(key)  # hits IP limit 3

    with pytest.raises(RateLimitedError):
        limiter.check(key)
