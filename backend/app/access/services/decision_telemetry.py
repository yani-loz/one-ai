"""
Role: Retrieval decision telemetry (PF-01 AC16) — records allow/deny counts + resource KEYS
      (never content) into the append-only audit_log, and computes the reduced-coverage
      disclosure flag the answer path must surface when visibility filtering starves retrieval.
Used by: the future retrieval layer (Ask) — the seam ships NOW so the first retrieval tool has
      one obvious call to make; doubles as the staleness/starvation alert rail (PF-FBP-1).
Depends on: app.identity.services.audit_service.
Key invariants:
  - KEYS, NEVER CONTENT: details carry counts and object ids only — no snippet, no subject,
    no address ever lands in audit_log from here.
  - REDUCED COVERAGE is a DISCLOSURE trigger, not a ranking signal: when fewer than
    REDUCED_COVERAGE_THRESHOLD of the candidate rows survived visibility filtering, the answer
    must say its view was limited instead of sounding confidently complete.
"""

from __future__ import annotations

from uuid import UUID

from app.identity.enums import AuditActorType
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService

# Below this surviving fraction of candidates, the answer path must disclose reduced coverage.
REDUCED_COVERAGE_THRESHOLD = 0.5


def is_coverage_reduced(candidate_count: int, allowed_count: int) -> bool:
    """True when visibility filtering starved retrieval below the disclosure threshold.

    Zero candidates is NOT reduced coverage (there was nothing to see); zero allowed out of a
    non-zero candidate set always is.
    """
    if candidate_count <= 0:
        return False
    return (allowed_count / candidate_count) < REDUCED_COVERAGE_THRESHOLD


class DecisionTelemetry:
    """Writes retrieval allow/deny decisions to the audit trail (metadata only)."""

    def __init__(self, audit: AuditService) -> None:
        """Bind the audit writer (on the caller's tenant session)."""
        self._audit = audit

    async def record_retrieval_decision(
        self,
        org_id: UUID,
        user_id: UUID | None,
        person_id: UUID | None,
        candidate_count: int,
        allowed_count: int,
        denied_object_ids: list[UUID],
    ) -> bool:
        """Record one retrieval's decision counts; returns the reduced-coverage flag.

        `denied_object_ids` are resource keys for the works-council trail — capped by the
        caller; never content. ID SPACES ARE KEPT SEPARATE (2026-07-04 review M4): actor_id is
        the USERS-table id like every other audit writer (so rows resolve against users), and
        the retrieval person (the grant principal) travels in details. user_id None = a
        person-less/system read, logged as such.
        """
        reduced = is_coverage_reduced(candidate_count, allowed_count)
        await self._audit.record(
            AuditEvent(
                action=AuditAction.ACCESS_RETRIEVAL_DECISION,
                actor_type=AuditActorType.user if user_id else AuditActorType.system,
                actor_id=user_id,
                org_id=org_id,
                entity_type="retrieval",
                details={
                    "person_id": str(person_id) if person_id else None,
                    "candidate_count": candidate_count,
                    "allowed_count": allowed_count,
                    "denied_count": candidate_count - allowed_count,
                    "denied_object_ids": [str(object_id) for object_id in denied_object_ids],
                    "reduced_coverage": reduced,
                },
            )
        )
        return reduced
