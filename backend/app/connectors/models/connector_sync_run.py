"""
Role: ConnectorSyncRun ORM model — one durable row per sync RUN (history + crashed-run
      reconciliation + the audit ethos). Distinct from the live claim/heartbeat columns on
      connector_connection: this is the ledger, that is the current state.
Used by: ConnectorSyncRunRepository + the SyncRunner; registered via models/__init__.
Depends on: app.common.base_model (Base + mixins), SQLAlchemy + postgresql dialect.
Key invariants:
  - TENANT-SCOPED (org_id NOT NULL + indexed); RLS ENABLE + org_isolation + FORCE (migration 0011).
  - status (running|succeeded|failed|abandoned, DB CHECK): 'abandoned' is stamped when a newer run
    steals a stale claim, so the audit never lies about a crashed run.
  - `last_error` is a SANITIZED message (type + safe text) — NEVER str(exc), which would echo a
    subject/body fragment.
  - run_id matches connector_connection.sync_run_id for the active run (the fencing token).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ConnectorSyncRun(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """One sync run's durable record (counts + outcome) for one connection."""

    __tablename__ = "connector_sync_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'abandoned')",
            name="ck_connector_sync_run_status",
        ),
    )

    connection_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("connector_connection.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    messages_seen: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    messages_stored: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    messages_skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    messages_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500))
