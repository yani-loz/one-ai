"""
Role: Data access for the audit_log table (no business decisions — rule A5).
Used by: AuditService (append on the caller's session; reads for the platform endpoints).
Depends on: app.identity.models.audit_log. Operates on a session passed in by the caller.
Key invariants:
  - Pure persistence: insert + ordered reads only; callers own the transaction (an
    append on the request session commits with the action; an independent append commits
    its own session). No UPDATE/DELETE methods exist — the table is append-only.
  - Reads are NEWEST-FIRST (occurred_at desc, id desc tiebreaker) and paginated; they
    return metadata-only rows (the model carries no tenant content).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models.audit_log import AuditLog


class AuditRepository:
    """Persistence operations for the append-only audit log."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session (the caller owns the transaction)."""
        self._session = session

    async def insert(self, entry: AuditLog) -> AuditLog:
        """Stage an audit row for insert and flush (no commit — the caller commits).

        Returns the flushed row so callers needing the server-generated id (the PF-01
        visibility_promotion lineage anchor) can read it in the same transaction.
        """
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list_for_org(
        self, org_id: UUID, *, limit: int, offset: int
    ) -> list[AuditLog]:
        """Return one org's audit rows, newest-first, paginated."""
        result = await self._session.execute(
            select(AuditLog)
            .where(AuditLog.org_id == org_id)
            .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def list_global(
        self,
        *,
        action: str | None,
        org_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[AuditLog]:
        """Return audit rows across all orgs, optionally filtered, newest-first, paginated."""
        statement = select(AuditLog)
        if action is not None:
            statement = statement.where(AuditLog.action == action)
        if org_id is not None:
            statement = statement.where(AuditLog.org_id == org_id)
        statement = (
            statement.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())
