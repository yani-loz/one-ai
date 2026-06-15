"""
Role: Service-layer tests for ConnectorConsentService — record + withdraw, the withdraw
      IDEMPOTENCY (a second withdraw is a silent no-op, not a duplicate audit row), and the
      retain-as-proof rule (the consent row survives withdrawal). Focused unit tests against real
      repos + DB (cheaper than a route round-trip for the consent-lifecycle invariants).
Used by: pytest (tests/connectors/services).
Depends on: the connectors conftest (connector_schema + db_session), tests.connectors.co01_seed
            (seed_user for the consent composite FK), the consent service + real consent repo +
            audit, the ConnectorConsent model, enums (AuthMethod), Principal.
Key invariants tested:
  - record() inserts an in-force consent (withdrawn_at NULL) + one connector.consented audit row.
  - withdraw() marks the row withdrawn (retained) + one connector.consent_withdrawn row; a SECOND
    withdraw() writes NO further audit row (idempotent — nothing was in force).
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.enums import AuthMethod
from app.connectors.models.connector_consent import ConnectorConsent
from app.connectors.repositories.connector_consent_repository import ConnectorConsentRepository
from app.connectors.services.connector_consent_service import ConnectorConsentService
from app.identity.models.audit_log import AuditLog
from app.identity.principal import Principal
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.services.audit_service import AuditService
from tests.connectors.co01_seed import seed_user


def _service(session: AsyncSession) -> ConnectorConsentService:
    """Build the consent service with the real repo + audit on one test session."""
    return ConnectorConsentService(
        ConnectorConsentRepository(session), AuditService(AuditRepository(session))
    )


def _user(org_id: UUID, user_id: UUID) -> Principal:
    return Principal(subject_id=user_id, org_id=org_id, role="member", subject_type="user")


async def _count_action(session: AsyncSession, org_id: UUID, action: str) -> int:
    """Count audit rows for (org_id, action)."""
    return (
        await session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.org_id == org_id, AuditLog.action == action)
        )
    ).scalar_one()


async def test_record_inserts_in_force_consent_and_audit_row(db_session: AsyncSession) -> None:
    org_id = uuid4()
    user_id = await seed_user(org_id, role="member")
    service = _service(db_session)

    await service.record(
        org_id=org_id,
        connector_type="imap",
        scope="mailbox:read",
        method=AuthMethod.app_password.value,
        consent_version="v1",
        actor=_user(org_id, user_id),
    )
    await db_session.flush()

    consent = (
        await db_session.execute(select(ConnectorConsent).where(ConnectorConsent.org_id == org_id))
    ).scalar_one()
    assert consent.withdrawn_at is None  # in force
    assert await _count_action(db_session, org_id, "connector.consented") == 1


async def test_second_withdraw_is_idempotent_no_duplicate_audit(db_session: AsyncSession) -> None:
    org_id = uuid4()
    user_id = await seed_user(org_id, role="member")
    actor = _user(org_id, user_id)
    service = _service(db_session)
    await service.record(
        org_id=org_id,
        connector_type="imap",
        scope="mailbox:read",
        method=AuthMethod.app_password.value,
        consent_version="v1",
        actor=actor,
    )
    await db_session.flush()

    await service.withdraw(org_id=org_id, connector_type="imap", actor=actor)
    await service.withdraw(org_id=org_id, connector_type="imap", actor=actor)
    await db_session.flush()

    consents = (
        (
            await db_session.execute(
                select(ConnectorConsent).where(ConnectorConsent.org_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(consents) == 1  # the row is RETAINED (never deleted on withdraw)
    assert consents[0].withdrawn_at is not None
    # Only the FIRST withdraw (which actually marked a row) wrote an audit row.
    assert await _count_action(db_session, org_id, "connector.consent_withdrawn") == 1
