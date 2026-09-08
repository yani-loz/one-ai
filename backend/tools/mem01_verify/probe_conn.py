"""
Role: The raw asyncpg layer beneath the MEM-01 probe lifecycle — the §12 targeting guard, the
      probe-path binding of the §16.3 run-id grammar, owner connections to the maintenance
      database and to a probe, the
      single-statement helpers `CREATE DATABASE` / `DROP DATABASE` need (they cannot run inside
      a transaction), the probe-name listing, a role login check and the process-liveness test
      the §16.4 reuse rule depends on. This module carries no lifecycle POLICY: it only opens,
      guards and closes connections.
Used by: tools.mem01_verify.probe_db (preflight, create, migrate, claim, release, drop). It is
      the only place in the instrument that opens a raw (non-SQLAlchemy) connection.
Depends on: asyncpg, app.core.config (the configured server and the owner credentials),
      tools.mem01_verify.db (`PROBE_PREFIX` — defined there so the session planes and this
      layer can never drift apart), tools.mem01_verify.run_id (the one encoding of the §16.3
      grammar), tools.mem01_verify.exceptions.
Key invariants:
  - TARGETING IS A SAFETY BOUNDARY: `owner_connect` opens the maintenance database or a name
    that carries `PROBE_PREFIX` and is not the configured database — nothing else, ever.
  - Every connection opened AT a probe re-verifies `current_database()` at the server before
    the caller's first statement (§12); a name that resolves elsewhere is refused and closed.
  - `CREATE DATABASE` / `DROP DATABASE` run through `lifecycle_statement`, on the maintenance
    database and outside any transaction — R6's one carve-out.
  - No password ever reaches a message: every server error passes through `redact_secrets`.
"""

from __future__ import annotations

import os

import asyncpg

from app.core.config import get_settings
from tools.mem01_verify.db import PROBE_PREFIX
from tools.mem01_verify.exceptions import ProbeDatabaseError
from tools.mem01_verify.run_id import assert_run_id as assert_run_id_grammar

MAINTENANCE_DATABASE = "postgres"
OWNER_TABLE = "mem01_probe_owner"


def redact_secrets(message: str) -> str:
    """Blank every configured password out of `message` before it can reach a log or a block."""
    settings = get_settings()
    for secret in (
        settings.postgres_password,
        settings.oneai_app_password,
        settings.oneai_global_password,
        settings.oneai_reader_password,
    ):
        if secret:
            message = message.replace(secret, "***")
    return message


def assert_probe_target(name: str) -> str:
    """Return `name` after proving it is a legitimate probe target (§12 safety boundary).

    Raises:
        ProbeDatabaseError: the name lacks `PROBE_PREFIX`, or names the configured database.
    """
    if not name.startswith(PROBE_PREFIX):
        raise ProbeDatabaseError(f"{name!r} does not carry the {PROBE_PREFIX!r} prefix")
    if name == get_settings().postgres_db:
        raise ProbeDatabaseError(f"{name!r} is the configured database — refusing to touch it")
    return name


def assert_run_id(run_id: str) -> str:
    """Return `run_id` after checking it against the determined §16.3 grammar (`.run_id`).

    Binds the shared check to this layer's own refusal class: a probe name is derived from the
    run id, so a bad id is a probe-targeting failure, not a formatting complaint.

    Raises:
        ProbeDatabaseError: the value does not match the §16.3 grammar.
    """
    return assert_run_id_grammar(run_id, error=ProbeDatabaseError)


async def raw_connect(user: str, password: str, database: str) -> asyncpg.Connection:
    """One asyncpg connection to the configured server as `user` (no targeting policy here).

    Only `owner_connect` and the preflight login check call this; every other caller goes
    through the guarded entry point so a database name is never trusted unchecked.
    """
    settings = get_settings()
    return await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=user,
        password=password,
        database=database,
    )


