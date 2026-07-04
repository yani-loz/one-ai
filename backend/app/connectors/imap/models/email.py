"""
Role: Email Layer-1 ORM models — the IMAP connector's source-of-record tables: one parsed message
      plus its recipients and attachments. The "natural" per-connector data (Bible §15 Layer 1).
Used by: the IMAP email repository + (later) the parser/ingest; the standing RLS-invariant test +
         test fixtures register these via app.connectors.imap.models.
Depends on: app.common.base_model (Base + mixins), SQLAlchemy + postgresql dialect (JSONB/ARRAY),
            app.connectors.extraction.extraction_result (the status vocabulary the 0015 CHECK
            pins). FKs to `person` (app.entities) and `connector_connection` (app.connectors) are
            STRING references — no Python import of those models, only same-Base.metadata
            registration.
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
  - 0015 EXTRACTION STATUS (CA-CONN-04): email_attachment.extraction_status is a CHECK-pinned
    closed vocabulary — the SAME set as extraction.extraction_result.EXTRACTION_STATUSES (the
    Python source of truth; tests/db assert the two never drift). `extracted_text` is non-NULL
    only for text-bearing statuses (extracted/truncated/extracted_partial_scanned) — honest NULL
    with a reason, never ''.
    extractor_name/extractor_version record WHICH engine produced the row (version-aware
    backfills); the (org_id, content_hash) index is the backfill/dedup lookup key 0014 deferred.
  - 0016 EXTRACTION DETAIL (EQ-7): email_attachment.extraction_detail persists
    ExtractionResult.detail (truncation reason, image_only_pages=N OCR backlog counts, corrupt
    exception class names) — nullable, machine-readable, NEVER attachment content verbatim
    (class names / fixed phrases / counts only — the contract's standing invariant).
  - 0017 EXTRACTED DATA (design §2.5): email_attachment.extracted_data persists
    ExtractionResult.structured — the typed, analysis-ready cell grid for formats that are DATA not
    prose (xlsx/xlsm: the lossless 'xlsx-grid-v1' grid, the source of truth alongside the embeddable
    extracted_text render). Nullable JSONB; NULL for every text/document format (structured is None
    there). Rides the table's existing tenant grants/RLS/erasure — a column, not a new table.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import (
    Base,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    VisibilityScopedMixin,
)
from app.connectors.extraction.extraction_result import EXTRACTION_STATUSES


class EmailMessage(Base, UUIDPrimaryKeyMixin, TenantMixin, VisibilityScopedMixin, TimestampMixin):
    """One logical email (deduped by Message-ID/content hash) with its decoded envelope + body.

    PF-01 (0019): a CONTENT table — carries visibility_scope/origin_scope/container_id
    (container_id = connection_id, the mailbox) under a RESTRICTIVE `visibility` RLS policy
    targeting the READER role (the retrieval plane); per-message acl_grant rows decide who
    retrieves it. Children (recipient/attachment) inherit the scopes in the same ingest
    transaction and bind to THIS row's grants via email_id.
    """

    __tablename__ = "email_message"
    __table_args__ = (
        UniqueConstraint("org_id", "connection_id", "dedup_key", name="uq_email_message_dedup"),
        # 0014 composite-FK anchor: children FK (org_id, id) so their org_id can never diverge.
        UniqueConstraint("org_id", "id", name="uq_email_message_org_row"),
        ForeignKeyConstraint(["org_id"], ["organizations.id"], name="fk_email_message_org_id"),
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
    # Data-minimized header allowlist (CA-CONN-05): identity/threading/addressing + the automation
    # flag-source headers only — the Received/Auth/X-*/Authorization/Bcc PII+secret surface is cut
    # at parse time (headers.allowlist_headers). Shape matches the parser's ParsedEmail.headers: a
    # repeated header collapses to a list of its values.
    headers: Mapped[dict[str, str | list[str]]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    parse_status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="parsed")


class EmailRecipient(Base, UUIDPrimaryKeyMixin, TenantMixin, VisibilityScopedMixin, TimestampMixin):
    """A recipient of an email (to/cc/bcc/reply_to/sender) — raw address + resolved person link.

    PF-01 (0019): a CONTENT table (recipient identities are the message's PII edge) — inherits
    the parent message's visibility_scope/origin_scope/container_id in the same ingest
    transaction; its `visibility` policy binds to the PARENT's grants via email_id (AC22's
    chunk→grant contract, exercised today on the children that already exist).
    """

    __tablename__ = "email_recipient"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('to', 'cc', 'bcc', 'reply_to', 'sender')",
            name="ck_email_recipient_kind",
        ),
        # 0014 M-6 guard: one edge per (message, kind, address) — re-ingest can't duplicate.
        UniqueConstraint("email_id", "kind", "address", name="uq_email_recipient_edge"),
        ForeignKeyConstraint(["org_id"], ["organizations.id"], name="fk_email_recipient_org_id"),
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


class EmailAttachment(
    Base, UUIDPrimaryKeyMixin, TenantMixin, VisibilityScopedMixin, TimestampMixin
):
    """Attachment metadata + extracted text (original bytes are NOT kept — lean storage).

    PF-01 (0019): a CONTENT table (extracted_text is the embeddable document substrate) —
    inherits the parent message's visibility scopes in the same ingest transaction; its
    `visibility` policy (READER role) binds to the PARENT's grants via email_id (AC22
    contract). The content-addressed extraction-reuse lookup runs on the WRITE plane, where
    the policy does not apply — reusing an extraction requires already HOLDING the identical
    bytes, so it is not a read of another principal's content.
    """

    __tablename__ = "email_attachment"
    __table_args__ = (
        ForeignKeyConstraint(["org_id"], ["organizations.id"], name="fk_email_attachment_org_id"),
        ForeignKeyConstraint(
            ["org_id", "email_id"],
            ["email_message.org_id", "email_message.id"],
            ondelete="CASCADE",
            name="fk_email_attachment_email_id_org",
        ),
        # 0015: pin the closed extraction-status vocabulary at the DB layer too — kept in
        # lockstep with extraction.extraction_result.EXTRACTION_STATUSES (sorted for determinism).
        CheckConstraint(
            "extraction_status IN ({})".format(
                ", ".join(f"'{status}'" for status in sorted(EXTRACTION_STATUSES))
            ),
            name="ck_email_attachment_extraction_status",
        ),
        # 0015: the hash-keyed extraction/backfill lookup 0014 deferred to CA-CONN-04.
        Index("ix_email_attachment_org_content_hash", "org_id", "content_hash"),
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
    # Text extracted inline at parse; original bytes discarded. Non-NULL only for the text-bearing
    # statuses (extracted/truncated/extracted_partial_scanned) — honest NULL with a reason,
    # never ''.
    extracted_text: Mapped[str | None] = mapped_column(Text)
    # WHY extracted_text is (or is not) populated — the 0015 CHECK-pinned vocabulary.
    extraction_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    # Provenance: which engine + version produced this row (version-aware backfill targeting).
    extractor_name: Mapped[str | None] = mapped_column(Text)
    extractor_version: Mapped[str | None] = mapped_column(Text)
    # 0016 (EQ-7): the ExtractionResult.detail — WHY/HOW the status was reached (truncation
    # reason, OCR backlog counts, exception class names). Never attachment content verbatim.
    extraction_detail: Mapped[str | None] = mapped_column(Text)
    # 0017 (design §2.5): the ExtractionResult.structured — a typed, analysis-ready cell grid for
    # formats that are DATA not prose (xlsx/xlsm: the lossless 'xlsx-grid-v1' typed grid, the source
    # of truth alongside the embeddable extracted_text render). NULL for every text/document format
    # (structured is None there) — backward-compatible.
    # none_as_null=True is LOAD-BEARING: SQLAlchemy's JSONB default maps Python None -> the JSONB
    # literal 'null' (a present value), so `WHERE extracted_data IS NOT NULL` would match every
    # prose attachment and "has structured data?" becomes unanswerable. With it, None -> SQL NULL.
    extracted_data: Mapped[dict | None] = mapped_column(postgresql.JSONB(none_as_null=True))
