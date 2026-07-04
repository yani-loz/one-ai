"""
Role: AC16 decision-telemetry tests — allow/deny counts + resource KEYS (never content) land in
      the append-only audit_log, and the reduced-coverage disclosure flag fires when visibility
      filtering starves retrieval.
Used by: pytest (tests/access). Real DB (audit_log) via the access conftest.
Depends on: app.access.services.decision_telemetry, the audit repository (read-back).
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.services.decision_telemetry import DecisionTelemetry, is_coverage_reduced
from app.identity.models.audit_log import AuditLog
from app.identity.repositories.audit_repository import AuditRepository
from app.identity.services.audit_service import AuditService
from tests.conftest import register_org


def test_reduced_coverage_boundaries() -> None:
    assert is_coverage_reduced(candidate_count=10, allowed_count=0) is True
    assert is_coverage_reduced(candidate_count=10, allowed_count=4) is True  # 40% < 50%
    assert is_coverage_reduced(candidate_count=10, allowed_count=5) is False  # exactly 50%
    assert is_coverage_reduced(candidate_count=0, allowed_count=0) is False  # nothing to see


async def test_decisions_are_logged_as_keys_never_content(db_session: AsyncSession) -> None:
    org = uuid4()
    await register_org(db_session, org)
    user_id = uuid4()
    person = uuid4()
    denied_id = uuid4()
    telemetry = DecisionTelemetry(AuditService(AuditRepository(db_session)))

    reduced = await telemetry.record_retrieval_decision(
        org, user_id, person, candidate_count=4, allowed_count=1, denied_object_ids=[denied_id]
    )
    await db_session.commit()

    assert reduced is True
    row = (
        await db_session.execute(
            select(AuditLog)
            .where(AuditLog.org_id == org, AuditLog.action == "access.retrieval_decision")
            .order_by(AuditLog.occurred_at.desc())
            .limit(1)
        )
    ).scalar_one()
    assert row.actor_id == user_id  # the USERS-table id space, like every audit writer (M4)
    assert row.details["person_id"] == str(person)  # the grant principal travels in details
    assert row.details["candidate_count"] == 4
    assert row.details["allowed_count"] == 1
    assert row.details["denied_object_ids"] == [str(denied_id)]
    assert row.details["reduced_coverage"] is True
