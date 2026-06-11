"""
Role: ConnectorSyncCursor ORM model — the per-(org, connection, folder) incremental IMAP fetch
      cursor: which UIDVALIDITY + last UID we've durably stored, plus the parse-poison UIDs
      currently stalling the folder (an operator signal — see Key invariants). The runner UPSERTs
      it in the SAME per-batch transaction as the batch's email rows.
Used by: ConnectorSyncCursorRepository + the SyncRunner; registered via models/__init__.
Depends on: app.common.base_model (Base + mixins), SQLAlchemy + postgresql dialect (ARRAY).
Key invariants:
  - TENANT-SCOPED (org_id NOT NULL + indexed); RLS ENABLE + org_isolation + FORCE (migration 0011).
  - RESUMABILITY: because the cursor row commits ATOMICALLY with its batch's emails, last_seen_uid
    can NEVER be ahead of durably-stored mail — a crash rolls back both and the resume re-fetches
    (idempotent on dedup_key) and re-advances. Cursor advance = the contiguous committed prefix.
  - `failed_uids` is an OPERATOR SIGNAL ONLY (capped, lowest UIDs kept): which UIDs are currently
    stalling the folder. It is NOT load-bearing for the advance — a failed UID is NEVER stepped
    over: the contiguous-prefix advance STOPS AT the lowest failed (or unreturned) UID and the next
    run re-fetches from there (raw bytes are discarded on fail, so re-download is the only retry).
    A permanent failure therefore visibly stalls the folder — new mail above it still stores via
    dedup — until drained operationally (test_persistent_failure_never_steps_over_the_uid).
  - UNIQUE(org_id, connection_id, folder); folder is the RAW modified-UTF-7 IMAP name.
  - 0014 guards: org_id FKs organizations(id); the connection FK is the COMPOSITE
    (org_id, connection_id) form (tenant-coherent, CASCADE preserved).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import BigInteger, ForeignKeyConstraint, String, UniqueConstraint, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ConnectorSyncCursor(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """One IMAP folder's incremental fetch cursor for one connection."""

    __tablename__ = "connector_sync_cursor"
    __table_args__ = (
        UniqueConstraint("org_id", "connection_id", "folder", name="uq_sync_cursor_identity"),
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_connector_sync_cursor_org_id"
        ),
        ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["connector_connection.org_id", "connector_connection.id"],
            ondelete="CASCADE",
            name="fk_connector_sync_cursor_connection_id_org",
        ),
    )

    connection_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    folder: Mapped[str] = mapped_column(String(998), nullable=False)
    uidvalidity: Mapped[int | None] = mapped_column(BigInteger)
    last_seen_uid: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    failed_uids: Mapped[list[int]] = mapped_column(
        postgresql.ARRAY(BigInteger), nullable=False, server_default=text("'{}'::bigint[]")
    )
