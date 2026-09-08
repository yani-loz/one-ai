"""
Role: The Alembic child process that migrates a probe database (contract §12, §16.16(a) and
      fix-registry rows A1(a)/A10) — the allowlist-only environment the child receives, the
      annex-declared allowlist it is built from, and the migration run whose stdout and stderr
      are captured to a log file instead of reaching this process's streams.
Used by: tools.mem01_verify.probe_db (`create_probe_database` migrates an already-claimed probe
      through `migrate_probe_database`, and `claim_probe_database` reads a reused probe's head
      through `read_migration_head`; `child_environment` is re-exported there as the §16.16(a)
      public surface) and, through it, the sealed oracle test module
      backend/tests/tools/mem01_verify/test_probe_child_env.py.
Depends on: tools.mem01_verify.criteria (`load_criteria` — the annex `env_allowlist`),
      tools.mem01_verify.run_identity (`PACKAGED_CRITERIA_PATH`, the one handle on the annex
      bytes), tools.mem01_verify.probe_conn (`owner_connect`, `redact_secrets`),
      tools.mem01_verify.exceptions, app.core.config, and the standard library's `subprocess`.
Key invariants:
  - The child's environment is built ONLY by `child_environment`: the allowlisted names present
    in the parent (values passed through unchanged) plus the three explicit overrides. The
    parent's own `POSTGRES_DB` never survives — the child always targets the probe.
  - The child's stdout/stderr NEVER reach this process's streams. On failure they are written,
    redacted, to the caller's log path and the refusal says only `probe migration failed`.
  - The migration child is OWNED: it is spawned with `Popen` and waited for in a worker thread,
    and on ANY `BaseException` — a cancellation included — it is killed AND reaped before the
    exception is re-raised (§16.17(l)), so the probe's FORCE-less drop never meets a live
    Alembic connection and a cancellation surfaces as itself, never as a masked
    `ProbeDatabaseError`.
  - The parent environment is never copied wholesale: only allowlisted names are read from
    it, so the §3.11 input observer records those names and no others.
  - The probe the child migrates ALREADY carries the §16.4 `mem01_probe_owner` marker table
    (§16.16(s), written before this runs). That table is not part of `Base.metadata`, so the
    migration chain — whose every step names its own objects — neither reads nor drops it; the
    only migration reaching every table, 0009's `GRANT ... ON ALL TABLES`, merely widens the
    marker's grants inside a database that is thrown away.
  - This module launches the child; it never decides the probe's lifecycle (that is `probe_db`).
"""

from __future__ import annotations

import asyncio
import os
import subprocess  # noqa: S404 - the Alembic child process IS the §12 safety boundary
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path

import asyncpg

from app.core.config import get_settings
from tools.mem01_verify.criteria import load_criteria
from tools.mem01_verify.exceptions import ProbeDatabaseError
from tools.mem01_verify.probe_conn import owner_connect, redact_secrets
from tools.mem01_verify.run_identity import PACKAGED_CRITERIA_PATH

MIGRATION_TIMEOUT_SECONDS = 900.0

MIGRATION_LOG_NAME = "probe_migration.log"
"""The log file name a caller that names no report directory gets, beside the maintenance cwd."""

MIGRATION_FAILED = "probe migration failed"
"""The §16.16(a) abort reason — the child's own output never joins it (A10)."""

# backend/ — derived from this file, never from the working directory: the runner is invoked
# both as `python -m tools.mem01_verify.verify_step1` (cwd backend/) and as a script path from
# the repository root (§3.1).
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def child_environment(
    probe_name: str,
    parent: Mapping[str, str],
    allowlist: Sequence[str],
    *,
    host: str,
    port: str,
) -> dict[str, str]:
    """Build the environment of a child process from the annex allowlist alone (§16.16(a)).

    Pure: it reads only `parent` (never the process environment of its own accord), writes
    nothing back, and leaves `parent` untouched.

    Args:
        probe_name: The probe the child must target — becomes `POSTGRES_DB`.
        parent: This process's environment (or any mapping standing in for it).
        allowlist: The annex `env_allowlist` names; a name absent from `parent` stays absent.
        host: The configured server host — becomes `POSTGRES_HOST`.
        port: The configured server port, already rendered — becomes `POSTGRES_PORT`.

    Returns:
        A fresh mapping whose keys are exactly the allowlisted names present in `parent` plus
        `POSTGRES_DB`, `POSTGRES_HOST` and `POSTGRES_PORT`; allowlisted values pass through
        unchanged and the three overrides win over any parent value of the same name.
    """
    selected = {name: parent[name] for name in allowlist if name in parent}
    selected["POSTGRES_DB"] = probe_name
    selected["POSTGRES_HOST"] = host
    selected["POSTGRES_PORT"] = port
    return selected


def annex_env_allowlist() -> tuple[str, ...]:
    """Return the `env_allowlist` of the annex the instrument ships (§4.5).

    The declared list — never a trace of what was read — is the only source of names a child
    process may inherit, so it is loaded from the packaged annex rather than assembled here.
    """
    return tuple(load_criteria(PACKAGED_CRITERIA_PATH).env_allowlist)


