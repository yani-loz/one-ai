"""
Role: Alembic migration environment — async (asyncpg) engine, online + offline modes.
Used by: the `alembic` CLI (upgrade/downgrade/revision).
Depends on: app.core.config (DB URL), app.common.base_model (target metadata), and every domain's
            model package (each registers its tables on Base.metadata): app.identity.models,
            app.entities.models, app.connectors.models, app.connectors.imap.models.
Key invariants:
  - Uses the SAME async driver as the app (asyncpg); no second sync driver needed.
  - target_metadata = Base.metadata so autogenerate sees every imported model.
    Import new model modules here as domains are added.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.connectors.imap.models  # noqa: F401  (registers email Layer-1 tables on Base.metadata)
import app.connectors.models  # noqa: F401  (registers connector tables on Base.metadata)
import app.entities.models  # noqa: F401  (registers entity-graph tables on Base.metadata)
import app.identity.models  # noqa: F401  (registers identity tables on Base.metadata)
from app.common.base_model import Base
from app.core.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the runtime DB URL (single source of truth: app settings).
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without a DB connection (offline mode)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live async connection."""
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
