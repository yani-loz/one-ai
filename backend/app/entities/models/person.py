"""
Role: Person entity + its match keys (emails) and aliases — the shared cross-source identity Core.
      The same human appearing in email, Slack, and meetings resolves to ONE Person, matched
      deterministically by a NORMALIZED email key.
Used by: entities repositories + (later) the entity resolver; referenced by connector Layer-1 tables
         (email_message.from_person_id, email_recipient.person_id) via string FK.
Depends on: app.common.base_model (Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin).
Key invariants:
  - TENANT-SCOPED (org_id NOT NULL + indexed): each tenant has its OWN entity graph.
  - `person_email.email` is the NORMALIZED match key, UNIQUE(org_id, email), for get-or-create. The
    raw, as-seen address lives on the source rows (email_message.from_address /
    email_recipient.address), never here.
  - A person owns MANY emails (work/personal/aliases): email is a match key, NOT the primary key.
  - `is_internal` marks the tenant's own people (vs external contacts) — set later by the resolver.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base_model import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Person(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """A human entity, unified across all of a tenant's data sources."""

    __tablename__ = "person"

    display_name: Mapped[str | None] = mapped_column(String(320))
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonEmail(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """A normalized email address owned by a person — the deterministic cross-source match key."""

    __tablename__ = "person_email"
    __table_args__ = (UniqueConstraint("org_id", "email", name="uq_person_email_identity"),)

    person_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NORMALIZED match key (not the raw as-seen address). UNIQUE(org_id, email).
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    # Provenance: which connector contributed this address (e.g. 'imap').
    source: Mapped[str | None] = mapped_column(String(32))


class PersonAlias(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    """An alternate display name seen for a person (helps later name-based disambiguation)."""

    __tablename__ = "person_alias"

    person_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(320), nullable=False)
    source: Mapped[str | None] = mapped_column(String(32))
