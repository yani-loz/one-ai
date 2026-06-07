"""
Role: Data access for the connector_connection table (no business decisions — rule A5).
Used by: ConnectorService (constructed on the caller's tenant-scoped session).
Depends on: app.connectors.models.connector_connection, SQLAlchemy async.
Key invariants:
  - Pure persistence: insert / scoped read / delete only. The service owns all decisions
    (encryption, verification, status transitions); no status logic here.
  - EVERY read is org-scoped: get_in_org / list_for_org / exists all filter by org_id, so a
    caller can only ever load their own org's connections (cross-org -> None -> 404 in the
    service). This is the app-layer half of tenant isolation (RLS is the inert backstop today).
  - The caller owns the transaction (get_tenant_session is the unit-of-work boundary); methods
    only add/flush/delete, never commit.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.models.connector_connection import ConnectorConnection


class ConnectorConnectionRepository:
    """Persistence operations for connector connections, always org-scoped."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the caller's tenant-scoped session."""
        self._session = session

    async def insert(self, connection: ConnectorConnection) -> ConnectorConnection:
        """Stage a new connection for insert and flush to populate server defaults/id."""
        self._session.add(connection)
        await self._session.flush()
        return connection

    async def get_in_org(self, connection_id: UUID, org_id: UUID) -> ConnectorConnection | None:
        """Load a connection by id iff it belongs to `org_id`, else None (the org boundary)."""
        result = await self._session.execute(
            select(ConnectorConnection).where(
                ConnectorConnection.id == connection_id,
                ConnectorConnection.org_id == org_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_org(self, org_id: UUID) -> list[ConnectorConnection]:
        """Return all of one org's connections, newest-first."""
        result = await self._session.execute(
            select(ConnectorConnection)
            .where(ConnectorConnection.org_id == org_id)
            .order_by(ConnectorConnection.created_at.desc(), ConnectorConnection.id.desc())
        )
        return list(result.scalars().all())

    async def exists(self, org_id: UUID, connector_type: str, username: str) -> bool:
        """Return True iff this org already has a connection for (connector_type, username)."""
        result = await self._session.execute(
            select(ConnectorConnection.id).where(
                ConnectorConnection.org_id == org_id,
                ConnectorConnection.connector_type == connector_type,
                ConnectorConnection.username == username,
            )
        )
        return result.first() is not None

    async def delete(self, connection: ConnectorConnection) -> None:
        """Delete a loaded connection row (org scope already verified by the caller)."""
        await self._session.execute(
            sql_delete(ConnectorConnection).where(
                ConnectorConnection.id == connection.id,
                ConnectorConnection.org_id == connection.org_id,
            )
        )
