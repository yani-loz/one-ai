"""
Role: The database planes of the MEM-01 Stage-A instruments — the READ ONLY / REPEATABLE READ
      corpus snapshot session of contract rule R6, and the three REAL application planes
      (write / person-scoped reader / global) re-bound to a probe database (§1.3 `db`, §12).
      This module is the ONLY place the instruments open a connection to Postgres through
      SQLAlchemy; every engine it creates is cached here so a probe can be fully disconnected
      before it is dropped (§16.13).
Used by: tools.mem01_verify.probe_db (engine disposal before DROP DATABASE),
      tools.mem01_verify.corpus_identity, .census, .snapshot, .leakage, .lang_bootstrap,
      .release, the gate evaluators and tools.mem01_verify.verify_step1; the sealed oracle
      under backend/tests/tools/mem01_verify/.
Depends on: app.core.config (the configured server and the four role URLs), app.core.database
      (`_bind_scope` — the per-transaction GUC binding + AC18 engine-seam assertion the
      application itself uses, reused verbatim so the instrument measures the REAL planes),
      tools.mem01_verify.exceptions.
Key invariants:
  - R6: every transaction of `readonly_corpus_snapshot` is `REPEATABLE READ` and `READ ONLY`
    AT THE SERVER — the characteristics live on the engine, so asyncpg re-issues them on the
    lazy BEGIN of every transaction, including those opened after a commit, a rollback or a
    reconnect. A write is refused by Postgres (SQLSTATE 25006), never by convention.
  - Every session this module hands out verifies `current_database()` on its own connection,
    inside the transaction, before the caller's first statement; a mismatch raises
    `ProbeDatabaseError` rather than reading the wrong database.
  - The instrument NEVER writes to the configured database: the snapshot plane is the only
    plane ever pointed at it, and that plane cannot write.
  - Engines are cached per (plane, database) and disposed only through
    `dispose_probe_engines`; nothing else may hold a connection to a probe at drop time.
  - §16.15: `read_alembic_version` is the ONE sanctioned global-plane read of the instrument.
    Migration 0013 revokes every privilege on `alembic_version` from `oneai_app`, so the
    migration ledger is unreadable on the snapshot plane; this helper reads it — and nothing
    else — in its own `REPEATABLE READ` + `READ ONLY` transaction on the global role, and every
    caller (census, corpus identity, run identity) takes the value from here.
  - That read is MEMOISED per database name for the life of the process, so a run performs
    exactly one global-plane statement per database however many callers ask (§16.15). Only a
    successful, single-head answer is cached; every refusal is recomputed on the next call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction

from app.core.config import get_settings
from app.core.database import _bind_scope
from tools.mem01_verify.exceptions import IntegrityViolationError, ProbeDatabaseError

Plane = Literal["snapshot", "write", "reader", "global"]

# The probe-database name prefix (§12). It is DEFINED here and re-exported by `probe_db` as the
# public `PROBE_PREFIX` of §1.3, so the targeting guard below and the lifecycle guards there can
# never drift apart, and `probe_db` may import this module without a cycle.
PROBE_PREFIX = "mem01_probe_"

# (plane, database) -> engine. Cached because a probe database must be fully disconnected
# before DROP DATABASE (§16.11/§16.13), which is only possible if every engine is reachable.
_ENGINES: dict[tuple[str, str], AsyncEngine] = {}


def _plane_url(plane: Plane, database: str) -> str:
    """The application URL of `plane`, re-pointed at `database`.

    The URL is derived from the settings property the application itself uses, so the role,
    password and server are never re-assembled by hand (a mis-quoted password would otherwise
    silently move the instrument onto a different role).
    """
    settings = get_settings()
    by_plane = {
        "snapshot": settings.tenant_database_url,
        "write": settings.tenant_database_url,
        "reader": settings.reader_database_url,
        "global": settings.global_database_url,
    }
    # `str(URL)` masks the password as `***` in SQLAlchemy 2.0, which would make every probe
    # connection authenticate with the literal string "***"; render it unmasked instead.
    return make_url(by_plane[plane]).set(database=database).render_as_string(hide_password=False)


def _engine_for(plane: Plane, database: str) -> AsyncEngine:
    """Return (creating once) the cached engine for `plane` against `database`."""
    cached = _ENGINES.get((plane, database))
    if cached is not None:
        return cached
    url = _plane_url(plane, database)
    if plane == "snapshot":
        # R6 lives HERE: asyncpg opens every transaction of this engine with
        # `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`, so the guarantee survives
        # commits, rollbacks and pool reconnects without any per-statement discipline.
        engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            isolation_level="REPEATABLE READ",
            execution_options={"postgresql_readonly": True},
        )
    else:
        engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    _ENGINES[(plane, database)] = engine
    return engine


def _bind_database_check(session: AsyncSession, database: str) -> None:
    """Assert on EVERY transaction that this connection really is bound to `database`.

    The check runs inside the transaction (never on pool checkout): a statement issued from a
    `connect` handler would start asyncpg's transaction early and pin the REPEATABLE READ
    snapshot before the caller's first read.
    """

    @event.listens_for(session.sync_session, "after_begin")
    def _verify_bound(
        _session: Session, _transaction: SessionTransaction, connection: Connection
    ) -> None:
        bound = connection.exec_driver_sql("SELECT current_database()").scalar()
        if bound != database:
            raise ProbeDatabaseError(
                f"session is bound to database {bound!r}, expected {database!r} — refusing to "
                "run instrument SQL on this connection"
            )


@asynccontextmanager
async def readonly_corpus_snapshot(
    org_id: UUID, *, database: str | None = None
) -> AsyncIterator[AsyncSession]:
    """Yield a tenant-scoped session over ONE `REPEATABLE READ` + `READ ONLY` snapshot (R6).

    Args:
        org_id: The tenant whose rows the session may read; bound as `app.current_org_id` on
            every transaction, so the org-isolation policies apply.
        database: The database to open. `None` (the default) means the configured database —
            the real corpus, which this plane can only ever read.

    Contract:
        - Runs on the application's WRITE role (`oneai_app`): org isolation applies, the
            within-tenant `visibility` policies (which target `oneai_reader`) do not, so a
            corpus gate sees the tenant's complete evidence.
        - Every transaction is declared `REPEATABLE READ` and `READ ONLY` at the server; a
            write attempt raises the server's read-only error (SQLSTATE 25006).
        - Re-entering the context yields a NEW snapshot; one gate run must therefore hold a
            single context open for the whole of its evidence.

    Edge cases:
        A connection bound to another database raises `ProbeDatabaseError` before any read.
    """
    target = database if database is not None else get_settings().postgres_db
    factory = async_sessionmaker(
        _engine_for("snapshot", target), class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        _bind_database_check(session, target)
        _bind_scope(session, org_id, None, get_settings().app_db_user)
        yield session


async def snapshot_transaction_id(session: AsyncSession) -> str:
    """Return the server's snapshot identifier for the session's CURRENT transaction (R6).

    `pg_current_snapshot()` renders `xmin:xmax:xip_list`, which identifies the visibility
    horizon every statement of a REPEATABLE READ transaction shares — the value the machine
    block records so two readings can be proven to have come from one snapshot.
    """
    return str((await session.execute(text("SELECT pg_current_snapshot()::text"))).scalar_one())


@dataclass(frozen=True)
class ProbeSessions:
    """The three REAL application planes of §12, bound to one probe database.

    Fixture gates arrange state through `write` (or the ingest service running on it) and read
    through `reader`, exactly as the product does; `global_` is the privileged plane used for
    cross-org bookkeeping and by the oracle's own seeding. No plane here is ever pointed at the
    configured database — `probe_db` is the only caller that names a database, and it only ever
    names a `mem01_probe_` one.
    """

    database: str

    @asynccontextmanager
    async def write(self, org_id: UUID) -> AsyncIterator[AsyncSession]:
        """Yield the tenant WRITE/system plane (`oneai_app`) bound to `org_id` on the probe."""
        async with self._session("write") as session:
            _bind_scope(session, org_id, None, get_settings().app_db_user)
            yield session

    @asynccontextmanager
    async def reader(
        self, org_id: UUID, person_id: UUID | None = None
    ) -> AsyncIterator[AsyncSession]:
        """Yield the person-scoped RETRIEVAL plane (`oneai_reader`) on the probe.

        `person_id=None` serves only `visibility_scope='org'` rows (PF-01 AC3, fail-closed) —
        the same behaviour the application's `reader_session` has, deliberately not relaxed.
        """
        async with self._session("reader") as session:
            _bind_scope(session, org_id, person_id, get_settings().reader_db_user)
            yield session

    @asynccontextmanager
    async def global_(self) -> AsyncIterator[AsyncSession]:
        """Yield the cross-org GLOBAL plane (`oneai_global`, BYPASSRLS) on the probe.

        It never gets the GUC listener (matching `app.core.database.get_session`): bypass is a
        static role attribute on its own pool, not connection state that could leak.
        """
        async with self._session("global") as session:
            yield session

    @asynccontextmanager
    async def _session(self, plane: Plane) -> AsyncIterator[AsyncSession]:
        factory = async_sessionmaker(
            _engine_for(plane, self.database), class_=AsyncSession, expire_on_commit=False
        )
        async with factory() as session:
            _bind_database_check(session, self.database)
            yield session


def probe_session_factories(database: str) -> ProbeSessions:
    """Return the three application planes bound to the probe database `database` (§12).

    Args:
        database: The probe database name. It must carry the `mem01_probe_` prefix — this
            function is a safety boundary, not a convenience, so the configured database can
            never be reached through a write-capable plane.

    Raises:
        ProbeDatabaseError: `database` does not carry the probe prefix.
    """
    if not database.startswith(PROBE_PREFIX):
        raise ProbeDatabaseError(
            f"refusing to open application planes on {database!r}: not a probe database"
        )
    return ProbeSessions(database=database)


async def dispose_probe_engines(database: str) -> None:
    """Dispose and forget every engine this module holds against `database` (§16.13).

    Called immediately before `DROP DATABASE`: the drop is issued WITHOUT `FORCE`, so a single
    pooled connection left behind would turn a clean teardown into an infrastructure error.
    Disposing an engine twice is harmless, so the caller may retry a failed drop.
    """
    for key in [key for key in _ENGINES if key[1] == database]:
        engine = _ENGINES.pop(key)
        await engine.dispose()


# ── §16.15: the migration ledger, the one read the snapshot plane cannot perform ──────────

# Migration 0013 (`_PLATFORM_ONLY_TABLES`) does `REVOKE ALL PRIVILEGES ON TABLE alembic_version
# FROM oneai_app`, so `SELECT version_num FROM alembic_version` on the snapshot plane fails with
# "permission denied for table alembic_version". `oneai_global` kept the 0009 grant, so the
# ledger — and ONLY the ledger — is read through a dedicated global-plane engine, cached under
# its own plane key so `dispose_probe_engines` still disconnects it before a probe is dropped.
_LEDGER_PLANE = "ledger"

# database name -> the revision already read for it. A run never migrates the database it
# measures and probe names are unique per run (§12), so a name can never denote two different
# schema states within one process; the cache therefore holds the read to one per database.
_ALEMBIC_VERSIONS: dict[str, str] = {}

# One statement: the bound database beside the sorted revisions of the ledger. `array_agg` over
# an empty table still yields exactly one row (NULL), so an unmigrated database is a clean
# refusal rather than an empty result set.
_LEDGER_SQL = (
    "SELECT current_database(), array_agg(version_num ORDER BY version_num) FROM alembic_version"
)


def _ledger_engine(database: str) -> AsyncEngine:
    """Return (creating once) the READ ONLY global-plane engine used for the ledger read."""
    cached = _ENGINES.get((_LEDGER_PLANE, database))
    if cached is not None:
        return cached
    engine = create_async_engine(
        _plane_url("global", database),
        echo=False,
        pool_pre_ping=True,
        isolation_level="REPEATABLE READ",
        execution_options={"postgresql_readonly": True},
    )
    _ENGINES[(_LEDGER_PLANE, database)] = engine
    return engine


async def read_alembic_version(database: str) -> str:
    """Return the Alembic revision `database` is migrated to, reading it at most once (§16.15).

    The read runs in its own `REPEATABLE READ` + `READ ONLY` transaction on the global role and
    touches `alembic_version` alone — it never reads a tenant table, so it observes no corpus
    row and cannot widen what the caller's snapshot saw. Callers (census, corpus identity, run
    identity) take the revision from here instead of from their own snapshot session, which
    holds no privilege on the ledger.

    The answer is memoised per database name for the life of the process: the first caller pays
    the one global-plane statement and every later caller is served from the cache, so a run
    performs exactly one such read per database. Only a successful single-head answer is
    cached — a refusal is never remembered, and no engine is opened on a cache hit.

    Args:
        database: The database whose migration ledger to read — the caller's
            `SELECT current_database()`, so the value can never name another database.

    Returns:
        The single revision string the ledger holds.

    Raises:
        ProbeDatabaseError: the connection is bound to a different database than requested.
        IntegrityViolationError: the ledger is empty or holds more than one head revision.
    """
    cached = _ALEMBIC_VERSIONS.get(database)
    if cached is not None:
        return cached
    engine = _ledger_engine(database)
    async with engine.connect() as connection:
        bound, revisions = (await connection.execute(text(_LEDGER_SQL))).one()
    if bound != database:
        raise ProbeDatabaseError(
            f"ledger connection is bound to database {bound!r}, expected {database!r}"
        )
    if not revisions or len(revisions) != 1:
        raise IntegrityViolationError(
            f"alembic_version holds {len(revisions or ())} revisions, expected exactly one head"
        )
    revision = str(revisions[0])
    _ALEMBIC_VERSIONS[database] = revision
    return revision
