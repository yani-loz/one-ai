"""
THE cross-tenant containment test for GDPR org erasure (CA-CONN-01 / CA-CONN-03), end-to-end
against the real ASGI app + DB + JWTs.

Seeds org A AND org B across EVERY wired PII table (users + the Connect tables + the entity
graph), erases A over HTTP, and proves: every A row in every table is GONE, every B row
SURVIVES untouched, and the deletion certificate's erased_rows_by_table reports exactly A's
per-table counts (B's rows must not inflate them). The erasure hooks run on the RLS-exempt
global session, so their own org-scoped SQL is the ONLY containment — this test is the proof
it holds. Split out of test_erasure_routes.py for the 500-line file ceiling. Requires
Postgres (identity_schema fixture; the Connect/entity tables are ensured + reset here).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.base_model import Base
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_sync_cursor import ConnectorSyncCursor
from app.connectors.models.connector_sync_run import ConnectorSyncRun
from app.core.database import engine
from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail
from app.identity.enums import UserRole
from tests.identity.conftest import (
    bearer,
    platform_token,
    seed_organization,
    seed_platform_admin,
    seed_user,
)

_ADMIN_PASSWORD = "Test-Pass-123"  # the seeded platform admin's password (sudo re-auth)
_REASON = "Customer offboarding per contract termination."

_CONNECT_ENTITY_TABLES = [
    Person.__table__,
    Company.__table__,
    PersonEmail.__table__,
    PersonAlias.__table__,
    CompanyDomain.__table__,
    PersonCompany.__table__,
    ConnectorConnection.__table__,
    ConnectorSyncCursor.__table__,
    ConnectorSyncRun.__table__,
    EmailMessage.__table__,
    EmailRecipient.__table__,
    EmailAttachment.__table__,
]

# One seeded row per PII table per org (see _seed_connect_entity_rows): the erase-A certificate
# must report EXACTLY these per-table counts.
_ONE_ROW_PER_PII_TABLE = {table.name: 1 for table in _CONNECT_ENTITY_TABLES}


def _missing_connect_entity_tables(sync_connection: object) -> list[object]:
    """Return the Connect/entity tables that don't yet exist on the connected database."""
    from sqlalchemy import inspect

    existing = set(inspect(sync_connection).get_table_names())
    return [table for table in _CONNECT_ENTITY_TABLES if table.name not in existing]


@pytest_asyncio.fixture
async def connect_entity_schema(db_session: AsyncSession) -> AsyncIterator[None]:
    """Ensure the Connect + entity-graph tables exist, then reset them (truncate pre-existing,
    drop created) — the same pattern as the connectors/entities conftests. Chained onto
    db_session (which chains identity_schema, so the runtime-roles skip still fires first)
    because this fixture's finalizer runs BEFORE db_session's (reverse setup order): the test's
    final SELECTs leave db_session idle-in-transaction holding ACCESS SHARE on email_message,
    and the TRUNCATE below would block forever on ACCESS EXCLUSIVE — one client, two sessions,
    a deadlock Postgres cannot detect. Teardown therefore releases db_session's transaction
    first; by then every write the test needs persisted has been committed by the test itself."""
    async with engine.begin() as connection:
        created = await connection.run_sync(_missing_connect_entity_tables)
        await connection.run_sync(Base.metadata.create_all, tables=created)
    try:
        yield
    finally:
        # Release db_session's open (read-only) transaction so the TRUNCATE can take its
        # ACCESS EXCLUSIVE locks. rollback (not commit) is safe in every session state,
        # including a test that failed mid-transaction.
        await db_session.rollback()
        async with engine.begin() as connection:
            pre_existing = [t for t in _CONNECT_ENTITY_TABLES if t not in created]
            if pre_existing:
                names = ", ".join(t.name for t in pre_existing)
                await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            if created:
                await connection.run_sync(Base.metadata.drop_all, tables=created)


async def _seed_org(session: AsyncSession, slug: str):
    """Seed an org + its company_admin + a platform admin; return (org, platform_admin)."""
    org = await seed_organization(session, name=slug.title(), slug=slug)
    await seed_user(
        session,
        org_id=org.id,
        email=f"admin@{slug}.example",
        full_name="Company Admin",
        role=UserRole.company_admin,
    )
    platform_admin = await seed_platform_admin(
        session, email=f"staff-{slug}@ethera.example", full_name="Staff"
    )
    return org, platform_admin


async def _seed_connect_entity_rows(session: AsyncSession, org_id: UUID, tag: str) -> None:
    """Seed ONE row per Connect + entity-graph PII table for `org_id` (values distinct per tag)."""
    person = Person(org_id=org_id, display_name=f"{tag} Person")
    company = Company(org_id=org_id, name=f"{tag} GmbH")
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
    session.add_all([person, company, connection])
    await session.flush()
    message = EmailMessage(
        org_id=org_id,
        connection_id=connection.id,
        dedup_key=f"dedup-{tag}",
        from_address=f"contact@{tag}.example",
        from_person_id=person.id,
        subject=f"{tag} subject",
        body_text=f"{tag} body",
    )
    session.add(message)
    await session.flush()
    session.add_all(
        [
            PersonEmail(org_id=org_id, person_id=person.id, email=f"contact@{tag}.example"),
            PersonAlias(org_id=org_id, person_id=person.id, alias=f"{tag} Alias"),
            CompanyDomain(org_id=org_id, company_id=company.id, domain=f"{tag}.example"),
            PersonCompany(org_id=org_id, person_id=person.id, company_id=company.id),
            # 0014 ck_sync_run_terminal_finished: a terminal status carries finished_at.
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
                org_id=org_id,
                email_id=message.id,
                kind="to",
                address=f"owner@{tag}.example",
                person_id=person.id,
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


async def test_erase_purges_every_org_a_pii_table_and_spares_every_org_b_row(
    client: AsyncClient, db_session: AsyncSession, connect_entity_schema: None
) -> None:
    org_a, platform_admin = await _seed_org(db_session, "acme")
    org_b, _admin_b = await _seed_org(db_session, "globex")
    await _seed_connect_entity_rows(db_session, org_a.id, "acme")
    await _seed_connect_entity_rows(db_session, org_b.id, "globex")
    await db_session.commit()

    response = await client.post(
        f"/platform/orgs/{org_a.id}/erase",
        json={"reason": _REASON, "confirm_slug": "acme", "password": _ADMIN_PASSWORD},
        headers=bearer(platform_token(platform_admin.id)),
    )

    assert response.status_code == 200
    # The certificate reports the hook deletes truthfully — exactly org A's rows, never B's.
    assert response.json()["erased_rows_by_table"] == _ONE_ROW_PER_PII_TABLE
    for table in [*_ONE_ROW_PER_PII_TABLE, "users"]:
        assert await _org_row_count(db_session, table, org_a.id) == 0, (
            f"{table}: an org A row survived erasure"
        )
        assert await _org_row_count(db_session, table, org_b.id) == 1, (
            f"{table}: an org B row was touched by org A's erasure"
        )
    # And B's content survives verbatim — untouched, not merely recounted.
    survivor = await db_session.execute(
        text("SELECT body_text FROM email_message WHERE org_id=:o"), {"o": str(org_b.id)}
    )
    assert survivor.scalar_one() == "globex body"
