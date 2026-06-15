"""
Role: Data access for connector_consent (CO-01 Tier 3 / §8) — no business decisions (A5).
Used by: ConnectorConsentService (capture at self-connect, withdraw on disconnect) + per-user
         erasure. Constructed on the caller's TENANT-scoped session.
Depends on: app.connectors.models.connector_consent, SQLAlchemy async.
Key invariants:
  - Pure persistence: insert / read-active / withdraw-active / list-for-user. Every read filters
    by org_id + user_id (tenant + per-user isolation; RLS enforces the org half).
  - APPEND-then-mark: a consent row is never deleted while in force; withdrawal sets withdrawn_at
    and the row is retained as proof of lawful basis. Per-user ERASURE (offboarding) deletes it.
  - The caller owns the transaction; methods flush/update only.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.models.connector_consent import ConnectorConsent


class ConnectorConsentRepository:
    """Persistence for per-user connector consents, always org + user scoped."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant-scoped session."""
        self._session = session

    async def insert(self, consent: ConnectorConsent) -> ConnectorConsent:
        """Stage a new consent row and flush to populate server defaults/id."""
        self._session.add(consent)
        await self._session.flush()
        return consent

    async def get_active(
        self, org_id: UUID, user_id: UUID, connector_type: str
    ) -> ConnectorConsent | None:
        """Return the user's latest in-force consent (withdrawn_at NULL) for the type, else None."""
        result = await self._session.execute(
            select(ConnectorConsent)
            .where(
                ConnectorConsent.org_id == org_id,
                ConnectorConsent.user_id == user_id,
                ConnectorConsent.connector_type == connector_type,
                ConnectorConsent.withdrawn_at.is_(None),
            )
            .order_by(ConnectorConsent.granted_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def withdraw_active(self, org_id: UUID, user_id: UUID, connector_type: str) -> int:
        """Mark all of a user's in-force consents for the type as withdrawn (Art. 7(4)).

        Returns the number of rows withdrawn (0 if none were active). The rows are retained as
        proof — withdrawal is a mark, not a delete.
        """
        result = await self._session.execute(
            update(ConnectorConsent)
            .where(
                ConnectorConsent.org_id == org_id,
                ConnectorConsent.user_id == user_id,
                ConnectorConsent.connector_type == connector_type,
                ConnectorConsent.withdrawn_at.is_(None),
            )
            .values(withdrawn_at=func.now())
        )
        return result.rowcount
