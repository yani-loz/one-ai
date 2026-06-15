"""
Role: HTTP contract + authorization tests for the Tier-1 entitlement plane (CO-01 /platform/orgs/
      {org_id}/connector-entitlements). Covers AC7 (a platform admin grants -> the company's
      governance shows entitled=true; revoke -> entitled=false but its policies/connections persist;
      re-grant re-exposes them), the platform-token gate (a company token is rejected), and AC4
      cross-tenant invisibility (org A's entitlement is read/written only via its own path).
Used by: pytest (tests/connectors). Real DB + the connectors conftest (platform/company tokens,
         me_client, seed helpers).
Depends on: tests.connectors.conftest (client / me_client / platform_token / company_token /
            bearer), tests.connectors.co01_seed (seed_user / seed_policy).
Key invariants tested:
  - AC7: PUT enabled=true makes get_governance(entitled) true; PUT enabled=false makes it false
    while the org's policy + connections survive (no cascade); a re-grant re-exposes the same rows.
  - A COMPANY token on the platform entitlement route is rejected (401, wrong audience); a member's
    company token likewise can never reach the platform plane.
  - The entitlement for org A is addressed by the {org_id} PATH (a platform admin acts across orgs),
    never by a tenant JWT — org B's path returns org B's (empty) entitlement, not org A's.
"""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient

from tests.conftest import seed_org
from tests.connectors.co01_seed import seed_policy, seed_user
from tests.connectors.conftest import bearer, company_token, platform_token


def _self_connect_payload(username: str = "owner@example.com") -> dict[str, object]:
    """A valid /me self-connect body (used to seed a surviving connection across a revoke)."""
    return {
        "connector_type": "imap",
        "display_name": "Owner mailbox",
        "host": "mail.example.com",
        "port": 993,
        "use_ssl": True,
        "username": username,
        "password": "imap-app-pw-123",
        "consent": {"accepted": True, "scope": "mailbox:read", "consent_version": "v1"},
    }


# ── AC7 — grant / revoke / re-grant, governance reflects the ceiling ────────────────────────


async def test_platform_grant_makes_governance_entitled(me_client: AsyncClient) -> None:
    org_id = uuid4()
    admin_id = await seed_user(org_id, role="company_admin")

    grant = await me_client.put(
        f"/platform/orgs/{org_id}/connector-entitlements",
        json={"connector_type": "imap", "enabled": True},
        headers=bearer(platform_token()),
    )
    governance = await me_client.get(
        "/admin/connectors/governance/imap",
        headers=bearer(company_token(admin_id, org_id, role="company_admin")),
    )

    assert grant.status_code == 200
    assert grant.json()["enabled"] is True
    assert governance.json()["entitled"] is True


async def test_platform_revoke_keeps_governance_not_entitled_but_persists_policy_and_connections(
    me_client: AsyncClient,
) -> None:
    org_id = uuid4()
    admin_id = await seed_user(org_id, role="company_admin")
    member_id = await seed_user(org_id, role="member")
    await me_client.put(
        f"/platform/orgs/{org_id}/connector-entitlements",
        json={"connector_type": "imap", "enabled": True},
        headers=bearer(platform_token()),
    )
    await seed_policy(org_id, org_wide_enabled=True)
    await me_client.post(
        "/me/connectors",
        json=_self_connect_payload("keeps@example.com"),
        headers=bearer(company_token(member_id, org_id, role="member")),
    )

    revoke = await me_client.put(
        f"/platform/orgs/{org_id}/connector-entitlements",
        json={"connector_type": "imap", "enabled": False},
        headers=bearer(platform_token()),
    )
    governance = await me_client.get(
        "/admin/connectors/governance/imap",
        headers=bearer(company_token(admin_id, org_id, role="company_admin")),
    )

    assert revoke.status_code == 200
    body = governance.json()
    assert body["entitled"] is False  # the ceiling now denies
    assert body["org_wide_enabled"] is True  # the policy survived the revoke (no cascade)
    assert len(body["connections"]) == 1  # the connection survived too


async def test_platform_regrant_re_exposes_entitlement(me_client: AsyncClient) -> None:
    org_id = uuid4()
    admin_id = await seed_user(org_id, role="company_admin")
    headers = bearer(platform_token())
    url = f"/platform/orgs/{org_id}/connector-entitlements"
    await me_client.put(url, json={"connector_type": "imap", "enabled": True}, headers=headers)
    await me_client.put(url, json={"connector_type": "imap", "enabled": False}, headers=headers)

    await me_client.put(url, json={"connector_type": "imap", "enabled": True}, headers=headers)
    governance = await me_client.get(
        "/admin/connectors/governance/imap",
        headers=bearer(company_token(admin_id, org_id, role="company_admin")),
    )

    assert governance.json()["entitled"] is True  # re-grant restores the ceiling


async def test_list_entitlements_reflects_revoked_state(client: AsyncClient) -> None:
    org_id = await seed_org()
    headers = bearer(platform_token())
    url = f"/platform/orgs/{org_id}/connector-entitlements"
    await client.put(url, json={"connector_type": "imap", "enabled": True}, headers=headers)
    await client.put(url, json={"connector_type": "imap", "enabled": False}, headers=headers)

    listed = await client.get(url, headers=headers)

    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["enabled"] is False
    assert rows[0]["revoked_at"] is not None


# ── Token-audience gate: a company token can't reach the platform plane ──────────────────────


async def test_company_token_on_entitlement_route_is_rejected(client: AsyncClient) -> None:
    org_id = await seed_org()
    company = company_token(uuid4(), org_id, role="company_admin")

    response = await client.put(
        f"/platform/orgs/{org_id}/connector-entitlements",
        json={"connector_type": "imap", "enabled": True},
        headers=bearer(company),
    )

    assert response.status_code == 401  # wrong audience — never reaches the platform handler


async def test_member_token_on_entitlement_route_is_rejected(client: AsyncClient) -> None:
    org_id = uuid4()
    member = company_token(uuid4(), org_id, role="member")

    response = await client.get(
        f"/platform/orgs/{org_id}/connector-entitlements", headers=bearer(member)
    )

    assert response.status_code == 401


async def test_entitlement_route_missing_token_returns_401(client: AsyncClient) -> None:
    response = await client.get(f"/platform/orgs/{uuid4()}/connector-entitlements")

    assert response.status_code == 401


# ── AC4 — entitlement for org A is invisible from org B's path ──────────────────────────────


async def test_org_b_entitlement_path_does_not_show_org_a_entitlement(client: AsyncClient) -> None:
    org_a = await seed_org()
    org_b = uuid4()
    headers = bearer(platform_token())
    await client.put(
        f"/platform/orgs/{org_a}/connector-entitlements",
        json={"connector_type": "imap", "enabled": True},
        headers=headers,
    )

    listed_b = await client.get(f"/platform/orgs/{org_b}/connector-entitlements", headers=headers)

    assert listed_b.status_code == 200
    assert listed_b.json() == []  # org B's path carries org B's entitlements only (none)
