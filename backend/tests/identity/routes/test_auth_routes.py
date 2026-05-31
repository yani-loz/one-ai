"""
HTTP tests for /auth/* — login, /auth/me, refresh rotation, logout.

Drives the real ASGI app end-to-end (real dependencies, real DB, real JWTs) so the
full route -> service -> repository chain is exercised. Login wrong-password must be a
GENERIC 401, refresh rotation must invalidate the old token, /auth/me must reject
missing/invalid tokens with 401. Requires Postgres (identity_schema fixture).
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.enums import UserRole
from tests.identity.conftest import bearer, company_token, seed_organization, seed_user

_PASSWORD = "Adm1n-Dev-Only-2026!"


async def _seed_admin(session: AsyncSession, email: str = "admin@acme.example"):
    org = await seed_organization(session, name="Acme", slug="acme")
    user = await seed_user(
        session,
        org_id=org.id,
        email=email,
        full_name="Admin",
        role=UserRole.company_admin,
        password=_PASSWORD,
    )
    await session.commit()
    return org, user


async def test_login_valid_returns_pair_and_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_admin(db_session)

    response = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "admin@acme.example"
    assert body["user"]["org_name"] == "Acme"


async def test_login_wrong_password_returns_401_generic(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_admin(db_session)

    response = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": "wrong"}
    )

    assert response.status_code == 401
    detail = response.json()["detail"].lower()
    # Generic message — must not reveal which field was wrong (no enumeration).
    assert "password" not in detail or "email" in detail
    assert "no such user" not in detail


async def test_login_unknown_email_returns_401(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"email": "ghost@acme.example", "password": _PASSWORD}
    )

    assert response.status_code == 401


async def test_login_email_is_case_insensitive(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # DYN-02: a user stored lowercase logs in with a MIXED-case email (normalized) -> 200.
    await _seed_admin(db_session)

    response = await client.post(
        "/auth/login", json={"email": "Admin@Acme.Example", "password": _PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "admin@acme.example"


async def test_me_with_valid_token_returns_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _org, user = await _seed_admin(db_session)
    token = company_token(user.id, user.org_id, UserRole.company_admin)

    response = await client.get("/auth/me", headers=bearer(token))

    assert response.status_code == 200
    assert response.json()["email"] == "admin@acme.example"


async def test_me_without_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/auth/me")

    assert response.status_code == 401


async def test_me_with_invalid_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/auth/me", headers=bearer("garbage.token.value"))

    assert response.status_code == 401


async def test_me_with_expired_token_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # An expired-but-well-signed access token must be rejected with 401 at the route.
    _org, user = await _seed_admin(db_session)
    expired = company_token(user.id, user.org_id, UserRole.company_admin, ttl_minutes=-5)

    response = await client.get("/auth/me", headers=bearer(expired))

    assert response.status_code == 401


async def test_refresh_rotates_old_token_then_reuse_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_admin(db_session)
    login = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )
    old_refresh = login.json()["refresh_token"]

    rotated = await client.post("/auth/refresh", json={"refresh_token": old_refresh})

    assert rotated.status_code == 200
    new_refresh = rotated.json()["refresh_token"]
    assert new_refresh != old_refresh
    assert "user" not in rotated.json()  # refresh excludes the user view

    reuse = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse.status_code == 401


async def test_logout_revokes_refresh_then_refresh_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_admin(db_session)
    login = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )
    refresh_token = login.json()["refresh_token"]

    logout = await client.post("/auth/logout", json={"refresh_token": refresh_token})

    assert logout.status_code == 204

    after = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert after.status_code == 401


async def test_me_with_platform_token_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A platform-audience token must be rejected on the company /auth/me endpoint.
    from tests.identity.conftest import platform_token

    response = await client.get("/auth/me", headers=bearer(platform_token()))

    assert response.status_code == 401
