"""
Role: Shared read-model for support grants — computes the LIVE active flag and builds the
      metadata response. Used by both support services so platform and company views agree.
Used by: services.platform_support_service, services.company_support_service.
Depends on: identity.enums, identity.models.support_grant, identity.schemas.support_schemas.
Key invariants:
  - `grant_is_active` is the SINGLE source of truth for "does this grant confer access":
    status=='approved' AND now < expires_at, computed against the clock — never a stored
    status. Expiry is derivable here, so no `expired` column/sweeper exists.
  - ⚠️ SEAM: when content endpoints land (Connect/Ask/Learn), any support-mediated tenant
    read MUST gate on grant_is_active(...) for a *current, approved, unexpired* grant —
    otherwise break-glass is silently bypassed. Today there are no content endpoints, so an
    active grant unlocks nothing; this function is the hook they must call.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.identity.enums import SupportGrantStatus
from app.identity.models.support_grant import SupportGrant
from app.identity.schemas.support_schemas import SupportGrantResponse


def grant_is_active(grant: SupportGrant, *, now: datetime | None = None) -> bool:
    """Return whether the grant currently confers access (approved AND within its time box)."""
    if grant.status != SupportGrantStatus.approved.value or grant.expires_at is None:
        return False
    moment = now if now is not None else datetime.now(UTC)
    return grant.expires_at > moment


def to_support_response(grant: SupportGrant) -> SupportGrantResponse:
    """Build the metadata response, stamping the live `is_active` flag."""
    return SupportGrantResponse(
        id=grant.id,
        org_id=grant.org_id,
        requested_by_admin_id=grant.requested_by_admin_id,
        requested_by_email=grant.requested_by_email,
        reason=grant.reason,
        status=grant.status,
        is_active=grant_is_active(grant),
        decided_at=grant.decided_at,
        decided_by_email=grant.decided_by_email,
        expires_at=grant.expires_at,
        created_at=grant.created_at,
    )
