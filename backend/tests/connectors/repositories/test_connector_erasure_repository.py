"""
Repo-layer tests for the Connect erasure hook (erase_connector_data_for_org) — real DB.

The cross-tenant negative is the headline: deleting org A's Connect rows must not touch ONE
org B row in ANY of the six tables (the hook's own org-scoped SQL is the only containment on
the RLS-exempt erasure session), and the returned per-table counts must be exactly A's rows.
Defines its own schema fixture (the suite conftest manages only connector_connection; this
hook also spans the sync + email tables, whose FKs need `person` to exist). Requires Postgres.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.base_model import Base
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_sync_cursor import ConnectorSyncCursor
from app.connectors.models.connector_sync_run import ConnectorSyncRun
from app.connectors.repositories.connector_erasure_repository import erase_connector_data_for_org
from app.core.database import GlobalSessionLocal, engine, runtime_roles_present
from app.entities.models.person import Person
from tests.conftest import register_org

# `person` is included only as an FK target of the email tables (created if missing; no rows
# are seeded into it here — the entity graph has its own erasure hook + tests).
_ERASURE_TABLES = [
    Person.__table__,
    ConnectorConnection.__table__,
    ConnectorSyncCursor.__table__,
    ConnectorSyncRun.__table__,
    EmailMessage.__table__,
    EmailRecipient.__table__,
    EmailAttachment.__table__,
]

_CONNECT_TABLE_NAMES = (
    "email_recipient",
    "email_attachment",
    "email_message",
    "connector_sync_cursor",
    "connector_sync_run",
    "connector_connection",
)


def _missing_tables(sync_connection: object) -> list[object]:
    """Return the tables this suite needs that don't yet exist on the connected database."""
    from sqlalchemy import inspect

    existing = set(inspect(sync_connection).get_table_names())
    return [table for table in _ERASURE_TABLES if table.name not in existing]


@pytest_asyncio.fixture
async def erasure_schema() -> AsyncIterator[None]:
    """Ensure the Connect erasure tables exist, then reset them (truncate pre-existing, drop
    created) — the same pattern as the suite's other DB conftests."""
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
            pre_existing = [t for t in _ERASURE_TABLES if t not in created]
            if pre_existing:
                names = ", ".join(t.name for t in pre_existing)
                await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            if created:
                await connection.run_sync(Base.metadata.drop_all, tables=created)


@pytest_asyncio.fixture
async def db_session(erasure_schema: None) -> AsyncIterator[AsyncSession]:
    """Committed-on-success session over the FULL Connect erasure schema (overrides the suite
    conftest's db_session, which manages connector_connection only)."""
    async with GlobalSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _seed_connect_rows(session: AsyncSession, org_id: UUID, tag: str) -> None:
    """Seed ONE row per Connect table for `org_id` (values distinct per `tag`).

    Registers the org row first (0014 org-root FK); the succeeded sync run carries
    finished_at (0014 ck_sync_run_terminal_finished: terminal status => finished).
    """
    await register_org(session, org_id)
    connection = ConnectorConnection(
        org_id=org_id,
        connector_type="imap",
        display_name=f"{tag} Mailbox",
        auth_method="app_password",
        username=f"owner@{tag}.example",
        config={"host": "mail.example", "port": 993, "use_ssl": True},
        secret_ciphertext=b"\x00" * 32,
        secret_key_version=1,
        status="configured",
    )
    session.add(connection)
    await session.flush()
    message = EmailMessage(
        org_id=org_id,
        connection_id=connection.id,
        dedup_key=f"dedup-{tag}",
        from_address=f"contact@{tag}.example",
        subject=f"{tag} subject",
        body_text=f"{tag} body",
    )
    session.add(message)
    await session.flush()
    session.add_all(
        [
            ConnectorSyncRun(
                org_id=org_id,
                connection_id=connection.id,
                run_id=uuid4(),
                status="succeeded",
                finished_at=datetime.now(UTC),
            ),
            ConnectorSyncCursor(
                org_id=org_id, connection_id=connection.id, folder="INBOX", last_seen_uid=10
            ),
            EmailRecipient(
                org_id=org_id, email_id=message.id, kind="to", address=f"owner@{tag}.example"
            ),
            EmailAttachment(
                org_id=org_id, email_id=message.id, filename=f"{tag}.txt", content_type="text/plain"
            ),
        ]
    )
    await session.flush()


async def _org_row_count(session: AsyncSession, table: str, org_id: UUID) -> int:
    """Count `table` rows belonging to `org_id` (table names are fixed, test-owned)."""
    result = await session.execute(
        text(f"SELECT count(*) FROM {table} WHERE org_id=:o"),  # noqa: S608 — fixed names
        {"o": str(org_id)},
    )
    return result.scalar_one()


async def test_erase_connector_data_for_org_deletes_a_and_spares_b(
    db_session: AsyncSession,
) -> None:
    # Cross-tenant negative: erasing A's Connect data leaves EVERY B row in EVERY table, and
    # the returned counts are exactly A's one-row-per-table seed (B never inflates them).
    org_a, org_b = uuid4(), uuid4()
    await _seed_connect_rows(db_session, org_a, "acme")
    await _seed_connect_rows(db_session, org_b, "globex")

    counts = await erase_connector_data_for_org(org_a, db_session)

    assert counts == {table: 1 for table in _CONNECT_TABLE_NAMES}
    for table in _CONNECT_TABLE_NAMES:
        assert await _org_row_count(db_session, table, org_a) == 0, f"{table}: org A survived"
        assert await _org_row_count(db_session, table, org_b) == 1, f"{table}: org B touched"


async def test_erase_connector_data_for_org_no_rows_returns_zero_counts(
    db_session: AsyncSession,
) -> None:
    # Idempotent on an org with no Connect data: all-zero per-table counts, no error.
    counts = await erase_connector_data_for_org(uuid4(), db_session)

    assert counts == {table: 0 for table in _CONNECT_TABLE_NAMES}
