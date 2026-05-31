"""
Tests for app.identity.dependencies — the JWT-verification + tenant-session seam.

get_tenant_session moved here from core: it derives org_id ONLY from the verified
Principal and binds the per-transaction Postgres GUC. These tests prove it scopes the
session to the principal's org and rejects a principal with no org scope. The
role/audience gate functions are also unit-tested. DB-backed tests require Postgres.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.identity.dependencies import (
    get_tenant_session,
    require_company_admin,
)
from app.identity.enums import UserRole
from app.identity.exceptions import PermissionDeniedError, TokenInvalidError
from app.identity.principal import Principal


def _user_principal(org_id: UUID, role: str) -> Principal:
    return Principal(subject_id=uuid4(), org_id=org_id, role=role, subject_type="user")


async def test_get_tenant_session_scopes_to_principal_org(identity_schema: None) -> None:
    org_id = UUID("55555555-5555-5555-5555-555555555555")
    principal = _user_principal(org_id, UserRole.company_admin)

    session_gen = get_tenant_session(principal)
    session = await session_gen.__anext__()
    try:
        bound = (
            await session.execute(text("SELECT current_setting('app.current_org_id', true)"))
        ).scalar()
        assert bound == str(org_id)
    finally:
        await session_gen.aclose()


async def test_get_tenant_session_without_org_raises_token_invalid() -> None:
    # A principal with no org scope (e.g. a platform token reaching this seam) must be
    # rejected before any session opens — org_id never comes from a header/body.
    platform_principal = Principal(
        subject_id=uuid4(), org_id=None, role="platform_admin", subject_type="platform_admin"
    )

    session_gen = get_tenant_session(platform_principal)

    with pytest.raises(TokenInvalidError):
        await session_gen.__anext__()


async def test_require_company_admin_allows_company_admin() -> None:
    principal = _user_principal(uuid4(), UserRole.company_admin)

    result = await require_company_admin(principal)

    assert result is principal


async def test_require_company_admin_rejects_member() -> None:
    principal = _user_principal(uuid4(), UserRole.member)

    with pytest.raises(PermissionDeniedError):
        await require_company_admin(principal)
