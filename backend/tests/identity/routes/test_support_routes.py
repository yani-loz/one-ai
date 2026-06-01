"""
HTTP tests for break-glass support access (PC-05) — the platform request/revoke side and the
company approve/deny/revoke side, end-to-end against the real ASGI app + DB + JWTs.

The guards ARE the feature, so they lead: cross-tenant isolation (a company_admin can only
act on their OWN org's grants → 404), the state machine (illegal transitions → 409), and
CONSENT (no platform path can approve). Plus audience confinement, denormalized attribution,
live expiry, and audit emission. Requires Postgres (identity_schema fixture).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import scoped_session
from app.identity.enums import UserRole
from app.identity.exceptions import InvalidGrantTransitionError
from app.identity.models.support_grant import SupportGrant
from app.identity.principal import Principal
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.repositories.support_grant_repository import SupportGrantRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.services.audit_service import AuditService
from app.identity.services.company_support_service import CompanySupportService
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


async def test_list_my_requests_is_requester_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A platform admin's list returns ONLY their own requests, never another admin's.
    org, _admin, platform_a = await _seed_actors(db_session)
    platform_b = await seed_platform_admin(
        db_session, email="staff-b@ethera.example", full_name="Staff B"
    )
    await db_session.commit()
    grant_a = await _request_grant(client, org.id, platform_a.id)
    await _request_grant(client, org.id, platform_b.id)  # B's request

    mine = await client.get(
        "/platform/support-requests", headers=_platform_headers(platform_a.id)
    )

    assert mine.status_code == 200
    assert [g["id"] for g in mine.json()] == [grant_a["id"]]  # only A's


async def test_cross_tenant_deny_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org_a, _admin_a, platform_admin = await _seed_actors(db_session, slug="acme")
    org_b, admin_b, _platform_b = await _seed_actors(db_session, slug="globex")
    grant = await _request_grant(client, org_a.id, platform_admin.id)

    hijack = await client.post(
        f"/support-access/{grant['id']}/deny",
        headers=_company_headers(admin_b.id, org_b.id),
    )

    assert hijack.status_code == 404
    status = (
        await db_session.execute(
            text("SELECT status FROM support_grant WHERE id = :id"), {"id": grant["id"]}
        )
    ).scalar_one()
    assert status == "requested"  # untouched


async def test_cross_tenant_revoke_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org_a, admin_a, platform_admin = await _seed_actors(db_session, slug="acme")
    org_b, admin_b, _platform_b = await _seed_actors(db_session, slug="globex")
    grant = await _request_grant(client, org_a.id, platform_admin.id)
    # Approve it (as A) so the revocable (requested|approved) set is exercised cross-tenant.
    await client.post(
        f"/support-access/{grant['id']}/approve",
        headers=_company_headers(admin_a.id, org_a.id),
    )

    hijack = await client.post(
        f"/support-access/{grant['id']}/revoke",
        headers=_company_headers(admin_b.id, org_b.id),
    )

    assert hijack.status_code == 404
    status = (
        await db_session.execute(
            text("SELECT status FROM support_grant WHERE id = :id"), {"id": grant["id"]}
        )
    ).scalar_one()
    assert status == "approved"  # B couldn't cut off A's grant


async def test_company_revoke_approved_grant_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The emergency cut-off happy path: approve -> revoke flips to revoked + logs it.
    org, admin_user, platform_admin = await _seed_actors(db_session)
    grant = await _request_grant(client, org.id, platform_admin.id)
    headers = _company_headers(admin_user.id, org.id)
    await client.post(f"/support-access/{grant['id']}/approve", headers=headers)

    revoked = await client.post(f"/support-access/{grant['id']}/revoke", headers=headers)

    assert revoked.status_code == 200
    body = revoked.json()
    assert body["status"] == "revoked"
    assert body["is_active"] is False  # access cut off
    audit = await client.get(
        "/platform/audit",
        params={"action": "support.revoked"},
        headers=_platform_headers(platform_admin.id),
    )
    assert len(audit.json()) == 1


async def test_platform_revoke_own_request_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, _admin, platform_admin = await _seed_actors(db_session)
    grant = await _request_grant(client, org.id, platform_admin.id)

    revoked = await client.post(
        f"/platform/support-requests/{grant['id']}/revoke",
        headers=_platform_headers(platform_admin.id),
    )

    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"


async def test_request_empty_reason_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The consent justification is mandatory + bounded (min_length=1) — pin the contract.
    org, _admin, platform_admin = await _seed_actors(db_session)

    response = await client.post(
        f"/platform/orgs/{org.id}/support-requests",
        json={"reason": ""},
        headers=_platform_headers(platform_admin.id),
    )

    assert response.status_code == 422


async def test_concurrent_transitions_serialize_via_row_lock(
    db_session: AsyncSession,
) -> None:
    # Two simultaneous approves on the same grant must serialize (SELECT ... FOR UPDATE):
    # exactly one wins, the other re-reads the committed status and is rejected (409). Without
    # the row lock both would read 'requested' and both commit (lost update). Mirrors DYN-01.
    org, admin_user, platform_admin = await _seed_actors(db_session)
    grant = SupportGrant(
        org_id=org.id,
        requested_by_admin_id=platform_admin.id,
        requested_by_email=platform_admin.email,
        reason=_REASON,
        status="requested",
    )
    db_session.add(grant)
    await db_session.commit()
    actor = Principal(
        subject_id=admin_user.id, org_id=org.id, role="company_admin", subject_type="user"
    )

    async def approve() -> str:
        async with scoped_session(org.id) as session:
            service = CompanySupportService(
                support_grants=SupportGrantRepository(session),
                users=UserRepository(session),
                audit=AuditService(AuditRepository(session)),
            )
            try:
                await service.approve(grant.id, org.id, actor)
                await session.commit()
                return "ok"
            except InvalidGrantTransitionError:
                await session.rollback()
                return "rejected"

    outcomes = sorted(await asyncio.gather(approve(), approve()))

    assert outcomes == ["ok", "rejected"]  # exactly one approve stuck
