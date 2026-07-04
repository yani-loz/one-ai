"""
Role: Data access for the email Layer-1 tables (email_message + recipients + attachments) — no
      business decisions (rule A5). Parsing/resolution/ingest logic lives elsewhere (3b/3c/3d).
Used by: the IMAP ingest pipeline; constructed on the caller's tenant session.
Depends on: app.connectors.imap.models.email, SQLAlchemy async.
Key invariants:
  - EVERY read is org-scoped. `exists` is the idempotency check on the NON-NULL dedup_key
    (Message-ID or content hash) within (org, connection), so a re-run never double-inserts.
  - Recipients/attachments are children of a flushed message (their email_id is set by the caller);
    DB cascades delete them with the message.
  - The caller owns the transaction; methods only add/flush.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.extraction.extraction_result import STATUS_PENDING
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient


class EmailMessageRepository:
    """Org-scoped persistence for parsed emails and their recipients/attachments."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to the caller's tenant-scoped session."""
        self._session = session

    async def insert(self, message: EmailMessage) -> EmailMessage:
        """Stage a new email message and flush to populate its id (for child rows)."""
        self._session.add(message)
        await self._session.flush()
        return message

    async def get_message_id_by_dedup(
        self, org_id: UUID, connection_id: UUID, dedup_key: str
    ) -> UUID | None:
        """Return the existing (org, connection, dedup_key) message id, or None (idempotency).

        Returns the ID (not a bare bool) so the skip path can reconcile the existing message's
        grants — re-ingest is the grant backfill/reconciliation pass (PF-01 AC21).
        """
        result = await self._session.execute(
            select(EmailMessage.id).where(
                EmailMessage.org_id == org_id,
                EmailMessage.connection_id == connection_id,
                EmailMessage.dedup_key == dedup_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_in_org(self, email_id: UUID, org_id: UUID) -> EmailMessage | None:
        """Load an email by id iff it belongs to `org_id`, else None."""
        result = await self._session.execute(
            select(EmailMessage).where(EmailMessage.id == email_id, EmailMessage.org_id == org_id)
        )
        return result.scalar_one_or_none()

    async def list_for_org(self, org_id: UUID, limit: int = 100) -> list[EmailMessage]:
        """Return one org's emails, newest-received first (capped)."""
        result = await self._session.execute(
            select(EmailMessage)
            .where(EmailMessage.org_id == org_id)
            .order_by(EmailMessage.received_at.desc().nulls_last(), EmailMessage.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def add_recipient(self, recipient: EmailRecipient) -> EmailRecipient:
        """Attach a recipient to a flushed message (caller sets org_id/email_id)."""
        self._session.add(recipient)
        await self._session.flush()
        return recipient

    async def add_attachment(self, attachment: EmailAttachment) -> EmailAttachment:
        """Attach an attachment record to a flushed message (caller sets org_id/email_id)."""
        self._session.add(attachment)
        await self._session.flush()
        return attachment

    async def get_prior_extraction(
        self, org_id: UUID, content_hash: str, content_type: str
    ) -> EmailAttachment | None:
        """Return the newest ATTEMPTED extraction of identical bytes in this org, else None.

        The content-addressed reuse lookup (study §8.4 — 59% of attachment rows are byte-identical
        dups): extraction is a pure function of (payload bytes, content_type), so a prior row with
        the same (org_id, content_hash, content_type) already holds this payload's exact extraction
        outcome. `content_type` must match — identical bytes under a different declared type
        dispatch to a different extractor. Rows still at the 'pending' DB default are excluded:
        extraction never RAN on them, so there is no outcome to reuse (and a later extractor
        generation may handle what 'pending' predates). Served by
        ix_email_attachment_org_content_hash. Runs on the WRITE plane (oneai_app), where the
        PF-01 visibility policy does not apply — the reader plane never calls this.
        """
        result = await self._session.execute(
            select(EmailAttachment)
            .where(
                EmailAttachment.org_id == org_id,
                EmailAttachment.content_hash == content_hash,
                EmailAttachment.content_type == content_type,
                EmailAttachment.extraction_status != STATUS_PENDING,
            )
            .order_by(EmailAttachment.created_at.desc(), EmailAttachment.id.desc())
            .limit(1)
        )
        return result.scalars().first()
