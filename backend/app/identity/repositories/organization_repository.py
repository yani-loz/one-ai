"""
Role: Data access for the Organization table (no business decisions — rule A5).
Used by: PlatformAuthService (onboard + list orgs), PlatformOrgService (org detail +
         status/legal-hold lifecycle). Platform domain only.
Depends on: app.identity.models.organization. Operates on a plain (non-tenant)
            session passed in by the caller.
Key invariants:
  - Pure persistence: queries/inserts only; callers commit. No role checks, no
    password logic here.
  - Organization is the tenant root, so these reads are intentionally NOT org-scoped
    (the platform domain spans all orgs). User-table access stays org-scoped elsewhere.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models.organization import Organization
from app.identity.models.user import User


class OrganizationRepository:
    """Persistence operations for organizations."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session (plain/non-tenant for the platform domain)."""
        self._session = session

    async def get_by_id(self, org_id: UUID) -> Organization | None:
        """Return the organization with `org_id`, or None if absent."""
        result = await self._session.execute(
            select(Organization).where(Organization.id == org_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Organization | None:
        """Return the organization with `slug`, or None if absent."""
        result = await self._session.execute(
            select(Organization).where(Organization.slug == slug)
        )
        return result.scalar_one_or_none()

    async def add(self, organization: Organization) -> Organization:
        """Stage a new organization for insert and flush to populate server defaults."""
        self._session.add(organization)
        await self._session.flush()
        return organization

    async def list_all_with_user_counts(self) -> list[tuple[Organization, int]]:
        """Return every organization paired with its active+inactive user count.

        Contract: one row per org as (Organization, user_count). Orgs with zero users
        are included (LEFT OUTER JOIN). Metadata only — no tenant content is read.
        """
        result = await self._session.execute(
            select(Organization, func.count(User.id))
            .outerjoin(User, User.org_id == Organization.id)
            .group_by(Organization.id)
            .order_by(Organization.created_at)
        )
        return [(org, count) for org, count in result.all()]

    async def get_with_user_count(self, org_id: UUID) -> tuple[Organization, int] | None:
        """Return (Organization, user_count) for `org_id`, or None if absent.

        The Organization is a live ORM object (mutable) so callers can update its
        status/legal_hold in place; the user_count is metadata only (no tenant content).
        """
        result = await self._session.execute(
            select(Organization, func.count(User.id))
            .outerjoin(User, User.org_id == Organization.id)
            .where(Organization.id == org_id)
            .group_by(Organization.id)
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row is not None else None
