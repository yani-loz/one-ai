"""
Role: HTTP contract + authorization tests for the Tier-2 governance plane (CO-01 /admin/connectors
      governance/policies/overrides). Covers AC6 (admin sets org-wide + per-user grant/deny and it
      takes effect on a member's /me access; can't enable/grant beyond the entitlement ceiling;
      member -> 403), AC8 (the §7 metadata-only view — field-by-field NO secret/host/username), and
      the AC4 cross-tenant 404/empty (org A can't see org B's governance).
Used by: pytest (tests/connectors). Real DB + the connectors conftest (stub registry, me_client,
         seed helpers).
Depends on: tests.connectors.conftest (client / me_client / company_token / bearer),
            tests.connectors.co01_seed (seed_user / seed_entitlement / seed_policy).
Key invariants tested:
  - AC6: org-wide on/off + per-user grant/deny written by an admin changes a member's /me result.
    Enabling/granting a non-entitled type -> 403. A member calling governance -> 403.
  - AC8 (non-negotiable §7): the governance connection entries carry owner + status + sync health
    ONLY — asserted field-by-field that NO password/secret/host/port/username/config key is present.
  - AC4 (non-negotiable): org A's admin sees only its own governance; org B's policy/overrides/
    connections never appear, and B's mailbox identity never leaks into A's body.
"""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient

from tests.connectors.co01_seed import seed_entitlement, seed_policy, seed_user
from tests.connectors.conftest import bearer, company_token


def _self_connect_payload(username: str = "owner@example.com") -> dict[str, object]:
    """A valid /me self-connect body for seeding a user-owned connection to govern over."""
    return {
        "connector_type": "imap",
        "display_name": "Owner mailbox",
        "host": "secret-host.example.com",
        "port": 993,
        "use_ssl": True,
        "username": username,
        "password": "imap-secret-pw-xyz",
        "consent": {"accepted": True, "scope": "mailbox:read", "consent_version": "v1"},
    }


# ── AC6 — the admin governs reach; it takes effect on /me ───────────────────────────────────


async def test_admin_org_wide_toggle_changes_member_self_connect_access(
    me_client: AsyncClient,
) -> None:
    org_id = uuid4()
    admin_id = await seed_user(org_id, role="company_admin")
    member_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    admin = company_token(admin_id, org_id, role="company_admin")
    member = company_token(member_id, org_id, role="member")

    off = await me_client.post(
        "/me/connectors", json=_self_connect_payload("m@example.com"), headers=bearer(member)
    )
    await me_client.put(
        "/admin/connectors/policies",
        json={"connector_type": "imap", "org_wide_enabled": True},
        headers=bearer(admin),
    )
    on = await me_client.post(
        "/me/connectors", json=_self_connect_payload("m@example.com"), headers=bearer(member)
    )

    assert off.status_code == 403  # org-wide defaults off -> member denied
    assert on.status_code == 201  # admin turned it on -> member allowed


async def test_admin_per_user_grant_changes_member_self_connect_access(
    me_client: AsyncClient,
) -> None:
    org_id = uuid4()
    admin_id = await seed_user(org_id, role="company_admin")
    member_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=False)
    admin = company_token(admin_id, org_id, role="company_admin")
    member = company_token(member_id, org_id, role="member")

    await me_client.put(
        "/admin/connectors/overrides",
        json={"connector_type": "imap", "user_id": str(member_id), "override_type": "grant"},
        headers=bearer(admin),
    )
    granted = await me_client.post(
        "/me/connectors", json=_self_connect_payload("m@example.com"), headers=bearer(member)
    )

    assert granted.status_code == 201  # the per-user grant overrides org-wide-off


async def test_admin_cannot_enable_org_wide_beyond_entitlement_returns_403(
    client: AsyncClient,
) -> None:
    org_id = uuid4()
    admin_id = await seed_user(org_id, role="company_admin")
    await seed_entitlement(org_id, enabled=False)  # not entitled
    admin = company_token(admin_id, org_id, role="company_admin")

    response = await client.put(
        "/admin/connectors/policies",
        json={"connector_type": "imap", "org_wide_enabled": True},
        headers=bearer(admin),
    )

    assert response.status_code == 403
    assert "plan" in response.json()["detail"]


