"""
Role: Executable seal for the AC18 engine seam (ledger V5). The seam must refuse a connection
      whose role can IGNORE row-level security, not merely one whose name is unexpected —
      because the role name comes from configuration, and pointing READER_DB_USER at a
      BYPASSRLS role would satisfy a name check while every policy silently stopped applying.
Used by: pytest (tests/ask/tools) and scripts/ask_loop/seal_check via the ledger.
Depends on: app.core.database (_bind_scope, GlobalSessionLocal), app.core.config. Needs a live
      database: role attributes are a server-side fact, so a mocked session proves nothing.
Key invariants:
  - `oneai_global` is created WITH BYPASSRLS (migration 0009) — it is the real, already-present
    role this test uses, so the check is proven against production role attributes rather than
    a fixture invented for the occasion.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import (
    EngineSeamViolationError,
    GlobalSessionLocal,
    _bind_scope,
    runtime_roles_present,
)


@pytest.fixture(autouse=True)
async def _require_roles() -> None:
    """Skip loudly rather than pass vacuously when the runtime roles are absent."""
    if not await runtime_roles_present():
        pytest.skip(
            "Runtime DB roles missing — run `alembic upgrade head` then "
            "`python -m scripts.provision_roles` before the DB suite."
        )


async def test_a_bypassrls_role_is_refused_even_when_its_name_is_expected() -> None:
    # oneai_global holds BYPASSRLS. Bind a scope to it while DECLARING that exact role as the
    # expected one, so the name check passes and only the attribute check can object. Before
    # the attribute check existed, this session would have run every query with RLS inert.
    settings = get_settings()

    async with GlobalSessionLocal() as session:
        _bind_scope(session, uuid4(), None, settings.global_db_user)

        with pytest.raises(EngineSeamViolationError, match="BYPASSRLS"):
            await session.execute(text("SELECT 1"))


async def test_a_wrong_role_name_is_still_refused() -> None:
    # The original name check must keep working — the attribute check is an addition, not a
    # replacement.
    async with GlobalSessionLocal() as session:
        _bind_scope(session, uuid4(), None, "some_other_role")

        with pytest.raises(EngineSeamViolationError):
            await session.execute(text("SELECT 1"))
