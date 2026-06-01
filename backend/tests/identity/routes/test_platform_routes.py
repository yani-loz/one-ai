"""
HTTP tests for /platform/* — the SEPARATE platform auth domain.

Proves the audience boundary in both directions (a company token is rejected on
/platform/*, and a platform token is rejected on company endpoints — covered in
test_user_routes/test_auth_routes), that onboarding creates an org plus its first
company_admin, that org listing returns METADATA ONLY (no content/cost/token fields),
and that the platform session endpoints behave (refresh is single-use + rejects a
company refresh token; /me returns the admin identity but rejects a company token;
logout revokes). Requires Postgres (identity_schema fixture).
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


async def test_platform_login_overlong_password_returns_422(client: AsyncClient) -> None:
    # N-04 (TC-PC-013): parity with onboarding — an over-72-byte login password is rejected
    # at the schema boundary (422), not swallowed by verify_password into a 401.
    response = await client.post(
        "/platform/login", json={"email": "super@ethera.ai", "password": "x" * 73}
    )

    assert response.status_code == 422


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


async def _login_platform(client: AsyncClient, db_session: AsyncSession) -> str:
    """Seed a platform admin, log in, and return the issued refresh token."""
    await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    await db_session.commit()
    login = await client.post(
        "/platform/login", json={"email": "super@ethera.ai", "password": _PASSWORD}
    )
    return login.json()["refresh_token"]


async def test_platform_refresh_rotates_and_returns_new_pair(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    refresh = await _login_platform(client, db_session)

    response = await client.post("/platform/refresh", json={"refresh_token": refresh})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"] and body["refresh_token"] != refresh  # rotated
    assert "user" not in body  # platform domain has no user view


async def test_platform_refresh_reuse_of_rotated_token_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Single-use rotation: presenting the same token twice fails the second time.
    refresh = await _login_platform(client, db_session)
    await client.post("/platform/refresh", json={"refresh_token": refresh})

    reuse = await client.post("/platform/refresh", json={"refresh_token": refresh})

    assert reuse.status_code == 401


async def test_platform_refresh_rejects_company_refresh_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Cross-domain: a company refresh token must NOT rotate in the platform domain.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="admin@acme.example", full_name="Admin",
        role=UserRole.company_admin, password=_PASSWORD,
    )
    await db_session.commit()
    company_login = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )
    company_refresh = company_login.json()["refresh_token"]

    response = await client.post("/platform/refresh", json={"refresh_token": company_refresh})

    assert response.status_code == 401
    # Discriminating: the subject_type guard rejects BEFORE revoking, so the company token
    # is untouched and still rotates at /auth/refresh. If that guard were removed, consume
    # would have revoked it here and this follow-up would 401 — so this assertion is what
    # actually proves the domain boundary (not the generic 401 above).
    still_valid = await client.post("/auth/refresh", json={"refresh_token": company_refresh})
    assert still_valid.status_code == 200


async def test_platform_logout_revokes_refresh_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    refresh = await _login_platform(client, db_session)

    logout = await client.post("/platform/logout", json={"refresh_token": refresh})
    reuse = await client.post("/platform/refresh", json={"refresh_token": refresh})

    assert logout.status_code == 204
    assert reuse.status_code == 401  # revoked token cannot rotate


async def test_platform_me_returns_admin_identity(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super", password=_PASSWORD
    )
    await db_session.commit()

    response = await client.get("/platform/me", headers=bearer(platform_token(admin.id)))

    assert response.status_code == 200
    assert response.json() == {
        "id": str(admin.id),
        "email": "super@ethera.ai",
        "full_name": "Super",
    }


async def test_platform_me_with_company_token_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Discriminating: the company token carries a REAL platform admin's id, so the ONLY
    # thing preventing a 200 is the aud='platform' audience guard — strip it and
    # build_admin_view_by_id would resolve this very admin. The org supplies the token's
    # org_id claim; the admin id is what the (mutated) handler would look up.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    admin = await seed_platform_admin(
        db_session, email="super@ethera.ai", full_name="Super"
    )
    await db_session.commit()
    headers = bearer(company_token(admin.id, org.id, UserRole.company_admin))

    response = await client.get("/platform/me", headers=headers)

    assert response.status_code in (401, 403)


async def test_platform_me_without_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/platform/me")

    assert response.status_code in (401, 403)


async def test_platform_me_unknown_admin_returns_401(client: AsyncClient) -> None:
    # A validly-signed platform token whose admin id has no row (deactivated/deleted).
    response = await client.get("/platform/me", headers=bearer(platform_token()))

    assert response.status_code == 401
