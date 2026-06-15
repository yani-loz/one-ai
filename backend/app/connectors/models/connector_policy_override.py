"""
Role: ConnectorPolicyOverride ORM model (CO-01 Tier 2) — a company admin's PER-USER decision
      (grant or deny) for a connector type, overriding the org-wide policy for one employee.
Used by: ConnectorPolicyOverrideRepository; the permission resolver (connector_authz) reads it
         BEFORE the org-wide policy; registered on Base.metadata via models/__init__.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin),
            app.connectors.enums (OverrideType), SQLAlchemy + postgresql dialect.
Key invariants:
  - TENANT-SCOPED (TenantMixin org_id NOT NULL + indexed) + LIVE org_isolation RLS (0018). Set
    by company admins only.
  - The override ALWAYS WINS over the org-wide policy (CO-01 §10.1): `deny` excludes a user even
    when org-wide is on; `grant` lets a user self-connect even when org-wide is off. Entitlement
    (Tier 1) is still the hard ceiling above it.
  - `override_type` is pinned by a DB CHECK to OverrideType ('grant','deny').
  - `UNIQUE(org_id, user_id, connector_type)` — at most one override per user per type.
  - COMPOSITE FK `(org_id, user_id) -> users(org_id, id)` (the 0018 anchor): a user's org can
    never diverge from the override's org. ON DELETE CASCADE so removing the user clears it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ConnectorPolicyOverride(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """A per-user grant/deny that overrides the org-wide policy for one connector type."""

    __tablename__ = "connector_policy_override"
    __table_args__ = (
        CheckConstraint("connector_type IN ('imap')", name="ck_connector_override_type"),
        CheckConstraint(
            "override_type IN ('grant', 'deny')", name="ck_connector_override_decision"
        ),
        UniqueConstraint(
            "org_id", "user_id", "connector_type", name="uq_connector_override_identity"
        ),
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_connector_override_org"
        ),
        # Composite anchor (0018 adds UNIQUE(org_id, id) on users): the override's user MUST
        # belong to its org; deleting the user removes the override.
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            ondelete="CASCADE",
            name="fk_connector_override_user",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(32), nullable=False)
    override_type: Mapped[str] = mapped_column(String(8), nullable=False)
    # The admin who set this override (audit_log holds the full trail).
    set_by_user_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
