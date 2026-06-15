"""
Role: ConnectorEntitlement ORM model (CO-01 Tier 1) — which connector TYPES a company is
      entitled to use, tied to its paid plan. The outermost gate above company governance.
Used by: ConnectorEntitlementRepository (platform/global engine); the permission resolver
         (connector_authz) reads it as the hard ceiling; registered on Base.metadata via
         models/__init__.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TimestampMixin), SQLAlchemy +
            postgresql dialect.
Key invariants:
  - PLATFORM-PLANE, NOT tenant-scoped: deliberately does NOT mix in TenantMixin. It carries an
    `org_id` column (which company) but is written/read on the GLOBAL engine by platform admins
    only — the tenant role holds no privilege on it (CO-01 §5; migration 0013's fail-closed
    default ACL). So it is not under the per-tenant org_isolation RLS the TenantMixin tables use.
  - `enabled` IS the billing seam: True = the company may use `connector_type`; False/absent =
    not entitled (the resolver denies before any policy/credential work). Phase 1 sets this
    manually via the platform console (CO-01 §9 — real subscription source is Phase 3).
  - `UNIQUE(org_id, connector_type)` — one entitlement row per company per connector type.
  - `org_id` FKs organizations(id) (no phantom tenants); revoking sets enabled=False +
    revoked_at (policies/connections persist and are re-exposed on re-grant — no cascade).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ConnectorEntitlement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A company's plan-level entitlement to one connector type (platform-managed)."""

    __tablename__ = "connector_entitlement"
    __table_args__ = (
        CheckConstraint("connector_type IN ('imap')", name="ck_connector_entitlement_type"),
        UniqueConstraint("org_id", "connector_type", name="uq_connector_entitlement_identity"),
        ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_connector_entitlement_org",
        ),
    )

    # Which company. Plain column (NOT TenantMixin) — this row lives on the platform plane.
    org_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # The live entitlement: True = entitled (may use), False = revoked. The billing seam.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Provenance: the platform admin who last granted/revoked (audit_log holds the full trail).
    set_by_platform_admin_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
