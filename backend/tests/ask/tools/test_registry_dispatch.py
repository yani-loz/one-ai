"""
Role: LIVE session tests for ToolRegistry.dispatch's failure containment — a tool whose SQL
      errors server-side must not abort the run's transaction. Without the SAVEPOINT the
      whole session enters SQLSTATE 25P02 and EVERY later tool call fails with
      InFailedSQLTransactionError, while the model is told the error is "repairable"
      (observed cascading across real eval transcripts before the fix).
Used by: pytest (tests/ask/tools). Needs a live reader plane — the failure only exists at the
      database level, so a mocked session would prove nothing.
Depends on: app.ask.tools.registry, app.core.database.reader_session, tests/ask/conftest.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.exceptions import ToolExecutionError
from app.ask.tools.registry import ToolRegistry, ToolSpec
from app.core.database import reader_session

pytestmark = pytest.mark.usefixtures("ask_schema")


async def _broken_sql_tool(session: AsyncSession, args: dict[str, object]) -> object:
    """A tool whose generated SQL names a column that does not exist (the real 42703 case)."""
    result = await session.execute(text("SELECT no_such_column FROM email_message"))
    return [dict(r) for r in result.mappings()]


async def _healthy_tool(session: AsyncSession, args: dict[str, object]) -> object:
    """A tool that runs one trivially valid statement."""
    return {"one": (await session.execute(text("SELECT 1 AS one"))).scalar_one()}


def _registry() -> ToolRegistry:
    """Two executors: one that fails inside the database, one that must keep working."""
    empty_schema: dict[str, object] = {"type": "object", "properties": {}}
    return ToolRegistry(
        [
            ToolSpec("broken_sql", "fails server-side", empty_schema, _broken_sql_tool),
            ToolSpec("healthy", "trivial select", empty_schema, _healthy_tool),
        ]
    )


async def test_failed_sql_does_not_poison_later_tool_calls() -> None:
    # Arrange: one reader session, exactly as a whole question's tool loop uses it.
    registry = _registry()
    org = uuid4()

    async with reader_session(org) as session:
        # Act: the failing tool errors, then two more calls run on the SAME session.
        with pytest.raises(ToolExecutionError):
            await registry.dispatch(session, "broken_sql", {})

        first = await registry.dispatch(session, "healthy", {})
        second = await registry.dispatch(session, "healthy", {})

    # Assert: the session survived — before the savepoint both of these raised 25P02.
    assert first == {"one": 1}
    assert second == {"one": 1}


async def test_tenant_scope_survives_the_savepoint_rollback() -> None:
    # A savepoint rollback must not drop the org GUC the RLS policies read: losing it would
    # silently change WHICH ROWS every later tool call can see.
    registry = _registry()
    org = uuid4()

    async with reader_session(org) as session:
        before = (
            await session.execute(text("SELECT current_setting('app.current_org_id', true)"))
        ).scalar_one()

        with pytest.raises(ToolExecutionError):
            await registry.dispatch(session, "broken_sql", {})

        after = (
            await session.execute(text("SELECT current_setting('app.current_org_id', true)"))
        ).scalar_one()

    assert before == str(org)
    assert after == str(org)
