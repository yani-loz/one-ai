"""
Role: ConnectorConsent ORM model (CO-01 Tier 3 / §8) — the GDPR Art. 7 consent a user records
      when self-connecting a connector: who/when/scope/method + UI proof. Withdrawal (Art. 7(4))
      is retained as proof, never deleted while the consent is in force.
Used by: ConnectorConsentRepository / ConnectorConsentService (capture at self-connect, withdraw
         on disconnect); registered on Base.metadata via models/__init__.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin),
            SQLAlchemy + postgresql dialect.
Key invariants:
  - TENANT-SCOPED (TenantMixin org_id NOT NULL + indexed) + LIVE org_isolation RLS (0018).
  - A row is the user's OWN consent: only the owner self-connects; admins/platform never write it.
    Composite FK `(org_id, user_id) -> users(org_id, id)` (0018 anchor) — the consent's user can
    never diverge from its org; ON DELETE CASCADE clears it on per-user erasure/offboarding.
  - APPEND-then-mark: `granted_at` is set at consent; `withdrawn_at` is set on withdrawal and the
    row is RETAINED as proof of lawful basis (Art. 7(1)) — a sync never runs without an active
    (granted, not withdrawn) consent. Re-consent inserts a NEW row (history accumulates).
  - `ui_proof` (JSONB) holds NON-PII proof only (consent text version, accepted flag) — never an
    IP, never content.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ConnectorConsent(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """A user's recorded consent (with UI proof) to self-connect a connector type."""

    __tablename__ = "connector_consent"
    __table_args__ = (
        CheckConstraint("connector_type IN ('imap')", name="ck_connector_consent_type"),
        # Lookup the user's consents for a type (active = withdrawn_at IS NULL, latest granted_at).
        Index("ix_connector_consent_user_type", "org_id", "user_id", "connector_type"),
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_connector_consent_org"
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            ondelete="CASCADE",
            name="fk_connector_consent_user",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # What was consented to (e.g. "mailbox:read") and how it was connected (auth method).
    scope: Mapped[str] = mapped_column(String(120), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Set on withdrawal (Art. 7(4)); the row is retained as proof. NULL = consent in force.
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Non-PII proof of the consent UI interaction (consent text version, accepted flag) — never PII.
    ui_proof: Mapped[dict[str, object]] = mapped_column(
        postgresql.JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
