"""
Role: Data access for connector_sync_run (no business decisions — rule A5) — the per-run ledger:
      open a run, finalize it with counts + outcome, and reconcile a superseded (stale) run.
Used by: SyncService (open + abandon on claim) + the SyncRunner (finalize).
Depends on: app.connectors.models.connector_sync_run, SQLAlchemy async.
Key invariants:
  - EVERY read/write is org-scoped (the org boundary + RLS).
  - mark_stale_running_abandoned stamps any leftover 'running' ledger row for this connection (which
    can only be a crashed/stale prior run) so the audit never lies after a new run steals the claim.
  - The caller owns the transaction; methods only add/flush/execute, never commit.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.models.connector_sync_run import ConnectorSyncRun


class ConnectorSyncRunRepository:
    """Org-scoped persistence for the sync-run ledger."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant-scoped session."""
        self._session = session

    async def start_run(self, org_id: UUID, connection_id: UUID, run_id: UUID) -> ConnectorSyncRun:
        """Open a 'running' ledger row for a new run and flush to populate its id."""
        run = ConnectorSyncRun(
            org_id=org_id, connection_id=connection_id, run_id=run_id, status="running"
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def finalize(
        self,
        org_id: UUID,
        run_id: UUID,
        *,
        status: str,
        seen: int,
        stored: int,
        skipped: int,
        failed: int,
        error: str | None,
    ) -> None:
        """Close the run's ledger row with its outcome + counts (status 'succeeded'|'failed')."""
        await self._session.execute(
            update(ConnectorSyncRun)
            .where(ConnectorSyncRun.org_id == org_id, ConnectorSyncRun.run_id == run_id)
            .values(
                status=status,
                finished_at=func.now(),
                messages_seen=seen,
                messages_stored=stored,
                messages_skipped=skipped,
                messages_failed=failed,
                last_error=error,
            )
        )

    async def mark_stale_running_abandoned(
        self, org_id: UUID, connection_id: UUID, keep_run_id: UUID
    ) -> None:
        """Stamp any OTHER still-'running' run for this connection 'abandoned' (a stolen claim)."""
        await self._session.execute(
            update(ConnectorSyncRun)
            .where(
                ConnectorSyncRun.org_id == org_id,
                ConnectorSyncRun.connection_id == connection_id,
                ConnectorSyncRun.status == "running",
                ConnectorSyncRun.run_id != keep_run_id,
            )
            .values(status="abandoned", finished_at=func.now())
        )

    async def list_for_connection(
        self, org_id: UUID, connection_id: UUID, limit: int = 20
    ) -> list[ConnectorSyncRun]:
        """Return a connection's recent runs, newest-first (run history)."""
        result = await self._session.execute(
            select(ConnectorSyncRun)
            .where(
                ConnectorSyncRun.org_id == org_id,
                ConnectorSyncRun.connection_id == connection_id,
            )
            .order_by(ConnectorSyncRun.started_at.desc(), ConnectorSyncRun.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
