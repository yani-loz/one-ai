"""
Role: Platform-domain organization lifecycle service — per-company detail + status +
      legal-hold updates. Separate from PlatformAuthService (which owns auth + onboarding)
      to keep each service single-responsibility (rule A5).
Used by: routes.platform_routes (GET /platform/orgs/{id}, PATCH status / legal-hold);
         constructed in identity.dependencies on a PLAIN (non-tenant) session.
Depends on: organization repository, identity.enums, identity.schemas.platform_schemas,
            identity.exceptions, identity.models.organization.
Key invariants:
  - METADATA ONLY: returns id/name/slug/status/user_count/legal_hold/created_at — never
    tenant content (SPEC §4).
  - Mutations set attributes on the live ORM object; the get_session dependency commits.
  - An unknown org id raises OrganizationNotFoundError (-> 404).
"""

from __future__ import annotations

from uuid import UUID

from app.identity.enums import AuditActorType, OrganizationStatus
from app.identity.exceptions import OrganizationNotFoundError
from app.identity.models.organization import Organization
from app.identity.principal import Principal
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.schemas.platform_schemas import OrganizationDetailResponse
from app.identity.security.token_denylist import TokenDenylist
from app.identity.services.audit_service import AuditAction, AuditEvent, AuditService

_ENTITY_ORGANIZATION = "organization"
# Status value -> the specific audit action it represents (others -> generic change).
_STATUS_ACTION = {
    OrganizationStatus.suspended.value: AuditAction.ORG_SUSPEND,
    OrganizationStatus.active.value: AuditAction.ORG_REACTIVATE,
}


class PlatformOrgService:
    """Per-organization lifecycle administration (platform domain, metadata only)."""

    def __init__(
        self,
        organizations: OrganizationRepository,
        audit: AuditService,
        token_denylist: TokenDenylist,
    ) -> None:
        """Wire the org repository + audit writer (one plain platform session) + denylist.

        token_denylist is the process-wide revocation singleton: suspending an org must
        immediately kill its users' in-flight access tokens, not wait out the <=15min TTL.
        """
        self._organizations = organizations
        self._audit = audit
        self._token_denylist = token_denylist

    async def get_detail(self, org_id: UUID) -> OrganizationDetailResponse:
        """Return one org's lifecycle detail (metadata + legal hold).

        Raises:
            OrganizationNotFoundError: no org with `org_id` (-> 404).
        """
        organization, user_count = await self._load(org_id)
        return self._to_detail(organization, user_count)

    async def set_status(
        self, org_id: UUID, status: OrganizationStatus, actor: Principal
    ) -> OrganizationDetailResponse:
        """Set an org's lifecycle status (e.g. suspend/reactivate); return the new detail.

        Contract: a 'suspended' org's company logins + refreshes are blocked by the auth
        service; this flips the status and records the action on the SAME session, so the
        change and its audit row commit together (the caller's unit-of-work commits both).

        Raises:
            OrganizationNotFoundError: no org with `org_id` (-> 404).
        """
        organization, user_count = await self._load(org_id)
        from_status = organization.status
        organization.status = status.value
        # Suspending an org immediately revokes every user's in-flight access token (the
        # denylist) — otherwise a suspended org's existing tokens reach /users etc. for the
        # remaining <=15min TTL (the gap this control closes). Reactivation does NOT un-revoke
        # (those tokens are already dead); users simply log in afresh.
        if status == OrganizationStatus.suspended:
            self._token_denylist.revoke_org(org_id)
        await self._audit.record(
            AuditEvent(
                action=_STATUS_ACTION.get(status.value, AuditAction.ORG_STATUS_CHANGE),
                actor_type=AuditActorType.platform_admin,
                actor_id=actor.subject_id,
                org_id=org_id,
                entity_type=_ENTITY_ORGANIZATION,
                entity_id=org_id,
                details={"from_status": from_status, "to_status": status.value},
            )
        )
        return self._to_detail(organization, user_count)

    async def set_legal_hold(
        self, org_id: UUID, legal_hold: bool, actor: Principal
    ) -> OrganizationDetailResponse:
        """Set or clear an org's legal hold; return the new detail (audited same-tx).

        Raises:
            OrganizationNotFoundError: no org with `org_id` (-> 404).
        """
        organization, user_count = await self._load(org_id)
        organization.legal_hold = legal_hold
        await self._audit.record(
            AuditEvent(
                action=(
                    AuditAction.ORG_LEGAL_HOLD_SET
                    if legal_hold
                    else AuditAction.ORG_LEGAL_HOLD_CLEAR
                ),
                actor_type=AuditActorType.platform_admin,
                actor_id=actor.subject_id,
                org_id=org_id,
                entity_type=_ENTITY_ORGANIZATION,
                entity_id=org_id,
                details={"legal_hold": legal_hold},
            )
        )
        return self._to_detail(organization, user_count)

    async def _load(self, org_id: UUID) -> tuple[Organization, int]:
        """Load the org + its user count, or raise OrganizationNotFoundError (-> 404)."""
        row = await self._organizations.get_with_user_count(org_id)
        if row is None:
            raise OrganizationNotFoundError("Organization not found.")
        return row

    @staticmethod
    def _to_detail(organization: Organization, user_count: int) -> OrganizationDetailResponse:
        """Assemble the metadata-only detail view from the org + its user count."""
        return OrganizationDetailResponse(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            status=organization.status,
            user_count=user_count,
            legal_hold=organization.legal_hold,
            created_at=organization.created_at,
        )
