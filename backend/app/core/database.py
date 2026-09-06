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
  - FOUR engines since PF-01 (migration 0019): the fourth is
      * reader_engine  — `oneai_reader` (NOSUPERUSER, NO BYPASSRLS, SELECT-only). The person-
                         scoped RETRIEVAL plane: the `visibility` RLS policies target this role
                         only, and it holds no write grant on any tenant table — agent/tool/
                         retrieval code physically cannot write or widen. Feeds reader_session.
    WHY a separate role (discovered live, 2026-07-04): Postgres applies SELECT policies to the
    rows returned by INSERT ... RETURNING, so a restrictive visibility policy on the WRITE role
    would break person-less ingest inserting the very restricted rows it creates. One role
    cannot be both the person-less write plane and the person-scoped fail-closed read plane.
  - scoped_session(org_id) binds `app.current_org_id` on EVERY transaction (the seam the
    org_isolation policies key on); org_id MUST come from the verified JWT (get_tenant_session).
  - reader_session(org_id, person_id=None) additionally binds `app.current_person_id` — the
    seam the PF-01 `visibility` policies key on. person_id MUST come from the verified auth
    binding (principal_source_identity), never a header/body. A person-less reader session
    serves only visibility_scope='org' rows (AC3, fail-closed).
  - ENGINE-SEAM GUARD (PF-01 AC18): every scoped/reader transaction ASSERTS the connection's
    current_user is the expected role and aborts otherwise — a flow that somehow acquired a
    BYPASSRLS (or merely wrong) pool fails LOUD, never open/silent. Piggybacked on the same
    round-trip that sets the GUCs (no extra latency).
  - The privilege boundary is a STATIC pool property: a flow on the wrong engine is a one-file,
    code-review-visible mis-wire. A global flow wrongly on the tenant engine fails closed/loud
    (empty/500); a tenant flow wrongly on the global engine fails open/silent — so the bypass pool
    must only ever back get_session, and the runtime assertion above backstops the convention.
  - expire_on_commit=False so ORM objects remain usable after commit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, SessionTransaction

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

# READER engine — the SELECT-only `oneai_reader` role: the PF-01 person-scoped retrieval plane.
# The `visibility` policies target this role; it cannot write (no DML grant on tenant tables).
reader_engine = create_async_engine(_settings.reader_database_url, echo=False, pool_pre_ping=True)
ReaderSessionLocal = async_sessionmaker(reader_engine, class_=AsyncSession, expire_on_commit=False)


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


class EngineSeamViolationError(RuntimeError):
    """A scoped session is running as the WRONG database role (PF-01 AC18).

    Raised by the scoped/reader transaction listener when `current_user` is not the role the
    seam expects — the silent, maximally permissive failure mode (a tenant flow on a BYPASSRLS
    pool, or a retrieval flow on the write pool) converted into a loud abort before any SQL runs.
    """


@asynccontextmanager
async def scoped_session(org_id: UUID) -> AsyncIterator[AsyncSession]:
    """Yield a tenant-scoped async session bound to `org_id` (the WRITE/system plane).

    Contract:
        - Sets the context-local tenant (set_current_org) so get_current_org() works
          downstream, then opens a session whose every transaction re-applies the
          `app.current_org_id` GUC via the after_begin listener below.
        - org_id MUST come from a verified source (the JWT claim) — callers never
          pass a header or body value here.
        - Every transaction asserts the connection runs as the app role (AC18).
        - Within-tenant visibility does NOT apply on this plane (the `visibility` policies
          target oneai_reader) — person-scoped retrieval uses reader_session instead.

    Used as the engine of app.identity.dependencies.get_tenant_session.
    """
    set_current_org(org_id)
    async with TenantSessionLocal() as session:
        _bind_scope(session, org_id, None, _settings.app_db_user)
        yield session


