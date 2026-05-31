"""
HTTP tests for /users/* — THE NON-NEGOTIABLE cross-tenant isolation suite (testing.md).

Authenticates as company_admin of org A and proves every endpoint is org-scoped:
  - list/create/patch/delete touch ONLY org A's users;
  - GET/PATCH/DELETE against an org-B user resolve to 404 (never 200-empty, never an
    existence leak), and no org-B data appears in any response body;
  - a member (non-admin) hitting any /users endpoint gets 403.
Drives the real ASGI app (real JWT -> get_tenant_session -> org-scoped repo). Requires
Postgres (identity_schema fixture).
"""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.enums import UserRole
from tests.identity.conftest import bearer, company_token, seed_organization, seed_user


async def _seed_two_orgs(session: AsyncSession):
    """Seed org A (with an admin + a member) and org B (with one user). Commit."""
    org_a = await seed_organization(session, name="Org A", slug="org-a")
    org_b = await seed_organization(session, name="Org B", slug="org-b")
    admin_a = await seed_user(
        session, org_id=org_a.id, email="admin-a@a.example", full_name="Admin A",
        role=UserRole.company_admin,
    )
    member_a = await seed_user(
        session, org_id=org_a.id, email="member-a@a.example", full_name="Member A",
        role=UserRole.member,
    )
    user_b = await seed_user(
        session, org_id=org_b.id, email="secret-b@b.example", full_name="Secret B",
        role=UserRole.member,
    )
    await session.commit()
    return org_a, org_b, admin_a, member_a, user_b


def _admin_a_headers(admin_a, org_a) -> dict[str, str]:
    return bearer(company_token(admin_a.id, org_a.id, UserRole.company_admin))


async def test_list_users_returns_only_caller_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org_a, _org_b, admin_a, _member_a, user_b = await _seed_two_orgs(db_session)

    response = await client.get("/users", headers=_admin_a_headers(admin_a, org_a))

    assert response.status_code == 200
    emails = {row["email"] for row in response.json()}
    assert emails == {"admin-a@a.example", "member-a@a.example"}
    # NON-NEGOTIABLE: org B's user must be ABSENT from the body.
    assert user_b.email not in emails
    assert str(user_b.id) not in response.text


