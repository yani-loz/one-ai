"""
Role: Data access for connector_policy (CO-01 Tier 2 org-wide) — no business decisions (A5).
Used by: ConnectorGovernanceService (admin writes) + the permission resolver (reads org-wide).
         Constructed on the caller's TENANT-scoped session (org_isolation RLS is the backstop).
Depends on: app.connectors.models.connector_policy, SQLAlchemy async.
Key invariants:
  - Pure persistence: get / list / upsert by (org_id, connector_type). Every read filters by
    org_id (app-layer tenant isolation; RLS enforces the same).
  - The caller owns the transaction (get_tenant_session unit-of-work); methods flush only.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.models.connector_policy import ConnectorPolicy


class ConnectorPolicyRepository:
    """Persistence for org-wide connector policies, always org-scoped."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant-scoped session."""
        self._session = session

    async def get(self, org_id: UUID, connector_type: str) -> ConnectorPolicy | None:
        """Load the org-wide policy for (org_id, connector_type), or None if never set."""
        result = await self._session.execute(
            select(ConnectorPolicy).where(
                ConnectorPolicy.org_id == org_id,
                ConnectorPolicy.connector_type == connector_type,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_org(self, org_id: UUID) -> list[ConnectorPolicy]:
        """Return all org-wide policies for one company (one per connector type)."""
        result = await self._session.execute(
            select(ConnectorPolicy)
            .where(ConnectorPolicy.org_id == org_id)
            .order_by(ConnectorPolicy.connector_type)
        )
        return list(result.scalars().all())

    async def is_org_wide_enabled(self, org_id: UUID, connector_type: str) -> bool:
        """True iff an org-wide policy row exists AND is enabled (default reach for the type)."""
        row = await self.get(org_id, connector_type)
        return row is not None and row.org_wide_enabled

    async def upsert(
        self,
        *,
        org_id: UUID,
        connector_type: str,
        org_wide_enabled: bool,
        set_by_user_id: UUID | None,
    ) -> ConnectorPolicy:
        """Insert or update the org-wide policy; flush and return it."""
        row = await self.get(org_id, connector_type)
        if row is None:
            row = ConnectorPolicy(org_id=org_id, connector_type=connector_type)
            self._session.add(row)
        row.org_wide_enabled = org_wide_enabled
        row.set_by_user_id = set_by_user_id
        await self._session.flush()
        return row
