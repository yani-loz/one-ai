"""
Integration test for the tenant-scoping seam (security-foundational).

After the identity refactor the tenant DB seam lives in core as the
`scoped_session(org_id)` async context manager (get_tenant_session moved to
app.identity.dependencies and now derives org_id from the verified JWT). These tests
prove scoped_session binds the session to the org via set_config(): the Postgres
`app.current_org_id` setting must echo the org within the transaction and survive a
mid-session commit — the mechanism RLS policies will rely on. Requires Postgres.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text

from app.core.database import scoped_session


async def test_scoped_session_binds_org_via_set_config() -> None:
    org_id = UUID("33333333-3333-3333-3333-333333333333")

    async with scoped_session(org_id) as session:
        result = await session.execute(
            text("SELECT current_setting('app.current_org_id', true)")
        )
        assert result.scalar() == str(org_id)


async def test_scoped_session_scope_survives_commit() -> None:
    # Regression for the fail-open gap: a mid-request commit ends the transaction, and
    # the tenant GUC must be re-applied to the next one (not reset to empty).
    org_id = UUID("44444444-4444-4444-4444-444444444444")

    async with scoped_session(org_id) as session:
        before = (
            await session.execute(text("SELECT current_setting('app.current_org_id', true)"))
        ).scalar()
        assert before == str(org_id)

        await session.commit()

        after = (
            await session.execute(text("SELECT current_setting('app.current_org_id', true)"))
        ).scalar()
        assert after == str(org_id)
