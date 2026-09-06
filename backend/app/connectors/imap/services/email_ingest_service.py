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
    TWO layers since PF-01: the exists() fast path, PLUS a SAVEPOINT-guarded insert that maps a
    dedup-unique violation to SKIPPED — the insert conflict is the truth under ANY read-
    visibility regime (belt-and-braces for concurrent syncs and any future policy narrowing;
    the 0019 visibility policies target the READER role, not this write plane).
  - PF-01 CAPTURE (0019): every stored row is born visibility_scope='restricted' /
    origin_scope='restricted' with container_id = the connection (mailbox); children inherit in
    the SAME transaction (AC22). After storing, the GrantWriter derives per-message acl_grant
    rows for the connection owner + every verified participant (UNKNOWN ⇒ DENY inside the
    writer) — grants commit or roll back WITH the message.
  - Returns a plain IngestOutcome enum, NEVER a live ORM row (a row read after the caller's commit
    could lazy-load on a closed greenlet). The CALLER owns the transaction + commit.
  - Attachment text is extracted inline and the bytes are dropped (lean storage, design §4);
    each row stores the ExtractionResult's status + detail (0016, EQ-7) + extractor provenance
    (0015) + the typed structured grid (0017, design §2.5 — NULL for non-xlsx) — honest NULL text
    always carries its machine-readable reason.
  - CONTENT-ADDRESSED EXTRACTION (study §8.4, perf lane #1): before running an extractor, the
    ingest reuses the newest attempted extraction of the SAME (org, content_hash, content_type) —
    extraction is a pure function of (bytes, declared type), and 59% of real-corpus attachment
    rows are byte-identical dups, so the copy is exact and skips ~a quarter of pipeline CPU.
    The reused row's provenance (extractor_name/version) is copied verbatim, so version-aware
    backfills still target every row extracted under an old engine. 'pending' rows are never
    reused (nothing ever ran on them).
  - CPU work is OFF-LOOP: parse_email (RFC822 parse, base64 decode, sha256, html2text) AND
    extract_text (pdfplumber + tables + possible pypdf over ≤50MB payloads) both run on a WORKER
    thread (asyncio.to_thread) so a large email or PDF never stalls the event loop mid-sync.
    Per-email transaction semantics are unchanged — neither call touches the session.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.services.grant_writer import GrantWriter
from app.connectors.extraction.extraction_result import ExtractionResult
from app.connectors.imap.models.email import EmailAttachment, EmailMessage, EmailRecipient
from app.connectors.imap.parsing import (
    DISCLOSED_RECIPIENT_KINDS,
    ParsedAttachment,
    ParsedEmail,
    extract_text,
    parse_email,
)
from app.connectors.imap.repositories.email_repository import EmailMessageRepository
from app.connectors.models.connector_connection import ConnectorConnection
from app.entities.services.entity_resolver import EntityResolver


class IngestOutcome(StrEnum):
    """The result of ingesting one email (FAILED is the driver's tally for a raised error)."""

    STORED = "stored"
    SKIPPED = "skipped"  # already present (idempotent re-run or same email in another folder)


def _disclosed_addresses(parsed: ParsedEmail) -> list[str]:
    """The to/cc addresses — the only recipient kinds that may derive an access grant.

    See DISCLOSED_RECIPIENT_KINDS for why bcc/reply_to/sender are excluded. Both the grant WRITE
    and the grant RECONCILE paths go through here, so the two can never disagree about which
    fields are authoritative.
    """
    return [
        recipient.address
        for recipient in parsed.recipients
        if recipient.kind in DISCLOSED_RECIPIENT_KINDS
    ]


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
        self._owner_user_id = connection.owner_user_id
        self._mailbox = connection.username
        self._emails = EmailMessageRepository(session)
        self._resolver = EntityResolver(
            session, mailbox_address=connection.username, source=connection.connector_type
        )
        self._grants = GrantWriter(session)

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

        existing_id = await self._emails.get_message_id_by_dedup(
            org_id, connection_id, parsed.dedup_key
        )
        if existing_id is not None:
            # Re-seen message: RECONCILE its grants instead of just skipping (PF-01 AC21) — a
            # binding verified since the first ingest starts matching, a de-verified one is
            # tombstoned, and a re-run over the pre-0019 corpus becomes the grant backfill pass.
            await self._grants.reconcile_email_message_grants(
                org_id,
                existing_id,
                connection_id,
                self._owner_user_id,
                parsed.from_address,
                _disclosed_addresses(parsed),
            )
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

        # SAVEPOINT-guarded insert: the dedup UNIQUE conflict is the idempotency truth under any
        # read-visibility regime (a concurrent sync — or a future policy narrowing — can make a
        # duplicate invisible to the exists() SELECT), mapped to SKIPPED. Any OTHER integrity
        # error is a real bug and must surface.
        try:
            async with self._session.begin_nested():
                message = await self._emails.insert(
                    self._build_message(
                        org_id, connection_id, parsed, from_person_id, len(raw_bytes)
                    )
                )
        except IntegrityError as error:
            if "uq_email_message_dedup" not in str(error.orig):
                raise
            racing_id = await self._emails.get_message_id_by_dedup(
                org_id, connection_id, parsed.dedup_key
            )
            if racing_id is not None:  # reconcile the winner's grants, same as the fast path
                await self._grants.reconcile_email_message_grants(
                    org_id,
                    racing_id,
                    connection_id,
                    self._owner_user_id,
                    parsed.from_address,
                    _disclosed_addresses(parsed),
                )
            return IngestOutcome.SKIPPED
        await self._store_recipients(org_id, message.id, parsed)
        await self._store_attachments(org_id, message.id, parsed)
        await self._grants.write_email_grants(
            org_id,
            message.id,
            connection_id,
            self._owner_user_id,
            parsed.from_address,
            _disclosed_addresses(parsed),
        )
        return IngestOutcome.STORED

    def _build_message(
        self,
        org_id: UUID,
        connection_id: UUID,
        parsed: ParsedEmail,
        from_person_id: UUID | None,
        size_bytes: int,
    ) -> EmailMessage:
        """Map a ParsedEmail to an EmailMessage row (size_bytes is the raw wire length).

        PF-01: born restricted/restricted with container_id = the connection (mailbox) — email is
        recipient-granted per message, never org-born (that is public-Slack territory).
        """
        return EmailMessage(
            org_id=org_id,
            connection_id=connection_id,
            visibility_scope="restricted",
            origin_scope="restricted",
            container_id=connection_id,
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
                # A RECIPIENT display name is chosen by the SENDER, about somebody else, and
                # `_enrich_person` back-fills `display_name` first-writer-wins. So mailing a
                # synced mailbox `To: "Chief Fraud Officer" <cfo@corp.com>` before any named
                # sighting of that address named that person PERMANENTLY, and `find_person`
                # then reported it as who they are (R5 write-plane W4). A sender naming
                # THEMSELVES in `From:` is a claim about their own identity and stays allowed;
                # naming a third party is not, so the address alone resolves them.
                None,
                parsed.received_at,
                allow_person=recipient.kind not in self._NON_PERSON_RECIPIENT_KINDS,
            )
            await self._emails.add_recipient(
                EmailRecipient(
                    org_id=org_id,
                    email_id=email_id,
                    # AC22 same-transaction inheritance: children carry the parent's birth scopes.
                    visibility_scope="restricted",
                    origin_scope="restricted",
                    container_id=self._connection_id,
                    kind=recipient.kind,
                    name=recipient.name,
                    address=recipient.address,
                    person_id=person_id,
                )
            )

    async def _store_attachments(self, org_id: UUID, email_id: UUID, parsed: ParsedEmail) -> None:
        """Insert each attachment with inline-extracted text + the extraction outcome; the raw
        bytes are dropped. Status + extractor provenance come straight off the ExtractionResult
        (the seam never raises), so every row records WHY its text is present or honestly NULL.
        extract_text is pure CPU (pdfplumber/pypdf over up-to-50MB payloads) — off-loaded to a
        worker thread, same as parse_email, so a heavy PDF never stalls the loop mid-sync.
        Byte-identical duplicates reuse the prior row's outcome instead (content-addressed
        extraction — see the module invariant)."""
        for attachment in parsed.attachments:
            extraction = await self._reuse_or_extract(org_id, attachment)
            await self._emails.add_attachment(
                EmailAttachment(
                    org_id=org_id,
                    email_id=email_id,
                    # AC22 same-transaction inheritance: children carry the parent's birth scopes.
                    visibility_scope="restricted",
                    origin_scope="restricted",
                    container_id=self._connection_id,
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    size_bytes=attachment.size_bytes,
                    content_hash=attachment.content_hash,
                    is_inline=attachment.is_inline,
                    content_id=attachment.content_id,
                    extracted_text=extraction.text,
                    extraction_status=extraction.status,
                    extraction_detail=extraction.detail,
                    extractor_name=extraction.extractor_name,
                    extractor_version=extraction.extractor_version,
                    extracted_data=extraction.structured,
                )
            )

    async def _reuse_or_extract(
        self, org_id: UUID, attachment: ParsedAttachment
    ) -> ExtractionResult:
        """Reuse the newest attempted extraction of identical bytes, else run the extractor.

        Content-addressed extraction (perf lane #1): the lookup key is (org, content_hash,
        content_type) — extraction is a pure function of the payload bytes and the declared type,
        so a prior outcome for the same key IS this attachment's outcome; the extractor run
        (188ms/email average, 48% of pipeline time) is skipped entirely. The copy carries the
        prior row's provenance verbatim so version-aware backfills still see the true engine.
        Falls through to a real extraction when no attempted prior row exists (first sighting,
        or only 'pending' rows predating the extractor).
        """
        prior = await self._emails.get_prior_extraction(
            org_id, attachment.content_hash, attachment.content_type
        )
        if prior is not None:
            return ExtractionResult(
                text=prior.extracted_text,
                status=prior.extraction_status,
                detail=prior.extraction_detail,
                extractor_name=prior.extractor_name,
                extractor_version=prior.extractor_version,
                structured=prior.extracted_data,
            )
        return await asyncio.to_thread(extract_text, attachment)
