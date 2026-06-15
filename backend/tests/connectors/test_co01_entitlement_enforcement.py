"""
Role: Regression tests for two CO-01 cross-vendor-review findings — (P1) the admin lifecycle plane
      must enforce the Tier-1 entitlement ceiling (a non-entitled company can't store/sync a shared
      mailbox), and (P1) the /me sync/test path must RE-RESOLVE current access (a user allowed at
      connect time stops ingesting once an admin denies them or the platform revokes entitlement —
      while disconnect/erase stay allowed).
Used by: pytest (tests/connectors). Real DB + the connectors conftest (stub registry, seed helpers).
Depends on: tests.conftest (seed_org — a non-entitled org), tests.connectors.co01_seed
            (seed_user / seed_entitlement / seed_policy / seed_override), tests.connectors.conftest
            (client / me_client / spawn_calls / company_token / platform_token / bearer / session).
Key invariants tested:
  - Admin create without entitlement -> 403, no connection stored (entitlement is the ceiling).
  - /me sync after a per-user DENY -> 403, but disconnect still works (denial doesn't trap them).
  - /me sync after the platform REVOKES entitlement -> 403 (a revoked grant stops ingest).
"""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.enums import OverrideType
from app.connectors.models.connector_connection import ConnectorConnection
from tests.conftest import seed_org
from tests.connectors.co01_seed import seed_entitlement, seed_override, seed_policy, seed_user
from tests.connectors.conftest import bearer, company_token, platform_token


def _admin_payload() -> dict[str, object]:
    """A valid /admin/connectors (shared, org-owned) create body — no consent."""
    return {
        "connector_type": "imap",
        "display_name": "Shared mailbox",
        "host": "mail.example.com",
        "port": 993,
        "use_ssl": True,
        "username": "info@example.com",
        "password": "imap-app-pw-123",
    }


def _self_connect_payload() -> dict[str, object]:
    """A valid /me self-connect body (user-owned)."""
    return {
        "connector_type": "imap",
        "display_name": "My mailbox",
        "host": "mail.example.com",
        "port": 993,
        "use_ssl": True,
        "username": "me@example.com",
        "password": "imap-app-pw-123",
        "consent": {"accepted": True, "scope": "mailbox:read", "consent_version": "v1"},
    }


# ── Fix 1 — the admin plane enforces the entitlement ceiling ─────────────────────────────────


async def test_admin_create_without_entitlement_returns_403_no_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org_id = await seed_org()  # registered, but NO connector entitlement
    admin = company_token(uuid4(), org_id, role="company_admin")

    response = await client.post("/admin/connectors", json=_admin_payload(), headers=bearer(admin))

    assert response.status_code == 403
    assert "plan" in response.json()["detail"]
    rows = (
        (
            await db_session.execute(
                select(ConnectorConnection).where(ConnectorConnection.org_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []  # no credential stored for a non-entitled company


async def test_admin_create_with_entitlement_succeeds(client: AsyncClient) -> None:
    org_id = await seed_org()
    await seed_entitlement(org_id, enabled=True)
    admin = company_token(uuid4(), org_id, role="company_admin")

    response = await client.post("/admin/connectors", json=_admin_payload(), headers=bearer(admin))

    assert response.status_code == 201


# ── Fix 2 — /me sync re-resolves CURRENT access (deny / revoke takes effect on existing rows) ─


async def test_me_sync_after_deny_override_returns_403_but_disconnect_works(
    me_client: AsyncClient, spawn_calls: list[str]
) -> None:
    org_id = uuid4()
    member_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=True)
    token = company_token(member_id, org_id, role="member")
    connection_id = (
        await me_client.post("/me/connectors", json=_self_connect_payload(), headers=bearer(token))
    ).json()["id"]

    # The admin later DENIES this member; an existing connection must stop syncing.
    await seed_override(org_id, member_id, override_type=OverrideType.deny)
    synced = await me_client.post(f"/me/connectors/{connection_id}/sync", headers=bearer(token))
    disconnected = await me_client.delete(f"/me/connectors/{connection_id}", headers=bearer(token))

    assert synced.status_code == 403  # a now-denied user can't keep ingesting
    assert spawn_calls == []  # no runner spawned
    assert disconnected.status_code == 204  # but they can still disconnect/erase


async def test_me_sync_after_entitlement_revoked_returns_403(
    me_client: AsyncClient, spawn_calls: list[str]
) -> None:
    org_id = uuid4()
    member_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=True)
    token = company_token(member_id, org_id, role="member")
    connection_id = (
        await me_client.post("/me/connectors", json=_self_connect_payload(), headers=bearer(token))
    ).json()["id"]

    # The platform REVOKES the company's entitlement (via the real Tier-1 endpoint).
    revoke = await me_client.put(
        f"/platform/orgs/{org_id}/connector-entitlements",
        json={"connector_type": "imap", "enabled": False},
        headers=bearer(platform_token()),
    )
    synced = await me_client.post(f"/me/connectors/{connection_id}/sync", headers=bearer(token))

    assert revoke.status_code == 200
    assert synced.status_code == 403  # revoked entitlement stops ingest on the existing connection
    assert spawn_calls == []