@asynccontextmanager
async def reader_session(
    org_id: UUID, person_id: UUID | None = None
) -> AsyncIterator[AsyncSession]:
    """Yield a RETRIEVAL session bound to `org_id` (+ optionally a person) on the reader pool.

    The PF-01 person-scoped read plane (SELECT-only role; the `visibility` policies target it):
        - person_id MUST come ONLY from the verified auth binding (principal_source_identity,
          source_type='auth') — never a header, body, or unverified row (AC20).
        - person_id=None serves only visibility_scope='org' rows (AC3, fail-closed) — an
          unbound or forgotten person context can never widen to restricted content.
        - Every transaction asserts the connection runs as the reader role (AC18); the role
          holds no write grant, so retrieval code cannot mutate tenant data even if it tries.

    The seam for the Ask/agent/retrieval layer and app.access.dependencies.
    """
    set_current_org(org_id)
    async with ReaderSessionLocal() as session:
        _bind_scope(session, org_id, person_id, _settings.reader_db_user)
        yield session


def _bind_scope(
    session: AsyncSession, org_id: UUID, person_id: UUID | None, expected_role: str
) -> None:
    """Re-apply the scope GUCs + the engine-seam assertion on every transaction.

    `set_config(..., is_local=true)` is transaction-scoped — the correct choice for
    a pooled async connection (no cross-request leakage) but it resets on commit.
    Listening on `after_begin` re-applies it to each new transaction, closing the
    fail-open window once RLS is enabled. org_id/person_id are validated UUIDs, so
    embedding them as literals is injection-safe.

    AC18 engine-seam guard: the SAME round-trip returns `current_user`; anything other
    than `expected_role` aborts the transaction with EngineSeamViolationError — a scoped
    flow can never silently run on a BYPASSRLS (or otherwise wrong) connection.

    The person GUC is bound on EVERY transaction, explicitly to '' when there is no person:
    binding it only when set would leave a person-less read inheriting whatever value the
    connection already carried, so any future path that leaves a session-level person id
    behind would silently widen an org-scope read. PF-01 policies read the GUC with
    NULLIF(current_setting(..., true), ''), so '' is exactly the fail-closed value.
    """
    person_setting = (
        f", set_config('app.current_person_id', '{person_id if person_id else ''}', true)"
    )
    # The probe returns the role NAME and its RLS-relevant attributes in the same round trip.
    # A name check alone is not an isolation guarantee: the role name comes from configuration,
    # so pointing READER_DB_USER at a BYPASSRLS role (or granting BYPASSRLS to the reader
    # later) would satisfy it while every policy silently stopped applying.
    scope_and_probe_sql = (
        f"SELECT set_config('app.current_org_id', '{org_id}', true){person_setting}, "
        "current_user, "
        "(SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user)"
    )

    @event.listens_for(session.sync_session, "after_begin")
    def _apply_scope(
        _session: Session, _transaction: SessionTransaction, connection: Connection
    ) -> None:
        row = connection.exec_driver_sql(scope_and_probe_sql).one()
        connected_role, bypasses_rls = row[-2], row[-1]
        if connected_role != expected_role:
            raise EngineSeamViolationError(
                f"Scoped session is connected as {connected_role!r}, expected "
                f"{expected_role!r} — refusing to run scoped SQL on this connection (AC18)."
            )
        if bypasses_rls:
            raise EngineSeamViolationError(
                f"Scoped session is connected as {connected_role!r}, which is SUPERUSER or "
                "BYPASSRLS — every row-level policy would be inert, so this connection is "
                "refused regardless of its name."
            )


async def runtime_roles_present() -> bool:
    """Return True iff every least-privilege runtime role exists on the connected database.

    Test-harness / diagnostic helper. Post-migration-0009 the app connects as `oneai_app` /
    `oneai_global` (and post-0019 the retrieval plane as `oneai_reader`), which exist only after
    `alembic upgrade head` + `scripts.provision_roles`. DB-touching test fixtures call this to
    SKIP the suite with a clear message on a non-provisioned database, instead of failing with a
    raw asyncpg "role does not exist" / auth error. Checked on the owner `engine`, which is
    always present.
    """
    required = {_settings.app_db_user, _settings.global_db_user, _settings.reader_db_user}
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:roles)"),
            {"roles": list(required)},
        )
        present = {row[0] for row in rows}
    return required <= present
