"""
Role: Organization ORM model — the tenant itself (one row per customer company).
Used by: identity repositories/services; users reference it via org_id FK.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TimestampMixin).
Key invariants:
  - Organization IS the tenant, so it does NOT mix in TenantMixin (it has no org_id
    of its own).
  - slug is globally unique and is the human-friendly tenant identifier.
  - status defaults to 'active' and is pinned to the OrganizationStatus enum by a DB
    CHECK (ck_organizations_status). legal_hold defaults to false. Column lengths match
    SPEC §2 exactly so the migration models match the schema.
  - legal_hold is platform-managed; once PC-06 lands it BLOCKS erasure (legal hold beats
    right-to-erasure). It is metadata about the tenant, not tenant content.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A customer company. The root tenant entity keyed by org_id elsewhere."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'onboarding', 'offboarded')",
            name="ck_organizations_status",
        ),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    legal_hold: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
