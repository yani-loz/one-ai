"""
Role: Async database engines + session factories — the RLS role/connection split.
Used by: app.api routes/services and app.identity; engines disposed by app.main lifespan.
Depends on: app.core.config, app.core.tenant.
Key invariants:
  - THREE engines, by privilege (migration 0009 + scripts.provision_roles create the roles):
      * engine          — OWNER/superuser. Alembic/DDL, provisioning, test schema only; never HTTP.
      * tenant_engine   — `oneai_app`  (NOSUPERUSER, non-owner, NO BYPASSRLS). The role RLS ENFORCES
                          against. Feeds TenantSessionLocal -> scoped_session -> get_tenant_session.
      * global_engine   — `oneai_global` (BYPASSRLS). The legitimately cross-org / pre-org flows.
                          Feeds GlobalSessionLocal -> get_session. NEVER gets the GUC listener.
  - scoped_session(org_id) binds `app.current_org_id` on EVERY transaction (the seam the
    org_isolation policies key on); org_id MUST come from the verified JWT (get_tenant_session).
  - The privilege boundary is a STATIC pool property: a flow on the wrong engine is a one-file,
    code-review-visible mis-wire. A global flow wrongly on the tenant engine fails closed/loud
    (empty/500); a tenant flow wrongly on the global engine fails open/silent — so the bypass pool
    must only ever back get_session. There is deliberately no ambiguous `SessionLocal` symbol.
  - expire_on_commit=False so ORM objects remain usable after commit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.tenant import set_current_org

_settings = get_settings()

# OWNER engine — superuser/owner role. Alembic/DDL, role provisioning, and test schema setup only;
# it never serves an HTTP request. Exposed so the test harness can create/drop/TRUNCATE schema, and
# so the conftest can dispose every engine on the per-test event loop.
engine = create_async_engine(_settings.database_url, echo=False, pool_pre_ping=True)

# TENANT engine — non-bypass `oneai_app`; the role RLS enforces against. Tenant requests run here
# with `app.current_org_id` bound per transaction (see scoped_session).
tenant_engine = create_async_engine(_settings.tenant_database_url, echo=False, pool_pre_ping=True)
TenantSessionLocal = async_sessionmaker(tenant_engine, class_=AsyncSession, expire_on_commit=False)

# GLOBAL engine — the `oneai_global` role (BYPASSRLS), for cross-org / pre-org flows. It NEVER gets
# the GUC listener: bypass is a static role attribute on its own pool, not connection state that
# could survive asyncpg checkin and contaminate the next request (the hazard is_local=true avoids).
global_engine = create_async_engine(_settings.global_database_url, echo=False, pool_pre_ping=True)
GlobalSessionLocal = async_sessionmaker(global_engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a GLOBAL (BYPASSRLS) async session, committing on success.

    Backed by the `oneai_global` engine — for the legitimately cross-org / pre-org flows: health
    checks, login/refresh (no org yet), and platform-admin / erasure / audit work that spans
    organizations. This dependency is the unit-of-work boundary — it commits when the request
    handler returns and rolls back on any exception, so repositories only flush and services hold
    no transaction logic (rule A5). MUST NOT back a tenant flow (that would bypass RLS silently).
    """
    async with GlobalSessionLocal() as session:
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
    async with TenantSessionLocal() as session:
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


async def runtime_roles_present() -> bool:
    """Return True iff both least-privilege runtime roles exist on the connected database.

    Test-harness / diagnostic helper. Post-migration-0009 the app connects as `oneai_app` /
    `oneai_global`, which exist only after `alembic upgrade head` + `scripts.provision_roles`.
    DB-touching test fixtures call this to SKIP the suite with a clear message on a non-provisioned
    database, instead of failing with a raw asyncpg "role does not exist" / auth error. Checked on
    the owner `engine`, which is always present.
    """
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT rolname FROM pg_roles WHERE rolname IN (:app_role, :global_role)"),
            {"app_role": _settings.app_db_user, "global_role": _settings.global_db_user},
        )
        present = {row[0] for row in rows}
    return {_settings.app_db_user, _settings.global_db_user} <= present
