"""
HTTP tests for break-glass support access (PC-05) — the platform request/revoke side and the
company approve/deny/revoke side, end-to-end against the real ASGI app + DB + JWTs.

The guards ARE the feature, so they lead: cross-tenant isolation (a company_admin can only
act on their OWN org's grants → 404), the state machine (illegal transitions → 409), and
CONSENT (no platform path can approve). Plus audience confinement, denormalized attribution,
live expiry, and audit emission. Requires Postgres (identity_schema fixture).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text
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

_REASON = "Investigate a failed sync for customer ticket #4821."


async def _seed_actors(
    session: AsyncSession, *, slug: str = "acme"
) -> tuple[object, object, object]:
    """Seed an org + its company_admin + a platform admin (committed); return all three."""
    org = await seed_organization(session, name=slug.title(), slug=slug)
    admin_user = await seed_user(
        session,
        org_id=org.id,
        email=f"admin@{slug}.example",
        full_name="Company Admin",
        role=UserRole.company_admin,
    )
    platform_admin = await seed_platform_admin(
        session, email=f"staff-{slug}@ethera.example", full_name="Staff"
    )
    await session.commit()
    return org, admin_user, platform_admin


def _platform_headers(admin_id) -> dict[str, str]:
    return bearer(platform_token(admin_id))


def _company_headers(user_id, org_id) -> dict[str, str]:
    return bearer(company_token(user_id, org_id, UserRole.company_admin))


async def _request_grant(client: AsyncClient, org_id, admin_id) -> dict:
    response = await client.post(
        f"/platform/orgs/{org_id}/support-requests",
        json={"reason": _REASON},
        headers=_platform_headers(admin_id),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_full_lifecycle_request_then_company_approves(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, admin_user, platform_admin = await _seed_actors(db_session)

    grant = await _request_grant(client, org.id, platform_admin.id)
    assert grant["status"] == "requested"
    assert grant["is_active"] is False
    assert grant["requested_by_email"] == platform_admin.email  # informed consent

    approved = await client.post(
        f"/support-access/{grant['id']}/approve",
        headers=_company_headers(admin_user.id, org.id),
    )

    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "approved"
    assert body["is_active"] is True
    assert body["expires_at"] is not None
    assert body["decided_by_email"] == admin_user.email  # who consented


async def test_request_creates_requested_not_approved_consent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # CONSENT: the platform side can only ever produce a `requested` grant — never active.
    org, _admin, platform_admin = await _seed_actors(db_session)

    grant = await _request_grant(client, org.id, platform_admin.id)

    assert grant["status"] == "requested"
    assert grant["is_active"] is False
    assert grant["expires_at"] is None


async def test_company_inbox_is_org_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A grant for org A must not appear in org B's inbox.
    org_a, _admin_a, platform_admin = await _seed_actors(db_session, slug="acme")
    org_b, admin_b, _platform_b = await _seed_actors(db_session, slug="globex")
    await _request_grant(client, org_a.id, platform_admin.id)

    inbox_b = await client.get(
        "/support-access", headers=_company_headers(admin_b.id, org_b.id)
    )

    assert inbox_b.status_code == 200
    assert inbox_b.json() == []


async def test_cross_tenant_approve_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # THE NON-NEGOTIABLE: org B's admin approving org A's grant must 404 (no existence leak),
    # and the grant must remain `requested`.
    org_a, _admin_a, platform_admin = await _seed_actors(db_session, slug="acme")
    org_b, admin_b, _platform_b = await _seed_actors(db_session, slug="globex")
    grant = await _request_grant(client, org_a.id, platform_admin.id)

    hijack = await client.post(
        f"/support-access/{grant['id']}/approve",
        headers=_company_headers(admin_b.id, org_b.id),
    )

    assert hijack.status_code == 404
    row = (
        await db_session.execute(
            text("SELECT status FROM support_grant WHERE id = :id"), {"id": grant["id"]}
        )
    ).scalar_one()
    assert row == "requested"  # untouched


async def test_approve_twice_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, admin_user, platform_admin = await _seed_actors(db_session)
    grant = await _request_grant(client, org.id, platform_admin.id)
    headers = _company_headers(admin_user.id, org.id)
    first = await client.post(f"/support-access/{grant['id']}/approve", headers=headers)
    assert first.status_code == 200

    second = await client.post(f"/support-access/{grant['id']}/approve", headers=headers)

    assert second.status_code == 409  # not `requested` any more


async def test_deny_then_revoke_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, admin_user, platform_admin = await _seed_actors(db_session)
    grant = await _request_grant(client, org.id, platform_admin.id)
    headers = _company_headers(admin_user.id, org.id)
    denied = await client.post(f"/support-access/{grant['id']}/deny", headers=headers)
    assert denied.status_code == 200

    revoke = await client.post(f"/support-access/{grant['id']}/revoke", headers=headers)

    assert revoke.status_code == 409  # denied is terminal


async def test_platform_cannot_revoke_another_admins_grant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, _admin, platform_admin = await _seed_actors(db_session)
    grant = await _request_grant(client, org.id, platform_admin.id)

    # A DIFFERENT platform admin (random subject) tries to revoke it.
    revoke = await client.post(
        f"/platform/support-requests/{grant['id']}/revoke",
        headers=_platform_headers(uuid4()),
    )

    assert revoke.status_code == 404  # only the requester can revoke their grant


async def test_company_token_rejected_on_platform_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, admin_user, _platform_admin = await _seed_actors(db_session)

    response = await client.post(
        f"/platform/orgs/{org.id}/support-requests",
        json={"reason": _REASON},
        headers=_company_headers(admin_user.id, org.id),
    )

    assert response.status_code == 401  # platform endpoint rejects a company token


async def test_platform_token_rejected_on_company_approve(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, _admin, platform_admin = await _seed_actors(db_session)
    grant = await _request_grant(client, org.id, platform_admin.id)

    response = await client.post(
        f"/support-access/{grant['id']}/approve",
        headers=_platform_headers(platform_admin.id),
    )

    assert response.status_code == 401  # company endpoint rejects a platform token


async def test_expiry_is_computed_live_from_expires_at(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Live expiry: an approved grant whose expires_at is in the past reads is_active=False,
    # even though the stored status is still 'approved' (the access decision reads the clock).
    org, admin_user, platform_admin = await _seed_actors(db_session)
    grant = await _request_grant(client, org.id, platform_admin.id)
    headers = _company_headers(admin_user.id, org.id)
    await client.post(f"/support-access/{grant['id']}/approve", headers=headers)

    await db_session.execute(
        text("UPDATE support_grant SET expires_at = :past WHERE id = :id"),
        {"past": datetime.now(UTC) - timedelta(hours=1), "id": grant["id"]},
    )
    await db_session.commit()

    inbox = await client.get("/support-access", headers=headers)
    entry = next(g for g in inbox.json() if g["id"] == grant["id"])
    assert entry["status"] == "approved"
    assert entry["is_active"] is False  # expired by the clock


async def test_approve_emits_audit_event_with_expires_at(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, admin_user, platform_admin = await _seed_actors(db_session)
    grant = await _request_grant(client, org.id, platform_admin.id)
    await client.post(
        f"/support-access/{grant['id']}/approve",
        headers=_company_headers(admin_user.id, org.id),
    )

    audit = await client.get(
        "/platform/audit",
        params={"action": "support.approved"},
        headers=_platform_headers(platform_admin.id),
    )

    entries = audit.json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["actor_type"] == "user"  # the company_admin consented
    assert entry["org_id"] == str(org.id)
    assert entry["entity_type"] == "support_grant"
    assert "expires_at" in entry["details"]  # "logged → expire"
