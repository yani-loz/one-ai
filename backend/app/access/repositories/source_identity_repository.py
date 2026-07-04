"""
Role: Data access for principal_source_identity (no business decisions — rule A5). The
      verified-identity lookups the grant writer and the login→person seam build on.
Used by: app.access.services.grant_writer (batch address resolution), app.access.dependencies
      (the auth-binding → person GUC resolution), tests.
Depends on: app.access.models.principal_source_identity, SQLAlchemy async.
Key invariants:
  - EVERY read is org-scoped (org_id filter) on the caller's session.
  - `get_verified_person_id(s)` return ONLY rows with verified=true — the UNVERIFIED ⇒ NO GRANT
    rule (AC8) is structurally unreachable from here because unverified rows never leave this
    layer as principals.
  - The caller owns the transaction; methods only add/flush.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.models.principal_source_identity import PrincipalSourceIdentity


class SourceIdentityRepository:
    """Org-scoped persistence for verified external-identity → person mappings."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant-scoped session."""
        self._session = session

    async def add(self, identity: PrincipalSourceIdentity) -> PrincipalSourceIdentity:
        """Stage a new identity mapping and flush (caller sets all fields incl. verified)."""
        self._session.add(identity)
        await self._session.flush()
        return identity

    async def get_verified_person_id(
        self, org_id: UUID, source_type: str, external_id: str
    ) -> UUID | None:
        """The person behind one VERIFIED external identity, or None (unverified ⇒ None too)."""
        result = await self._session.execute(
            select(PrincipalSourceIdentity.person_id).where(
                PrincipalSourceIdentity.org_id == org_id,
                PrincipalSourceIdentity.source_type == source_type,
                PrincipalSourceIdentity.external_id == external_id,
                PrincipalSourceIdentity.verified.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_verified_person_ids(
        self, org_id: UUID, source_type: str, external_ids: Iterable[str]
    ) -> dict[str, UUID]:
        """Batch variant: external_id → person_id for the VERIFIED subset (missing = denied)."""
        ids = list(external_ids)
        if not ids:
            return {}
        result = await self._session.execute(
            select(PrincipalSourceIdentity.external_id, PrincipalSourceIdentity.person_id).where(
                PrincipalSourceIdentity.org_id == org_id,
                PrincipalSourceIdentity.source_type == source_type,
                PrincipalSourceIdentity.external_id.in_(ids),
                PrincipalSourceIdentity.verified.is_(True),
            )
        )
        return {external_id: person_id for external_id, person_id in result.all()}
