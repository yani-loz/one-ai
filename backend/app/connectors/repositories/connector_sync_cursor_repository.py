"""
Role: Data access for connector_sync_cursor (no business decisions — rule A5) — load a connection's
      per-folder cursors and UPSERT one folder's cursor (rides the runner's per-batch commit).
Used by: the SyncRunner (constructed on its tenant-scoped session).
Depends on: app.connectors.models.connector_sync_cursor, SQLAlchemy async + postgresql ON CONFLICT.
Key invariants:
  - EVERY read/write is org-scoped (the org boundary + RLS).
  - upsert is an atomic INSERT ... ON CONFLICT (org_id, connection_id, folder) DO UPDATE, so it can
    commit in the SAME transaction as that batch's email rows (the resumability guarantee).
  - The caller owns the transaction; methods only execute/flush, never commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.models.connector_sync_cursor import ConnectorSyncCursor


@dataclass(frozen=True, slots=True)
class FolderSyncState:
    """A folder's loaded cursor state (plain — safe to hold across the runner's commits)."""

    uidvalidity: int | None
    last_seen_uid: int
    failed_uids: list[int]


class ConnectorSyncCursorRepository:
    """Org-scoped persistence for per-folder incremental cursors."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant-scoped session."""
        self._session = session

    async def list_for_connection(
        self, org_id: UUID, connection_id: UUID
    ) -> dict[str, FolderSyncState]:
        """Return {folder: FolderSyncState} for a connection (the resume point per folder)."""
        result = await self._session.execute(
            select(ConnectorSyncCursor).where(
                ConnectorSyncCursor.org_id == org_id,
                ConnectorSyncCursor.connection_id == connection_id,
            )
        )
        return {
            row.folder: FolderSyncState(row.uidvalidity, row.last_seen_uid, list(row.failed_uids))
            for row in result.scalars().all()
        }

    async def upsert(
        self,
        org_id: UUID,
        connection_id: UUID,
        folder: str,
        uidvalidity: int,
        last_seen_uid: int,
        failed_uids: list[int],
    ) -> None:
        """Insert-or-update one folder's cursor atomically (ON CONFLICT on the identity unique)."""
        statement = insert(ConnectorSyncCursor).values(
            org_id=org_id,
            connection_id=connection_id,
            folder=folder,
            uidvalidity=uidvalidity,
            last_seen_uid=last_seen_uid,
            failed_uids=failed_uids,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_sync_cursor_identity",
            set_={
                "uidvalidity": uidvalidity,
                "last_seen_uid": last_seen_uid,
                "failed_uids": failed_uids,
                "updated_at": func.now(),
            },
        )
        await self._session.execute(statement)
