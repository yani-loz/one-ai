"""
Role: End-to-end tests for EmailIngestService — one raw email → Layer-1 rows + the resolved
      person/company graph, idempotent re-ingest, attachment-text extraction, and the NON-NEGOTIABLE
      cross-tenant isolation (the same email under two connections stays in two separate tenants).
Used by: pytest (tests/connectors/imap/services). Real DB via the services conftest.
Depends on: app.connectors.imap.services.email_ingest_service + the connector/entity/email models.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.imap.services.email_ingest_service import EmailIngestService, IngestOutcome
from app.entities.models.company import Company
from app.entities.models.person import Person
from tests.connectors.imap.services.conftest import seed_connection

MAILBOX = "owner@acme.com"


def _eml(headers: str, body: str = "Hello there.") -> bytes:
    return (headers.strip() + "\r\n\r\n" + body).replace("\n", "\r\n").encode("utf-8")


async def _count(session: AsyncSession, model: type, org_id: UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(model).where(model.org_id == org_id)
    )
    return result.scalar_one()


async def test_ingest_stores_message_and_builds_entity_graph(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org, MAILBOX)
    service = EmailIngestService(db_session, connection)
    raw = _eml(
        "From: Boyan <boyan@globex.com>\nTo: owner@acme.com\nCc: maria@globex.com\n"
        "Subject: Hi\nMessage-ID: <m1@globex.com>\nDate: Mon, 02 Jun 2025 10:00:00 +0200"
    )

    outcome = await service.ingest_email(raw)

    assert outcome is IngestOutcome.STORED
    assert await _count(db_session, EmailMessage, org) == 1
    assert await _count(db_session, EmailRecipient, org) == 2  # to + cc
    # globex.com (external — sender + cc) and acme.com (the mailbox's own, internal recipient).
    assert await _count(db_session, Company, org) == 2
    message = (
        await db_session.execute(select(EmailMessage).where(EmailMessage.org_id == org))
    ).scalar_one()
    assert message.from_person_id is not None  # sender resolved to a person
    assert message.direction == "inbound"
    assert message.size_bytes == len(raw)


async def test_ingest_is_idempotent_on_reingest(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    raw = _eml("From: a@globex.com\nTo: owner@acme.com\nMessage-ID: <dup@x>")

    first = await service.ingest_email(raw)
    second = await service.ingest_email(raw)  # same logical email (e.g. seen in another folder)

    assert first is IngestOutcome.STORED
    assert second is IngestOutcome.SKIPPED
    assert await _count(db_session, EmailMessage, org) == 1


async def test_ingest_extracts_attachment_text(db_session: AsyncSession) -> None:
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nSubject: A\r\nMessage-ID: <att@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSee attached.\r\n"
        b"--B\r\nContent-Type: text/csv\r\n"
        b'Content-Disposition: attachment; filename="d.csv"\r\n\r\n'
        b"a,b\r\n1,2\r\n--B--\r\n"
    )

    await service.ingest_email(raw)

    attachment = (
        await db_session.execute(select(EmailAttachment).where(EmailAttachment.org_id == org))
    ).scalar_one()
    assert attachment.filename == "d.csv"
    assert attachment.extracted_text is not None and "a,b" in attachment.extracted_text


async def test_ingest_same_email_two_connections_stays_isolated(db_session: AsyncSession) -> None:
    # Cross-tenant non-negotiable: the same raw email under two org-scoped connections produces two
    # separate emails + two separate person graphs; neither org sees the other's rows.
    org_a, org_b = uuid4(), uuid4()
    conn_a = await seed_connection(db_session, org_a, "owner@acme.com")
    conn_b = await seed_connection(db_session, org_b, "owner@beta.com")
    raw = _eml("From: shared@globex.com\nTo: owner@acme.com\nMessage-ID: <shared@x>")

    await EmailIngestService(db_session, conn_a).ingest_email(raw)
    await EmailIngestService(db_session, conn_b).ingest_email(raw)

    assert await _count(db_session, EmailMessage, org_a) == 1
    assert await _count(db_session, EmailMessage, org_b) == 1
    assert await _count(db_session, Person, org_a) == 2  # sender + the mailbox-owner recipient
    assert await _count(db_session, Person, org_b) == 2
    message_a = (
        await db_session.execute(select(EmailMessage).where(EmailMessage.org_id == org_a))
    ).scalar_one()
    persons_a = set(
        (await db_session.execute(select(Person.id).where(Person.org_id == org_a))).scalars().all()
    )
    persons_b = set(
        (await db_session.execute(select(Person.id).where(Person.org_id == org_b))).scalars().all()
    )
    assert message_a.from_person_id in persons_a  # linked to its OWN org's person
    assert message_a.from_person_id not in persons_b  # never another org's
