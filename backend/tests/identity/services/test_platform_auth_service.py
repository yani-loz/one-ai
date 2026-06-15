"""
DB-backed tests for app.identity.services.platform_auth_service — platform login
(incl. the PC-04a audit rows: success/refresh/logout same-tx, failure on the
independent committed session), atomic org+admin onboarding, and metadata-only org
listing. Real repositories + token helpers on a real session (no internal mocking).
Requires Postgres.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.enums import UserRole
from app.identity.exceptions import (
    DuplicateOrganizationError,
    DuplicateUserError,
    InvalidCredentialsError,
    RefreshTokenInvalidError,
)
from app.identity.models.audit_log import AuditLog
from app.identity.principal import Principal
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.platform_admin_repository import PlatformAdminRepository
from app.identity.repositories.refresh_token_repository import RefreshTokenRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.platform_schemas import OrganizationCreateRequest
from app.identity.security.password import DUMMY_PASSWORD_HASH
from app.identity.security.rate_limit import RateLimiter
from app.identity.services.audit_service import AuditService
from app.identity.services.platform_auth_service import PlatformAuthService
from app.identity.services.token_issuer import TokenIssuer
from app.identity.services.token_rotator import TokenRotator
from tests.identity.conftest import (
    make_test_rate_limiter,
    make_test_token_denylist,
    seed_organization,
    seed_platform_admin,
    seed_user,
)

_PASSWORD = "Sup3r-Dev-Only-2026!"

# The platform admin performing onboarding (id is all the audit row needs as actor).
_ACTOR = Principal(
    subject_id=uuid4(), org_id=None, role="platform_admin", subject_type="platform_admin"
)


def _platform_service(
    session: AsyncSession, rate_limiter: RateLimiter | None = None
) -> PlatformAuthService:
    refresh_tokens = RefreshTokenRepository(session)
    return PlatformAuthService(
        platform_admins=PlatformAdminRepository(session),
        organizations=OrganizationRepository(session),
        users=UserRepository(session),
        token_issuer=TokenIssuer(refresh_tokens),
        token_rotator=TokenRotator(refresh_tokens),
        audit=AuditService(AuditRepository(session)),
        rate_limiter=rate_limiter or make_test_rate_limiter(),
        token_denylist=make_test_token_denylist(),
    )


def _onboard_payload(**overrides: str) -> OrganizationCreateRequest:
    base = {
        "org_name": "New GmbH",
        "org_slug": "new-gmbh",
        "admin_email": "owner@new.example",
        "admin_full_name": "Owner",
        "admin_password": "StrongPass1",
    }
    base.update(overrides)
    return OrganizationCreateRequest(**base)


async def test_login_valid_returns_pair(db_session: AsyncSession) -> None:
    await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    service = _platform_service(db_session)

    access_token, refresh_token = await service.login("super@ethera.ai", _PASSWORD)

    assert access_token
    assert refresh_token


async def test_login_wrong_password_raises_invalid(db_session: AsyncSession) -> None:
    await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    service = _platform_service(db_session)

    with pytest.raises(InvalidCredentialsError):
        await service.login("super@ethera.ai", "wrong")


async def test_platform_login_over_limit_raises_429_before_bcrypt(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # N-01 for the highest-value target: the platform login is throttled before bcrypt too.
    import app.identity.services.platform_auth_service as platform_module
    from app.identity.exceptions import RateLimitedError

    await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    service = _platform_service(db_session, make_test_rate_limiter(account_max=2))

    bcrypt_calls = 0
    real_verify = platform_module.verify_password_async

    async def _counting_verify(raw: str, hashed: str) -> bool:
        nonlocal bcrypt_calls
        bcrypt_calls += 1
        return await real_verify(raw, hashed)

    monkeypatch.setattr(platform_module, "verify_password_async", _counting_verify)

    for _ in range(2):
        with pytest.raises(InvalidCredentialsError):
            await service.login("super@ethera.ai", "wrong", client_ip="9.9.9.9")
    calls_after_failures = bcrypt_calls

    with pytest.raises(RateLimitedError):
        await service.login("super@ethera.ai", _PASSWORD, client_ip="9.9.9.9")

    assert bcrypt_calls == calls_after_failures


async def test_refresh_rotates_and_reuse_raises_invalid(db_session: AsyncSession) -> None:
    # Platform rotation mirrors company rotation: single-use, old token rejected.
    await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    service = _platform_service(db_session)
    _access, refresh = await service.login("super@ethera.ai", _PASSWORD)

    new_access, new_refresh = await service.refresh(refresh)

    assert new_access
    assert new_refresh != refresh
    with pytest.raises(RefreshTokenInvalidError):
        await service.refresh(refresh)


async def test_logout_then_refresh_raises_invalid(db_session: AsyncSession) -> None:
    await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    service = _platform_service(db_session)
    _access, refresh = await service.login("super@ethera.ai", _PASSWORD)

    await service.logout(refresh)

    with pytest.raises(RefreshTokenInvalidError):
        await service.refresh(refresh)


async def _audit_rows(session: AsyncSession, action: str) -> list[AuditLog]:
    """Read back the platform-admin audit rows for one action (newest-first)."""
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.action == action, AuditLog.actor_type == "platform_admin")
        .order_by(AuditLog.occurred_at.desc())
    )
    return list(result.scalars().all())


async def test_login_success_emits_platform_admin_audit_row(db_session: AsyncSession) -> None:
    # PC-04a (a): who entered the god-mode console is recorded — same-session with the
    # issued refresh token (visible on this session before commit), actor fully attributed.
    admin = await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    service = _platform_service(db_session)

    await service.login("super@ethera.ai", _PASSWORD)

    rows = await _audit_rows(db_session, "auth.login.success")
    assert len(rows) == 1
    assert rows[0].actor_id == admin.id
    assert rows[0].actor_email == "super@ethera.ai"
    assert rows[0].org_id is None  # platform scope, not an org row


async def test_login_failure_emits_independent_platform_audit_row(
    db_session: AsyncSession,
) -> None:
    # The failure path RAISES (request rolls back), so the row must come from the
    # INDEPENDENT committed session — mirroring the company pattern exactly.
    await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    service = _platform_service(db_session)

    with pytest.raises(InvalidCredentialsError):
        await service.login("super@ethera.ai", "wrong")

    rows = await _audit_rows(db_session, "auth.login.failure")
    assert len(rows) == 1
    assert rows[0].actor_id is None  # failed attempt is never attributed to an id
    assert rows[0].actor_email == "super@ethera.ai"  # the attempted email (internal log)
    assert rows[0].details == {"reason": "invalid_credentials"}


async def test_refresh_emits_platform_admin_audit_row(db_session: AsyncSession) -> None:
    admin = await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    service = _platform_service(db_session)
    _access, refresh = await service.login("super@ethera.ai", _PASSWORD)

    await service.refresh(refresh)

    rows = await _audit_rows(db_session, "auth.refresh")
    assert len(rows) == 1
    assert rows[0].actor_id == admin.id
    assert rows[0].actor_email == "super@ethera.ai"


async def test_logout_emits_unattributed_platform_audit_row(db_session: AsyncSession) -> None:
    # Logout's opaque token is not resolved to a subject (PC-04a item (c) tracks that):
    # the EVENT is still recorded, actor unattributed — mirroring the company pattern.
    await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    service = _platform_service(db_session)
    _access, refresh = await service.login("super@ethera.ai", _PASSWORD)

    await service.logout(refresh)

    rows = await _audit_rows(db_session, "auth.logout")
    assert len(rows) == 1
    assert rows[0].actor_id is None


async def test_login_unknown_email_still_runs_bcrypt_against_dummy_hash(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Constant-time guard (mirrors the company test): an unknown platform email must still
    # pay the bcrypt cost against the dummy hash. Spying on verify_password_async also PINS
    # the call site to the off-loop helper — the sync form would block the event loop.
    import app.identity.services.platform_auth_service as platform_module

    verified_hashes: list[str] = []
    real_verify_async = platform_module.verify_password_async

    async def _spy(raw_password: str, password_hash: str) -> bool:
        verified_hashes.append(password_hash)
        return await real_verify_async(raw_password, password_hash)

    monkeypatch.setattr(platform_module, "verify_password_async", _spy)
    service = _platform_service(db_session)

    with pytest.raises(InvalidCredentialsError):
        await service.login("ghost@ethera.ai", _PASSWORD)

    assert verified_hashes == [DUMMY_PASSWORD_HASH]


async def test_onboard_creates_org_and_first_admin(db_session: AsyncSession) -> None:
    service = _platform_service(db_session)

    result = await service.onboard_organization(_onboard_payload(), _ACTOR)

    assert result.organization.slug == "new-gmbh"
    assert result.organization.user_count == 1
    assert result.admin.email == "owner@new.example"
    assert result.admin.role == UserRole.company_admin
    assert result.admin.org_id == result.organization.id


async def test_onboard_duplicate_slug_raises_and_creates_nothing(
    db_session: AsyncSession,
) -> None:
    await seed_organization(db_session, name="Taken", slug="new-gmbh")
    service = _platform_service(db_session)

    with pytest.raises(DuplicateOrganizationError):
        await service.onboard_organization(_onboard_payload(), _ACTOR)

    # Atomic: the admin user must NOT have been created when the slug clashes.
    assert await UserRepository(db_session).email_exists("owner@new.example") is False


async def test_onboard_duplicate_admin_email_raises(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session,
        org_id=org.id,
        email="owner@new.example",
        full_name="Existing",
        role=UserRole.member,
    )
    service = _platform_service(db_session)

    with pytest.raises(DuplicateUserError):
        await service.onboard_organization(_onboard_payload(), _ACTOR)


async def test_list_organizations_returns_metadata_with_counts(
    db_session: AsyncSession,
) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="m@acme.example", full_name="M", role=UserRole.member
    )
    service = _platform_service(db_session)

    organizations = await service.list_organizations()

    acme = next(item for item in organizations if item.slug == "acme")
    assert acme.name == "Acme"
    assert acme.user_count == 1
    # Metadata only: the response model exposes no content/cost/token fields.
    assert set(acme.model_dump().keys()) == {
        "id",
        "name",
        "slug",
        "status",
        "user_count",
        "created_at",
    }
