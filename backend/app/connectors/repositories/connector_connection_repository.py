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

from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select, update
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

    async def is_disabled(self, org_id: UUID, connection_id: UUID) -> bool:
        """Return True iff the connection is currently admin-disabled (disabled_at set).

        A fresh per-batch read the SyncRunner uses to stop an in-flight sync when an admin disables
        the connection mid-run — disable is not part of the sync_run_id fence (audit TC-IM-B02).
        """
        result = await self._session.execute(
            select(ConnectorConnection.disabled_at).where(
                ConnectorConnection.id == connection_id,
                ConnectorConnection.org_id == org_id,
            )
        )
        return result.scalar_one_or_none() is not None

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

    # ── SyncRunner state: the atomic single-runner CLAIM + the FENCED progress writes ──
    async def claim_for_sync(
        self, org_id: UUID, connection_id: UUID, run_id: UUID, stale_seconds: int
    ) -> bool:
        """Atomically claim this connection for a sync run; True iff the claim was won.

        A single-row conditional UPDATE (the Postgres row lock serializes concurrent claims, no
        SELECT-then-UPDATE TOCTOU): claimable only if it's the caller's org, NOT disabled, and
        either not 'running' OR its heartbeat is stale (a crashed run is reclaimable). On success
        the row is reset for the new run (run_id is the fencing token). Returns False (→ 409) if a
        fresh run already holds it.
        """
        result = await self._session.execute(
            update(ConnectorConnection)
            .where(
                ConnectorConnection.id == connection_id,
                ConnectorConnection.org_id == org_id,
                ConnectorConnection.disabled_at.is_(None),
                or_(
                    ConnectorConnection.sync_status != "running",
                    ConnectorConnection.sync_heartbeat_at
                    < func.now() - timedelta(seconds=stale_seconds),
                ),
            )
            .values(
                sync_status="running",
                sync_run_id=run_id,
                sync_started_at=func.now(),
                sync_heartbeat_at=func.now(),
                synced_count=0,
                total_count=None,
                last_sync_error=None,
            )
        )
        return result.rowcount == 1

    async def heartbeat(self, org_id: UUID, connection_id: UUID, run_id: UUID) -> bool:
        """FENCED: refresh the heartbeat iff this run still owns the claim. False ⇒ claim stolen."""
        return await self._fenced_update(
            org_id, connection_id, run_id, {"sync_heartbeat_at": func.now()}
        )

    async def set_total(self, org_id: UUID, connection_id: UUID, run_id: UUID, total: int) -> bool:
        """FENCED: set the run's total (the N/M denominator). False ⇒ claim stolen."""
        return await self._fenced_update(org_id, connection_id, run_id, {"total_count": total})

    async def set_synced_count(
        self, org_id: UUID, connection_id: UUID, run_id: UUID, count: int
    ) -> bool:
        """FENCED: update the live processed count. False ⇒ claim stolen (caller aborts).

        Also refreshes sync_heartbeat_at: steady per-batch progress IS proof of liveness, so the
        claim stays fresh even if the heartbeat ticker dies (belt-and-suspenders for staleness).
        """
        return await self._fenced_update(
            org_id, connection_id, run_id, {"synced_count": count, "sync_heartbeat_at": func.now()}
        )

    async def finalize_sync(
        self, org_id: UUID, connection_id: UUID, run_id: UUID, *, status: str, error: str | None
    ) -> bool:
        """FENCED: end the run — status 'idle' (success, stamps last_synced_at) or 'error'."""
        values: dict[str, object] = {
            "sync_status": status,
            "last_sync_error": error,
            "sync_heartbeat_at": func.now(),
        }
        if status == "idle":
            values["last_synced_at"] = func.now()
        return await self._fenced_update(org_id, connection_id, run_id, values)

    async def _fenced_update(
        self, org_id: UUID, connection_id: UUID, run_id: UUID, values: dict[str, object]
    ) -> bool:
        """Apply `values` only while this run still holds the claim (sync_run_id == run_id)."""
        result = await self._session.execute(
            update(ConnectorConnection)
            .where(
                ConnectorConnection.id == connection_id,
                ConnectorConnection.org_id == org_id,
                ConnectorConnection.sync_run_id == run_id,
            )
            .values(**values)
        )
        return result.rowcount > 0
