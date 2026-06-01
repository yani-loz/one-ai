"""
Role: Company side of break-glass support access — a company_admin reviews support-access
      requests targeting THEIR org and approves / denies / revokes them. This is the consent
      gate: approval here is the ONLY way a grant becomes active. Holds the company-side
      business logic (rule A5).
Used by: routes.support_routes (company /support-access endpoints); constructed in
         identity.dependencies on the caller's TENANT-scoped session.
Depends on: support-grant + user repositories, AuditService, support_grant_view,
            identity.principal/enums/exceptions/models/schemas.
Key invariants:
  - CROSS-TENANT ISOLATION (the non-negotiable): every read/transition loads the grant via
    get_in_org(grant_id, caller_org_id), so a company_admin can only ever act on their own
    org's grants — another org's grant resolves to SupportGrantNotFoundError (-> 404), never
    a 403/200-empty existence leak.
  - STATE GUARDS: approve/deny require the grant to be `requested`; revoke requires
    `requested` or `approved`. Any illegal transition raises InvalidGrantTransitionError
    (-> 409), so a revoked grant can't be re-approved and a denied one can't be resurrected.
  - On approval the time box is set here (expires_at = now + SUPPORT_ACCESS_WINDOW); expiry
    itself is computed live (support_grant_view), never stored.
  - `decided_by_email` is looked up at write time (durable attribution of WHO consented).
  - Each transition records an audit event on the SAME session (commits with the change);
    `support.approved` carries expires_at in its details so "logged → expire" holds without a
    discrete expiry event.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.identity.enums import AuditActorType, SupportGrantStatus
from app.identity.exceptions import (
    InvalidGrantTransitionError,
    SupportGrantNotFoundError,
)
from app.identity.models.support_grant import SupportGrant
from app.identity.principal import Principal
from app.identity.repositories.support_grant_repository import SupportGrantRepository
from app.identity.repositories.user_repository import UserRepository
from app.identity.schemas.support_schemas import SupportGrantResponse
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService
from app.identity.services.support_grant_view import to_support_response

# How long an approved break-glass grant stays active before it expires.
SUPPORT_ACCESS_WINDOW = timedelta(hours=4)

_ENTITY_SUPPORT_GRANT = "support_grant"
_REVOCABLE = {SupportGrantStatus.requested.value, SupportGrantStatus.approved.value}


class CompanySupportService:
    """Company-domain break-glass: review + approve/deny/revoke the org's support grants."""

    def __init__(
        self,
        support_grants: SupportGrantRepository,
        users: UserRepository,
        audit: AuditService,
    ) -> None:
        """Wire the repositories + audit writer (all bound to the caller's tenant session)."""
        self._support_grants = support_grants
        self._users = users
        self._audit = audit

    async def list_for_org(self, org_id: UUID) -> list[SupportGrantResponse]:
        """Return the support grants targeting the caller's org (the inbox), newest-first."""
        grants = await self._support_grants.list_for_org(org_id)
        return [to_support_response(grant) for grant in grants]

    async def approve(
        self, grant_id: UUID, org_id: UUID, actor: Principal
    ) -> SupportGrantResponse:
        """Approve a `requested` grant, opening the time box (the consent action).

        Raises:
            SupportGrantNotFoundError: not this org's grant / unknown (-> 404).
            InvalidGrantTransitionError: the grant is not `requested` (-> 409).
        """
        grant = await self._load_requested(grant_id, org_id)
        now = datetime.now(UTC)
        await self._stamp_decision(grant, actor, decided_at=now)
        grant.status = SupportGrantStatus.approved.value
        grant.expires_at = now + SUPPORT_ACCESS_WINDOW
        await self._record(
            AuditAction.SUPPORT_APPROVED,
            grant,
            actor,
            {
                "expires_at": grant.expires_at.isoformat(),
                "window_hours": SUPPORT_ACCESS_WINDOW.total_seconds() / 3600,
            },
        )
        return to_support_response(grant)

    async def deny(
        self, grant_id: UUID, org_id: UUID, actor: Principal
    ) -> SupportGrantResponse:
        """Deny a `requested` grant (terminal).

        Raises:
            SupportGrantNotFoundError / InvalidGrantTransitionError: see approve.
        """
        grant = await self._load_requested(grant_id, org_id)
        await self._stamp_decision(grant, actor, decided_at=datetime.now(UTC))
        grant.status = SupportGrantStatus.denied.value
        await self._record(AuditAction.SUPPORT_DENIED, grant, actor, {})
        return to_support_response(grant)

    async def revoke(
        self, grant_id: UUID, org_id: UUID, actor: Principal
    ) -> SupportGrantResponse:
        """Revoke the org's grant (from `requested` or `approved`) — cuts off active access.

        Raises:
            SupportGrantNotFoundError: not this org's grant / unknown (-> 404).
            InvalidGrantTransitionError: already denied/revoked (-> 409).
        """
        grant = await self._support_grants.get_in_org(grant_id, org_id)
        if grant is None:
            raise SupportGrantNotFoundError("Support grant not found.")
        if grant.status not in _REVOCABLE:
            raise InvalidGrantTransitionError(
                f"Cannot revoke a grant that is {grant.status}."
            )
        grant.status = SupportGrantStatus.revoked.value
        await self._record(AuditAction.SUPPORT_REVOKED, grant, actor, {})
        return to_support_response(grant)

    async def _load_requested(self, grant_id: UUID, org_id: UUID) -> SupportGrant:
        """Load the org's grant and require it to be in `requested` state (else 404/409)."""
        grant = await self._support_grants.get_in_org(grant_id, org_id)
        if grant is None:
            raise SupportGrantNotFoundError("Support grant not found.")
        if grant.status != SupportGrantStatus.requested.value:
            raise InvalidGrantTransitionError(
                f"Cannot decide a grant that is already {grant.status}."
            )
        return grant

    async def _stamp_decision(
        self, grant: SupportGrant, actor: Principal, *, decided_at: datetime
    ) -> None:
        """Record WHO decided + when (denormalized email for durable attribution)."""
        decider = await self._users.get_by_subject_id(actor.subject_id)
        grant.decided_at = decided_at
        grant.decided_by_user_id = actor.subject_id
        grant.decided_by_email = decider.email if decider is not None else None

    async def _record(
        self,
        action: str,
        grant: SupportGrant,
        actor: Principal,
        details: dict[str, object],
    ) -> None:
        """Append a support audit event (company-user actor) on the same session."""
        await self._audit.record(
            AuditEvent(
                action=action,
                actor_type=AuditActorType.user,
                actor_id=actor.subject_id,
                org_id=grant.org_id,
                entity_type=_ENTITY_SUPPORT_GRANT,
                entity_id=grant.id,
                details=details,
            )
        )
