"""sync runner state — connection sync columns + cursor + run-ledger tables

Adds the IMAP SyncRunner's durable state:
  - connector_connection gains sync_* columns: sync_status (idle|running|error, its OWN CHECK,
    orthogonal to the health `status` and to disabled_at), sync_run_id (the fencing token),
    sync_started_at / sync_heartbeat_at / last_synced_at, synced_count / total_count,
    last_sync_error (sanitized). These double as the single-runner claim + heartbeat + the row the
    UI already loads.
  - connector_sync_cursor — one row per (org, connection, folder): the incremental fetch cursor
    (uidvalidity + last_seen_uid) + failed_uids (parse-poison UIDs to step over). The cursor UPSERT
    rides the SAME per-batch commit as that batch's email rows, so it can never be ahead of stored
    mail. UNIQUE(org_id, connection_id, folder).
  - connector_sync_run — one row per run (status running|succeeded|failed|abandoned + counts +
    sanitized last_error): run history + crashed-run reconciliation + the audit ethos.

Both new tables are tenant-scoped and get ENABLE + org_isolation + FORCE RLS (RLS is enforced as of
0009). Erasure: both join the org-erasure obligation (CA-CONN-03 family; wiring deferred).

Revision ID: 0011_sync_runner_state
Revises: 0010_connector_disabled_at
Create Date: 2026-06-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_sync_runner_state"
down_revision: str | None = "0010_connector_disabled_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_TENANT_TABLES = ["connector_sync_cursor", "connector_sync_run"]


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
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _org_id() -> sa.Column:
    return sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    # ── connector_connection: sync state / claim / heartbeat / progress ──
    op.add_column(
        "connector_connection",
        sa.Column("sync_status", sa.String(length=10), server_default="idle", nullable=False),
    )
    op.add_column(
        "connector_connection",
        sa.Column("sync_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "connector_connection",
        sa.Column("sync_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "connector_connection",
        sa.Column("sync_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "connector_connection",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "connector_connection",
        sa.Column("synced_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("connector_connection", sa.Column("total_count", sa.Integer(), nullable=True))
    op.add_column(
        "connector_connection", sa.Column("last_sync_error", sa.String(length=500), nullable=True)
    )
    op.create_check_constraint(
        "ck_connector_connection_sync_status",
        "connector_connection",
        "sync_status IN ('idle', 'running', 'error')",
    )

    # ── connector_sync_cursor: per-(org, connection, folder) incremental cursor ──
    op.create_table(
        "connector_sync_cursor",
        _uuid_pk(),
        _org_id(),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connector_connection.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("folder", sa.String(length=998), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=True),
        sa.Column("last_seen_uid", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "failed_uids",
            postgresql.ARRAY(sa.BigInteger()),
            server_default=sa.text("'{}'::bigint[]"),
            nullable=False,
        ),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "connection_id", "folder", name="uq_sync_cursor_identity"),
    )
    op.create_index("ix_connector_sync_cursor_org_id", "connector_sync_cursor", ["org_id"])
    op.create_index(
        "ix_connector_sync_cursor_connection_id", "connector_sync_cursor", ["connection_id"]
    )

    # ── connector_sync_run: one durable row per run (history + reconciliation) ──
    op.create_table(
        "connector_sync_run",
        _uuid_pk(),
        _org_id(),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connector_connection.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="running", nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("messages_seen", sa.Integer(), server_default="0", nullable=False),
        sa.Column("messages_stored", sa.Integer(), server_default="0", nullable=False),
        sa.Column("messages_skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("messages_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'abandoned')",
            name="ck_connector_sync_run_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connector_sync_run_org_id", "connector_sync_run", ["org_id"])
    op.create_index("ix_connector_sync_run_connection_id", "connector_sync_run", ["connection_id"])

    # ── ENABLE + org_isolation policy + FORCE RLS on both new tenant tables (0008/0009 idiom) ──
    for table in _NEW_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY org_isolation ON {table}
                USING (org_id = current_setting('app.current_org_id', true)::uuid)
                WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid)
            """
        )
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(_NEW_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("connector_sync_run")
    op.drop_table("connector_sync_cursor")
    op.drop_constraint("ck_connector_connection_sync_status", "connector_connection")
    for column in (
        "last_sync_error",
        "total_count",
        "synced_count",
        "last_synced_at",
        "sync_heartbeat_at",
        "sync_started_at",
        "sync_run_id",
        "sync_status",
    ):
        op.drop_column("connector_connection", column)
