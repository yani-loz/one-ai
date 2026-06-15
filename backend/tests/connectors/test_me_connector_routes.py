"""
Role: HTTP contract + authorization tests for the Tier-3 self-connect plane (CO-01 /me/connectors).
      Covers who may connect (AC2 member access, AC5 permission resolution at the route) and the
      NON-NEGOTIABLE per-user isolation (AC3 — user B can't touch user A's connection: 404, no
      leak). The /me sync test rides me_client (captured spawner). Consent (AC9) lives in its own
      sibling test_co01_consent_routes.py.
Used by: pytest (tests/connectors). Real DB + the connectors conftest (stub registry, seed helpers).
Depends on: tests.connectors.conftest (me_client / stub_outcome / spawn_calls / company_token /
            bearer), tests.connectors.co01_seed (seed_user / seed_entitlement / seed_policy /
            seed_override), app.connectors models (the no-row-created read-back).
Key invariants tested:
  - AC2: a MEMBER (not just an admin) can self-connect, list, get, and test their OWN connection
    when allowed; a missing token is 401.
  - AC5: org-wide-off + no grant -> 403 BEFORE any row is created; org-wide-off + per-user grant
    -> allowed; org-wide-on + per-user deny -> 403; a non-entitled company -> allowed=false + 403.
  - AC3 (non-negotiable): user B (same org) gets 404 on GET/test/sync/DELETE of A's connection, and
    A's mailbox identity never appears in B's response body.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.enums import OverrideType
from app.connectors.models.connector_connection import ConnectorConnection
from tests.connectors.co01_seed import (
    seed_entitlement,
    seed_override,
    seed_policy,
    seed_user,
)
from tests.connectors.conftest import bearer, company_token


def _self_connect_payload(*, accepted: bool = True) -> dict[str, object]:
    """A valid /me self-connect body (IMAP params + write-only password + consent)."""
    return {
        "connector_type": "imap",
        "display_name": "My mailbox",
        "host": "mail.example.com",
        "port": 993,
        "use_ssl": True,
        "username": "me@example.com",
        "password": "imap-app-pw-123",
        "consent": {"accepted": accepted, "scope": "mailbox:read", "consent_version": "v1"},
    }


async def _allowed_org() -> tuple[UUID, UUID]:
    """Seed an entitled, org-wide-on org with one member; return (member_id, org_id)."""
    org_id = uuid4()
    user_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=True)
    return user_id, org_id


# ── AC2 — a member owns the self-connect plane ──────────────────────────────────────────────


async def test_member_can_self_connect_when_allowed_returns_201(me_client: AsyncClient) -> None:
    user_id, org_id = await _allowed_org()
    token = company_token(user_id, org_id, role="member")

    response = await me_client.post(
        "/me/connectors", json=_self_connect_payload(), headers=bearer(token)
    )

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "me@example.com"
    assert "password" not in body
    assert "imap-app-pw-123" not in response.text


async def test_member_can_list_and_get_and_test_their_own_connection(
    me_client: AsyncClient,
) -> None:
    user_id, org_id = await _allowed_org()
    token = company_token(user_id, org_id, role="member")
    created = await me_client.post(
        "/me/connectors", json=_self_connect_payload(), headers=bearer(token)
    )
    connection_id = created.json()["id"]

    listed = await me_client.get("/me/connectors", headers=bearer(token))
    got = await me_client.get(f"/me/connectors/{connection_id}", headers=bearer(token))
    tested = await me_client.post(f"/me/connectors/{connection_id}/test", headers=bearer(token))

    assert listed.status_code == 200 and len(listed.json()) == 1
    assert got.status_code == 200 and got.json()["id"] == connection_id
    assert tested.status_code == 200 and tested.json()["status"] == "connected"


async def test_self_connect_missing_token_returns_401(me_client: AsyncClient) -> None:
    response = await me_client.post("/me/connectors", json=_self_connect_payload())

    assert response.status_code == 401


async def test_member_can_sync_their_own_connection_returns_202(
    me_client: AsyncClient, spawn_calls: list[str]
) -> None:
    user_id, org_id = await _allowed_org()
    token = company_token(user_id, org_id, role="member")
    connection_id = (
        await me_client.post("/me/connectors", json=_self_connect_payload(), headers=bearer(token))
    ).json()["id"]

    response = await me_client.post(f"/me/connectors/{connection_id}/sync", headers=bearer(token))

    assert response.status_code == 202
    assert response.json()["sync_status"] == "running"
    assert spawn_calls == [f"sync:{connection_id}"]


# ── AC5 — permission resolution gates the self-connect at the route ─────────────────────────


async def test_self_connect_org_wide_off_no_grant_returns_403_and_creates_nothing(
    me_client: AsyncClient, db_session: AsyncSession
) -> None:
    org_id = uuid4()
    user_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=False)
    token = company_token(user_id, org_id, role="member")

    response = await me_client.post(
        "/me/connectors", json=_self_connect_payload(), headers=bearer(token)
    )

    assert response.status_code == 403
    assert "enabled this for you" in response.json()["detail"]
    rows = (
        (
            await db_session.execute(
                select(ConnectorConnection).where(ConnectorConnection.org_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []  # the 403 fired BEFORE any credential work — no connection row


async def test_self_connect_org_wide_off_with_grant_returns_201(me_client: AsyncClient) -> None:
    org_id = uuid4()
    user_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=False)
    await seed_override(org_id, user_id, override_type=OverrideType.grant)
    token = company_token(user_id, org_id, role="member")

    response = await me_client.post(
        "/me/connectors", json=_self_connect_payload(), headers=bearer(token)
    )

    assert response.status_code == 201  # the per-user grant wins over org-wide-off


async def test_self_connect_org_wide_on_with_deny_returns_403(me_client: AsyncClient) -> None:
    org_id = uuid4()
    user_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=True)
    await seed_override(org_id, user_id, override_type=OverrideType.deny)
    token = company_token(user_id, org_id, role="member")

    response = await me_client.post(
        "/me/connectors", json=_self_connect_payload(), headers=bearer(token)
    )

    assert response.status_code == 403  # the per-user deny wins over org-wide-on


async def test_non_entitled_company_types_show_not_allowed_and_self_connect_403(
    me_client: AsyncClient,
) -> None:
    org_id = uuid4()
    user_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=False)
    await seed_policy(org_id, org_wide_enabled=True)  # policy on, but the ceiling denies
    token = company_token(user_id, org_id, role="member")

    types = await me_client.get("/me/connectors/types", headers=bearer(token))
    connect = await me_client.post(
        "/me/connectors", json=_self_connect_payload(), headers=bearer(token)
    )

    imap_card = next(t for t in types.json() if t["connector_type"] == "imap")
    assert imap_card["allowed"] is False
    assert "plan" in (imap_card["reason"] or "")
    assert connect.status_code == 403
    assert "plan" in connect.json()["detail"]


# ── AC3 — per-user isolation (NON-NEGOTIABLE): B cannot touch A's connection ────────────────


async def test_user_b_cannot_access_user_a_connection_returns_404_no_leak(
    me_client: AsyncClient,
) -> None:
    org_id = uuid4()
    user_a = await seed_user(org_id, role="member")
    user_b = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=True)
    token_a = company_token(user_a, org_id, role="member")
    token_b = company_token(user_b, org_id, role="member")
    connection_id = (
        await me_client.post(
            "/me/connectors", json=_self_connect_payload(), headers=bearer(token_a)
        )
    ).json()["id"]

    get_b = await me_client.get(f"/me/connectors/{connection_id}", headers=bearer(token_b))
    test_b = await me_client.post(f"/me/connectors/{connection_id}/test", headers=bearer(token_b))
    sync_b = await me_client.post(f"/me/connectors/{connection_id}/sync", headers=bearer(token_b))
    delete_b = await me_client.delete(f"/me/connectors/{connection_id}", headers=bearer(token_b))

    assert get_b.status_code == 404
    assert test_b.status_code == 404
    assert sync_b.status_code == 404
    assert delete_b.status_code == 404
    # B never sees A's mailbox identity in any field of any response.
    assert all("me@example.com" not in r.text for r in (get_b, test_b, sync_b, delete_b))


async def test_user_b_list_excludes_user_a_connection(me_client: AsyncClient) -> None:
    org_id = uuid4()
    user_a = await seed_user(org_id, role="member")
    user_b = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=True)
    await me_client.post(
        "/me/connectors",
        json=_self_connect_payload(),
        headers=bearer(company_token(user_a, org_id, role="member")),
    )

    listed = await me_client.get(
        "/me/connectors", headers=bearer(company_token(user_b, org_id, role="member"))
    )

    assert listed.status_code == 200
    assert listed.json() == []
    assert "me@example.com" not in listed.text
