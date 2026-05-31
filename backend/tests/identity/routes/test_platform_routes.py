"""
HTTP tests for /platform/* — the SEPARATE platform auth domain.

Proves the audience boundary in both directions (a company token is rejected on
/platform/*, and a platform token is rejected on company endpoints — covered in
test_user_routes/test_auth_routes), that onboarding creates an org plus its first
company_admin, and that org listing returns METADATA ONLY (no content/cost/token
fields). Requires Postgres (identity_schema fixture).
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.enums import UserRole
from tests.identity.conftest import (
    bearer,
    company_token,
    platform_token,
    seed_organization,
    seed_platform_admin,
    seed_user,
)

_PASSWORD = "Sup3r-Dev-Only-2026!"


async def test_platform_login_valid_returns_pair(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    await db_session.commit()

    response = await client.post(
        "/platform/login", json={"email": "super@ethera.ai", "password": _PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert "user" not in body  # platform domain has no user view


async def test_platform_login_wrong_password_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    await db_session.commit()

    response = await client.post(
        "/platform/login", json={"email": "super@ethera.ai", "password": "wrong"}
    )

    assert response.status_code == 401


async def test_orgs_with_company_token_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A company-audience token must NOT pass the platform-admin gate.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    admin = await seed_user(
        db_session, org_id=org.id, email="admin@acme.example", full_name="Admin",
        role=UserRole.company_admin,
    )
    await db_session.commit()
    headers = bearer(company_token(admin.id, org.id, UserRole.company_admin))

    response = await client.get("/platform/orgs", headers=headers)

    assert response.status_code in (401, 403)


async def test_orgs_without_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/platform/orgs")

    assert response.status_code in (401, 403)


async def test_onboard_creates_org_and_first_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        "/platform/orgs",
        headers=bearer(platform_token()),
        json={
            "org_name": "Fresh GmbH",
            "org_slug": "fresh-gmbh",
            "admin_email": "owner@fresh.example",
            "admin_full_name": "Owner",
            "admin_password": "StrongPass1",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["organization"]["slug"] == "fresh-gmbh"
    assert body["organization"]["user_count"] == 1
    assert body["admin"]["email"] == "owner@fresh.example"
    assert body["admin"]["role"] == UserRole.company_admin


async def test_onboard_duplicate_slug_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_organization(db_session, name="Taken", slug="fresh-gmbh")
    await db_session.commit()

    response = await client.post(
        "/platform/orgs",
        headers=bearer(platform_token()),
        json={
            "org_name": "Fresh GmbH",
            "org_slug": "fresh-gmbh",
            "admin_email": "owner@fresh.example",
            "admin_full_name": "Owner",
            "admin_password": "StrongPass1",
        },
    )

    assert response.status_code == 409


async def test_onboard_overlong_admin_password_returns_422(client: AsyncClient) -> None:
    # AUD-02: a >72-byte admin_password is rejected with 422, not a 500 from bcrypt.
    response = await client.post(
        "/platform/orgs",
        headers=bearer(platform_token()),
        json={
            "org_name": "Fresh GmbH",
            "org_slug": "fresh-gmbh",
            "admin_email": "owner@fresh.example",
            "admin_full_name": "Owner",
            "admin_password": "x" * 73,
        },
    )

    assert response.status_code == 422


async def test_list_orgs_returns_metadata_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="m@acme.example", full_name="M", role=UserRole.member
    )
    await db_session.commit()

    response = await client.get("/platform/orgs", headers=bearer(platform_token()))

    assert response.status_code == 200
    rows = response.json()
    acme = next(row for row in rows if row["slug"] == "acme")
    assert acme["user_count"] == 1
    # Metadata only: NO tenant content / cost / token-usage fields are exposed.
    assert set(acme.keys()) == {"id", "name", "slug", "status", "user_count", "created_at"}
    forbidden = {"messages", "conversations", "memory", "cost", "tokens", "content"}
    assert forbidden.isdisjoint(acme.keys())
