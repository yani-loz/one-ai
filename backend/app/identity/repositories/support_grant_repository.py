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
  - Both lookups SELECT ... FOR UPDATE: they are the transition loaders (approve/deny/revoke),
    so locking the row serializes concurrent transitions on the same grant. Under READ
    COMMITTED a second transition blocks on the lock, then re-reads the just-committed status
    and its guard correctly rejects the now-illegal move (e.g. a revoke can't be silently
    overwritten back to approved). Mirrors the DYN-01 last-admin lock. The LIST reads
    (list_for_org / list_for_requester) stay non-locking — only transitions need the lock.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
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
        """Load + LOCK a grant by id iff the given platform admin requested it, else None.

        FOR UPDATE: this is the platform transition loader (revoke), so the row is locked to
        serialize concurrent transitions (a re-read after the lock sees the committed status).
        """
        result = await self._session.execute(
            select(SupportGrant)
            .where(
                SupportGrant.id == grant_id,
                SupportGrant.requested_by_admin_id == requested_by_admin_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_in_org(self, grant_id: UUID, org_id: UUID) -> SupportGrant | None:
        """Load + LOCK a grant by id iff it targets `org_id` (the company boundary), else None.

        FOR UPDATE: this is the company transition loader (approve/deny/revoke), so the row is
        locked to serialize concurrent transitions on the same grant.
        """
        result = await self._session.execute(
            select(SupportGrant)
            .where(SupportGrant.id == grant_id, SupportGrant.org_id == org_id)
            .with_for_update()
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

    async def scrub_decider_emails(self, org_id: UUID) -> int:
        """Null `decided_by_email` on all of `org_id`'s grants (erasure); return rows scrubbed.

        decided_by_email is a tenant data subject (the approving company_admin), so erasure
        removes it. requested_by_email (the PLATFORM admin = Ethera staff) is NOT tenant data
        and is kept. The grant rows are retained as the pseudonymized access record.
        """
        result = await self._session.execute(
            update(SupportGrant)
            .where(SupportGrant.org_id == org_id, SupportGrant.decided_by_email.is_not(None))
            .values(decided_by_email=None)
        )
        return result.rowcount or 0
