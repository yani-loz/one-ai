"""
Role: SQLAlchemy declarative base and shared model mixins (PK, timestamps, tenant).
Used by: every ORM model in the project; Alembic autogenerate (target_metadata).
Depends on: SQLAlchemy 2.0 (external). Leaf module within the project.
Key invariants:
  - Every persisted model inherits Base.
  - Every TENANT-SCOPED model mixes in TenantMixin -> org_id NOT NULL + indexed
    (security.md layer 1). org_id is the canonical tenant key, project-wide.
  - Primary keys are server-generated UUIDs (gen_random_uuid(), Postgres core).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all One AI ORM models."""


class UUIDPrimaryKeyMixin:
    """Adds a server-generated UUID primary key `id`."""

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    """Adds `created_at` / `updated_at` (timezone-aware, server-maintained)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TenantMixin:
    """Adds `org_id` — the tenant scope column (NOT NULL + indexed).

    The hardest security rule: every tenant-scoped table carries this and every
    query is filtered by it (application scope today, RLS once policies land).
    """

    org_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False, index=True)
