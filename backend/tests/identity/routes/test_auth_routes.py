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


async def test_login_overlong_password_returns_422(client: AsyncClient) -> None:
    # N-04 (TC-PC-013): an over-72-byte login password is rejected at the schema boundary
    # (422), not run through bcrypt and swallowed into a 401 — parity with user-create.
    response = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": "x" * 73}
    )

    assert response.status_code == 422


async def test_login_suspended_org_returns_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # PC-03a: valid credentials but a suspended org -> 403 (login blocked), not a token pair.
    org = await seed_organization(db_session, name="Acme", slug="acme", status="suspended")
    await seed_user(
        db_session, org_id=org.id, email="admin@acme.example", full_name="Admin",
        role=UserRole.company_admin, password=_PASSWORD,
    )
    await db_session.commit()

    response = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )

    assert response.status_code == 403


async def test_login_suspended_org_wrong_password_stays_401_no_oracle(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Suspension is revealed ONLY to someone with valid credentials: a WRONG password on a
    # suspended org must still be a GENERIC 401, never a 403 (no enumeration oracle).
    org = await seed_organization(db_session, name="Acme", slug="acme", status="suspended")
    await seed_user(
        db_session, org_id=org.id, email="admin@acme.example", full_name="Admin",
        role=UserRole.company_admin, password=_PASSWORD,
    )
    await db_session.commit()

    response = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": "wrong"}
    )

    assert response.status_code == 401


async def test_refresh_blocked_after_org_suspended(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A suspension can't be outlived by a long-lived refresh token: once suspended, refresh
    # is blocked (403) so the session can't be extended.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="admin@acme.example", full_name="Admin",
        role=UserRole.company_admin, password=_PASSWORD,
    )
    await db_session.commit()
    login = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )
    refresh_token = login.json()["refresh_token"]
    org.status = "suspended"
    await db_session.commit()

    response = await client.post("/auth/refresh", json={"refresh_token": refresh_token})

    assert response.status_code == 403


async def test_me_with_valid_token_succeeds_even_when_org_suspended(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The deliberate asymmetry: login + refresh BLOCK a suspended org, but a still-valid
    # access token keeps working at /auth/me until it lapses (short TTL). Pin it so a future
    # "consistency" change can't silently break valid mid-session tokens.
    org = await seed_organization(db_session, name="Acme", slug="acme", status="suspended")
    user = await seed_user(
        db_session, org_id=org.id, email="admin@acme.example", full_name="Admin",
        role=UserRole.company_admin,
    )
    await db_session.commit()
    headers = bearer(company_token(user.id, org.id, UserRole.company_admin))

    response = await client.get("/auth/me", headers=headers)

    assert response.status_code == 200
    assert response.json()["email"] == "admin@acme.example"


async def test_refresh_token_survives_suspension_then_rotates_after_reactivation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # TC-OL-003 (high if it fails): a suspension-failed refresh must NOT burn the token.
    # consume() stages the revoke UPDATE *before* _load_loginable_org raises 403, so
    # correctness depends on get_session rolling that back — the token's revoked_at stays
    # NULL and it rotates cleanly once the org is reactivated (suspend = pause, not reset).
    # This pins the rollback so the AUD-06 reuse-family work can't silently regress it.
    org = await seed_organization(db_session, name="Acme", slug="acme")
    await seed_user(
        db_session, org_id=org.id, email="admin@acme.example", full_name="Admin",
        role=UserRole.company_admin, password=_PASSWORD,
    )
    await db_session.commit()
    login = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )
    refresh_token = login.json()["refresh_token"]

    org.status = "suspended"
    await db_session.commit()
    blocked = await client.post("/auth/refresh", json={"refresh_token": refresh_token})

    org.status = "active"
    await db_session.commit()
    restored = await client.post("/auth/refresh", json={"refresh_token": refresh_token})

    assert blocked.status_code == 403
    assert restored.status_code == 200  # the SAME token survived the 403
    assert restored.json()["refresh_token"] != refresh_token  # and really rotated


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
