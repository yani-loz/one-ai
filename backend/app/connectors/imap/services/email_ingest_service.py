"""
Role: The single-pass email ingest (design §2) — parse one raw email, resolve its participants into
      the shared person/company graph, and store the Layer-1 rows (message + recipients +
      attachments), idempotently. This is the reusable core the future production SyncRunner calls
      and that the dev disk-ingest driver (scripts.ingest_imap_dump) drives over the spike corpus.
Used by: scripts.ingest_imap_dump (now); the connector sync runner (later).
Depends on: app.connectors.imap.parsing (parse_email + extract_text), .repositories (email repo),
            .models.email, app.entities.services.entity_resolver. Bound to ONE connection.
Key invariants:
  - ORG-SCOPED: the org and connection come from the bound ConnectorConnection — the caller never
    passes an org_id, so an email can only ever land in its connection's tenant.
  - IDEMPOTENT: skips (does NOT insert) when (org, connection, dedup_key) already exists, so a
    re-run — or the same logical email seen in multiple IMAP folders — stores exactly one row.
  - Returns a plain IngestOutcome enum, NEVER a live ORM row (a row read after the caller's commit
    could lazy-load on a closed greenlet). The CALLER owns the transaction + commit.
  - Attachment text is extracted inline and the bytes are dropped (lean storage, design §4).
  - parse_email (pure CPU: RFC822 parse, base64 decode, sha256, html2text) runs on a WORKER
    thread (asyncio.to_thread) so a large email never stalls the event loop mid-sync.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.imap.parsing import ParsedEmail, extract_text, parse_email
from app.connectors.imap.repositories.email_repository import EmailMessageRepository
from app.connectors.models.connector_connection import ConnectorConnection
from app.entities.services.entity_resolver import EntityResolver


class IngestOutcome(StrEnum):
    """The result of ingesting one email (FAILED is the driver's tally for a raised error)."""

    STORED = "stored"
    SKIPPED = "skipped"  # already present (idempotent re-run or same email in another folder)


class EmailIngestService:
    """Ingest parsed emails for ONE connection into Layer-1 + the shared entity graph."""

    def __init__(self, session: AsyncSession, connection: ConnectorConnection) -> None:
        """Bind to the caller's tenant session and the connection being synced.

        Args:
            session: the caller's session (owns the transaction; this service only adds/flushes).
            connection: the ConnectorConnection — supplies org_id, connection_id, the mailbox
                address (for direction + the resolver), and the source connector type.
        """
        # Capture the connection's fields as PLAIN values now (while it is freshly loaded), so the
        # service never re-reads the ORM object — a later rollback in a per-email-commit loop
        # expires ORM objects, and touching one then would lazy-load on a closed greenlet.
        self._session = session
        self._org_id = connection.org_id
        self._connection_id = connection.id
        self._mailbox = connection.username
        self._emails = EmailMessageRepository(session)
        self._resolver = EntityResolver(
            session, mailbox_address=connection.username, source=connection.connector_type
        )

    async def ingest_email(
        self, raw_bytes: bytes, internal_date: datetime | None = None
    ) -> IngestOutcome:
        """Parse → resolve participants → store one email; idempotent on dedup_key.

        Args:
            raw_bytes: the full RFC822 message (from a .eml file or a BODY.PEEK[] fetch).
            internal_date: the IMAP INTERNALDATE if known (authoritative for received_at).

        Returns:
            IngestOutcome.SKIPPED if the email already exists for this (org, connection), else
            IngestOutcome.STORED after inserting the message + recipients + attachments.
        """
        org_id = self._org_id
        connection_id = self._connection_id
        # parse_email is a documented PURE function — safe and worthwhile to offload: it is the
        # ingest's CPU hot spot and would otherwise block the loop inside the background sync.
        parsed = await asyncio.to_thread(parse_email, raw_bytes, self._mailbox, internal_date)

        if await self._emails.exists(org_id, connection_id, parsed.dedup_key):
            return IngestOutcome.SKIPPED

        from_person_id = None
        if parsed.from_address:
            # DQ-C01: an automated SENDER IDENTITY (noreply@/auto-generated) is not a person — its
            # domain is still observed as a company, but from_person_id stays NULL. Gated on
            # is_automated_origin, NOT is_automated: a real human emailing a mailing list still
            # becomes a person (their mail carries List-*, but the sender is human).
            from_person_id = await self._resolver.resolve_participant(
                org_id,
                parsed.from_address,
                parsed.from_name,
                parsed.received_at,
                allow_person=not parsed.is_automated_origin,
            )

        message = await self._emails.insert(
            self._build_message(org_id, connection_id, parsed, from_person_id, len(raw_bytes))
        )
        await self._store_recipients(org_id, message.id, parsed)
        await self._store_attachments(org_id, message.id, parsed)
        return IngestOutcome.STORED

    def _build_message(
        self,
        org_id: UUID,
        connection_id: UUID,
        parsed: ParsedEmail,
        from_person_id: UUID | None,
        size_bytes: int,
    ) -> EmailMessage:
        """Map a ParsedEmail to an EmailMessage row (size_bytes is the raw wire length)."""
        return EmailMessage(
            org_id=org_id,
            connection_id=connection_id,
            dedup_key=parsed.dedup_key,
            message_id=parsed.message_id,
            in_reply_to=parsed.in_reply_to,
            references=parsed.references or None,
            from_name=parsed.from_name,
            from_address=parsed.from_address,
            from_person_id=from_person_id,
            subject=parsed.subject,
            sent_at=parsed.sent_at,
            received_at=parsed.received_at,
            body_text=parsed.body_text,
            direction=parsed.direction,
            is_automated=parsed.is_automated,
            is_reply=parsed.is_reply,
            has_attachments=parsed.has_attachments,
            word_count=parsed.word_count,
            language=parsed.language,
            headers=parsed.headers,
            size_bytes=size_bytes,
            parse_status=parsed.parse_status,
        )

    # reply_to / sender are routing headers, not real recipients — they must not mint people
    # (audit DQ-C02). Their domain is still observed as a company; only person-hood is suppressed.
    _NON_PERSON_RECIPIENT_KINDS = frozenset({"reply_to", "sender"})

    async def _store_recipients(self, org_id: UUID, email_id: UUID, parsed: ParsedEmail) -> None:
        """Insert each recipient, resolving real recipients to a person (None for role/routing)."""
        for recipient in parsed.recipients:
            person_id = await self._resolver.resolve_participant(
                org_id,
                recipient.address,
                recipient.name,
                parsed.received_at,
                allow_person=recipient.kind not in self._NON_PERSON_RECIPIENT_KINDS,
            )
            await self._emails.add_recipient(
                EmailRecipient(
                    org_id=org_id,
                    email_id=email_id,
                    kind=recipient.kind,
                    name=recipient.name,
                    address=recipient.address,
                    person_id=person_id,
                )
            )

    async def _store_attachments(self, org_id: UUID, email_id: UUID, parsed: ParsedEmail) -> None:
        """Insert each attachment with inline-extracted text; the raw bytes are dropped."""
        for attachment in parsed.attachments:
            await self._emails.add_attachment(
                EmailAttachment(
                    org_id=org_id,
                    email_id=email_id,
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    size_bytes=attachment.size_bytes,
                    content_hash=attachment.content_hash,
                    is_inline=attachment.is_inline,
                    content_id=attachment.content_id,
                    extracted_text=extract_text(attachment),
                )
            )
