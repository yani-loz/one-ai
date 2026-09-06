"""
Role: End-to-end tests for EmailIngestService — one raw email → Layer-1 rows + the resolved
      person/company graph, idempotent re-ingest, and the NON-NEGOTIABLE cross-tenant isolation
      (the same email under two connections stays in two separate tenants). The per-format binary
      attachment-extraction matrix (pdf/docx/xlsx/tnef/image) lives in the sibling
      test_email_ingest_attachments.py (A2 split).
Used by: pytest (tests/connectors/imap/services). Real DB via the services conftest.
Depends on: app.connectors.imap.services.email_ingest_service + the connector/entity/email models.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
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
    assert attachment.extraction_status == "extracted"  # 0015: the outcome is recorded
    assert attachment.extractor_name == "text-decode"
    assert attachment.extractor_version is not None


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
    # DQ-D01: the role sender's domain (globex.com) is still observed as a company, though.
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
    assert await _count(db_session, Company, org) == 2  # globex (role sender, D01) + acme


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
        b"--B\r\nContent-Type: " + giant + b'\r\nContent-Disposition: attachment; filename="b"'
        b"\r\n\r\nZ\r\n--B--\r\n"
    )

    outcome = await service.ingest_email(raw)

    assert outcome is IngestOutcome.STORED  # neither malformed attachment dropped the email
    attachments = (
        (await db_session.execute(select(EmailAttachment).where(EmailAttachment.org_id == org)))
        .scalars()
        .all()
    )
    assert len(attachments) == 2
    assert all(len(a.content_type) <= 255 for a in attachments)
    assert all("\x00" not in (a.extracted_text or "") for a in attachments)


async def test_ingest_recursion_failure_stores_a_flagged_stub_not_dropped(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Audit C01: a RecursionError in the strict parse must NOT drop the email. parse_email degrades
    # to a flagged stub and the ingest service STORES it (parse_status='failed') — queryable, not
    # a silent drop. (Guards the never-lose-mail insert path the parser unit test can't reach.)
    import app.connectors.imap.parsing.email_parser as parser_mod

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(parser_mod, "_parse_email_strict", _boom)
    org = uuid4()
    connection = await seed_connection(db_session, org)
    raw = _eml("From: a@globex.com\nTo: owner@acme.com\nMessage-ID: <deep@x>")

    outcome = await EmailIngestService(db_session, connection).ingest_email(raw)

    assert outcome is IngestOutcome.STORED  # stored, never silently dropped
    message = (
        await db_session.execute(select(EmailMessage).where(EmailMessage.org_id == org))
    ).scalar_one()
    assert message.parse_status == "failed"
    assert message.dedup_key.startswith("sha256:")
    assert message.from_person_id is None  # degraded stub ran no resolver
    assert (
        await _count(db_session, Person, org) == 0
    )  # no entity-graph rows from a content-less stub


async def test_ingest_runs_parse_email_off_the_event_loop_thread(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Event-loop hygiene: parse_email is pure CPU (RFC822 parse, base64, sha256, html2text) and
    # must run via asyncio.to_thread — never on the loop thread inside the background sync.
    import threading

    import app.connectors.imap.services.email_ingest_service as ingest_module

    parse_threads: list[threading.Thread] = []
    real_parse = ingest_module.parse_email

    def _spy(raw_bytes: bytes, mailbox: str, internal_date: object = None) -> object:
        parse_threads.append(threading.current_thread())
        return real_parse(raw_bytes, mailbox, internal_date)

    monkeypatch.setattr(ingest_module, "parse_email", _spy)
    org = uuid4()
    connection = await seed_connection(db_session, org)
    raw = _eml("From: a@globex.com\nTo: owner@acme.com\nMessage-ID: <thread@x>")

    outcome = await EmailIngestService(db_session, connection).ingest_email(raw)

    assert outcome is IngestOutcome.STORED  # behavior unchanged by the offload
    assert len(parse_threads) == 1
    assert parse_threads[0] is not threading.main_thread()  # ran on a worker, not the loop


async def test_ingest_runs_extract_text_off_the_event_loop_thread(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Event-loop hygiene (2026-06-11 review): extract_text runs full pdfplumber + tables +
    # possible pypdf over ≤50MB payloads — pure CPU that must run via asyncio.to_thread, never
    # on the loop thread inside the background sync.
    import threading

    import app.connectors.imap.services.email_ingest_service as ingest_module

    extract_threads: list[threading.Thread] = []
    real_extract = ingest_module.extract_text

    def _spy(attachment: object) -> object:
        extract_threads.append(threading.current_thread())
        return real_extract(attachment)

    monkeypatch.setattr(ingest_module, "extract_text", _spy)
    org = uuid4()
    connection = await seed_connection(db_session, org)
    raw = (
        b"From: a@globex.com\r\nTo: owner@acme.com\r\nMessage-ID: <offloop@x>\r\n"
        b'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        b"--B\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
        b'--B\r\nContent-Type: text/csv\r\nContent-Disposition: attachment; filename="d.csv"'
        b"\r\n\r\na,b\r\n--B--\r\n"
    )

    outcome = await EmailIngestService(db_session, connection).ingest_email(raw)

    assert outcome is IngestOutcome.STORED  # behavior unchanged by the offload
    assert len(extract_threads) == 1
    assert extract_threads[0] is not threading.main_thread()  # ran on a worker, not the loop


async def test_ingest_human_on_mailing_list_still_creates_from_person(
    db_session: AsyncSession,
) -> None:
    # DQ-C01 fix: a real human posting to a list (List-Id/Precedence on the distributed copy) is NOT
    # a machine — the From-person must still be created (the over-broad is_automated gate would have
    # nulled from_person_id, silently dropping the colleague).
    org = uuid4()
    connection = await seed_connection(db_session, org)
    raw = _eml(
        "From: Boyan <boyan@globex.com>\nTo: team@acme.com\n"
        "List-Id: team.acme.com\nPrecedence: list\nMessage-ID: <list@x>"
    )

    await EmailIngestService(db_session, connection).ingest_email(raw)

    message = (
        await db_session.execute(select(EmailMessage).where(EmailMessage.org_id == org))
    ).scalar_one()
    assert message.is_automated is True  # the message IS list mail...
    assert message.from_person_id is not None  # ...but the human sender is still a person


async def test_ingest_auto_generated_sender_makes_no_from_person(db_session: AsyncSession) -> None:
    # DQ-C01: a machine sender (Auto-Submitted: auto-generated, non-role localpart) mints no
    # from-person, yet its domain is still observed as a company (DQ-D01).
    org = uuid4()
    connection = await seed_connection(db_session, org)
    raw = _eml(
        "From: alerts@globex.com\nTo: owner@acme.com\n"
        "Auto-Submitted: auto-generated\nMessage-ID: <auto@x>"
    )

    await EmailIngestService(db_session, connection).ingest_email(raw)

    message = (
        await db_session.execute(select(EmailMessage).where(EmailMessage.org_id == org))
    ).scalar_one()
    assert message.from_person_id is None  # machine sender → no person
    assert await _count(db_session, Company, org) == 2  # globex (auto sender) + acme (recipient)


# — R5 write-plane W4: a sender may name THEMSELVES, never a third party ————————————————


async def test_a_sender_supplied_recipient_name_never_names_that_person(
    db_session: AsyncSession,
) -> None:
    # `_enrich_person` back-fills display_name FIRST-WRITER-WINS, and a recipient display name is
    # chosen by the SENDER about somebody else. So mailing a synced mailbox
    # `To: "Chief Fraud Officer" <cfo@acme.com>` before any named sighting of that address named
    # that person PERMANENTLY, and find_person then reported it as who they are.
    org = uuid4()
    connection = await seed_connection(db_session, org, mailbox=MAILBOX)
    raw = _eml('From: outsider@globex.com\nTo: "Chief Fraud Officer" <cfo@acme.com>\nSubject: hi')

    await EmailIngestService(db_session, connection).ingest_email(raw)

    named = await db_session.execute(select(Person).where(Person.org_id == org))
    display_names = {person.display_name for person in named.scalars().all()}
    assert "Chief Fraud Officer" not in display_names


async def test_a_sender_still_names_themselves_from_the_from_header(
    db_session: AsyncSession,
) -> None:
    # The other direction: naming yourself in `From:` is a claim about your OWN identity and must
    # keep working, or the contact graph loses every display name it legitimately has.
    org = uuid4()
    connection = await seed_connection(db_session, org, mailbox=MAILBOX)
    raw = _eml('From: "Real Person" <real@globex.com>\nTo: owner@acme.com\nSubject: hi')

    await EmailIngestService(db_session, connection).ingest_email(raw)

    named = await db_session.execute(select(Person).where(Person.org_id == org))
    display_names = {person.display_name for person in named.scalars().all()}
    assert "Real Person" in display_names