async def migrate_probe_database(name: str, *, migration_log: Path | None) -> str:
    """Run `alembic upgrade head` against `name` in a CHILD process; return the head revision.

    The child's environment names the probe and the configured server explicitly: the parent's
    `.env` may carry a different port and its settings are cached process-wide — which is
    exactly why the migration cannot run in-process (§12).

    Args:
        name: The probe database to migrate — already proven a legitimate target and already
            carrying its ownership marker (§16.16(s)), which the chain leaves alone.
        migration_log: Where the child's captured output goes when it fails; `None` writes
            `probe_migration.log` beside the maintenance working directory.

    Returns:
        The single `alembic_version` revision the migrated probe carries.

    Raises:
        ProbeDatabaseError: the child failed or timed out (`probe migration failed`, its output
            in the log file), or the migrated database reports no head.
        BaseException: anything else raised while waiting — a cancellation above all — is
            re-raised UNCHANGED after the child is killed and reaped (§16.17(l)).
    """
    child = _spawn_migration_child(name)
    try:
        stdout, stderr = await asyncio.to_thread(
            child.communicate, timeout=MIGRATION_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as expired:
        timed_out, timed_err = _kill_and_collect(child, expired)
        _write_migration_log(migration_log, name, timed_out, timed_err, "timed out")
        raise ProbeDatabaseError(MIGRATION_FAILED) from expired
    except BaseException:  # a cancellation must not leave the child holding a connection
        child.kill()
        child.wait()
        raise
    if child.returncode != 0:
        _write_migration_log(migration_log, name, stdout, stderr, f"exit {child.returncode}")
        raise ProbeDatabaseError(MIGRATION_FAILED)
    return await read_migration_head(name)


def _spawn_migration_child(name: str) -> subprocess.Popen[bytes]:
    """Launch `alembic upgrade head` against `name` with the §16.16(a) minimal environment.

    Synchronous on purpose: the spawn is what the caller must OWN, so it happens before the
    first await and the returned handle is the only way the child is ever reaped.

    Args:
        name: The probe database the child must target — it becomes `POSTGRES_DB`.

    Returns:
        The running child, its stdout and stderr piped so neither reaches this process.
    """
    settings = get_settings()
    # `os.environ` is passed as-is, never copied: a copy would read EVERY name through
    # `os._Environ.__getitem__` and the §3.11 observer would record the whole environment.
    environment = child_environment(
        name,
        os.environ,
        annex_env_allowlist(),
        host=settings.postgres_host,
        port=str(settings.postgres_port),
    )
    argv = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(BACKEND_ROOT / "alembic.ini"),
        "upgrade",
        "head",
    ]
    return subprocess.Popen(  # noqa: S603 - argv is built here, never from caller input
        argv,
        cwd=str(BACKEND_ROOT),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _kill_and_collect(
    child: subprocess.Popen[bytes], expired: subprocess.TimeoutExpired
) -> tuple[bytes | str | None, bytes | str | None]:
    """Kill a child that overran its timeout and collect whatever it had already written.

    The second `communicate()` returns the COMPLETE captured streams, so the partial output
    `TimeoutExpired` carries is used only when that collection itself fails.
    """
    child.kill()
    try:
        return child.communicate()
    except (OSError, ValueError):
        return expired.stdout, expired.stderr


def _write_migration_log(
    migration_log: Path | None,
    name: str,
    stdout: bytes | str | None,
    stderr: bytes | str | None,
    outcome: str,
) -> None:
    """Write the failed child's captured output to the log path, redacted; never raises.

    A log that cannot be written must not mask the migration failure itself, so an unwritable
    path is swallowed here and the caller still refuses with `probe migration failed`.
    """
    path = migration_log if migration_log is not None else Path(MIGRATION_LOG_NAME)
    body = (
        f"alembic upgrade head for {name!r} {outcome}\n"
        f"--- stdout ---\n{_as_text(stdout)}\n"
        f"--- stderr ---\n{_as_text(stderr)}\n"
    )
    with suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(redact_secrets(body), encoding="utf-8")


def _as_text(stream: bytes | str | None) -> str:
    """Decode one captured child stream; a stream the child never wrote reads as empty."""
    if stream is None:
        return ""
    return stream if isinstance(stream, str) else stream.decode("utf-8", errors="replace")


async def read_migration_head(name: str) -> str:
    """Return the single `alembic_version` revision a probe carries.

    Raises:
        ProbeDatabaseError: the probe has no `alembic_version` table or reports no revision.
    """
    connection = await owner_connect(name)
    try:
        revision = await connection.fetchval("SELECT version_num FROM alembic_version")
    except asyncpg.PostgresError as error:
        raise ProbeDatabaseError(f"{name!r} has no alembic_version") from error
    finally:
        await connection.close()
    if not isinstance(revision, str) or not revision:
        raise ProbeDatabaseError(f"{name!r} reports no migration head")
    return revision
