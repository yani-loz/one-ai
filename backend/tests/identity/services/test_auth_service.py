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
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.refresh_token_repository import RefreshTokenRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.security.password import DUMMY_PASSWORD_HASH
from app.identity.security.tokens import PLATFORM_AUDIENCE
from app.identity.services.auth_service import AuthService
from app.identity.services.token_issuer import TokenIssuer
from app.identity.services.token_rotator import TokenRotator
from tests.identity.conftest import seed_organization, seed_platform_admin, seed_user

_PASSWORD = "Adm1n-Dev-Only-2026!"


def _auth_service(session: AsyncSession) -> AuthService:
    refresh_tokens = RefreshTokenRepository(session)
    return AuthService(
        users=UserRepository(session),
        organizations=OrganizationRepository(session),
        token_issuer=TokenIssuer(refresh_tokens),
        token_rotator=TokenRotator(refresh_tokens),
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
        db_session, org_id=org.id, email="admin@acme.example", full_name="Admin",
        role=UserRole.company_admin, password=_PASSWORD,
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
        db_session, org_id=org.id, email="off@acme.example", full_name="Off",
        role=UserRole.member, password=_PASSWORD, is_active=False,
    )
    service = _auth_service(db_session)

    with pytest.raises(InvalidCredentialsError):
        await service.login("off@acme.example", _PASSWORD)


async def test_refresh_rotates_and_revokes_old_token(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="admin@acme.example", full_name="Admin",
        role=UserRole.company_admin, password=_PASSWORD,
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
        db_session, org_id=org.id, email="admin@acme.example", full_name="Admin",
        role=UserRole.company_admin, password=_PASSWORD,
    )
    service = _auth_service(db_session)
    first = await service.login("admin@acme.example", _PASSWORD)
    await service.refresh(first.refresh_token)

    with pytest.raises(RefreshTokenInvalidError):
        await service.refresh(first.refresh_token)


async def test_logout_then_refresh_raises_invalid(db_session: AsyncSession) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="admin@acme.example", full_name="Admin",
        role=UserRole.company_admin, password=_PASSWORD,
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
    # dummy hash) so it is indistinguishable from a real login by response time.
    import app.identity.services.auth_service as auth_module

    verified_hashes: list[str] = []
    real_verify = auth_module.verify_password

    def _spy(raw_password: str, password_hash: str) -> bool:
        verified_hashes.append(password_hash)
        return real_verify(raw_password, password_hash)

    monkeypatch.setattr(auth_module, "verify_password", _spy)
    service = _auth_service(db_session)

    with pytest.raises(InvalidCredentialsError):
        await service.login("ghost@acme.example", _PASSWORD)

    assert verified_hashes == [DUMMY_PASSWORD_HASH]


async def test_refresh_rejects_token_from_other_auth_domain(db_session: AsyncSession) -> None:
    # A platform refresh token must NOT be rotatable through the company refresh path
    # (subject_type binding) — defense in depth across the two auth domains.
    admin = await seed_platform_admin(
        db_session, email="staff@ethera.example", full_name="Staff"
    )
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
