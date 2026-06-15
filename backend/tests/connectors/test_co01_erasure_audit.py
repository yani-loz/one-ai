"""
Role: End-to-end tests for the CO-01 per-user erasure (AC10) and the audit lifecycle (AC12).
      AC10: user A's disconnect deletes A's connection, DB-cascades its ingested email corpus, and
      withdraws A's consent (retained as proof) — while user B's connection + corpus stay intact.
      AC12: the full Tier 1/2/3 lifecycle writes the expected audit actions (entitlement.granted/
      revoked, connector.policy_changed, connector.consented, connector.connected/disconnected),
      each actor-attributed and org-scoped.
Used by: pytest (tests/connectors). Real DB + the connectors conftest (me_client, seed helpers).
Depends on: tests.connectors.conftest (me_client / db_session / company_token / platform_token /
            bearer), tests.connectors.co01_seed (seed_user / seed_entitlement / seed_policy),
            app.connectors.imap.models.email (the cascade read-back), app.identity AuditLog.
Key invariants tested:
  - AC10 (non-negotiable): DELETE /me/connectors/{A} removes A's connection + its email_message rows
    (ON DELETE CASCADE) + marks A's consent withdrawn; B's connection + its email_message survive.
  - AC9: the same DELETE withdraws the consent (withdrawn_at set, the row RETAINED — Art. 7(4)).
  - AC12: each lifecycle action appears once, with the acting principal's id + the right org_id.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailMessage
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_consent import ConnectorConsent
from app.identity.models.audit_log import AuditLog
from tests.connectors.co01_seed import seed_entitlement, seed_policy, seed_user
from tests.connectors.conftest import bearer, company_token, platform_token


def _payload(username: str) -> dict[str, object]:
    """A valid /me self-connect body for `username`."""
    return {
        "connector_type": "imap",
        "display_name": "Mailbox",
        "host": "mail.example.com",
        "port": 993,
        "use_ssl": True,
        "username": username,
        "password": "imap-app-pw-123",
        "consent": {"accepted": True, "scope": "mailbox:read", "consent_version": "v1"},
    }


async def _seed_email(session: AsyncSession, org_id: UUID, connection_id: UUID, key: str) -> UUID:
    """Insert one EmailMessage tied to `connection_id` (simulated corpus); return its id."""
    email = EmailMessage(
        org_id=org_id,
        connection_id=connection_id,
        dedup_key=key,
        parse_status="parsed",
    )
    session.add(email)
    await session.flush()
    return email.id


# ── AC10 — per-user erasure: A erased (corpus cascades), B intact ───────────────────────────


async def test_disconnect_deletes_owner_connection_and_cascades_corpus_keeping_other_user(
    me_client: AsyncClient, db_session: AsyncSession
) -> None:
    org_id = uuid4()
    user_a = await seed_user(org_id, role="member")
    user_b = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=True)
    token_a = company_token(user_a, org_id, role="member")
    token_b = company_token(user_b, org_id, role="member")
    conn_a = (
        await me_client.post(
            "/me/connectors", json=_payload("a@example.com"), headers=bearer(token_a)
        )
    ).json()["id"]
    conn_b = (
        await me_client.post(
            "/me/connectors", json=_payload("b@example.com"), headers=bearer(token_b)
        )
    ).json()["id"]
    email_a = await _seed_email(db_session, org_id, UUID(conn_a), "dedup-a")
    email_b = await _seed_email(db_session, org_id, UUID(conn_b), "dedup-b")
    await db_session.commit()

    delete = await me_client.delete(f"/me/connectors/{conn_a}", headers=bearer(token_a))

    assert delete.status_code == 204
    surviving_connections = (
        (
            await db_session.execute(
                select(ConnectorConnection.id).where(ConnectorConnection.org_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    surviving_emails = (
        (await db_session.execute(select(EmailMessage.id).where(EmailMessage.org_id == org_id)))
        .scalars()
        .all()
    )
    # A's connection + its email are gone (cascade); B's connection + its email survive.
    assert UUID(conn_a) not in surviving_connections
    assert UUID(conn_b) in surviving_connections
    assert email_a not in surviving_emails
    assert email_b in surviving_emails


async def test_disconnect_withdraws_consent_and_retains_the_row(
    me_client: AsyncClient, db_session: AsyncSession
) -> None:
    org_id = uuid4()
    user_id = await seed_user(org_id, role="member")
    await seed_entitlement(org_id, enabled=True)
    await seed_policy(org_id, org_wide_enabled=True)
    token = company_token(user_id, org_id, role="member")
    conn = (
        await me_client.post(
            "/me/connectors", json=_payload("c@example.com"), headers=bearer(token)
        )
    ).json()["id"]

    await me_client.delete(f"/me/connectors/{conn}", headers=bearer(token))

    consents = (
        (
            await db_session.execute(
                select(ConnectorConsent).where(ConnectorConsent.org_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(consents) == 1  # the consent row is RETAINED as proof (Art. 7(4))
    assert consents[0].withdrawn_at is not None  # but marked withdrawn


# ── AC12 — the lifecycle writes the expected actor-attributed, org-scoped audit actions ─────


async def _actions_for(session: AsyncSession, org_id: UUID) -> dict[str, AuditLog]:
    """Return {action: row} for one org's audit rows (one expected per action in this lifecycle)."""
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.org_id == org_id))).scalars().all()
    )
    return {row.action: row for row in rows}


