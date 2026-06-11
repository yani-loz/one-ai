"""
Role: DB fixtures for the SyncRunner persistence/runner tests — the three tables the sync path
      touches (connector_connection + connector_sync_cursor + connector_sync_run) + a committed
      session + a connection seed helper.
Used by: tests under tests/connectors/sync/.
Depends on: app.core.database, the connector + sync models.
Key invariants:
  - Function-scoped; creates missing tables, TRUNCATE ... CASCADE on teardown. SKIPs (not fails)
    when the runtime DB roles aren't provisioned, matching the other DB conftests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.base_model import Base
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_sync_cursor import ConnectorSyncCursor
from app.connectors.models.connector_sync_run import ConnectorSyncRun
from app.core.database import GlobalSessionLocal, engine, runtime_roles_present
from tests.conftest import register_org

_SYNC_TABLES = [
    ConnectorConnection.__table__,
    ConnectorSyncCursor.__table__,
    ConnectorSyncRun.__table__,
]


def _missing(sync_connection: object) -> list[object]:
    from sqlalchemy import inspect

    existing = set(inspect(sync_connection).get_table_names())
    return [table for table in _SYNC_TABLES if table.name not in existing]


@pytest_asyncio.fixture
async def sync_schema() -> AsyncIterator[None]:
    """Give each test the sync schema (connection + cursor + run), then reset it."""
    if not await runtime_roles_present():
        pytest.skip(
            "Runtime DB roles missing — run `alembic upgrade head` then "
            "`python -m scripts.provision_roles` before the DB suite."
        )
    async with engine.begin() as connection:
        created = await connection.run_sync(_missing)
        await connection.run_sync(Base.metadata.create_all, tables=created)
    try:
        yield
    finally:
        async with engine.begin() as connection:
            pre_existing = [t for t in _SYNC_TABLES if t not in created]
            if pre_existing:
                names = ", ".join(t.name for t in pre_existing)
                await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            if created:
                await connection.run_sync(Base.metadata.drop_all, tables=created)


@pytest_asyncio.fixture
async def db_session(sync_schema: None) -> AsyncIterator[AsyncSession]:
    """Yield a committed-on-success session for the sync persistence/runner tests."""
    async with GlobalSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def seed_connection(
    session: AsyncSession, org_id: UUID, *, mailbox: str = "owner@acme.com", disabled: bool = False
) -> ConnectorConnection:
    """Insert a minimal connector_connection (sync_* take their idle server defaults).

    Registers the org row first (idempotent) — 0014's org-root FK rejects phantom tenants.
    """
    from datetime import UTC, datetime

    await register_org(session, org_id)
    connection = ConnectorConnection(
        org_id=org_id,
        connector_type="imap",
        display_name="Mailbox",
        auth_method="app_password",
        username=mailbox,
        config={"host": "mail.example.com", "port": 993, "use_ssl": True},
        secret_ciphertext=b"\x00" * 32,
        secret_key_version=1,
        status="configured",
        disabled_at=datetime.now(UTC) if disabled else None,
    )
    session.add(connection)
    await session.flush()
    return connection
