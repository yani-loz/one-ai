"""
HTTP tests for GDPR erasure + compliance export (PC-06), end-to-end against the real ASGI
app + DB + JWTs.

The compliance substance leads: LEGAL-HOLD-BEATS-ERASURE (409, nothing deleted), then a full
erasure deletes users + tokens, scrubs the support-grant decider email, RETAINS the append-only
audit_log (honest certificate), offboards the org. Plus the slug-confirmation guard, the
deactivated-admin sudo re-auth rejection (403), audience confinement, and the export shape.
The cross-tenant containment test across the Connect + entity-graph erasure hooks lives in
test_erasure_cross_tenant.py (file-size split). Orgs here are freshly seeded + truncated per
test, so the demo/globex orgs are never erased. Requires Postgres (identity_schema fixture).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.enums import UserRole
from app.identity.models.support_grant import SupportGrant
from tests.identity.conftest import (
    bearer,
    company_token,
    platform_token,
    seed_organization,
    seed_platform_admin,
    seed_user,
)

_PASSWORD = "Adm1n-Dev-Only-2026!"
# The seeded platform admin's own password (seed_platform_admin default) — re-entered to erase.
_ADMIN_PASSWORD = "Test-Pass-123"
_REASON = "Customer offboarding per contract termination."


async def _seed(session: AsyncSession, *, slug: str = "acme", legal_hold: bool = False):
    """Seed an org (optionally under legal hold) + its company_admin + a platform admin."""
    org = await seed_organization(session, name=slug.title(), slug=slug)
    if legal_hold:
        org.legal_hold = True
    user = await seed_user(
        session,
        org_id=org.id,
        email=f"admin@{slug}.example",
        full_name="Company Admin",
        role=UserRole.company_admin,
        password=_PASSWORD,
    )
    platform_admin = await seed_platform_admin(
        session, email=f"staff-{slug}@ethera.example", full_name="Staff"
    )
    await session.commit()
    return org, user, platform_admin


def _platform_headers(admin_id) -> dict[str, str]:
    return bearer(platform_token(admin_id))


def _erase_body(slug: str) -> dict[str, str]:
    return {"reason": _REASON, "confirm_slug": slug, "password": _ADMIN_PASSWORD}


async def _count(session: AsyncSession, sql: str, org_id) -> int:
    result = await session.execute(text(sql), {"o": str(org_id)})
    return result.scalar_one()


async def _token_count(session: AsyncSession, user_id) -> int:
    """Count refresh tokens still held by a user (subject_id keys on the user's id)."""
    result = await session.execute(
        text("SELECT count(*) FROM refresh_tokens WHERE subject_id=:u"), {"u": str(user_id)}
    )
    return result.scalar_one()


async def test_legal_hold_blocks_erasure_and_deletes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # THE HEADLINE: legal hold beats erasure — 409 and NOTHING is touched across every store.
    org, user, platform_admin = await _seed(db_session, legal_hold=True)
    await client.post(  # a refresh token to prove it survives
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )
    # 0014 ck_support_grant_lifecycle: 'approved' requires decided_at + decided_by + expires_at
    # (the shape the real _stamp_decision write path produces).
    grant = SupportGrant(
        org_id=org.id,
        requested_by_admin_id=platform_admin.id,
        requested_by_email="staff-acme@ethera.example",
        reason="prior visit",
        status="approved",
        decided_at=datetime.now(UTC),
        decided_by_user_id=user.id,
        decided_by_email="admin@acme.example",
        expires_at=datetime.now(UTC) + timedelta(hours=8),
    )
    db_session.add(grant)
    await db_session.commit()
    headers = _platform_headers(platform_admin.id)

    response = await client.post(
        f"/platform/orgs/{org.id}/erase", json=_erase_body("acme"), headers=headers
    )

    assert response.status_code == 409
    # Touch NOTHING: users + tokens + decider-email intact, status NOT offboarded, no audit row.
    assert await _count(db_session, "SELECT count(*) FROM users WHERE org_id=:o", org.id) == 1
    assert await _token_count(db_session, user.id) >= 1
    await db_session.refresh(grant)
    assert grant.decided_by_email == "admin@acme.example"
    status = (
        await db_session.execute(
            text("SELECT status FROM organizations WHERE id=:o"), {"o": str(org.id)}
        )
    ).scalar_one()
    assert status == "active"
    audit = await client.get(f"/platform/orgs/{org.id}/audit", headers=headers)
    assert all(entry["action"] != "org.erased" for entry in audit.json())


async def test_erase_deletes_users_revokes_tokens_offboards_and_certifies(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, _user, platform_admin = await _seed(db_session)
    # A login creates a refresh token to erase.
    await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )

    response = await client.post(
        f"/platform/orgs/{org.id}/erase",
        json=_erase_body("acme"),
        headers=_platform_headers(platform_admin.id),
    )

    assert response.status_code == 200
    cert = response.json()
    assert cert["status"] == "offboarded"
    assert cert["users_erased"] == 1
    assert cert["tokens_deleted"] >= 1
    assert cert["audit_log_retained"] is True
    assert "17(3)" in cert["retained_legal_basis"]
    # Verify against the STORES, not just the self-reported certificate.
    assert await _count(db_session, "SELECT count(*) FROM users WHERE org_id=:o", org.id) == 0
    assert await _token_count(db_session, _user.id) == 0
    status = (
        await db_session.execute(
            text("SELECT status FROM organizations WHERE id=:o"), {"o": str(org.id)}
        )
    ).scalar_one()
    assert status == "offboarded"


async def test_erase_scrubs_support_grant_decider_email(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The approving company_admin's email (a tenant subject) is scrubbed from support_grant.
    org, user, platform_admin = await _seed(db_session)
    # 0014 ck_support_grant_lifecycle: 'approved' requires decided_at + decided_by + expires_at.
    grant = SupportGrant(
        org_id=org.id,
        requested_by_admin_id=platform_admin.id,
        requested_by_email="staff-acme@ethera.example",  # Ethera staff — must be KEPT
        reason="prior support visit",
        status="approved",
        decided_at=datetime.now(UTC),
        decided_by_user_id=user.id,
        decided_by_email="admin@acme.example",  # tenant subject — must be SCRUBBED
        expires_at=datetime.now(UTC) + timedelta(hours=8),
    )
    db_session.add(grant)
    await db_session.commit()

    await client.post(
        f"/platform/orgs/{org.id}/erase",
        json=_erase_body("acme"),
        headers=_platform_headers(platform_admin.id),
    )

    row = await db_session.execute(
        text("SELECT decided_by_email, requested_by_email FROM support_grant WHERE id=:i"),
        {"i": str(grant.id)},
    )
    decided, requested = row.one()
    assert decided is None  # tenant subject PII scrubbed
    assert requested == "staff-acme@ethera.example"  # Ethera staff retained


async def test_erase_retains_audit_log_and_records_the_erasure(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, _user, platform_admin = await _seed(db_session)
    # Generate an auth event so there is a prior audit row to retain.
    await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )
    headers = _platform_headers(platform_admin.id)
    await client.post(f"/platform/orgs/{org.id}/erase", json=_erase_body("acme"), headers=headers)

    audit = await client.get(f"/platform/orgs/{org.id}/audit", headers=headers)

    actions = [entry["action"] for entry in audit.json()]
    assert "org.erased" in actions  # the erasure itself is logged
    assert "auth.login.success" in actions  # the prior trail is RETAINED, not deleted


async def test_slug_confirmation_mismatch_returns_400_and_deletes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, _user, platform_admin = await _seed(db_session)

    response = await client.post(
        f"/platform/orgs/{org.id}/erase",
        json={"reason": _REASON, "confirm_slug": "wrong-slug", "password": _ADMIN_PASSWORD},
        headers=_platform_headers(platform_admin.id),
    )

    assert response.status_code == 400
    assert await _count(db_session, "SELECT count(*) FROM users WHERE org_id=:o", org.id) == 1


async def test_erase_wrong_password_returns_403_and_deletes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Sudo-style re-auth: a correct slug but the WRONG admin password -> 403, nothing deleted
    # (and 403, not 401, so it never tears down the admin's valid session).
    org, _user, platform_admin = await _seed(db_session)

    response = await client.post(
        f"/platform/orgs/{org.id}/erase",
        json={"reason": _REASON, "confirm_slug": "acme", "password": "wrong-password"},
        headers=_platform_headers(platform_admin.id),
    )

    assert response.status_code == 403
    assert await _count(db_session, "SELECT count(*) FROM users WHERE org_id=:o", org.id) == 1


async def test_erase_rejects_company_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, user, _platform_admin = await _seed(db_session)

    response = await client.post(
        f"/platform/orgs/{org.id}/erase",
        json=_erase_body("acme"),
        headers=bearer(company_token(user.id, org.id, UserRole.company_admin)),
    )

    assert response.status_code == 401  # erasure is platform-only


async def test_compliance_export_returns_metadata_and_trail(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, _user, platform_admin = await _seed(db_session)
    await client.post(
        "/auth/login", json={"email": "admin@acme.example", "password": _PASSWORD}
    )

    export = await client.get(
        f"/platform/orgs/{org.id}/compliance-export",
        headers=_platform_headers(platform_admin.id),
    )

    assert export.status_code == 200
    body = export.json()
    assert body["organization"]["slug"] == "acme"
    assert any(entry["action"] == "auth.login.success" for entry in body["audit"])
    assert "generated_at" in body


async def test_compliance_export_rejects_company_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, user, _platform_admin = await _seed(db_session)

    export = await client.get(
        f"/platform/orgs/{org.id}/compliance-export",
        headers=bearer(company_token(user.id, org.id, UserRole.company_admin)),
    )

    assert export.status_code == 401


async def test_erase_unknown_org_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _org, _user, platform_admin = await _seed(db_session)

    response = await client.post(
        f"/platform/orgs/{uuid4()}/erase",
        json=_erase_body("whatever"),
        headers=_platform_headers(platform_admin.id),
    )

    assert response.status_code == 404


async def test_compliance_export_unknown_org_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _org, _user, platform_admin = await _seed(db_session)

    export = await client.get(
        f"/platform/orgs/{uuid4()}/compliance-export",
        headers=_platform_headers(platform_admin.id),
    )

    assert export.status_code == 404


async def test_re_erase_is_idempotent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Erasing an already-offboarded org again is a clean no-op with zero counts (operator
    # double-click on a destructive endpoint must be safe).
    org, _user, platform_admin = await _seed(db_session)
    headers = _platform_headers(platform_admin.id)
    body = _erase_body("acme")
    first = await client.post(f"/platform/orgs/{org.id}/erase", json=body, headers=headers)
    assert first.status_code == 200

    second = await client.post(f"/platform/orgs/{org.id}/erase", json=body, headers=headers)

    assert second.status_code == 200
    assert second.json()["users_erased"] == 0
    assert second.json()["tokens_deleted"] == 0


async def test_compliance_export_after_erase_still_builds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # The proof-of-processing artifact must still build for an erased org (audit retained).
    org, _user, platform_admin = await _seed(db_session)
    headers = _platform_headers(platform_admin.id)
    await client.post(f"/platform/orgs/{org.id}/erase", json=_erase_body("acme"), headers=headers)

    export = await client.get(f"/platform/orgs/{org.id}/compliance-export", headers=headers)

    assert export.status_code == 200
    body = export.json()
    assert body["organization"]["status"] == "offboarded"
    assert body["organization"]["user_count"] == 0
    assert any(entry["action"] == "org.erased" for entry in body["audit"])  # trail retained


async def test_erase_missing_reason_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, _user, platform_admin = await _seed(db_session)

    response = await client.post(
        f"/platform/orgs/{org.id}/erase",
        json={"confirm_slug": "acme"},  # reason omitted
        headers=_platform_headers(platform_admin.id),
    )

    assert response.status_code == 422


async def test_erase_deactivated_admin_returns_403_and_deletes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # A deactivated platform admin still holding a valid token fails the sudo re-auth with the
    # SAME generic 403 as a wrong password (never a hint that the account state was the cause).
    org, _user, _active_admin = await _seed(db_session)
    deactivated = await seed_platform_admin(
        db_session, email="gone-acme@ethera.example", full_name="Gone", is_active=False
    )
    await db_session.commit()

    response = await client.post(
        f"/platform/orgs/{org.id}/erase",
        json=_erase_body("acme"),  # the CORRECT password — deactivation alone must reject
        headers=_platform_headers(deactivated.id),
    )

    assert response.status_code == 403
    assert await _count(db_session, "SELECT count(*) FROM users WHERE org_id=:o", org.id) == 1