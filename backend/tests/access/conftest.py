"""
Role: DB fixtures + seed helpers for the access (PF-01) test suite — the full set of tables one
      permission-fidelity flow touches (users + connection + email content + the four access
      tables) and explicit seed helpers (seed data stays visible in each test, per testing.md).
Used by: tests under tests/access/.
Depends on: app.core.database, the access + connector + entity + email + identity models.
Key invariants:
  - Function-scoped; creates missing tables, TRUNCATE ... CASCADE on teardown (migrated-DB
    pattern shared with the other suites). audit_log is deliberately NOT truncated (append-only).
  - The LIVE-policy tests additionally need a migrated DB (policies/triggers/functions are
    migration-only) — they skip on a non-migrated one via the alembic_version probe.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.models.acl_grant import AclGrant
from app.access.models.fact_provenance import FactProvenance
from app.access.models.principal_source_identity import PrincipalSourceIdentity
from app.access.models.visibility_promotion import VisibilityPromotion
from app.common.base_model import Base
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.models.connector_connection import ConnectorConnection
from app.core.database import GlobalSessionLocal, engine, runtime_roles_present
from app.entities.models.company import Company, CompanyDomain, PersonCompany
from app.entities.models.person import Person, PersonAlias, PersonEmail
from app.identity.models.user import User
from app.identity.security.password import hash_password

# Parents before children (create order); TRUNCATE ... CASCADE handles FK order on teardown.
_ACCESS_TABLES = [
    User.__table__,
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
    PrincipalSourceIdentity.__table__,
    AclGrant.__table__,
    VisibilityPromotion.__table__,
    FactProvenance.__table__,
]


def _missing_tables(sync_connection: object) -> list[object]:
    from sqlalchemy import inspect

    existing = set(inspect(sync_connection).get_table_names())
    return [table for table in _ACCESS_TABLES if table.name not in existing]


@pytest_asyncio.fixture
async def access_schema() -> AsyncIterator[None]:
    """Give each test the access-flow schema, then reset it (truncate or drop)."""
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
            pre_existing = [t for t in _ACCESS_TABLES if t not in created]
            if pre_existing:
                names = ", ".join(t.name for t in pre_existing)
                await connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            if created:
                await connection.run_sync(Base.metadata.drop_all, tables=created)


@pytest_asyncio.fixture
async def db_session(access_schema: None) -> AsyncIterator[AsyncSession]:
    """Yield a committed-on-success BYPASSRLS session for seeding + direct assertions."""
    async with GlobalSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def seed_connection(
    session: AsyncSession,
    org_id: UUID,
    *,
    owner_user_id: UUID | None = None,
    mailbox: str = "owner@acme.test",
) -> ConnectorConnection:
    """Insert a minimal connector_connection (registers the org first — 0014 org-root FK).

    owner_user_id None = a shared/admin mailbox (which PF-01 gives NO promoter and no owner
    grant); set = a self-connected user-owned mailbox.
    """
    from tests.conftest import register_org

    await register_org(session, org_id)
    connection = ConnectorConnection(
        org_id=org_id,
        connector_type="imap",
        owner_user_id=owner_user_id,
        display_name="Access Test Mailbox",
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


async def seed_user(session: AsyncSession, org_id: UUID, email: str = "owner@acme.test") -> User:
    """Insert an active member user (the promotion approver / connection owner).

    Registers the org row first (idempotent) — the 0014 org-root FK rejects phantom tenants.
    """
    from tests.conftest import register_org

    await register_org(session, org_id)
    user = User(
        org_id=org_id,
        email=email,
        full_name="Access Test User",
        password_hash=hash_password("Pw-Access-123456"),
        role="member",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def seed_person(session: AsyncSession, org_id: UUID, name: str = "Person") -> Person:
    """Insert a bare person (the grant principal)."""
    person = Person(org_id=org_id, display_name=name, is_internal=True)
    session.add(person)
    await session.flush()
    return person


async def bind_verified_identity(
    session: AsyncSession,
    org_id: UUID,
    person_id: UUID,
    source_type: str,
    external_id: str,
    *,
    verified: bool = True,
) -> PrincipalSourceIdentity:
    """Insert a principal_source_identity row ('email' address or 'auth' user binding)."""
    identity = PrincipalSourceIdentity(
        org_id=org_id,
        person_id=person_id,
        source_type=source_type,
        external_id=external_id,
        verified=verified,
        verified_at=datetime.now(UTC) if verified else None,
    )
    session.add(identity)
    await session.flush()
    return identity


async def seed_email_message(
    session: AsyncSession,
    org_id: UUID,
    connection_id: UUID,
    *,
    visibility_scope: str = "restricted",
    subject: str = "restricted subject",
    promotion_approver_user_id: UUID | None = None,
) -> EmailMessage:
    """Insert one email message row (+ one to-recipient + one attachment child inheriting its
    scopes) — the content triple every policy test reads.

    Email is ALWAYS born origin='restricted' (DB CHECK, 2026-07-04 review M6). Seeding an
    org-VISIBLE message therefore requires promotion lineage: pass
    promotion_approver_user_id and the helper stages the audit anchor + visibility_promotion
    row first (the AC5 trigger validates it), exactly like the real promotion path.
    """
    from app.identity.models.audit_log import AuditLog

    message_id = uuid4()
    if visibility_scope == "org":
        assert promotion_approver_user_id is not None, (
            "org-visible email needs promotion lineage — pass promotion_approver_user_id"
        )
        audit_anchor = AuditLog(
            actor_type="user",
            actor_id=promotion_approver_user_id,
            action="access.visibility_promoted",
            org_id=org_id,
            details={},
        )
        session.add(audit_anchor)
        await session.flush()
        session.add(
            VisibilityPromotion(
                org_id=org_id,
                object_type="email_message",
                object_id=message_id,
                approved_by_user_id=promotion_approver_user_id,
                audit_log_id=audit_anchor.id,
            )
        )
        await session.flush()
    message = EmailMessage(
        id=message_id,
        org_id=org_id,
        connection_id=connection_id,
        visibility_scope=visibility_scope,
        origin_scope="restricted",
        container_id=connection_id,
        dedup_key=f"seed-{uuid4()}",
        subject=subject,
        headers={},
    )
    session.add(message)
    await session.flush()
    session.add(
        EmailRecipient(
            org_id=org_id,
            email_id=message.id,
            visibility_scope=visibility_scope,
            origin_scope="restricted",
            container_id=connection_id,
            kind="to",
            address="someone@acme.test",
        )
    )
    session.add(
        EmailAttachment(
            org_id=org_id,
            email_id=message.id,
            visibility_scope=visibility_scope,
            origin_scope="restricted",
            container_id=connection_id,
            filename="doc.txt",
            extraction_status="empty",
        )
    )
    await session.flush()
    return message


async def grant_email_access(
    session: AsyncSession,
    org_id: UUID,
    person_id: UUID,
    message_id: UUID,
    connection_id: UUID | None = None,
    provenance: str = "recipient",
) -> AclGrant:
    """Insert one live acl_grant on a message for a person."""
    grant = AclGrant(
        org_id=org_id,
        person_id=person_id,
        object_type="email_message",
        object_id=message_id,
        connection_id=connection_id,
        provenance=provenance,
    )
    session.add(grant)
    await session.flush()
    return grant
