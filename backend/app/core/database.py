"""
Role: Async database engine, session factory, and FastAPI session dependencies.
Used by: app.api routes/services, tests; the engine is disposed by app.main lifespan.
Depends on: app.core.config, app.core.tenant.
Key invariants:
  - One async engine per process.
  - get_tenant_session() scopes the DB session to the active org via set_config(),
    the enforcement seam for Postgres Row-Level Security once policies exist.
  - expire_on_commit=False so ORM objects remain usable after commit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.tenant import resolve_org_id, set_current_org

_settings = get_settings()

engine = create_async_engine(_settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a plain async session (no tenant scope) — for non-tenant operations
    such as health checks."""
    async with SessionLocal() as session:
        yield session


async def get_tenant_session(
    org_id: UUID = Depends(resolve_org_id),
) -> AsyncIterator[AsyncSession]:
    """Yield a tenant-scoped async session bound to the resolved org_id.

    The `app.current_org_id` Postgres GUC is (re)applied at the start of EVERY
    transaction the session opens (see _bind_tenant_scope), so RLS policies — added
    with the first org_id table — filter every row to the tenant, and a mid-request
    commit cannot silently drop the scope. Harmless before policies exist.
    """
    set_current_org(org_id)
    async with SessionLocal() as session:
        _bind_tenant_scope(session, org_id)
        yield session


def _bind_tenant_scope(session: AsyncSession, org_id: UUID) -> None:
    """Re-apply the tenant GUC on every transaction this session opens.

    `set_config(..., is_local=true)` is transaction-scoped — the correct choice for
    a pooled async connection (no cross-request leakage) but it resets on commit.
    Listening on `after_begin` re-applies it to each new transaction, closing the
    fail-open window once RLS is enabled. org_id is a validated UUID, so embedding
    it as a literal is injection-safe.
    """

    @event.listens_for(session.sync_session, "after_begin")
    def _apply_tenant_scope(_session, _transaction, connection) -> None:
        connection.exec_driver_sql(f"SELECT set_config('app.current_org_id', '{org_id}', true)")
