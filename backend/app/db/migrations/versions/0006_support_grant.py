"""break-glass support_grant table

Creates the support_grant table — the break-glass consent record: a platform admin
requests time-boxed access to a tenant, a company_admin of that tenant approves/denies,
and access is computed live against expires_at. Mirrors app.identity.models.support_grant.

Tenant-tagged (org_id NOT NULL + index) but with NO foreign keys (durable attribution via
denormalized emails — the row survives actor/org deletion, like audit_log). status is pinned
to requested|approved|denied|revoked by a CHECK; there is no stored `expired` state (expiry
is computed). The table is mutable (status transitions), so it carries no append-only trigger.

Revision ID: 0006_support_grant
Revises: 0005_audit_log
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_support_grant"
down_revision: str | None = "0005_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_grant",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_admin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_email", sa.String(length=320), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="requested", nullable=False
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_by_email", sa.String(length=320), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('requested', 'approved', 'denied', 'revoked')",
            name="ck_support_grant_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_grant_org_id", "support_grant", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_support_grant_org_id", table_name="support_grant")
    op.drop_table("support_grant")
