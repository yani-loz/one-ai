"""
Role: User ORM model — an org-scoped person (company_admin or member).
Used by: identity repositories/services; the company auth domain authenticates these.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TimestampMixin,
            TenantMixin).
Key invariants:
  - Tenant-scoped: mixes in TenantMixin -> org_id NOT NULL + indexed. The explicit
    table-level FK adds REFERENCES organizations(id) ON DELETE CASCADE (TenantMixin
    supplies the column but no FK).
  - email is globally UNIQUE (MVP: one user = one org) — and 0014 adds the functional
    UNIQUE index uq_users_email_lower on lower(email), the DB-level backing for the
    Pydantic NormalizedEmail guarantee (lowercased login lookups always match).
  - role is constrained to UserRole values by a DB CHECK.
  - password_hash holds a bcrypt hash only — never a plaintext password, never logged.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """An employee or admin belonging to exactly one organization."""

    __tablename__ = "users"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE", name="fk_users_org_id"
        ),
        CheckConstraint("role IN ('company_admin', 'member')", name="ck_users_role"),
        # 0014 M-1: case-insensitive identity uniqueness (expression matches the PG-reflected
        # form `lower((email)::text)` so autogenerate sees no drift).
        Index("uq_users_email_lower", text("lower(email::text)"), unique=True),
        # Composite-FK anchor (migration 0018): lets tenant child tables FK (org_id, user_id) ->
        # users(org_id, id) so a child's org can never diverge from its user's. id is already the
        # PK (globally unique); this UNIQUE(org_id, id) is the referencible target for those FKs.
        UniqueConstraint("org_id", "id", name="uq_users_org_row"),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
