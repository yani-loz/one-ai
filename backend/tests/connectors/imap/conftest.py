"""
Role: DB fixtures for the IMAP email Layer-1 tests — schema (email tables + their FK targets) +
      a committed session + a connection seed helper.
Used by: tests under tests/connectors/imap/ that exercise the email repository against a real DB.
Depends on: app.core.database, app.connectors.imap.models, app.connectors.models (the FK target
            connector_connection), app.entities.models (the FK target person).
Key invariants:
  - email_message FKs `connector_connection` + `person`; both needed for the email DDL. They're in
    the create/teardown set (truncated, never seeded with identity data).
  - Function-scoped (per-test event loop); TRUNCATE ... CASCADE clears emails + their parents.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.base_model import Base
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.models.connector_connection import ConnectorConnection
from app.core.database import GlobalSessionLocal, engine, runtime_roles_present
from app.entities.models.person import Person

# Email tables FK connector_connection + person, so those FK targets are in the create set too.
_EMAIL_SCHEMA_TABLES = [
    ConnectorConnection.__table__,
    Person.__table__,
    EmailMessage.__table__,
    EmailRecipient.__table__,
    EmailAttachment.__table__,
]


def _missing_email_tables(sync_connection: object) -> list[object]:
    """Return the email-schema tables that don't yet exist on the connected database."""
    from sqlalchemy import inspect

    existing = set(inspect(sync_connection).get_table_names())
    return [table for table in _EMAIL_SCHEMA_TABLES if table.name not in existing]


@pytest_asyncio.fixture
async def email_schema() -> AsyncIterator[None]:
    """Give each test a clean email schema (+ FK targets), then reset it."""
    if not await runtime_roles_present():
        pytest.skip(
            "Runtime DB roles missing — run `alembic upgrade head` then "
            "`python -m scripts.provision_roles` before the DB suite."
        )
    async with engine.begin() as connection:
        created = await connection.run_sync(_missing_email_tables)
        await connection.run_sync(Base.metadata.create_all, tables=created)
    try:
        yield
    finally:
        async with engine.begin() as connection:
            pre_existing = [t for t in _EMAIL_SCHEMA_TABLES if t not in created]
            if pre_existing:
                names = ", ".join(t.name for t in pre_existing)
                await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            if created:
                await connection.run_sync(Base.metadata.drop_all, tables=created)


@pytest_asyncio.fixture
async def db_session(email_schema: None) -> AsyncIterator[AsyncSession]:
    """Yield a committed-on-success plain session for direct email-repository tests."""
    async with GlobalSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def seed_connection(
    session: AsyncSession, org_id: UUID, username: str = "mailbox@example.com"
) -> ConnectorConnection:
    """Insert a minimal connector_connection (ciphertext is opaque here) so emails can FK it."""
    connection = ConnectorConnection(
        org_id=org_id,
        connector_type="imap",
        display_name="Mailbox",
        auth_method="app_password",
        username=username,
        config={"host": "mail.example.com", "port": 993, "use_ssl": True},
        secret_ciphertext=b"\x00" * 32,
        secret_key_version=1,
        status="configured",
    )
    session.add(connection)
    await session.flush()
    return connection
