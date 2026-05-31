"""
Role: Data access for the PlatformAdmin table (no business decisions — rule A5).
Used by: PlatformAuthService (platform login lookup).
Depends on: app.identity.models.platform_admin. Operates on a plain (non-tenant)
            session — platform admins have global scope.
Key invariants:
  - Pure persistence. Platform admins are not org-scoped, so there is no org filter
    here by design.
  - email is globally unique; get_by_email resolves a single admin.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models.platform_admin import PlatformAdmin


class PlatformAdminRepository:
    """Persistence operations for platform admins."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a plain (non-tenant) session."""
        self._session = session

    async def get_by_email(self, email: str) -> PlatformAdmin | None:
        """Return the platform admin with `email`, or None. Login lookup only."""
        result = await self._session.execute(
            select(PlatformAdmin).where(PlatformAdmin.email == email)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, admin_id: UUID) -> PlatformAdmin | None:
        """Return the platform admin with `admin_id`, or None if absent."""
        result = await self._session.execute(
            select(PlatformAdmin).where(PlatformAdmin.id == admin_id)
        )
        return result.scalar_one_or_none()
