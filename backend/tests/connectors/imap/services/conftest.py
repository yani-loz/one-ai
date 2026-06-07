"""
Role: DB fixtures for the email ingest-service tests — the FULL set of tables one ingest touches
      (connector_connection + the shared entity graph + the email Layer-1 tables) + a committed
      session + a connection seed helper.
Used by: tests under tests/connectors/imap/services/ (the EmailIngestService end-to-end path).
Depends on: app.core.database, the connector + entity + email models.
Key invariants:
  - Ingest = parse + resolve + store, so person/person_email/company/company_domain/person_company
    AND email_message/recipient/attachment AND connector_connection must all exist for one test.
  - Function-scoped; creates missing tables, TRUNCATE ... CASCADE on teardown.
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
from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail

# Parents before children (create order); TRUNCATE ... CASCADE handles FK order on teardown.
_INGEST_TABLES = [
    ConnectorConnection.__table__,
    Person.__table__,
    Company.__table__,
    PersonEmail.__table__,
    PersonAlias.__table__,
    CompanyDomain.__table__,
    PersonCompany.__table__,
    EmailMessage.__table__,
    EmailRecipient.__table__,
    EmailAttachment.__table__,
]


def _missing_tables(sync_connection: object) -> list[object]:
    from sqlalchemy import inspect

    existing = set(inspect(sync_connection).get_table_names())
    return [table for table in _INGEST_TABLES if table.name not in existing]


@pytest_asyncio.fixture
async def ingest_schema() -> AsyncIterator[None]:
    """Give each test the full ingest schema, then reset it (truncate or drop)."""
    if not await runtime_roles_present():
        pytest.skip(
            "Runtime DB roles missing — run `alembic upgrade head` then "
            "`python -m scripts.provision_roles` before the DB suite."
        )
    async with engine.begin() as connection:
        created = await connection.run_sync(_missing_tables)
        await connection.run_sync(Base.metadata.create_all, tables=created)
    try:
        yield
    finally:
        async with engine.begin() as connection:
            pre_existing = [t for t in _INGEST_TABLES if t not in created]
            if pre_existing:
                names = ", ".join(t.name for t in pre_existing)
                await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            if created:
                await connection.run_sync(Base.metadata.drop_all, tables=created)


@pytest_asyncio.fixture
async def db_session(ingest_schema: None) -> AsyncIterator[AsyncSession]:
    """Yield a committed-on-success session for the ingest-service tests."""
    async with GlobalSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def seed_connection(
    session: AsyncSession, org_id: UUID, mailbox: str = "owner@acme.com"
) -> ConnectorConnection:
    """Insert a minimal connector_connection (opaque ciphertext) so emails can FK it."""
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
    )
    session.add(connection)
    await session.flush()
    return connection
