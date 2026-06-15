"""
Role: ConnectorPolicy ORM model (CO-01 Tier 2) — a company's ORG-WIDE enable/disable for a
      connector type. The default reach for every employee, within the company's entitlement.
Used by: ConnectorPolicyRepository; the permission resolver (connector_authz) reads it after
         entitlement + per-user override; registered on Base.metadata via models/__init__.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin),
            SQLAlchemy + postgresql dialect.
Key invariants:
  - TENANT-SCOPED (TenantMixin org_id NOT NULL + indexed) + LIVE org_isolation RLS (migration
    0018): a company admin reads/writes ONLY their org's policy; the tenant engine is the
    non-bypass role. Set by company admins only (`require_company_admin`).
  - `org_wide_enabled` DEFAULTS FALSE (CO-01 §10.1): the common case is "200 employees, only a
    few should have it" — leave org-wide off and per-user GRANT the few. A per-user override
    always wins over this value; entitlement (Tier 1) is the hard ceiling above both.
  - `UNIQUE(org_id, connector_type)` — one org-wide policy row per company per type.
  - `set_by_user_id` records the admin who last changed it (audit_log holds the full trail).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ConnectorPolicy(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """A company's org-wide enable/disable for one connector type (admin-governed)."""

    __tablename__ = "connector_policy"
    __table_args__ = (
        CheckConstraint("connector_type IN ('imap')", name="ck_connector_policy_type"),
        UniqueConstraint("org_id", "connector_type", name="uq_connector_policy_identity"),
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_connector_policy_org"
        ),
    )

    connector_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Default OFF (CO-01 §10.1): org-wide-off + per-user grant is the common shape.
    org_wide_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # The admin who last set this policy (audit_log holds the full trail).
    set_by_user_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