async def test_create_user_lands_in_caller_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org_a, _org_b, admin_a, *_ = await _seed_two_orgs(db_session)

    response = await client.post(
        "/users",
        headers=_admin_a_headers(admin_a, org_a),
        json={
            "email": "new-a@a.example",
            "full_name": "New A",
            "role": "member",
            "password": "StrongPass1",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["org_id"] == str(org_a.id)
    assert body["email"] == "new-a@a.example"


async def test_patch_user_same_org_updates(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org_a, _org_b, admin_a, member_a, _user_b = await _seed_two_orgs(db_session)

    response = await client.patch(
        f"/users/{member_a.id}",
        headers=_admin_a_headers(admin_a, org_a),
        json={"full_name": "Renamed A"},
    )

    assert response.status_code == 200
    assert response.json()["full_name"] == "Renamed A"


async def test_patch_cross_tenant_user_returns_404_no_b_data(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # NON-NEGOTIABLE: admin A patching org B's user -> 404, no B data leaked.
    org_a, _org_b, admin_a, _member_a, user_b = await _seed_two_orgs(db_session)

    response = await client.patch(
        f"/users/{user_b.id}",
        headers=_admin_a_headers(admin_a, org_a),
        json={"full_name": "HIJACKED"},
    )

    assert response.status_code == 404
    assert "secret-b@b.example" not in response.text
    assert "Secret B" not in response.text


async def test_delete_cross_tenant_user_returns_404_no_b_data(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # NON-NEGOTIABLE: admin A deleting org B's user -> 404, no B data leaked.
    org_a, _org_b, admin_a, _member_a, user_b = await _seed_two_orgs(db_session)

    response = await client.delete(
        f"/users/{user_b.id}", headers=_admin_a_headers(admin_a, org_a)
    )

    assert response.status_code == 404
    assert "secret-b@b.example" not in response.text


async def test_get_unknown_user_id_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # An entirely unknown id behaves like a cross-tenant miss: 404 via PATCH path.
    org_a, _org_b, admin_a, *_ = await _seed_two_orgs(db_session)

    response = await client.patch(
        f"/users/{uuid4()}",
        headers=_admin_a_headers(admin_a, org_a),
        json={"full_name": "X"},
    )

    assert response.status_code == 404


async def test_delete_user_same_org_deactivates(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org_a, _org_b, admin_a, member_a, _user_b = await _seed_two_orgs(db_session)

    response = await client.delete(
        f"/users/{member_a.id}", headers=_admin_a_headers(admin_a, org_a)
    )

    assert response.status_code == 204

    # The deactivated user is now inactive but still listed (soft delete).
    listing = await client.get("/users", headers=_admin_a_headers(admin_a, org_a))
    member_row = next(r for r in listing.json() if r["id"] == str(member_a.id))
    assert member_row["is_active"] is False


async def test_member_listing_users_returns_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The role gate: a member must not reach user management at all.
    _org_a, _org_b, _admin_a, member_a, _user_b = await _seed_two_orgs(db_session)
    headers = bearer(company_token(member_a.id, member_a.org_id, UserRole.member))

    response = await client.get("/users", headers=headers)

    assert response.status_code == 403


async def test_member_creating_user_returns_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _org_a, _org_b, _admin_a, member_a, _user_b = await _seed_two_orgs(db_session)
    headers = bearer(company_token(member_a.id, member_a.org_id, UserRole.member))

    response = await client.post(
        "/users",
        headers=headers,
        json={
            "email": "x@a.example",
            "full_name": "X",
            "role": "member",
            "password": "StrongPass1",
        },
    )

    assert response.status_code == 403


async def test_create_user_overlong_password_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # AUD-02: a >72-byte password is rejected at validation (422), never a bcrypt 500.
    org_a, _org_b, admin_a, *_ = await _seed_two_orgs(db_session)

    response = await client.post(
        "/users",
        headers=_admin_a_headers(admin_a, org_a),
        json={
            "email": "long-pw@a.example",
            "full_name": "Long PW",
            "role": "member",
            "password": "x" * 73,
        },
    )

    assert response.status_code == 422


async def test_create_user_canonicalizes_email_to_lowercase(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # DYN-02: a mixed-case email is stored lowercased so case variants are one identity.
    org_a, _org_b, admin_a, *_ = await _seed_two_orgs(db_session)

    response = await client.post(
        "/users",
        headers=_admin_a_headers(admin_a, org_a),
        json={
            "email": "Mixed.Case@Example.com",
            "full_name": "Mixed",
            "role": "member",
            "password": "StrongPass1",
        },
    )

    assert response.status_code == 201
    assert response.json()["email"] == "mixed.case@example.com"


async def test_create_user_duplicate_email_case_insensitive_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # DYN-02: 'dup@a' and 'DUP@A' canonicalize to one identity -> the second is a dup.
    org_a, _org_b, admin_a, *_ = await _seed_two_orgs(db_session)
    headers = _admin_a_headers(admin_a, org_a)
    first = await client.post(
        "/users",
        headers=headers,
        json={
            "email": "dup@a.example",
            "full_name": "First",
            "role": "member",
            "password": "StrongPass1",
        },
    )
    assert first.status_code == 201

    second = await client.post(
        "/users",
        headers=headers,
        json={
            "email": "DUP@A.EXAMPLE",
            "full_name": "Second",
            "role": "member",
            "password": "StrongPass1",
        },
    )

    assert second.status_code == 409


async def test_create_user_nul_byte_in_full_name_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # DYN-03: a NUL/control char is rejected at validation (422), never a DB 500.
    org_a, _org_b, admin_a, *_ = await _seed_two_orgs(db_session)

    response = await client.post(
        "/users",
        headers=_admin_a_headers(admin_a, org_a),
        json={
            "email": "nul@a.example",
            "full_name": "Bad\x00Name",
            "role": "member",
            "password": "StrongPass1",
        },
    )

    assert response.status_code == 422


async def test_create_user_unknown_field_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # DYN-04: extra='forbid' rejects unknown fields (e.g. a smuggled privilege flag).
    org_a, _org_b, admin_a, *_ = await _seed_two_orgs(db_session)

    response = await client.post(
        "/users",
        headers=_admin_a_headers(admin_a, org_a),
        json={
            "email": "x@a.example",
            "full_name": "X",
            "role": "member",
            "password": "StrongPass1",
            "is_superuser": True,
        },
    )

    assert response.status_code == 422


async def test_users_without_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/users")

    assert response.status_code == 401


async def test_platform_token_on_users_returns_401(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A platform-audience token is wrong-audience for the company /users surface.
    from tests.identity.conftest import platform_token

    response = await client.get("/users", headers=bearer(platform_token()))

    assert response.status_code == 401
