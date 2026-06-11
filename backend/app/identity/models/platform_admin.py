"""
Role: PlatformAdmin ORM model — Ethera staff in the SEPARATE platform auth domain.
Used by: identity platform repositories/services (platform login + org onboarding).
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TimestampMixin).
Key invariants:
  - NOT tenant-scoped: a platform admin has global scope and no org_id (it is not an
    org row). Therefore it does NOT mix in TenantMixin.
  - email is globally UNIQUE — and 0014 adds the functional UNIQUE index
    uq_platform_admins_email_lower on lower(email) (case-insensitive identity, M-1).
  - password_hash holds a bcrypt hash only — never plaintext, never logged.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformAdmin(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An Ethera staff member who onboards companies and sees org metadata only."""

    __tablename__ = "platform_admins"
    __table_args__ = (
        # 0014 M-1: case-insensitive identity uniqueness (expression matches the PG-reflected
        # form `lower((email)::text)` so autogenerate sees no drift).
        Index("uq_platform_admins_email_lower", text("lower(email::text)"), unique=True),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
