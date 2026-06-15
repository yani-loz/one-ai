"""
DB-backed tests for app.identity.services.auth_service — company login, refresh
rotation, logout, and the /auth/me view builder. Real repositories + token helpers on
a real session (no internal mocking). Requires Postgres.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.enums import UserRole
from app.identity.exceptions import InvalidCredentialsError, RefreshTokenInvalidError
from app.identity.principal import Principal
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.refresh_token_repository import RefreshTokenRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.security.password import DUMMY_PASSWORD_HASH
from app.identity.security.rate_limit import RateLimiter
from app.identity.security.tokens import PLATFORM_AUDIENCE
from app.identity.services.audit_service import AuditService
from app.identity.services.auth_service import AuthService
from app.identity.services.token_issuer import TokenIssuer
from app.identity.services.token_rotator import TokenRotator
from tests.identity.conftest import (
    make_test_rate_limiter,
    make_test_token_denylist,
    seed_organization,
    seed_platform_admin,
    seed_user,
)

_PASSWORD = "Adm1n-Dev-Only-2026!"


def _auth_service(session: AsyncSession, rate_limiter: RateLimiter | None = None) -> AuthService:
    refresh_tokens = RefreshTokenRepository(session)
    return AuthService(
        users=UserRepository(session),
        organizations=OrganizationRepository(session),
        token_issuer=TokenIssuer(refresh_tokens),
        token_rotator=TokenRotator(refresh_tokens),
        audit=AuditService(AuditRepository(session)),
        rate_limiter=rate_limiter or make_test_rate_limiter(),
        token_denylist=make_test_token_denylist(),
    )


async def test_login_valid_credentials_returns_pair_and_user(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session,
        org_id=org.id,
        email="admin@acme.example",
        full_name="Admin",
        role=UserRole.company_admin,
        password=_PASSWORD,
    )
    service = _auth_service(db_session)

    result = await service.login("admin@acme.example", _PASSWORD)

    assert result.access_token
    assert result.refresh_token
    assert result.token_type == "bearer"
    assert result.user is not None
    assert result.user.email == "admin@acme.example"
    assert result.user.org_name == "Acme"


async def test_login_wrong_password_raises_invalid_credentials(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session,
        org_id=org.id,
        email="admin@acme.example",
        full_name="Admin",
        role=UserRole.company_admin,
        password=_PASSWORD,
    )
    service = _auth_service(db_session)

    with pytest.raises(InvalidCredentialsError):
        await service.login("admin@acme.example", "wrong-password")


async def test_login_unknown_email_raises_invalid_credentials(db_session: AsyncSession) -> None:
    service = _auth_service(db_session)

    with pytest.raises(InvalidCredentialsError):
        await service.login("ghost@acme.example", _PASSWORD)


async def test_login_inactive_user_raises_invalid_credentials(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session,
        org_id=org.id,
        email="off@acme.example",
        full_name="Off",
        role=UserRole.member,
        password=_PASSWORD,
        is_active=False,
    )
    service = _auth_service(db_session)

    with pytest.raises(InvalidCredentialsError):
        await service.login("off@acme.example", _PASSWORD)


async def test_refresh_rotates_and_revokes_old_token(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session,
        org_id=org.id,
        email="admin@acme.example",
        full_name="Admin",
        role=UserRole.company_admin,
        password=_PASSWORD,
    )
    service = _auth_service(db_session)
    first = await service.login("admin@acme.example", _PASSWORD)

    rotated = await service.refresh(first.refresh_token)

    assert rotated.refresh_token != first.refresh_token
    assert rotated.user is None  # refresh returns no user view


async def test_refresh_reusing_old_token_raises_invalid(db_session: AsyncSession) -> None:
    # Rotation is single-use: the consumed token must be rejected on reuse.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session,
        org_id=org.id,
        email="admin@acme.example",
        full_name="Admin",
        role=UserRole.company_admin,
        password=_PASSWORD,
    )
    service = _auth_service(db_session)
    first = await service.login("admin@acme.example", _PASSWORD)
    await service.refresh(first.refresh_token)

    with pytest.raises(RefreshTokenInvalidError):
        await service.refresh(first.refresh_token)


async def test_logout_then_refresh_raises_invalid(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session,
        org_id=org.id,
        email="admin@acme.example",
        full_name="Admin",
        role=UserRole.company_admin,
        password=_PASSWORD,
    )
    service = _auth_service(db_session)
    issued = await service.login("admin@acme.example", _PASSWORD)

    await service.logout(issued.refresh_token)

    with pytest.raises(RefreshTokenInvalidError):
        await service.refresh(issued.refresh_token)


async def test_build_authenticated_user_unknown_id_raises_invalid(
    db_session: AsyncSession,
) -> None:
    from uuid import uuid4

    service = _auth_service(db_session)

    with pytest.raises(InvalidCredentialsError):
        await service.build_authenticated_user_by_id(uuid4())


async def test_login_unknown_email_still_runs_bcrypt_against_dummy_hash(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Constant-time guard: an unknown email must still pay the bcrypt cost (against the
    # dummy hash) so it is indistinguishable from a real login by response time. Spying
    # on verify_password_async also PINS the call site to the off-loop helper — the sync
    # form would block the event loop for every tenant (~300ms of bcrypt CPU).
    import app.identity.services.auth_service as auth_module

    verified_hashes: list[str] = []
    real_verify_async = auth_module.verify_password_async

    async def _spy(raw_password: str, password_hash: str) -> bool:
        verified_hashes.append(password_hash)
        return await real_verify_async(raw_password, password_hash)

    monkeypatch.setattr(auth_module, "verify_password_async", _spy)
    service = _auth_service(db_session)

    with pytest.raises(InvalidCredentialsError):
        await service.login("ghost@acme.example", _PASSWORD)

    assert verified_hashes == [DUMMY_PASSWORD_HASH]


async def test_login_over_account_limit_raises_429_before_bcrypt(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # N-01: once the per-account budget is exhausted, the NEXT login must 429 BEFORE any
    # bcrypt verify runs — the CPU cost is the attacker's, not ours. We monkeypatch
    # verify_password_async to COUNT calls and assert it is not invoked past the limit.
    import app.identity.services.auth_service as auth_module
    from app.identity.exceptions import RateLimitedError

    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session,
        org_id=org.id,
        email="admin@acme.example",
        full_name="Admin",
        role=UserRole.company_admin,
        password=_PASSWORD,
    )
    # account limit = 2 wrong tries, then lockout.
    limiter = make_test_rate_limiter(account_max=2)
    service = _auth_service(db_session, limiter)

    bcrypt_calls = 0
    real_verify = auth_module.verify_password_async

    async def _counting_verify(raw: str, hashed: str) -> bool:
        nonlocal bcrypt_calls
        bcrypt_calls += 1
        return await real_verify(raw, hashed)

    monkeypatch.setattr(auth_module, "verify_password_async", _counting_verify)

    # Two wrong attempts run bcrypt (and register failures), arming the lockout.
    for _ in range(2):
        with pytest.raises(InvalidCredentialsError):
            await service.login("admin@acme.example", "wrong", client_ip="1.2.3.4")
    calls_after_failures = bcrypt_calls

    # The third attempt — even with the CORRECT password — is throttled BEFORE bcrypt.
    with pytest.raises(RateLimitedError):
        await service.login("admin@acme.example", _PASSWORD, client_ip="1.2.3.4")

    assert bcrypt_calls == calls_after_failures  # no bcrypt past the limit


async def test_login_under_limit_succeeds(db_session: AsyncSession) -> None:
    # A legitimate login below the throttle limit still works (the throttle is invisible to
    # a normal user) — and a SUCCESS does not consume the failure budget.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session,
        org_id=org.id,
        email="admin@acme.example",
        full_name="Admin",
        role=UserRole.company_admin,
        password=_PASSWORD,
    )
    service = _auth_service(db_session, make_test_rate_limiter(account_max=2))

    result = await service.login("admin@acme.example", _PASSWORD, client_ip="1.2.3.4")

    assert result.access_token


async def test_login_window_reset_allows_again(
    db_session: AsyncSession,
) -> None:
    # The limiter window resets: after the lockout lapses, a legit login works again. We drive
    # this with an injected fake clock so no real time passes.
    from app.identity.security.rate_limit import InProcessRateLimiter, RateLimitPolicy

    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session,
        org_id=org.id,
        email="admin@acme.example",
        full_name="Admin",
        role=UserRole.company_admin,
        password=_PASSWORD,
    )

    class _Clock:
        now = 0.0

        def __call__(self) -> float:
            return self.now

    clock = _Clock()
    policy = RateLimitPolicy(
        max_attempts=1, window_seconds=60.0, base_lockout_seconds=30.0, max_lockout_seconds=60.0
    )
    limiter = InProcessRateLimiter(ip_policy=policy, account_policy=policy, clock=clock)
    service = _auth_service(db_session, limiter)

    with pytest.raises(InvalidCredentialsError):
        await service.login("admin@acme.example", "wrong", client_ip="1.2.3.4")
    from app.identity.exceptions import RateLimitedError

    with pytest.raises(RateLimitedError):
        await service.login("admin@acme.example", _PASSWORD, client_ip="1.2.3.4")

    clock.now = 31.0  # lockout (30s) lapsed
    result = await service.login("admin@acme.example", _PASSWORD, client_ip="1.2.3.4")

    assert result.access_token


async def test_refresh_rejects_token_from_other_auth_domain(db_session: AsyncSession) -> None:
    # A platform refresh token must NOT be rotatable through the company refresh path
    # (subject_type binding) — defense in depth across the two auth domains.
    admin = await seed_platform_admin(db_session, email="staff@ethera.example", full_name="Staff")
    refresh_tokens = RefreshTokenRepository(db_session)
    _, raw_platform_refresh = await TokenIssuer(refresh_tokens).issue_pair(
        Principal(
            subject_id=admin.id,
            org_id=None,
            role="platform_admin",
            subject_type="platform_admin",
        ),
        PLATFORM_AUDIENCE,
        "platform_admin",
    )
    service = _auth_service(db_session)

    with pytest.raises(RefreshTokenInvalidError):
        await service.refresh(raw_platform_refresh)
