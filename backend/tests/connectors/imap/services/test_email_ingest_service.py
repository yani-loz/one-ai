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
from app.entities.models.company import Company, CompanyDomain, PersonCompany
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
    assert await _count(db_session, Person, org) == 3  # boyan, maria, owner@acme — distinct people
    # globex.com (external — sender + cc) and acme.com (the mailbox's own, internal recipient).
    assert await _count(db_session, Company, org) == 2
    message = (
        await db_session.execute(select(EmailMessage).where(EmailMessage.org_id == org))
    ).scalar_one()
    assert message.from_person_id is not None  # sender resolved to a person
    assert message.direction == "inbound"
    assert message.size_bytes == len(raw)
    # The two globex people (boyan sender + maria cc) resolve to ONE shared company, both linked.
    globex = (
        await db_session.execute(
            select(CompanyDomain.company_id).where(
                CompanyDomain.org_id == org, CompanyDomain.domain == "globex.com"
            )
        )
    ).scalar_one()
    globex_links = await db_session.execute(
        select(func.count()).select_from(PersonCompany).where(PersonCompany.company_id == globex)
    )
    assert globex_links.scalar_one() == 2


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
    # Every ingested row is org-scoped — each org sees only its own, zero of the other's.
    assert await _count(db_session, EmailRecipient, org_a) == 1
    assert await _count(db_session, EmailRecipient, org_b) == 1
    assert await _count(db_session, Company, org_a) == 2  # globex + acme, per org
    assert await _count(db_session, Company, org_b) == 2
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


async def test_ingest_role_sender_stores_message_with_null_from_person(
    db_session: AsyncSession,
) -> None:
    # A role mailbox (info@) is not a person: the message stores, but from_person_id stays NULL.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    raw = _eml("From: info@globex.com\nTo: owner@acme.com\nMessage-ID: <role@x>")

    outcome = await service.ingest_email(raw)

    assert outcome is IngestOutcome.STORED
    message = (
        await db_session.execute(select(EmailMessage).where(EmailMessage.org_id == org))
    ).scalar_one()
    assert message.from_person_id is None  # info@ minted no person
    assert await _count(db_session, Person, org) == 1  # only the recipient owner@acme


async def test_ingest_binary_attachment_records_null_text(db_session: AsyncSession) -> None:
    # Binary extraction is deferred (CA-CONN-04): a PDF stores, but with extracted_text NULL.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <bin@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b'--B\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename="d.pdf"'
        b"\r\n\r\n%PDF-1.4 binary\r\n--B--\r\n"
    )

    await service.ingest_email(raw)

    attachment = (
        await db_session.execute(select(EmailAttachment).where(EmailAttachment.org_id == org))
    ).scalar_one()
    assert attachment.content_type == "application/pdf"
    assert attachment.size_bytes > 0
    assert attachment.extracted_text is None  # honest absent, not empty-string


async def test_ingest_malformed_attachments_do_not_drop_email(db_session: AsyncSession) -> None:
    # Regression: an over-255 content_type AND a NUL in a text attachment must be sanitized so the
    # whole email still inserts (previously either would crash the insert and silently drop it).
    org = uuid4()
    connection = await seed_connection(db_session, org)
    service = EmailIngestService(db_session, connection)
    giant = b"a" * 300 + b"/" + b"b" * 300
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <bad@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b'--B\r\nContent-Type: text/csv\r\nContent-Disposition: attachment; filename="a.csv"'
        b"\r\n\r\nx,y\x00z\r\n"
        b'--B\r\nContent-Type: ' + giant + b'\r\nContent-Disposition: attachment; filename="b"'
        b"\r\n\r\nZ\r\n--B--\r\n"
    )

    outcome = await service.ingest_email(raw)

    assert outcome is IngestOutcome.STORED  # neither malformed attachment dropped the email
    attachments = (
        await db_session.execute(select(EmailAttachment).where(EmailAttachment.org_id == org))
    ).scalars().all()
    assert len(attachments) == 2
    assert all(len(a.content_type) <= 255 for a in attachments)
    assert all("\x00" not in (a.extracted_text or "") for a in attachments)
