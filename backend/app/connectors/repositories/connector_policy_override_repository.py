"""
Role: Data access for connector_policy_override (CO-01 Tier 2 per-user) — no decisions (A5).
Used by: ConnectorGovernanceService (admin sets/clears a user's grant/deny) + the permission
         resolver (reads the user's override). Constructed on the caller's TENANT-scoped session.
Depends on: app.connectors.models.connector_policy_override, app.connectors.enums (OverrideType),
            SQLAlchemy async.
Key invariants:
  - Pure persistence: get / list (per org, per user) / upsert / delete by (org_id, user_id,
    connector_type). Every read filters by org_id (tenant isolation; RLS enforces the same).
  - The caller owns the transaction; methods flush/delete only.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.enums import OverrideType
from app.connectors.models.connector_policy_override import ConnectorPolicyOverride


class ConnectorPolicyOverrideRepository:
    """Persistence for per-user connector overrides, always org-scoped."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant-scoped session."""
        self._session = session

    async def get(
        self, org_id: UUID, user_id: UUID, connector_type: str
    ) -> ConnectorPolicyOverride | None:
        """Load a user's override for (org_id, user_id, connector_type), or None if unset."""
        result = await self._session.execute(
            select(ConnectorPolicyOverride).where(
                ConnectorPolicyOverride.org_id == org_id,
                ConnectorPolicyOverride.user_id == user_id,
                ConnectorPolicyOverride.connector_type == connector_type,
            )
        )
        return result.scalar_one_or_none()

    async def get_override_type(
        self, org_id: UUID, user_id: UUID, connector_type: str
    ) -> OverrideType | None:
        """Return the user's OverrideType for the type, or None if no override exists."""
        row = await self.get(org_id, user_id, connector_type)
        return OverrideType(row.override_type) if row is not None else None

    async def list_for_type(
        self, org_id: UUID, connector_type: str
    ) -> list[ConnectorPolicyOverride]:
        """Return every per-user override for one org + type (the governance matrix rows)."""
        result = await self._session.execute(
            select(ConnectorPolicyOverride)
            .where(
                ConnectorPolicyOverride.org_id == org_id,
                ConnectorPolicyOverride.connector_type == connector_type,
            )
            .order_by(ConnectorPolicyOverride.user_id)
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        *,
        org_id: UUID,
        user_id: UUID,
        connector_type: str,
        override_type: OverrideType,
        set_by_user_id: UUID | None,
    ) -> ConnectorPolicyOverride:
        """Insert or update a user's grant/deny override; flush and return it."""
        row = await self.get(org_id, user_id, connector_type)
        if row is None:
            row = ConnectorPolicyOverride(
                org_id=org_id, user_id=user_id, connector_type=connector_type
            )
            self._session.add(row)
        row.override_type = override_type.value
        row.set_by_user_id = set_by_user_id
        await self._session.flush()
        return row

    async def delete(self, org_id: UUID, user_id: UUID, connector_type: str) -> None:
        """Remove a user's override (reverts them to the org-wide policy)."""
        await self._session.execute(
            sql_delete(ConnectorPolicyOverride).where(
                ConnectorPolicyOverride.org_id == org_id,
                ConnectorPolicyOverride.user_id == user_id,
                ConnectorPolicyOverride.connector_type == connector_type,
            )
        )