async def test_full_lifecycle_writes_expected_audit_actions(
    me_client: AsyncClient, db_session: AsyncSession
) -> None:
    org_id = uuid4()
    platform_admin_id = uuid4()
    admin_id = await seed_user(org_id, role="company_admin")
    member_id = await seed_user(org_id, role="member")

    # Tier 1 — platform grants then revokes (we grant again so Tier 2/3 can proceed).
    ent_url = f"/platform/orgs/{org_id}/connector-entitlements"
    p_headers = bearer(platform_token(platform_admin_id))
    await me_client.put(
        ent_url, json={"connector_type": "imap", "enabled": True}, headers=p_headers
    )
    await me_client.put(
        ent_url, json={"connector_type": "imap", "enabled": False}, headers=p_headers
    )
    await me_client.put(
        ent_url, json={"connector_type": "imap", "enabled": True}, headers=p_headers
    )

    # Tier 2 — the admin sets the org-wide policy on.
    await me_client.put(
        "/admin/connectors/policies",
        json={"connector_type": "imap", "org_wide_enabled": True},
        headers=bearer(company_token(admin_id, org_id, role="company_admin")),
    )

    # Tier 3 — the member self-connects (connected + consented), then disconnects.
    member = company_token(member_id, org_id, role="member")
    conn = (
        await me_client.post(
            "/me/connectors", json=_payload("m@example.com"), headers=bearer(member)
        )
    ).json()["id"]
    await me_client.delete(f"/me/connectors/{conn}", headers=bearer(member))

    actions = await _actions_for(db_session, org_id)
    # Every expected lifecycle action was recorded for this org.
    for action in (
        "entitlement.granted",
        "entitlement.revoked",
        "connector.policy_changed",
        "connector.consented",
        "connector.connected",
        "connector.disconnected",
    ):
        assert action in actions, f"missing audit action {action}"
    # Actor attribution: the platform admin owns the entitlement rows; the company admin owns the
    # policy change; the member owns the connect/consent/disconnect.
    assert actions["entitlement.granted"].actor_id == platform_admin_id
    assert actions["entitlement.granted"].actor_type == "platform_admin"
    assert actions["connector.policy_changed"].actor_id == admin_id
    assert actions["connector.connected"].actor_id == member_id
    assert actions["connector.consented"].actor_id == member_id
    assert actions["connector.disconnected"].actor_id == member_id
    assert actions["connector.disconnected"].actor_type == "user"
