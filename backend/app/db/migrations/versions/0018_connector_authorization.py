"""connector authorization (CO-01) — entitlement + governance policies + consent + owner dimension

Adds the three-tier connector authorization model over the built connector data plane
(app.connectors.models). Mirrors the ORM models:

  - connector_entitlement (Tier 1) — PLATFORM PLANE: which connector types a company is entitled
    to (the paid-plan ceiling). NOT a TenantMixin table → no per-tenant org_isolation RLS and NO
    oneai_app grant (migration 0013's fail-closed default ACL leaves it tenant-invisible); the
    GLOBAL engine (oneai_global, auto-granted) reads/writes it on behalf of platform admins.
  - connector_policy (Tier 2) — TENANT: a company's org-wide enable/disable per type.
  - connector_policy_override (Tier 2) — TENANT: a company admin's per-user grant/deny.
  - connector_consent (Tier 3 / §8) — TENANT: a user's GDPR Art. 7 consent at self-connect.
  - connector_connection gains owner_user_id (NULL = org-owned/shared, set = user-owned) with
    PARTIAL unique indexes and a composite FK (org_id, owner_user_id) -> users(org_id, id).

Per migration 0013's convention, every NEW TenantMixin table here is ENABLE + FORCE ROW LEVEL
SECURITY with the org_isolation policy AND gets an EXPLICIT GRANT of full DML to oneai_app (the
default ACL no longer auto-grants — tests/db/test_least_privilege_grants.py would fail otherwise).
A users UNIQUE(org_id, id) anchor (uq_users_org_row) is added first so the per-user composite FKs
resolve.

Revision ID: 0018_connector_authorization
Revises: 0017_attachment_extracted_data
Create Date: 2026-06-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_connector_authorization"
down_revision: str | None = "0017_attachment_extracted_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Runtime tenant role (matches migration 0013 / config.app_db_user; created in 0009).
_APP_ROLE = "oneai_app"
# The GUC the org_isolation policies key on (0003/0011/0013 idiom; core.scoped_session binds it).
_ORG_GUC = "current_setting('app.current_org_id', true)::uuid"

# New TenantMixin tables — each gets ENABLE+FORCE RLS, org_isolation, and an explicit app GRANT.
_TENANT_TABLES = ["connector_policy", "connector_policy_override", "connector_consent"]


def _enforce_tenant_rls(table: str) -> None:
    """ENABLE + org_isolation policy + FORCE + explicit oneai_app DML grant (0013 convention)."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY org_isolation ON {table}
            USING (org_id = {_ORG_GUC})
            WITH CHECK (org_id = {_ORG_GUC})
        """
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {_APP_ROLE}")


def _timestamps() -> list[sa.Column]:
    """The TimestampMixin columns (server-maintained created_at / updated_at)."""
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    # ── 1) users composite-FK anchor — referencible target for (org_id, user_id) child FKs.
    op.create_unique_constraint("uq_users_org_row", "users", ["org_id", "id"])

    # ── 2) connector_connection: the owner dimension (NULL = shared, set = user-owned).
    op.add_column(
        "connector_connection",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Replace the single per-org identity uniqueness with owner-partitioned partial uniques.
    op.drop_constraint("uq_connector_connection_identity", "connector_connection", type_="unique")
    op.create_index(
        "uq_connector_connection_shared_identity",
        "connector_connection",
        ["org_id", "connector_type", "username"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NULL"),
    )
    op.create_index(
        "uq_connector_connection_owned_identity",
        "connector_connection",
        ["org_id", "owner_user_id", "connector_type", "username"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
    )
    # A user-owned connection's owner must belong to its org; offboarding cascades the connection.
    op.create_foreign_key(
        "fk_connector_connection_owner",
        "connector_connection",
        "users",
        ["org_id", "owner_user_id"],
        ["org_id", "id"],
        ondelete="CASCADE",
    )

    # ── 3) connector_entitlement — PLATFORM PLANE (no tenant RLS, no oneai_app grant).
    op.create_table(
        "connector_entitlement",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_type", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("set_by_platform_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("connector_type IN ('imap')", name="ck_connector_entitlement_type"),
        sa.UniqueConstraint("org_id", "connector_type", name="uq_connector_entitlement_identity"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_connector_entitlement_org",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── 4) connector_policy (Tier 2, org-wide) — tenant table.
    op.create_table(
        "connector_policy",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_type", sa.String(length=32), nullable=False),
        sa.Column(
            "org_wide_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("set_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("connector_type IN ('imap')", name="ck_connector_policy_type"),
        sa.UniqueConstraint("org_id", "connector_type", name="uq_connector_policy_identity"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_connector_policy_org"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connector_policy_org_id", "connector_policy", ["org_id"])

    # ── 5) connector_policy_override (Tier 2, per-user) — tenant table.
    op.create_table(
        "connector_policy_override",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_type", sa.String(length=32), nullable=False),
        sa.Column("override_type", sa.String(length=8), nullable=False),
        sa.Column("set_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("connector_type IN ('imap')", name="ck_connector_override_type"),
        sa.CheckConstraint(
            "override_type IN ('grant', 'deny')", name="ck_connector_override_decision"
        ),
        sa.UniqueConstraint(
            "org_id", "user_id", "connector_type", name="uq_connector_override_identity"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_connector_override_org"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            ondelete="CASCADE",
            name="fk_connector_override_user",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connector_policy_override_org_id", "connector_policy_override", ["org_id"])

    # ── 6) connector_consent (Tier 3 / §8) — tenant table.
    op.create_table(
        "connector_consent",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_type", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=120), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ui_proof",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        *_timestamps(),
        sa.CheckConstraint("connector_type IN ('imap')", name="ck_connector_consent_type"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_connector_consent_org"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            ondelete="CASCADE",
            name="fk_connector_consent_user",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connector_consent_org_id", "connector_consent", ["org_id"])
    op.create_index(
        "ix_connector_consent_user_type",
        "connector_consent",
        ["org_id", "user_id", "connector_type"],
    )

    # ── 7) Enforce tenant RLS + grants on the three new tenant tables (0013 convention).
    for table in _TENANT_TABLES:
        _enforce_tenant_rls(table)


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_connector_consent_user_type", table_name="connector_consent")
    op.drop_index("ix_connector_consent_org_id", table_name="connector_consent")
    op.drop_table("connector_consent")

    op.drop_index("ix_connector_policy_override_org_id", table_name="connector_policy_override")
    op.drop_table("connector_policy_override")

    op.drop_index("ix_connector_policy_org_id", table_name="connector_policy")
    op.drop_table("connector_policy")

    op.drop_table("connector_entitlement")

    op.drop_constraint("fk_connector_connection_owner", "connector_connection", type_="foreignkey")
    op.drop_index("uq_connector_connection_owned_identity", table_name="connector_connection")
    op.drop_index("uq_connector_connection_shared_identity", table_name="connector_connection")
    op.create_unique_constraint(
        "uq_connector_connection_identity",
        "connector_connection",
        ["org_id", "connector_type", "username"],
    )
    op.drop_column("connector_connection", "owner_user_id")

    op.drop_constraint("uq_users_org_row", "users", type_="unique")
