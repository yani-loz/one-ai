"""
Role: ConnectorConnection ORM model — one org's configured connection to a data source (point
      1: an IMAP mailbox). Holds the non-secret connection params + the ENCRYPTED credential.
Used by: ConnectorConnectionRepository; registered on Base.metadata via models/__init__.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin),
            SQLAlchemy + the postgresql dialect (JSONB / BYTEA).
Key invariants:
  - TENANT-SCOPED (TenantMixin org_id NOT NULL + indexed): a connection always belongs to one
    org; the company side reads it org-scoped (a company_admin sees ONLY their org's
    connections). RLS is ENFORCED (migration 0009): the tenant engine runs as a non-bypass role
    against the org_isolation policy; the app-layer org_id filter is the second layer.
  - `disabled_at` is the reversible admin disable (design §8): NULL = active; set = disabled
    (sync stops; AI retrieval excludes it once that path enforces it). Orthogonal to `status`.
  - THE SECRET IS NEVER STORED IN PLAINTEXT: the credential lives only in `secret_ciphertext`
    (AES-256-GCM, see security.credential_cipher); `secret_key_version` records which app key
    encrypted it (for later rotation). `config` (JSONB) holds ONLY non-secret params
    (host/port/use_ssl) — never the password. `last_error` holds only sanitized messages.
  - `connector_type`, `auth_method`, and `status` are pinned by DB CHECKs to the enum values
    (app.connectors.enums).
  - OWNER DIMENSION (CO-01, migration 0018): `owner_user_id` NULL = org-owned/shared (the legacy
    admin-provisioned mailbox); set = USER-OWNED (self-connect, the CO-01 default). Uniqueness is
    PARTIAL: shared rows are unique per (org_id, type, username); owned rows per (org_id,
    owner_user_id, type, username) — so two employees may each connect the same shared address,
    but one employee can't connect it twice. The composite FK (org_id, owner_user_id) ->
    users(org_id, id) ON DELETE CASCADE means offboarding a user cascades their connections
    (per-user erasure). §7: only owner_user_id == principal may reach credential/content surfaces;
    admins/platform see metadata only.
  - 0014 guards: org_id FKs organizations(id) (no phantom tenants), and UNIQUE (org_id, id)
    anchors the composite child FKs (email_message / sync run / sync cursor reference
    (org_id, connection_id) so a child's org_id can never diverge from its connection's).
  - created_at IS the configured-at time; status starts 'configured' and moves to
    'connected'/'error' on each verification (last_checked_at / last_error updated with it).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ConnectorConnection(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """An org's configured connection to one data source (credential encrypted at rest)."""

    __tablename__ = "connector_connection"
    __table_args__ = (
        CheckConstraint("connector_type IN ('imap')", name="ck_connector_connection_type"),
        CheckConstraint(
            "auth_method IN ('app_password')", name="ck_connector_connection_auth_method"
        ),
        CheckConstraint(
            "status IN ('configured', 'connected', 'error')",
            name="ck_connector_connection_status",
        ),
        CheckConstraint(
            "sync_status IN ('idle', 'running', 'error')",
            name="ck_connector_connection_sync_status",
        ),
        # PARTIAL uniqueness by owner (CO-01 0018): shared (owner NULL) rows are unique per org;
        # user-owned rows are unique per owner. Lets two employees connect the same address while
        # stopping one employee connecting it twice.
        Index(
            "uq_connector_connection_shared_identity",
            "org_id",
            "connector_type",
            "username",
            unique=True,
            postgresql_where=text("owner_user_id IS NULL"),
        ),
        Index(
            "uq_connector_connection_owned_identity",
            "org_id",
            "owner_user_id",
            "connector_type",
            "username",
            unique=True,
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
        # 0014 composite-FK anchor: children FK (org_id, id) so their org_id can never diverge.
        UniqueConstraint("org_id", "id", name="uq_connector_connection_org_row"),
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name="fk_connector_connection_org_id"
        ),
        # Owner dimension (CO-01 0018): a user-owned connection's owner MUST belong to its org;
        # offboarding the user cascades the connection (per-user erasure). NULL owner = shared:
        # the composite FK is not enforced for shared rows (MATCH SIMPLE), which is intended.
        ForeignKeyConstraint(
            ["org_id", "owner_user_id"],
            ["users.org_id", "users.id"],
            ondelete="CASCADE",
            name="fk_connector_connection_owner",
        ),
    )

    connector_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Owner dimension (CO-01): NULL = org-owned/shared (legacy admin-provisioned); set = user-owned
    # (self-connect). Only the owner reaches credential/content surfaces (§7); admins see metadata.
    owner_user_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    auth_method: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="app_password"
    )
    # Login identity (the email/username). Top-level (not in config) so it can anchor the
    # per-org uniqueness constraint.
    username: Mapped[str] = mapped_column(String(320), nullable=False)
    # Non-secret connection params only (host/port/use_ssl). NEVER the password.
    config: Mapped[dict[str, object]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # AES-256-GCM (nonce || ciphertext+tag). The ONLY place the credential lives.
    secret_ciphertext: Mapped[bytes] = mapped_column(postgresql.BYTEA, nullable=False)
    secret_key_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="configured")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Sanitized failure message from the last verification — never a secret or raw vendor error.
    last_error: Mapped[str | None] = mapped_column(String(500))
    # Reversible admin disable (NULL = active). Orthogonal to `status` (health): a disabled
    # connection keeps its last verification state, and enable just clears this back to NULL.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── SyncRunner state — the single-runner CLAIM + heartbeat + progress on the row the UI loads.
    # sync_status (idle|running|error) is orthogonal to `status` (health) and `disabled_at`.
    # sync_run_id is the FENCING TOKEN: every heartbeat/progress/finalize UPDATE the runner issues
    # is conditional on it, so a reclaimed (stale) run's late writes hit 0 rows and abort.
    sync_status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="idle")
    sync_run_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    sync_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_count: Mapped[int | None] = mapped_column(Integer)
    last_sync_error: Mapped[str | None] = mapped_column(String(500))
