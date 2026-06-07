"""
DB-level tests for the audit_log append-only trigger (PC-04-AC1).

Requires Postgres with the immutability trigger present — created by migration 0005 on a
migrated DB, or by the model's after_create DDL event on the create_all (fresh-DB) test
path. Proves UPDATE and DELETE both RAISE (even via the BYPASSRLS global role) and the row
survives, so no code path can rewrite history. INSERT must still succeed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import GlobalSessionLocal
from app.identity.models.audit_log import AuditLog


async def _insert_selftest_row(session: AsyncSession) -> AuditLog:
    """Insert a minimal system audit row and flush so its id is populated."""
    entry = AuditLog(actor_type="system", action="audit.selftest", details={})
    session.add(entry)
    await session.flush()
    return entry


async def test_audit_log_insert_succeeds(db_session: AsyncSession) -> None:
    entry = await _insert_selftest_row(db_session)
    await db_session.commit()

    async with GlobalSessionLocal() as session:
        count = await session.execute(
            text("SELECT count(*) FROM audit_log WHERE id = :id"), {"id": str(entry.id)}
        )

    assert count.scalar_one() == 1


async def test_audit_log_update_is_blocked_by_trigger(db_session: AsyncSession) -> None:
    entry = await _insert_selftest_row(db_session)
    await db_session.commit()

    with pytest.raises(DBAPIError) as raised:
        async with GlobalSessionLocal() as session:
            await session.execute(
                text("UPDATE audit_log SET action = 'tampered' WHERE id = :id"),
                {"id": str(entry.id)},
            )
            await session.commit()

    assert "append-only" in str(raised.value).lower()


async def test_audit_log_delete_is_blocked_and_row_survives(
    db_session: AsyncSession,
) -> None:
    entry = await _insert_selftest_row(db_session)
    await db_session.commit()

    with pytest.raises(DBAPIError) as raised:
        async with GlobalSessionLocal() as session:
            await session.execute(
                text("DELETE FROM audit_log WHERE id = :id"), {"id": str(entry.id)}
            )
            await session.commit()

    assert "append-only" in str(raised.value).lower()

    async with GlobalSessionLocal() as session:
        count = await session.execute(
            text("SELECT count(*) FROM audit_log WHERE id = :id"), {"id": str(entry.id)}
        )
    assert count.scalar_one() == 1
