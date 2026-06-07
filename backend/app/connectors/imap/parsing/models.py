"""
Role: Pure, ORM-free value objects for a parsed email — the parser's output contract. Mirror the
      Layer-1 columns (email_message / email_recipient / email_attachment) without importing
      SQLAlchemy, so parsing stays a deterministic pure function that the store step maps to rows.
Used by: app.connectors.imap.parsing.email_parser (produces these), attachment_extractor (fills
         ParsedAttachment.extracted_text), and the later ingest runner (maps these → ORM rows).
Depends on: stdlib only (dataclasses, datetime).
Key invariants:
  - These objects carry NO org_id / connection_id — those are tenancy/source context the runner
    supplies when it maps a ParsedEmail to ORM rows. The parser is content-only and source-agnostic.
  - `dedup_key` is ALWAYS set: the Message-ID when present, else a sha256 of the RAW rfc822 bytes
    (never of derived body_text — derived text changes across library versions and would break the
    idempotent upsert). It is the email's stable logical identity for `ON CONFLICT DO NOTHING`.
  - `ParsedAttachment.payload` is TRANSIENT raw bytes for the extractor; it is dropped after text
    extraction and is NEVER persisted (lean-attachments decision, design §4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# Recipient roles mirrored by the email_recipient.kind CHECK constraint. 'sender' is the envelope
# From; reply_to is the Reply-To header. to/cc/bcc are the standard destination headers. The Literal
# gives the parser (and any future producer) a static guarantee that matches the DB CHECK.
RecipientKind = Literal["to", "cc", "bcc", "reply_to", "sender"]


@dataclass(frozen=True, slots=True)
class ParsedRecipient:
    """One address on an email, with its role. `address` is the RAW as-seen value (the normalized
    match key is derived later by the entity resolver, never stored here)."""

    kind: RecipientKind
    address: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedAttachment:
    """An attachment's metadata + its transient raw bytes. The ingest runner derives the stored text
    by calling attachment_extractor.extract_text(self); `payload` is dropped after and never
    persisted (lean-attachments decision, design §4)."""

    filename: str | None
    content_type: str
    size_bytes: int
    content_hash: str
    is_inline: bool
    content_id: str | None
    payload: bytes


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    """The full deterministic decomposition of one logical email (design §4). ORM-free; the runner
    maps it to email_message + recipient + attachment rows under an (org, connection)."""

    dedup_key: str
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    from_name: str | None
    from_address: str | None
    subject: str | None
    sent_at: datetime | None
    received_at: datetime | None
    body_text: str
    recipients: list[ParsedRecipient]
    attachments: list[ParsedAttachment]
    direction: str | None
    is_automated: bool
    is_reply: bool
    has_attachments: bool
    word_count: int
    language: str | None
    headers: dict[str, str | list[str]]
    parse_status: str = "parsed"
