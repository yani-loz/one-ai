"""
Role: Async database engine, session factory, and session helpers (plain + tenant-scoped).
Used by: app.api routes/services and app.identity; the engine is disposed by
         app.main lifespan.
Depends on: app.core.config, app.core.tenant.
Key invariants:
  - One async engine per process.
  - scoped_session(org_id) binds the `app.current_org_id` Postgres GUC on EVERY
    transaction the session opens — the seam Row-Level Security policies bind to.
    Policies are DEFINED (migration 0003); DB-level enforcement stays inert until the
    app connects as a non-superuser role (today it connects as superuser `oneai`,
    which bypasses RLS), so the active control remains the app-layer org_id filter —
    see docs/FIX_BEFORE_PROD.md. The FastAPI tenant dependency (get_tenant_session)
    lives in app.identity.dependencies and derives org_id from the verified JWT.
  - expire_on_commit=False so ORM objects remain usable after commit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.tenant import set_current_org

_settings = get_settings()

engine = create_async_engine(_settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a plain async session (no tenant scope), committing on success.

    For non-tenant operations: health checks, login (no token yet), platform-admin
    work that spans organizations. This dependency is the unit-of-work boundary —
    it commits when the request handler returns and rolls back on any exception, so
    repositories only flush and services hold no transaction logic (rule A5).
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


@asynccontextmanager
async def scoped_session(org_id: UUID) -> AsyncIterator[AsyncSession]:
    """Yield a tenant-scoped async session bound to `org_id`.

    Contract:
        - Sets the context-local tenant (set_current_org) so get_current_org() works
          downstream, then opens a session whose every transaction re-applies the
          `app.current_org_id` GUC via the after_begin listener below.
        - org_id MUST come from a verified source (the JWT claim) — callers never
          pass a header or body value here.

    Used as the engine of app.identity.dependencies.get_tenant_session.
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
