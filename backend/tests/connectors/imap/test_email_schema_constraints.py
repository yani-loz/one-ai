"""
Role: Behavioral DB-constraint tests for the email Layer-1 schema — pins the delete model
      (CASCADE / SET NULL), the dedup_key UNIQUE idempotency guard, and the CHECK enums against the
      REAL schema, so a future migration that flips an ondelete or drops a constraint fails loudly
      instead of silently changing production behavior.
Used by: pytest (tests/connectors/imap).
Depends on: the imap conftest (email_schema + db_session + seed_connection), the models + repo,
            connector_connection (FK target), Person (FK target).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.imap.repositories.email_repository import EmailMessageRepository
from app.connectors.models.connector_connection import ConnectorConnection
from app.entities.models.person import Person
from tests.connectors.imap.conftest import seed_connection


def _message(org_id: UUID, connection_id: UUID, dedup_key: str = "<m@x>") -> EmailMessage:
    return EmailMessage(
        org_id=org_id,
        connection_id=connection_id,
        dedup_key=dedup_key,
        subject="Hi",
        body_text="body",
        parse_status="parsed",
    )


async def test_delete_connection_cascades_email_but_keeps_person(db_session: AsyncSession) -> None:
    repo = EmailMessageRepository(db_session)
    org = uuid4()
    connection = await seed_connection(db_session, org)
    person = Person(org_id=org, display_name="Boyan")
    db_session.add(person)
    await db_session.flush()
    message = await repo.insert(_message(org, connection.id))
    await repo.add_recipient(
        EmailRecipient(org_id=org, email_id=message.id, kind="to", address="a@x.com")
    )
    await repo.add_attachment(EmailAttachment(org_id=org, email_id=message.id, filename="a.pdf"))
    # Capture ids while rows live; assert via column queries (bypass the ORM identity map).
    message_id, person_id = message.id, person.id

    await db_session.execute(
        delete(ConnectorConnection).where(ConnectorConnection.id == connection.id)
    )
    await db_session.flush()

    email_rows = await db_session.execute(
        select(EmailMessage.id).where(EmailMessage.id == message_id)
    )
    child_rows = await db_session.execute(
        select(EmailRecipient.id).where(EmailRecipient.email_id == message_id)
    )
    attachment_rows = await db_session.execute(
        select(EmailAttachment.id).where(EmailAttachment.email_id == message_id)
    )
    person_rows = await db_session.execute(select(Person.id).where(Person.id == person_id))
    assert email_rows.first() is None  # email purged by CASCADE
    assert child_rows.first() is None  # recipients cascade with the message
    assert attachment_rows.first() is None  # attachments cascade with the message
    assert person_rows.first() is not None  # shared graph survives


async def test_delete_person_nulls_links_and_keeps_email(db_session: AsyncSession) -> None:
    repo = EmailMessageRepository(db_session)
    org = uuid4()
    connection = await seed_connection(db_session, org)
    person = Person(org_id=org, display_name="Boyan")
    db_session.add(person)
    await db_session.flush()
    message = _message(org, connection.id)
    message.from_person_id = person.id
    await repo.insert(message)
    recipient = EmailRecipient(
        org_id=org, email_id=message.id, kind="to", address="a@x.com", person_id=person.id
    )
    await repo.add_recipient(recipient)
    message_id, recipient_id, person_id = message.id, recipient.id, person.id

    await db_session.execute(delete(Person).where(Person.id == person_id))
    await db_session.flush()

    # Column-level: a present row with a NULL value (distinct from an absent row, which scalar would
    # also report as None) — proves the email SURVIVES with from_person_id SET NULL.
    email_row = (
        await db_session.execute(
            select(EmailMessage.from_person_id).where(EmailMessage.id == message_id)
        )
    ).one_or_none()
    recipient_row = (
        await db_session.execute(
            select(EmailRecipient.person_id).where(EmailRecipient.id == recipient_id)
        )
    ).one_or_none()
    assert email_row is not None and email_row[0] is None  # email kept, link nulled
    assert recipient_row is not None and recipient_row[0] is None


async def test_duplicate_dedup_key_same_connection_rejected(db_session: AsyncSession) -> None:
    repo = EmailMessageRepository(db_session)
    org = uuid4()
    connection = await seed_connection(db_session, org)
    await repo.insert(_message(org, connection.id, dedup_key="dk"))

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await repo.insert(_message(org, connection.id, dedup_key="dk"))


async def test_same_dedup_key_other_connection_allowed(db_session: AsyncSession) -> None:
    repo = EmailMessageRepository(db_session)
    org = uuid4()
    conn_a = await seed_connection(db_session, org, username="a@x.com")
    conn_b = await seed_connection(db_session, org, username="b@x.com")
    await repo.insert(_message(org, conn_a.id, dedup_key="dk"))

    # Same dedup_key under a different connection is a different email — must be allowed.
    await repo.insert(_message(org, conn_b.id, dedup_key="dk"))

    assert len(await repo.list_for_org(org)) == 2


@pytest.mark.parametrize(
    ("field", "value"), [("direction", "sideways"), ("parse_status", "broken")]
)
async def test_email_message_check_rejects_bad_enum(
    db_session: AsyncSession, field: str, value: str
) -> None:
    repo = EmailMessageRepository(db_session)
    org = uuid4()
    connection = await seed_connection(db_session, org)
    message = _message(org, connection.id, dedup_key=f"dk-{field}")
    setattr(message, field, value)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await repo.insert(message)


async def test_email_recipient_kind_check_rejects_bad_value(db_session: AsyncSession) -> None:
    repo = EmailMessageRepository(db_session)
    org = uuid4()
    connection = await seed_connection(db_session, org)
    message = await repo.insert(_message(org, connection.id, dedup_key="dk-kind"))

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await repo.add_recipient(
                EmailRecipient(org_id=org, email_id=message.id, kind="from", address="a@x.com")
            )
