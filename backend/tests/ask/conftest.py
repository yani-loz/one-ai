"""
Role: DB fixtures + seed helpers for the Ask shared-core tool tests — the tables one retrieval
      call touches (org root + connection + person/person_email + email content + the acl_grant
      that makes a restricted row visible to the reader plane) and explicit seed helpers (seed
      data stays visible in each test, per testing.md).
Used by: the DB-backed tests under tests/ask/tools/ — test_email_search.py,
      test_person_and_isolation.py, test_read_tools_isolation.py, test_registry_dispatch.py.
Depends on: app.core.database, the access + connector + entity + email models, tests.conftest
            (register_org for the 0014 org-root FK).
Key invariants:
  - Function-scoped; creates missing tables, TRUNCATE ... CASCADE on teardown (the migrated-DB
    pattern shared with tests/access + the other DB suites).
  - shared_core runs on the REAL reader plane (reader_session): org RLS + the PF-01 `visibility`
    policy both apply, so every seeded email is granted to the reader person — otherwise the
    reader sees ZERO rows and a passing test would prove nothing. Recipients/attachments inherit
    the parent message's grant (AC22), so they are never granted separately.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Table, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.models.acl_grant import AclGrant
from app.common.base_model import Base
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.models.connector_connection import ConnectorConnection
from app.core.database import GlobalSessionLocal, engine, runtime_roles_present
from app.entities.models.person import Person, PersonEmail

# Parents before children (create order); TRUNCATE ... CASCADE handles FK order on teardown.
# The isinstance filter narrows each Model.__table__ (typed FromClause) to a concrete Table, so
# the list is list[Table] — every entry IS a Table at runtime, nothing is dropped and order holds.
_ASK_TABLES: list[Table] = [
    table
    for table in (
        ConnectorConnection.__table__,
        Person.__table__,
        PersonEmail.__table__,
        EmailMessage.__table__,
        EmailRecipient.__table__,
        EmailAttachment.__table__,
        AclGrant.__table__,
    )
    if isinstance(table, Table)
]


def _missing_tables(sync_connection: Connection) -> list[Table]:
    """Return the _ASK_TABLES not yet present in the connected database (create order kept)."""
    inspector = inspect(sync_connection)
    existing = set(inspector.get_table_names())
    return [table for table in _ASK_TABLES if table.name not in existing]


@pytest_asyncio.fixture
async def ask_schema() -> AsyncIterator[None]:
    """Give each test the shared-core retrieval schema, then reset it (truncate or drop)."""
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
            pre_existing = [t for t in _ASK_TABLES if t not in created]
            if pre_existing:
                names = ", ".join(t.name for t in pre_existing)
                await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            if created:
                await connection.run_sync(Base.metadata.drop_all, tables=created)


@pytest_asyncio.fixture
async def db_session(ask_schema: None) -> AsyncIterator[AsyncSession]:
    """Yield a committed-on-success BYPASSRLS session for seeding + direct assertions."""
    async with GlobalSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _seed_connection(
    session: AsyncSession, org_id: UUID, *, mailbox: str = "owner@acme.test"
) -> ConnectorConnection:
    """Insert a minimal connector_connection (registers the org first — 0014 org-root FK)."""
    from tests.conftest import register_org

    await register_org(session, org_id)
    connection = ConnectorConnection(
        org_id=org_id,
        connector_type="imap",
        owner_user_id=None,
        display_name="Ask Test Mailbox",
        auth_method="app_password",
        username=mailbox,
        config={"host": "mail.example.test", "port": 993, "use_ssl": True},
        secret_ciphertext=b"\x00" * 32,
        secret_key_version=1,
        status="configured",
    )
    session.add(connection)
    await session.flush()
    return connection


async def _seed_person(
    session: AsyncSession, org_id: UUID, display_name: str, *, emails: tuple[str, ...] = ()
) -> Person:
    """Insert a person (+ optional linked person_email match keys). Registers the org first."""
    from tests.conftest import register_org

    await register_org(session, org_id)
    person = Person(org_id=org_id, display_name=display_name, is_internal=True)
    session.add(person)
    await session.flush()
    for email in emails:
        session.add(
            PersonEmail(org_id=org_id, person_id=person.id, email=email.lower(), source="imap")
        )
    await session.flush()
    return person


async def _seed_email(
    session: AsyncSession,
    org_id: UUID,
    connection_id: UUID,
    reader_person_id: UUID,
    *,
    from_address: str,
    from_name: str | None = None,
    subject: str | None = "",  # None = a subject-less message (degraded parse — real case)
    sent_at: datetime | None = None,
    direction: str | None = None,
    body_text: str | None = None,
    recipients: tuple[tuple[str, str, str | None], ...] = (),
) -> EmailMessage:
    """Insert one email (+ its recipients) and GRANT the reader person access.

    recipients: tuple of (kind, address, name) rows. The acl_grant is what makes a RESTRICTED
    row visible on the reader plane; without it the reader session sees nothing (fail-closed).

    Every seeded row is RESTRICTED. An org-scoped email would leave `org_isolation` as the only
    policy between two tenants, which is the cleaner way to pin it — but PF-01 forbids writing
    one directly: `ck_email_message_origin_scope` and the AC5 promotion-lineage guard both
    reject a widened row with no promotion behind it. So org isolation is pinned where it
    stands unaided instead: the person graph (person, person_email) carries org isolation
    ALONE, and the cross-tenant `find_person` test in test_person_and_isolation.py is that pin.
    """
    visibility_scope = "restricted"
    message = EmailMessage(
        org_id=org_id,
        connection_id=connection_id,
        visibility_scope=visibility_scope,
        # origin_scope is CHECK-pinned to 'restricted': it records where the row CAME FROM (a
        # restricted source, always), while visibility_scope is what promotion widens.
        origin_scope="restricted",
        container_id=connection_id,
        dedup_key=f"ask-{uuid4()}",
        from_address=from_address,
        from_name=from_name,
        subject=subject,
        sent_at=sent_at,
        direction=direction,
        body_text=body_text,
        headers={},
    )
    session.add(message)
    await session.flush()
    for kind, address, name in recipients:
        session.add(
            EmailRecipient(
                org_id=org_id,
                email_id=message.id,
                visibility_scope=visibility_scope,
                origin_scope="restricted",
                container_id=connection_id,
                kind=kind,
                address=address,
                name=name,
            )
        )
    session.add(
        AclGrant(
            org_id=org_id,
            person_id=reader_person_id,
            object_type="email_message",
            object_id=message.id,
            connection_id=connection_id,
            provenance="recipient",
        )
    )
    await session.flush()
    return message


async def _seed_attachment(
    session: AsyncSession,
    org_id: UUID,
    message: EmailMessage,
    *,
    filename: str = "document.pdf",
    content_type: str = "application/pdf",
    extracted_text: str | None = "Document body text.",
    is_inline: bool = False,
) -> EmailAttachment:
    """Insert one attachment on a seeded message.

    Attachments inherit the parent message's acl_grant (AC22), so no separate grant is
    written — that inheritance is part of what these tests exercise.
    """
    attachment = EmailAttachment(
        org_id=org_id,
        email_id=message.id,
        visibility_scope=message.visibility_scope,
        origin_scope=message.origin_scope,
        container_id=message.container_id,
        filename=filename,
        content_type=content_type,
        size_bytes=1024,
        is_inline=is_inline,
        extracted_text=extracted_text,
        extraction_status="extracted" if extracted_text else "pending",
    )
    session.add(attachment)
    await session.flush()
    return attachment


# Seed helpers are exposed as factory fixtures (not imported) so no test module imports another
# test module (testing.md: share via fixtures, not module-level imports). Each fixture returns the
# corresponding async seeder; tests inject it by name and call it with the seam session.
ConnectionSeeder = Callable[..., Awaitable[ConnectorConnection]]
PersonSeeder = Callable[..., Awaitable[Person]]
EmailSeeder = Callable[..., Awaitable[EmailMessage]]
AttachmentSeeder = Callable[..., Awaitable[EmailAttachment]]


@pytest.fixture
def seed_connection() -> ConnectionSeeder:
    """Factory fixture returning the connector_connection seeder."""
    return _seed_connection


@pytest.fixture
def seed_person() -> PersonSeeder:
    """Factory fixture returning the person seeder."""
    return _seed_person


@pytest.fixture
def seed_email() -> EmailSeeder:
    """Factory fixture returning the granted-restricted-email seeder."""
    return _seed_email


@pytest.fixture
def seed_attachment() -> AttachmentSeeder:
    """Factory fixture returning the attachment seeder (inherits its message's grant)."""
    return _seed_attachment
