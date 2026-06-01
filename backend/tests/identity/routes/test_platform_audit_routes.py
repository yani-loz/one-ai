"""
HTTP tests for the platform audit endpoints + event emission (PC-04).

Drives the real ASGI app end-to-end (real DB, real JWTs): a company login / org suspend
produces audit rows that the platform read endpoints return newest-first; failed logins
are recorded WITHOUT secrets and without an actor id; a company token is rejected on both
audit endpoints (cross-domain). Reads use a real platform token (the production encoder).
Requires Postgres (identity_schema fixture). Orgs here are freshly seeded + truncated per
test, so the demo/globex orgs are never touched.
"""

from __future__ import annotations

import json
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.enums import UserRole
from app.identity.models.organization import Organization
from tests.identity.conftest import (
    bearer,
    company_token,
    platform_token,
    seed_organization,
    seed_user,
)

_PASSWORD = "Adm1n-Dev-Only-2026!"


async def _seed_company_admin(
    session: AsyncSession, *, status: str = "active"
) -> Organization:
    """Seed an org + its company_admin (committed) and return the org."""
    org = await seed_organization(session, name="Acme", slug="acme", status=status)
    await seed_user(
        session,
        org_id=org.id,
        email="admin@acme.example",
        full_name="Admin",
        role=UserRole.company_admin,
        password=_PASSWORD,
    )
    await session.commit()
    return org


def _platform_headers() -> dict[str, str]:
    """A valid platform read token (the gate verifies audience, not DB existence)."""
    return bearer(platform_token())


async def test_login_success_records_event_with_denormalized_email(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await _seed_company_admin(db_session)

    login = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )
    assert login.status_code == 200

    audit = await client.get(
        f"/platform/orgs/{org.id}/audit", headers=_platform_headers()
    )

    assert audit.status_code == 200
    success = next(e for e in audit.json() if e["action"] == "auth.login.success")
    assert success["actor_type"] == "user"
    assert success["actor_email"] == "admin@acme.example"  # AC7 denormalized
    assert success["org_id"] == str(org.id)


async def test_failed_login_records_failure_without_secrets(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_company_admin(db_session)

    failed = await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": "wrong-secret"}
    )
    assert failed.status_code == 401

    audit = await client.get(
        "/platform/audit",
        params={"action": "auth.login.failure"},
        headers=_platform_headers(),
    )

    assert audit.status_code == 200
    entries = audit.json()
    assert len(entries) >= 1
    entry = entries[0]
    assert entry["actor_id"] is None  # AC2: failed login has no actor id
    assert entry["actor_email"] == "admin@acme.example"
    blob = json.dumps(entries).lower()
    assert _PASSWORD.lower() not in blob  # AC4: no secrets anywhere
    assert "wrong-secret" not in blob
    assert "password" not in json.dumps(entry["details"]).lower()


async def test_suspend_records_event_metadata_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await _seed_company_admin(db_session)
    headers = _platform_headers()

    patch = await client.patch(
        f"/platform/orgs/{org.id}/status",
        json={"status": "suspended"},
        headers=headers,
    )
    assert patch.status_code == 200

    audit = await client.get(f"/platform/orgs/{org.id}/audit", headers=headers)

    entry = next(e for e in audit.json() if e["action"] == "org.suspend")
    assert entry["actor_type"] == "platform_admin"
    assert entry["entity_type"] == "organization"
    assert entry["entity_id"] == str(org.id)
    assert entry["details"]["to_status"] == "suspended"


async def test_org_audit_is_newest_first_and_paginated(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await _seed_company_admin(db_session)
    headers = _platform_headers()
    await client.patch(
        f"/platform/orgs/{org.id}/status", json={"status": "suspended"}, headers=headers
    )
    await client.patch(
        f"/platform/orgs/{org.id}/status", json={"status": "active"}, headers=headers
    )
    await client.patch(
        f"/platform/orgs/{org.id}/legal-hold", json={"legal_hold": True}, headers=headers
    )

    page = await client.get(
        f"/platform/orgs/{org.id}/audit", params={"limit": 2}, headers=headers
    )

    entries = page.json()
    assert len(entries) == 2  # pagination caps the page
    assert entries[0]["action"] == "org.legal_hold.set"  # newest first
    assert entries[1]["action"] == "org.reactivate"


async def test_audit_endpoints_reject_company_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await _seed_company_admin(db_session)
    headers = bearer(company_token(uuid4(), org.id, UserRole.company_admin))

    org_audit = await client.get(f"/platform/orgs/{org.id}/audit", headers=headers)
    global_audit = await client.get("/platform/audit", headers=headers)

    assert org_audit.status_code == 401  # AC6: cross-domain rejected
    assert global_audit.status_code == 401
