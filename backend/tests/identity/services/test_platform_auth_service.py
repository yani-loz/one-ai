"""
DB-backed tests for app.identity.services.platform_auth_service — platform login,
atomic org+admin onboarding, and metadata-only org listing. Real repositories +
token helpers on a real session (no internal mocking). Requires Postgres.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.enums import UserRole
from app.identity.exceptions import (
    DuplicateOrganizationError,
    DuplicateUserError,
    InvalidCredentialsError,
    RefreshTokenInvalidError,
)
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.platform_admin_repository import PlatformAdminRepository
from app.identity.repositories.refresh_token_repository import RefreshTokenRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.platform_schemas import OrganizationCreateRequest
from app.identity.services.platform_auth_service import PlatformAuthService
from app.identity.services.token_issuer import TokenIssuer
from app.identity.services.token_rotator import TokenRotator
from tests.identity.conftest import seed_organization, seed_platform_admin, seed_user

_PASSWORD = "Sup3r-Dev-Only-2026!"


def _platform_service(session: AsyncSession) -> PlatformAuthService:
    refresh_tokens = RefreshTokenRepository(session)
    return PlatformAuthService(
        platform_admins=PlatformAdminRepository(session),
        organizations=OrganizationRepository(session),
        users=UserRepository(session),
        token_issuer=TokenIssuer(refresh_tokens),
        token_rotator=TokenRotator(refresh_tokens),
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


async def test_onboard_creates_org_and_first_admin(db_session: AsyncSession) -> None:
    service = _platform_service(db_session)

    result = await service.onboard_organization(_onboard_payload())

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
        await service.onboard_organization(_onboard_payload())

    # Atomic: the admin user must NOT have been created when the slug clashes.
    assert await UserRepository(db_session).email_exists("owner@new.example") is False


async def test_onboard_duplicate_admin_email_raises(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="owner@new.example",
        full_name="Existing", role=UserRole.member,
    )
    service = _platform_service(db_session)

    with pytest.raises(DuplicateUserError):
        await service.onboard_organization(_onboard_payload())


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
        "id", "name", "slug", "status", "user_count", "created_at",
    }
