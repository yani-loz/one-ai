"""email ingestion schema — entity graph + email Layer-1 tables (step 3)

Creates the shared cross-source entity Core (person / person_email / person_alias / company /
company_domain / person_company) and the IMAP email Layer-1 tables (email_message,
email_recipient, email_attachment). Mirrors app.entities.models + app.connectors.imap.models.

Tenancy: every table is tenant-scoped (org_id NOT NULL + index) and gets an inert org_isolation RLS
policy (defined now, enforced once the least-privilege DB role lands — see 0003 + FIX_BEFORE_PROD).

Dedup: email_message uniqueness is on a NON-NULL `dedup_key` (Message-ID, else a content hash) —
UNIQUE(org_id, connection_id, dedup_key) — because a UNIQUE on the nullable message_id would treat
NULLs as distinct and never dedup. Match keys: person_email.email UNIQUE(org_id, email);
company_domain.domain UNIQUE(org_id, domain).

Cascades: email_message.connection_id -> connector_connection ON DELETE CASCADE (delete-a-connection
purges its email, DB-enforced). Person links (from_person_id / recipient.person_id) -> person ON
DELETE SET NULL (the resolver reassigns on merges). The shared person/company graph is NEVER
cascade-deleted from a connection. Recipients/attachments -> email_message ON DELETE CASCADE.

Erasure: all nine tables hold tenant PII and MUST join the org-erasure path (tracked in
FIX_BEFORE_PROD as CA-CONN-03); wiring is deferred (identity->connectors/entities import cycle).

Revision ID: 0008_email_ingestion_schema
Revises: 0007_connector_connection
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_email_ingestion_schema"
down_revision: str | None = "0007_connector_connection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table created here is tenant-scoped and gets the inert org-isolation RLS policy.
_TENANT_TABLES = [
    "person",
    "person_email",
    "person_alias",
    "company",
    "company_domain",
    "person_company",
    "email_message",
    "email_recipient",
    "email_attachment",
]


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def _org_id() -> sa.Column:
    return sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    # ── entity Core ───────────────────────────────────────────────────────────
    op.create_table(
        "person",
        _uuid_pk(),
        _org_id(),
        sa.Column("display_name", sa.String(length=320), nullable=True),
        sa.Column("is_internal", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_person_org_id", "person", ["org_id"])

    op.create_table(
        "person_email",
        _uuid_pk(),
        _org_id(),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("person.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "email", name="uq_person_email_identity"),
    )
    op.create_index("ix_person_email_org_id", "person_email", ["org_id"])
    op.create_index("ix_person_email_person_id", "person_email", ["person_id"])

    op.create_table(
        "person_alias",
        _uuid_pk(),
        _org_id(),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("person.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(length=320), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_person_alias_org_id", "person_alias", ["org_id"])
    op.create_index("ix_person_alias_person_id", "person_alias", ["person_id"])

    op.create_table(
        "company",
        _uuid_pk(),
        _org_id(),
        sa.Column("name", sa.String(length=320), nullable=True),
        sa.Column("is_internal", sa.Boolean(), server_default="false", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_company_org_id", "company", ["org_id"])

    op.create_table(
        "company_domain",
        _uuid_pk(),
        _org_id(),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "domain", name="uq_company_domain_identity"),
    )
    op.create_index("ix_company_domain_org_id", "company_domain", ["org_id"])
    op.create_index("ix_company_domain_company_id", "company_domain", ["company_id"])

    op.create_table(
        "person_company",
        _uuid_pk(),
        _org_id(),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("person.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("company.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "person_id", "company_id", name="uq_person_company_identity"
        ),
    )
    op.create_index("ix_person_company_org_id", "person_company", ["org_id"])
    op.create_index("ix_person_company_person_id", "person_company", ["person_id"])
    op.create_index("ix_person_company_company_id", "person_company", ["company_id"])

    # ── email Layer-1 ─────────────────────────────────────────────────────────
    op.create_table(
        "email_message",
        _uuid_pk(),
        _org_id(),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connector_connection.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.String(length=998), nullable=False),
        sa.Column("message_id", sa.String(length=998), nullable=True),
        sa.Column("in_reply_to", sa.String(length=998), nullable=True),
        sa.Column("references", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("from_name", sa.String(length=320), nullable=True),
        sa.Column("from_address", sa.String(length=320), nullable=True),
        sa.Column(
            "from_person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("person.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("direction", sa.String(length=10), nullable=True),
        sa.Column("is_automated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_reply", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("has_attachments", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column(
            "headers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("parse_status", sa.String(length=10), server_default="parsed", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('inbound', 'outbound')",
            name="ck_email_message_direction",
        ),
        sa.CheckConstraint(
            "parse_status IN ('parsed', 'failed')", name="ck_email_message_parse_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "connection_id", "dedup_key", name="uq_email_message_dedup"
        ),
    )
    op.create_index("ix_email_message_org_id", "email_message", ["org_id"])
    op.create_index("ix_email_message_connection_id", "email_message", ["connection_id"])
    op.create_index("ix_email_message_from_person_id", "email_message", ["from_person_id"])
    op.create_index("ix_email_message_message_id", "email_message", ["message_id"])

    op.create_table(
        "email_recipient",
        _uuid_pk(),
        _org_id(),
        sa.Column(
            "email_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_message.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=320), nullable=True),
        sa.Column("address", sa.String(length=320), nullable=False),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("person.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('to', 'cc', 'bcc', 'reply_to', 'sender')",
            name="ck_email_recipient_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_recipient_org_id", "email_recipient", ["org_id"])
    op.create_index("ix_email_recipient_email_id", "email_recipient", ["email_id"])
    op.create_index("ix_email_recipient_person_id", "email_recipient", ["person_id"])

    op.create_table(
        "email_attachment",
        _uuid_pk(),
        _org_id(),
        sa.Column(
            "email_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_message.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=998), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("is_inline", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("content_id", sa.String(length=998), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_attachment_org_id", "email_attachment", ["org_id"])
    op.create_index("ix_email_attachment_email_id", "email_attachment", ["email_id"])

    # ── inert org-isolation RLS policy on every tenant table (0003 / 0007 idiom) ──
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY org_isolation ON {table}
                USING (org_id = current_setting('app.current_org_id', true)::uuid)
                WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)
            """
        )


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    # Drop in reverse dependency order (children before parents).
    op.drop_table("email_attachment")
    op.drop_table("email_recipient")
    op.drop_table("email_message")
    op.drop_table("person_company")
    op.drop_table("company_domain")
    op.drop_table("company")
    op.drop_table("person_alias")
    op.drop_table("person_email")
    op.drop_table("person")