async def owner_connect(database: str) -> asyncpg.Connection:
    """Owner connection to the maintenance database or to a probe — and to nothing else.

    Args:
        database: `MAINTENANCE_DATABASE`, or a probe name carrying `PROBE_PREFIX`.

    Returns:
        An open connection whose `current_database()` was verified for a probe target.

    Raises:
        ProbeDatabaseError: an illegitimate target, a server that refused the connection, or a
            connection that resolved to a database other than the requested one.
    """
    if database != MAINTENANCE_DATABASE:
        assert_probe_target(database)
    settings = get_settings()
    try:
        connection = await raw_connect(settings.postgres_user, settings.postgres_password, database)
    except (OSError, asyncpg.PostgresError) as error:
        raise ProbeDatabaseError(
            f"cannot open {database!r} on the configured server: {redact_secrets(str(error))}"
        ) from error
    if database != MAINTENANCE_DATABASE:
        await _assert_bound(connection, database)
    return connection


async def _assert_bound(connection: asyncpg.Connection, database: str) -> None:
    """Prove at the server that `connection` really landed on `database` (§12)."""
    bound = await connection.fetchval("SELECT current_database()")
    if bound != database:
        await connection.close()
        raise ProbeDatabaseError(
            f"connection opened for {database!r} is bound to {bound!r} — refusing to use it"
        )


async def execute_on_probe(name: str, statement: str, *args: object) -> None:
    """Run one owner statement against a probe and close the connection again.

    Raises:
        ProbeDatabaseError: the target is illegitimate or the server refused the statement.
    """
    connection = await owner_connect(name)
    try:
        await connection.execute(statement, *args)
    except asyncpg.PostgresError as error:
        raise ProbeDatabaseError(f"{name!r}: {redact_secrets(str(error))}") from error
    finally:
        await connection.close()


async def lifecycle_statement(statement: str, name: str, failure: str) -> None:
    """Run a `CREATE`/`DROP DATABASE` on the maintenance database, outside any transaction.

    Args:
        statement: The rendered lifecycle statement (the caller quoted `name` into it).
        name: The probe the statement targets — used only for the failure message.
        failure: The human prefix of the failure message, e.g. `"cannot create probe"`.

    Raises:
        ProbeDatabaseError: the server refused the statement (for a drop this is the §16.11
            infrastructure error — never a reason to escalate to `FORCE`).
    """
    connection = await owner_connect(MAINTENANCE_DATABASE)
    try:
        await connection.execute(statement)
    except asyncpg.PostgresError as error:
        raise ProbeDatabaseError(f"{failure} {name!r}: {redact_secrets(str(error))}") from error
    finally:
        await connection.close()


async def fetch_probe_names(connection: asyncpg.Connection) -> tuple[str, ...]:
    """Every `mem01_probe_*` database currently registered in `pg_database`, sorted."""
    records = await connection.fetch(
        "SELECT datname FROM pg_database WHERE datname LIKE $1 ORDER BY datname",
        f"{PROBE_PREFIX}%",
    )
    return tuple(record["datname"] for record in records)


async def can_log_in(role: str, password: str) -> bool:
    """True iff `role` can open a session on the maintenance database with `password`.

    Observation only: a role that cannot log in is REPORTED by the preflight, never repaired —
    the instrument provisions nothing (§12).
    """
    try:
        connection = await raw_connect(role, password, MAINTENANCE_DATABASE)
    except (OSError, asyncpg.PostgresError):
        return False
    await connection.close()
    return True


def process_is_alive(pid: int) -> bool:
    """True iff a process with `pid` runs on this host (the §16.4 reuse rule).

    `os.kill(pid, 0)` is NOT portable: on Windows it TERMINATES the target instead of probing
    it, so the Win32 branch opens a query-only handle and reads the exit code instead.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_process_is_alive(pid: int) -> bool:
    """The Win32 branch of `process_is_alive`: `OpenProcess` + `GetExitCodeProcess`."""
    import ctypes

    query_limited_information, still_active = 0x1000, 259
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]  # win32-only branch
    handle = kernel32.OpenProcess(query_limited_information, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        queried = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return bool(queried) and code.value == still_active
    finally:
        kernel32.CloseHandle(handle)
