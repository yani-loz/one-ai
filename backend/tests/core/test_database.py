"""
Integration test for the tenant-scoping seam (security-foundational).
Proves get_tenant_session binds the session to the org via set_config(): the
Postgres `app.current_org_id` setting must echo back the resolved org within the
same transaction — the mechanism RLS policies will rely on. Requires Postgres.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text

from app.core.database import get_tenant_session


async def test_get_tenant_session_scopes_org_via_set_config() -> None:
    org_id = UUID("33333333-3333-3333-3333-333333333333")

    session_gen = get_tenant_session(org_id)
    session = await session_gen.__anext__()
    try:
        result = await session.execute(
            text("SELECT current_setting('app.current_org_id', true)")
        )
        assert result.scalar() == str(org_id)
    finally:
        await session_gen.aclose()


async def test_get_tenant_session_scope_survives_commit() -> None:
    # Regression for the fail-open gap: a mid-request commit ends the transaction,
    # and the tenant GUC must be re-applied to the next one (not reset to empty).
    org_id = UUID("44444444-4444-4444-4444-444444444444")

    session_gen = get_tenant_session(org_id)
    session = await session_gen.__anext__()
    try:
        before = (
            await session.execute(text("SELECT current_setting('app.current_org_id', true)"))
        ).scalar()
        assert before == str(org_id)

        await session.commit()

        after = (
            await session.execute(text("SELECT current_setting('app.current_org_id', true)"))
        ).scalar()
        assert after == str(org_id)
    finally:
        await session_gen.aclose()
