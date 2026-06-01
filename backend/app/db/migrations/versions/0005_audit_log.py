"""append-only audit_log table + immutability trigger

Creates the audit_log table (the who/what/when/where/why action trail), its two
read indexes, the actor_type CHECK, and the BEFORE UPDATE OR DELETE trigger that
makes the table APPEND-ONLY against DML — robust even under the current superuser
app role (a superuser bypasses RLS but still fires triggers).

Mirrors app.identity.models.audit_log exactly (the model attaches the same trigger via
an after_create DDL event for the create_all test path). The table is intentionally:
  - NOT tenant-scoped and NOT under any RLS policy (cross-org compliance data; a future
    tenant-scoped session must still be able to INSERT here), and
  - free of foreign keys to organizations/users, so a row survives actor/org deletion
    (durable attribution via the denormalized actor_email) — PC-04-AC7 + PC-06 erasure.

Revision ID: 0005_audit_log
Revises: 0004_org_lifecycle
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_audit_log"
down_revision: str | None = "0004_org_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APPEND_ONLY_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION audit_log_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

_APPEND_ONLY_TRIGGER_SQL = """
CREATE TRIGGER audit_log_no_mutation
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_block_mutation();
"""


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(length=320), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "actor_type IN ('platform_admin', 'user', 'system')",
            name="ck_audit_log_actor_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_log_org_occurred", "audit_log", ["org_id", "occurred_at"]
    )
    op.create_index(
        "ix_audit_log_action_occurred", "audit_log", ["action", "occurred_at"]
    )
    op.execute(_APPEND_ONLY_FUNCTION_SQL)
    op.execute(_APPEND_ONLY_TRIGGER_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_mutation ON audit_log")
    op.drop_index("ix_audit_log_action_occurred", table_name="audit_log")
    op.drop_index("ix_audit_log_org_occurred", table_name="audit_log")
    op.drop_table("audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_block_mutation()")
