"""
Role: The probe-database LIFECYCLE of contract §12 / §16.4 / §16.11 — preflight of the
      configured server, creation of a disposable `mem01_probe_<run_id>` database, the
      exclusive-ownership marker written BEFORE the Alembic migration runs in a CHILD process,
      the emptiness check taken afterwards, the `--probe-db` reuse rules, and the drop (WITHOUT
      `FORCE`) after every engine is disposed. Fixture gates live inside a probe; the configured
      database is never created, migrated, written or dropped here. The MARKER and STALENESS
      half — `ProbeOwnerMarker`, `read_owner_marker`, `pid_is_alive`, `is_stale`,
      `list_stale_probe_databases` — lives in the sibling `probe_marker` and is RE-EXPORTED
      here, because §1.3 places those names on this module.
Used by: tools.mem01_verify.runner_steps — the instrument's only importer (step 5 lists the
      stale probes, step 6 creates or claims the probe the fixture gates run in, step 11 drops
      it); and the sealed oracle under backend/tests/tools/mem01_verify/, whose conftest builds
      its session probe corpus here and whose staleness pins import through these re-exports.
Depends on: tools.mem01_verify.probe_conn (the guarded raw-connection layer: targeting,
      run-id grammar, lifecycle statements), tools.mem01_verify.probe_marker (the ownership
      marker, liveness and stale listing), tools.mem01_verify.probe_env (the allowlist-only
      child environment and the Alembic migration child), tools.mem01_verify.db
      (`PROBE_PREFIX`, engine disposal), app.core.config, tools.mem01_verify.exceptions.
Key invariants:
  - TARGETING IS A SAFETY BOUNDARY: every create, claim, migrate and drop first proves the
    target carries `PROBE_PREFIX` and differs from the configured database.
  - Alembic NEVER runs in-process — `get_settings` is cached process-wide and the migration
    environment reads its URL from it, so it runs in a child whose environment comes ONLY from
    `child_environment` and whose streams are captured, never echoed (§16.16(a)).
  - MARKER BEFORE MIGRATION (§16.16(s)): the marker is written straight after `CREATE
    DATABASE`, before the migration child, so a markerless probe was never claimed and a
    creation in flight is never mistaken for one left behind. The marker table is outside the
    app metadata (`alembic upgrade head` ignores it) and outside the emptiness check.
  - REUSE, not staleness, is what `released` governs: `claim_probe_database` admits a probe only
    when its marker is released or its creator is gone. The staleness rule of §16.16(l) and its
    accepted residual window belong to `probe_marker`.
  - The instrument never provisions, alters or re-passwords a cluster role; preflight only
    OBSERVES that the three runtime roles exist and can log in, and RETURNS the observation.
  - A probe this process did not create is never truncated, never dropped (§16.4).
  - The drop omits `FORCE` and follows `db.dispose_probe_engines`, so a probe still holding a
    foreign connection refuses to drop — the §16.11 infrastructure error rather than a silent
    disconnection of someone else's session.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from app.core.config import get_settings
from tools.mem01_verify.db import PROBE_PREFIX, dispose_probe_engines
from tools.mem01_verify.exceptions import ProbeDatabaseError
from tools.mem01_verify.probe_conn import (
    MAINTENANCE_DATABASE,
    OWNER_TABLE,
    assert_probe_target,
    assert_run_id,
    can_log_in,
    execute_on_probe,
    fetch_probe_names,
    lifecycle_statement,
    owner_connect,
    redact_secrets,
)

# §16.16(a) names `child_environment` here; the child it belongs to lives in `probe_env`.
from tools.mem01_verify.probe_env import child_environment as child_environment
from tools.mem01_verify.probe_env import migrate_probe_database, read_migration_head

# §1.3 places these five names on `probe_db`; the marker and staleness half of §16.4/§16.16(l)
# lives in `probe_marker`, which this module drives.
from tools.mem01_verify.probe_marker import (
    ProbeOwnerMarker as ProbeOwnerMarker,
)
from tools.mem01_verify.probe_marker import (
    is_stale as is_stale,
)
from tools.mem01_verify.probe_marker import (
    list_stale_probe_databases as list_stale_probe_databases,
)
from tools.mem01_verify.probe_marker import (
    pid_is_alive as pid_is_alive,
)
from tools.mem01_verify.probe_marker import (
    read_owner_marker as read_owner_marker,
)

# Tenant tables whose emptiness proves a freshly migrated probe carries no rows (§12).
_EMPTY_START_TABLES = ("organizations", "email_message", "person")


@dataclass(frozen=True)
class ProbePreflight:
    """What the configured server allows, observed before anything is created (§12)."""

    roles_ok: bool
    missing_roles: tuple[str, ...]
    login_ok: Mapping[str, bool]
    can_create_database: bool
    stale_probes: tuple[str, ...]


@dataclass
class ProbeDatabase:
    """A live probe database and the lease this process holds on it (§12/§16.4).

    `owns_lifecycle` is False for a probe claimed through `claim_probe_database`: Stage A never
    drops a probe it did not create, so `drop()` on such a lease is a documented no-op.
    """

    name: str
    run_id: str
    created_at: datetime
    migrated_to: str
    owns_lifecycle: bool = field(default=True, repr=False, compare=False)
    dropped: bool = field(default=False, repr=False, compare=False)

    async def release(self) -> None:
        """Mark the lease released so another run may claim this probe (§16.4).

        Called by the creator before handing the probe to a child process; the marker keeps this
        `run_id`/`pid` until a reuser overwrites them.
        """
        await execute_on_probe(self.name, f'UPDATE "{OWNER_TABLE}" SET released = true')

    async def drop(self) -> None:
        """Dispose every engine bound to this probe, then `DROP DATABASE` without `FORCE`.

        Idempotent after a successful drop and re-attemptable after a refused one; a claimed
        probe is never dropped.

        Raises:
            ProbeDatabaseError: backends are still connected, or the server refused the drop —
                the §16.11 infrastructure error, never a reason to escalate to `FORCE`.
        """
        if self.dropped or not self.owns_lifecycle:
            return
        await dispose_probe_engines(self.name)
        await _drop_database(self.name)
        self.dropped = True


def probe_name_for(run_id: str) -> str:
    """Return `mem01_probe_<run_id>`, validating the §16.3 run-id grammar first.

    Raises ProbeDatabaseError when `run_id` does not match the determined grammar, or when the
    resulting name is not a legitimate probe target.
    """
    return assert_probe_target(f"{PROBE_PREFIX}{assert_run_id(run_id)}")


async def _drop_database(name: str) -> None:
    """`DROP DATABASE` — never `WITH (FORCE)`, and only ever for a prefixed name."""
    assert_probe_target(name)
    await lifecycle_statement(
        f'DROP DATABASE IF EXISTS "{name}"', name, "cannot drop probe (connections still open?)"
    )


async def _claim_probe_database(name: str, run_id: str) -> datetime:
    """Write the §16.4 ownership marker on a just-created probe; return its `created_at`.

    Runs BEFORE the migration child (§16.16(s)): the claim is what makes the probe legible to
    `list_stale_probe_databases`, so a markerless probe means "never claimed", not "left behind".

    Raises ProbeDatabaseError when the marker table, its grants or its row could not be written.
    """
    settings = get_settings()
    roles = ", ".join(
        f'"{role}"'
        for role in (settings.app_db_user, settings.global_db_user, settings.reader_db_user)
    )
    created_at = datetime.now(UTC)
    connection = await owner_connect(name)
    try:
        await connection.execute(
            f'CREATE TABLE "{OWNER_TABLE}" (run_id text NOT NULL, pid integer NOT NULL, '
            "created_at timestamptz NOT NULL, released boolean NOT NULL)"
        )
        await connection.execute(f'GRANT SELECT ON "{OWNER_TABLE}" TO {roles}')
        await connection.execute(
            f'INSERT INTO "{OWNER_TABLE}" (run_id, pid, created_at, released) '
            "VALUES ($1, $2, $3, false)",
            run_id,
            os.getpid(),
            created_at,
        )
    except asyncpg.PostgresError as error:
        raise ProbeDatabaseError(f"cannot claim {name!r}: {redact_secrets(str(error))}") from error
    finally:
        await connection.close()
    return created_at


async def _verify_probe_is_empty(name: str) -> None:
    """Prove the freshly migrated probe holds no tenant rows (§12) — marker table EXCLUDED.

    `_EMPTY_START_TABLES` names tenant tables only, so the marker written before the migration is
    never counted. Raises ProbeDatabaseError on tenant rows, or when the count cannot be read.
    """
    counts = " + ".join(f"(SELECT count(*) FROM {table})" for table in _EMPTY_START_TABLES)
    connection = await owner_connect(name)
    try:
        rows = await connection.fetchval(f"SELECT {counts}")
    except asyncpg.PostgresError as error:
        raise ProbeDatabaseError(
            f"cannot count tenant rows in {name!r}: {redact_secrets(str(error))}"
        ) from error
    finally:
        await connection.close()
    if rows:
        raise ProbeDatabaseError(f"freshly migrated probe {name!r} already holds {rows} rows")


async def preflight_probe_server() -> ProbePreflight:
    """Observe whether the configured server can host a probe, before anything is created.

    Checks that the three runtime roles exist and can LOG IN with their configured
    credentials, that the owner may create databases, and lists the probes already present.
    Nothing is provisioned and no role is altered (§12); the CALLER decides whether to abort —
    a run aborts when `roles_ok` is false, a login failed, or `can_create_database` is false.

    Raises:
        ProbeDatabaseError: the maintenance database itself could not be opened.
    """
    settings = get_settings()
    roles = (settings.app_db_user, settings.global_db_user, settings.reader_db_user)
    passwords = (
        settings.oneai_app_password,
        settings.oneai_global_password,
        settings.oneai_reader_password,
    )
    connection = await owner_connect(MAINTENANCE_DATABASE)
    try:
        present = {
            record["rolname"]
            for record in await connection.fetch(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY($1::text[])", list(roles)
            )
        }
        can_create = bool(
            await connection.fetchval(
                "SELECT rolsuper OR rolcreatedb FROM pg_roles WHERE rolname = current_user"
            )
        )
        stale = await fetch_probe_names(connection)
    finally:
        await connection.close()
    login_ok = {
        role: await can_log_in(role, password)
        for role, password in zip(roles, passwords, strict=True)
    }
    missing = tuple(role for role in roles if role not in present)
    return ProbePreflight(
        roles_ok=not missing,
        missing_roles=missing,
        login_ok=login_ok,
        can_create_database=can_create,
        stale_probes=stale,
    )


@asynccontextmanager
async def create_probe_database(
    run_id: str, *, keep: bool = False, migration_log: Path | None = None
) -> AsyncIterator[ProbeDatabase]:
    """Create, CLAIM, migrate and finally drop a probe database for `run_id` (§12).

    Args:
        run_id: A §16.3 run id; the probe is named `mem01_probe_<run_id>`.
        keep: Leave the probe in place on exit (the runner's `--keep-probe`).
        migration_log: Where a failed Alembic child's captured output is written (A10); `None`
            writes `probe_migration.log` beside the maintenance working directory.

    Yields:
        The live probe: claimed before the migration ran (§16.16(s)), at the repository's head
        revision, empty of tenant rows.

    Raises:
        ProbeDatabaseError: malformed run id, illegitimate target, a failed creation, claim or
            migration (`probe migration failed`), a non-empty migrated database, or a refused
            drop (§16.11); every failure before the yield drops the half-built probe first.
    """
    name = probe_name_for(run_id)
    await lifecycle_statement(f'CREATE DATABASE "{name}"', name, "cannot create probe")
    try:
        created_at = await _claim_probe_database(name, run_id)
        migrated_to = await migrate_probe_database(name, migration_log=migration_log)
        await _verify_probe_is_empty(name)
    except BaseException:
        await dispose_probe_engines(name)
        await _drop_database(name)
        raise
    probe = ProbeDatabase(name=name, run_id=run_id, created_at=created_at, migrated_to=migrated_to)
    try:
        yield probe
    finally:
        if not keep:
            await probe.drop()


@asynccontextmanager
async def claim_probe_database(name: str, run_id: str) -> AsyncIterator[ProbeDatabase]:
    """Take over an EXISTING probe named by `--probe-db`, under the reuse rules of §16.4.

    Admitted only if the target carries the probe prefix, is not the configured database, and
    carries an ownership marker that is released or held by a process no longer running. The
    reuser overwrites the marker with its own `run_id`/`pid`, never truncates the probe and never
    drops it (Stage A). `run_id` is this run's §16.3 run id, written into the marker.

    Yields:
        A lease whose `owns_lifecycle` is False.

    Raises ProbeDatabaseError when an admission condition fails, or when the database, the
    marker table or the migration head is absent.
    """
    assert_probe_target(name)
    assert_run_id(run_id)
    marker = _admit_reuse(await read_owner_marker(name), name)
    migrated_to = await read_migration_head(name)
    connection = await owner_connect(name)
    try:
        await connection.execute(
            f'UPDATE "{OWNER_TABLE}" SET run_id = $1, pid = $2, released = false',
            run_id,
            os.getpid(),
        )
    except asyncpg.PostgresError as error:
        raise ProbeDatabaseError(f"cannot claim {name!r}: {redact_secrets(str(error))}") from error
    finally:
        await connection.close()
    try:
        yield ProbeDatabase(
            name=name,
            run_id=run_id,
            created_at=marker.created_at,
            migrated_to=migrated_to,
            owns_lifecycle=False,
        )
    finally:
        await dispose_probe_engines(name)


def _admit_reuse(marker: ProbeOwnerMarker | None, name: str) -> ProbeOwnerMarker:
    """Return the ownership marker of `name`, refusing a probe another live run still holds."""
    if marker is None:
        raise ProbeDatabaseError(f"{name!r} carries no ownership marker")
    if not marker.released and pid_is_alive(marker.pid):
        raise ProbeDatabaseError(
            f"{name!r} is held by a running process — refusing to reuse it (§16.4)"
        )
    return marker
