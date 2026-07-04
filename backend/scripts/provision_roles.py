"""
Role: Provision the least-privilege runtime DB roles' LOGIN credentials (app / global / the
      PF-01 reader), out-of-band.
Used by: the container start command + CI — run AFTER `alembic upgrade head` and BEFORE
         uvicorn/pytest. Idempotent (ALTER ROLE), safe to re-run on every boot.
Depends on: asyncpg (already a dependency), app.core.config (the single source of role passwords).

Why this exists:
  - Migration 0009 CREATEs `oneai_app` / `oneai_global` with the correct privilege attributes but
    as NOLOGIN — because a role password must NEVER live in a VCS-tracked migration. The migration
    sets the *shape* of the roles; THIS script sets each role's LOGIN + password from the SAME value
    the app connects with (config.oneai_app_password / oneai_global_password), so the provisioned
    password and the connection password can never drift (FIX_BEFORE_PROD fix #4).
  - asyncpg, not psql: the backend image ships no psql client. `ALTER ROLE ... PASSWORD` takes no
    bind parameter, so the password is a single-quote-escaped SQL literal — and it is only ever a
    config-supplied value, never user input.

Key invariants:
  - Connects as the OWNER/superuser role — the only role permitted to ALTER other roles.
  - Fails LOUDLY if a target role is missing (migration 0009 not yet applied) — not a silent no-op.
"""

from __future__ import annotations

import asyncio

import asyncpg

from app.core.config import get_settings


def _quote_literal(value: str) -> str:
    """Return `value` as a single-quoted Postgres string literal (doubling embedded quotes).

    `ALTER ROLE ... PASSWORD` cannot take a bind parameter, so the password is interpolated as an
    inline literal. The value is always config-sourced (never user input); doubling `'` is the
    standard, injection-safe Postgres string-literal escaping.
    """
    return "'" + value.replace("'", "''") + "'"


async def _provision() -> None:
    """Set LOGIN + password on each runtime role, connected as the owner."""
    settings = get_settings()
    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )
    try:
        roles = {
            settings.app_db_user: settings.oneai_app_password,
            settings.global_db_user: settings.oneai_global_password,
            settings.reader_db_user: settings.oneai_reader_password,
        }
        for role, password in roles.items():
            exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
            if not exists:
                raise SystemExit(
                    f"Role {role!r} does not exist. Run `alembic upgrade head` first "
                    f"(migrations 0009/0019 create the runtime roles), then provision "
                    f"their passwords."
                )
            # role names are fixed config values (identifier-quoted); the password is a quoted
            # literal because ALTER ROLE takes no bind params. LOGIN flips the migration's NOLOGIN
            # role into a connectable one without putting the secret in the migration.
            await conn.execute(
                f'ALTER ROLE "{role}" WITH LOGIN PASSWORD {_quote_literal(password)}'
            )
            print(f"Provisioned LOGIN for role {role!r}.")
    finally:
        await conn.close()


def main() -> None:
    """Entry point for `python -m scripts.provision_roles`."""
    asyncio.run(_provision())


if __name__ == "__main__":
    main()
