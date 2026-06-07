"""connector_connection table

Creates the connector_connection table — one org's configured connection to a data source
(point 1: an IMAP mailbox). Holds non-secret params (config JSONB: host/port/use_ssl) and the
credential ENCRYPTED at rest (secret_ciphertext BYTEA, AES-256-GCM; secret_key_version records
the app key used). Mirrors app.connectors.models.connector_connection.

Tenant-tagged (org_id NOT NULL + index). connector_type / auth_method / status are pinned by
CHECKs to the connectors enum values; (org_id, connector_type, username) is unique per org. No
FK to organizations (kept simple + consistent with support_grant); wiring this table into the
GDPR erasure path is tracked in docs/FIX_BEFORE_PROD.md (credentials must be erased on
offboarding).

Like users (0003) / support_grant (0006), an inert org-isolation RLS policy is DEFINED here;
enforcement is deferred until the app connects as a least-privilege role (the superuser app role
bypasses it today). Credentials being encrypted at rest means a pre-flip app-layer bug would
leak only ciphertext — but the RLS engine-flip remains the hard gate before the content tables.

Revision ID: 0007_connector_connection
Revises: 0006_support_grant
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_connector_connection"
down_revision: str | None = "0006_support_grant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_connection",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_type", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column(
            "auth_method",
            sa.String(length=20),
            server_default="app_password",
            nullable=False,
        ),
        sa.Column("username", sa.String(length=320), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("secret_ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column(
            "secret_key_version", sa.SmallInteger(), server_default="1", nullable=False
        ),
        sa.Column(
            "status", sa.String(length=20), server_default="configured", nullable=False
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
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
        sa.CheckConstraint(
            "connector_type IN ('imap')", name="ck_connector_connection_type"
        ),
        sa.CheckConstraint(
            "auth_method IN ('app_password')", name="ck_connector_connection_auth_method"
        ),
        sa.CheckConstraint(
            "status IN ('configured', 'connected', 'error')",
            name="ck_connector_connection_status",
        ),
        sa.UniqueConstraint(
            "org_id", "connector_type", "username", name="uq_connector_connection_identity"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_connector_connection_org_id", "connector_connection", ["org_id"]
    )
    # Inert org-isolation policy (defined now, enforced later — see 0003 + FIX_BEFORE_PROD).
    op.execute("ALTER TABLE connector_connection ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY org_isolation ON connector_connection
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
            WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_isolation ON connector_connection")
    op.execute("ALTER TABLE connector_connection DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_connector_connection_org_id", table_name="connector_connection"
    )
    op.drop_table("connector_connection")
