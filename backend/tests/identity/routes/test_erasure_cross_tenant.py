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

from app.access.models.acl_grant import AclGrant
from app.access.models.fact_provenance import FactProvenance
from app.access.models.principal_source_identity import PrincipalSourceIdentity
from app.access.models.visibility_promotion import VisibilityPromotion
from app.common.base_model import Base
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.models.connector_connection import ConnectorConnection
from app.connectors.models.connector_consent import ConnectorConsent
from app.connectors.models.connector_entitlement import ConnectorEntitlement
from app.connectors.models.connector_policy import ConnectorPolicy
from app.connectors.models.connector_policy_override import ConnectorPolicyOverride
from app.connectors.models.connector_sync_cursor import ConnectorSyncCursor
from app.connectors.models.connector_sync_run import ConnectorSyncRun
from app.core.database import engine
from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail
from app.identity.enums import UserRole
from app.identity.models.audit_log import AuditLog
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
    # CO-01 authorization tables — the Connect erasure hook now deletes these too, so the
    # certificate counts include them; seed one row per org so the cross-tenant negative is real.
    ConnectorConsent.__table__,
    ConnectorPolicyOverride.__table__,
    ConnectorPolicy.__table__,
    ConnectorEntitlement.__table__,
    # PF-01 access tables — the access erasure hook (registered FIRST) deletes + counts these.
    AclGrant.__table__,
    PrincipalSourceIdentity.__table__,
    VisibilityPromotion.__table__,
    FactProvenance.__table__,
]

# One seeded row per PII table per org (see _seed_connect_entity_rows). Used for the row-SURVIVAL
# loop: after erasing A, every one of these tables must have 0 A-rows and 1 (untouched) B-row.
_ONE_ROW_PER_PII_TABLE = {table.name: 1 for table in _CONNECT_ENTITY_TABLES}

# The deletion CERTIFICATE now counts EVERY PII table honestly (one row each): the ErasureService
# runs the feature erasure hooks BEFORE deleting users (2026-06-15 cross-vendor review P2), so the
# Connect hook explicitly deletes + counts connector_consent / connector_policy_override itself,
# rather than having them cascade-erased uncounted by the user delete. The user delete then has
# nothing connector-related left to cascade.
_EXPECTED_CERTIFICATE_COUNTS = dict(_ONE_ROW_PER_PII_TABLE)


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
    """Seed an org + its company_admin + a platform admin; return (org, platform_admin, admin_id).

    The returned admin id is reused as the CO-01 consent/override FK target (one user per org).
    """
    org = await seed_organization(session, name=slug.title(), slug=slug)
    admin = await seed_user(
        session,
        org_id=org.id,
        email=f"admin@{slug}.example",
        full_name="Company Admin",
        role=UserRole.company_admin,
    )
    platform_admin = await seed_platform_admin(
        session, email=f"staff-{slug}@ethera.example", full_name="Staff"
    )
    return org, platform_admin, admin.id


async def _seed_connect_entity_rows(
    session: AsyncSession, org_id: UUID, tag: str, user_id: UUID
) -> None:
    """Seed ONE row per Connect + entity-graph PII table for `org_id` (values distinct per tag).

    `user_id` is the org's existing company_admin (NOT a new user — the certificate asserts exactly
    one users row per org), reused as the FK target for the CO-01 consent + per-user override rows.
    """
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
            ConnectorConsent(
                org_id=org_id,
                user_id=user_id,
                connector_type="imap",
                scope="mailbox:read",
                method="app_password",
            ),
            ConnectorPolicyOverride(
                org_id=org_id, user_id=user_id, connector_type="imap", override_type="grant"
            ),
            ConnectorPolicy(org_id=org_id, connector_type="imap", org_wide_enabled=True),
            ConnectorEntitlement(org_id=org_id, connector_type="imap", enabled=True),
        ]
    )
    await session.flush()
    # PF-01 access rows (one per table): a grant on the message, the person's verified identity,
    # a promotion lineage row (anchored to a real audit row), and a synthetic fact anchor.
    audit_anchor = AuditLog(
        actor_type="user",
        actor_id=user_id,
        action="access.visibility_promoted",
        org_id=org_id,
        details={},
    )
    session.add(audit_anchor)
    await session.flush()
    session.add_all(
        [
            AclGrant(
                org_id=org_id,
                person_id=person.id,
                object_type="email_message",
                object_id=message.id,
                connection_id=connection.id,
                provenance="recipient",
            ),
            PrincipalSourceIdentity(
                org_id=org_id,
                person_id=person.id,
                source_type="email",
                external_id=f"contact@{tag}.example",
                verified=True,
            ),
            VisibilityPromotion(
                org_id=org_id,
                object_type="email_message",
                object_id=message.id,
                approved_by_user_id=user_id,
                audit_log_id=audit_anchor.id,
            ),
            FactProvenance(
                org_id=org_id,
                fact_id=uuid4(),
                source_object_type="email_message",
                source_object_id=message.id,
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
    org_a, platform_admin, admin_a = await _seed_org(db_session, "acme")
    org_b, _admin_b, admin_b = await _seed_org(db_session, "globex")
    await _seed_connect_entity_rows(db_session, org_a.id, "acme", admin_a)
    await _seed_connect_entity_rows(db_session, org_b.id, "globex", admin_b)
    await db_session.commit()

    response = await client.post(
        f"/platform/orgs/{org_a.id}/erase",
        json={"reason": _REASON, "confirm_slug": "acme", "password": _ADMIN_PASSWORD},
        headers=bearer(platform_token(platform_admin.id)),
    )

    assert response.status_code == 200
    # The certificate reports the hook deletes truthfully — exactly org A's rows (one per table),
    # never B's. Hooks run BEFORE the user delete, so every connector table is counted honestly
    # (no more cascade-erased-uncounted CO-01 rows).
    assert response.json()["erased_rows_by_table"] == _EXPECTED_CERTIFICATE_COUNTS
    # Row survival is the real proof of erasure: A's rows are GONE (whether by the hook or the user
    # cascade) and B's survive — for EVERY table, including the cascaded CO-01 ones.
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
