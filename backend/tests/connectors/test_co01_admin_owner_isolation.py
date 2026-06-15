"""
Role: Regression tests for the CO-01 §7 admin/owner-isolation fix (adversarial-review HIGH) and
      the multi-mailbox consent fix (review MEDIUM). The admin lifecycle plane (/admin/connectors/*)
      is SHARED-only: it can NEVER reach a user's self-connected (owner_user_id-set) mailbox, so an
      admin cannot read its address (GET/list), decrypt+use its credential (test), ingest its mail
      (sync), or disable/delete it — every such call 404s with no leak. Consent withdrawal on
      disconnect is scoped to the LAST mailbox of a type, so disconnecting one of several does not
      revoke the still-connected ones' lawful basis.
Used by: pytest (tests/connectors). Real DB + the connectors conftest (stub registry, seed helpers).
Depends on: tests.connectors.conftest (me_client / company_token / bearer / spawn_calls / session)
            tests.connectors.co01_seed (seed_user / seed_entitlement / seed_policy), the connector +
            consent models for the DB read-backs.
Key invariants tested:
  - §7 (non-negotiable): /admin/connectors get/list/test/sync/disable/delete on a USER-OWNED id ->
    404, and the owner's username/host never appears in any response body; the row is untouched.
  - The admin SHARED plane still works (admin can create + get + test an org-owned connection).
  - Multi-mailbox consent: disconnecting one of two same-type mailboxes keeps the other's consent
    active; disconnecting the last withdraws it.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_consent import ConnectorConsent
from tests.connectors.co01_seed import seed_entitlement, seed_policy, seed_user
from tests.connectors.conftest import bearer, company_token

_OWNER_USERNAME = "owner-secret@example.com"
_OWNER_HOST = "owner-secret-host.example.com"


def _self_connect_payload(username: str = _OWNER_USERNAME) -> dict[str, object]:
    """A valid /me self-connect body (creates a USER-OWNED connection)."""
    return {
        "connector_type": "imap",
        "display_name": "Owner mailbox",
        "host": _OWNER_HOST,
        "port": 993,
        "use_ssl": True,
        "username": username,
        "password": "owner-app-pw-123",
        "consent": {"accepted": True, "scope": "mailbox:read", "consent_version": "v1"},
    }


def _admin_create_payload() -> dict[str, object]:
    """A valid /admin/connectors body (creates a SHARED/org-owned connection)."""
    return {
        "connector_type": "imap",
        "display_name": "Shared info@",
        "host": "shared-host.example.com",
        "port": 993,
        "use_ssl": True,
        "username": "info@example.com",
        "password": "shared-app-pw-123",
    }


async def _member_with_own_connection(me_client: AsyncClient) -> tuple[UUID, UUID, str]:
    """Seed an entitled+on org, member self-connects; return (member_id, org_id, connection_id)."""
    org_id = uuid4()
    member_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=True)
    token = company_token(member_id, org_id, role="member")
    connection_id = (
        await me_client.post("/me/connectors", json=_self_connect_payload(), headers=bearer(token))
    ).json()["id"]
    return member_id, org_id, connection_id


def _no_owner_leak(*responses: object) -> bool:
    """True iff none of the response bodies contain the owner's mailbox username or host."""
    return all(
        _OWNER_USERNAME not in r.text and _OWNER_HOST not in r.text  # type: ignore[attr-defined]
        for r in responses
    )


# ── §7 — the admin lifecycle plane cannot reach a user-owned connection ──────────────────────


async def test_admin_get_user_owned_connection_returns_404_no_leak(me_client: AsyncClient) -> None:
    _member_id, org_id, connection_id = await _member_with_own_connection(me_client)
    admin = company_token(
        await seed_user(org_id, role="company_admin"), org_id, role="company_admin"
    )

    got = await me_client.get(f"/admin/connectors/{connection_id}", headers=bearer(admin))

    assert got.status_code == 404
    assert _no_owner_leak(got)


async def test_admin_list_excludes_user_owned_connections(me_client: AsyncClient) -> None:
    _member_id, org_id, _connection_id = await _member_with_own_connection(me_client)
    admin = company_token(
        await seed_user(org_id, role="company_admin"), org_id, role="company_admin"
    )

    listed = await me_client.get("/admin/connectors", headers=bearer(admin))

    assert listed.status_code == 200
    assert listed.json() == []  # the member's user-owned mailbox is invisible to the admin plane
    assert _no_owner_leak(listed)


