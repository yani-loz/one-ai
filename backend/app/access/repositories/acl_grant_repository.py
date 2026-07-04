"""
Role: Data access for acl_grant (no business decisions — rule A5). Idempotent grant inserts,
      tombstone revocation, and the reconciliation reads the grant writer builds on.
Used by: app.access.services.grant_writer; tests.
Depends on: app.access.models.acl_grant, SQLAlchemy async + the postgresql insert dialect.
Key invariants:
  - EVERY statement is org-scoped on the caller's session.
  - `insert_live_grant` is idempotent via ON CONFLICT DO NOTHING on the partial live-grant
    unique index — re-ingesting a message never duplicates or errors, and works regardless of
    row visibility (no SELECT needed).
  - Revocation is UPDATE revoked_at (tombstone) — never DELETE (AC14: the trail survives until
    erasure; the visibility policy stops matching immediately).
  - The caller owns the transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.models.acl_grant import AclGrant


class AclGrantRepository:
    """Org-scoped persistence for retrieval grants (idempotent insert + tombstone revoke)."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant-scoped session."""
        self._session = session

    async def insert_live_grant(
        self,
        org_id: UUID,
        person_id: UUID,
        object_type: str,
        object_id: UUID,
        provenance: str,
        connection_id: UUID | None,
    ) -> None:
        """Idempotently ensure a LIVE grant row (ON CONFLICT on the live partial unique)."""
        # ON CONFLICT DO NOTHING without a target arbitrates on ANY unique violation — here that
        # is exactly the live partial unique (org, object_type, object_id, person) WHERE
        # revoked_at IS NULL, so a re-grant of a live grant is a clean no-op.
        statement = (
            pg_insert(AclGrant)
            .values(
                org_id=org_id,
                person_id=person_id,
                object_type=object_type,
                object_id=object_id,
                provenance=provenance,
                connection_id=connection_id,
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(statement)

    async def list_live_person_ids_for_object(
        self, org_id: UUID, object_type: str, object_id: UUID
    ) -> set[UUID]:
        """The principals currently holding a live grant on one object (reconciliation read)."""
        result = await self._session.execute(
            select(AclGrant.person_id).where(
                AclGrant.org_id == org_id,
                AclGrant.object_type == object_type,
                AclGrant.object_id == object_id,
                AclGrant.revoked_at.is_(None),
            )
        )
        return set(result.scalars().all())

    async def tombstone_grants(
        self,
        org_id: UUID,
        object_type: str,
        object_id: UUID,
        person_ids: set[UUID],
    ) -> int:
        """Revoke (tombstone) the live grants of `person_ids` on one object; returns count."""
        if not person_ids:
            return 0
        result = await self._session.execute(
            update(AclGrant)
            .where(
                AclGrant.org_id == org_id,
                AclGrant.object_type == object_type,
                AclGrant.object_id == object_id,
                AclGrant.person_id.in_(person_ids),
                AclGrant.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        return result.rowcount or 0

    async def tombstone_grants_for_person(self, org_id: UUID, person_id: UUID) -> int:
        """Revoke ALL of one principal's live grants (binding unverified/removed); returns count."""
        result = await self._session.execute(
            update(AclGrant)
            .where(
                AclGrant.org_id == org_id,
                AclGrant.person_id == person_id,
                AclGrant.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        return result.rowcount or 0
