"""
Role: Data access for the support_grant table (no business decisions — rule A5).
Used by: PlatformSupportService (requester-scoped reads) and CompanySupportService
         (org-scoped reads). Operates on a session passed in by the caller.
Depends on: app.identity.models.support_grant.
Key invariants:
  - Pure persistence: insert + scoped reads only; callers own the transaction + the
    state-transition logic. No status guards here.
  - The two lookups encode the access boundary: get_in_org filters by org_id (a company
    admin can only ever load THEIR org's grant — cross-org → None → 404), get_for_requester
    filters by the platform admin's own id (an admin only loads grants they requested).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models.support_grant import SupportGrant


class SupportGrantRepository:
    """Persistence operations for break-glass support grants."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session (plain for platform / tenant for company)."""
        self._session = session

    async def insert(self, grant: SupportGrant) -> SupportGrant:
        """Stage a new grant for insert and flush to populate server defaults/id."""
        self._session.add(grant)
        await self._session.flush()
        return grant

    async def get_for_requester(
        self, grant_id: UUID, requested_by_admin_id: UUID
    ) -> SupportGrant | None:
        """Load a grant by id ONLY if the given platform admin requested it, else None."""
        result = await self._session.execute(
            select(SupportGrant).where(
                SupportGrant.id == grant_id,
                SupportGrant.requested_by_admin_id == requested_by_admin_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_in_org(self, grant_id: UUID, org_id: UUID) -> SupportGrant | None:
        """Load a grant by id ONLY if it targets `org_id` (the company boundary), else None."""
        result = await self._session.execute(
            select(SupportGrant).where(
                SupportGrant.id == grant_id, SupportGrant.org_id == org_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_requester(self, requested_by_admin_id: UUID) -> list[SupportGrant]:
        """Return the grants a platform admin requested, newest-first."""
        result = await self._session.execute(
            select(SupportGrant)
            .where(SupportGrant.requested_by_admin_id == requested_by_admin_id)
            .order_by(SupportGrant.created_at.desc(), SupportGrant.id.desc())
        )
        return list(result.scalars().all())

    async def list_for_org(self, org_id: UUID) -> list[SupportGrant]:
        """Return all grants targeting one org (the company approval inbox), newest-first."""
        result = await self._session.execute(
            select(SupportGrant)
            .where(SupportGrant.org_id == org_id)
            .order_by(SupportGrant.created_at.desc(), SupportGrant.id.desc())
        )
        return list(result.scalars().all())
