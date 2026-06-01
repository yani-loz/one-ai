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

from app.identity.enums import OrganizationStatus
from app.identity.exceptions import OrganizationNotFoundError
from app.identity.models.organization import Organization
from app.identity.repositories.organization_repository import OrganizationRepository
from app.identity.schemas.platform_schemas import OrganizationDetailResponse


class PlatformOrgService:
    """Per-organization lifecycle administration (platform domain, metadata only)."""

    def __init__(self, organizations: OrganizationRepository) -> None:
        """Wire the organization repository (bound to a plain platform session)."""
        self._organizations = organizations

    async def get_detail(self, org_id: UUID) -> OrganizationDetailResponse:
        """Return one org's lifecycle detail (metadata + legal hold).

        Raises:
            OrganizationNotFoundError: no org with `org_id` (-> 404).
        """
        organization, user_count = await self._load(org_id)
        return self._to_detail(organization, user_count)

    async def set_status(
        self, org_id: UUID, status: OrganizationStatus
    ) -> OrganizationDetailResponse:
        """Set an org's lifecycle status (e.g. suspend/reactivate); return the new detail.

        Contract: a 'suspended' org's company logins + refreshes are blocked by the auth
        service; this only flips the status (the caller's session commits the change).

        Raises:
            OrganizationNotFoundError: no org with `org_id` (-> 404).
        """
        organization, user_count = await self._load(org_id)
        organization.status = status.value
        return self._to_detail(organization, user_count)

    async def set_legal_hold(
        self, org_id: UUID, legal_hold: bool
    ) -> OrganizationDetailResponse:
        """Set or clear an org's legal hold; return the new detail.

        Raises:
            OrganizationNotFoundError: no org with `org_id` (-> 404).
        """
        organization, user_count = await self._load(org_id)
        organization.legal_hold = legal_hold
        return self._to_detail(organization, user_count)

    async def _load(self, org_id: UUID) -> tuple[Organization, int]:
        """Load the org + its user count, or raise OrganizationNotFoundError (-> 404)."""
        row = await self._organizations.get_with_user_count(org_id)
        if row is None:
            raise OrganizationNotFoundError("Organization not found.")
        return row

    @staticmethod
    def _to_detail(
        organization: Organization, user_count: int
    ) -> OrganizationDetailResponse:
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
