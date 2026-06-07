"""
Role: ConnectorConnection ORM model — one org's configured connection to a data source (point
      1: an IMAP mailbox). Holds the non-secret connection params + the ENCRYPTED credential.
Used by: ConnectorConnectionRepository; registered on Base.metadata via models/__init__.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin),
            SQLAlchemy + the postgresql dialect (JSONB / BYTEA).
Key invariants:
  - TENANT-SCOPED (TenantMixin org_id NOT NULL + indexed): a connection always belongs to one
    org; the company side reads it org-scoped (a company_admin sees ONLY their org's
    connections). RLS policy is DEFINED (migration 0007) but inert until the least-privilege DB
    role lands — the active control is the app-layer org_id filter (see docs/FIX_BEFORE_PROD).
  - THE SECRET IS NEVER STORED IN PLAINTEXT: the credential lives only in `secret_ciphertext`
    (AES-256-GCM, see security.credential_cipher); `secret_key_version` records which app key
    encrypted it (for later rotation). `config` (JSONB) holds ONLY non-secret params
    (host/port/use_ssl) — never the password. `last_error` holds only sanitized messages.
  - `connector_type`, `auth_method`, and `status` are pinned by DB CHECKs to the enum values
    (app.connectors.enums); a (org_id, connector_type, username) is unique per org.
  - created_at IS the configured-at time; status starts 'configured' and moves to
    'connected'/'error' on each verification (last_checked_at / last_error updated with it).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, String, UniqueConstraint, text
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
        UniqueConstraint(
            "org_id", "connector_type", "username", name="uq_connector_connection_identity"
        ),
    )

    connector_type: Mapped[str] = mapped_column(String(32), nullable=False)
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
