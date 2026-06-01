"""
Role: Platform side of break-glass support access — a platform admin REQUESTS time-boxed
      access to a tenant, lists their own requests, and can revoke them. Holds the
      platform-side business logic (rule A5).
Used by: routes.support_routes (platform support endpoints); constructed in
         identity.dependencies on a PLAIN session (platform spans all orgs).
Depends on: organization + support-grant + platform-admin repositories, AuditService,
            support_grant_view, identity.principal/enums/exceptions/models/schemas.
Key invariants:
  - CONSENT: this service can NEVER set status='approved'. It only creates `requested`
    grants and `revoked` transitions — approval is exclusively the company side. That is
    the whole point of break-glass (the customer must consent).
  - A platform admin only ever loads/revokes grants they themselves requested
    (get_for_requester) — another admin's grant resolves to 404.
  - `requested_by_email` is looked up at write time so the approving company_admin sees WHO
    is asking (informed consent), and the row stays attributable after the admin is gone.
  - Each transition records an audit event on the SAME session (commits with the change).
"""

from __future__ import annotations

from uuid import UUID

from app.identity.enums import AuditActorType, SupportGrantStatus
from app.identity.exceptions import (
    InvalidGrantTransitionError,
    OrganizationNotFoundError,
    SupportGrantNotFoundError,
)
from app.identity.models.support_grant import SupportGrant
from app.identity.principal import Principal
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.repositories.platform_admin_repository import PlatformAdminRepository
from app.identity.repositories.support_grant_repository import SupportGrantRepository
from app.identity.schemas.support_schemas import SupportGrantResponse
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService
from app.identity.services.support_grant_view import to_support_response

_ENTITY_SUPPORT_GRANT = "support_grant"
# Transitions the requester may revoke from (a denied/revoked grant is terminal).
_REVOCABLE = {SupportGrantStatus.requested.value, SupportGrantStatus.approved.value}


class PlatformSupportService:
    """Platform-domain break-glass: request access, list own requests, revoke."""

    def __init__(
        self,
        organizations: OrganizationRepository,
        support_grants: SupportGrantRepository,
        platform_admins: PlatformAdminRepository,
        audit: AuditService,
    ) -> None:
        """Wire the repositories + audit writer (all bound to one plain platform session)."""
        self._organizations = organizations
        self._support_grants = support_grants
        self._platform_admins = platform_admins
        self._audit = audit

    async def request_access(
        self, org_id: UUID, reason: str, actor: Principal
    ) -> SupportGrantResponse:
        """Open a `requested` break-glass grant for `org_id` (awaits the customer's approval).

        Raises:
            OrganizationNotFoundError: no such org (-> 404).
        """
        if await self._organizations.get_by_id(org_id) is None:
            raise OrganizationNotFoundError("Organization not found.")

        admin = await self._platform_admins.get_by_id(actor.subject_id)
        grant = await self._support_grants.insert(
            SupportGrant(
                org_id=org_id,
                requested_by_admin_id=actor.subject_id,
                requested_by_email=admin.email if admin is not None else None,
                reason=reason,
                status=SupportGrantStatus.requested.value,
            )
        )
        await self._record(AuditAction.SUPPORT_REQUESTED, grant, actor, {"reason": reason})
        return to_support_response(grant)

    async def list_my_requests(self, actor: Principal) -> list[SupportGrantResponse]:
        """Return the grants this platform admin requested, newest-first."""
        grants = await self._support_grants.list_for_requester(actor.subject_id)
        return [to_support_response(grant) for grant in grants]

    async def revoke(self, grant_id: UUID, actor: Principal) -> SupportGrantResponse:
        """Revoke a grant this admin requested (from `requested` or `approved`).

        Raises:
            SupportGrantNotFoundError: not the caller's grant / unknown (-> 404).
            InvalidGrantTransitionError: the grant is already denied/revoked (-> 409).
        """
        grant = await self._support_grants.get_for_requester(grant_id, actor.subject_id)
        if grant is None:
            raise SupportGrantNotFoundError("Support grant not found.")
        if grant.status not in _REVOCABLE:
            raise InvalidGrantTransitionError(
                f"Cannot revoke a grant that is {grant.status}."
            )
        grant.status = SupportGrantStatus.revoked.value
        await self._record(AuditAction.SUPPORT_REVOKED, grant, actor, {})
        return to_support_response(grant)

    async def _record(
        self,
        action: str,
        grant: SupportGrant,
        actor: Principal,
        details: dict[str, object],
    ) -> None:
        """Append a support audit event (platform actor) on the same session."""
        await self._audit.record(
            AuditEvent(
                action=action,
                actor_type=AuditActorType.platform_admin,
                actor_id=actor.subject_id,
                org_id=grant.org_id,
                entity_type=_ENTITY_SUPPORT_GRANT,
                entity_id=grant.id,
                details=details,
            )
        )