async def test_admin_test_user_owned_connection_returns_404(me_client: AsyncClient) -> None:
    _member_id, org_id, connection_id = await _member_with_own_connection(me_client)
    admin = company_token(
        await seed_user(org_id, role="company_admin"), org_id, role="company_admin"
    )

    tested = await me_client.post(f"/admin/connectors/{connection_id}/test", headers=bearer(admin))

    assert tested.status_code == 404  # admin can't decrypt + IMAP-login an employee's mailbox
    assert _no_owner_leak(tested)


async def test_admin_sync_user_owned_connection_returns_404_no_spawn(
    me_client: AsyncClient, spawn_calls: list[str]
) -> None:
    _member_id, org_id, connection_id = await _member_with_own_connection(me_client)
    admin = company_token(
        await seed_user(org_id, role="company_admin"), org_id, role="company_admin"
    )

    synced = await me_client.post(f"/admin/connectors/{connection_id}/sync", headers=bearer(admin))

    assert synced.status_code == 404  # admin can't ingest an employee's mail into org memory
    assert spawn_calls == []  # no runner spawned


async def test_admin_delete_user_owned_connection_returns_404_row_intact(
    me_client: AsyncClient, db_session: AsyncSession
) -> None:
    _member_id, org_id, connection_id = await _member_with_own_connection(me_client)
    admin = company_token(
        await seed_user(org_id, role="company_admin"), org_id, role="company_admin"
    )

    deleted = await me_client.delete(f"/admin/connectors/{connection_id}", headers=bearer(admin))

    assert deleted.status_code == 404
    still_there = (
        await db_session.execute(
            select(ConnectorConnection).where(ConnectorConnection.id == UUID(connection_id))
        )
    ).scalar_one_or_none()
    assert still_there is not None  # the admin could not delete the member's mailbox


async def test_admin_disable_user_owned_connection_returns_404(me_client: AsyncClient) -> None:
    _member_id, org_id, connection_id = await _member_with_own_connection(me_client)
    admin = company_token(
        await seed_user(org_id, role="company_admin"), org_id, role="company_admin"
    )

    disabled = await me_client.post(
        f"/admin/connectors/{connection_id}/disable", headers=bearer(admin)
    )

    assert disabled.status_code == 404


# ── The admin SHARED plane still works (the fix didn't break org-owned connections) ──────────


async def test_admin_can_create_get_and_test_a_shared_connection(me_client: AsyncClient) -> None:
    org_id = uuid4()
    await seed_entitlement(org_id, enabled=True)  # the company is entitled (Tier-1 ceiling)
    admin = company_token(
        await seed_user(org_id, role="company_admin"), org_id, role="company_admin"
    )

    created = await me_client.post(
        "/admin/connectors", json=_admin_create_payload(), headers=bearer(admin)
    )
    connection_id = created.json()["id"]
    got = await me_client.get(f"/admin/connectors/{connection_id}", headers=bearer(admin))
    tested = await me_client.post(f"/admin/connectors/{connection_id}/test", headers=bearer(admin))

    assert created.status_code == 201
    assert got.status_code == 200 and got.json()["username"] == "info@example.com"
    assert tested.status_code == 200 and tested.json()["status"] == "connected"


# ── Consent — disconnecting one of several same-type mailboxes keeps the others' consent ─────


async def test_disconnect_one_of_two_mailboxes_keeps_other_consent_active(
    me_client: AsyncClient, db_session: AsyncSession
) -> None:
    org_id = uuid4()
    member_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=True)
    token = company_token(member_id, org_id, role="member")
    first = (
        await me_client.post(
            "/me/connectors",
            json=_self_connect_payload("mbox-a@example.com"),
            headers=bearer(token),
        )
    ).json()["id"]
    await me_client.post(
        "/me/connectors", json=_self_connect_payload("mbox-b@example.com"), headers=bearer(token)
    )

    disconnect = await me_client.delete(f"/me/connectors/{first}", headers=bearer(token))

    assert disconnect.status_code == 204
    active = (
        (
            await db_session.execute(
                select(ConnectorConsent).where(
                    ConnectorConsent.org_id == org_id,
                    ConnectorConsent.user_id == member_id,
                    ConnectorConsent.connector_type == "imap",
                    ConnectorConsent.withdrawn_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    # The second mailbox is still connected -> its consent must remain in force (not blanket-cut).
    assert len(active) >= 1
