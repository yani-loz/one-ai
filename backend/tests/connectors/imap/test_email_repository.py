"""
Role: Cross-tenant isolation + idempotency tests for EmailMessageRepository — the testing.md
      non-negotiable at the email Layer-1 repo layer.
Used by: pytest (tests/connectors/imap).
Depends on: the imap conftest (email_schema + db_session + seed_connection), the model + repo.
Key invariants tested:
  - get_in_org / list_for_org are org-scoped; `exists` (the idempotency check) is scoped to
    (org, connection, dedup_key), so a re-run for one org never sees another org's emails.
  - A message stores its recipients + attachments (the FK child path works).
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.imap.repositories.email_repository import EmailMessageRepository
from tests.connectors.imap.conftest import seed_connection


def _message(
    org_id: UUID, connection_id: UUID, dedup_key: str = "<msg-1@example.com>"
) -> EmailMessage:
    return EmailMessage(
        org_id=org_id,
        connection_id=connection_id,
        dedup_key=dedup_key,
        message_id=dedup_key,
        subject="Hello",
        from_address="boyan@example.com",
        body_text="hi there",
        parse_status="parsed",
    )


async def test_get_in_org_other_org_returns_none(db_session: AsyncSession) -> None:
    repo = EmailMessageRepository(db_session)
    org_a, org_b = uuid4(), uuid4()
    connection = await seed_connection(db_session, org_a)
    message = await repo.insert(_message(org_a, connection.id))

    assert await repo.get_in_org(message.id, org_b) is None
    assert await repo.get_in_org(message.id, org_a) is not None


async def test_dedup_lookup_is_org_scoped(db_session: AsyncSession) -> None:
    repo = EmailMessageRepository(db_session)
    org_a, org_b = uuid4(), uuid4()
    connection = await seed_connection(db_session, org_a)
    message = await repo.insert(_message(org_a, connection.id, dedup_key="dk-1"))

    assert await repo.get_message_id_by_dedup(org_a, connection.id, "dk-1") == message.id
    # The idempotency check must not leak across tenants.
    assert await repo.get_message_id_by_dedup(org_b, connection.id, "dk-1") is None


async def test_list_for_org_excludes_other_orgs(db_session: AsyncSession) -> None:
    repo = EmailMessageRepository(db_session)
    org_a, org_b = uuid4(), uuid4()
    connection = await seed_connection(db_session, org_a)
    await repo.insert(_message(org_a, connection.id))

    assert await repo.list_for_org(org_b) == []
    assert len(await repo.list_for_org(org_a)) == 1


async def test_stores_message_with_recipients_and_attachments(db_session: AsyncSession) -> None:
    repo = EmailMessageRepository(db_session)
    org_a = uuid4()
    connection = await seed_connection(db_session, org_a)
    message = await repo.insert(_message(org_a, connection.id))

    await repo.add_recipient(
        EmailRecipient(org_id=org_a, email_id=message.id, kind="to", address="anna@example.com")
    )
    await repo.add_attachment(
        EmailAttachment(
            org_id=org_a, email_id=message.id, filename="offer.pdf", content_type="application/pdf"
        )
    )

    assert await repo.get_in_org(message.id, org_a) is not None
