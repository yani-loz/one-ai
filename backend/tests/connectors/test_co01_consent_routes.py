"""
Role: HTTP contract tests for the CO-01 consent gate at self-connect (AC9, GDPR Art. 7 HITL) —
      the /me/connectors POST must refuse without an accepted consent and, on acceptance, record an
      in-force consent row atomically with the connection. Consent WITHDRAWAL on disconnect is
      covered in test_co01_erasure_audit.py.
Used by: pytest (tests/connectors). Real DB + the connectors conftest (me_client, seed helpers).
Depends on: tests.connectors.conftest (me_client / db_session / company_token / bearer),
            tests.connectors.co01_seed (seed_user / seed_entitlement / seed_policy),
            app.connectors models (the no-row-created + consent read-backs).
Key invariants tested:
  - consent.accepted=false -> 400 with NEITHER a connection NOR a consent row created (the server
    enforces the HITL gate even though the UI gates the button).
  - consent.accepted=true -> 201 + exactly one in-force consent row (withdrawn_at NULL) tagged to
    the owner, with the non-PII ui_proof (version + accepted flag).
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


def _payload(*, accepted: bool = True) -> dict[str, object]:
    """A valid /me self-connect body with a toggleable consent.accepted flag."""
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


async def _rows(session: AsyncSession, model: type, org_id: UUID) -> list[object]:
    """Return all rows of `model` for `org_id` (a small read-back helper)."""
    result = await session.execute(select(model).where(model.org_id == org_id))
    return list(result.scalars().all())


async def test_self_connect_without_consent_returns_400_and_creates_nothing(
    me_client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id, org_id = await _allowed_org()
    token = company_token(user_id, org_id, role="member")

    response = await me_client.post(
        "/me/connectors", json=_payload(accepted=False), headers=bearer(token)
    )

    assert response.status_code == 400
    connections = await _rows(db_session, ConnectorConnection, org_id)
    consents = await _rows(db_session, ConnectorConsent, org_id)
    assert connections == [] and consents == []  # neither the connection nor a consent row


async def test_self_connect_records_an_in_force_consent_row(
    me_client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id, org_id = await _allowed_org()
    token = company_token(user_id, org_id, role="member")

    response = await me_client.post("/me/connectors", json=_payload(), headers=bearer(token))

    assert response.status_code == 201
    consents = await _rows(db_session, ConnectorConsent, org_id)
    assert len(consents) == 1
    consent = consents[0]
    assert consent.user_id == user_id
    assert consent.withdrawn_at is None  # in force
    assert consent.ui_proof == {"consent_version": "v1", "accepted": True}