async def test_admin_cannot_grant_user_beyond_entitlement_returns_403(client: AsyncClient) -> None:
    org_id = uuid4()
    admin_id = await seed_user(org_id, role="company_admin")
    member_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=False)  # not entitled
    admin = company_token(admin_id, org_id, role="company_admin")

    response = await client.put(
        "/admin/connectors/overrides",
        json={"connector_type": "imap", "user_id": str(member_id), "override_type": "grant"},
        headers=bearer(admin),
    )

    assert response.status_code == 403


async def test_member_cannot_read_governance_returns_403(client: AsyncClient) -> None:
    token = company_token(uuid4(), uuid4(), role="member")

    response = await client.get("/admin/connectors/governance/imap", headers=bearer(token))

    assert response.status_code == 403


async def test_member_cannot_set_policy_returns_403(client: AsyncClient) -> None:
    token = company_token(uuid4(), uuid4(), role="member")

    response = await client.put(
        "/admin/connectors/policies",
        json={"connector_type": "imap", "org_wide_enabled": True},
        headers=bearer(token),
    )

    assert response.status_code == 403


# ── AC8 — §7 metadata-only governance view (NON-NEGOTIABLE) ─────────────────────────────────

# Every field the §7 governance connection entry is ALLOWED to expose. The entry's key set must
# equal this EXACTLY — any extra key (a password/secret/ciphertext/host/port/username/config field)
# is a structural §7 leak. (Note: the legitimate `status` value "configured" contains "config", so a
# bare-substring scan would false-positive — the exact key-set + value-absence checks are precise.)
_ALLOWED_METADATA_FIELDS = {
    "id",
    "connector_type",
    "owner_user_id",
    "status",
    "is_enabled",
    "sync_status",
    "synced_count",
    "total_count",
    "last_synced_at",
    "last_error",
}
# Field NAMES that must NOT appear as keys anywhere in the body (the secret/param surface).
_FORBIDDEN_FIELD_NAMES = (
    "password",
    "secret_ciphertext",
    "secret_key_version",
    "host",
    "port",
    "use_ssl",
    "username",
    "config",
)


async def test_governance_view_exposes_metadata_only_no_secret_or_params(
    me_client: AsyncClient,
) -> None:
    org_id = uuid4()
    admin_id = await seed_user(org_id, role="company_admin")
    member_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=True)
    member = company_token(member_id, org_id, role="member")
    admin = company_token(admin_id, org_id, role="company_admin")
    await me_client.post(
        "/me/connectors",
        json=_self_connect_payload("secret-owner@example.com"),
        headers=bearer(member),
    )

    response = await me_client.get("/admin/connectors/governance/imap", headers=bearer(admin))

    assert response.status_code == 200
    body = response.json()
    assert len(body["connections"]) == 1
    entry = body["connections"][0]
    # Field-by-field: the entry's keys are EXACTLY the §7 metadata allowlist — nothing more.
    assert set(entry.keys()) == _ALLOWED_METADATA_FIELDS
    assert entry["owner_user_id"] == str(member_id)
    # No secret/param FIELD NAME appears as a key anywhere in the body (quoted as a JSON key).
    assert all(f'"{name}"' not in response.text for name in _FORBIDDEN_FIELD_NAMES)
    # No seeded sensitive VALUE (the mailbox username, host, or password) leaks into the payload.
    assert "secret-owner@example.com" not in response.text
    assert "secret-host.example.com" not in response.text
    assert "imap-secret-pw-xyz" not in response.text


# ── AC4 — cross-tenant: org A can't see org B's governance (NON-NEGOTIABLE) ─────────────────


async def test_org_a_governance_excludes_org_b_connections_and_policy(
    me_client: AsyncClient,
) -> None:
    org_b = uuid4()
    member_b = await seed_user(org_b, role="member")
    await seed_entitlement(org_b, enabled=True)
    await seed_policy(org_b, org_wide_enabled=True)
    await me_client.post(
        "/me/connectors",
        json=_self_connect_payload("b-owner@example.com"),
        headers=bearer(company_token(member_b, org_b, role="member")),
    )

    org_a = uuid4()
    admin_a = await seed_user(org_a, role="company_admin")
    await seed_entitlement(org_a, enabled=True)
    response = await me_client.get(
        "/admin/connectors/governance/imap",
        headers=bearer(company_token(admin_a, org_a, role="company_admin")),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["connections"] == []  # org A sees none of org B's connections
    assert body["org_wide_enabled"] is False  # org B's policy is invisible to org A
    assert "b-owner@example.com" not in response.text
