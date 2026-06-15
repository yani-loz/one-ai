"""
Role: Data access for connector_entitlement (CO-01 Tier 1) — no business decisions (rule A5).
Used by: ConnectorEntitlementService (platform admin writes) + the permission resolver / governance
         service (reads the ceiling). Constructed on the GLOBAL session — this is the platform plane
         (the tenant role holds no privilege on the table).
Depends on: app.connectors.models.connector_entitlement, SQLAlchemy async.
Key invariants:
  - GLOBAL-ENGINE ONLY: every method runs on the BYPASSRLS global session (get_session). There is
    no per-tenant RLS on this table, so reads are filtered by org_id in code here.
  - Pure persistence: get / list / upsert by (org_id, connector_type). The service owns the
    grant/revoke decision + audit; this only reads and writes the row.
  - The caller owns the transaction (commits in the route's unit-of-work); methods flush only.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.models.connector_entitlement import ConnectorEntitlement


class ConnectorEntitlementRepository:
    """Persistence for company connector entitlements (platform plane, global session)."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the GLOBAL session (platform plane — never the tenant engine)."""
        self._session = session

    async def get(self, org_id: UUID, connector_type: str) -> ConnectorEntitlement | None:
        """Load the entitlement row for (org_id, connector_type), or None if never set."""
        result = await self._session.execute(
            select(ConnectorEntitlement).where(
                ConnectorEntitlement.org_id == org_id,
                ConnectorEntitlement.connector_type == connector_type,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_org(self, org_id: UUID) -> list[ConnectorEntitlement]:
        """Return all entitlement rows for one company (one per connector type)."""
        result = await self._session.execute(
            select(ConnectorEntitlement)
            .where(ConnectorEntitlement.org_id == org_id)
            .order_by(ConnectorEntitlement.connector_type)
        )
        return list(result.scalars().all())

    async def is_entitled(self, org_id: UUID, connector_type: str) -> bool:
        """True iff the company has an ENABLED entitlement row for the type (the Tier 1 ceiling)."""
        row = await self.get(org_id, connector_type)
        return row is not None and row.enabled

    async def upsert(
        self,
        *,
        org_id: UUID,
        connector_type: str,
        enabled: bool,
        set_by_platform_admin_id: UUID | None,
    ) -> ConnectorEntitlement:
        """Insert or update the entitlement row for (org_id, connector_type); flush and return it.

        Grant sets enabled=True + clears revoked_at; revoke sets enabled=False + stamps revoked_at
        (the service decides which — this only persists). Existing policies/connections are left
        untouched (no cascade) so a re-grant re-exposes them.
        """
        row = await self.get(org_id, connector_type)
        if row is None:
            row = ConnectorEntitlement(org_id=org_id, connector_type=connector_type)
            self._session.add(row)
        row.enabled = enabled
        row.set_by_platform_admin_id = set_by_platform_admin_id
        if enabled:
            row.granted_at = func.now()  # type: ignore[assignment]  # SQL expr resolved on flush
            row.revoked_at = None
        else:
            row.revoked_at = func.now()  # type: ignore[assignment]  # SQL expr resolved on flush
        await self._session.flush()
        # A func.now() assignment leaves granted_at/revoked_at as server-evaluated values that
        # flush marks EXPIRED; refresh them here (still inside the async greenlet) so the caller's
        # response serialization reads concrete datetimes instead of triggering a lazy load outside
        # the greenlet (which raises MissingGreenlet — a 500 on the grant/revoke response).
        await self._session.refresh(row, attribute_names=["granted_at", "revoked_at"])
        return row
