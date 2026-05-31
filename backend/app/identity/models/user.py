"""
Role: User ORM model — an org-scoped person (company_admin or member).
Used by: identity repositories/services; the company auth domain authenticates these.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TimestampMixin,
            TenantMixin).
Key invariants:
  - Tenant-scoped: mixes in TenantMixin -> org_id NOT NULL + indexed. The explicit
    table-level FK adds REFERENCES organizations(id) ON DELETE CASCADE (TenantMixin
    supplies the column but no FK).
  - email is globally UNIQUE (MVP: one user = one org).
  - role is constrained to UserRole values by a DB CHECK.
  - password_hash holds a bcrypt hash only — never a plaintext password, never logged.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, ForeignKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """An employee or admin belonging to exactly one organization."""

    __tablename__ = "users"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_users_org_id"
        ),
        CheckConstraint(
            "role IN ('company_admin', 'member')", name="ck_users_role"
        ),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
