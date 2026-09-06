"""
Role: LIVE reader-plane tests for the id-addressed and document tools — get_email,
      get_attachment, count_emails, search_attachments. Covers the two guarantees that only
      show up at runtime: TENANT ISOLATION (testing.md's hardest rule — a tenant-B id must be
      indistinguishable from a nonexistent one) and the ANTI-FABRICATION rule that a not-found
      envelope never echoes the id it was asked for.
Used by: pytest (tests/ask/tools). Requires a migrated + role-provisioned DB; the ask_schema
      fixture skips loudly otherwise.
Depends on: app.ask.tools.email_read/attachment_tools/email_search,
      app.core.database.reader_session, the tests/ask/conftest seed helpers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.ask.tools.attachment_tools import _get_attachment, _search_attachments
from app.ask.tools.email_read import _get_email
from app.ask.tools.email_search import _count_emails
from app.connectors.imap.models.email import EmailAttachment, EmailMessage
from app.connectors.models.connector_connection import ConnectorConnection
from app.core.database import reader_session
from app.entities.models.person import Person

ConnectionSeeder = Callable[..., Awaitable[ConnectorConnection]]
PersonSeeder = Callable[..., Awaitable[Person]]
EmailSeeder = Callable[..., Awaitable[EmailMessage]]
AttachmentSeeder = Callable[..., Awaitable[EmailAttachment]]

_SENT_AT = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)


# — Anti-fabrication: a not-found envelope must not hand the id back —


async def test_get_email_not_found_does_not_echo_the_requested_id(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
) -> None:
    # An echoed id would appear in the observation the citation grader scans, letting a
    # model-invented uuid launder itself into "evidence a tool returned".
    org = uuid4()
    await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader One", emails=("reader@a.test",))
    await db_session.commit()
    invented = "44444444-4444-4444-4444-444444444444"

    async with reader_session(org, reader.id) as session:
        result = await _get_email(session, {"email_id": invented})

    assert result["found"] is False
    assert invented not in str(result)


async def test_get_attachment_not_found_does_not_echo_the_requested_id(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
) -> None:
    org = uuid4()
    await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader One", emails=("reader@a.test",))
    await db_session.commit()
    invented = "55555555-5555-5555-5555-555555555555"

    async with reader_session(org, reader.id) as session:
        result = await _get_attachment(session, {"attachment_id": invented})

    assert result["found"] is False
    assert invented not in str(result)


async def test_get_email_never_serves_bcc_recipients(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # A stored message is the OWNER's copy and on a Sent copy it carries the blind-copy set.
    # Grants go to every addressee regardless of kind, so without this a plain To recipient
    # reading the message learns exactly who was blind-copied — the one thing BCC means.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "To Recipient", emails=("to@acme.test",))
    message = await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="sender@acme.test", subject="Deal terms", sent_at=_SENT_AT,
        recipients=(
            ("to", "to@acme.test", "To Recipient"),
            ("cc", "cc@acme.test", "Cc Colleague"),
            ("bcc", "secret-watcher@legal.test", "Legal Watcher"),
        ),
    )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        result = await _get_email(session, {"email_id": str(message.id)})

    kinds = {r["kind"] for r in result["recipients"]}
    assert kinds == {"to", "cc"}
    assert "secret-watcher@legal.test" not in str(result)


async def test_participant_search_cannot_be_used_as_a_bcc_oracle(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # A filter that MATCHES bcc rows answers "was this address blind-copied?" through
    # total_matches alone — no row ever has to be shown for the secret to leak.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "To Recipient", emails=("to@acme.test",))
    await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="sender@acme.test", subject="Deal terms", sent_at=_SENT_AT,
        recipients=(
            ("to", "to@acme.test", "To Recipient"),
            ("bcc", "secret-watcher@legal.test", "Legal Watcher"),
        ),
    )
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        blind = await _count_emails(session, {"participants": ["secret-watcher@legal.test"]})
        visible = await _count_emails(session, {"participants": ["to@acme.test"]})

    assert blind["matches"] == 0  # the blind copy is not discoverable
    assert visible["matches"] == 1  # …while ordinary addressing still works


# — Tenant isolation: org A must never reach org B's content through ANY read tool —


async def test_cross_tenant_get_email_is_indistinguishable_from_missing(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    reader_a = await seed_person(db_session, org_a, "Reader A", emails=("a@a.test",))
    connection_b = await seed_connection(db_session, org_b, mailbox="owner@b.test")
    person_b = await seed_person(db_session, org_b, "Person B", emails=("b@b.test",))
    message_b = await seed_email(
        db_session, org_b, connection_b.id, person_b.id,
        from_address="counterparty@b.test", subject="B-only settlement", sent_at=_SENT_AT,
        body_text="Confidential to org B.",
    )
    # NOTE on what this test does and does not pin. The row is restricted, so org A is held
    # out by the per-person `visibility` policy as well as by `org_isolation` — dropping
    # org_isolation alone would leave this green. Making it org-visible is not possible here
    # by design: PF-01 AC5's lineage guard REFUSES a restricted-origin row that claims org
    # visibility without a promotion record, which is a stronger guarantee than this test
    # would have been. `org_isolation` itself is pinned where it stands alone —
    # test_cross_tenant_find_person_never_sees_org_b_candidate, since `person` and
    # `person_email` carry no per-person policy at all.
    await db_session.commit()

    async with reader_session(org_a, reader_a.id) as session:
        result = await _get_email(session, {"email_id": str(message_b.id)})

    assert result["found"] is False
    assert "B-only" not in str(result)
    assert str(message_b.id) not in str(result)


async def test_cross_tenant_get_attachment_is_indistinguishable_from_missing(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
    seed_attachment: AttachmentSeeder,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    reader_a = await seed_person(db_session, org_a, "Reader A", emails=("a@a.test",))
    connection_b = await seed_connection(db_session, org_b, mailbox="owner@b.test")
    person_b = await seed_person(db_session, org_b, "Person B", emails=("b@b.test",))
    message_b = await seed_email(
        db_session, org_b, connection_b.id, person_b.id,
        from_address="counterparty@b.test", subject="B contract", sent_at=_SENT_AT,
    )
    attachment_b = await seed_attachment(
        db_session, org_b, message_b,
        filename="b-contract.pdf", extracted_text="Total price for org B: 100000.",
    )
    await db_session.commit()

    async with reader_session(org_a, reader_a.id) as session:
        result = await _get_attachment(session, {"attachment_id": str(attachment_b.id)})

    assert result["found"] is False
    assert "100000" not in str(result)


async def test_cross_tenant_count_emails_counts_nothing_from_org_b(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
) -> None:
    # A count is a side channel too: a non-zero total would disclose that org B holds traffic
    # with this counterparty even though no row is returned.
    org_a, org_b = uuid4(), uuid4()
    reader_a = await seed_person(db_session, org_a, "Reader A", emails=("a@a.test",))
    connection_b = await seed_connection(db_session, org_b, mailbox="owner@b.test")
    person_b = await seed_person(db_session, org_b, "Person B", emails=("b@b.test",))
    for day in (1, 2, 3):
        await seed_email(
            db_session, org_b, connection_b.id, person_b.id,
            from_address="counterparty@b.test", subject="B settlement",
            sent_at=datetime(2020, 1, day, 12, 0, tzinfo=UTC),
        )
    await db_session.commit()

    async with reader_session(org_a, reader_a.id) as session:
        result = await _count_emails(session, {"participants": ["counterparty@b.test"]})

    assert result["matches"] == 0


async def test_cross_tenant_search_attachments_returns_no_org_b_documents(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
    seed_attachment: AttachmentSeeder,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    reader_a = await seed_person(db_session, org_a, "Reader A", emails=("a@a.test",))
    connection_b = await seed_connection(db_session, org_b, mailbox="owner@b.test")
    person_b = await seed_person(db_session, org_b, "Person B", emails=("b@b.test",))
    message_b = await seed_email(
        db_session, org_b, connection_b.id, person_b.id,
        from_address="counterparty@b.test", subject="B contract", sent_at=_SENT_AT,
    )
    await seed_attachment(
        db_session, org_b, message_b,
        filename="b-secret-terms.pdf", extracted_text="Org B payment schedule.",
    )
    await db_session.commit()

    async with reader_session(org_a, reader_a.id) as session:
        result = await _search_attachments(session, {"query": "terms"})

    assert result["results"] == []
    assert "b-secret-terms.pdf" not in str(result)


# — The document tools' own contracts, proven live on a granted row —


async def test_search_attachments_zero_matches_carries_absence_note(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
) -> None:
    org = uuid4()
    await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader One", emails=("reader@a.test",))
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        result = await _search_attachments(session, {"query": "nothing-matches-this"})

    assert result["listing_complete"] is False  # nothing enumerated = nothing complete
    assert "not verified absence" in result["listing_note"]


async def test_get_attachment_pages_long_documents_with_next_offset(
    db_session: AsyncSession,
    seed_connection: ConnectionSeeder,
    seed_person: PersonSeeder,
    seed_email: EmailSeeder,
    seed_attachment: AttachmentSeeder,
) -> None:
    # Contract totals and payment schedules sit near the END of a document — paging must
    # actually reach it, and the second page must continue where the first stopped.
    org = uuid4()
    connection = await seed_connection(db_session, org)
    reader = await seed_person(db_session, org, "Reader One", emails=("reader@a.test",))
    message = await seed_email(
        db_session, org, connection.id, reader.id,
        from_address="counterparty@x.test", subject="Contract", sent_at=_SENT_AT,
    )
    body = "A" * 5000 + "TOTAL PRICE 42"
    attachment = await seed_attachment(db_session, org, message, extracted_text=body)
    await db_session.commit()

    async with reader_session(org, reader.id) as session:
        first = await _get_attachment(session, {"attachment_id": str(attachment.id)})
        second = await _get_attachment(
            session,
            {"attachment_id": str(attachment.id), "offset": first["next_offset"]},
        )

    assert first["total_chars"] == len(body)
    assert "TOTAL PRICE 42" not in first["text"]
    assert "TOTAL PRICE 42" in second["text"]
