"""
Role: Email Layer-1 ORM models — the IMAP connector's source-of-record tables: one parsed message
      plus its recipients and attachments. The "natural" per-connector data (Bible §15 Layer 1).
Used by: the IMAP email repository + (later) the parser/ingest; the standing RLS-invariant test +
         test fixtures register these via app.connectors.imap.models.
Depends on: app.common.base_model (Base + mixins), SQLAlchemy + postgresql dialect (JSONB/ARRAY).
            FKs to `person` (app.entities) and `connector_connection` (app.connectors) are STRING
            references — no Python import of those models, only same-Base.metadata registration.
Key invariants:
  - TENANT-SCOPED (org_id NOT NULL + indexed). RLS policy defined in the migration (inert until the
    least-privilege role lands — the RLS engine-flip).
  - ONE ROW PER LOGICAL EMAIL: dedup on a NON-NULL `dedup_key` = a CONTENT IDENTITY (sha256 of
    normalized Message-ID + decoded From/Subject/Date + decoded body text + sorted attachment
    content hashes; raw-byte hash when no usable Message-ID — see email_parser._dedup_key).
    FOLDER-INDEPENDENT: one email seen in N IMAP folders dedups to ONE row even though Outlook
    regenerates the MIME boundaries inside each folder copy's raw bytes (audit H-1 — the reason
    DECODED content, never raw serialization, is hashed), yet injective enough that two distinct
    emails never collide. UNIQUE(org_id, connection_id, dedup_key). Folders are NOT stored.
    `body_text` is the decoded extraction (LF-normalized, C0 controls stripped; NOT content-
    cleaned). Raw bytes are NOT kept.
  - `connection_id` → connector_connection ON DELETE CASCADE = delete-a-connection purges its email
    (DB-enforced). from_person_id / recipient person_id → person ON DELETE SET NULL (resolver
    reassigns on merges). The shared person/company graph is NEVER cascade-deleted.
  - Recipients/attachments → email_message ON DELETE CASCADE (children of the message).
  - 0014 TENANT-COHERENCE GUARDS (mirrored here so models == migrated schema): org_id FKs
    organizations(id) on all three tables (no phantom tenants); every child FK is the COMPOSITE
    (org_id, parent_id) form anchored on the parents' UNIQUE (org_id, id), so child.org_id =
    parent.org_id is DB-enforced; recipient edges are UNIQUE (email_id, kind, address). The two
    person FKs null ONLY the person pointer on delete: PG-15+ `ON DELETE SET NULL (<col>)`,
    declared verbatim via ondelete="SET NULL (<col>)" (passes through to DDL + matches
    reflection, so the schema-parity gate sees no drift).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class EmailMessage(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """One logical email (deduped by Message-ID/content hash) with its decoded envelope + body."""

    __tablename__ = "email_message"
    __table_args__ = (
        UniqueConstraint("org_id", "connection_id", "dedup_key", name="uq_email_message_dedup"),
        # 0014 composite-FK anchor: children FK (org_id, id) so their org_id can never diverge.
        UniqueConstraint("org_id", "id", name="uq_email_message_org_row"),
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_email_message_org_id"
        ),
        ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["connector_connection.org_id", "connector_connection.id"],
            ondelete="CASCADE",
            name="fk_email_message_connection_id_org",
        ),
        # PG-15+ column-list SET NULL: only the person pointer is nulled on person delete
        # (org_id is NOT NULL). The string passes through to DDL and matches reflection.
        ForeignKeyConstraint(
            ["org_id", "from_person_id"],
            ["person.org_id", "person.id"],
            ondelete="SET NULL (from_person_id)",
            name="fk_email_message_from_person_id_org",
        ),
        CheckConstraint(
            "direction IS NULL OR direction IN ('inbound', 'outbound')",
            name="ck_email_message_direction",
        ),
        CheckConstraint(
            "parse_status IN ('parsed', 'failed')", name="ck_email_message_parse_status"
        ),
    )

    connection_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    # Content-identity dedup key: sha256 of normalized Message-ID + decoded From/Subject/Date +
    # decoded body text + sorted attachment content hashes, else raw-byte hash (folder-independent
    # yet injective; never the bare Message-ID). See email_parser._dedup_key.
    dedup_key: Mapped[str] = mapped_column(String(998), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(998), index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(998))
    references: Mapped[list[str] | None] = mapped_column(postgresql.ARRAY(String))
    from_name: Mapped[str | None] = mapped_column(String(320))
    from_address: Mapped[str | None] = mapped_column(String(320))
    from_person_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        index=True,
    )
    subject: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Decoded extraction (plain or html→text; LF-normalized, C0 controls stripped — audit L-6),
    # NOT content-cleaned. Raw RFC822 bytes are not stored.
    body_text: Mapped[str | None] = mapped_column(Text)
    direction: Mapped[str | None] = mapped_column(String(10))
    is_automated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    word_count: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(16))
    # Full header set (cheap; preserves metadata even though raw bytes are discarded). Shape matches
    # the parser's ParsedEmail.headers: a repeated header collapses to a list of its values.
    headers: Mapped[dict[str, str | list[str]]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    parse_status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="parsed")


class EmailRecipient(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """A recipient of an email (to/cc/bcc/reply_to/sender) — raw address + resolved person link."""

    __tablename__ = "email_recipient"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('to', 'cc', 'bcc', 'reply_to', 'sender')",
            name="ck_email_recipient_kind",
        ),
        # 0014 M-6 guard: one edge per (message, kind, address) — re-ingest can't duplicate.
        UniqueConstraint("email_id", "kind", "address", name="uq_email_recipient_edge"),
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_email_recipient_org_id"
        ),
        ForeignKeyConstraint(
            ["org_id", "email_id"],
            ["email_message.org_id", "email_message.id"],
            ondelete="CASCADE",
            name="fk_email_recipient_email_id_org",
        ),
        # PG-15+ column-list SET NULL: only person_id is nulled on person delete.
        ForeignKeyConstraint(
            ["org_id", "person_id"],
            ["person.org_id", "person.id"],
            ondelete="SET NULL (person_id)",
            name="fk_email_recipient_person_id_org",
        ),
    )

    email_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str | None] = mapped_column(String(320))
    address: Mapped[str] = mapped_column(String(320), nullable=False)  # raw, as-seen
    person_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        index=True,
    )


class EmailAttachment(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """Attachment metadata + extracted text (original bytes are NOT kept — lean storage)."""

    __tablename__ = "email_attachment"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_email_attachment_org_id"
        ),
        ForeignKeyConstraint(
            ["org_id", "email_id"],
            ["email_message.org_id", "email_message.id"],
            ondelete="CASCADE",
            name="fk_email_attachment_email_id_org",
        ),
    )

    email_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    filename: Mapped[str | None] = mapped_column(String(998))
    content_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    is_inline: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    content_id: Mapped[str | None] = mapped_column(String(998))
    # Text extracted inline at parse (later stage); original bytes discarded.
    extracted_text: Mapped[str | None] = mapped_column(Text)
